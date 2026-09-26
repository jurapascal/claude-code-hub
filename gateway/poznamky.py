"""
Kdo vidí kterou poznámku ve firemním Obsidianu.

Bez záznamu poznámku vidí každý, kdo vidí firemní Obsidian (gateway/accounts.py:
none / read / write). U poznámky jde vybrat konkrétní lidi — pak ji vidí jen
oni a správci poznámek. Nastavovat to smí jen **správci poznámek**: pevný
seznam účtů (`claude-hub-admin poznamky spravci …`), nezávislý na roli admin.

Skrytí je natvrdo: prostor skrytou poznámku vůbec nemá. Firemní trezor se do
sandboxu přiváže celý jen ke čtení a přes složky, ve kterých je něco skrytého,
se položí prázdný tmpfs, do kterého se znovu přivážou jen viditelné položky
(`mask_args`). Claude, terminál ani hub v prostoru tak o skryté poznámce nevědí.
Změna se projeví při dalším startu prostoru; kdo přístup ztratil a zrovna
nepracuje, tomu brána prostor zastaví hned.

Registr leží vedle trezoru, ne v něm (COMPANY_DIR/poznamky-pristupy.json), podle
id účtů a relativních cest. Přesun poznámky mimo bránu (git, Obsidian na
počítači správce) omezení nepřenese — poznámka na nové cestě je pro všechny.
"""
import contextlib
import fcntl
import json
import os
import threading
import time

from . import config, shared

REGISTRY = os.path.join(config.COMPANY_DIR, "poznamky-pristupy.json")
LOG = os.path.join(config.COMPANY_DIR, "poznamky-pristupy.jsonl")
_LOCK = threading.Lock()
# Místo prázdného seznamu u poznámky, kterou vidí jen správci.
NOBODY = -1


@contextlib.contextmanager
def _locked():
    with _LOCK:
        os.makedirs(config.COMPANY_DIR, exist_ok=True)
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
        data = {}
    if not isinstance(data, dict):
        data = {}
    managers = [int(u) for u in data.get("spravci") or [] if isinstance(u, int)]
    notes = data.get("poznamky") if isinstance(data.get("poznamky"), dict) else {}
    notes = {str(rel): [int(u) for u in uids if isinstance(u, int)]
             for rel, uids in notes.items() if isinstance(uids, list)}
    return {"spravci": managers, "poznamky": notes}


def _save(data):
    tmp = REGISTRY + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, REGISTRY)


def _log(user, rel, before, after):
    before = [u for u in before or [] if u != NOBODY] if before else None
    after = [u for u in after if u != NOBODY] if after is not None else None
    entry = {"cas": time.strftime("%Y-%m-%d %H:%M:%S"), "email": (user or {}).get("email", ""),
             "poznamka": rel, "z": ([_email(u) for u in before] or "jen správci") if before is not None else "všichni",
             "na": ([_email(u) for u in after] or "jen správci") if after is not None else "všichni"}
    try:
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _account(uid):
    return shared.ACCOUNTS.by_id(uid) if shared.ACCOUNTS and uid else None


def _email(uid):
    u = _account(uid)
    return u["email"] if u else f"#{uid}"


def _rel(rel):
    from .workspace import company_rel
    return company_rel(rel)


# ── kdo co smí ───────────────────────────────────────────────────────────────
def managers():
    return list(_load()["spravci"])


def is_manager(user):
    return bool(user) and int(user["id"]) in _load()["spravci"]


def _sees(data, uid, rel):
    allowed = data["poznamky"].get(rel)
    return not allowed or uid in allowed or uid in data["spravci"]


def allowed(user, rel):
    """Smí `user` poznámku `rel` vidět (a tedy i přepsat)?"""
    try:
        rel = _rel(rel)
    except ValueError:
        return True                      # neplatnou cestu odmítne až zápis sám
    return _sees(_load(), int(user["id"]), rel)


def hidden_for(user, data=None):
    """Relativní cesty poznámek, které `user` nevidí a v trezoru opravdu jsou."""
    data = data or _load()
    uid = int(user["id"])
    vault = config.COMPANY_VAULT
    return sorted(rel for rel in data["poznamky"]
                  if not _sees(data, uid, rel)
                  and os.path.isfile(os.path.join(vault, *rel.split("/"))))


def masked_dirs():
    """Složky (relativně k trezoru), ve kterých je nějaká omezená poznámka —
    aspoň pro někoho v nich leží tmpfs s jednotlivě přivázanými soubory."""
    return {os.path.dirname(rel) for rel in _load()["poznamky"]}


