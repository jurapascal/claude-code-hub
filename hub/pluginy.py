"""
Pluginy — dvojí:

1. **Pluginy Claude Code** (skilly, příkazy, agenti, hooky, MCP servery
   z „marketplace"). Hub je jen nabízí naklikáním: katalog, instalace,
   zapnout/vypnout, aktualizace, odinstalace a správa marketplace. Všechno
   dělá `claude plugin …`, hub nic nekopíruje sám. Projeví se v nových
   chatech (Claude Code pluginy načítá při startu).

2. **Pluginy appky** — rozšíření samotného hubu: složka v
   `~/.claude/hub-plugins/<id>/` s `plugin.json` a JS/CSS, které se načtou
   do okna appky (static/pluginy.js jim dá API: tlačítka v Rychlých akcích,
   reakce na události, toast, úložiště, volání hubu). Běží se stejnými
   právy jako appka — umí tedy cokoli, co umíš ty. Proto jsou po přidání
   vypnuté a zapnout je jde jen vědomě, s varováním.

    plugin.json:
    {"name": "Moje rozšíření", "version": "1.0.0", "description": "…",
     "author": "…", "main": "plugin.js", "style": "plugin.css"}
"""
import json
import os
import re
import shutil
import subprocess
import threading
import time

from . import core

ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
SOUBORY = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
           ".json": "application/json; charset=utf-8", ".svg": "image/svg+xml",
           ".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp",
           ".woff2": "font/woff2"}


# ── Pluginy Claude Code ──────────────────────────────────────────────────────
_KATALOG = {"at": 0.0, "data": None}
_KATALOG_LOCK = threading.Lock()
KATALOG_TTL = 600


def _cli(args, timeout=180):
    """(returncode, stdout, stderr) z `claude plugin …`."""
    claude = shutil.which("claude")
    if not claude:
        return 127, "", "Claude Code CLI není nainstalovaný."
    try:
        r = subprocess.run([claude, "plugin", *args], capture_output=True, text=True,
                           timeout=timeout, stdin=subprocess.DEVNULL, cwd=core.HOME,
                           creationflags=getattr(core, "_NO_WINDOW", 0))
        return r.returncode, r.stdout or "", r.stderr or ""
    except subprocess.TimeoutExpired:
        return 124, "", "Claude Code neodpověděl včas."
    except OSError as exc:
        return 126, "", str(exc)


def _json_z(text):
    """JSON z výstupu CLI — i když před ním něco napsal (varování)."""
    text = (text or "").strip()
    for start in (0, text.find("{"), text.find("[")):
        if start < 0:
            continue
        try:
            return json.loads(text[start:])
        except ValueError:
            continue
    # --json u akcí vrací jeden řádek — vezme se poslední, který je JSON.
    for line in reversed(text.splitlines()):
        try:
            return json.loads(line)
        except ValueError:
            continue
    return None


def _logo_zdroje(source):
    """Klíč loga vydavatele pluginu: avatar jeho účtu na GitHubu (`gh:owner`)."""
    # Plugin uložený v repozitáři marketplace Anthropicu (`anthropics/…`) nemusí
    # být od Anthropicu (třeba context7 od Upstash) — jeho logo by lhalo,
    # proto tam zůstane ikonka. Logo jen tam, kde repozitář patří vydavateli.
    if not isinstance(source, dict):
        return ""
    m = re.search(r"github\.com[/:]([A-Za-z0-9-]+)/", source.get("url") or "") or \
        re.match(r"([A-Za-z0-9-]+)/", source.get("repo") or "")
    if not m or m.group(1).lower() in ("anthropics", "anthropic"):
        return ""
    return "gh:" + m.group(1)


