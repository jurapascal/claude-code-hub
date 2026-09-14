"""
Účet na bráně — appka jako okno do prostoru na serveru.

Hub běží na dvou místech a je to schválně: na počítači sahá na tvoje projekty,
na serveru běží pořád a je dostupný odkudkoli. Tenhle modul drží to třetí —
**že jsi to na obou stranách ty**.

Tok, kterým appka projde, když si člověk vybere server:

1. `probe()` — ověří adresu dřív, než se napíše heslo. Nestačí, že „něco
   odpovídá": na překlepnuté adrese by se heslo psalo do cizího webu.
2. `login()` — heslo se pošle jednou a zpátky přijde token zařízení.
3. `handoff()` — token se vymění za jednorázový kód do adresy okna, a okno je
   pak přihlášené i bez hesla. Totéž se děje při každém dalším spuštění.

Dvě věci, které stojí za vysvětlení:

* **Heslo se nikam neukládá.** V konfiguraci leží jen token zařízení; heslo
  hub nezná ani vteřinu po přihlášení.

* **Přepnutí na server nejde přes token v adrese.** Prohlížeč potřebuje cookie
  na doméně brány, ale token v odkazu by skončil v historii. Brána proto token
  vymění za jednorázový kód s minutovou platností (`/gw/handoff`), a ten se
  při prvním použití zahodí.

Když je server nedostupný, hub se netváří jako odhlášený — vrátí poslední
známý účet s `offline: True`. Odhlášení kvůli výpadku sítě by znamenalo psát
heslo znovu pokaždé, když je vlak v tunelu.
"""
import json
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request

from . import core

TIMEOUT = 12
UA = "claude-code-hub"

# Druh chyby — podle něj se appka rozhoduje, co ukázat. Věta pro člověka jde
# vedle; porovnávat věty mezi sebou (jak se to dělalo dřív) se rozbije první
# úpravou textu.
OFFLINE = "offline"      # server se nedá zastihnout
AUTH = "auth"            # server žije, ale token nezná
HTTP = "http"            # server odpověděl chybou


