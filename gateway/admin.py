#!/usr/bin/env python3
"""
Správa účtů brány z příkazové řádky.

Běží na serveru pod uživatelem `hub`. První účet je vždycky admin — bez něj by
se do webové správy nedalo dostat. Heslo se buď zadá skrytě (getpass), nebo
přes `--password` (hodí se do skriptu, ale zůstane v historii shellu).

    python3 -m gateway.admin add jmeno@firma.cz --name "Jméno" --role admin
    python3 -m gateway.admin list
    python3 -m gateway.admin passwd jmeno@firma.cz
    python3 -m gateway.admin role jmeno@firma.cz user
    python3 -m gateway.admin vault jmeno@firma.cz "Jméno Brain"
    python3 -m gateway.admin disable jmeno@firma.cz
    python3 -m gateway.admin enable jmeno@firma.cz
    python3 -m gateway.admin remove jmeno@firma.cz
    python3 -m gateway.admin sessions            # které prostory běží
    python3 -m gateway.admin stop jmeno@firma.cz # zastavit prostor
    python3 -m gateway.admin stop --orphans      # zastavit osiřelé
    python3 -m gateway.admin auth jmeno@firma.cz central   # klíč API / own
    python3 -m gateway.admin auth --vsechny central
    python3 -m gateway.admin apikey set          # společný klíč pro prostory
    python3 -m gateway.admin apikey set --user jmeno@firma.cz   # klíč jen jeho
    python3 -m gateway.admin apikey status | remove
    python3 -m gateway.admin skills install      # firemní skilly do trezoru
    python3 -m gateway.admin skills status | update
    python3 -m gateway.admin google set          # klient OAuth pro Google (jednou)
    python3 -m gateway.admin google status | remove
"""
import argparse
import getpass
import json
import os
import shutil
import subprocess
import sys
import time

from . import config, isolation, shared, workspace
from .accounts import Accounts


def _accounts():
    accounts = Accounts(config.DB_PATH)
    shared.ACCOUNTS = accounts          # jména členů sdílených Obsidianů
    return accounts


def _ask_password(given):
    if given:
        return given
    first = getpass.getpass("Heslo (min. 10 znaků): ")
    again = getpass.getpass("Heslo znovu: ")
    if first != again:
        sys.exit("Hesla se neshodují.")
    return first


def cmd_add(a, args):
    pw = _ask_password(args.password)
    uid = a.add(args.email, pw, name=args.name or "", role=args.role,
                vault=args.vault or "")
    # Domov a vault připravíme rovnou, ať první přihlášení nic nezdržuje —
    # rovnou pod jménem podle e-mailu (prostor ještě nikdy neběžel), jinak by
    # ensure založil starý u<id> a přejmenovával se až při přihlášení.
    user = a.get(args.email)
    workspace.migrate_home(user)
    workspace.ensure(user)
    print(f"Založen účet #{uid}: {args.email} ({args.role})")


def cmd_list(a, args):
    rows = a.list()
    if not rows:
        print("Zatím žádné účty.")
        return
    w = max(len(r["email"]) for r in rows)
    print(f'{"e-mail":<{w}}  role   claude    2fa  vault              stav')
    for r in rows:
        stav = "vypnutý" if r["disabled"] else "aktivní"
        auth = r.get("claude_auth") or "central"
        twofa = "ano" if r.get("twofa") else "ne"
        print(f'{r["email"]:<{w}}  {r["role"]:<5}  {auth:<8}  {twofa:<3}  '
              f'{(r["vault"] or "-"):<18} {stav}')


def cmd_sdilene(a, args):
    """Sdílené Obsidiany: název, složka, zakladatel a členové."""
    vaults = shared.all_vaults()
    if not vaults:
        print("Zatím žádný sdílený Obsidian.")
        return
    for v in vaults:
        print(f'{v["name"]}  ({v["path"]})')
        print(f'  založil: {v["owner"] or "(účet smazaný — spravuje správce)"}')
        print("  členové: " + (", ".join(m["email"] for m in v["members"]) or "nikdo"))


