"""
Most do počítače uživatele — Claude v prostoru na serveru dosáhne i na soubory
a příkazy na počítači, ze kterého se k prostoru přihlásil.

Spojení staví počítač, ne server. Hub na počítači (`hub/pocitac.py`) se tokenem
zařízení ptá brány, jestli pro něj něco nemá (`/gw/pocitac/poll`, brána dotaz
drží, dokud úkol nepřijde nebo neuplyne POLL_WAIT), úkol provede a výsledek
pošle zpátky (`/gw/pocitac/vysledek`). Na počítači se tak nic neotevírá — žádný
port ani tunel; projde to každou sítí, kudy projde přihlášení k serveru.

Z prostoru volá MCP server `tools/pocitac_mcp.py` (`/gw/pocitac/volani`) se
žetonem, který prostor dostal od brány při startu v prostředí. Brána úkol
zařadí počítači, počká na odpověď a vrátí ji. Do obsahu nesahá.

Co smí, rozhoduje počítač: přístup (jen čtení / plný) se zapíná v appce na něm
a ověřuje ho u každého úkolu znovu. Brána ho zná jen proto, aby Claude dostal
srozumitelnou odpověď dřív, než úkol vůbec odejde.
"""
import re
import secrets
import threading
import time

READ_OPS = frozenset({"info", "ls", "read", "find", "download"})
ALL_OPS = READ_OPS | frozenset({"write", "edit", "upload", "run"})
ACCESS = {"cteni": READ_OPS, "vse": ALL_OPS}
ACCESS_LABEL = {"cteni": "jen čtení", "vse": "plný přístup"}

POLL_WAIT = 25          # jak dlouho brána drží dotaz počítače, když nemá úkol
ONLINE_GRACE = 40       # počítač, který se tak dlouho neozval, je pryč
FORGET_AFTER = 24 * 3600
MAX_QUEUE = 32          # úkoly čekající na jeden počítač
BATCH = 8               # kolik úkolů si počítač odnese jedním dotazem
RUN_MAX = 600

_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")


def _text(value, limit=200):
    return str(value or "").strip()[:limit]


def summary(op, args):
    """Krátký popis úkolu pro člověka — štítek v hlavičce prostoru."""
    args = args if isinstance(args, dict) else {}
    if op == "run":
        return _text(args.get("command"), 160)
    return _text(args.get("path"), 160)


def timeout_for(op, args):
    """Jak dlouho brána na výsledek čeká. Počítá brána, ne volající."""
    if op == "run":
        try:
            limit = int((args or {}).get("timeout") or 120)
        except (TypeError, ValueError):
            limit = 120
        return max(1, min(RUN_MAX, limit)) + 30
    return 150


class Computer:
    """Jeden počítač jednoho účtu, jak ho brána zná z jeho dotazů."""

    def __init__(self, uid, cid):
        self.uid = uid
        self.id = cid
        self.name = ""
        self.system = ""
        self.home = ""
        self.user = ""
        self.shell = ""
        self.access = ""
        self.version = ""
        self.polling = 0
        self.last_seen = 0.0
        self.queue = []
        self.last = None            # poslední úkol: {"op", "summary", "at"}

    def online(self, now):
        return self.polling > 0 or now - self.last_seen < ONLINE_GRACE

    def public(self, now):
        return {"id": self.id, "name": self.name, "system": self.system,
                "home": self.home, "user": self.user, "shell": self.shell,
                "access": self.access,
                "access_label": ACCESS_LABEL.get(self.access, "vypnuto"),
                "version": self.version, "online": self.online(now),
                "seen_ago": 0 if self.polling else int(now - self.last_seen),
                "last": (dict(self.last, ago=int(now - self.last["at"]))
                         if self.last else None)}


