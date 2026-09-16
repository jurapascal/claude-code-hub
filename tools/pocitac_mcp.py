#!/usr/bin/env python3
"""
MCP server „pocitac" — Claude v prostoru na serveru sahá na počítač uživatele.

Běží v prostoru (v sandboxu) jako stdio MCP server Claude Code. Každý nástroj
pošle úkol bráně (`$HUB_POCITAC_URL/gw/pocitac/volani` se žetonem
`$HUB_POCITAC_TOKEN`, obojí dala prostoru brána při startu) a ta ho předá hubu
na počítači uživatele (`hub/pocitac.py`). Tady se jen skládá dotaz a z odpovědi
text pro Clauda — co smí, hlídá počítač.

Zaregistruje ho hub v prostoru sám při startu (`hub/pocitac.py`, register_mcp).
Jen standardní knihovna: v sandboxu je systémový python a nic víc.

Protokol: JSON-RPC 2.0 po řádcích na stdin/stdout. Volání nástrojů běží každé
ve vlastním vlákně — dlouhý příkaz na počítači nesmí blokovat čtení souboru,
o které si Claude řekl souběžně.
"""
import base64
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

NAME = "pocitac"
VERSION = "1.1.0"
UKOLY_OD = "2.15.0"                   # appka na počítači, která úkoly na později umí
MAX_TRANSFER = 15 * 1024 * 1024       # stejný strop jako na počítači
MAX_TEXT = 80 * 1024                  # víc textu naráz Claudovi nepomůže

INSTRUCTIONS = """\
Nástroje mcp__pocitac__* sahají na **počítač uživatele** — ne na server, na kterém
běžíš. Běžné nástroje (Read, Write, Edit, Bash…) dál pracují tady na serveru
v prostoru uživatele.

- Když uživatel mluví o souborech, složkách nebo programech „na počítači", „u mě",
  „na ploše", „ve Stažených" a podobně, použij tyhle nástroje. Nejdřív zavolej
  `pocitace`: dozvíš se systém (cesty na Windows jsou C:\\Users\\…), domovskou
  složku, shell a jaký přístup uživatel povolil.
- Relativní cesty a `~` se na počítači berou od domovské složky uživatele.
- Soubory mezi počítačem a serverem kopíruj přes `stahnout` (počítač → server)
  a `nahrat` (server → počítač).
- Počítač patří uživateli: mazání, přepisování, instalace a jiné změny tam dělej
  jen na jeho výslovné přání. Hesla, klíče a přihlašovací údaje z počítače
  nevypisuj ani nekopíruj na server, pokud o to výslovně nepožádá.
- Když počítač není připojený, řekni uživateli, ať má na počítači otevřenou appku
  Claude Code Hub a v Nastavení → Účet zapnutý přístup pro Clauda ze serveru.
- Když připojený není a má se na něm něco udělat, nemusí se čekat: co jde, připrav
  tady na serveru a nech počítači úkol přes `nechat_ukol`. Až se počítač připojí,
  otevře se na něm tab s Claude Code, který úkol podle tvého zadání dodělá. Jak
  jsou úkoly daleko, ukáže `ukoly_pro_pocitac`."""

_PC = {"type": "string",
       "description": "Na kterém počítači (jméno z `pocitace`). Stačí vynechat, když je připojený jen jeden."}