def cmd_2fa(a, args):
    """Dvoufázové ověření: stav, nebo reset (ztracený telefon i záložní kódy)."""
    if args.action == "status":
        email = (args.email or "").strip().lower()
        rows = [r for r in a.list() if not email or r["email"] == email]
        if not rows:
            raise ValueError(f"{args.email} tu žádný účet nemá." if email else "Zatím žádné účty.")
        w = max(len(r["email"]) for r in rows)
        for r in rows:
            st = a.twofa(r["id"])
            print(f'{r["email"]:<{w}}  ' + (
                f'zapnuté, záložních kódů zbývá {st["recovery_left"]}' if st["enabled"]
                else "nenastavené — nastaví si ho při příštím přihlášení"))
        return
    if not args.email:
        raise ValueError("Zadej e-mail účtu.")
    a.reset_totp(args.email)
    print(f"{args.email}: dvoufázové ověření zrušené a všechna zařízení odhlášená. "
          "Při příštím přihlášení si ho nastaví znovu.")


def cmd_passwd(a, args):
    a.set_password(args.email, _ask_password(args.password))
    print(f"Heslo pro {args.email} změněno (a všechna zařízení odhlášena).")


def cmd_role(a, args):
    a.set_role(args.email, args.role)
    print(f"{args.email} má teď roli {args.role}.")


def cmd_vault(a, args):
    a.set_vault(args.email, args.vault)
    print(f"{args.email} má vault „{args.vault}“.")


def cmd_auth(a, args):
    kde = "klíč API" if args.mode == "central" else "vlastní účet"
    if args.vsechny:
        if args.email:
            raise ValueError("Zadej buď e-mail, nebo --vsechny, ne obojí.")
        rows = a.list()
        for r in rows:
            a.set_auth(r["email"], args.mode)
        print(f"Přepnuto na {kde}: účtů {len(rows)}.")
    elif not args.email:
        raise ValueError("Zadej e-mail účtu, nebo --vsechny.")
    else:
        a.set_auth(args.email, args.mode)
        print(f"{args.email}: Claude se teď ověřuje přes {kde}.")
    print("Projeví se při dalším startu prostoru — běžící zastavíš:"
          " stop <e-mail>.")


def _check_key(key):
    """Ověří klíč u API — seznam modelů nic nestojí. Vrací důvod odmítnutí,
    nebo ''. Bez spojení se klíč uloží i tak; ověří se až v prostoru."""
    import urllib.error
    import urllib.request
    req = urllib.request.Request("https://api.anthropic.com/v1/models", headers={
        "x-api-key": key, "anthropic-version": "2023-06-01"})
    try:
        with urllib.request.urlopen(req, timeout=10):
            return ""
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return "Anthropic klíč odmítl (neplatný nebo zrušený)."
        return ""
    except Exception:
        print("Klíč se nepodařilo ověřit (bez spojení s API) — ukládám ho i tak.")
        return ""


def _user_or_die(a, email):
    user = a.get(email)
    if not user:
        raise ValueError(f"{email} tu žádný účet nemá.")
    return user


def _key_path(user=None):
    """Kde leží klíč: společný, nebo vlastní podle čísla účtu (e-mail se do
    cesty nedostane)."""
    return (os.path.join(config.API_KEYS_DIR, str(user["id"])) if user
            else config.API_KEY_FILE)


def _key_info(path):
    """(posledních 5 znaků, kdy uložen), nebo ('', '') když tam klíč není."""
    try:
        with open(path, encoding="utf-8") as fh:
            key = fh.read().strip()
        if not key:
            return "", ""
        when = time.strftime("%d. %m. %Y %H:%M",
                             time.localtime(os.path.getmtime(path)))
        return key[-5:], when
    except OSError:
        return "", ""


def _save_key(path, key):
    """Klíč umí přečíst jen uživatel hub (0600 v 0700 složce) — v prostoru je
    pak vidět jedině v prostředí toho, komu patří."""
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    if parent == config.API_KEYS_DIR:
        os.chmod(parent, 0o700)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(key + "\n")
    os.replace(tmp, path)


