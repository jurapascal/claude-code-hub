"""
Čtení — konverzace s Claude Code jako text, ne jako terminál.

V tabu běží skutečný terminál a v něm Claude Code kreslí svoje okno. Přečíst
se to dá, ale nedá se v tom nic rozkliknout: jsou to řádky znaků, ne bloky.
Claude Code si přitom o každé konverzaci píše přepis (JSONL) a v něm je
všechno rozlišené — co řekl člověk, co odepsal Claude a co spustil za nástroj.
Tenhle modul z přepisu skládá bloky, ze kterých stránka postaví čtení:

    me       zadání člověka (`mid` = Claude si ho přečetl uprostřed práce)
    queued   zpráva poslaná během práce — stojí ve frontě, Claude ji ještě nevidí
    unqueue  zpráva z fronty odešla (nebo ji člověk vzal zpátky do pole)
    say      co Claude napsal (markdown)
    tool     volání nástroje, složené na jeden řádek (rozbalí se klepnutím)
    res      výsledek nástroje — patří k `tool` / `agent` se stejným `id`
    agent    Claude pustil agenta (Agent/Task) — kreslí se jako karta
    task     úloha na pozadí doběhla (agent i příkaz) — patří k `id`
    peer     zpráva od jiné session Claude Code

Zprávy poslané během práce Claude Code do přepisu nezapisuje jako zadání:
nejdřív je zařadí do fronty (`queue-operation` enqueue), a když si je vezme
uprostřed práce, zapíše je jako přílohu (`attachment` queued_command). Bez
toho by ve čtení druhá zpráva nebyla vůbec. Podle `key` (otisk textu) stránka
pozná, že zpráva z fronty je tatáž, která pak přišla jako zadání.

Čte se od bajtu, na kterém stránka minule skončila (`start`), takže během
práce přibývají jen nové bloky a přepis o stovkách MB se nikdy nečte celý.
Nedopsaný poslední řádek se nechává na příště — zapisuje ho Claude Code
zrovna teď.
"""
import datetime
import hashlib
import json
import os
import re
import threading
import time

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
# Co agent odevzdal, je zpráva k přečtení (markdown), ne výpis — vejde se víc.
MAX_RESULT = 60_000
# Nástroje, kterými Claude pouští agenta. „Task" je staré jméno téhož.
AGENT_TOOLS = ("Agent", "Task")


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
# Delší vložený text Claude Code obalí značkami <pasted_content id="…">.
# Do bubliny patří jen to, co člověk vložil.
_VLOZENO = re.compile(r"</?pasted_content\b[^>]*>")
_OD_SESSION = re.compile(r"\s*<cross-session-message\b([^>]*)>(.*?)(?:</cross-session-message>|$)", re.S)
_OZNAMENI = re.compile(r"<task-notification>(.*?)</task-notification>", re.S)
# Poznámka, kterou Claude Code předřadí výsledku agenta pro Clauda („control
# tags below are neutralized…"). Člověku nic neříká, do karty nepatří.
_POZNAMKA_HARNESS = re.compile(r"^\s*\[harness:[^\n]*\]\s*")


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
    if _OD_SESSION.match(obsah):
        return ""                     # zpráva od jiné session, ne od člověka
    text = _VLOZENO.sub("", chats._NOISE.sub("", obsah)).strip()
    if text.startswith(("Caveat:", "[Request interrupted")):
        return ""
    return text


def _klic(text, obrazky=()):
    """Otisk zprávy. Tatáž zpráva projde přepisem víckrát — ve frontě, při
    vyjmutí z fronty a nakonec jako zadání — a stránka podle otisku pozná,
    kterou bublinu z fronty tím sundat."""
    norm = " ".join(str(text or "").split()) + "|" + "|".join(obrazky)
    return hashlib.sha1(norm.encode("utf-8", "replace")).hexdigest()[:12]


def _zprava_cloveka(obsah):
    """Blok `me` ze zadání (text i seznam kusů s obrázky), nebo None."""
    text, cesty = _obrazky(_text_cloveka(obsah))
    vlozene = _vlozene(obsah)
    if not (text or cesty or vlozene):
        return None
    blok = {"kind": "me", "text": _zkrat(text, MAX_TEXT), "images": cesty,
            "key": _klic(text, cesty)}
    if vlozene:
        blok["inline"] = vlozene
    return blok