def katalog(obnovit=False):
    """{"installed": [...], "available": [...], "marketplaces": [...]}
    — dostupných je přes dva tisíce, proto se drží v paměti 10 minut."""
    with _KATALOG_LOCK:
        if not obnovit and _KATALOG["data"] and time.time() - _KATALOG["at"] < KATALOG_TTL:
            return _KATALOG["data"]
    rc, out, err = _cli(["list", "--available", "--json"], timeout=120)
    data = _json_z(out) if rc == 0 else None
    if not isinstance(data, dict):
        return {"error": (err or out).strip()[-300:] or "Seznam pluginů se nenačetl.",
                "installed": [], "available": [], "marketplaces": []}
    rc2, out2, _ = _cli(["marketplace", "list", "--json"], timeout=60)
    trhy = _json_z(out2) if rc2 == 0 else []
    instalovane = {}
    for p in data.get("installed") or []:
        pid = p.get("id") or ""
        # Projektové instalace se v appce neukazují zvlášť — stačí vědět, že
        # plugin je doma; zapínat/vypínat jde ten uživatelský.
        cur = instalovane.get(pid)
        if cur is None or p.get("scope") == "user":
            instalovane[pid] = {"id": pid, "version": p.get("version", ""), "scope": p.get("scope", ""),
                                "enabled": bool(p.get("enabled")), "projectPath": p.get("projectPath", ""),
                                "lastUpdated": p.get("lastUpdated", "")}
    dostupne = []
    for p in data.get("available") or []:
        pid = p.get("pluginId") or ""
        dostupne.append({"id": pid, "logo": _logo_zdroje(p.get("source")),
                         "name": p.get("name") or pid.split("@")[0],
                         "description": (p.get("description") or "")[:400],
                         "marketplace": p.get("marketplaceName", ""),
                         "installs": int(p.get("installCount") or 0),
                         "installed": pid in instalovane})
    dostupne.sort(key=lambda p: -p["installs"])
    out = {"installed": sorted(instalovane.values(), key=lambda p: p["id"]),
           "available": dostupne, "marketplaces": trhy if isinstance(trhy, list) else []}
    with _KATALOG_LOCK:
        _KATALOG.update(at=time.time(), data=out)
    return out


def _zapomen_katalog():
    with _KATALOG_LOCK:
        _KATALOG.update(at=0.0)


def _platne_id(pid):
    return bool(re.fullmatch(r"[A-Za-z0-9@._/:+-]{1,200}", str(pid or "")))


def akce(co, pid="", prikaz_sha="", zdroj=""):
    """Instalace a správa pluginu Claude Code. Vrací {"ok", "message"} a u
    pluginu, který se instaluje spuštěním příkazu z marketplace, nejdřív
    {"potvrdit": text příkazu, "sha": …} — ten musí odsouhlasit člověk."""
    if co in ("install", "uninstall", "enable", "disable", "update") and not _platne_id(pid):
        return {"ok": False, "message": "Neplatný název pluginu."}
    if co == "install":
        args = ["install", pid, "--json"]
        if prikaz_sha:
            if not re.fullmatch(r"[0-9a-f]{64}", prikaz_sha):
                return {"ok": False, "message": "Neplatné potvrzení příkazu."}
            args += ["--accept-command", prikaz_sha]
        rc, out, err = _cli(args)
        res = _json_z(out) or {}
        shown = res.get("shownCommand") if isinstance(res, dict) else None
        if rc != 0 and isinstance(shown, dict) and shown.get("sha256"):
            return {"ok": False, "potvrdit": shown.get("command") or shown.get("text") or "",
                    "sha": shown["sha256"],
                    "message": "Plugin se instaluje spuštěním příkazu — podívej se na něj a potvrď."}
    elif co == "uninstall":
        rc, out, err = _cli(["uninstall", pid, "--json"])
    elif co in ("enable", "disable"):
        rc, out, err = _cli([co, pid])
    elif co == "update":
        rc, out, err = _cli(["update", pid])
    elif co == "marketplace-add":
        zdroj = str(zdroj or "").strip()
        if not zdroj or len(zdroj) > 300 or zdroj.startswith("-"):
            return {"ok": False, "message": "Zadej adresu marketplace (GitHub owner/repo nebo URL)."}
        rc, out, err = _cli(["marketplace", "add", zdroj], timeout=300)
    elif co == "marketplace-update":
        rc, out, err = _cli(["marketplace", "update"] + ([pid] if _platne_id(pid) else []), timeout=300)
    else:
        return {"ok": False, "message": "Neznámá akce."}
    _zapomen_katalog()
    text = (err or out).strip()
    res = _json_z(out)
    if isinstance(res, dict) and res.get("message"):
        text = res["message"]
    if rc == 0:
        core.log(f"pluginy: {co} {pid or zdroj}")
        return {"ok": True, "message": _hotovo(co) + " Projeví se v nově otevřených chatech."}
    return {"ok": False, "message": (text or "Nepovedlo se.")[-400:]}