def cmd_apikey(a, args):
    """Klíč API pro prostory na `central`. Leží jen u brány (0600, uživatel
    hub) a do prostoru přijde jako ANTHROPIC_API_KEY při jeho startu.

    Bez `--user` je to společný klíč pro každého, kdo svůj nemá. S `--user`
    klíč jednoho účtu: ten pak má v Anthropic Console vlastní strop a kdo si
    klíč v prostoru přečte z prostředí, přečte jen ten svůj."""
    user = _user_or_die(a, args.user) if args.user else None
    path = _key_path(user)
    kdo = user["email"] if user else "Společný klíč"
    if args.action == "status":
        tail, when = _key_info(path)
        if user:
            print(f"{kdo}: " + (f"vlastní klíč …{tail} (uložen {when})" if tail
                                else "vlastní klíč nemá — jede na společném"))
            return
        print(f"Společný klíč: " + (f"…{tail}, uložen {when}" if tail
                                    else "není nastavený"))
        rows = [u for u in a.list()
                if (u.get("claude_auth") or "central") == "central"]
        svoje = 0
        for u in rows:
            t, w = _key_info(_key_path(u))
            svoje += bool(t)
            print(f"  {u['email']:<28} " + (f"vlastní klíč …{t} ({w})" if t
                                            else "společný klíč"))
        print(f"Účtů na klíči API: {len(rows)} (z toho na vlastním: {svoje});"
              f" na vlastním účtu Claude: {len(a.list()) - len(rows)}.")
        if not tail and svoje < len(rows):
            print("Pozor: kdo je na společném klíči a ten není nastavený,"
                  " se v prostoru musí přihlásit sám.")
        return
    if args.action == "remove":
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        print((f"{kdo}: vlastní klíč smazán — od dalšího startu prostoru jede"
               " na společném." if user else
               "Společný klíč smazán. Běžící prostory ho mají do svého"
               " dalšího startu."))
        return
    key = (getpass.getpass(f"Klíč API pro {kdo} (sk-ant-…): ")
           if sys.stdin.isatty() else sys.stdin.readline()).strip()
    if not key.startswith("sk-ant-"):
        raise ValueError("Tohle nevypadá jako klíč API Anthropicu (začíná sk-ant-).")
    problem = _check_key(key)
    if problem:
        raise ValueError(problem)
    _save_key(path, key)
    print(f"{kdo}: klíč uložen (…{key[-5:]}). Platí od dalšího startu prostoru;"
          " běžící zastavíš: stop <e-mail>.")


SKILLS_REPO = os.environ.get("HUB_SKILLS_REPO", "jurapascal/claude-brain-skills")


def _skill_count(root):
    return sum(1 for _d, _s, files in os.walk(root) if "SKILL.md" in files)


def _git(*args):
    """(povedlo se, výstup). Bez shellu — cesty i adresa repozitáře jdou
    argumentem."""
    try:
        done = subprocess.run(("git",) + args, capture_output=True, text=True,
                              timeout=600)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    return done.returncode == 0, (done.stdout + done.stderr).strip()


def _move_aside(root):
    """Stávající skilly odloží vedle trezoru (ne do něj — v Obsidianu by se
    ukázaly dvakrát). Vrací, kam."""
    away = os.path.join(config.COMPANY_DIR,
                        "_skilly-" + time.strftime("%Y%m%d-%H%M%S"))
    shutil.move(root, away)
    os.makedirs(root, exist_ok=True)
    return away


def cmd_skills(a, args):
    """Firemní skilly — `<firemní trezor>/skills`.

    Leží v trezoru, takže je má každý prostor jen ke čtení jako zbytek trezoru
    a nikdo je nemá zvlášť u sebe. Claude o nich ví z pokynů, které brána píše
    do `~/.claude/CLAUDE.md` prostoru při jeho startu (workspace._company_block)
    — nové skilly se tam objeví po dalším startu prostoru."""
    workspace.ensure_company_vault()
    root = config.COMPANY_SKILLS
    clone = os.path.isdir(os.path.join(root, ".git"))
    obsah = [n for n in os.listdir(root) if not n.startswith(".")]

    if args.action == "status":
        print(f"Firemní skilly: {root}")
        if not obsah:
            print("Zatím tam žádné nejsou — zavedeš je:"
                  " claude-hub-admin skills install")
            return
        cats = workspace.company_skill_categories()
        print(f"skillů: {_skill_count(root)}, kategorií: {len(cats)}")
        if cats:
            print("  " + ", ".join(cats))
        if clone:
            ok, out = _git("-C", root, "log", "-1", "--format=%cd %s",
                           "--date=short")
            src = _git("-C", root, "remote", "get-url", "origin")[1]
            print(f"z gitu: {src}" + (f" ({out})" if ok else ""))
            print("aktualizace: claude-hub-admin skills update")
        else:
            print("nejsou z gitu — aktualizuješ je znovu přes"
                  " skills install --from <cesta>")
        return

    if args.action == "update":
        if not clone:
            raise ValueError("Skilly nejsou z gitu — nahraj novou verzi:"
                             " skills install --from <cesta> --force")
        ok, out = _git("-C", root, "pull", "--ff-only")
        if not ok:
            raise ValueError(f"Aktualizace nevyšla: {out or 'git mlčí'}")
        print(f"Skilly aktuální: {_skill_count(root)}."
              " Prostory je uvidí po svém dalším startu.")
        return

    # install
    if obsah and not args.force:
        raise ValueError(f"Ve {root} už skilly jsou ({_skill_count(root)})."
                         " Aktualizuj je: skills update — nebo přepiš:"
                         " skills install --force")
    away = _move_aside(root) if obsah else ""
    if args.src:
        src = os.path.abspath(os.path.expanduser(args.src))
        if not _skill_count(src):
            raise ValueError(f"V {src} žádný SKILL.md není.")
        shutil.copytree(src, root, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(".git"))
        odkud = src
    else:
        ok, out = _git("clone", "--quiet", "--depth", "1",
                       f"https://github.com/{SKILLS_REPO}.git", root)
        if not ok:
            raise ValueError(f"Stažení nevyšlo: {out or 'git mlčí'}")
        odkud = SKILLS_REPO
    cats = workspace.company_skill_categories()
    print(f"Firemní skilly z {odkud}: {_skill_count(root)} ve {len(cats)}"
          f" kategoriích → {root}")
    if away:
        print(f"Předchozí odloženy do {away} (smaž je, až to prověříš).")
    print("Prostory je uvidí po svém dalším startu — hned to bude:"
          " claude-hub-admin stop <e-mail>.")