TOOLS = [
    {"name": "pocitace",
     "description": "Počítače uživatele připojené k prostoru: jméno, systém, domovská složka, "
                    "shell a povolený přístup (jen čtení / plný). Zavolej, než s počítačem "
                    "začneš pracovat.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "slozka",
     "description": "Vypíše obsah složky na počítači uživatele (ne na serveru).",
     "inputSchema": {"type": "object", "properties": {
         "cesta": {"type": "string", "description": "Složka; prázdné = domovská složka."},
         "pocitac": _PC}}},
    {"name": "precist",
     "description": "Přečte textový soubor z počítače uživatele. Vrací řádky s čísly "
                    "(jako cat -n). Dlouhý soubor čti po částech přes `od` a `pocet`.",
     "inputSchema": {"type": "object", "required": ["cesta"], "properties": {
         "cesta": {"type": "string"},
         "od": {"type": "integer", "description": "První řádek, od 1."},
         "pocet": {"type": "integer", "description": "Kolik řádků (výchozí 2000)."},
         "pocitac": _PC}}},
    {"name": "hledat",
     "description": "Hledá na počítači uživatele soubory podle jména (glob, např. *.pdf nebo "
                    "src/*.py) a volitelně podle obsahu (regulární výraz). Přeskakuje .git, "
                    "node_modules a podobné složky.",
     "inputSchema": {"type": "object", "properties": {
         "cesta": {"type": "string", "description": "Kde hledat; prázdné = domovská složka."},
         "jmeno": {"type": "string", "description": "Glob na jméno souboru, výchozí *."},
         "text": {"type": "string", "description": "Regulární výraz, který má být v obsahu."},
         "bez_velikosti": {"type": "boolean", "description": "Nerozlišovat velká a malá písmena."},
         "limit": {"type": "integer", "description": "Nejvýš kolik výsledků (výchozí 100)."},
         "pocitac": _PC}}},
    {"name": "zapsat",
     "description": "Zapíše textový soubor na počítač uživatele — přepíše celý obsah, chybějící "
                    "složky založí. Potřebuje plný přístup.",
     "inputSchema": {"type": "object", "required": ["cesta", "obsah"], "properties": {
         "cesta": {"type": "string"},
         "obsah": {"type": "string"},
         "pocitac": _PC}}},
    {"name": "upravit",
     "description": "Nahradí přesný text v souboru na počítači uživatele. `stary` musí být "
                    "v souboru právě jednou (přidej okolní řádky), nebo nastav `vsechny`. "
                    "Potřebuje plný přístup.",
     "inputSchema": {"type": "object", "required": ["cesta", "stary", "novy"], "properties": {
         "cesta": {"type": "string"},
         "stary": {"type": "string"},
         "novy": {"type": "string"},
         "vsechny": {"type": "boolean", "description": "Nahradit všechny výskyty."},
         "pocitac": _PC}}},
    {"name": "spustit",
     "description": "Spustí příkaz v shellu na počítači uživatele (bash; na Windows Git Bash, "
                    "když ho nemá, cmd) a vrátí výstup a návratový kód. Bez interaktivního "
                    "vstupu. Potřebuje plný přístup.",
     "inputSchema": {"type": "object", "required": ["prikaz"], "properties": {
         "prikaz": {"type": "string"},
         "slozka": {"type": "string", "description": "Pracovní složka; prázdné = domovská."},
         "limit_s": {"type": "integer", "description": "Časový limit v sekundách (výchozí 120, nejvýš 600)."},
         "pocitac": _PC}}},
    {"name": "stahnout",
     "description": "Zkopíruje soubor (i binární, do 15 MB) z počítače uživatele sem na server.",
     "inputSchema": {"type": "object", "required": ["cesta_na_pocitaci", "cesta_na_serveru"],
                     "properties": {
         "cesta_na_pocitaci": {"type": "string"},
         "cesta_na_serveru": {"type": "string",
                              "description": "Kam soubor uložit na serveru; relativní = od pracovní složky."},
         "pocitac": _PC}}},
    {"name": "nechat_ukol",
     "description": "Nechá úkol pro Clauda na počítači uživatele, který teď není připojený "
                    "(nebo když to má počkat). Až se počítač připojí — appka Claude Code Hub "
                    "otevřená a přístup pro Clauda ze serveru zapnutý — otevře se na něm tab "
                    "s Claude Code, dostane tvoje zadání a přílohy a úkol dodělá. Zadání piš "
                    "samostatně: Claude na počítači tuhle konverzaci nevidí, ví jen to, co "
                    "napíšeš (co udělat, kde, s čím a jak poznat, že je hotovo). Co jde "
                    "připravit tady na serveru, připrav předem a přilož.",
     "inputSchema": {"type": "object", "required": ["nazev", "zadani"], "properties": {
         "nazev": {"type": "string",
                   "description": "Krátký název, jak ho uživatel uvidí, např. „Web květinářství na plochu\"."},
         "zadani": {"type": "string",
                    "description": "Celé zadání pro Clauda na počítači, nejvýš 8000 znaků."},
         "slozka": {"type": "string",
                    "description": "Složka na počítači, ve které se tab otevře (např. ~/Desktop/projekt). "
                                   "Prázdné = domovská složka."},
         "soubory": {"type": "array", "items": {"type": "string"},
                     "description": "Soubory tady na serveru, které se k úkolu přiloží (dohromady do "
                                    "15 MB, nejvýš 20). Claude na počítači se dozví, kde je najde."},
         "pocitac": {"type": "string",
                     "description": "Pro který počítač (jméno z `pocitace`). Prázdné = první, který se připojí."}}}},
    {"name": "ukoly_pro_pocitac",
     "description": "Úkoly, které čekají na počítač uživatele nebo si je už převzal (nechat_ukol): "
                    "název, stav, kdy a na kterém počítači.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "zrusit_ukol",
     "description": "Zruší úkol, který si počítač ještě nevyzvedl.",
     "inputSchema": {"type": "object", "required": ["id"], "properties": {
         "id": {"type": "string", "description": "Id úkolu z `ukoly_pro_pocitac`."}}}},
    {"name": "nahrat",
     "description": "Zkopíruje soubor (i binární, do 15 MB) ze serveru na počítač uživatele. "
                    "Potřebuje plný přístup.",
     "inputSchema": {"type": "object", "required": ["cesta_na_serveru", "cesta_na_pocitaci"],
                     "properties": {
         "cesta_na_serveru": {"type": "string"},
         "cesta_na_pocitaci": {"type": "string"},
         "pocitac": _PC}}},
]