def _znacka(text, jmeno):
    m = re.search(r"<%s>(.*?)</%s>" % (jmeno, jmeno), text, re.S)
    return m.group(1).strip() if m else ""


def _cislo(text):
    try:
        return int(str(text).strip())
    except (TypeError, ValueError):
        return None


def _oznameni(text):
    """Bloky `task` z hlášek o doběhlé úloze na pozadí. Agent v nich odevzdává
    výsledek — a podle `tool-use-id` se najde karta, která na něj čeká."""
    out = []
    for m in _OZNAMENI.finditer(text if isinstance(text, str) else ""):
        telo = m.group(1)
        # Výsledek je markdown a klidně v něm může být cokoli — bere se
        # od první otevírací značky po poslední zavírací.
        zac, kon = telo.find("<result>"), telo.rfind("</result>")
        vysledek = telo[zac + 8:kon].strip() if 0 <= zac < kon else ""
        vysledek = _POZNAMKA_HARNESS.sub("", vysledek)
        uziti = _znacka(telo, "usage")
        blok = {"kind": "task", "id": _znacka(telo, "tool-use-id"),
                "task": _znacka(telo, "task-id"),
                "status": _znacka(telo, "status"),
                "summary": _znacka(telo, "summary"),
                "result": _zkrat(vysledek, MAX_RESULT),
                "tokens": _cislo(_znacka(uziti, "subagent_tokens") or
                                 _znacka(uziti, "total_tokens")),
                "tools": _cislo(_znacka(uziti, "tool_uses")),
                "ms": _cislo(_znacka(uziti, "duration_ms"))}
        if blok["id"] or blok["task"]:
            out.append(blok)
    return out


def _zprava_od_session(text, jmeno=""):
    """Blok `peer` — jiná session Claude Code poslala zprávu (SendMessage)."""
    m = _OD_SESSION.match(text) if isinstance(text, str) else None
    if not m:
        return None
    if not jmeno:
        od = re.search(r'from-name="([^"]*)"', m.group(1))
        jmeno = od.group(1) if od else ""
    return {"kind": "peer", "from": jmeno, "text": _zkrat(m.group(2).strip(), MAX_DETAIL)}


# Obrázek, který člověk přiložil v bublině: hub ho uloží do ~/.claude/hub-images
# a Claudovi napíše jeho cestu. Ve čtení má být vidět obrázek, ne cesta.
_OBRAZEK = re.compile(r"""['"]?(%s/[^\s'"]+?\.(?:png|jpe?g|gif|webp|bmp))['"]?"""
                      % re.escape(core.IMAGE_DIR), re.I)
# Obrázek vložený přímo do Claude Code (Ctrl+V v terminálu) je v přepisu jako
# base64. Do stránky se pošle jen rozumně velký — obří by čtení zdržel.
MAX_INLINE = 2_500_000


def _obrazky(text):
    """(text bez cest k obrázkům, [cesty]) — jen obrázky, které hub opravdu
    uložil a stránka si je umí vyžádat (/api/image čte jen z hub-images)."""
    cesty = []
    for m in _OBRAZEK.finditer(text):
        if os.path.isfile(m.group(1)) and m.group(1) not in cesty:
            cesty.append(m.group(1))
    if not cesty:
        return text, []
    zbytek = _OBRAZEK.sub(" ", text)
    return re.sub(r"[ \t]{2,}", " ", zbytek).strip(), cesty


def _vlozene(obsah):
    """Obrázky vložené přímo v Claude Code jako data: URI."""
    out = []
    if not isinstance(obsah, list):
        return out
    for cast in obsah:
        if not isinstance(cast, dict) or cast.get("type") != "image":
            continue
        zdroj = cast.get("source") or {}
        data = zdroj.get("data") or ""
        if zdroj.get("type") == "base64" and data and len(data) <= MAX_INLINE:
            out.append("data:%s;base64,%s" % (zdroj.get("media_type") or "image/png", data))
    return out


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
    if name in AGENT_TOOLS:
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


