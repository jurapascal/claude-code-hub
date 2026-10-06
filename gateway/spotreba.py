"""
Spotřeba Claude Code po lidech — pro admina (Statistiky → Tým) a příkaz
`claude-hub-admin spotreba`.

Čísla jsou z přepisů sezení, které si Claude Code každému ukládá do domova
(`~/.claude/projects/*/*.jsonl`) — stejný zdroj jako Statistiky v hubu
(hub/stats.py). Je to odhad podle ceníku API, ne faktura: na předplatném se
po tokenech neplatí, ale dá se podle něj srovnat, kdo kolik čerpá ze
společného limitu.

Počítá se přírůstkově a na pozadí: přepis, který se od minula nezměnil, se
nečte znovu, a první průchod (stovky MB) neblokuje odpověď — stránka dostane
„počítám“ a za chvíli čísla. Čte se jen to, co se za poslední týden změnilo.

Limity: týdenní strop v USD (odhad) pro každého zvlášť. Strop nic nevypíná —
jen ukáže, že je člověk blízko nebo nad, adminovi i jemu samotnému (hub mu
to připomene hláškou nahoře) a počítá se, kolikrát už přes strop přešel.
Počítání dělá hlídač na pozadí (`sledovani`), který se po pár minutách sám
podívá, jak kdo stojí — i když nikdo Statistiky neotevřel.
"""
import json
import os
import threading
import time

from hub import stats

from . import config, workspace

CACHE = os.path.join(config.GATEWAY_DIR, "spotreba-cache.json")
LIMITY = os.path.join(config.GATEWAY_DIR, "spotreba-limity.json")
PRESLO = os.path.join(config.GATEWAY_DIR, "spotreba-preslo.json")   # kolikrát přes strop
DNI = 7
STARE = 8 * 86400            # přepis starší než tohle se do týdne nevejde
OBNOVA = 300                 # nejdřív za 5 minut znovu projít disk
BLIZKO = 0.8                 # od kolika procent stropu se varuje

_lock = threading.Lock()
_stav = {"cas": 0, "bezi": False, "lide": {}}


def _load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)


def limity():
    out = {}
    for email, v in _load(LIMITY).items():
        try:
            if float(v) > 0:
                out[str(email).lower()] = float(v)
        except (TypeError, ValueError):
            pass
    return out


def set_limit(email, usd):
    """Týdenní strop v USD; 0 nebo prázdné = bez stropu."""
    email = str(email or "").strip().lower()
    if not email:
        raise ValueError("Chybí e-mail.")
    try:
        usd = float(str(usd or 0).replace(",", "."))
    except ValueError:
        raise ValueError("Limit musí být číslo (USD za týden).") from None
    if usd < 0 or usd > 100000:
        raise ValueError("Limit musí být mezi 0 a 100 000 USD.")
    data = _load(LIMITY)
    if usd:
        data[email] = round(usd, 2)
    else:
        data.pop(email, None)
    os.makedirs(config.GATEWAY_DIR, exist_ok=True)
    _save(LIMITY, data)
    return usd


def _prepisy(home):
    """Přepisy sezení z posledního týdne; symlinky se přeskakují (domov je
    psatelný pro prostor člověka, tak ať brána nečte, kam ukáže on)."""
    root = os.path.join(home, ".claude", "projects")
    hranice = time.time() - STARE
    try:
        slozky = [e for e in os.scandir(root) if e.is_dir(follow_symlinks=False)]
    except OSError:
        return
    for slozka in slozky:
        try:
            for e in os.scandir(slozka.path):
                if e.name.endswith(".jsonl") and e.is_file(follow_symlinks=False):
                    st = e.stat(follow_symlinks=False)
                    if st.st_mtime >= hranice:
                        yield e.path, st
        except OSError:
            continue


def _clovek(home, cache):
    dny = {}
    soubory = set()
    for path, st in _prepisy(home):
        soubory.add(path)
        c = cache.get(path)
        if not (c and c.get("size") == st.st_size and c.get("mtime") == int(st.st_mtime)):
            try:
                r = stats._scan_file(path)
            except OSError:
                continue
            c = cache[path] = {"size": st.st_size, "mtime": int(st.st_mtime),
                               "days": r["days"], "dcost": r["dcost"]}
        for den, usd in (c.get("dcost") or {}).items():
            d = dny.setdefault(den, {"cost": 0.0, "out": 0})
            d["cost"] += usd
            d["out"] += (c.get("days") or {}).get(den, 0)
    return dny, soubory


def _projdi(users):
    cache = _load(CACHE)
    videne = set()
    lide = {}
    for u in users:
        try:
            home = workspace.home_for(u)
        except OSError:
            continue
        dny, soubory = _clovek(home, cache)
        videne |= soubory
        lide[u["email"].lower()] = dny
    for gone in set(cache) - videne:
        cache.pop(gone, None)
    try:
        os.makedirs(config.GATEWAY_DIR, exist_ok=True)
        _save(CACHE, cache)
    except OSError:
        pass
    return lide


