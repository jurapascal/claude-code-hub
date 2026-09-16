#!/usr/bin/env python3
"""Spouštěč prostoru pod vlastním systémovým účtem (běží jako root přes sudo).

Bez tohohle běží všechny prostory pod jedním účtem `hub` a od sebe je dělí jen
bwrap. Tenhle skript z toho udělá dvě nezávislé hranice: prostor běží jako
`hub-u<id>`, takže na cizí domov nedosáhne, ani kdyby ze sandboxu vyklouzl.

Brána (účet `hub`) nemá práva přepnout se na jiného uživatele, a dát jí je
obecně by znamenalo root pro kohokoli, kdo ji ovládne. Proto tenhle úzký
spouštěč: v sudoers je povolený jen on, dostane účet, jméno scope a hotové
argv — a **všechno si ověří**, než to pustí.

    claude-hub-prostor spustit u7 claude-hub-anna -- bwrap … python3 …
    claude-hub-prostor zastavit claude-hub-anna

Práva domova: vlastník `hub-u<id>`, skupina `hub`, `2770`.
  * prostor je vlastník      → píše si k sobě
  * brána je ve skupině hub  → zakládá domov, sype tam firemní vault
  * cizí prostor             → není ani jedno → nedosáhne

Účty prostorů proto do skupiny `hub` NIKDY nepatří — tím by se navzájem viděly.
"""
import os
import pwd
import re
import shutil
import subprocess
import sys

USERS_ROOT = "/home/hub/users"
REPO_DIR = "/opt/claude-code-hub"
HUB_USER = "hub"
PREFIX = "hub-"
# Co smí prostor doopravdy spustit. Nic jiného přes tenhle skript neprojde —
# jinak by „sudo spouštěč" bylo jen obejití sudoers.
INNER = ["python3", os.path.join(REPO_DIR, "claude-hub.py"), "--no-browser"]

UCET_RE = re.compile(r"^u[0-9]+$")
# systemd-escape píše nepovolené znaky jako \xNN, proto i zpětné lomítko.
UNIT_RE = re.compile(r"^claude-hub-[a-z0-9.\\x_-]+$")

LIMITY = {"MemoryMax": "1500M", "MemorySwapMax": "0", "TasksMax": "256",
          "CPUWeight": "100"}


def konec(zprava):
    print(f"claude-hub-prostor: {zprava}", file=sys.stderr)
    raise SystemExit(2)


def overeny_domov(argv):
    """Domov z `--bind <cesta> <cesta>` v argv. Musí ležet v USERS_ROOT."""
    for i, a in enumerate(argv):
        if a == "--bind" and i + 2 < len(argv) and argv[i + 1] == argv[i + 2]:
            cesta = os.path.realpath(argv[i + 1])
            koren = os.path.realpath(USERS_ROOT)
            if os.path.dirname(cesta) == koren and os.path.isdir(cesta):
                return cesta
    return ""


def zkontroluj_argv(argv):
    """Pustí jen bwrap, který končí naším hubem. Vrací domov."""
    if not argv or os.path.basename(argv[0]) != "bwrap":
        konec("spouštět jde jen bwrap")
    if argv[-len(INNER):] != INNER:
        konec("na konci musí být právě " + " ".join(INNER))
    domov = overeny_domov(argv)
    if not domov:
        konec(f"v argv chybí zápisový --bind na domov v {USERS_ROOT}")
    return domov


def zaloz_ucet(jmeno):
    try:
        return pwd.getpwnam(jmeno)
    except KeyError:
        pass
    # --system: bez hesla a bez expirace. Domov nezakládáme, ten už je.
    # Účet NENÍ ve skupině hub — tím by viděl do cizích domovů.
    subprocess.run(["useradd", "--system", "--no-create-home",
                    "--shell", "/usr/sbin/nologin", jmeno],
                   check=True, capture_output=True)
    return pwd.getpwnam(jmeno)


def prochozi_cesta(domov):
    """Nadřazené složky musí jít projít, jinak se účet k domovu nedostane.

    `751` = vlastník všechno, skupina `hub` čte, ostatní **jen projdou**.
    Výpis složek zůstává skrytý, takže cizí prostor se ani nedozví, kdo tu je,
    a do žádného domova stejně nedosáhne (ten má 2770 a jiného vlastníka).
    """
    for cesta in (os.path.dirname(os.path.realpath(USERS_ROOT)),
                  os.path.realpath(USERS_ROOT)):
        try:
            rezim = os.stat(cesta).st_mode & 0o7777
            if not rezim & 0o001:
                os.chmod(cesta, rezim | 0o001)
        except OSError:
            pass


