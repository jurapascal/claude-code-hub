#!/usr/bin/env python3
"""
Doinstaluje všechny AI agenty z katalogu hubu (Codex, Gemini, opencode, aider,
Ollama) a k Ollamě stáhne základní lokální modely. Claude Code řeší instalačka
sama. Volá ji install.sh / install.ps1 (při instalaci ke stažení, nebo když to
člověk odsouhlasí); jde pustit i ručně:

    python3 tools/install_agents.py            # nainstaluje, co chybí
    python3 tools/install_agents.py --dry-run  # jen vypíše, co by udělala
    python3 tools/install_agents.py --no-models  # bez stahování modelů Ollamy

Co se nepovede (třeba chybí sudo pro Ollamu), jen se vypíše — instalace
ostatního jede dál. Výstup: jeden řádek na agenta, `ok:` / `chyba:` / `přeskočeno:`.
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from hub import agents  # noqa: E402

# Lokální modely k Ollamě: malý univerzální a malý na kód (dohromady ~7 GB).
OLLAMA_MODELY = ["llama3.2:3b", "qwen2.5-coder:7b"]
DRY = "--dry-run" in sys.argv
BEZ_MODELU = "--no-models" in sys.argv


def say(kind, text):
    print(f"{kind}: {text}", flush=True)


def bash():
    return shutil.which("bash") or "bash"


def run(cmd, timeout=1800):
    if DRY:
        say("příkaz", cmd)
        return 0
    # ~/.local/bin do PATH — tam jdou uživatelské instalace (npm prefix, uv, opencode).
    env = {**os.environ, "PATH": os.path.expanduser("~/.local/bin") + os.pathsep + os.environ.get("PATH", "")}
    try:
        return subprocess.run([bash(), "-lc", cmd], timeout=timeout, env=env,
                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
    except (OSError, subprocess.TimeoutExpired):
        return 1


def sudo_bez_hesla():
    return os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0) or \
        subprocess.call(["sudo", "-n", "true"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0


def uv_je():
    if shutil.which("uv") or os.path.exists(os.path.expanduser("~/.local/bin/uv")):
        return True
    return run("curl -LsSf https://astral.sh/uv/install.sh | sh") == 0


def main():
    cat = agents.catalog(None)
    for key, spec in cat.items():
        label = spec.get("label") or key
        if key == "claude":
            continue
        if agents.which(spec):
            say("ok", f"{label} už je nainstalovaný")
            continue
        cmd = agents.install_cmd(spec)
        if not cmd:
            say("přeskočeno", f"{label}: pro tenhle systém není postup")
            continue
        if cmd.startswith("npm ") and not shutil.which("npm"):
            say("přeskočeno", f"{label}: chybí Node.js (npm)")
            continue
        if cmd.startswith("uv ") and not uv_je():
            say("přeskočeno", f"{label}: nejde nainstalovat uv")
            continue
        if key == "ollama" and sys.platform.startswith("linux") and not sudo_bez_hesla():
            say("přeskočeno", f"{label}: instalace chce heslo správce — spusť jednou: {cmd}")
            continue
        say("instaluju", label)
        if run(cmd) == 0 and (DRY or agents.which(spec)):
            say("ok", f"{label} nainstalovaný")
        else:
            say("chyba", f"{label} se nenainstaloval — zkus ručně: {cmd}")

    ollama = shutil.which("ollama")
    if BEZ_MODELU:
        return 0
    if not ollama and not DRY:
        say("přeskočeno", "modely Ollamy: Ollama není nainstalovaná")
        return 0
    for model in OLLAMA_MODELY:
        say("stahuju", f"model {model}")
        # Server Ollamy musí běžet; na Linuxu po instalaci naběhne jako služba, jinak ho zkusíme pustit.
        rc = run(f"ollama pull {model}", timeout=3600)
        if rc != 0 and not DRY:
            run("nohup ollama serve >/dev/null 2>&1 &")
            subprocess.call(["sleep", "4"])
            rc = run(f"ollama pull {model}", timeout=3600)
        say("ok" if rc == 0 else "chyba", f"model {model}" + ("" if rc == 0 else " se nestáhl"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