def cmd_google(a, args):
    """Klient OAuth pro napojení na Google (Nastavení → Napojení v prostorech).

    Zakládá se jednou pro všechny v Google Cloudu (typ Desktopová aplikace,
    aplikace zveřejněná — v testovacím režimu Google přihlášení po 7 dnech
    zruší). Lidé si pak v prostoru přidávají vlastní Google účty."""
    path = config.GOOGLE_CLIENT_FILE
    if args.action == "status":
        cid, _ = workspace.google_client()
        if cid:
            when = time.strftime("%d. %m. %Y %H:%M", time.localtime(os.path.getmtime(path)))
            print(f"Klient Google: {cid} (uložen {when}).")
        else:
            print("Klient Google není nastavený — Google v prostorech čeká na správce.")
        return
    if args.action == "remove":
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        print("Klient Google smazán. Napojené účty přestanou fungovat po dalším startu prostoru.")
        return
    tty = sys.stdin.isatty()
    cid = (input("Client ID: ") if tty else sys.stdin.readline()).strip()
    secret = (getpass.getpass("Client secret: ") if tty else sys.stdin.readline()).strip()
    if not cid.endswith(".apps.googleusercontent.com"):
        raise ValueError("Client ID končí na .apps.googleusercontent.com — zkontroluj, co jsi vložil.")
    if not secret:
        raise ValueError("Chybí client secret.")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump({"client_id": cid, "client_secret": secret}, fh)
    os.replace(tmp, path)
    print("Klient Google uložen. Platí od dalšího startu prostoru; lidé si pak "
          "v Nastavení → Napojení přidají svoje Google účty.")


def cmd_disable(a, args):
    a.set_disabled(args.email, True)
    print(f"{args.email} zablokován (a odhlášen).")


def cmd_enable(a, args):
    a.set_disabled(args.email, False)
    print(f"{args.email} znovu povolen.")


def cmd_remove(a, args):
    user = a.get(args.email)
    a.remove(args.email)
    if not user:
        return
    shared.forget_user(user["id"])
    # Prostor smazaného účtu nemá komu běžet — a nikdo by ho už nezastavil.
    for name in _units(user):
        isolation.stop_scope(name)
    moved = workspace.retire(user)
    print(f"{args.email} smazán z databáze." +
          (f" Jeho složka je odložená v {moved}." if moved else ""))


def _units(user):
    """Jména scope, pod kterými může prostor účtu běžet: podle e-mailu a to
    staré `claude-hub-u<id>` z doby před restartem brány po aktualizaci."""
    return [workspace.unit_name(user), workspace.legacy_unit_name(user)]


def _by_unit(a):
    return {name: u for u in a.list() for name in _units(u)}


def cmd_sessions(a, args):
    """Běžící prostory a kolik drží paměti. Starší bez jména jsou osiřelé."""
    scopes = isolation.running_scopes()
    if not scopes:
        print("Neběží žádný prostor.")
        return
    by_unit = _by_unit(a)
    for name, _desc in scopes:
        user = by_unit.get(name)
        who = user["email"] if user else "(osiřelý — brána o něm neví)"
        mb = isolation.scope_memory(name) / 1024 / 1024
        print(f"{name:<45} {mb:>7.0f} MB  {who}")


