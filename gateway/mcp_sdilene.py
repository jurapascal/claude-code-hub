"""
Sdílená napojení (MCP) — jedno nastavení, klíč drží brána, používá víc lidí.

Kdokoli si v hubu (Nastavení → Napojení → Sdílená napojení) založí napojení
na službu — Ecomail, WordPress, cokoli s MCP — a vybere, s kým ho sdílí.
Klíče, hesla a hlavičky zadá prohlížeč rovnou bráně (/gw/mcp-sdilene), do
prostorů se nikdy nedostanou: ani vlastníkovi, ani těm, s kým sdílí.

Dva druhy:

* **adresa** — vzdálený MCP server (Streamable HTTP). Brána k požadavku
  přidá uložené hlavičky (typicky `Authorization: Bearer …`).
* **příkaz** — MCP server spouštěný příkazem (`npx -y balíček`), tajemství
  v proměnných prostředí. Brána ho pustí sama, v bwrap sandboxu, který vidí
  jen systém ke čtení a vlastní složku na cache (HOME) — žádný domov, žádný
  trezor, žádnou databázi. Jeden proces na člověka a jeho sezení Claude Code;
  nečinný se po IDLE ukončí.

V prostoru je za každé napojení stdio most `tools/sdilene_mcp.py <zkratka>`
(zaregistruje ho hub, `hub/connect.py` → sync_shared). Most posílá zprávy
JSON-RPC na loopback brány (`/gw/mcp-sdilene/volani`) se žetonem prostoru —
stejně jako most na počítač. Brána u **každé** zprávy znovu ověří, že člověk
je členem; odebraný tak přístup ztratí hned, ne až po restartu.

Registr: GATEWAY_DIR/mcp-sdilene/registr.json (0600, složka 0700), podle id
účtů. Záznam o změnách (bez tajemství): mcp-sdilene/zmeny.jsonl.
"""
import contextlib
import fcntl
import http.client
import json
import os
import queue
import re
import secrets
import signal
import ssl
import subprocess
import threading
import time
import unicodedata
import urllib.parse

from . import config, isolation, shared

DIR = os.environ.get("HUB_GW_MCP_SHARED_DIR", os.path.join(config.GATEWAY_DIR, "mcp-sdilene"))
REGISTRY = os.path.join(DIR, "registr.json")
LOG = os.path.join(DIR, "zmeny.jsonl")
CACHE = os.path.join(DIR, "cache")
SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
HEADER_NAME = re.compile(r"[A-Za-z0-9-]{1,64}")
# Hlavičky, které brána nastavuje sama — z uložených se nepřeberou.
OWN_HEADERS = {"host", "content-type", "content-length", "accept", "mcp-session-id",
               "connection", "transfer-encoding"}
IDLE = int(os.environ.get("HUB_GW_MCP_SHARED_IDLE", str(15 * 60)))
MAX_PROCS = int(os.environ.get("HUB_GW_MCP_SHARED_PROCS", "24"))
TIMEOUT = 180                      # jedno volání nástroje (třeba export z WordPressu)
MAX_MESSAGE = 8 * 1024 * 1024
_LOCK = threading.Lock()


# ── registr ──────────────────────────────────────────────────────────────────
@contextlib.contextmanager
def _locked():
    with _LOCK:
        os.makedirs(DIR, mode=0o700, exist_ok=True)
        with open(REGISTRY + ".lock", "a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)


def _load():
    try:
        with open(REGISTRY, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    items = data.get("napojeni") if isinstance(data, dict) else None
    return items if isinstance(items, dict) else {}


def _save(items):
    tmp = REGISTRY + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"napojeni": items}, fh, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, REGISTRY)


