"""Restart hubu po aktualizaci — a taby zpátky tam, kde byly.

Aktualizace vymění soubory pod běžícím procesem, takže nová verze se projeví
až po startu. Dřív si to člověk musel zavřít a otevřít sám a přišel při tom
o všechny otevřené taby. Tenhle modul to dělá za něj:

  1. `snapshot(hub)`  zapíše, co je otevřené — u agentů i id konverzace,
                      takže se dá pokračovat přes `claude --resume`
  2. `relaunch()`     pustí novou instanci a tuhle ukončí
  3. `take()`         nová instance si stav jednou přečte a soubor zahodí

Proč nová instance a ne `execv`: okno na Linuxu běží v tomhle procesu
(WebKitGTK) a jeho smyčka se z vlákna obsluhy požadavku bezpečně ukončit nedá.
Nový proces si otevře vlastní okno a tenhle pak může spadnout celý.

Terminály restart nepřežijí — pseudoterminál patří procesu, který umírá s ním.
Proto se neobnovuje „běžící program", ale konverzace: Claude Code naváže tam,
kde skončil, což je přesně to, o co při restartu jde.
"""
import json
import os
import subprocess
import sys
import threading
import time

from . import core

STATE_PATH = os.path.join(core.CLAUDE_DIR, "hub-restore.json")
LAUNCHER = os.path.join(core.CLAUDE_DIR, "claude-hub.py")

# Starší soubor nemá cenu obnovovat — když se hub nepustil hodinu, člověk už
# dávno dělá něco jiného a taby z minula by ho jen zmátly.
MAX_AGE = 3600


def _resume_for(hub, session):
    """Id konverzace, kterou tab drží (nebo ''), ať se dá pokračovat.

    Stejná cesta jako u seznamu konverzací: značka HUB_TAB od Stop hooku,
    jinak nejnovější přepis ze složky projektu.
    """
    try:
        transcript = core._tab_transcript(session.id, session.path or core.HOME,
                                          session.started)
        if transcript:
            return os.path.basename(transcript)[:-6]
    except Exception:
        pass
    return session.resume or ""


def snapshot(hub):
    """Zapíše otevřené taby. Vrací, kolik jich bylo."""
    tabs = []
    for session in list(hub.sessions.values()):
        if session.exited:
            continue
        agent = session.agent or ""
        # Konverzaci má smysl hledat jen u Clauda — ostatní agenti `--resume`
        # neumí a holý terminál nemá co obnovovat.
        resume = ""
        if session.kind == "project" or session.kind.startswith("slash:"):
            if (agent or core.default_agent()) == "claude":
                resume = _resume_for(hub, session)
        tabs.append({
            "kind": session.kind,
            "path": session.path or "",
            "title": session.title or "",
            "agent": agent,
            "model": session.model or "",
            "resume": resume,
        })
    data = {"at": int(time.time()), "version": core.version(), "tabs": tabs}
    try:
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, STATE_PATH)
    except OSError as exc:
        core.log(f"restart: stav tabů se nepodařilo uložit: {exc}", "warn")
        return 0
    core.log(f"restart: uloženo {len(tabs)} tabů")
    return len(tabs)


def take():
    """Přečte uložené taby a soubor smaže — obnovuje se jednou, ne pokaždé."""
    try:
        with open(STATE_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    try:
        os.remove(STATE_PATH)
    except OSError:
        pass
    if not isinstance(data, dict):
        return []
    if time.time() - int(data.get("at") or 0) > MAX_AGE:
        core.log("restart: uložené taby jsou staré, neobnovuju")
        return []
    tabs = data.get("tabs")
    return tabs if isinstance(tabs, list) else []


def _spawn():
    """Pustí novou instanci hubu, odpojenou od téhle."""
    launcher = LAUNCHER if os.path.isfile(LAUNCHER) else os.path.abspath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir,
                     "claude-hub.py"))
    if not os.path.isfile(launcher):
        raise RuntimeError("Nenašel jsem claude-hub.py, ze kterého se hub pouští.")
    argv = [sys.executable, launcher]
    kwargs = {"cwd": core.HOME, "close_fds": True}
    if os.name == "nt":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP — ať nové okno nezmizí
        # s tímhle procesem.
        kwargs["creationflags"] = 0x00000008 | 0x00000200
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(argv, **kwargs)


def relaunch(hub, delay=0.6):
    """Uloží taby, pustí novou instanci a tuhle ukončí.

    Vrací hned, ať stihne odejít odpověď na požadavek — vypnutí běží ve vlákně.
    """
    count = snapshot(hub)

    def go():
        time.sleep(delay)
        try:
            _spawn()
        except Exception as exc:
            core.log(f"restart: nová instance nenaběhla: {exc}", "error")
            return
        core.log("restart: nová instance spuštěna, končím")
        # Terminály stejně umírají s procesem; tohle je pošle spát řízeně,
        # ať po sobě Claude Code stihne zavřít přepisy.
        try:
            hub.shutdown()
        except Exception:
            pass
        # os._exit, protože okno drží hlavní smyčku a obyčejný sys.exit by
        # z tohohle vlákna proces neukončil.
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)

    threading.Thread(target=go, daemon=True).start()
    return {"ok": True, "tabs": count}
