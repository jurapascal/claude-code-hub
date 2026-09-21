"""
Čtení — konverzace s Claude Code jako text, ne jako terminál.

V tabu běží skutečný terminál a v něm Claude Code kreslí svoje okno. Přečíst
se to dá, ale nedá se v tom nic rozkliknout: jsou to řádky znaků, ne bloky.
Claude Code si přitom o každé konverzaci píše přepis (JSONL) a v něm je
všechno rozlišené — co řekl člověk, co odepsal Claude a co spustil za nástroj.
Tenhle modul z přepisu skládá bloky, ze kterých stránka postaví čtení:

    me    zadání člověka
    say   co Claude napsal (markdown)
    tool  volání nástroje, složené na jeden řádek (rozbalí se klepnutím)
    res   výsledek nástroje — patří k `tool` se stejným `id`

Čte se od bajtu, na kterém stránka minule skončila (`start`), takže během
práce přibývají jen nové bloky a přepis o stovkách MB se nikdy nečte celý.
Nedopsaný poslední řádek se nechává na příště — zapisuje ho Claude Code
zrovna teď.
"""
import json
import os
import re

from . import chats
from . import core

# Kolik se toho pošle najednou. Přepis narůstá po kilobajtech, ale první
# načtení dlouhé konverzace by jinak poslalo desítky MB do prohlížeče.
MAX_BYTES = 8 * 1024 * 1024
# Od kolika se při prvním otevření čte jen konec. Výsledky nástrojů nesou
# i obrázky v base64, takže přepis naroste do stovek MB, aniž by v něm bylo
# víc konverzace — a člověk stejně kouká na to, co bylo naposledy.
TAIL_BYTES = 8 * 1024 * 1024
# Strop na jedno načtení, ať se do stránky nevysype půlden práce najednou.
MAX_BLOCKS = 400
# Strop na jeden rozbalený blok. Výpis `npm install` má klidně 200 kB a v okně
# ho stejně nikdo nečte — co se nevejde, končí třemi tečkami.
MAX_DETAIL = 4000
MAX_TEXT = 200_000


def path_for(chat_id):
    """Přepis konverzace podle jejího id, nebo ''."""
    if not je_id(chat_id):
        return ""
    root = os.path.join(core.CLAUDE_DIR, "projects")
    try:
        folders = os.listdir(root)
    except OSError:
        return ""
    for folder in folders:
        path = os.path.join(root, folder, chat_id + ".jsonl")
        if os.path.isfile(path):
            return path
    return ""


def je_id(chat_id):
    """Id konverzace z prohlížeče se dosazuje do cesty — pustí se jen tvar,
    který Claude Code sám používá (UUID), nic s lomítkem ani tečkami."""
    return bool(core.SESSION_ID.fullmatch(str(chat_id or "")))


_PRIKAZ = re.compile(r"<command-name>(.*?)</command-name>", re.S)
_ARGY = re.compile(r"<command-args>(.*?)</command-args>", re.S)


def _text_cloveka(obsah):
    """Co člověk napsal, i s odřádkováním. chats.prompt_text je na jednořádkový
    náhled v seznamu (slepí bílé znaky a usekne na 200 znaků) — do čtení patří
    celá zpráva tak, jak ji psal."""
    if isinstance(obsah, list):
        obsah = "\n".join(cast.get("text", "") for cast in obsah
                          if isinstance(cast, dict) and cast.get("type") == "text")
    if not isinstance(obsah, str):
        return ""
    prikaz = _PRIKAZ.search(obsah)
    if prikaz:
        argy = _ARGY.search(obsah)
        return (prikaz.group(1) + " " + (argy.group(1) if argy else "")).strip()
    text = chats._NOISE.sub("", obsah).strip()
    if text.startswith(("Caveat:", "[Request interrupted")):
        return ""
    return text


def _zkrat(text, limit=MAX_DETAIL):
    text = str(text or "")
    return text if len(text) <= limit else text[:limit].rstrip() + "\n…"


def _jmeno(cesta):
    return os.path.basename(str(cesta or "").rstrip("/")) or str(cesta or "")


def _radku(text):
    return str(text or "").count("\n") + 1 if text else 0


def _popis(name, vstup):
    """(nadpis, doplněk, rozbalený text) pro jedno volání nástroje.

    Nadpis je věta v minulém čase — „upravil hub.css" — protože blok se čte
    zpětně jako záznam toho, co se stalo, ne jako tlačítko.
    """
    v = vstup if isinstance(vstup, dict) else {}
    if name == "Read":
        return "přečetl " + _jmeno(v.get("file_path")), "", str(v.get("file_path") or "")
    if name in ("Edit", "NotebookEdit"):
        stare, nove = str(v.get("old_string") or ""), str(v.get("new_string") or "")
        meta = f"+{_radku(nove)} −{_radku(stare)}" if (stare or nove) else ""
        detail = ""
        if stare:
            detail += "\n".join("− " + r for r in stare.split("\n")) + "\n"
        if nove:
            detail += "\n".join("+ " + r for r in nove.split("\n"))
        return "upravil " + _jmeno(v.get("file_path")), meta, detail
    if name == "Write":
        obsah = str(v.get("content") or "")
        return ("zapsal " + _jmeno(v.get("file_path")),
                f"{_radku(obsah)} ř.", obsah)
    if name == "Bash":
        prikaz = str(v.get("command") or "")
        return (str(v.get("description") or prikaz.split("\n")[0]) or "spustil příkaz",
                "", prikaz)
    if name in ("Grep", "Glob"):
        return ("hledal " + str(v.get("pattern") or ""), "",
                json.dumps(v, ensure_ascii=False, indent=2))
    if name == "Task":
        return ("pustil agenta " + str(v.get("subagent_type") or ""),
                str(v.get("description") or ""), str(v.get("prompt") or ""))
    if name in ("WebFetch", "WebSearch"):
        return ("načetl " + str(v.get("url") or v.get("query") or ""), "",
                json.dumps(v, ensure_ascii=False, indent=2))
    if name == "TodoWrite":
        ukoly = v.get("todos") if isinstance(v.get("todos"), list) else []
        return "zapsal úkoly", f"{len(ukoly)}", json.dumps(ukoly, ensure_ascii=False, indent=2)
    if name == "AskUserQuestion":
        return "zeptal se", "", json.dumps(v.get("questions"), ensure_ascii=False, indent=2)
    if str(name).startswith("mcp__"):
        cast = str(name).split("__")
        server = cast[1] if len(cast) > 2 else ""
        nastroj = cast[-1]
        return f"{server}: {nastroj}", "", json.dumps(v, ensure_ascii=False, indent=2)
    return str(name or "nástroj"), "", json.dumps(v, ensure_ascii=False, indent=2)