def _log(user, action, slug, **extra):
    entry = {"cas": time.strftime("%Y-%m-%d %H:%M:%S"), "email": (user or {}).get("email", ""),
             "akce": action, "napojeni": slug, **extra}
    try:
        fd = os.open(LOG, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _account(uid):
    return shared.ACCOUNTS.by_id(uid) if shared.ACCOUNTS and uid else None


def _label(uid):
    u = _account(uid)
    return (u.get("name") or u["email"]) if u else ""


def _public(slug, entry, uid):
    """Co z napojení smí vidět hub — bez hodnot tajemství, jen jejich jména."""
    owner = _account(entry.get("owner"))
    out = {"slug": slug, "name": entry.get("name") or slug, "kind": entry.get("kind"),
           "owner": owner["email"] if owner else "", "owner_name": _label(entry.get("owner")),
           "is_owner": entry.get("owner") == uid, "created": entry.get("created", ""),
           "mcp_name": "sdilene-" + slug}
    if entry.get("owner") == uid:
        out["members"] = [{"email": u["email"], "name": u.get("name", "")}
                          for u in (_account(m) for m in entry.get("members", []))
                          if u and u["id"] != uid]
        if entry.get("kind") == "adresa":
            out["url"] = entry.get("url", "")
            out["secrets"] = sorted(entry.get("headers") or {})
        else:
            out["command"] = " ".join(entry.get("command") or [])
            out["secrets"] = sorted(entry.get("env") or {})
    return out


def for_user(user):
    """Napojení, která `user` smí používat (svoje i nasdílená)."""
    uid = int(user["id"])
    return [_public(slug, e, uid) for slug, e in sorted(_load().items())
            if uid in e.get("members", [])]


def all_items():
    return [dict(_public(slug, e, None), members=[_label(m) for m in e.get("members", [])])
            for slug, e in sorted(_load().items())]


def member(user, slug):
    """Záznam napojení, když je `user` členem — jinak None."""
    entry = _load().get(str(slug or ""))
    if entry and int(user["id"]) in entry.get("members", []):
        return entry
    return None


# ── založení a správa ────────────────────────────────────────────────────────
def _resolve(emails):
    if isinstance(emails, str):
        emails = re.split(r"[,\s]+", emails)
    ids, unknown = [], []
    for email in emails or []:
        email = str(email or "").strip().lower()
        if not email:
            continue
        u = shared.ACCOUNTS.get(email) if shared.ACCOUNTS else None
        if not u or u.get("disabled"):
            unknown.append(email)
        elif u["id"] not in ids:
            ids.append(u["id"])
    return ids, unknown


def _slugify(name, taken):
    base = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")[:30] or "napojeni"
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}-{n}", n + 1
    return slug


def _pairs(raw, name_re, what):
    """{jméno: hodnota} z formuláře — jména podle `name_re`, hodnoty text."""
    if not raw:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"{what} jsou poškozené.")
    out = {}
    for key, value in raw.items():
        key = str(key or "").strip()
        if not key:
            continue
        if not name_re.fullmatch(key):
            raise ValueError(f"{what}: neplatné jméno „{key[:40]}“.")
        value = str(value if value is not None else "")
        if len(value) > 8000 or "\n" in value or "\r" in value:
            raise ValueError(f"{what}: hodnota u „{key}“ je moc dlouhá nebo víceřádková.")
        out[key] = value
    if len(out) > 30:
        raise ValueError(f"{what}: nejvýš 30 položek.")
    return out


def _spec(form):
    """Druh a nastavení napojení z formuláře. Vrací dict do registru."""
    kind = str(form.get("kind") or "")
    if kind == "adresa":
        url = str(form.get("url") or "").strip()
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname or len(url) > 500:
            raise ValueError("Adresa MCP serveru musí začínat https://.")
        headers = _pairs(form.get("headers"), HEADER_NAME, "Hlavičky")
        if any(h.lower() in OWN_HEADERS for h in headers):
            raise ValueError("Tyhle hlavičky nastavuje brána sama: " + ", ".join(
                h for h in headers if h.lower() in OWN_HEADERS))
        return {"kind": kind, "url": url, "headers": headers}
    if kind == "prikaz":
        command = form.get("command")
        if isinstance(command, str):
            import shlex
            try:
                command = shlex.split(command)
            except ValueError:
                raise ValueError("Příkaz nejde rozložit — zkontroluj uvozovky.") from None
        if (not isinstance(command, list) or not command or len(command) > 40
                or not all(isinstance(c, str) and c and len(c) < 2000 for c in command)):
            raise ValueError("Chybí příkaz, kterým se MCP server spouští (třeba npx -y balíček).")
        return {"kind": kind, "command": command,
                "env": _pairs(form.get("env"), ENV_NAME, "Proměnné")}
    raise ValueError("Druh napojení je adresa, nebo příkaz.")


