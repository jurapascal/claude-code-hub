"""
Sdílené Obsidiany pro vybrané lidi — vedle osobního a firemního trezoru.

Kdokoli si přes Clauda založí trezor pro sebe a konkrétní kolegy („Marketing“
pro Jiřího a Petra). Stejně jako firemní je v prostorech jen ke čtení a mění ho
brána, a to až po potvrzení kartou v hubu (/gw/firma/publish, druh sdilene-*).
Brána znovu ověří, kdo co smí — návrh z prostoru nic nerozhoduje:

* založení: zakladatel + vybraní lidé (aspoň jeden další),
* zápis poznámky: jen člen,
* změna členů a smazání: jen zakladatel, nebo správce,
* odchod: člen, který ho nezaložil.

Kdo je kde, drží registr mimo domovy (SHARED_DIR/.sdilene.json) podle id účtů.
Prostor dostane svázané jen trezory, jejichž je členem. Přidání i odebrání se
tak projeví až při jeho dalším startu — hub restart nabídne a brána zastaví
prostor odebraného člověka hned, pokud v něm zrovna nikdo nepracuje.
"""
import contextlib
import fcntl
import json
import os
import re
import threading
import time
import unicodedata

from . import config

# Účty (gateway.accounts.Accounts) — nastaví brána při startu a claude-hub-admin.
ACCOUNTS = None
REGISTRY = os.path.join(config.SHARED_DIR, ".sdilene.json")
LOG = os.path.join(config.SHARED_DIR, "zmeny.jsonl")
SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,49}")
_LOCK = threading.Lock()


@contextlib.contextmanager
def _locked():
    with _LOCK:
        os.makedirs(config.SHARED_DIR, exist_ok=True)
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
    vaults = data.get("vaults") if isinstance(data, dict) else None
    return vaults if isinstance(vaults, dict) else {}


def _save(vaults):
    tmp = REGISTRY + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"vaults": vaults}, fh, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, REGISTRY)


def _log(user, action, slug, **extra):
    entry = {"cas": time.strftime("%Y-%m-%d %H:%M:%S"), "email": user.get("email", ""),
             "akce": action, "obsidian": slug, **extra}
    try:
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def vault_dir(slug):
    return os.path.join(config.SHARED_DIR, slug)


def _account(uid):
    return ACCOUNTS.by_id(uid) if ACCOUNTS and uid else None


def _label(uid):
    u = _account(uid)
    return (u.get("name") or u["email"]) if u else ""


def _public(slug, entry, uid):
    members = []
    for mid in entry.get("members", []):
        u = _account(mid)
        if u:
            members.append({"email": u["email"], "name": u.get("name", "")})
    owner = _account(entry.get("owner"))
    return {"slug": slug, "name": entry.get("name") or slug, "path": vault_dir(slug),
            "owner": owner["email"] if owner else "", "is_owner": entry.get("owner") == uid,
            "members": members}


def vaults_for(user):
    """Sdílené Obsidiany, jejichž je uživatel členem (jen ty se mu svážou)."""
    uid = int(user["id"])
    return [_public(slug, entry, uid) for slug, entry in sorted(_load().items())
            if uid in entry.get("members", []) and os.path.isdir(vault_dir(slug))]


def all_vaults():
    return [_public(slug, entry, None) for slug, entry in sorted(_load().items())]


def people():
    """Kdo je v týmu — z toho se vybírá, s kým sdílet."""
    if not ACCOUNTS:
        return []
    return [{"email": r["email"], "name": r["name"]} for r in ACCOUNTS.list() if not r["disabled"]]


def _resolve(emails):
    """E-maily → id aktivních účtů. Vrací (ids, neznámé e-maily)."""
    if isinstance(emails, str):
        emails = re.split(r"[,\s]+", emails)
    ids, unknown = [], []
    for email in emails or []:
        email = str(email or "").strip().lower()
        if not email:
            continue
        u = ACCOUNTS.get(email) if ACCOUNTS else None
        if not u or u.get("disabled"):
            unknown.append(email)
        elif u["id"] not in ids:
            ids.append(u["id"])
    return ids, unknown


def _slugify(name, taken):
    base = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")[:40] or "sdileny"
    slug, n = base, 2
    while slug in taken or os.path.lexists(vault_dir(slug)):
        slug, n = f"{base}-{n}", n + 1
    return slug


def _entry(vaults, slug):
    slug = str(slug or "")
    if not SLUG.fullmatch(slug) or slug not in vaults:
        raise ValueError("Takový sdílený Obsidian není.")
    return slug, vaults[slug]


def _can_manage(user, entry):
    return entry.get("owner") == int(user["id"]) or user.get("role") == "admin"


def create(user, name, emails):
    uid = int(user["id"])
    name = " ".join(str(name or "").split())[:60]
    if not name:
        raise ValueError("Sdílený Obsidian potřebuje název.")
    ids, unknown = _resolve(emails)
    if unknown:
        raise ValueError("Tihle lidé tu účet nemají: " + ", ".join(unknown))
    members = [uid] + [i for i in ids if i != uid]
    if len(members) < 2:
        raise ValueError("Vyber aspoň jednoho dalšího člověka — pro sebe máš osobní Obsidian.")
    with _locked():
        vaults = _load()
        if any((v.get("name") or "").lower() == name.lower() and uid in v.get("members", [])
               for v in vaults.values()):
            raise ValueError(f"Sdílený Obsidian „{name}“ už máš.")
        slug = _slugify(name, vaults)
        os.makedirs(vault_dir(slug))
        with open(os.path.join(vault_dir(slug), "README.md"), "w", encoding="utf-8") as fh:
            fh.write(f"# {name}\n\nSdílený Obsidian pro: "
                     + ", ".join(_label(m) for m in members) + ".\n")
        vaults[slug] = {"name": name, "owner": uid, "members": members,
                        "created": time.strftime("%Y-%m-%d %H:%M:%S")}
        _save(vaults)
    _log(user, "zalozit", slug, clenove=[_label(m) for m in members])
    return {"ok": True, "slug": slug, "name": name, "affected": members,
            "message": f"Sdílený Obsidian „{name}“ je založený pro {len(members)} lidi. "
                       "Do prostoru se načte po restartu — nabídne ho panel Sdílené Obsidiany."}


