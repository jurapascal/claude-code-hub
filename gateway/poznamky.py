"""
Kdo vidí kterou poznámku a složku ve firemním Obsidianu.

Bez záznamu poznámku vidí každý, kdo vidí firemní Obsidian (gateway/accounts.py:
none / read / write). U poznámky i u složky jde vybrat konkrétní lidi — pak ji
vidí jen oni a správci poznámek. Pravidlo složky platí na všechno v ní, i na
poznámky, které do ní přibudou později; poznámka ve složce musí projít
pravidlem složky i svým vlastním.

Složky `Lidé/` a `Sdílené/` sem nepatří — kdo je vidí, určuje vlastník
(gateway/slozky.py): osobní složku jen on, sdílenou její členové. Nastavovat to smí jen **správci poznámek**: pevný
seznam účtů (`claude-hub-admin poznamky spravci …`), nezávislý na roli admin.

Skrytí je natvrdo: prostor skrytou poznámku vůbec nemá. Firemní trezor se do
sandboxu přiváže celý jen ke čtení a přes složky, ve kterých je něco skrytého,
se položí prázdný tmpfs, do kterého se znovu přivážou jen viditelné položky
(`mask_args`). Claude, terminál ani hub v prostoru tak o skryté poznámce nevědí.
Změna se projeví při dalším startu prostoru; kdo přístup ztratil a zrovna
nepracuje, tomu brána prostor zastaví hned.

Registr leží vedle trezoru, ne v něm (COMPANY_DIR/poznamky-pristupy.json), podle
id účtů a relativních cest (`poznamky` a `slozky`). Přesun poznámky mimo bránu (git, Obsidian na
počítači správce) omezení nepřenese — poznámka na nové cestě je pro všechny.
"""
import contextlib
import fcntl
import json
import os
import re
import threading
import time

from . import config, shared, slozky

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
    out = {"spravci": managers}
    for key in ("poznamky", "slozky"):
        rules = data.get(key) if isinstance(data.get(key), dict) else {}
        out[key] = {str(rel): [int(u) for u in uids if isinstance(u, int)]
                    for rel, uids in rules.items() if isinstance(uids, list)}
    return out


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


def _dir_rel(rel):
    """Cesta ke složce ve firemním trezoru — jako company_rel, jen bez .md."""
    raw = str(rel or "").replace("\\", "/").strip()
    parts = [p for p in raw.strip("/").split("/") if p not in ("", ".")]
    if (not parts or len(raw) > 300 or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw)
            or any(p == ".." or p.startswith(".") for p in parts)):
        raise ValueError(f"Neplatná cesta ve firemním Obsidianu: {raw or '(prázdná)'}")
    return "/".join(parts)


# ── kdo co smí ───────────────────────────────────────────────────────────────
def managers():
    return list(_load()["spravci"])


def is_manager(user):
    return bool(user) and int(user["id"]) in _load()["spravci"]


def _sees(data, uid, rel):
    """Vidí `uid` cestu `rel`? Musí projít pravidly všech složek nad ní
    i vlastním (poznámky, nebo složky)."""
    if uid in data["spravci"]:
        return True
    parts = rel.split("/")
    for i in range(1, len(parts) + 1):
        path = "/".join(parts[:i])
        allowed = data["slozky"].get(path)
        if i == len(parts):
            allowed = data["poznamky"].get(path) or allowed
        if allowed and uid not in allowed:
            return False
    return True


def allowed(user, rel):
    """Smí `user` poznámku `rel` vidět (a tedy i přepsat)?"""
    try:
        rel = _rel(rel)
    except ValueError:
        return True                      # neplatnou cestu odmítne až zápis sám
    return _sees(_load(), int(user["id"]), rel)


def allowed_path(user, rel):
    """Jako `allowed`, jen pro jakýkoli soubor (ne jen .md) — nahrávání."""
    return _sees(_load(), int(user["id"]), str(rel))


