"""
Sdílené relace — majitel sdílí rozběhnutý chat s vybranými lidmi.

Kolega pak chat živě sleduje (konverzace, nástroje, kód, který Claude napsal),
vidí, že je sdílený a že Claude zrovna pracuje, a když na to má právo, píše do
něj taky. Chat zůstává v prostoru majitele: nic se nekopíruje a kolegovi se
neotevírá cizí hub — brána za něj čte z majitelova hubu a k majiteli se jeho
zprávy i vrací (hub/mcp_tools.py: sdilet_cteni, sdilet_poslat).

Kdo co smí, rozhoduje jen tenhle modul, ne prostor:

* sdílet a měnit členy, zrušit sdílení: majitel chatu (a správce),
* číst: majitel a členové,
* psát: majitel a členové s právem psát.

Registr drží brána mimo domovy (GATEWAY_DIR/relace.json) podle id účtů.
Kdo se zrovna dívá, se drží jen v paměti — je to „právě teď“, ne historie.
"""
import contextlib
import fcntl
import json
import os
import secrets
import threading
import time

from . import config, shared

REGISTRY = os.path.join(config.GATEWAY_DIR, "relace.json")
LOG = os.path.join(config.GATEWAY_DIR, "relace.jsonl")
_LOCK = threading.Lock()
_DIVA = {}                    # id relace → {uid: čas posledního dotazu}
DIVA_S = 15.0                # po jak dlouhém tichu už se člověk nedívá


@contextlib.contextmanager
def _locked():
    with _LOCK:
        os.makedirs(os.path.dirname(REGISTRY), exist_ok=True)
        with open(REGISTRY + ".lock", "a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)


def _load():
    try:
        with open(REGISTRY, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    rel = data.get("relace") if isinstance(data, dict) else None
    return rel if isinstance(rel, dict) else {}


def _save(rel):
    tmp = REGISTRY + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"relace": rel}, fh, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, REGISTRY)


def _log(user, action, rid, **extra):
    entry = {"cas": time.strftime("%Y-%m-%d %H:%M:%S"), "email": user.get("email", ""),
             "akce": action, "relace": rid, **extra}
    try:
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _account(uid):
    return shared.ACCOUNTS.by_id(uid) if shared.ACCOUNTS and uid else None


def _jmeno(uid):
    u = _account(uid)
    return ((u.get("name") or u["email"]) if u else "")


def _je_spravce(user):
    return user.get("role") == "admin"


def role(user, entry):
    """'majitel' | 'pise' | 'cte' | '' — co uživatel s relací smí."""
    uid = int(user["id"])
    if entry.get("owner") == uid:
        return "majitel"
    if uid in entry.get("psat", []):
        return "pise"
    if uid in entry.get("members", []):
        return "cte"
    return ""


def _public(rid, entry, user):
    uid = int(user["id"])
    owner = _account(entry.get("owner"))
    members = []
    for mid in entry.get("members", []):
        u = _account(mid)
        if u:
            members.append({"email": u["email"], "name": u.get("name", ""),
                            "pise": mid in entry.get("psat", [])})
    teď = time.time()
    diva = [_jmeno(m) for m, t in (_DIVA.get(rid) or {}).items()
            if teď - t < DIVA_S and m != uid]
    return {"id": rid, "chat": entry.get("chat", ""), "titulek": entry.get("titulek") or "Chat",
            "majitel": (owner.get("name") or owner["email"]) if owner else "",
            "role": role(user, entry) or ("spravce" if _je_spravce(user) else ""),
            "clenove": members, "diva": diva, "vytvoreno": entry.get("vytvoreno", 0)}


def seznam(user):
    """{moje: relace, kterou jsem založil, semnou: relace, kde jsem člen}."""
    uid = int(user["id"])
    rel = _load()
    moje, semnou = [], []
    for rid, entry in sorted(rel.items(), key=lambda x: -x[1].get("vytvoreno", 0)):
        if entry.get("owner") == uid:
            moje.append(_public(rid, entry, user))
        elif uid in entry.get("members", []):
            semnou.append(_public(rid, entry, user))
    return {"moje": moje, "semnou": semnou}


