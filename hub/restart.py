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


def _pokracuj(session):
    """Zpráva pro Clauda po obnovení tabu, když mu restart zastavil úlohy na
    pozadí (příkazy, hlídání, agenty). Bez úloh ''. Úlohy jsou děti procesu
    Claude Code, takže restart hubu je vždycky ukončí — sám by o tom nevěděl
    a čekal by na hlášky, které už nepřijdou."""
    try:
        from . import cteni
        ulohy = cteni.pozadi(core.transcript_for(session), int(session.started * 1000))
    except Exception:
        return ""
    if not ulohy:
        return ""
    radky = []
    for u in ulohy[:8]:
        druh = {"Agent": "agent", "Monitor": "hlídání",
                "ScheduleWakeup": "naplánované probuzení (ScheduleWakeup)"}.get(u.get("name"), "příkaz")
        co = (u.get("detail") or u.get("title") or "").strip().replace("\n", " ")
        radky.append(f"- {druh}: {co[:300]}")
    return ("[hub] Hub se mezitím restartoval (aktualizace) a tyhle úlohy na pozadí se "
            "tím zastavily — hlášky o jejich doběhnutí už nepřijdou:\n" + "\n".join(radky) +
            "\n\nZkontroluj, co z toho je pořád potřeba, pusť to znovu a pokračuj tam, "
            "kde jsi skončil. Uživateli to krátce oznam.")


def snapshot(hub, reason="restart", quiet=False):
    """Zapíše otevřené taby. Vrací, kolik jich bylo.

    `reason`: restart (aktualizace), zavreni (zavřená appka na počítači),
    uspani (brána zastavuje prostor) — podle toho jak starý stav se ještě
    obnoví a co stránka řekne."""
    if core.TEST_MODE:
        return 0
    tabs = []
    for session in list(hub.sessions.values()):
        if session.exited:
            continue
        agent = session.agent or ""
        # Konverzaci má smysl hledat jen u Clauda — ostatní agenti `--resume`
        # neumí a holý terminál nemá co obnovovat.
        resume = ""
        prompt = ""
        if session.kind == "project" or session.kind.startswith("slash:"):
            if (agent or core.default_agent()) == "claude":
                resume = _resume_for(hub, session)
                prompt = _pokracuj(session)
        tabs.append({
            "kind": session.kind,
            "path": session.path or "",
            "title": session.title or "",
            "agent": agent,
            "model": session.model or "",
            "resume": resume,
            # Co Claude nechal běžet na pozadí — restart to zastaví, po
            # obnovení mu to hub připomene, ať to pustí znovu a pokračuje.
            "prompt": prompt,
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
        if not _TAKEN or core.TEST_MODE:
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
    if core.TEST_MODE:
        return [], ""
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


def _spawn(*extra):
    """Pustí novou instanci hubu, odpojenou od téhle."""
    launcher = LAUNCHER if os.path.isfile(LAUNCHER) else os.path.abspath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir,
                     "claude-hub.py"))
    if not os.path.isfile(launcher):
        raise RuntimeError("Nenašel jsem claude-hub.py, ze kterého se hub pouští.")
    argv = [sys.executable, launcher, *extra]
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
        # Značka „běžím na pozadí" pryč dřív než nová instance: jinak by se
        # připojila k téhle, která za chvíli skončí, a appka by zmizela.
        try:
            from . import pozadi, window
            stara = ""
            try:
                with open(pozadi.soubor(), encoding="utf-8") as fh:
                    stara = json.load(fh).get("url", "")
            except (OSError, ValueError):
                pass
            pozadi.smaz()
        except Exception:
            stara = ""
        try:
            # Nová instance okno sama neotvírá — stávající okno se na ni přepne
            # (rychlejší než zavřít okno a čekat, až Chromium otevře nové).
            _spawn("--bez-okna")
        except Exception as exc:
            core.log(f"restart: nová instance nenaběhla: {exc}", "error")
            return
        # Počká, až nová instance odpovídá, a otevřené okno na ni přepne.
        # Když to nevyjde (nenaběhla včas), nová si okno otevře sama a tohle
        # se zavře.
        nova = ""
        try:
            for _ in range(60):
                nova = pozadi.bezici_url()
                if nova:
                    break
                time.sleep(0.1)
        except Exception:
            nova = ""
        try:
            hub.broadcast({"t": "okno-jdi", "url": nova} if nova else {"t": "okno-zavri"})
        except Exception:
            pass
        time.sleep(0.4)                     # ať zpráva stihne odejít
        core.log("restart: nová instance spuštěna, končím")
        # Okno, které se nepřepnulo (nová instance nenaběhla), se zavře — nová
        # si otevře vlastní. Jinak by tu zůstala dvě.
        if not nova:
            try:
                n = window.zavri_okno(stara)
                if n:
                    core.log(f"restart: zavřeno staré okno ({n})")
            except Exception:
                pass
        else:
            core.log("restart: okno přepnuto na novou instanci")
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