def _hotovo(co):
    return {"install": "Nainstalováno.", "uninstall": "Odinstalováno.", "enable": "Zapnuto.",
            "disable": "Vypnuto.", "update": "Aktualizováno.", "marketplace-add": "Marketplace přidán.",
            "marketplace-update": "Marketplace aktualizovány."
            }.get(co, "Hotovo.")


# ── Loga ─────────────────────────────────────────────────────────────────────
# Skutečná loga vydavatelů: avatar účtu na GitHubu (pluginy) a ikonka webu
# služby z jejího vlastního webu (napojení). Hub je stáhne sám, uloží na disk
# (~/.claude/hub-logos/) a vydává stránce — prohlížeč nesahá na cizí servery
# a nikdo se nedozví, co si prohlížíš. Jen rastrové obrázky, žádné SVG.
LOGO_RE = re.compile(r"^(gh:[A-Za-z0-9-]{1,39}|d:[a-z0-9.-]{3,80})$")
_LOGO_PRAZDNE = b""


def logo_domeny(url):
    """`d:notion.com` z adresy MCP serveru (mcp.notion.com → notion.com)."""
    import urllib.parse
    host = (urllib.parse.urlparse(str(url or "")).hostname or "").lower()
    labels = host.split(".")
    if len(labels) > 2:
        labels = labels[-3:] if labels[-2] in ("co", "com", "org", "net") and len(labels[-1]) == 2 else labels[-2:]
    dom = ".".join(labels)
    return "d:" + dom if re.fullmatch(r"[a-z0-9.-]{3,80}", dom) and "." in dom else ""


def _logo_slozka():
    return os.path.join(core.CLAUDE_DIR, "hub-logos")


def _obrazek(data):
    """Typ obrázku podle prvních bajtů (jen rastr), nebo ''."""
    for magic, typ in ((b"\x89PNG", "image/png"), (b"\xff\xd8\xff", "image/jpeg"), (b"GIF8", "image/gif"),
                       (b"\x00\x00\x01\x00", "image/x-icon")):
        if data.startswith(magic):
            return typ
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return ""


def _stahni(url, limit=400_000, timeout=8):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 claude-code-hub"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.read(limit + 1)[:limit], res.geturl()


def _ikonka_webu(domena):
    """Adresa ikonky z HTML webu služby (apple-touch-icon má přednost), nebo /favicon.ico."""
    import urllib.parse
    base = "https://" + domena + "/"
    kandidati = []
    for host in (domena, "www." + domena):
        try:
            html, konec = _stahni("https://" + host + "/", 250_000)
            text = html.decode("utf-8", "replace")
            nalezene = []
            for tag in re.findall(r"<link\b[^>]*>", text, re.I):
                rel = re.search(r"rel=[\"']([^\"']+)[\"']", tag, re.I)
                href = re.search(r"href=[\"']([^\"']+)[\"']", tag, re.I)
                if rel and href and "icon" in rel.group(1).lower() and not href.group(1).lower().split("?")[0].endswith(".svg"):
                    nalezene.append((0 if "apple" in rel.group(1).lower() else 1, urllib.parse.urljoin(konec, __import__("html").unescape(href.group(1)))))
            kandidati += [u for _, u in sorted(nalezene)]
            break
        except Exception:
            continue
    return kandidati + [base + "favicon.ico", "https://www." + domena + "/favicon.ico"]