def _fronta(entry):
    """Bloky z `queue-operation`: co člověk poslal, zatímco Claude pracoval.

    enqueue  zpráva se zařadila do fronty — Claude ji zatím nevidí
    remove   zpráva z fronty odešla: Claude si ji vzal uprostřed práce
             (`absorbed_mid_turn`, vzápětí přijde jako příloha) a podobně
    popAll   člověk si frontu vzal zpátky do pole (Esc / šipka nahoru)
    dequeue  Claude dopracoval a zprávu si bere jako další zadání — přijde
             hned za tím jako obyčejné `user` se stejným otiskem

    Frontou chodí i hlášky o doběhlých úlohách na pozadí a zprávy od jiných
    session. Od člověka nejsou, bublinu ve frontě nedostanou — hláška
    o agentovi se ale rovnou propíše do jeho karty.
    """
    op = entry.get("operation")
    obsah = entry.get("content")
    if op == "enqueue":
        if isinstance(obsah, str) and "<task-notification>" in obsah:
            return _oznameni(obsah)
        zprava = _zprava_cloveka(obsah)
        if not zprava:
            return []
        zprava["kind"] = "queued"
        return [zprava]
    if op == "remove":
        zprava = _zprava_cloveka(obsah)
        if not zprava:
            return []
        return [{"kind": "unqueue", "key": zprava["key"],
                 "why": str(entry.get("reason") or "")}]
    if op == "popAll":
        return [{"kind": "unqueue", "all": True}]
    if op == "dequeue":
        # Hned za tím přijde zpráva z fronty jako obyčejné `user` — čtení
        # podle toho pozná, že nepřišla „odjinud“ (viz cteni.js, ztracene).
        return [{"kind": "dequeue"}]
    return []


def _priloha(entry):
    """Zpráva, kterou si Claude vzal z fronty uprostřed práce. V přepisu je
    jako příloha (`queued_command`) na místě, kde ji uviděl — a tam patří
    i ve čtení."""
    priloha = entry.get("attachment") or {}
    if priloha.get("type") != "queued_command":
        return []
    prompt = priloha.get("prompt")
    puvod = priloha.get("origin") if isinstance(priloha.get("origin"), dict) else {}
    if priloha.get("commandMode") == "task-notification" or (
            isinstance(prompt, str) and "<task-notification>" in prompt):
        return _oznameni(prompt)
    if puvod.get("kind") == "peer" or (isinstance(prompt, str) and _OD_SESSION.match(prompt)):
        blok = _zprava_od_session(prompt, str(puvod.get("name") or ""))
        return [blok] if blok else []
    if puvod.get("kind") not in (None, "human"):
        return []
    zprava = _zprava_cloveka(prompt)
    if not zprava:
        return []
    zprava["mid"] = True
    return [zprava]


def _agent_z_vysledku(entry, text):
    """Co o agentovi Claude Code zapsal k výsledku nástroje Agent."""
    info = entry.get("toolUseResult")
    if not isinstance(info, dict) or not info.get("agentId"):
        return None
    return {"agentId": str(info.get("agentId")),
            "async": bool(info.get("isAsync")),
            "status": str(info.get("status") or ""),
            "tokens": info.get("totalTokens") if isinstance(info.get("totalTokens"), int) else None,
            "tools": info.get("totalToolUseCount") if isinstance(info.get("totalToolUseCount"), int) else None,
            "ms": info.get("totalDurationMs") if isinstance(info.get("totalDurationMs"), int) else None,
            "model": str(info.get("resolvedModel") or "")}


