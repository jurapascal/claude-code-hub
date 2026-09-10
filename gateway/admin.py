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
"""
import argparse
import getpass
import sys
import time

from . import config, workspace
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
    kde = "centrální předplatné" if args.mode == "central" else "vlastní účet"
    print(f"{args.email}: Claude se teď ověřuje přes {kde}."
          " (Projeví se při dalším startu jeho prostoru.)")


def cmd_disable(a, args):
    a.set_disabled(args.email, True)
    print(f"{args.email} zablokován (a odhlášen).")


def cmd_enable(a, args):
    a.set_disabled(args.email, False)
    print(f"{args.email} znovu povolen.")


def cmd_remove(a, args):
    a.remove(args.email)
    print(f"{args.email} smazán z databáze. (Jeho složka na disku zůstává.)")


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

    au = sub.add_parser("auth", help="Claude: centrální předplatné / vlastní")
    au.add_argument("email")
    au.add_argument("mode", choices=("central", "own"))
    au.set_defaults(func=cmd_auth)

    di = sub.add_parser("disable", help="zablokovat účet")
    di.add_argument("email")
    di.set_defaults(func=cmd_disable)

    en = sub.add_parser("enable", help="znovu povolit účet")
    en.add_argument("email")
    en.set_defaults(func=cmd_enable)

    rm = sub.add_parser("remove", help="smazat účet")
    rm.add_argument("email")
    rm.set_defaults(func=cmd_remove)
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