def logo(klic):
    """(bajty, content-type) loga, nebo None. Výsledek (i „žádné logo") se drží
    na disku — služba se nezkouší pořád dokola."""
    if not LOGO_RE.match(str(klic or "")):
        return None
    os.makedirs(_logo_slozka(), exist_ok=True)
    cesta = os.path.join(_logo_slozka(), klic.replace(":", "_"))
    try:
        age = time.time() - os.path.getmtime(cesta)
        with open(cesta, "rb") as fh:
            data = fh.read()
        if data or age < 7 * 86400:                # prázdný soubor = „nemá logo", zkusí se za týden znovu
            return (data, _obrazek(data)) if data else None
    except OSError:
        pass
    data = b""
    try:
        if klic.startswith("gh:"):
            data, _ = _stahni(f"https://github.com/{klic[3:]}.png?size=96")
        else:
            for url in _ikonka_webu(klic[2:]):
                try:
                    got, _ = _stahni(url)
                except Exception:
                    continue
                if _obrazek(got) and len(got) > 100:
                    data = got
                    break
    except Exception:
        data = b""
    if data and not _obrazek(data):
        data = b""
    try:
        with open(cesta, "wb") as fh:
            fh.write(data)
    except OSError:
        pass
    return (data, _obrazek(data)) if data else None


# ── Katalog napojení (oficiální registr MCP serverů) ─────────────────────────
REGISTR = "https://registry.modelcontextprotocol.io/v0/servers"
# Oficiální napojení firem (ověřená jména v registru). Co v registru zrovna
# není, se tiše vynechá — seznam jde rozšiřovat bez rizika.
DOPORUCENE = [
    "com.notion/mcp", "app.linear/linear", "com.atlassian/atlassian-mcp-server", "com.figma.mcp/mcp",
    "com.canva.mcp/mcp", "com.stripe/mcp", "com.paypal.mcp/mcp", "com.supabase/mcp", "com.neon/mcp",
    "com.airtable/mcp", "net.todoist/mcp", "com.monday/monday.com", "com.gitlab/mcp",
    "io.github.github/github-mcp-server", "com.asana/mcp", "io.sentry/mcp", "com.hubspot/mcp",
    "com.intercom/mcp", "com.webflow/mcp", "com.wix/mcp", "com.zapier/mcp", "com.make/mcp-server",
    "io.github.zoom/zoom-meetings", "com.postman/postman-mcp-server", "com.cloudflare.mcp/mcp",
    "io.prisma/mcp", "io.github.grafana/mcp-grafana", "com.apify/apify-mcp-server",
    "io.github.firecrawl/firecrawl-mcp-server", "ai.exa/exa", "co.huggingface/hf-mcp-server",
    "com.microsoft/microsoft-learn-mcp", "com.close/close-mcp",
]
_NAPOJENI = {"at": 0.0, "data": None}


def _registr(url, timeout=15):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "claude-code-hub"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.loads(res.read().decode("utf-8", "replace") or "{}")


def _polozka(server, oficialni=False):
    """Jedno napojení z registru → co ukáže appka; None bez použitelné adresy."""
    remotes = [r for r in server.get("remotes") or []
               if r.get("type") in ("streamable-http", "sse") and r.get("url") and "{" not in r["url"]]
    if not remotes:
        return None
    remotes.sort(key=lambda r: r.get("type") != "streamable-http")   # http má přednost
    name = server.get("name", "")
    ns = name.split("/")[0].split(".")
    # com.notion → Notion, io.github.zoom → Zoom (u GitHubu je firma až třetí)
    firma = ns[2] if ns[:2] == ["io", "github"] and len(ns) > 2 else (ns[1] if len(ns) > 1 else ns[0])
    title = server.get("title") or firma.capitalize()
    return {"name": name, "title": title[:60], "description": (server.get("description") or "")[:300],
            "url": remotes[0]["url"], "official": oficialni, "logo": logo_domeny(remotes[0]["url"])}


def _cache_napojeni():
    return os.path.join(core.CLAUDE_DIR, "hub-mcp-katalog.json")


