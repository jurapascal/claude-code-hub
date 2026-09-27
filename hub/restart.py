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

# Po aktualizaci: starší soubor nemá cenu obnovovat — když se hub nepustil
# hodinu, člověk už dávno dělá něco jiného a taby z minula by ho jen zmátly.
MAX_AGE = 3600
# Po zavření appky (počítač) nebo uspání prostoru (server) se ale pokračuje
# tam, kde člověk skončil, i druhý den nebo po víkendu.
MAX_AGE_CLOSED = 14 * 24 * 3600
REASONS = ("restart", "zavreni", "uspani")
# Uložené taby z minula, které si stránka ještě nevzala (take) — průběžné
# ukládání je do té doby nesmí přepsat prázdným stavem čerstvého startu.
_TAKEN = not os.path.exists(STATE_PATH)


def _resume_for(hub, session):
    """Id konverzace, kterou tab drží (nebo ''), ať se dá pokračovat.

    Stejná cesta jako u čtení (core.transcript_for): id, které si hub pro tab
    zvolil sám (--session-id), jinak značka HUB_TAB od Stop hooku a nakonec
    nejnovější přepis ze složky projektu. Bez toho prvního se dva taby nad
    jedním projektem po restartu vrátily do téže (nejnovější) konverzace.
    """
    try:
        transcript = core.transcript_for(session)
        if transcript:
            return os.path.basename(transcript)[:-6]
    except Exception:
        pass
    return session.resume or ""


def snapshot(hub, reason="restart", quiet=False):
    """Zapíše otevřené taby. Vrací, kolik jich bylo.

    `reason`: restart (aktualizace), zavreni (zavřená appka na počítači),
    uspani (brána zastavuje prostor) — podle toho jak starý stav se ještě
    obnoví a co stránka řekne."""
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
            # Firemní tab (trezor firmy) má zůstat firemní i po restartu.
            "vault": getattr(session, "vault", "") or "",
        })
    data = {"at": int(time.time()), "version": core.version(), "tabs": tabs,
            "duvod": reason if reason in REASONS else "restart"}
    try:
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, STATE_PATH)
    except OSError as exc:
        core.log(f"restart: stav tabů se nepodařilo uložit: {exc}", "warn")
        return 0
    if not quiet:
        core.log(f"restart: uloženo {len(tabs)} tabů")
    return len(tabs)


def keep_saving(hub, reason, every=60):
    """Ukládá taby průběžně (vlákno). Zavření appky i uspání prostoru je uloží
    samo; tohle je pro pád, zabití procesu (nedostatek paměti) a vypnutý
    počítač — ať se i pak dá pokračovat. Nejvýš minuta zpátky."""
    while True:
        time.sleep(every)
        if not _TAKEN:
            continue
        try:
            snapshot(hub, reason, quiet=True)
        except Exception:
            pass


def take():
    """Přečte uložené taby a soubor smaže — obnovuje se jednou, ne pokaždé.
    Vrací (taby, důvod)."""
    global _TAKEN
    _TAKEN = True
    try:
        with open(STATE_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return [], ""
    try:
        os.remove(STATE_PATH)
    except OSError:
        pass
    if not isinstance(data, dict):
        return [], ""
    reason = data.get("duvod") if data.get("duvod") in REASONS else "restart"
    limit = MAX_AGE if reason == "restart" else MAX_AGE_CLOSED
    if time.time() - int(data.get("at") or 0) > limit:
        core.log("restart: uložené taby jsou staré, neobnovuju")
        return [], ""
    tabs = data.get("tabs")
    return (tabs if isinstance(tabs, list) else []), reason


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
            hub.shutdown(keep=False)
        except Exception:
            pass
        # os._exit, protože okno drží hlavní smyčku a obyčejný sys.exit by
        # z tohohle vlákna proces neukončil.
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)

    threading.Thread(target=go, daemon=True).start()
    return {"ok": True, "tabs": count}