def normalize(raw):
    """Z čehokoli, co člověk napíše, udělá kořen brány.

    `test.alba-rosa.cz` → `https://test.alba-rosa.cz`; vložená adresa i s
    cestou (`…/login`) se ořízne, jinak by se API volalo o patro níž.
    """
    raw = (raw or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    parts = urllib.parse.urlsplit(raw)
    if not parts.netloc:
        return ""
    return f"{parts.scheme}://{parts.netloc}"


def _base():
    """Adresa brány z konfigurace, bez lomítka na konci."""
    return normalize(core.CONFIG.get("gw_server") or "")


def _host(base):
    return urllib.parse.urlsplit(base).netloc or base


def _offline_reason(exc, base):
    """Proč se k serveru nedá dostat — tak, aby se podle toho dalo něco udělat."""
    host = _host(base)
    reason = getattr(exc, "reason", exc)
    if isinstance(reason, socket.gaierror):
        return f"Adresu {host} se nepodařilo najít. Je správně napsaná?"
    if isinstance(reason, ssl.SSLCertVerificationError):
        return f"{host} nemá platný certifikát, spojení není bezpečné."
    if isinstance(reason, ConnectionRefusedError):
        return f"{host} odmítl spojení. Server asi neběží."
    if isinstance(reason, (socket.timeout, TimeoutError)):
        return f"{host} neodpověděl včas."
    return f"Server {host} není dostupný ({reason})."


def _call(path, token="", payload=None, method=None, base=None, timeout=TIMEOUT):
    """Zavolá bránu. Vrací (data, chyba, druh) — chyba je hotová věta pro člověka."""
    base = base or _base()
    if not base:
        return None, "Není nastavená adresa serveru.", HTTP
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
        with urllib.request.urlopen(req, timeout=timeout) as res:
            body = res.read().decode("utf-8", "replace")
        return (json.loads(body) if body else {}), "", ""
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8", "replace"))
            note = detail.get("error") or ""
        except Exception:
            note = ""
        if exc.code == 401:
            return None, note or "Přihlášení vypršelo.", AUTH
        if exc.code in (502, 503, 504):
            # nginx běží, brána za ním ne — pro člověka je to totéž co výpadek.
            return None, f"Server {_host(base)} teď neodpovídá ({exc.code}).", OFFLINE
        return None, note or f"Server odpověděl {exc.code}.", HTTP
    except (urllib.error.URLError, OSError) as exc:
        return None, _offline_reason(exc, base), OFFLINE
    except ValueError:
        return None, "Server neodpověděl tak, jak by brána Code Hubu měla.", HTTP
    except Exception as exc:
        return None, f"Nepovedlo se: {exc}", HTTP


def probe(server):
    """Je na adrese brána Code Hubu? Volá se před přihlášením.

    Vrací {"ok", "server", "host"} nebo {"error", "server"}.
    """
    base = normalize(server)
    if not base:
        return {"error": "Zadej adresu serveru.", "server": ""}
    out = {"server": base, "host": _host(base)}
    data, err, kind = _call("/gw/info", base=base, timeout=8)
    if data and data.get("app") == "claude-code-hub":
        return {**out, "ok": True, "version": data.get("version") or ""}
    if kind == OFFLINE:
        return {**out, "error": err}
    # Brána z doby před /gw/info na ni odpoví 401, protože ji nezná. Pozná se
    # tedy podle toho, jak odmítne cizí token — to umí od první verze.
    data, err, kind = _call("/gw/me", token="-", base=base, timeout=8)
    if kind == AUTH and err == "Neplatný token.":
        return {**out, "ok": True, "version": ""}
    return {**out, "error": f"Na {_host(base)} je něco jiného než server "
                             "Code Hubu. Zkontroluj adresu."}


def status(timeout=TIMEOUT):
    """Stav přihlášení. Nikdy nevyhodí výjimku — kreslí se z toho nastavení."""
    server = _base()
    token = core.CONFIG.get("gw_token") or ""
    cached = core.CONFIG.get("gw_user") or None
    base = {"server": server, "host": _host(server) if server else "",
            "server_mode": bool(core.CONFIG.get("server_mode"))}
    if not server or not token:
        out = {**base, "logged_in": False, "user": cached if server else None}
        if server and base["server_mode"]:
            # Token zahodil už launcher, když ho brána odmítla — appka se ale
            # pořád chce otevírat na serveru, tak ať je vidět proč se ptá.
            out["note"] = "Přihlášení na serveru skončilo, přihlas se znovu."
        return out

    data, err, kind = _call("/gw/me", token=token, timeout=timeout)
    if data and data.get("user"):
        user = data["user"]
        # Jméno nebo vault se mohly na serveru změnit — ať to nastavení ukazuje
        # to, co platí teď, ne co platilo při přihlášení.
        if user != cached:
            core.save_config({"gw_user": user})
        return {**base, "logged_in": True, "user": user}

    if kind == AUTH:
        # Token brána nezná (odhlášení z jiného zařízení, změna hesla) —
        # držet ho dál by jen mátlo. Uživatele si necháme: předvyplní e-mail.
        core.save_config({"gw_token": ""})
        return {**base, "logged_in": False, "user": cached, "expired": True,
                "note": "Přihlášení na serveru skončilo, přihlas se znovu."}

    return {**base, "logged_in": bool(cached), "user": cached,
            "offline": True, "note": err}


def login(server, email, password):
    base = normalize(server)
    if not base:
        return {"error": "Zadej adresu serveru."}
    data, err, kind = _call("/login", base=base,
                            payload={"email": (email or "").strip(),
                                     "password": password or ""})
    if not data or not data.get("token"):
        return {"error": err or "Přihlášení se nepovedlo."}
    # Adresa se ukládá až po úspěchu: nepovedený pokus nesmí přepsat server,
    # na kterém je člověk přihlášený.
    core.save_config({"gw_server": base, "gw_token": data["token"],
                      "gw_user": data.get("user")})
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
    # Po odhlášení se appka spouští zase na počítači; adresa i e-mail zůstanou
    # zapamatované, ať se dá přihlásit zpátky bez vypisování.
    core.save_config({"gw_token": "", "server_mode": False})
    return status()


def handoff(timeout=TIMEOUT):
    """Adresa, po jejímž otevření je okno přihlášené na serveru.

    Vrací {"url"} nebo {"error", "kind"} — podle druhu appka pozná, jestli
    nabídnout „zkusit znovu", nebo přihlášení.
    """
    token = core.CONFIG.get("gw_token") or ""
    if not _base() or not token:
        return {"error": "Nejsi přihlášený.", "kind": AUTH}
    data, err, kind = _call("/gw/handoff", token=token, payload={},
                            timeout=timeout)
    if not data or not data.get("url"):
        if kind == AUTH:
            core.save_config({"gw_token": ""})
        return {"error": err or "Nepovedlo se připravit přihlášení.",
                "kind": kind or HTTP}
    return {"url": data["url"]}
