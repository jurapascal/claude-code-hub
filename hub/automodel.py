"""
Automatická volba modelu a effortu podle zadání.

Bublina (composer.js) rozhoduje sama pravidly hned při odeslání. Tady je
druhý, přesnější názor: Haiku přečte zadání a řekne, kolik práce to je.
Trvá to kolem pěti vteřin, proto se ptá už při psaní (když se člověk na
chvíli zastaví) a při odeslání je odpověď hotová — zpráva kvůli tomu
nečeká.

Jede přes `claude -p` na předplatném, ne na API kreditu. Bez MCP serverů,
hooků, nástrojů a bez ukládání konverzace, ať se nezakládá přepis v seznamu
chatů a start je co nejkratší.
"""
import collections
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading

MODELS = ("haiku", "sonnet", "opus")
EFFORTS = ("low", "medium", "high", "xhigh")
MAX_TEXT = 4000
TIMEOUT = 25

_CACHE = collections.OrderedDict()
_ZAMEK = threading.Lock()          # najednou jen jeden dotaz, další počká
_JSON = re.compile(r"\{[^{}]*\}")

PROMPT = """You pick the Claude model and reasoning effort for ONE request sent \
to a coding agent (Claude Code). The user writes mostly in Czech. Cost matters: \
choose the cheapest option that will still do the job well.

- haiku + low: trivial chat, a quick factual question, a greeting, "díky", \
reading/showing something, a one-word or yes/no answer.
- sonnet + low: simple question about code or a tool, small lookup, rename, \
typo, a single obvious command (commit, push, deploy, status).
- sonnet + medium: ordinary coding task in one place — add/change a small \
feature, fix a clear bug, write a short text/e-mail, edit CSS.
- opus + high: multi-file change, unclear bug to debug, new feature with \
design decisions, review, data migration, anything on production data.
- opus + xhigh: architecture/design of a system, big refactor, security \
audit, long multi-part assignment, hard problem that needs deep thinking.

Reply with ONLY one line of JSON, model is exactly one of haiku/sonnet/opus: {"model":"...","effort":"..."}

Request:
"""


def _claude():
    path = shutil.which("claude")
    if path:
        return path
    for d in (os.path.expanduser("~/.local/bin"), os.path.expanduser("~/.claude/local")):
        cand = os.path.join(d, "claude.exe" if os.name == "nt" else "claude")
        if os.path.isfile(cand):
            return cand
    return ""


def _env():
    """Prostředí bez proměnných běžící session — jinak by se `claude -p`
    tvářil jako její dítě a psal do jejího přepisu."""
    env = {k: v for k, v in os.environ.items()
           if not (k.startswith("CLAUDE_CODE_") or k in ("CLAUDECODE", "CLAUDE_PID",
                                                        "CLAUDE_EFFORT"))}
    return env


def _parse(out):
    for m in reversed(_JSON.findall(out or "")):
        try:
            data = json.loads(m)
        except ValueError:
            continue
        # Haiku občas vrátí celé id („claude-haiku-4-5-…") místo jména.
        model = next((m for m in MODELS if m in str(data.get("model") or "").lower()), "")
        effort = str(data.get("effort") or "").lower()
        if model in MODELS and effort in EFFORTS:
            return {"model": model, "effort": effort}
    return {}


def classify(text):
    """{"model", "effort"} podle Haiku, nebo {} když to nejde (bez Claude
    Code, bez přihlášení, vypršel čas). Stejný text se neptá dvakrát."""
    text = str(text or "").strip()[:MAX_TEXT]
    if not text:
        return {}
    key = hashlib.sha1(text.encode("utf-8")).hexdigest()
    with _ZAMEK:
        if key in _CACHE:
            _CACHE.move_to_end(key)
            return dict(_CACHE[key])
        binary = _claude()
        if not binary:
            return {}
        args = [binary, "-p", "--model", "haiku", "--strict-mcp-config",
                "--mcp-config", '{"mcpServers":{}}', "--setting-sources", "",
                "--tools", "", "--no-session-persistence", PROMPT + text]
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=TIMEOUT,
                               cwd=tempfile.gettempdir(), env=_env(),
                               stdin=subprocess.DEVNULL,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError):
            return {}
        vysledek = _parse(r.stdout)
        if vysledek:
            _CACHE[key] = vysledek
            while len(_CACHE) > 300:
                _CACHE.popitem(last=False)
        return dict(vysledek)


# ── výchozí model a effort zůstávají ──────────────────────────────────────────
# `/model` a `/effort` si Claude Code ukládá do ~/.claude/settings.json jako
# výchozí pro nové sessions. Automatika ale přepíná jen tenhle tab: bez vrácení
# by po jednom „díky" startoval na Haiku každý další chat, i Claude Code
# v terminálu mimo hub. Před přepnutím se proto obě hodnoty zapamatují a po
# chvíli (až je Claude Code zapíše) vrátí. Víc přepnutí za sebou vrací na
# stav před prvním z nich.
KEYS = ("model", "effortLevel")
VRATIT_PO = 8.0
_VRACENI = {"snap": None, "timer": None}
_VRACENI_ZAMEK = threading.Lock()


def _settings_path():
    return os.path.join(os.path.expanduser("~"), ".claude", "settings.json")


def _read_settings():
    try:
        with open(_settings_path(), encoding="utf-8-sig") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _restore():
    with _VRACENI_ZAMEK:
        snap = _VRACENI["snap"]
        _VRACENI.update(snap=None, timer=None)
    if snap is None:
        return
    data = _read_settings()
    if data is None:
        return
    changed = False
    for k in KEYS:
        if k in snap and data.get(k) != snap[k]:
            data[k] = snap[k]
            changed = True
        elif k not in snap and k in data:
            del data[k]
            changed = True
    if not changed:
        return
    path = _settings_path()
    tmp = path + ".hub-auto.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass


def keep_default(extend=False):
    """Zavolat těsně před automatickým `/model` / `/effort`: zapamatuje si
    výchozí hodnoty (jen poprvé v řadě) a za VRATIT_PO vteřin je vrátí.

    `extend` = příkaz právě doběhl, odložit vrácení: Claude Code při každém
    `/model` i `/effort` přepíše celý soubor ze své paměti (i s modelem), takže
    vracet se smí až po posledním z nich. Bez rozjetého vracení nedělá nic —
    zapamatoval by si už přepnutou hodnotu."""
    with _VRACENI_ZAMEK:
        if extend and _VRACENI["snap"] is None:
            return {"ok": False}
        if _VRACENI["snap"] is None:
            data = _read_settings()
            if data is None:
                return {"ok": False}
            _VRACENI["snap"] = {k: data[k] for k in KEYS if k in data}
        if _VRACENI["timer"]:
            _VRACENI["timer"].cancel()
        timer = threading.Timer(VRATIT_PO, _restore)
        timer.daemon = True
        _VRACENI["timer"] = timer
        timer.start()
    return {"ok": True}
