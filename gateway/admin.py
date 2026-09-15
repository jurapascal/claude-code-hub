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
    python3 -m gateway.admin apikey set          # klíč API pro prostory na central
    python3 -m gateway.admin apikey status | remove
"""
import argparse
import getpass
import os
import sys
import time

from . import config, isolation, workspace
from .accounts import Accounts


def _accounts():
    return Accounts(config.DB_PATH)


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
    # Domov a vault připravíme rovnou, ať první přihlášení nic nezdržuje.
    workspace.ensure(a.get(args.email))
    print(f"Založen účet #{uid}: {args.email} ({args.role})")


def cmd_list(a, args):
    rows = a.list()
    if not rows:
        print("Zatím žádné účty.")
        return
    w = max(len(r["email"]) for r in rows)
    print(f'{"e-mail":<{w}}  role   claude    vault              stav')
    for r in rows:
        stav = "vypnutý" if r["disabled"] else "aktivní"
        auth = r.get("claude_auth") or "central"
        print(f'{r["email"]:<{w}}  {r["role"]:<5}  {auth:<8}  '
              f'{(r["vault"] or "-"):<18} {stav}')


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
    a.set_auth(args.email, args.mode)
    kde = "klíč API brány" if args.mode == "central" else "vlastní účet"
    print(f"{args.email}: Claude se teď ověřuje přes {kde}."
          " (Projeví se při dalším startu jeho prostoru.)")


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


def cmd_apikey(a, args):
    """Klíč API pro prostory na `central`. Leží jen u brány (0600, uživatel
    hub) a do prostoru přijde jako ANTHROPIC_API_KEY při jeho startu."""
    path = config.API_KEY_FILE
    if args.action == "status":
        key = workspace.api_key()
        central = sum(1 for u in a.list()
                      if (u.get("claude_auth") or "central") == "central")
        if not key:
            print("Klíč API není nastavený — prostory na central se musí přihlásit samy.")
        else:
            when = time.strftime("%d. %m. %Y %H:%M",
                                 time.localtime(os.path.getmtime(path)))
            print(f"Klíč API je nastavený (…{key[-4:]}, uložen {when}); "
                  f"používá ho účtů na central: {central}.")
        return
    if args.action == "remove":
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        print("Klíč API smazán. Běžící prostory ho mají do svého dalšího startu.")
        return
    key = (getpass.getpass("Klíč API (sk-ant-…): ") if sys.stdin.isatty()
           else sys.stdin.readline()).strip()
    if not key.startswith("sk-ant-"):
        raise ValueError("Tohle nevypadá jako klíč API Anthropicu (začíná sk-ant-).")
    problem = _check_key(key)
    if problem:
        raise ValueError(problem)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(key + "\n")
    os.replace(tmp, path)
    print(f"Klíč API uložen (…{key[-4:]}). Platí od dalšího startu prostoru; "
          "běžící zastavíš: stop <e-mail>.")


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
    # Prostor smazaného účtu nemá komu běžet — a nikdo by ho už nezastavil.
    isolation.stop_scope(workspace.unit_name(user))
    moved = workspace.retire(user)
    print(f"{args.email} smazán z databáze." +
          (f" Jeho složka je odložená v {moved}." if moved else ""))


def cmd_sessions(a, args):
    """Běžící prostory a kolik drží paměti. Starší bez jména jsou osiřelé."""
    scopes = isolation.running_scopes()
    if not scopes:
        print("Neběží žádný prostor.")
        return
    by_unit = {workspace.unit_name(u): u for u in a.list()}
    for name, _desc in scopes:
        user = by_unit.get(name)
        who = user["email"] if user else "(osiřelý — brána o něm neví)"
        mb = isolation.scope_memory(name) / 1024 / 1024
        print(f"{name:<40} {mb:>7.0f} MB  {who}")


def cmd_stop(a, args):
    if args.orphans:
        known = {workspace.unit_name(u) for u in a.list()}
        names = [n for n, _d in isolation.running_scopes() if n not in known]
    elif not args.email:
        raise ValueError("Zadej e-mail účtu, nebo --orphans.")
    else:
        user = a.get(args.email)
        if not user:
            raise ValueError(f"{args.email} tu žádný účet nemá.")
        names = [workspace.unit_name(user)]
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

    au = sub.add_parser("auth", help="Claude: klíč API brány (central) / vlastní účet (own)")
    au.add_argument("email")
    au.add_argument("mode", choices=("central", "own"))
    au.set_defaults(func=cmd_auth)

    ak = sub.add_parser("apikey", help="klíč API pro prostory na central")
    ak.add_argument("action", choices=("set", "status", "remove"))
    ak.set_defaults(func=cmd_apikey)

    di = sub.add_parser("disable", help="zablokovat účet")
    di.add_argument("email")
    di.set_defaults(func=cmd_disable)

    en = sub.add_parser("enable", help="znovu povolit účet")
    en.add_argument("email")
    en.set_defaults(func=cmd_enable)

    rm = sub.add_parser("remove", help="smazat účet")
    rm.add_argument("email")
    rm.set_defaults(func=cmd_remove)

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