def write_note(user, slug, rel, text, overwrite=False):
    from .workspace import company_rel
    if not isinstance(text, str):
        raise ValueError("Návrh je poškozený.")
    slug, entry = _entry(_load(), slug)
    if int(user["id"]) not in entry.get("members", []):
        raise ValueError("Do tohohle sdíleného Obsidianu zapisují jen jeho členové.")
    rel = company_rel(rel)
    vault = os.path.realpath(vault_dir(slug))
    target = os.path.join(vault, *rel.split("/"))
    if os.path.commonpath([os.path.realpath(os.path.dirname(target)), vault]) != vault:
        raise ValueError("Neplatná cesta ve sdíleném Obsidianu.")
    existed = os.path.exists(target)
    if existed and not overwrite:
        return {"ok": False, "exists": True, "path": rel}
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = target + ".hub-tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, target)
    _log(user, "zapis", slug, cil=rel, prepsano=existed)
    return {"ok": True, "path": rel, "overwritten": existed,
            "message": f"Nahráno do sdíleného Obsidianu „{entry.get('name') or slug}“: {rel}"}


def change_members(user, slug, add, remove):
    with _locked():
        vaults = _load()
        slug, entry = _entry(vaults, slug)
        if not _can_manage(user, entry):
            raise ValueError("Členy mění jen ten, kdo sdílený Obsidian založil.")
        add_ids, unknown = _resolve(add)
        remove_ids, unknown2 = _resolve(remove)
        if unknown or unknown2:
            raise ValueError("Tihle lidé tu účet nemají: " + ", ".join(unknown + unknown2))
        if entry.get("owner") in remove_ids:
            raise ValueError("Zakladatele odebrat nejde — sdílený Obsidian můžeš smazat.")
        before = list(entry.get("members", []))
        entry["members"] = [m for m in before if m not in remove_ids] + \
                           [a for a in add_ids if a not in before]
        added = [a for a in entry["members"] if a not in before]
        removed = [m for m in before if m not in entry["members"]]
        if not added and not removed:
            raise ValueError("Nic se nemění — tihle lidé už v něm jsou (nebo nejsou).")
        _save(vaults)
    _log(user, "clenove", slug, pridano=[_label(a) for a in added], odebrano=[_label(r) for r in removed])
    return {"ok": True, "slug": slug, "affected": added + removed, "revoked": removed,
            "message": f"Členové „{entry.get('name') or slug}“ změněni. Přidaným se načte a odebraným "
                       "zmizí po restartu jejich prostoru."}


def leave(user, slug):
    uid = int(user["id"])
    with _locked():
        vaults = _load()
        slug, entry = _entry(vaults, slug)
        if entry.get("owner") == uid:
            raise ValueError("Zakladatel odejít nemůže — sdílený Obsidian můžeš smazat.")
        if uid not in entry.get("members", []):
            raise ValueError("V tomhle sdíleném Obsidianu nejsi.")
        entry["members"] = [m for m in entry["members"] if m != uid]
        _save(vaults)
    _log(user, "odejit", slug)
    return {"ok": True, "slug": slug, "affected": [uid], "revoked": [],
            "message": f"Odešel jsi ze sdíleného Obsidianu „{entry.get('name') or slug}“. "
                       "Z prostoru zmizí po jeho restartu."}


def delete(user, slug):
    with _locked():
        vaults = _load()
        slug, entry = _entry(vaults, slug)
        if not _can_manage(user, entry):
            raise ValueError("Smazat ho může jen ten, kdo ho založil.")
        stamp = time.strftime("%Y%m%d-%H%M%S")
        if os.path.isdir(vault_dir(slug)):
            os.rename(vault_dir(slug), os.path.join(config.SHARED_DIR, f"_smazany-{slug}-{stamp}"))
        members = list(entry.get("members", []))
        del vaults[slug]
        _save(vaults)
    _log(user, "smazat", slug)
    others = [m for m in members if m != int(user["id"])]
    return {"ok": True, "slug": slug, "affected": members, "revoked": others,
            "message": f"Sdílený Obsidian „{entry.get('name') or slug}“ je smazaný. "
                       "Soubory zůstaly na serveru stranou."}


def forget_user(uid):
    """Smazaný účet: pryč ze všech sdílených Obsidianů (bez zakladatele je
    spravuje správce)."""
    if not os.path.exists(REGISTRY):
        return                       # žádný sdílený Obsidian ještě nevznikl
    with _locked():
        vaults = _load()
        changed = False
        for entry in vaults.values():
            if uid in entry.get("members", []):
                entry["members"] = [m for m in entry["members"] if m != uid]
                changed = True
            if entry.get("owner") == uid:
                entry["owner"] = None
                changed = True
        if changed:
            _save(vaults)