def _bloky_zpravy(entry):
    """Bloky z jednoho řádku přepisu. Meta zprávy a podkonverzace se přeskakují
    — do čtení patří jen to, co je mezi člověkem a Claudem."""
    if entry.get("isSidechain"):
        return []
    druh = entry.get("type")
    if druh == "queue-operation":
        return _fronta(entry)
    if druh == "attachment":
        return _priloha(entry)
    if entry.get("isMeta"):
        return []
    zprava = entry.get("message") or {}
    obsah = zprava.get("content")
    out = []

    if druh == "user":
        if isinstance(obsah, str):
            if "<task-notification>" in obsah:
                out.extend(_oznameni(obsah))
            od = _zprava_od_session(obsah)
            if od:
                return out + [od]
            blok = _zprava_cloveka(obsah)
            if blok:
                out.append(blok)
            return out
        if isinstance(obsah, list):
            # Zpráva s obrázkem vloženým přímo v Claude Code: text i obrázky
            # patří do jedné bubliny, ne každý kus zvlášť.
            vysledky = [c for c in obsah if isinstance(c, dict) and c.get("type") == "tool_result"]
            if not vysledky:
                for cast in obsah:
                    if isinstance(cast, dict) and cast.get("type") == "text":
                        out.extend(_oznameni(cast.get("text")))
                blok = _zprava_cloveka(obsah)
                if blok:
                    out.append(blok)
                return out
            for cast in obsah:
                if not isinstance(cast, dict):
                    continue
                if cast.get("type") == "tool_result":
                    text = _text_vysledku(cast.get("content"))
                    blok = {"kind": "res",
                            "id": str(cast.get("tool_use_id") or ""),
                            "ok": not cast.get("is_error"),
                            "detail": _zkrat(text),
                            "radku": _radku(text)}
                    # K výsledku agenta Claude Code zapisuje, jestli běží na
                    # pozadí, a když doběhl, kolik toho udělal. Jeden výsledek
                    # na řádek, jinak by se nevědělo, ke kterému to patří.
                    agent = _agent_z_vysledku(entry, text) if len(vysledky) == 1 else None
                    if agent:
                        blok["agent"] = agent
                        blok["detail"] = "" if agent["async"] else _zkrat(
                            _POZNAMKA_HARNESS.sub("", text), MAX_RESULT)
                    out.append(blok)
                elif cast.get("type") == "text":
                    blok = _zprava_cloveka(cast.get("text"))
                    if blok:
                        out.append(blok)
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
                elif cast.get("type") == "tool_use" and cast.get("name") in AGENT_TOOLS:
                    v = cast.get("input") if isinstance(cast.get("input"), dict) else {}
                    out.append({"kind": "agent", "id": str(cast.get("id") or ""),
                                "type": str(v.get("subagent_type") or "general-purpose"),
                                "title": str(v.get("description") or ""),
                                "prompt": _zkrat(v.get("prompt"), MAX_RESULT),
                                "model": str(v.get("model") or ""),
                                "bg": bool(v.get("run_in_background")),
                                "ts": str(entry.get("timestamp") or "")})
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


def navaz(stary, odkud, novy):
    """Kde v novém přepisu pokračovat, když Claude Code konverzaci přestěhoval
    (core._pokracovani): najde poslední zprávu, kterou čtení ze starého přepisu
    už mělo (před bajtem `odkud`), a vrátí pozici hned za ní v novém. Když ji
    nenajde, vrátí 0 — čtení pak začne od konce nového jako při otevření."""
    try:
        with open(stary, "rb") as fh:
            fh.seek(max(0, int(odkud) - 2 * 1024 * 1024))
            kus = fh.read(max(0, int(odkud) - fh.tell()))
    except (OSError, ValueError):
        return 0
    uuid = None
    for radek in reversed(kus.splitlines()):
        m = re.search(rb'"uuid":"([0-9a-f-]{36})"', radek)
        if m:
            uuid = m.group(0)
            break
    if not uuid:
        return 0
    try:
        with open(novy, "rb") as fh:
            data = fh.read()
    except OSError:
        return 0
    i = data.find(uuid)
    if i < 0:
        return 0
    konec = data.find(b"\n", i)
    return konec + 1 if konec >= 0 else len(data)


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


# ── agenti ───────────────────────────────────────────────────────────────────
# Agent si píše vlastní přepis vedle konverzace:
#     <id konverzace>/subagents/agent-<agentId>.jsonl  (+ .meta.json)
# V meta je `toolUseId` — id volání nástroje Agent, podle kterého stránka najde
# kartu. Z přepisu se čte, kolik toho agent udělal a co dělá teď. Soubory
# rostou po celou dobu práce (klidně na MB), proto se čtou od místa, kde se
# minule skončilo, a stav se drží tady.
MAX_AG_READ = 4 * 1024 * 1024
AG_KROKU = 5                  # kolik posledních kroků agenta se posílá
_AG_STAV = {}
_AG_ZAMEK = threading.Lock()


def _ms(stamp):
    """ISO čas z přepisu → epoch v ms (0 = nevíme)."""
    try:
        return int(datetime.datetime.fromisoformat(
            str(stamp).replace("Z", "+00:00")).timestamp() * 1000)
    except (TypeError, ValueError):
        return 0