def cmd_stop(a, args):
    if args.orphans:
        known = _by_unit(a)
        names = [n for n, _d in isolation.running_scopes() if n not in known]
    elif not args.email:
        raise ValueError("Zadej e-mail účtu, nebo --orphans.")
    else:
        user = a.get(args.email)
        if not user:
            raise ValueError(f"{args.email} tu žádný účet nemá.")
        running = {n for n, _d in isolation.running_scopes()}
        names = [n for n in _units(user) if n in running] or _units(user)[:1]
    if not names:
        print("Není co zastavit.")
    for name in names:
        ok = isolation.stop_scope(name)
        print(f"{name}: {'zastaveno' if ok else 'NEPODAŘILO SE zastavit'}")


def build_parser():
    p = argparse.ArgumentParser(prog="gateway.admin",
                                description="Správa účtů brány.")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="založit účet")
    a.add_argument("email")
    a.add_argument("--name", default="")
    a.add_argument("--role", choices=("user", "admin"), default="user")
    a.add_argument("--vault", default="", help="jméno Obsidian paměti")
    a.add_argument("--password", default="", help="jinak se zeptá skrytě")
    a.set_defaults(func=cmd_add)

    sub.add_parser("list", help="vypsat účty").set_defaults(func=cmd_list)

    pw = sub.add_parser("passwd", help="změnit heslo")
    pw.add_argument("email")
    pw.add_argument("--password", default="")
    pw.set_defaults(func=cmd_passwd)

    ro = sub.add_parser("role", help="změnit roli")
    ro.add_argument("email")
    ro.add_argument("role", choices=("user", "admin"))
    ro.set_defaults(func=cmd_role)

    va = sub.add_parser("vault", help="přejmenovat vault")
    va.add_argument("email")
    va.add_argument("vault")
    va.set_defaults(func=cmd_vault)

    au = sub.add_parser("auth", help="Claude: klíč API (central) / vlastní účet (own)")
    au.add_argument("email", nargs="?", default="")
    au.add_argument("mode", choices=("central", "own"))
    au.add_argument("--vsechny", action="store_true",
                    help="přepnout všechny účty místo jednoho")
    au.set_defaults(func=cmd_auth)

    ak = sub.add_parser("apikey", help="klíč API pro prostory na central")
    ak.add_argument("action", choices=("set", "status", "remove"))
    ak.add_argument("--user", default="",
                    help="e-mail účtu — klíč jen jeho, místo společného")
    ak.set_defaults(func=cmd_apikey)

    sk = sub.add_parser("skills", help="firemní skilly ve firemním Obsidianu")
    sk.add_argument("action", choices=("install", "update", "status"))
    sk.add_argument("--from", dest="src", default="",
                    help="složka se skilly místo stažení z gitu")
    sk.add_argument("--force", action="store_true",
                    help="přepsat skilly, které tam už jsou")
    sk.set_defaults(func=cmd_skills)

    go = sub.add_parser("google", help="klient OAuth pro napojení na Google")
    go.add_argument("action", choices=("set", "status", "remove"))
    go.set_defaults(func=cmd_google)

    di = sub.add_parser("disable", help="zablokovat účet")
    di.add_argument("email")
    di.set_defaults(func=cmd_disable)

    en = sub.add_parser("enable", help="znovu povolit účet")
    en.add_argument("email")
    en.set_defaults(func=cmd_enable)

    rm = sub.add_parser("remove", help="smazat účet")
    rm.add_argument("email")
    rm.set_defaults(func=cmd_remove)

    sd = sub.add_parser("sdilene", help="sdílené Obsidiany: kdo je založil a kdo v nich je")
    sd.set_defaults(func=cmd_sdilene)

    tf = sub.add_parser("2fa", help="dvoufázové ověření: stav, nebo reset (ztracený telefon)")
    tf.add_argument("action", choices=("status", "reset"))
    tf.add_argument("email", nargs="?", default="", help="e-mail účtu (u status volitelný)")
    tf.set_defaults(func=cmd_2fa)

    sub.add_parser("sessions", help="které prostory běží a kolik berou paměti"
                   ).set_defaults(func=cmd_sessions)

    st = sub.add_parser("stop", help="zastavit prostor (i s Claude Code v něm)")
    st.add_argument("email", nargs="?", default="", help="e-mail účtu")
    st.add_argument("--orphans", action="store_true",
                    help="zastavit prostory, ke kterým žádný účet nepatří")
    st.set_defaults(func=cmd_stop)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    a = _accounts()
    try:
        args.func(a, args)
    except ValueError as exc:
        sys.exit(f"Chyba: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
