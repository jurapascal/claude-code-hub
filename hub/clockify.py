"""Stopky do Clockify — Start/Stop u projektu, bez agenta.

Tohle je ruční měření času: člověk klikne Start u otevřeného projektu, hub
založí v Clockify běžící záznam, a po Stopu je vidět, jak dlouho se na čem
dělalo. S AI to nemá nic společného — žádný prompt, žádná session.

Vedle toho žije automat `~/.claude/hooks/clockify-log.py`, který měří sessions
označené `/klient`. Oba používají **stejný** `~/.claude/clockify/config.json`,
takže klíč se nastavuje jen jednou. Když soubor chybí, stopky se neukážou.

Který projekt v Clockify patří ke které složce, si hub pamatuje v
`hub-config.json` pod `clockify_map` (cesta → id projektu). Vybere se jednou,
podruhé už je předvyplněný.
"""
import datetime as dt
import json
import os
import threading
import urllib.error
import urllib.request

from . import core

TRACKER_DIR = os.path.join(core.CLAUDE_DIR, "clockify")
CONFIG_PATH = os.path.join(TRACKER_DIR, "config.json")
API = "https://api.clockify.me/api/v1"
TIMEOUT = 12

# Seznam projektů se mění zřídka a načítá se přes síť — drží se v paměti.
_CACHE = {"projects": None, "at": 0.0}
_LOCK = threading.Lock()


def _settings():
    """Přihlašovací údaje ze sdíleného config.json, nebo None, když chybí."""
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        return None
    if not (cfg.get("apiKey") and cfg.get("workspaceId") and cfg.get("userId")):
        return None
    return cfg


def _call(cfg, method, path, body=None):
    req = urllib.request.Request(
        API + path,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        method=method,
        headers={"X-Api-Key": cfg["apiKey"], "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
        raw = res.read()
    return json.loads(raw) if raw.strip() else None


def _projects(cfg, force=False):
    """Aktivní projekty workspace; drží se pět minut, ať se neptáme pořád."""
    import time
    with _LOCK:
        fresh = _CACHE["projects"] is not None and time.time() - _CACHE["at"] < 300
        if fresh and not force:
            return _CACHE["projects"]
    rows = _call(cfg, "GET", f"/workspaces/{cfg['workspaceId']}"
                             "/projects?page-size=200&archived=false") or []
    out = sorted(({"id": p["id"], "name": p["name"]} for p in rows),
                 key=lambda p: p["name"].lower())
    with _LOCK:
        _CACHE["projects"] = out
        _CACHE["at"] = time.time()
    return out


def _running(cfg):
    """Právě běžící záznam, nebo None. Clockify jich víc než jeden nedovolí."""
    rows = _call(cfg, "GET", f"/workspaces/{cfg['workspaceId']}"
                             f"/user/{cfg['userId']}/time-entries?in-progress=true") or []
    if not rows:
        return None
    entry = rows[0]
    start = (entry.get("timeInterval") or {}).get("start") or ""
    return {
        "id": entry.get("id"),
        "projectId": entry.get("projectId"),
        "description": entry.get("description") or "",
        "start": start,
        "seconds": _elapsed(start),
    }


def _elapsed(start):
    try:
        began = dt.datetime.strptime(start, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return 0
    return max(0, int((dt.datetime.utcnow() - began).total_seconds()))


def _now():
    return dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _mapping():
    return dict(core.CONFIG.get("clockify_map") or {})


def _remember(path, project_id):
    """Zapamatuje si, že tahle složka patří k tomuhle projektu v Clockify."""
    if not path:
        return
    mapping = _mapping()
    if project_id:
        mapping[path] = project_id
    else:
        mapping.pop(path, None)
    core.save_config({"clockify_map": mapping})


def _task(cfg, project_id):
    """Úkol z configu (`taskName`) na projektu — založí se, když ještě není.

    Automat pod něj věší svoje záznamy, tak ať ruční stopky nekončí jinde.
    """
    want = (cfg.get("taskName") or "").strip()
    if not want:
        return None
    try:
        tasks = _call(cfg, "GET", f"/workspaces/{cfg['workspaceId']}"
                                  f"/projects/{project_id}/tasks?page-size=200") or []
        for task in tasks:
            if (task.get("name") or "").strip() == want:
                return task.get("id")
        made = _call(cfg, "POST", f"/workspaces/{cfg['workspaceId']}"
                                  f"/projects/{project_id}/tasks", {"name": want})
        return (made or {}).get("id")
    except (urllib.error.URLError, OSError, ValueError):
        return None          # bez úkolu se záznam založit dá, jen bude holý


def status(path=""):
    """Stav pro panel: běží něco, jaké jsou projekty a co patří k téhle složce."""
    cfg = _settings()
    if not cfg:
        return {"ready": False,
                "hint": "Clockify není nastavený — chybí ~/.claude/clockify/config.json."}
    try:
        running = _running(cfg)
        projects = _projects(cfg)
    except urllib.error.HTTPError as exc:
        detail = "Klíč do Clockify neplatí." if exc.code in (401, 403) else f"HTTP {exc.code}"
        return {"ready": False, "hint": "Clockify neodpovídá: " + detail}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {"ready": False, "hint": f"Clockify neodpovídá: {exc}"}
    return {
        "ready": True,
        "running": running,
        "projects": projects,
        "selected": _mapping().get(path or "", ""),
        "rate": cfg.get("rateCzk"),
    }


def start(path="", project_id="", description=""):
    """Spustí měření. Když už něco běží, nejdřív to zastaví."""
    cfg = _settings()
    if not cfg:
        return {"error": "Clockify není nastavený."}
    if not project_id:
        return {"error": "Vyber projekt, na kterém děláš."}
    try:
        if _running(cfg):
            _call(cfg, "PATCH", f"/workspaces/{cfg['workspaceId']}"
                                f"/user/{cfg['userId']}/time-entries", {"end": _now()})
        body = {
            "start": _now(),
            "projectId": project_id,
            "description": (description or "").strip(),
            "billable": bool(cfg.get("billable", True)),
        }
        task = _task(cfg, project_id)
        if task:
            body["taskId"] = task
        _call(cfg, "POST", f"/workspaces/{cfg['workspaceId']}/time-entries", body)
    except urllib.error.HTTPError as exc:
        return {"error": f"Clockify odmítl start (HTTP {exc.code})."}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {"error": f"Start se nepovedl: {exc}"}
    _remember(path, project_id)
    return status(path)


def stop(path=""):
    """Zastaví běžící měření a vrátí, kolik se naměřilo."""
    cfg = _settings()
    if not cfg:
        return {"error": "Clockify není nastavený."}
    try:
        current = _running(cfg)
        if not current:
            return dict(status(path), stopped=0)
        _call(cfg, "PATCH", f"/workspaces/{cfg['workspaceId']}"
                            f"/user/{cfg['userId']}/time-entries", {"end": _now()})
    except urllib.error.HTTPError as exc:
        return {"error": f"Clockify odmítl stop (HTTP {exc.code})."}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {"error": f"Stop se nepovedl: {exc}"}
    return dict(status(path), stopped=current["seconds"])


def choose(path="", project_id=""):
    """Jen si zapamatuje projekt u složky, nic nespouští."""
    _remember(path, project_id)
    return status(path)