def zalozit(user, chat, titulek, emails, psat_emails=()):
    """Nová sdílená relace nad chatem majitele (nebo změna členů, když už je)."""
    chat = str(chat or "").strip()
    if not chat or len(chat) > 80 or not all(c.isalnum() or c in "-_" for c in chat):
        raise ValueError("Tenhle chat se nedá sdílet (chybí jeho číslo) — pošli v něm aspoň jednu zprávu.")
    ids, neznami = shared._resolve(emails)
    psat, _ = shared._resolve(psat_emails)
    uid = int(user["id"])
    ids = [m for m in ids if m != uid]
    if not ids:
        raise ValueError("Vyber aspoň jednoho kolegu, se kterým chat sdílíš.")
    psat = [m for m in psat if m in ids]
    with _locked():
        rel = _load()
        rid = next((r for r, e in rel.items() if e.get("owner") == uid and e.get("chat") == chat), None)
        novy = rid is None
        if novy:
            rid = secrets.token_urlsafe(9)
            rel[rid] = {"owner": uid, "chat": chat, "vytvoreno": int(time.time())}
        rel[rid].update(members=ids, psat=psat, titulek=str(titulek or "")[:120])
        _save(rel)
    _log(user, "zalozeno" if novy else "zmeneno", rid, clenove=len(ids), psat=len(psat))
    zprava = ("Chat je sdílený." if novy else "Sdílení je upravené.")
    if neznami:
        zprava += " Neznámé adresy: " + ", ".join(neznami) + "."
    return {"ok": True, "id": rid, "message": zprava}


def zrusit(user, rid):
    with _locked():
        rel = _load()
        entry = rel.get(rid)
        if not entry:
            raise ValueError("Tohle sdílení už neexistuje.")
        if entry.get("owner") != int(user["id"]) and not _je_spravce(user):
            raise ValueError("Sdílení může zrušit jen ten, kdo chat sdílí.")
        del rel[rid]
        _save(rel)
    _DIVA.pop(rid, None)
    _log(user, "zruseno", rid)
    return {"ok": True, "message": "Sdílení je zrušené."}


def odejit(user, rid):
    uid = int(user["id"])
    with _locked():
        rel = _load()
        entry = rel.get(rid)
        if not entry or uid not in entry.get("members", []):
            raise ValueError("V téhle relaci nejsi.")
        entry["members"] = [m for m in entry["members"] if m != uid]
        entry["psat"] = [m for m in entry.get("psat", []) if m != uid]
        if not entry["members"]:
            del rel[rid]
        _save(rel)
    _log(user, "odchod", rid)
    return {"ok": True, "message": "Odešel jsi ze sdílení."}


def ziskat(user, rid, psat=False):
    """Záznam relace, k němuž má uživatel přístup — nebo ValueError. Zápis
    (`psat=True`) smí majitel a členové s právem psát. Tímhle projde každé
    čtení i zápis z prohlížeče; nic jiného nevolá hub majitele."""
    entry = _load().get(str(rid or ""))
    r = role(user, entry) if entry else ""
    if not r or (psat and r == "cte"):
        raise ValueError("K téhle relaci nemáš přístup.")
    return entry, r


def dival_se(user, rid):
    """Zaznamená, že se uživatel právě dívá (ukazuje se majiteli i ostatním)."""
    _DIVA.setdefault(rid, {})[int(user["id"])] = time.time()


def jmeno(user):
    return user.get("name") or user.get("email") or "?"


def forget_user(uid):
    """Smazaný účet: jeho relace zmizí, z cizích odejde."""
    with _locked():
        rel = _load()
        zmena = False
        for rid, entry in list(rel.items()):
            if entry.get("owner") == uid:
                del rel[rid]
                zmena = True
            elif uid in entry.get("members", []):
                entry["members"] = [m for m in entry["members"] if m != uid]
                entry["psat"] = [m for m in entry.get("psat", []) if m != uid]
                zmena = True
                if not entry["members"]:
                    del rel[rid]
        if zmena:
            _save(rel)


_PSANI = {}                   # uid → časy posledních zpráv (proti zahlcení)


def smi_poslat(user):
    """Nejvýš 6 zpráv za 20 s na člověka — Claude Code to stejně nestíhá číst."""
    teď = time.time()
    casy = [t for t in _PSANI.get(int(user["id"]), []) if teď - t < 20]
    if len(casy) >= 6:
        _PSANI[int(user["id"])] = casy
        return False
    casy.append(teď)
    _PSANI[int(user["id"])] = casy
    return True


def verejne(user, rid, entry):
    return _public(rid, entry, user)


def pridej_stav(result, pub, moje_role):
    """K odpovědi s přepisem přidá, kdo chat sdílí, kdo se dívá a co smím."""
    result["relace"] = {"majitel": pub["majitel"], "titulek": pub["titulek"],
                        "clenove": pub["clenove"], "diva": pub["diva"],
                        "role": moje_role, "psat": moje_role in ("majitel", "pise")}
