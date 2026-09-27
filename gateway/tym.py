"""
Správa týmu z hubu (Nastavení → Tým) — jen pro adminy.

Totéž, co umí `claude-hub-admin` na serveru, jen naklikáním: pozvat člověka,
nové heslo, role, zablokování, reset ověření z telefonu, správci poznámek
a odemknutí zablokovaného přihlášení. Mazání účtu zůstává jen na serveru —
je nevratné a odkládá celý domov.

Hesla vymýšlí brána (čitelná slova + čísla), ukážou se adminovi jednou a on
je předá. Při prvním přihlášení si člověk nastaví ověření v telefonu (2FA)
a heslo si může změnit v Nastavení → Účet. Každá změna jde do záznamu
`gateway/tym.jsonl` (kdo, komu, co — bez hesel).
"""
import json
import os
import secrets
import time

from . import config, poznamky, workspace

LOG = os.path.join(config.GATEWAY_DIR, "tym.jsonl")

_WORDS = ("jablko hruska tresen svestka malina borovka ostruzina meloun citron pomeranc "
          "slunce mesic hvezda mrak vitr dest duha snih led kamen reka potok more ostrov "
          "hora les louka pole zahrada kvetina ruze tulipan lipa dub buk javor briza "
          "kocka pes kun ovce koza kralik liska jezek sova vrana labut kachna ryba zelva "
          "stul zidle lampa okno dvere kniha pero papir hrnek talir lzice vidlicka "
          "vlak auto kolo lod letadlo most vez hrad mlyn kostel skola park trh").split()


def new_password():
    """Heslo, které jde nadiktovat: tři slova a číslo (přes 40 bitů náhody)."""
    words = [secrets.choice(_WORDS) for _ in range(3)]
    return "-".join(words) + "-" + str(secrets.randbelow(900) + 100)


def _log(admin, target, action, **extra):
    entry = {"cas": time.strftime("%Y-%m-%d %H:%M:%S"), "admin": admin.get("email", ""),
             "komu": target, "akce": action, **extra}
    try:
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def people(accounts, hubs=None):
    """Tým pro stránku: kdo je kdo a v jakém stavu."""
    managers = set(poznamky.managers())
    locked = {part[len("email:"):] for r in accounts.fail_list() if r["locked"]
              for part in r["key"].split("|") if part.startswith("email:")}
    out = []
    for r in accounts.list():
        running = False
        if hubs:
            proc = hubs.procs.get(r["id"])
            running = bool(proc and proc.alive())
        out.append({
            "id": r["id"], "email": r["email"], "name": r["name"] or "",
            "role": r["role"], "blokovany": bool(r["disabled"]),
            "overeni": bool(r["twofa"]),
            "firma": accounts.company_level(dict(r)) if not r["disabled"] else "none",
            "spravce": r["id"] in managers,
            "zamceny": r["email"] in locked,
            "pracuje": running,
        })
    return out


def _target(accounts, form):
    try:
        uid = int(form.get("id"))
    except (TypeError, ValueError):
        raise ValueError("Neznámý člověk.") from None
    row = next((r for r in accounts.list() if r["id"] == uid), None)
    if not row:
        raise ValueError("Tenhle člověk tu účet nemá.")
    return row


def act(accounts, admin, form, hubs=None):
    """Provede jednu akci z Nastavení → Tým. Vrací odpověď pro stránku."""
    action = str(form.get("akce") or "")
    if action == "pozvat":
        email = str(form.get("email") or "").strip().lower()
        name = " ".join(str(form.get("jmeno") or "").split())[:80]
        role = "admin" if form.get("role") == "admin" else "user"
        password = new_password()
        # Jak se v prostoru přihlašuje Claude: stejně jako většina týmu.
        auths = [r.get("claude_auth") or "central" for r in accounts.list()]
        auth = max(set(auths), key=auths.count) if auths else "central"
        accounts.add(email, password, name=name, role=role, claude_auth=auth)
        user = accounts.get(email)
        try:
            workspace.migrate_home(user)
            workspace.ensure(user)
        except OSError:
            pass                         # domov se založí při prvním přihlášení
        _log(admin, email, "pozvat", role=role)
        return {"ok": True, "email": email, "heslo": password}

    row = _target(accounts, form)
    email = row["email"]
    if action == "heslo":
        password = new_password()
        accounts.set_password(email, password)
        _log(admin, email, "nove-heslo")
        return {"ok": True, "email": email, "heslo": password}
    if action == "role":
        role = "admin" if form.get("role") == "admin" else "user"
        if row["id"] == admin["id"] and role != "admin":
            raise ValueError("Sám sobě admina nevezmeš — požádej jiného admina.")
        accounts.set_role(email, role)
        _log(admin, email, "role", na=role)
        return {"ok": True}
    if action == "jmeno":
        accounts.set_name(email, form.get("jmeno"))
        _log(admin, email, "jmeno")
        return {"ok": True}
    if action == "blokovat":
        on = bool(form.get("zapnout"))
        if on and row["id"] == admin["id"]:
            raise ValueError("Sám sebe zablokovat nejde.")
        accounts.set_disabled(email, on)
        if on and hubs:
            hubs.restart({"id": row["id"]})      # zastavit jeho prostor hned
        _log(admin, email, "zablokovat" if on else "odblokovat")
        return {"ok": True}
    if action == "overeni":
        accounts.reset_totp(email)
        _log(admin, email, "reset-overeni")
        return {"ok": True}
    if action == "odemknout":
        accounts.fail_unlock(email)
        _log(admin, email, "odemknout")
        return {"ok": True}
    if action == "spravce":
        ids = set(poznamky.managers())
        if form.get("zapnout"):
            ids.add(row["id"])
        else:
            ids.discard(row["id"])
        emails = [r["email"] for r in accounts.list() if r["id"] in ids]
        poznamky.set_managers(emails)
        _log(admin, email, "spravce-poznamek", zapnuto=bool(form.get("zapnout")))
        return {"ok": True}
    raise ValueError("Neznámá akce.")