def _novy_stav():
    return {"pos": 0, "tools": 0, "last": [], "text": "", "start": 0,
            "update": 0, "tokens": 0, "model": "", "konec": False}


def _zapocti(stav, entry):
    cas = _ms(entry.get("timestamp"))
    if cas:
        stav["start"] = stav["start"] or cas
        stav["update"] = cas
    if entry.get("type") != "assistant":
        if entry.get("type") == "user":
            stav["konec"] = False          # agent pokračuje (výsledek nástroje)
        return
    zprava = entry.get("message") or {}
    stav["model"] = str(zprava.get("model") or stav["model"])
    uziti = zprava.get("usage") if isinstance(zprava.get("usage"), dict) else {}
    # Stejné číslo jako Claude Code u běžícího agenta: kolik tokenů má
    # v kontextu po poslední odpovědi.
    tokeny = sum(v for k, v in uziti.items() if isinstance(v, int) and k in (
        "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens",
        "output_tokens"))
    if tokeny:
        stav["tokens"] = tokeny
    obsah = zprava.get("content")
    nastroj = False
    for cast in obsah if isinstance(obsah, list) else []:
        if not isinstance(cast, dict):
            continue
        if cast.get("type") == "tool_use":
            nastroj = True
            stav["tools"] += 1
            nadpis, meta, _ = _popis(cast.get("name"), cast.get("input"))
            stav["last"].append({"name": str(cast.get("name") or ""),
                                 "title": _zkrat(nadpis, 160), "meta": meta})
            del stav["last"][:-AG_KROKU]
        elif cast.get("type") == "text" and str(cast.get("text") or "").strip():
            stav["text"] = _zkrat(" ".join(str(cast["text"]).split()), 240)
    stav["konec"] = not nastroj and zprava.get("stop_reason") == "end_turn"


def _prubeh(cesta):
    """Stav jednoho agenta z jeho přepisu — dočte jen to, co přibylo."""
    try:
        velikost = os.path.getsize(cesta)
    except OSError:
        return None
    with _AG_ZAMEK:
        stav = _AG_STAV.get(cesta)
        if stav is None or velikost < stav["pos"]:
            stav = _novy_stav()
            if len(_AG_STAV) > 300:
                _AG_STAV.clear()
            _AG_STAV[cesta] = stav
        if velikost > stav["pos"]:
            try:
                with open(cesta, "rb") as fh:
                    fh.seek(stav["pos"])
                    syrove = fh.read(MAX_AG_READ)
            except OSError:
                syrove = b""
            konec = syrove.rfind(b"\n")
            if konec >= 0:
                for radek in syrove[:konec + 1].split(b"\n"):
                    radek = radek.strip()
                    if not radek.startswith(b"{"):
                        continue
                    try:
                        entry = json.loads(radek)
                    except ValueError:
                        continue
                    if isinstance(entry, dict):
                        _zapocti(stav, entry)
                stav["pos"] += konec + 1
        return {k: (list(v) if isinstance(v, list) else v)
                for k, v in stav.items() if k != "pos"}


def agenti(path):
    """Průběh všech agentů, které konverzace pustila. `now` je čas serveru —
    podle něj si stránka srovná hodiny, ať běžící čas nelže o rozdíl mezi
    počítačem a serverem."""
    ted = int(time.time() * 1000)
    if not path or not path.endswith(".jsonl"):
        return {"agents": [], "now": ted}
    slozka = os.path.join(path[:-len(".jsonl")], "subagents")
    try:
        jmena = os.listdir(slozka)
    except OSError:
        return {"agents": [], "now": ted}
    out = []
    for jmeno in sorted(jmena):
        if not (jmeno.startswith("agent-") and jmeno.endswith(".jsonl")):
            continue
        agent_id = jmeno[len("agent-"):-len(".jsonl")]
        try:
            with open(os.path.join(slozka, "agent-%s.meta.json" % agent_id),
                      encoding="utf-8") as fh:
                meta = json.load(fh)
        except (OSError, ValueError):
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        stav = _prubeh(os.path.join(slozka, jmeno))
        if stav is None:
            continue
        stav.update({"agentId": agent_id, "id": str(meta.get("toolUseId") or ""),
                     "type": str(meta.get("agentType") or ""),
                     "title": str(meta.get("description") or "")})
        out.append(stav)
    return {"agents": out, "now": ted}
