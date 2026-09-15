#!/usr/bin/env python3
"""Sdílené Obsidiany pro vybrané lidi — pro Claude Code v prostoru na bráně.

    python3 sdilene.py lide                                    kdo je v týmu
    python3 sdilene.py seznam                                  kde jsem členem
    python3 sdilene.py zalozit NÁZEV --lide e1,e2 --potvrzeno
    python3 sdilene.py navrh OBSIDIAN CÍL [SOUBOR | -] --potvrzeno
    python3 sdilene.py clenove OBSIDIAN [--pridat e1,e2] [--odebrat e3] --potvrzeno
    python3 sdilene.py odejit OBSIDIAN --potvrzeno
    python3 sdilene.py smazat OBSIDIAN --potvrzeno

OBSIDIAN je zkratka ze `seznam` (třeba marketing). Nic se neprovede hned: návrh
čeká v ~/.firma/ke-schvaleni/, hub ho uživateli ukáže s tlačítkem k potvrzení
a provede ho brána (gateway/shared.py), která znovu ověří, kdo co smí.
Bez --potvrzeno nevznikne ani návrh — Claude se má nejdřív zeptat v chatu.
"""
import json
import os
import re
import secrets
import sys
import time

HOME = os.path.expanduser("~")
PENDING = os.path.join(HOME, ".firma", "ke-schvaleni")
CLAUDE_DIR = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(HOME, ".claude")
MAX_TEXT = 512 * 1024


def fail(message, code=1):
    print(message, file=sys.stderr)
    sys.exit(code)


def config():
    try:
        with open(os.path.join(CLAUDE_DIR, "hub-config.json"), encoding="utf-8-sig") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


CFG = config()


def me():
    user = CFG.get("gateway_user") or {}
    if not user.get("email"):
        fail("Sdílené Obsidiany jsou jen v prostorech na serveru (bráně).")
    return user


def team():
    return {p["email"].lower(): p for p in CFG.get("people") or [] if p.get("email")}


def label(email):
    person = team().get(email, {})
    return person.get("name") or email


def vaults():
    return CFG.get("shared_vaults") or []


def find(slug):
    for v in vaults():
        if v.get("slug") == slug:
            return v
    fail(f"Sdílený Obsidian „{slug}“ tu nemáš. `seznam` ukáže, kde jsi členem — "
         "nově založený se do prostoru načte až po jeho restartu.")


def option(args, name):
    """Hodnota přepínače `--jmeno hodnota` a zbytek argumentů."""
    if name in args:
        i = args.index(name)
        if i + 1 >= len(args):
            fail(f"Za {name} chybí hodnota.")
        return args[i + 1], args[:i] + args[i + 2:]
    return "", args


def emails(value, allowed):
    found = [e.strip().lower() for e in re.split(r"[,\s]+", value or "") if e.strip()]
    unknown = [e for e in found if e not in allowed]
    if unknown:
        fail("Tihle lidé v týmu nejsou: " + ", ".join(unknown) + ". `lide` vypíše, kdo je.")
    return list(dict.fromkeys(found))


def confirmed(args, what):
    if "--potvrzeno" in args:
        return [a for a in args if a != "--potvrzeno"]
    print("Ještě nic nevzniklo — nejdřív se zeptej uživatele v chatu: " + what +
          " Až to výslovně potvrdí, spusť příkaz znovu s --potvrzeno.", file=sys.stderr)
    sys.exit(3)


def target(rel):
    raw = str(rel or "").replace("\\", "/").strip()
    rel = raw.strip("/")
    if rel and not rel.lower().endswith(".md"):
        rel += ".md"
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if (not parts or len(rel) > 300 or raw.startswith("/") or re.match(r"^[A-Za-z]:", rel)
            or any(p == ".." or p.startswith(".") for p in parts)):
        fail(f"Neplatná cesta: {rel or '(prázdná)'} — zadej relativní cestu, třeba kampane/zari.md.")
    return "/".join(parts)