def napojeni_katalog(hledat=""):
    """Doporučená oficiální napojení, nebo výsledky hledání v registru."""
    import concurrent.futures
    import urllib.parse
    hledat = str(hledat or "").strip()[:80]
    if hledat:
        # Registr hledá jedno slovo v názvu — pošle se nejdelší, zbytek se
        # dofiltruje tady (i v popisu), ať „microsoft learn" najde, co má.
        slova = [w for w in re.split(r"\s+", hledat.lower()) if w]
        try:
            d = _registr(f"{REGISTR}?version=latest&limit=100&search={urllib.parse.quote(max(slova, key=len))}")
        except Exception as exc:
            return {"error": f"Katalog teď neodpovídá: {exc}", "items": []}
        doporucene = set(DOPORUCENE)
        items = [_polozka(x.get("server") or {}, (x.get("server") or {}).get("name") in doporucene)
                 for x in d.get("servers") or []]
        items = [i for i in items if i and all(
            w in (i["name"] + " " + i["title"] + " " + i["description"]).lower() for w in slova)]
        items.sort(key=lambda i: not i["official"])
        return {"items": items[:40]}
    if _NAPOJENI["data"] and time.time() - _NAPOJENI["at"] < 86400:
        return _NAPOJENI["data"]
    try:
        with open(_cache_napojeni(), encoding="utf-8") as fh:
            cached = json.load(fh)
        if time.time() - cached.get("at", 0) < 86400 and cached.get("items"):
            for i in cached["items"]:
                i.setdefault("logo", logo_domeny(i.get("url")))   # starší cache loga neměla
            _NAPOJENI.update(at=cached["at"], data={"items": cached["items"]})
            return _NAPOJENI["data"]
    except (OSError, ValueError):
        pass

    def jedno(name):
        try:
            return _polozka((_registr(f"{REGISTR}/{urllib.parse.quote(name, safe='')}/versions/latest")
                             .get("server") or {}), True)
        except Exception:
            return None
    with concurrent.futures.ThreadPoolExecutor(12) as ex:
        items = [i for i in ex.map(jedno, DOPORUCENE) if i]
    items.sort(key=lambda i: i["title"].lower())
    if not items:
        return {"error": "Katalog napojení teď neodpovídá — zkus to za chvíli.", "items": []}
    _NAPOJENI.update(at=time.time(), data={"items": items})
    try:
        with open(_cache_napojeni(), "w", encoding="utf-8") as fh:
            json.dump({"at": time.time(), "items": items}, fh)
    except OSError:
        pass
    return _NAPOJENI["data"]


# ── Skilly ───────────────────────────────────────────────────────────────────
_SKILLY = {"at": 0.0, "data": None}