def create(user, form):
    uid = int(user["id"])
    name = " ".join(str(form.get("name") or "").split())[:60]
    if not name:
        raise ValueError("Napojení potřebuje název.")
    spec = _spec(form)
    ids, unknown = _resolve(form.get("emaily"))
    if unknown:
        raise ValueError("Tihle lidé tu účet nemají: " + ", ".join(unknown))
    with _locked():
        items = _load()
        if any((e.get("name") or "").lower() == name.lower() and e.get("owner") == uid
               for e in items.values()):
            raise ValueError(f"Napojení „{name}“ už máš.")
        slug = _slugify(name, items)
        items[slug] = {"name": name, "owner": uid, "members": [uid] + [i for i in ids if i != uid],
                       "created": time.strftime("%Y-%m-%d %H:%M:%S"), **spec}
        _save(items)
    _log(user, "zalozit", slug, druh=spec["kind"], clenove=[_label(i) for i in ids])
    return {"ok": True, "slug": slug}


def _own(items, user, slug):
    slug = str(slug or "")
    if not SLUG.fullmatch(slug) or slug not in items:
        raise ValueError("Takové napojení není.")
    entry = items[slug]
    if entry.get("owner") != int(user["id"]) and user.get("role") != "admin":
        raise PermissionError("Napojení spravuje jen ten, kdo ho založil.")
    return slug, entry


def update(user, form):
    """Změna sdílení, případně i nastavení (nové tajemství přepíše staré;
    prázdná hodnota u existujícího jména = nechat původní)."""
    with _locked():
        items = _load()
        slug, entry = _own(items, user, form.get("slug"))
        removed = []
        if "emaily" in form:
            ids, unknown = _resolve(form.get("emaily"))
            if unknown:
                raise ValueError("Tihle lidé tu účet nemají: " + ", ".join(unknown))
            owner = entry.get("owner")
            new = ([owner] if owner else []) + [i for i in ids if i != owner]
            removed = [m for m in entry.get("members", []) if m not in new]
            entry["members"] = new
        if form.get("kind"):
            spec = _spec(form)
            key = "headers" if spec["kind"] == "adresa" else "env"
            if spec["kind"] == entry.get("kind"):
                old = entry.get(key) or {}
                spec[key] = {k: (v if v else old.get(k, "")) for k, v in spec[key].items()}
            entry.update({k: v for k, v in spec.items()})
            for drop in ({"url", "headers"} if spec["kind"] == "prikaz" else {"command", "env"}):
                entry.pop(drop, None)
        name = " ".join(str(form.get("name") or "").split())[:60]
        if name:
            entry["name"] = name
        _save(items)
    _log(user, "upravit", slug, clenove=[_label(m) for m in entry.get("members", [])])
    # Odebraní přijdou o přístup hned (ověřuje se u každé zprávy); změněné
    # nastavení chce nové procesy.
    POOL.stop(slug, removed if not form.get("kind") else None)
    return {"ok": True, "slug": slug}


def delete(user, slug):
    with _locked():
        items = _load()
        slug, entry = _own(items, user, slug)
        del items[slug]
        _save(items)
    _log(user, "smazat", slug)
    POOL.stop(slug)
    return {"ok": True, "slug": slug}


def forget_user(uid):
    """Smazaný účet: pryč ze sdílení; jeho napojení zmizí celá (klíče byly jeho)."""
    if not os.path.exists(REGISTRY):
        return
    with _locked():
        items = _load()
        gone = [s for s, e in items.items() if e.get("owner") == uid]
        for s in gone:
            del items[s]
        for e in items.values():
            e["members"] = [m for m in e.get("members", []) if m != uid]
        _save(items)
    for s in gone:
        POOL.stop(s)


# ── předávání zpráv ──────────────────────────────────────────────────────────
def _key(mid):
    return json.dumps(mid, sort_keys=True)


