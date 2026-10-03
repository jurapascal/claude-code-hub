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
        dostupne.append({"id": pid, "name": p.get("name") or pid.split("@")[0],
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
            out.append({"name": name or n, "description": desc, "source": zdroj, **(extra or {})})
    return out


def skilly(obnovit=False):
    """Skilly, které Claude umí: moje příkazy (~/.claude/skills), ze zapnutých
    pluginů Claude Code a postupy v Obsidian Brainu (načítá se na požádání)."""
    if not obnovit and _SKILLY["data"] and time.time() - _SKILLY["at"] < 120:
        return _SKILLY["data"]
    out = _skill_dir(core.SKILLS_DIR, "moje")
    # Pluginy: jen uživatelsky zapnuté (projektové platí jen ve své složce).
    rc, raw, _ = _cli(["list", "--json"], timeout=60)
    for p in (_json_z(raw) or []) if rc == 0 else []:
        if not isinstance(p, dict) or not p.get("enabled") or p.get("scope") != "user":
            continue
        plugin = (p.get("id") or "").split("@")[0]
        out += _skill_dir(os.path.join(p.get("installPath") or "", "skills"), "plugin", {"plugin": plugin})
    brain = os.path.join(core.BRAIN, "skills")
    if os.path.isdir(brain):
        for kat in sorted(os.listdir(brain)):
            if os.path.isdir(os.path.join(brain, kat)) and not kat.startswith((".", "_")):
                out += _skill_dir(os.path.join(brain, kat), "obsidian", {"category": kat})
    data = {"skills": out}
    _SKILLY.update(at=time.time(), data=data)
    return data


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
