"""
Účet na bráně — hub na počítači jako klient serverového hubu.

Hub běží na dvou místech a je to schválně: na počítači sahá na tvoje projekty,
na serveru běží pořád a je dostupný odkudkoli. Tenhle modul drží to třetí —
**že jsi to na obou stranách ty**.

Dvě věci, které stojí za vysvětlení:

* **Heslo se nikam neukládá.** Pošle se jednou při přihlášení a zpátky přijde
  token zařízení. V konfiguraci leží jen ten token; heslo hub nezná ani
  vteřinu po přihlášení.

* **Přepnutí na server nejde přes token v adrese.** Prohlížeč potřebuje cookie
  na doméně brány, ale token v odkazu by skončil v historii. Brána proto token
  vymění za jednorázový kód s minutovou platností (`/gw/handoff`), a ten se
  při prvním použití zahodí.

Když je server nedostupný, hub se netváří jako odhlášený — vrátí poslední
známý účet s `offline: True`. Odhlášení kvůli výpadku sítě by znamenalo psát
heslo znovu pokaždé, když je vlak v tunelu.
"""
import json
import urllib.error
import urllib.parse
import urllib.request

from . import core

TIMEOUT = 12
UA = "claude-code-hub"


def _base():
    """Adresa brány z konfigurace, bez lomítka na konci."""
    raw = (core.CONFIG.get("gw_server") or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    return raw.rstrip("/")


def _call(path, token="", payload=None, method=None):
    """Zavolá bránu. Vrací (data, chyba) — chyba je hotová věta pro člověka."""
    base = _base()
    if not base:
        return None, "Není nastavená adresa serveru."
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(base + path, data=data, method=method)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", UA)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
            body = res.read().decode("utf-8", "replace")
        return (json.loads(body) if body else {}), ""
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8", "replace"))
            note = detail.get("error") or ""
        except Exception:
            note = ""
        if exc.code == 401:
            return None, note or "Přihlášení vypršelo."
        return None, note or f"Server odpověděl {exc.code}."
    except urllib.error.URLError as exc:
        return None, f"Server není dostupný ({exc.reason})."
    except Exception as exc:
        return None, f"Nepovedlo se: {exc}"


def status():
    """Stav přihlášení. Nikdy nevyhodí výjimku — kreslí se z toho nastavení."""
    server = _base()
    token = core.CONFIG.get("gw_token") or ""
    cached = core.CONFIG.get("gw_user") or None
    if not server or not token:
        return {"server": server, "logged_in": False, "user": None}

    data, err = _call("/gw/me", token=token)
    if data and data.get("user"):
        user = data["user"]
        # Jméno nebo vault se mohly na serveru změnit — ať to nastavení ukazuje
        # to, co platí teď, ne co platilo při přihlášení.
        if user != cached:
            core.save_config({"gw_user": user})
        return {"server": server, "logged_in": True, "user": user}

    if err in ("Přihlášení vypršelo.", "Neplatný token."):
        # Token brána nezná (odhlášení z jiného zařízení, změna hesla) —
        # držet ho dál by jen mátlo.
        core.save_config({"gw_token": "", "gw_user": None})
        return {"server": server, "logged_in": False, "user": None,
                "note": "Přihlášení na serveru skončilo, přihlaš se znovu."}

    return {"server": server, "logged_in": bool(cached), "user": cached,
            "offline": True, "note": err}


def login(server, email, password):
    core.save_config({"gw_server": (server or "").strip()})
    if not _base():
        return {"error": "Zadej adresu serveru."}
    data, err = _call("/login", payload={"email": (email or "").strip(),
                                         "password": password or ""})
    if not data or not data.get("token"):
        return {"error": err or "Přihlášení se nepovedlo."}
    core.save_config({"gw_token": data["token"], "gw_user": data.get("user")})
    return status()


def logout():
    """Odhlásí i na serveru, ať token nezůstane platný po zbytek měsíce."""
    token = core.CONFIG.get("gw_token") or ""
    if token:
        base = _base()
        if base:
            try:
                req = urllib.request.Request(base + "/logout", method="GET")
                # `/logout` na bráně čte cookie, ne hlavičku — token jí tedy
                # podáme tak, jak ho čeká od prohlížeče.
                req.add_header("Cookie", "gw_session=" +
                               urllib.parse.quote(token))
                req.add_header("User-Agent", UA)
                urllib.request.urlopen(req, timeout=TIMEOUT).close()
            except Exception:
                pass  # token si stejně zahodíme; víc udělat nejde
    core.save_config({"gw_token": "", "gw_user": None})
    return status()


def handoff():
    """Adresa, po jejímž otevření je prohlížeč přihlášený na serveru."""
    token = core.CONFIG.get("gw_token") or ""
    if not token:
        return {"error": "Nejsi přihlášený."}
    data, err = _call("/gw/handoff", token=token, payload={})
    if not data or not data.get("url"):
        return {"error": err or "Nepovedlo se připravit přihlášení."}
    return {"url": data["url"]}