def _frontmatter(path):
    """(name, description) z hlavičky SKILL.md — jen to, co je potřeba."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read(6000)
    except OSError:
        return "", ""
    if not text.startswith("---"):
        return "", ""
    head = text[3:].split("\n---", 1)[0]
    out, key, buf = {}, None, []
    for line in head.splitlines():
        m = re.match(r"^([A-Za-z_-]+):\s*(.*)$", line)
        if m and not line.startswith((" ", "\t")):
            if key:
                out[key] = " ".join(buf).strip()
            key, val = m.group(1).lower(), m.group(2).strip()
            buf = [] if val in (">", "|", ">-", "|-") else [val]
        elif key:
            buf.append(line.strip())
    if key:
        out[key] = " ".join(buf).strip()
    clean = lambda v: str(v or "").strip().strip('"').strip("'")
    return clean(out.get("name")), clean(out.get("description"))[:500]


def _skill_dir(base, zdroj, extra=None):
    out = []
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return out
    for n in names:
        f = os.path.join(base, n, "SKILL.md")
        if os.path.isfile(f):
            name, desc = _frontmatter(f)
            out.append({"name": name or n, "description": desc, "source": zdroj, "_dir": os.path.join(base, n),
                        **(extra or {})})
    return out


def _skilly_zdroj(zdroj):
    """Skilly jednoho zdroje (moje | plugin | obsidian) — ať se dají načítat po částech."""
    if zdroj == "moje":
        return _skill_dir(core.SKILLS_DIR, "moje")
    out = []
    if zdroj == "plugin":
        # Jen uživatelsky zapnuté pluginy (projektové platí jen ve své složce).
        rc, raw, _ = _cli(["list", "--json"], timeout=60)
        for p in (_json_z(raw) or []) if rc == 0 else []:
            if not isinstance(p, dict) or not p.get("enabled") or p.get("scope") != "user":
                continue
            plugin = (p.get("id") or "").split("@")[0]
            out += _skill_dir(os.path.join(p.get("installPath") or "", "skills"), "plugin", {"plugin": plugin})
    elif zdroj == "obsidian":
        brain = os.path.join(core.BRAIN, "skills")
        if os.path.isdir(brain):
            for kat in sorted(os.listdir(brain)):
                if os.path.isdir(os.path.join(brain, kat)) and not kat.startswith((".", "_")):
                    out += _skill_dir(os.path.join(brain, kat), "obsidian", {"category": kat})
    return out


_SKILLY_KESH = {}


def skilly(zdroj="", obnovit=False):
    """Skilly, které Claude umí: moje příkazy (~/.claude/skills), ze zapnutých
    pluginů Claude Code a postupy v Obsidian Brainu (načítá se na požádání).
    Po zdrojích, aby se v appce ukazovaly postupně, jak se načtou."""
    zdroje = [zdroj] if zdroj in ("moje", "plugin", "obsidian") else ["moje", "plugin", "obsidian"]
    out = []
    for z in zdroje:
        hit = _SKILLY_KESH.get(z)
        if obnovit or not hit or time.time() - hit[0] > 120:
            hit = (time.time(), _skilly_zdroj(z))
            _SKILLY_KESH[z] = hit
        out += hit[1]
    return {"skills": [{k: v for k, v in x.items() if k != "_dir"} for x in out]}


def skill_detail(zdroj, name, kde=""):
    """Celý popis skillu, SKILL.md a seznam souborů — složku si hledá server
    sám podle jména (klient žádnou cestu neposílá)."""
    skilly(zdroj)
    for x in _SKILLY_KESH.get(zdroj, (0, []))[1]:
        if x["name"] == name and (not kde or kde in (x.get("plugin"), x.get("category"))):
            base = x["_dir"]
            try:
                with open(os.path.join(base, "SKILL.md"), encoding="utf-8", errors="replace") as fh:
                    text = fh.read(80000)
            except OSError:
                text = ""
            files = []
            for root, dirs, names in os.walk(base):
                dirs[:] = [d for d in dirs if not d.startswith(".") and d != "node_modules"]
                for n in sorted(names):
                    full = os.path.join(root, n)
                    if os.path.islink(full):
                        continue
                    try:
                        files.append({"path": os.path.relpath(full, base).replace(os.sep, "/"),
                                      "size": os.path.getsize(full)})
                    except OSError:
                        pass
                    if len(files) >= 200:
                        break
            try:
                updated = max(os.path.getmtime(os.path.join(base, f["path"])) for f in files) if files else 0
            except OSError:
                updated = 0
            return {k: v for k, v in x.items() if k != "_dir"} | {"text": text, "files": files, "updated": updated}
    return {"error": "Takový skill tu není."}


# ── Pluginy appky ────────────────────────────────────────────────────────────
def slozka():
    return os.path.join(core.CLAUDE_DIR, "hub-plugins")


def _manifest(pid):
    path = os.path.join(slozka(), pid, "plugin.json")
    try:
        with open(path, encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def zapnute():
    return [p for p in core.CONFIG.get("hub_plugins_enabled") or [] if ID_RE.match(str(p))]


def povoleno():
    """Na serveru je může správce vypnout všem (HUB_PLUGINS=0)."""
    return os.environ.get("HUB_PLUGINS", "1") != "0"


def appka_seznam():
    """Pluginy appky ve složce, se stavem zapnutí."""
    out = []
    on = set(zapnute())
    try:
        names = sorted(os.listdir(slozka()))
    except OSError:
        names = []
    for pid in names:
        if not ID_RE.match(pid) or not os.path.isdir(os.path.join(slozka(), pid)):
            continue
        m = _manifest(pid)
        if m is None:
            out.append({"id": pid, "name": pid, "error": "Chybí nebo je rozbitý plugin.json.", "enabled": False})
            continue
        out.append({"id": pid, "name": str(m.get("name") or pid)[:80],
                    "version": str(m.get("version") or "")[:20],
                    "description": str(m.get("description") or "")[:400],
                    "author": str(m.get("author") or "")[:80],
                    "main": _bezpecny_soubor(m.get("main"), (".js",)),
                    "style": _bezpecny_soubor(m.get("style"), (".css",)),
                    "enabled": pid in on})
    return {"plugins": out, "dir": slozka(), "allowed": povoleno()}


def _bezpecny_soubor(name, pripony):
    name = str(name or "")
    if not name or name.startswith(("/", "\\")) or ".." in name.replace("\\", "/").split("/"):
        return ""
    return name if os.path.splitext(name)[1].lower() in pripony else ""


def appka_zapni(pid, on):
    if not ID_RE.match(str(pid or "")) or _manifest(pid) is None:
        return {"ok": False, "message": "Takový plugin tu není."}
    if on and not povoleno():
        return {"ok": False, "message": "Pluginy appky jsou tady vypnuté správcem."}
    seznam = [p for p in zapnute() if p != pid] + ([pid] if on else [])
    core.save_config({"hub_plugins_enabled": seznam})
    core.log(f"plugin appky {pid}: {'zapnut' if on else 'vypnut'}")
    return {"ok": True}


def appka_pridej(url):
    """Plugin z gitu (GitHub URL / owner/repo). Po přidání je vypnutý."""
    url = str(url or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", url):
        url = f"https://github.com/{url}.git"
    if not re.match(r"^https://[A-Za-z0-9.-]+/[^\s]+$", url):
        return {"ok": False, "message": "Zadej adresu repozitáře (https://… nebo owner/repo)."}
    git = shutil.which("git")
    if not git:
        return {"ok": False, "message": "Na tomhle stroji chybí git."}
    pid = re.sub(r"[^a-z0-9._-]+", "-", url.rstrip("/").rsplit("/", 1)[-1].lower().removesuffix(".git")).strip("-")[:60]
    if not ID_RE.match(pid or ""):
        return {"ok": False, "message": "Z adresy nejde poznat název pluginu."}
    cil = os.path.join(slozka(), pid)
    if os.path.exists(cil):
        return {"ok": False, "message": f"Plugin „{pid}“ už tu je."}
    os.makedirs(slozka(), exist_ok=True)
    try:
        r = subprocess.run([git, "clone", "--depth", "1", "--", url, cil], capture_output=True, text=True,
                           timeout=180, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "message": f"Stažení se nepovedlo: {exc}"}
    if r.returncode != 0:
        shutil.rmtree(cil, ignore_errors=True)
        return {"ok": False, "message": "Stažení se nepovedlo: " + (r.stderr or "").strip()[-300:]}
    if _manifest(pid) is None:
        shutil.rmtree(cil, ignore_errors=True)
        return {"ok": False, "message": "V repozitáři není plugin.json — tohle není plugin appky."}
    core.log(f"plugin appky {pid}: přidán z {url}")
    return {"ok": True, "id": pid}


def appka_odeber(pid):
    if not ID_RE.match(str(pid or "")):
        return {"ok": False, "message": "Takový plugin tu není."}
    cil = os.path.join(slozka(), pid)
    root = os.path.realpath(slozka())
    if not os.path.isdir(cil) or os.path.islink(cil) or os.path.dirname(os.path.realpath(cil)) != root:
        return {"ok": False, "message": "Takový plugin tu není."}
    if pid in zapnute():
        core.save_config({"hub_plugins_enabled": [p for p in zapnute() if p != pid]})
    shutil.rmtree(cil)
    core.log(f"plugin appky {pid}: odebrán")
    return {"ok": True}


def appka_ukazka():
    """Založí ukázkový plugin (vypnutý) — šablona pro vlastní rozšíření."""
    zdroj = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugin-ukazka")
    cil = os.path.join(slozka(), "ukazka")
    if os.path.exists(cil):
        return {"ok": True, "id": "ukazka", "message": "Ukázka už tu je."}
    os.makedirs(slozka(), exist_ok=True)
    shutil.copytree(zdroj, cil)
    return {"ok": True, "id": "ukazka"}


def appka_soubor(pid, rel):
    """(cesta, content-type) souboru zapnutého pluginu, nebo None."""
    if not povoleno() or pid not in zapnute():
        return None
    rel = str(rel or "")
    if not rel or ".." in rel.replace("\\", "/").split("/"):
        return None
    ctype = SOUBORY.get(os.path.splitext(rel)[1].lower())
    if not ctype:
        return None
    base = os.path.realpath(os.path.join(slozka(), pid))
    full = os.path.realpath(os.path.join(base, *rel.split("/")))
    if os.path.commonpath([base, full]) != base or not os.path.isfile(full):
        return None
    return full, ctype