# ── sandbox ──────────────────────────────────────────────────────────────────
def mask_args(vault, hidden):
    """Argumenty bwrap, které ze svázaného trezoru `vault` vynechají `hidden`.

    Musí přijít až po `--ro-bind vault vault`. Každá složka, ve které je něco
    skrytého, dostane přes sebe prázdný tmpfs a do něj se přivážou zpátky jen
    viditelné položky (podsložky celé, takže nové soubory v nich jsou vidět
    hned). Rodiče jdou před dětmi — podsložka, ve které je taky něco skrytého,
    se nejdřív přiváže a pak se přes ni položí vlastní tmpfs. Odkazy se
    nepřivazují, ale vyrobí znovu: bind by je na serveru následoval kamkoli.
    """
    vault = os.path.realpath(vault)
    by_dir = {}
    for rel in hidden:
        parts = rel.split("/")
        by_dir.setdefault("/".join(parts[:-1]), set()).add(parts[-1])
    args = []
    for rel_dir in sorted(by_dir, key=lambda d: (d.count("/") if d else -1, d)):
        src = os.path.join(vault, *rel_dir.split("/")) if rel_dir else vault
        if os.path.realpath(src) != src or not os.path.isdir(src):
            continue                     # odkaz nebo nic — nepřivazovat
        try:
            names = sorted(os.listdir(src))
        except OSError:
            continue
        args += ["--tmpfs", src]
        for name in names:
            if name in by_dir[rel_dir]:
                continue
            path = os.path.join(src, name)
            if os.path.islink(path):
                args += ["--symlink", os.readlink(path), path]
            elif os.path.isdir(path) or os.path.isfile(path):
                args += ["--ro-bind", path, path]
        args += ["--remount-ro", src]
    return args


# ── správa ───────────────────────────────────────────────────────────────────
def people():
    """Kdo je v týmu a jestli vidí firemní Obsidian — z toho se vybírá."""
    if not shared.ACCOUNTS:
        return []
    data = _load()
    out = []
    for r in shared.ACCOUNTS.list():
        if r["disabled"]:
            continue
        level = shared.ACCOUNTS.company_level(r)
        out.append({"email": r["email"], "name": r["name"], "firma": level,
                    "spravce": r["id"] in data["spravci"]})
    return out


def listing():
    """Omezené poznámky a kdo je vidí (pro správce)."""
    out = {}
    for rel, uids in sorted(_load()["poznamky"].items()):
        out[rel] = [{"email": u["email"], "name": u.get("name", "")}
                    for u in (_account(i) for i in uids) if u]
    return out


def _resolve(emails):
    ids, unknown = [], []
    for email in emails or []:
        email = str(email or "").strip().lower()
        if not email:
            continue
        u = shared.ACCOUNTS.get(email) if shared.ACCOUNTS else None
        if not u or u.get("disabled"):
            unknown.append(email)
        elif u["id"] not in ids:
            ids.append(u["id"])
    return ids, unknown


def set_note(user, rel, emails, only=None):
    """Nastaví, kdo poznámku vidí. `only` False (nebo prázdný seznam bez
    `only`) = všichni, omezení se zruší. `only` s prázdným seznamem = jen
    správci poznámek.

    Vrací {"ok", "path", "people", "revoked": [id účtů, které ji přestaly
    vidět], "granted": [id, které ji nově vidí]}."""
    if not is_manager(user):
        raise PermissionError("Kdo vidí poznámku, nastavují jen správci poznámek.")
    rel = _rel(rel)
    if not os.path.isfile(os.path.join(config.COMPANY_VAULT, *rel.split("/"))):
        raise ValueError(f"Poznámka {rel} ve firemním Obsidianu není.")
    if isinstance(emails, str):
        emails = emails.replace(",", " ").split()
    ids, unknown = _resolve(emails)
    if unknown:
        raise ValueError("Tihle lidé tu účet nemají: " + ", ".join(unknown))
    everyone = [r["id"] for r in shared.ACCOUNTS.list()] if shared.ACCOUNTS else []
    if only is None:
        only = bool(ids)
    with _locked():
        data = _load()
        before = data["poznamky"].get(rel, [])
        saw = {u for u in everyone if _sees(data, u, rel)}
        if only:
            # -1 = nikdo navíc; prázdný seznam by znamenal „všichni“.
            data["poznamky"][rel] = sorted(ids) or [NOBODY]
        else:
            ids = []
            data["poznamky"].pop(rel, None)
        sees = {u for u in everyone if _sees(data, u, rel)}
        _save(data)
    _log(user, rel, before, ids if only else None)
    return {"ok": True, "path": rel, "jen": bool(only), "people": [_email(i) for i in ids],
            "revoked": sorted(saw - sees), "granted": sorted(sees - saw)}


def set_managers(emails):
    """Správci poznámek (claude-hub-admin). Nahradí celý seznam."""
    ids, unknown = _resolve(emails)
    if unknown:
        raise ValueError("Tihle lidé tu účet nemají: " + ", ".join(unknown))
    with _locked():
        data = _load()
        data["spravci"] = ids
        _save(data)
    return [_email(i) for i in ids]


def forget_user(uid):
    """Smazaný účet: pryč ze správců i ze všech poznámek. Poznámka, kterou
    by pak neviděl nikdo, zůstane omezená — vidí ji správci."""
    if not os.path.exists(REGISTRY):
        return
    with _locked():
        data = _load()
        changed = uid in data["spravci"]
        data["spravci"] = [u for u in data["spravci"] if u != uid]
        for rel, uids in data["poznamky"].items():
            if uid in uids:
                data["poznamky"][rel] = [u for u in uids if u != uid] or [NOBODY]
                changed = True
        if changed:
            _save(data)