class Broker:
    """Fronty úkolů pro počítače a čekání na jejich výsledky."""

    def __init__(self):
        self._cond = threading.Condition()
        self.computers = {}           # (uid, id počítače) -> Computer
        self._waiting = {}            # id úkolu -> {"event", "result", "uid", "cid"}

    # ── strana počítače ──────────────────────────────────────────────────────
    def poll(self, uid, info, wait=POLL_WAIT):
        """Počítač se hlásí a čeká na úkoly. Vrací jejich seznam (i prázdný)."""
        info = info if isinstance(info, dict) else {}
        cid = str(info.get("id") or "")
        if not _ID.fullmatch(cid):
            raise ValueError("Chybí nebo nesedí id počítače.")
        with self._cond:
            comp = self.computers.get((uid, cid))
            if comp is None:
                comp = self.computers[(uid, cid)] = Computer(uid, cid)
            comp.name = _text(info.get("name"), 80) or "počítač"
            comp.system = _text(info.get("system"), 80)
            comp.home = _text(info.get("home"), 300)
            comp.user = _text(info.get("user"), 80)
            comp.shell = _text(info.get("shell"), 40)
            comp.version = _text(info.get("version"), 20)
            access = str(info.get("access") or "")
            comp.access = access if access in ACCESS else ""
            if not comp.access:
                self._drop_queue(comp, "Přístup na počítači je vypnutý.")
            comp.polling += 1
            try:
                deadline = time.time() + max(0.0, min(float(wait), POLL_WAIT))
                while not comp.queue:
                    left = deadline - time.time()
                    if left <= 0:
                        break
                    self._cond.wait(left)
                tasks, comp.queue = comp.queue[:BATCH], comp.queue[BATCH:]
            finally:
                comp.polling -= 1
                comp.last_seen = time.time()
            self._forget_old()
        return tasks

    def bye(self, uid, cid):
        """Počítač se odhlásil (vypnutý přístup, zavřená appka)."""
        with self._cond:
            comp = self.computers.pop((uid, str(cid or "")), None)
            if comp:
                self._drop_queue(comp, "Počítač se právě odpojil.")
        return bool(comp)

    def requeue(self, uid, cid, tasks):
        """Úkoly, které se počítači nepodařilo předat (spadlé spojení)."""
        with self._cond:
            comp = self.computers.get((uid, str(cid or "")))
            if comp and tasks:
                comp.queue[:0] = tasks
                self._cond.notify_all()

    def reply(self, uid, cid, rid, result):
        """Výsledek úkolu od počítače. False = úkol nezná (vypršel, cizí)."""
        with self._cond:
            slot = self._waiting.get(str(rid or ""))
            if not slot or slot["uid"] != uid or slot["cid"] != str(cid or ""):
                return False
            slot["result"] = result
            slot["event"].set()
            return True

    # ── strana prostoru ──────────────────────────────────────────────────────
    def list(self, uid):
        now = time.time()
        with self._cond:
            mine = [c for (u, _cid), c in self.computers.items() if u == uid]
            return [c.public(now) for c in sorted(mine, key=lambda c: -c.last_seen)]

    def call(self, uid, op, args, target=""):
        """Předá úkol počítači a počká na výsledek.

        Vrací {"ok", "result"|"error", "computer"}. Nikdy nevyhodí výjimku —
        chyba je věta, kterou dostane Claude a z ní pozná, co dál.
        """
        if op not in ALL_OPS:
            return {"ok": False, "error": f"Neznámý úkol {op!r}."}
        args = args if isinstance(args, dict) else {}
        timeout = timeout_for(op, args)
        now = time.time()
        with self._cond:
            comp, error = self._pick(uid, _text(target, 80), now)
            if error:
                return {"ok": False, "error": error}
            if op not in ACCESS.get(comp.access, ()):
                return {"ok": False, "computer": comp.name, "error":
                        f"Na počítači {comp.name} je zapnutý jen přístup ke čtení — "
                        "zapisovat, nahrávat ani spouštět příkazy nejde. Když to "
                        "uživatel chce, přepne to v appce na počítači: Nastavení → "
                        "Účet → Claude ze serveru na tomhle počítači."}
            if len(comp.queue) >= MAX_QUEUE:
                return {"ok": False, "computer": comp.name,
                        "error": "Na počítač čeká moc úkolů najednou, zkus to za chvíli."}
            rid = secrets.token_hex(8)
            slot = {"event": threading.Event(), "result": None, "uid": uid, "cid": comp.id}
            self._waiting[rid] = slot
            comp.queue.append({"id": rid, "op": op, "args": args})
            comp.last = {"op": op, "summary": summary(op, args), "at": now}
            self._cond.notify_all()
        done = slot["event"].wait(timeout)
        with self._cond:
            self._waiting.pop(rid, None)
            queued = any(t["id"] == rid for t in comp.queue)
            if queued:
                comp.queue = [t for t in comp.queue if t["id"] != rid]
        if not done:
            return {"ok": False, "computer": comp.name, "error": (
                f"Počítač {comp.name} úkol nepřevzal — asi se právě odpojil "
                "(zavřená appka, uspaný počítač, výpadek sítě)." if queued else
                f"Počítač {comp.name} neodpověděl do {timeout} s.")}
        result = slot["result"] or {}
        if result.get("ok"):
            return {"ok": True, "computer": comp.name, "result": result.get("result")}
        return {"ok": False, "computer": comp.name,
                "error": _text(result.get("error"), 2000) or "Na počítači se to nepovedlo."}

    # ── pomůcky (pod zámkem) ─────────────────────────────────────────────────
    def _pick(self, uid, target, now):
        mine = [c for (u, _cid), c in self.computers.items()
                if u == uid and c.online(now) and c.access in ACCESS]
        if target:
            low = target.lower()
            hits = [c for c in mine if c.id == target or c.name.lower() == low]
            if not hits:
                names = ", ".join(c.name for c in mine) or "žádný"
                return None, f"Počítač {target!r} není připojený. Připojené: {names}."
            mine = hits[:1]
        if not mine:
            return None, ("Žádný počítač uživatele teď není připojený. Musí mít na "
                          "počítači otevřenou appku Claude Code Hub přihlášenou k "
                          "tomuhle serveru a v ní zapnutý přístup: Nastavení → Účet → "
                          "Claude ze serveru na tomhle počítači.")
        if len(mine) > 1:
            names = ", ".join(c.name for c in mine)
            return None, (f"Připojených počítačů je víc ({names}). Řekni, na kterém "
                          "pracovat — parametr `pocitac`.")
        return mine[0], ""

    def _drop_queue(self, comp, why):
        for task in comp.queue:
            slot = self._waiting.get(task["id"])
            if slot:
                slot["result"] = {"ok": False, "error": why}
                slot["event"].set()
        comp.queue = []

    def _forget_old(self):
        now = time.time()
        for key, comp in list(self.computers.items()):
            if not comp.polling and now - comp.last_seen > FORGET_AFTER:
                self.computers.pop(key, None)