def _text_vysledku(obsah):
    """Výsledek nástroje jako text. Obrázek se nepřenáší — v čtení by z něj
    byl kilometr base64 a beztak se nevykreslí."""
    if isinstance(obsah, str):
        return obsah
    if isinstance(obsah, list):
        kusy = []
        for cast in obsah:
            if not isinstance(cast, dict):
                continue
            if cast.get("type") == "text":
                kusy.append(str(cast.get("text") or ""))
            elif cast.get("type") == "image":
                kusy.append("(obrázek)")
        return "\n".join(kusy)
    return ""


def _bloky_zpravy(entry):
    """Bloky z jednoho řádku přepisu. Meta zprávy a podkonverzace se přeskakují
    — do čtení patří jen to, co je mezi člověkem a Claudem."""
    if entry.get("isMeta") or entry.get("isSidechain"):
        return []
    druh = entry.get("type")
    zprava = entry.get("message") or {}
    obsah = zprava.get("content")
    out = []

    if druh == "user":
        if isinstance(obsah, str):
            text = _text_cloveka(obsah)
            if text:
                out.append({"kind": "me", "text": _zkrat(text, MAX_TEXT)})
            return out
        if isinstance(obsah, list):
            for cast in obsah:
                if not isinstance(cast, dict):
                    continue
                if cast.get("type") == "tool_result":
                    text = _text_vysledku(cast.get("content"))
                    out.append({"kind": "res",
                                "id": str(cast.get("tool_use_id") or ""),
                                "ok": not cast.get("is_error"),
                                "detail": _zkrat(text),
                                "radku": _radku(text)})
                elif cast.get("type") == "text":
                    text = _text_cloveka(cast.get("text"))
                    if text:
                        out.append({"kind": "me", "text": _zkrat(text, MAX_TEXT)})
        return out

    if druh == "assistant":
        if isinstance(obsah, str):
            if obsah.strip():
                out.append({"kind": "say", "text": _zkrat(obsah, MAX_TEXT)})
            return out
        if isinstance(obsah, list):
            for cast in obsah:
                if not isinstance(cast, dict):
                    continue
                if cast.get("type") == "text" and str(cast.get("text") or "").strip():
                    out.append({"kind": "say", "text": _zkrat(cast["text"], MAX_TEXT)})
                elif cast.get("type") == "tool_use":
                    nadpis, meta, detail = _popis(cast.get("name"), cast.get("input"))
                    out.append({"kind": "tool", "id": str(cast.get("id") or ""),
                                "name": str(cast.get("name") or ""),
                                "title": nadpis, "meta": meta,
                                "detail": _zkrat(detail)})
                # thinking se nečte: ve čtení má být to, co Claude napsal,
                # ne to, co si přitom myslel.
        return out
    return []


def read(path, start=0, tail=False):
    """Nové bloky od bajtu `start`. Vrací i offset, na kterém se skončilo.

    `tail` = první otevření: začne se u konce přepisu, ne u prvního řádku.
    """
    if not path or not os.path.isfile(path):
        return {"blocks": [], "next": 0, "ready": False}
    try:
        velikost = os.path.getsize(path)
        start = max(0, min(int(start or 0), velikost))
        preskoceno = False
        if tail and start == 0 and velikost > TAIL_BYTES:
            start = velikost - TAIL_BYTES
            preskoceno = True
        with open(path, "rb") as fh:
            fh.seek(start)
            syrove = fh.read(MAX_BYTES)
    except (OSError, ValueError):
        return {"blocks": [], "next": start, "ready": False}

    if preskoceno:
        # Doprostřed řádku se skočit nedá — první nedočtený se zahodí.
        rez = syrove.find(b"\n")
        if rez < 0:
            return {"blocks": [], "next": velikost, "ready": True}
        start += rez + 1
        syrove = syrove[rez + 1:]

    # Poslední řádek může být rozepsaný — ten se nechá na příště.
    konec = syrove.rfind(b"\n")
    if konec < 0:
        return {"blocks": [], "next": start, "ready": True}
    hotovo, syrove = syrove[:konec + 1], None

    bloky = []
    for radek in hotovo.split(b"\n"):
        radek = radek.strip()
        if not radek.startswith(b"{"):
            continue
        try:
            entry = json.loads(radek)
        except ValueError:
            continue
        if isinstance(entry, dict):
            bloky.extend(_bloky_zpravy(entry))
    return {"blocks": bloky[-MAX_BLOCKS:], "next": start + konec + 1, "ready": True}
