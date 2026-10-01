"""
Přenos chatu mezi počítačem a serverem — strana hubu (balení v prenos.py).

* Na počítači: `na_server` pošle chat bráně po kusech (token zařízení z přihlášení);
  brána ho předá hubu v prostoru, ten ho zapíše a ohlásí.
* V prostoru na serveru: `na_pocitac` nechá chat počítači jako „úkol na později"
  druhu chat (most na počítač, hub/pocitac.py); počítač si ho vyzvedne, až je
  online. S plným přístupem se rozbalí sám, jinak čeká na souhlas v appce.
"""
import base64
import json
import os
import socket
import urllib.error
import urllib.request

from . import account, core, pocitac, prenos

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _balicek(chat, titulek):
    if not prenos.SESSION_ID.fullmatch(str(chat or "")):
        raise ValueError("Tenhle chat se nedá přenést — ještě nemá žádnou zprávu.")
    odkud = socket.gethostname() if not core.on_gateway() else "server"
    return prenos.zabal(core.CLAUDE_DIR, chat, titulek, odkud)


def na_server(chat, titulek=""):
    """Počítač → server. Vrací {ok, message} nebo vyhodí ValueError s větou pro člověka."""
    token = core.CONFIG.get("gw_token") or ""
    if core.on_gateway():
        raise ValueError("Tohle se dělá z appky na počítači.")
    if not token or not account._base():
        raise ValueError("Nejsi přihlášený k serveru — přihlas se v Nastavení → Účet.")
    data, meta = _balicek(chat, titulek)
    kusy = prenos.kusy(data)
    for i, kus in enumerate(kusy, 1):
        res, err, _kind = account._call(
            "/gw/prenos/nahrat", token=token, timeout=300,
            payload={"id": meta["id"], "part": i, "parts": len(kusy), "title": meta["title"],
                     "from": meta["from"], "zip": base64.b64encode(kus).decode("ascii")})
        if res is None or not res.get("ok"):
            raise ValueError((res or {}).get("error") or err or "Server chat nepřijal.")
    core.log(f"přenos: chat „{meta['title']}\" poslán na server ({len(data) // 1024} kB)")
    return {"ok": True, "chat": meta["id"], "title": meta["title"],
            "message": f"Chat „{meta['title']}“ je na serveru."}


def na_pocitac(chat, titulek=""):
    """Server → počítač (z prostoru, přes bránu)."""
    url = os.environ.get("HUB_POCITAC_URL", "").rstrip("/")
    token = os.environ.get("HUB_POCITAC_TOKEN", "")
    if not core.on_gateway() or not url or not token:
        raise ValueError("Tohle se dělá z okna na serveru, kde je vidět most na počítač.")
    data, meta = _balicek(chat, titulek)
    kusy = prenos.kusy(data)
    files = [{"name": f"chat-{i:02d}.bin", "data": base64.b64encode(k).decode("ascii")}
             for i, k in enumerate(kusy, 1)]
    body = json.dumps({"op": "ukol-novy", "args": {
        "title": meta["title"], "kind": "chat",
        "text": f"Chat „{meta['title']}“ ze serveru — otevře se na počítači a pokračuje se v něm.",
        "files": files}}).encode("utf-8")
    req = urllib.request.Request(url + "/gw/pocitac/volani", data=body, method="POST",
                                 headers={"Content-Type": "application/json", "X-Hub-Pocitac": token})
    try:
        with _OPENER.open(req, timeout=300) as res:
            out = json.loads(res.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            out = json.loads(exc.read().decode("utf-8") or "{}")
        except ValueError:
            out = {}
        raise ValueError(out.get("error") or f"Brána odpověděla {exc.code}.") from None
    except (urllib.error.URLError, OSError) as exc:
        raise ValueError(f"Brána neodpovídá: {getattr(exc, 'reason', exc)}") from None
    if not out.get("ok"):
        raise ValueError(out.get("error") or "Počítači se chat nepodařilo nechat.")
    core.log(f"přenos: chat „{meta['title']}\" nechán počítači ({len(data) // 1024} kB)")
    # Chat se stěhuje, nekopíruje: na serveru zmizí ze seznamu. Přepis zůstane
    # vedle jako `<id>.jsonl.preneseno`, kdyby se na cestě něco ztratilo.
    stary, _sub = prenos.najdi(core.CLAUDE_DIR, meta["id"])
    if stary:
        try:
            os.replace(stary, stary + ".preneseno")
        except OSError:
            pass
    return {"ok": True, "chat": meta["id"], "title": meta["title"],
            "message": f"Chat „{meta['title']}“ je na cestě na počítač a ze serveru zmizel — otevře se tam, jakmile bude online."}


def prijmout(tid):
    """Počítač: člověk souhlasil, že chat ze serveru přijme."""
    return pocitac.prijmi_chat(tid)