def preved_domov(domov, ucet):
    """Vlastníka a práva domova srovná na (hub-u<id>, hub, 2770).

    Dělá se to při každém startu, ale je to laciné, když už to sedí — a zvládne
    to i přechod ze starého stavu, kdy všechno patřilo účtu `hub`.
    """
    st = os.stat(domov)
    hub_gid = pwd.getpwnam(HUB_USER).pw_gid
    if st.st_uid == ucet.pw_uid and st.st_gid == hub_gid:
        # Vršek sedí → přepis proběhl dřív. Dovnitř nelezeme, jen se ujistíme,
        # že brána má pořád přístup: bez téhle kontroly zůstal domov převedený
        # starší verzí bez ACL a brána do něj nezapsala (naměřeno u druhého
        # účtu, kde převod proběhl dřív, než se ACL vůbec nastavovalo).
        if (st.st_mode & 0o7777) != 0o2770:
            os.chmod(domov, 0o2770)
        if not ma_pristup(domov):
            pristup_brany(domov)
        return False
    subprocess.run(["chown", "-R", f"{ucet.pw_name}:{HUB_USER}", domov],
                   check=True, capture_output=True)
    os.chmod(domov, 0o2770)
    pristup_brany(domov)
    return True


def ma_pristup(domov):
    """Má brána na domově ACL? Levná kontrola jen na vršku, jednou za start."""
    if not shutil.which("getfacl"):
        return True                 # bez nástrojů to stejně neověříme
    res = subprocess.run(["getfacl", "-c", "-p", domov],
                         capture_output=True, text=True)
    return f"group:{HUB_USER}:rwx" in (res.stdout or "")


def pristup_brany(domov):
    """Brána musí do domova dosáhnout, i když v něm soubory zakládá prostor.

    Samotné `chown` nestačí: podsložky měly 700/755, takže skupina `hub` do nich
    nezapíše (naměřeno — brána padala na `.claude/hub-config.json`). A `chmod -R
    g+rwX` by spravil jen to, co tam je teď; co si prostor vyrobí potom, vznikne
    podle jeho umask a brána by o přístup zase přišla.

    Proto ACL: `-d` (default) platí i pro soubory, které teprve vzniknou.
    Když `setfacl` na stroji není, zbývá aspoň chmod na to, co existuje.
    """
    if shutil.which("setfacl"):
        for extra in ([], ["-d"]):
            subprocess.run(["setfacl", "-R", *extra, "-m", f"g:{HUB_USER}:rwX", domov],
                           capture_output=True)
    else:
        subprocess.run(["chmod", "-R", "g+rwX", domov], capture_output=True)


def spustit(args):
    if len(args) < 3 or args[2] != "--":
        konec("použití: spustit <u<id>> <jednotka> -- <argv…>")
    ucet_slug, jednotka, argv = args[0], args[1], args[3:]
    if not UCET_RE.match(ucet_slug):
        konec(f"účet {ucet_slug!r} nemá tvar u<číslo>")
    if not UNIT_RE.match(jednotka):
        konec(f"jméno jednotky {jednotka!r} neodpovídá vzoru")
    domov = zkontroluj_argv(argv)

    ucet = zaloz_ucet(PREFIX + ucet_slug)
    prochozi_cesta(domov)
    preved_domov(domov, ucet)

    cmd = ["systemd-run", "--scope", "--quiet", "--collect",
           f"--unit={jednotka}",
           f"--uid={ucet.pw_name}", f"--gid={ucet.pw_name}"]
    for k, v in LIMITY.items():
        cmd += ["-p", f"{k}={v}"]
    cmd += ["--"] + argv
    os.execvp(cmd[0], cmd)


def zastavit(args):
    if len(args) != 1:
        konec("použití: zastavit <jednotka>")
    jednotka = args[0]
    if not UNIT_RE.match(jednotka):
        konec(f"jméno jednotky {jednotka!r} neodpovídá vzoru")
    subprocess.run(["systemctl", "stop", jednotka + ".scope"],
                   capture_output=True)
    raise SystemExit(0)


def main():
    if os.geteuid() != 0:
        konec("spouští se jako root (přes sudo z účtu hub)")
    if len(sys.argv) < 2:
        konec("použití: spustit … | zastavit …")
    prikaz, args = sys.argv[1], sys.argv[2:]
    if prikaz == "spustit":
        spustit(args)
    elif prikaz == "zastavit":
        zastavit(args)
    else:
        konec(f"neznámý příkaz {prikaz!r}")


if __name__ == "__main__":
    main()