def hidden_for(user, data=None):
    """Relativní cesty poznámek a složek, které `user` nevidí a v trezoru
    opravdu jsou — i cizí osobní a sdílené složky (gateway/slozky.py).
    Co leží ve skryté složce, se už nevypisuje: skryje se s ní."""
    data = data or _load()
    uid = int(user["id"])
    vault = config.COMPANY_VAULT

    def there(rel, check):
        path = os.path.join(vault, *rel.split("/"))
        return check(path) and not os.path.islink(path)

    hidden = {rel for rel in data["poznamky"] if there(rel, os.path.isfile)} | \
             {rel for rel in data["slozky"] if there(rel, os.path.isdir)}
    hidden = {rel for rel in hidden if not _sees(data, uid, rel)}
    hidden = {rel for rel in hidden
              if not any(rel.startswith(h + "/") for h in hidden)}
    return sorted(hidden | set(slozky.hidden_for(user)))


def masked_dirs():
    """Složky (relativně k trezoru), ve kterých je nějaká omezená poznámka nebo
    složka — aspoň pro někoho v nich leží tmpfs s jednotlivě přivázanými soubory."""
    data = _load()
    return ({os.path.dirname(rel) for rel in data["poznamky"]}
            | {os.path.dirname(rel) for rel in data["slozky"]} | set(slozky.ROOTS))


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
    """Omezené poznámky i složky a kdo je vidí (pro správce). Složky jsou
    bez .md, podle toho se od poznámek poznají."""
    data = _load()
    out = {}
    for rel, uids in sorted({**data["slozky"], **data["poznamky"]}.items()):
        out[rel] = [{"email": u["email"], "name": u.get("name", "")}
                    for u in (_account(i) for i in uids) if u]
    return out


def folders():
    """Cesty omezených složek."""
    return sorted(_load()["slozky"])


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


def _target(rel):
    """(druh, cesta): složka (`slozky`), nebo poznámka (`poznamky`)."""
    vault = config.COMPANY_VAULT
    raw = str(rel or "").replace("\\", "/").strip().strip("/")
    if slozky.special(raw):
        raise ValueError("Kdo vidí osobní a sdílené složky, určuje jejich vlastník — "
                         f"v {slozky.PEOPLE}/ a {slozky.SHARED}/ se to tady nenastavuje.")
    if not raw.lower().endswith(".md"):
        folder = _dir_rel(raw)
        path = os.path.join(vault, *folder.split("/"))
        if os.path.isdir(path) and not os.path.islink(path):
            return "slozky", folder
    rel = _rel(raw)
    if not os.path.isfile(os.path.join(vault, *rel.split("/"))):
        raise ValueError(f"{rel} ve firemním Obsidianu není.")
    return "poznamky", rel


def set_note(user, rel, emails, only=None):
    """Nastaví, kdo poznámku (nebo složku) vidí. `only` False (nebo prázdný
    seznam bez `only`) = všichni, omezení se zruší. `only` s prázdným
    seznamem = jen správci poznámek.

    Vrací {"ok", "path", "slozka", "people", "revoked": [id účtů, které ji
    přestaly vidět], "granted": [id, které ji nově vidí]}."""
    if not is_manager(user):
        raise PermissionError("Kdo vidí poznámku, nastavují jen správci poznámek.")
    kind, rel = _target(rel)
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
        before = data[kind].get(rel, [])
        saw = {u for u in everyone if _sees(data, u, rel)}
        if only:
            # -1 = nikdo navíc; prázdný seznam by znamenal „všichni“.
            data[kind][rel] = sorted(ids) or [NOBODY]
        else:
            ids = []
            data[kind].pop(rel, None)
        sees = {u for u in everyone if _sees(data, u, rel)}
        _save(data)
    _log(user, rel, before, ids if only else None)
    return {"ok": True, "path": rel, "slozka": kind == "slozky", "jen": bool(only),
            "people": [_email(i) for i in ids],
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
        for rules in (data["poznamky"], data["slozky"]):
            for rel, uids in rules.items():
                if uid in uids:
                    rules[rel] = [u for u in uids if u != uid] or [NOBODY]
                    changed = True
        if changed:
            _save(data)