def _tyden(dny):
    hranice = time.strftime("%Y-%m-%d", time.gmtime(time.time() - (DNI - 1) * 86400))
    return sum(d["cost"] for den, d in dny.items() if den >= hranice)


def preslo():
    """{e-mail: {"pocet": kolikrát přes strop, "stav": poslední stav, "naposledy": kdy}}"""
    return _load(PRESLO)


def _zapocti(lide):
    """Přechod z „v pořádku / blízko“ na „nad stropem“ je jedno překročení.
    Člověk, který zůstane nad, se nepočítá znovu; až klesne a zase přeleze,
    připočte se další. Změnu stavu zapíše na disk."""
    lim = limity()
    data = preslo()
    zmena = False
    for email, dny in lide.items():
        zaznam = data.get(email) or {"pocet": 0, "stav": "", "naposledy": ""}
        stav = _stav_limitu(_tyden(dny), lim.get(email, 0))
        if stav != zaznam.get("stav"):
            if stav == "nad":
                zaznam["pocet"] = int(zaznam.get("pocet") or 0) + 1
                zaznam["naposledy"] = time.strftime("%Y-%m-%d %H:%M")
            zaznam["stav"] = stav
            data[email] = zaznam
            zmena = True
    if zmena:
        try:
            os.makedirs(config.GATEWAY_DIR, exist_ok=True)
            _save(PRESLO, data)
        except OSError:
            pass


def _na_pozadi(users):
    try:
        lide = _projdi(users)
        _zapocti(lide)
        with _lock:
            _stav["lide"] = lide
            _stav["cas"] = time.time()
    except Exception as exc:                      # hlídač nesmí spadnout s bránou
        print("spotřeba:", exc, flush=True)
    finally:
        with _lock:
            _stav["bezi"] = False


def sledovani(get_users, kazdych=600):
    """Vlákno brány: každých pár minut přepočítá spotřebu, ať se překročení
    stropu zaznamená, i když Statistiky nikdo nemá otevřené."""
    def smycka():
        time.sleep(30)
        while True:
            with _lock:
                volno = not _stav["bezi"]
                if volno:
                    _stav["bezi"] = True
            if volno:
                try:
                    _na_pozadi([dict(u) for u in get_users()])
                except Exception as exc:
                    print("spotřeba:", exc, flush=True)
            time.sleep(kazdych)
    threading.Thread(target=smycka, daemon=True).start()


def _stav_limitu(cost, limit):
    if not limit:
        return ""
    if cost >= limit:
        return "nad"
    return "blizko" if cost >= BLIZKO * limit else "ok"


def prehled(users, only=None, wait=0.0):
    """Spotřeba za posledních 7 dní po lidech. `users` = řádky účtů,
    `only` = e-mail, pro kterého se vrací jen jeden řádek (běžný člověk).
    Vrací {"lide": [...], "pocita": bool, "stari": sekundy}."""
    with _lock:
        stale = time.time() - _stav["cas"] > OBNOVA
        if stale and not _stav["bezi"]:
            _stav["bezi"] = True
            threading.Thread(target=_na_pozadi, args=([dict(u) for u in users],),
                             daemon=True).start()
    if wait:
        end = time.time() + wait
        while time.time() < end and not _stav["cas"]:
            time.sleep(0.2)
    with _lock:
        lide, cas, bezi = dict(_stav["lide"]), _stav["cas"], _stav["bezi"]
    hranice = time.strftime("%Y-%m-%d", time.gmtime(time.time() - (DNI - 1) * 86400))
    dnes = time.strftime("%Y-%m-%d", time.gmtime())
    lim = limity()
    kolikrat = preslo()
    out = []
    for u in users:
        email = u["email"].lower()
        if only and email != only.lower():
            continue
        dny = lide.get(email, {})
        tyden = sum(d["cost"] for den, d in dny.items() if den >= hranice)
        tokenu = sum(d["out"] for den, d in dny.items() if den >= hranice)
        limit = lim.get(email, 0)
        out.append({
            "id": u["id"], "email": u["email"], "name": u.get("name") or "",
            "tyden": round(tyden, 2), "tokenu": tokenu,
            "dnes": round(dny.get(dnes, {}).get("cost", 0.0), 2),
            "dny": [{"den": den, "cost": round(d["cost"], 2)}
                    for den, d in sorted(dny.items()) if den >= hranice],
            "limit": limit, "stav": _stav_limitu(tyden, limit),
            "preslo": int((kolikrat.get(email) or {}).get("pocet") or 0),
            "preslo_kdy": (kolikrat.get(email) or {}).get("naposledy") or "",
            "zablokovany": bool(u.get("disabled")),
        })
    out.sort(key=lambda r: -r["tyden"])
    soucet = sum(r["tyden"] for r in out)
    for r in out:
        r["podil"] = round(r["tyden"] / soucet, 3) if soucet else 0
    return {"lide": out, "soucet": round(soucet, 2),
            "pocita": bool(bezi and not cas), "stari": int(time.time() - cas) if cas else None}
