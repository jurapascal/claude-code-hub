"""
Instalace v okně — to, co dřív dělal člověk v terminálu, teď běží za něj.

Instalačky ke stažení (.exe, AppImage, .dmg — viz packaging/) v sobě mají
Python i zdroj hubu. Po spuštění nakopírují zdroj do ~/.claude/hub-src
a pustí hub rovnou odtamtud s `--setup`. Hub pak v okně neukáže taby, ale
tuhle instalaci: spustí `install.sh --app` (Windows `install.ps1 -App`),
průběh řádek po řádku posílá stránce (hub/static/setup.js) a po doběhnutí
se restartuje už do nainstalované appky, kde naváže průvodce.

`--app` je `--yes` bez ptaní, jen navíc doinstaluje, co appka potřebuje
(Claude Code, na Windows i Git kvůli bashi), a hooky i zástupce v nabídce
míří na Python, kterým instalace běží (HUB_PYTHON) — na Macu a Windows jiný
v systému být nemusí.
"""
import os
import re
import subprocess
import sys
import threading
import time

from . import core

# Jak se tahle instance pustila: `claude-hub.py --setup` (packaging/launcher.py).
ACTIVE = core.SETUP_MODE

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_LOCK = threading.Lock()
STATE = {"running": False, "done": False, "ok": False, "lines": [], "error": "",
         "started": 0.0, "finished": 0.0}
MAX_LINES = 400
TIMEOUT = 45 * 60          # prohlížeč pro Playwright a Obsidian stahují dlouho


def python_for_install():
    """Python, na který mají mířit hooky a zástupce. Okno na Windows běží pod
    pythonw.exe, který nic nevypíše — instalačka potřebuje jeho dvojče."""
    exe = sys.executable or ""
    folder, name = os.path.split(exe)
    if name.lower() == "pythonw.exe":
        twin = os.path.join(folder, "python.exe")
        if os.path.isfile(twin):
            return twin
    return exe


def installer_argv(src=None):
    """Příkaz instalačky ze zdroje `src` (výchozí ~/.claude/hub-src) nebo None."""
    src = src or core.SRC_DIR
    if core.IS_WINDOWS:
        path = os.path.join(src, "install.ps1")
        if not os.path.isfile(path):
            return None
        return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", path, "-App"]
    path = os.path.join(src, "install.sh")
    if not os.path.isfile(path) or not core.BASH:
        return None
    return [core.BASH, path, "--app"]


def installer_env():
    env = dict(os.environ)
    python = python_for_install()
    env["HUB_PYTHON"] = python
    # Instalačka volá `python3`/`python` i sama — ať je to tenhle.
    env["PATH"] = os.path.dirname(python) + os.pathsep + env.get("PATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _add(line):
    line = _ANSI.sub("", line).rstrip()
    if not line.strip():
        return
    with _LOCK:
        STATE["lines"].append(line)
        if len(STATE["lines"]) > MAX_LINES:
            del STATE["lines"][:len(STATE["lines"]) - MAX_LINES]


def _run(argv):
    kwargs = {"cwd": core.SRC_DIR,
              "stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE,
              "stderr": subprocess.STDOUT, "env": installer_env()}
    if core.IS_WINDOWS:
        kwargs["creationflags"] = 0x08000000          # CREATE_NO_WINDOW
    else:
        # Bez řídicího terminálu: `sudo` se pak nemá kde ptát na heslo a hned
        # skončí, místo aby visel v konzoli, kterou nikdo nevidí.
        kwargs["start_new_session"] = True
    ok, error = False, ""
    try:
        proc = subprocess.Popen(argv, **kwargs)
        deadline = time.time() + TIMEOUT
        for raw in iter(proc.stdout.readline, b""):
            _add(raw.decode("utf-8", "replace"))
            if time.time() > deadline:
                proc.kill()
                error = "Instalace trvala moc dlouho."
                break
        code = proc.wait()
        ok = code == 0 and not error
        if not ok and not error:
            error = f"Instalace skončila chybou (kód {code})."
    except Exception as exc:
        error = f"Instalaci se nepodařilo spustit: {exc}"
    with _LOCK:
        STATE.update(running=False, done=True, ok=ok, error=error,
                     finished=time.time())
    core.log(f"instalace v okně: {'ok' if ok else error}")


def start():
    """Spustí instalaci na pozadí (podruhé nic neudělá, dokud běží)."""
    argv = installer_argv()
    with _LOCK:
        begin = not STATE["running"] and bool(argv)
        if not STATE["running"] and not argv:
            STATE.update(done=True, ok=False,
                         error="Instalačka ve zdroji chybí — stáhni instalaci znovu.")
        elif begin:
            STATE.update(running=True, done=False, ok=False, error="", lines=[],
                         started=time.time(), finished=0.0)
    if begin:
        threading.Thread(target=_run, args=(argv,), daemon=True).start()
    return state()


def _copy():
    out = dict(STATE)
    out["lines"] = list(STATE["lines"])
    return out


def state():
    with _LOCK:
        out = _copy()
    out["active"] = ACTIVE
    out["installed"] = os.path.isfile(os.path.join(core.CLAUDE_DIR, "claude-hub.py"))
    return out
