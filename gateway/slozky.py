"""
Osobní a sdílené složky ve firemním Obsidianu.

Firemní trezor má dvě zvláštní složky vedle firemních poznámek:

* `Lidé/<jméno>` — osobní Obsidian člověka (jeho trezor v domově prostoru).
  Vidí ji jen on a zapisuje do ní rovnou, jako do osobního trezoru.
* `Sdílené/<název>` — sdílený Obsidian (gateway/shared.py). Vidí ho jen jeho
  členové; zápis jde přes kartu jako dřív.

Uvnitř nejsou kopie, ale odkazy na skutečné trezory: data zůstávají, kde
byla, a zálohy, git ani Obsidian na počítači je nezdvojí. Odkazy drží brána
(`sync`, při každém startu prostoru) — jiné než její odkazy v těch složkách
nemaže, skutečné soubory tam nechá být (patří pod firemní pravidla).

Kdo co vidí, se nerozhoduje v registru poznámek, ale tady: odkaz cizího
člověka nebo trezoru, kde uživatel není členem, je v jeho prostoru skrytý
stejně natvrdo jako omezená poznámka (poznamky.hidden_for → mask_args).
V prostoru by takový odkaz stejně vedl do prázdna — cizí domov ani cizí
sdílený trezor v sandboxu není —, skrývá se kvůli jménům.
"""
import os
import re
import threading

from . import config, shared

PEOPLE = "Lidé"
SHARED = "Sdílené"
ROOTS = (PEOPLE, SHARED)
_LOCK = threading.Lock()


def _clean(name):
    """Jméno složky: bez lomítek, skrytých začátků a znaků, které v cestě zlobí."""
    name = re.sub(r"[\x00-\x1f/\\:*?\"<>|]", " ", str(name or ""))
    name = " ".join(name.split()).strip(". ")[:60]
    return name


def _unique(name, taken, fallback):
    name = _clean(name) or _clean(fallback) or "bez-jmena"
    if name.lower() not in taken:
        return name
    alt = f"{name} ({_clean(fallback)})" if _clean(fallback) and _clean(fallback) != name else name
    n = 2
    while alt.lower() in taken:
        alt, n = f"{name} {n}", n + 1
    return alt


def links():
    """Co má ve zvláštních složkách být: {rel: {kind, target, uids, owner, ...}}.

    Pořadí podle id (účty) a zkratky (trezory), ať se jména při shodě
    nepřehazují mezi lidmi."""
    from .workspace import home_for, vault_dir
    out = {}
    if shared.ACCOUNTS:
        taken = set()
        for r in sorted(shared.ACCOUNTS.list(), key=lambda r: r["id"]):
            if r["disabled"]:
                continue
            try:
                home = home_for(r)
            except (OSError, ValueError, KeyError):
                continue
            if not os.path.isdir(home):
                continue                     # ještě se nepřihlásil — domov není
            local = (r["email"] or "").split("@")[0]
            name = _unique(r.get("name") or local, taken, local)
            taken.add(name.lower())
            out[f"{PEOPLE}/{name}"] = {"kind": "osobni", "target": vault_dir(r, home),
                                       "uids": [r["id"]], "owner": r["id"],
                                       "name": r.get("name") or r["email"]}
    taken = set()
    registry = shared._load()
    for v in sorted(shared.all_vaults(), key=lambda v: v["slug"]):
        entry = registry.get(v["slug"]) or {}
        name = _unique(v["name"], taken, v["slug"])
        taken.add(name.lower())
        out[f"{SHARED}/{name}"] = {"kind": "sdilene", "target": shared.vault_dir(v["slug"]),
                                   "uids": list(entry.get("members") or []),
                                   "owner": entry.get("owner"), "slug": v["slug"],
                                   "name": v["name"], "members": v["members"]}
    return out


def special(rel):
    """Je cesta v `Lidé/` nebo `Sdílené/` (tedy mimo firemní pravidla)?"""
    return str(rel or "").split("/", 1)[0] in ROOTS


def sync(vault=None):
    """Srovná odkazy v `Lidé/` a `Sdílené/` s účty a sdílenými trezory.

    Maže jen odkazy, které vedou do domovů nebo sdílených trezorů (tedy ty
    naše). Skutečné soubory a složky nechá — někdo je tam mohl dát gitem."""
    from .workspace import USERS_ROOT
    vault = vault or config.COMPANY_VAULT
    want = links()
    ours = tuple({f(d) + os.sep for d in (USERS_ROOT, config.SHARED_DIR)
                  for f in (os.path.abspath, os.path.realpath)})
    with _LOCK:
        for top in ROOTS:
            base = os.path.join(vault, top)
            if os.path.islink(base):
                continue                     # podvržený odkaz — nesahat
            os.makedirs(base, exist_ok=True)
            for name in os.listdir(base):
                path = os.path.join(base, name)
                if not os.path.islink(path):
                    continue
                rel = f"{top}/{name}"
                target = os.readlink(path)
                if rel in want and target == want[rel]["target"]:
                    continue
                if rel in want or target.startswith(ours):
                    os.unlink(path)
            for rel, info in want.items():
                if not rel.startswith(top + "/"):
                    continue
                path = os.path.join(vault, *rel.split("/"))
                if not os.path.lexists(path):
                    os.symlink(info["target"], path)


def hidden_for(user):
    """Odkazy, které uživatel nevidí (relativně k trezoru, jen ty, co na disku jsou)."""
    uid = int(user["id"])
    vault = config.COMPANY_VAULT
    return sorted(rel for rel, info in links().items()
                  if uid not in info["uids"]
                  and os.path.islink(os.path.join(vault, *rel.split("/"))))


def visible_for(user):
    """Zvláštní složky, které uživatel ve firemním trezoru má — pro hub
    (odznak u složky a kam se zapisuje)."""
    uid = int(user["id"])
    out = []
    for rel, info in sorted(links().items()):
        if uid not in info["uids"]:
            continue
        item = {"path": rel, "kind": info["kind"]}
        if info["kind"] == "sdilene":
            item.update(slug=info["slug"], name=info["name"],
                        members=[m.get("name") or m["email"] for m in info["members"]])
        out.append(item)
    return out