def _is_request(msg):
    return isinstance(msg, dict) and "method" in msg and msg.get("id") is not None


class _Remote:
    """Vzdálený MCP server (Streamable HTTP) za jedno sezení mostu."""

    def __init__(self, entry):
        self.url = urllib.parse.urlparse(entry["url"])
        self.headers = dict(entry.get("headers") or {})
        self.session = ""
        self.used = time.time()

    def send(self, msg):
        self.used = time.time()
        body = json.dumps(msg).encode("utf-8")
        headers = dict(self.headers)
        headers.update({"Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream"})
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        path = self.url.path or "/"
        if self.url.query:
            path += "?" + self.url.query
        conn = http.client.HTTPSConnection(self.url.hostname, self.url.port or 443,
                                           timeout=TIMEOUT, context=ssl.create_default_context())
        try:
            conn.request("POST", path, body=body, headers=headers)
            resp = conn.getresponse()
            if msg.get("method") == "initialize" and resp.getheader("Mcp-Session-Id"):
                self.session = resp.getheader("Mcp-Session-Id")
            if resp.status == 202 or not _is_request(msg):
                resp.read(64 * 1024)
                return []
            if resp.status == 404 and self.session:
                self.session = ""
                raise RuntimeError("Služba sezení zapomněla — Claude se připojí znovu.")
            if resp.status in (401, 403):
                raise RuntimeError(f"Služba odmítla přihlášení ({resp.status}) — "
                                   "vlastník napojení má zkontrolovat klíč.")
            if resp.status >= 400:
                raise RuntimeError(f"Služba odpověděla {resp.status}.")
            ctype = (resp.getheader("Content-Type") or "").lower()
            if "text/event-stream" in ctype:
                return self._events(resp, msg.get("id"))
            data = json.loads(resp.read(MAX_MESSAGE).decode("utf-8") or "null")
            return data if isinstance(data, list) else [data] if data else []
        finally:
            conn.close()

    @staticmethod
    def _events(resp, want):
        """Zprávy ze streamu SSE až po odpověď na náš požadavek."""
        out, data = [], []
        while True:
            line = resp.readline(MAX_MESSAGE)
            if not line:
                break
            line = line.decode("utf-8", "replace").rstrip("\r\n")
            if line.startswith("data:"):
                data.append(line[5:].lstrip())
                continue
            if line or not data:
                continue
            try:
                msg = json.loads("\n".join(data))
            except ValueError:
                msg = None
            data = []
            if isinstance(msg, dict):
                out.append(msg)
                if "method" not in msg and _key(msg.get("id")) == _key(want):
                    break
        return out

    def close(self):
        pass


class _Process:
    """MCP server spuštěný příkazem, v sandboxu, za jedno sezení mostu."""

    def __init__(self, slug, entry):
        home = os.path.join(CACHE, slug)
        os.makedirs(home, mode=0o700, exist_ok=True)
        argv = list(entry["command"])
        if config.ISOLATION == "bwrap":
            argv = isolation._bwrap(argv, home, extra_ro=())
            # Proces patří bráně: když brána skončí, skončí i on (a s ním celý
            # jmenný prostor PID) — žádná scope, kterou by musel někdo uklízet.
            argv.insert(argv.index("--"), "--die-with-parent")
        env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": home, "LANG": "C.UTF-8",
               **(entry.get("env") or {})}
        self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, env=env, cwd=home,
                                     start_new_session=True)
        self.used = time.time()
        self.waiting = {}              # id požadavku → Queue
        self.extra = []                # zprávy, na které nikdo nečeká (notifikace, dotazy serveru)
        self.lock = threading.Lock()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for raw in self.proc.stdout:
            try:
                msg = json.loads(raw.decode("utf-8"))
            except ValueError:
                continue                 # server píše na stdout i něco jiného
            if not isinstance(msg, dict):
                continue
            with self.lock:
                q = self.waiting.pop(_key(msg.get("id")), None) if "method" not in msg else None
                if q is None:
                    self.extra.append(msg)
                    del self.extra[:-50]
            if q:
                q.put(msg)
        with self.lock:
            for q in self.waiting.values():
                q.put(None)
            self.waiting.clear()

    def alive(self):
        return self.proc.poll() is None

    def send(self, msg):
        self.used = time.time()
        q = None
        if _is_request(msg):
            q = queue.Queue(1)
            with self.lock:
                self.waiting[_key(msg["id"])] = q
        try:
            self.proc.stdin.write(json.dumps(msg).encode("utf-8") + b"\n")
            self.proc.stdin.flush()
        except OSError:
            raise RuntimeError("MCP server napojení neběží (spadl při startu?).") from None
        if q is None:
            return self._take_extra()
        try:
            reply = q.get(timeout=TIMEOUT)
        except queue.Empty:
            raise RuntimeError("MCP server napojení neodpověděl včas.") from None
        if reply is None:
            raise RuntimeError("MCP server napojení skončil — vlastník má zkontrolovat "
                               "příkaz a proměnné.")
        return self._take_extra() + [reply]

    def _take_extra(self):
        with self.lock:
            out, self.extra = self.extra, []
        return out

    def close(self):
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)
        except OSError:
            pass