def save(data, done):
    os.makedirs(PENDING, exist_ok=True)
    pid = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
    data = dict(data, autor=me()["email"], vytvoreno=time.strftime("%Y-%m-%d %H:%M:%S"))
    tmp = os.path.join(PENDING, pid + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    os.replace(tmp, os.path.join(PENDING, pid + ".json"))
    print(done)
    print("Hub teď uživateli ukázal kartu — provede se, až ji potvrdí.")


def cmd_lide(_args):
    me()
    for email, p in sorted(team().items()):
        print(f"{email}  {p.get('name') or ''}".rstrip())


def cmd_seznam(_args):
    me()
    if not vaults():
        print("Zatím nejsi členem žádného sdíleného Obsidianu.")
    for v in vaults():
        owner = "ty" if v.get("is_owner") else v.get("owner")
        print(f"{v['slug']}  „{v['name']}“  {v['path']}")
        print("  členové: " + ", ".join(m.get("name") or m["email"] for m in v.get("members", []))
              + f" · založil: {owner}")


def cmd_zalozit(args):
    user = me()
    people, args = option(args, "--lide")
    if not args or not args[0].strip():
        fail("Použití: sdilene.py zalozit NÁZEV --lide e1,e2 --potvrzeno")
    name = " ".join(args[0].split())[:60]
    allowed = set(team())
    chosen = [e for e in emails(people, allowed) if e != user["email"].lower()]
    if not chosen:
        fail("Vyber aspoň jednoho dalšího člověka (--lide) — pro sebe má uživatel osobní Obsidian.")
    names = [label(e) for e in chosen]
    confirmed(args, f"založit sdílený Obsidian „{name}“ pro {', '.join(names)} (a uživatele).")
    save({"druh": "sdilene-zalozit", "nazev": name, "emaily": chosen,
          "lide": [label(user["email"].lower())] + names},
         f"Návrh je připravený: založit „{name}“ pro {', '.join(names)}.")


def cmd_navrh(args):
    me()
    args = [a for a in args]
    plain = [a for a in args if a != "--potvrzeno"]
    if len(plain) < 2:
        fail("Použití: sdilene.py navrh OBSIDIAN CÍL [SOUBOR | -] --potvrzeno")
    v = find(plain[0])
    rel = target(plain[1])
    source = plain[2] if len(plain) > 2 else "-"
    exists = os.path.exists(os.path.join(v["path"], *rel.split("/")))
    who = ", ".join(m.get("name") or m["email"] for m in v.get("members", []))
    confirmed(args, f"do sdíleného Obsidianu „{v['name']}“ nahraješ {rel}"
                    + (" a PŘEPÍŠEŠ tím existující poznámku" if exists else "")
                    + f" — uvidí to: {who}. Stručně shrň obsah.")
    try:
        if source == "-":
            text = sys.stdin.read()
        else:
            with open(source, encoding="utf-8") as fh:
                text = fh.read()
    except OSError as exc:
        fail(f"Soubor s poznámkou nejde přečíst: {exc}")
    if not text.strip():
        fail("Poznámka je prázdná — nic se nenavrhlo.")
    if len(text.encode("utf-8")) > MAX_TEXT:
        fail("Poznámka je moc velká (víc než 512 kB).")
    save({"druh": "sdilene-zapis", "vault": v["slug"], "nazev": v["name"], "cil": rel,
          "text": text, "lide": [m.get("name") or m["email"] for m in v.get("members", [])]},
         f"Návrh je připravený: {rel} do „{v['name']}“" + (" (přepíše existující)" if exists else ""))


def cmd_clenove(args):
    user = me()
    add, args = option(args, "--pridat")
    remove, args = option(args, "--odebrat")
    plain = [a for a in args if a != "--potvrzeno"]
    if not plain or not (add or remove):
        fail("Použití: sdilene.py clenove OBSIDIAN [--pridat e1,e2] [--odebrat e3] --potvrzeno")
    v = find(plain[0])
    if not v.get("is_owner") and user.get("role") != "admin":
        fail(f"Členy mění jen ten, kdo „{v['name']}“ založil ({v.get('owner')}).")
    members = {m["email"].lower() for m in v.get("members", [])}
    to_add = [e for e in emails(add, set(team())) if e not in members]
    to_remove = [e for e in emails(remove, members)]
    if v.get("owner", "").lower() in to_remove:
        fail("Zakladatele odebrat nejde — sdílený Obsidian můžeš smazat.")
    if not to_add and not to_remove:
        fail("Nic se nemění — tihle lidé už v něm jsou (nebo nejsou).")
    what = "; ".join(filter(None, [
        "přidat " + ", ".join(label(e) for e in to_add) if to_add else "",
        "odebrat " + ", ".join(label(e) for e in to_remove) if to_remove else ""]))
    confirmed(args, f"v „{v['name']}“ {what}.")
    save({"druh": "sdilene-clenove", "vault": v["slug"], "nazev": v["name"],
          "emaily_pridat": to_add, "emaily_odebrat": to_remove,
          "pridat": [label(e) for e in to_add], "odebrat": [label(e) for e in to_remove]},
         f"Návrh je připravený: {what} v „{v['name']}“.")


def cmd_odejit(args):
    me()
    plain = [a for a in args if a != "--potvrzeno"]
    if not plain:
        fail("Použití: sdilene.py odejit OBSIDIAN --potvrzeno")
    v = find(plain[0])
    if v.get("is_owner"):
        fail("Zakladatel odejít nemůže — sdílený Obsidian můžeš smazat.")
    confirmed(args, f"odejít ze sdíleného Obsidianu „{v['name']}“ (vrátit tě může jen zakladatel).")
    save({"druh": "sdilene-odejit", "vault": v["slug"], "nazev": v["name"]},
         f"Návrh je připravený: odejít z „{v['name']}“.")


def cmd_smazat(args):
    user = me()
    plain = [a for a in args if a != "--potvrzeno"]
    if not plain:
        fail("Použití: sdilene.py smazat OBSIDIAN --potvrzeno")
    v = find(plain[0])
    if not v.get("is_owner") and user.get("role") != "admin":
        fail(f"Smazat „{v['name']}“ může jen ten, kdo ho založil ({v.get('owner')}).")
    who = ", ".join(m.get("name") or m["email"] for m in v.get("members", []))
    confirmed(args, f"smazat sdílený Obsidian „{v['name']}“ — zmizí všem ({who}).")
    save({"druh": "sdilene-smazat", "vault": v["slug"], "nazev": v["name"],
          "lide": [m.get("name") or m["email"] for m in v.get("members", [])]},
         f"Návrh je připravený: smazat „{v['name']}“.")


COMMANDS = {"lide": cmd_lide, "seznam": cmd_seznam, "zalozit": cmd_zalozit, "navrh": cmd_navrh,
            "clenove": cmd_clenove, "odejit": cmd_odejit, "smazat": cmd_smazat}


def main(argv):
    cmd = argv[0] if argv else ""
    if cmd in COMMANDS:
        COMMANDS[cmd](argv[1:])
    else:
        print(__doc__.strip())
        sys.exit(0 if cmd in ("-h", "--help", "") else 2)


if __name__ == "__main__":
    main(sys.argv[1:])