class Failed(Exception):
    """Chyba, kterou dostane Claude jako výsledek nástroje."""


# ── brána ────────────────────────────────────────────────────────────────────
# Brána je na loopbacku stroje; proxy z prostředí by dotaz poslala ven.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def gateway(op, args=None, computer="", wait=150):
    url = os.environ.get("HUB_POCITAC_URL", "").rstrip("/")
    token = os.environ.get("HUB_POCITAC_TOKEN", "")
    if not url or not token:
        raise Failed("Most na počítač tu není — prostor ho od brány nedostal. "
                     "Běží tenhle Claude opravdu v prostoru na serveru s Code Hubem?")
    body = json.dumps({"op": op, "args": args or {}, "computer": computer or ""}).encode("utf-8")
    req = urllib.request.Request(url + "/gw/pocitac/volani", data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "X-Hub-Pocitac": token})
    try:
        with _OPENER.open(req, timeout=wait) as res:
            data = json.loads(res.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            data = json.loads(exc.read().decode("utf-8") or "{}")
        except ValueError:
            data = {}
        raise Failed(data.get("error") or f"Brána odpověděla {exc.code}.") from None
    except (urllib.error.URLError, OSError) as exc:
        raise Failed(f"Brána neodpovídá: {getattr(exc, 'reason', exc)}") from None
    except ValueError:
        raise Failed("Brána poslala nečitelnou odpověď.") from None
    if not data.get("ok"):
        raise Failed(data.get("error") or "Nepovedlo se.")
    return data


def on_pc(op, args, computer, wait=150):
    data = gateway(op, args, computer, wait)
    return data.get("result") or {}, data.get("computer") or "počítač"


# ── nástroje ─────────────────────────────────────────────────────────────────
def _size(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return ""


def _stamp(ts):
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(int(ts)))
    except (TypeError, ValueError, OverflowError):
        return ""


def _server_path(raw):
    path = os.path.expanduser(str(raw or "").strip())
    if not path:
        raise Failed("Chybí cesta na serveru.")
    return os.path.abspath(path)


def t_pocitace(a):
    data = gateway("list", wait=20)
    comps = data.get("computers") or []
    online = [c for c in comps if c.get("online") and c.get("access") in ("cteni", "vse")]
    waiting = sum(1 for u in data.get("ukoly") or [] if u.get("state") == "ceka")
    later = f"Na počítač čeká úkolů na později: {waiting} (ukoly_pro_pocitac)." if waiting else ""
    if not online:
        return ("Žádný počítač uživatele teď není připojený. Musí mít na počítači otevřenou "
                "appku Claude Code Hub přihlášenou k tomuhle serveru a v Nastavení → Účet "
                "zapnutý přístup „Claude ze serveru na tomhle počítači\". Když to může počkat, "
                "nech počítači úkol (nechat_ukol). " + later).strip()
    lines = []
    for c in online:
        lines.append(f"- {c.get('name')}: {c.get('system') or '?'}, uživatel {c.get('user') or '?'}, "
                     f"domovská složka {c.get('home') or '?'}, shell {c.get('shell') or '?'}, "
                     f"přístup: {c.get('access_label')}")
    return "Připojené počítače:\n" + "\n".join(lines + ([later] if later else []))


def t_slozka(a):
    res, pc = on_pc("ls", {"path": a.get("cesta") or ""}, a.get("pocitac"))
    entries = res.get("entries") or []
    out = [f"{res.get('path')} na počítači {pc} — {res.get('total', len(entries))} položek"]
    for e in entries:
        if e.get("type") == "dir":
            out.append(f"  [složka]  {e.get('name')}/")
        else:
            out.append(f"  {_size(e.get('size')):>9}  {_stamp(e.get('mtime'))}  {e.get('name')}"
                       + ("  (odkaz)" if e.get("link") else ""))
    if res.get("total", 0) > len(entries):
        out.append(f"  … a dalších {res['total'] - len(entries)} (zužuj přes `hledat`)")
    return "\n".join(out)


def t_precist(a):
    args = {"path": a.get("cesta") or ""}
    for src, dst in (("od", "offset"), ("pocet", "limit")):
        if a.get(src):
            args[dst] = a[src]
    res, pc = on_pc("read", args, a.get("pocitac"))
    total = res.get("lines_total")
    head = (f"{res.get('path')} na počítači {pc} — řádky {res.get('from')}–{res.get('to')}"
            + (f" z {total}" if total else ""))
    if res.get("truncated"):
        head += f" (dál pokračuj od řádku {res.get('to', 0) + 1})"
    return head + "\n" + (res.get("text") or "(prázdný soubor)")


def t_hledat(a):
    args = {"path": a.get("cesta") or "", "name": a.get("jmeno") or "*",
            "text": a.get("text") or "", "ignore_case": a.get("bez_velikosti") is True}
    if a.get("limit"):
        args["limit"] = a["limit"]
    res, pc = on_pc("find", args, a.get("pocitac"), wait=150)
    matches = res.get("matches") or []
    if not matches:
        return f"Na počítači {pc} v {res.get('path')} nic nenalezeno."
    out = [f"Na počítači {pc} v {res.get('path')} — {len(matches)} výsledků"
           + (" (neúplné, zužuj hledání)" if res.get("truncated") else "")]
    for m in matches:
        if m.get("line"):
            out.append(f"{m.get('path')}:{m.get('line')}: {m.get('text', '')}")
        else:
            out.append(str(m.get("path")) + ("/" if m.get("type") == "dir" else ""))
    return "\n".join(out)


def t_zapsat(a):
    if not isinstance(a.get("obsah"), str):
        raise Failed("Chybí obsah.")
    res, pc = on_pc("write", {"path": a.get("cesta") or "", "content": a["obsah"]}, a.get("pocitac"))
    kind = "založen" if res.get("created") else "přepsán"
    return f"Soubor {res.get('path')} na počítači {pc} {kind} ({_size(res.get('bytes'))})."


def t_upravit(a):
    res, pc = on_pc("edit", {"path": a.get("cesta") or "", "old": a.get("stary"),
                             "new": a.get("novy"), "all": a.get("vsechny") is True},
                    a.get("pocitac"))
    return f"Upraveno {res.get('path')} na počítači {pc} — nahrazeno výskytů: {res.get('replaced')}."


def t_spustit(a):
    try:
        limit = int(a.get("limit_s") or 120)
    except (TypeError, ValueError):
        limit = 120
    limit = max(1, min(600, limit))
    res, pc = on_pc("run", {"command": a.get("prikaz") or "", "cwd": a.get("slozka") or "",
                            "timeout": limit}, a.get("pocitac"), wait=limit + 60)
    code = res.get("exit_code")
    head = (f"Na počítači {pc} ({res.get('shell')}, {res.get('cwd')}): "
            + ("PŘERUŠENO po limitu " + str(limit) + " s" if res.get("timed_out")
               else f"návratový kód {code}")
            + f", {res.get('seconds')} s")
    out = [head]
    if res.get("stdout"):
        out += ["--- výstup ---", res["stdout"].rstrip("\n")]
    if res.get("stderr"):
        out += ["--- chybový výstup ---", res["stderr"].rstrip("\n")]
    if not res.get("stdout") and not res.get("stderr"):
        out.append("(bez výstupu)")
    if res.get("note"):
        out.append(res["note"])
    return "\n".join(out)


def t_stahnout(a):
    target = _server_path(a.get("cesta_na_serveru"))
    res, pc = on_pc("download", {"path": a.get("cesta_na_pocitaci") or ""}, a.get("pocitac"),
                    wait=300)
    try:
        raw = base64.b64decode(res.get("data") or "", validate=True)
    except ValueError:
        raise Failed("Počítač poslal poškozená data.") from None
    if os.path.isdir(target):
        target = os.path.join(target, os.path.basename(str(res.get("path") or "soubor")))
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    tmp = f"{target}.pocitac-{os.getpid()}-{threading.get_ident()}"
    with open(tmp, "wb") as fh:
        fh.write(raw)
    os.replace(tmp, target)
    return f"Staženo z počítače {pc}: {res.get('path')} → {target} na serveru ({_size(len(raw))})."


def t_nahrat(a):
    source = _server_path(a.get("cesta_na_serveru"))
    if not os.path.isfile(source):
        raise Failed(f"Na serveru soubor {source} není.")
    size = os.path.getsize(source)
    if size > MAX_TRANSFER:
        raise Failed(f"Soubor má {_size(size)}, najednou jde nahrát nejvýš {_size(MAX_TRANSFER)}.")
    with open(source, "rb") as fh:
        data = base64.b64encode(fh.read()).decode("ascii")
    res, pc = on_pc("upload", {"path": a.get("cesta_na_pocitaci") or "", "data": data,
                               "name": os.path.basename(source)}, a.get("pocitac"), wait=300)
    return f"Nahráno na počítač {pc}: {source} → {res.get('path')} ({_size(res.get('bytes'))})."


def _older(version, than):
    """Je verze appky starší než `than`? Neznámá verze se bere jako starší."""
    def num(v):
        return [int(p) if p.isdigit() else 0 for p in str(v or "").split(".")[:3]]
    return not version or num(version) < num(than)


def t_nechat_ukol(a):
    paths = a.get("soubory") or []
    if not isinstance(paths, list):
        raise Failed("`soubory` má být seznam cest na serveru.")
    files, total = [], 0
    for raw in paths:
        source = _server_path(raw)
        if not os.path.isfile(source):
            raise Failed(f"Na serveru soubor {source} není.")
        total += os.path.getsize(source)
        if total > MAX_TRANSFER:
            raise Failed(f"Přílohy mají dohromady přes {_size(MAX_TRANSFER)} — zabal je, nebo "
                         "přilož jen to podstatné.")
        with open(source, "rb") as fh:
            files.append({"name": os.path.basename(source),
                          "data": base64.b64encode(fh.read()).decode("ascii")})
    data = gateway("ukol-novy", {"title": a.get("nazev") or "", "text": a.get("zadani") or "",
                                 "folder": a.get("slozka") or "", "computer": a.get("pocitac") or "",
                                 "files": files}, wait=120)
    ukol = data.get("ukol") or {}
    out = [f"Úkol „{ukol.get('title')}\" (id {ukol.get('id')}) čeká na počítač"
           + (f" {ukol['computer']}" if ukol.get("computer") else "") + "."]
    try:
        comps = gateway("list", wait=20).get("computers") or []
    except Failed:
        comps = []
    target = str(ukol.get("computer") or "").casefold()
    mine = [c for c in comps if not target or target in (str(c.get("name")).casefold(),
                                                         str(c.get("id")).casefold())]
    online = [c for c in mine if c.get("online") and c.get("access") in ("cteni", "vse")]
    if online:
        out.append(f"Počítač {online[0].get('name')} je připojený — vyzvedne si ho hned.")
    else:
        out.append("Až se počítač připojí (appka Claude Code Hub otevřená, přístup pro Clauda "
                   "ze serveru zapnutý), otevře se na něm tab s Claude Code a úkol dodělá. "
                   "S plným přístupem se spustí sám, s přístupem jen ke čtení ho uživatel "
                   "na počítači potvrdí.")
    old = [c for c in mine if _older(c.get("version"), UKOLY_OD)]
    if old:
        out.append("Pozor: appka na počítači " + ", ".join(str(c.get("name")) for c in old)
                   + f" je starší než {UKOLY_OD} a úkoly na později ještě neumí. Řekni "
                   "uživateli, ať ji aktualizuje (Nastavení → Aktualizace).")
    return "\n".join(out)


def t_ukoly(a):
    items = gateway("list", wait=20).get("ukoly") or []
    if not items:
        return "Žádné úkoly na později tu nejsou."
    lines = []
    for u in items:
        line = f"- {u.get('id')} · „{u.get('title')}\" — {u.get('state_label')}"
        if u.get("by"):
            line += f" ({u['by']}, {_stamp(u.get('changed'))})"
        line += f"; zadáno {_stamp(u.get('created'))}"
        if u.get("computer"):
            line += f"; pro počítač {u['computer']}"
        if u.get("files"):
            line += "; přílohy: " + ", ".join(u["files"])
        lines.append(line)
    return "Úkoly na později (nejnovější první):\n" + "\n".join(lines)


def t_zrusit_ukol(a):
    ukol = gateway("ukol-zrusit", {"id": a.get("id") or ""}, wait=20).get("ukol") or {}
    return f"Úkol „{ukol.get('title')}\" je zrušený, na počítač už nepřijde."


HANDLERS = {"pocitace": t_pocitace, "slozka": t_slozka, "precist": t_precist,
            "hledat": t_hledat, "zapsat": t_zapsat, "upravit": t_upravit,
            "spustit": t_spustit, "stahnout": t_stahnout, "nahrat": t_nahrat,
            "nechat_ukol": t_nechat_ukol, "ukoly_pro_pocitac": t_ukoly,
            "zrusit_ukol": t_zrusit_ukol}


# ── MCP po stdio ─────────────────────────────────────────────────────────────
_OUT = threading.Lock()


def send(msg):
    line = json.dumps(msg, ensure_ascii=False)
    with _OUT:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


def call_tool(rid, params):
    name = params.get("name")
    args = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
    handler = HANDLERS.get(name)
    if not handler:
        return send({"jsonrpc": "2.0", "id": rid,
                     "error": {"code": -32602, "message": f"Neznámý nástroj {name}"}})
    try:
        text, error = handler(args), False
    except Failed as exc:
        text, error = str(exc), True
    except Exception as exc:                      # chyba tady, ne na počítači
        text, error = f"Nástroj {name} selhal: {exc}", True
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT] + "\n… (zkráceno — požádej o menší část)"
    send({"jsonrpc": "2.0", "id": rid,
          "result": {"content": [{"type": "text", "text": text}], "isError": error}})


def handle(msg):
    method = msg.get("method")
    rid = msg.get("id")
    if method == "initialize":
        asked = (msg.get("params") or {}).get("protocolVersion") or "2025-06-18"
        return send({"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": asked,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": NAME, "version": VERSION},
            "instructions": INSTRUCTIONS}})
    if method == "tools/list":
        return send({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
    if method == "tools/call":
        threading.Thread(target=call_tool, args=(rid, msg.get("params") or {}),
                         daemon=True).start()
        return None
    if method == "ping":
        return send({"jsonrpc": "2.0", "id": rid, "result": {}})
    if rid is not None:                           # notifikace se nepotvrzují
        return send({"jsonrpc": "2.0", "id": rid,
                     "error": {"code": -32601, "message": f"Metodu {method} neznám."}})
    return None


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if isinstance(msg, dict):
            handle(msg)


if __name__ == "__main__":
    main()