class Pool:
    """Běžící spojení: (zkratka, id účtu, sezení mostu) → _Remote / _Process."""

    def __init__(self):
        self.items = {}
        self.lock = threading.Lock()
        threading.Thread(target=self._reaper, daemon=True).start()

    def get(self, slug, uid, relace, entry):
        key = (slug, uid, relace)
        with self.lock:
            item = self.items.get(key)
            if item and (not isinstance(item, _Process) or item.alive()):
                return item
            if item:
                item.close()
            if entry["kind"] == "prikaz":
                procs = [k for k, v in self.items.items() if isinstance(v, _Process)]
                if len(procs) >= MAX_PROCS:
                    oldest = min(procs, key=lambda k: self.items[k].used)
                    self.items.pop(oldest).close()
                item = _Process(slug, entry)
            else:
                item = _Remote(entry)
            self.items[key] = item
            return item

    def stop(self, slug, uids=None):
        """Ukončí spojení napojení — všech, nebo jen účtů `uids`."""
        with self.lock:
            keys = [k for k in self.items if k[0] == slug and (uids is None or k[1] in uids)]
            gone = [self.items.pop(k) for k in keys]
        for item in gone:
            item.close()

    def stop_one(self, slug, uid, relace):
        with self.lock:
            item = self.items.pop((slug, uid, relace), None)
        if item:
            item.close()

    def _reaper(self):
        while True:
            time.sleep(60)
            now = time.time()
            with self.lock:
                keys = [k for k, v in self.items.items() if now - v.used > IDLE]
                gone = [self.items.pop(k) for k in keys]
            for item in gone:
                item.close()


POOL = Pool()


def call(user, slug, relace, msg):
    """Jedna zpráva z mostu v prostoru → zprávy pro Claude Code."""
    relace = str(relace or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", relace):
        raise ValueError("Neplatné sezení mostu.")
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        raise ValueError("Zpráva není JSON-RPC.")
    entry = member(user, slug)
    if not entry:
        POOL.stop(str(slug or ""), [int(user["id"])])
        raise PermissionError("Tohle napojení s tebou už nikdo nesdílí.")
    item = POOL.get(slug, int(user["id"]), relace, entry)
    try:
        return item.send(msg)
    except (OSError, http.client.HTTPException, ValueError) as exc:
        raise RuntimeError(f"Služba není k dosažení: {exc}") from None


def test(user, slug):
    """Zkusí napojení (initialize + tools/list) za `user`. Vrací počet nástrojů."""
    relace = "test-" + secrets.token_hex(8)
    try:
        call(user, slug, relace, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "claude-code-hub", "version": "1"}}})
        call(user, slug, relace, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        out = call(user, slug, relace, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    finally:
        POOL.stop_one(slug, int(user["id"]), relace)
    reply = next((m for m in out if m.get("id") == 2), None) or {}
    if reply.get("error"):
        raise RuntimeError(str(reply["error"].get("message") or reply["error"])[:300])
    return len((reply.get("result") or {}).get("tools") or [])
