"""
Domov uživatele na bráně — jeho vault, jeho konfigurace hubu, jeho session.

Každý účet dostane vlastní domovskou složku (`/home/hub/users/u<id>`), a v ní:

* **Obsidian vault** s jeho pamětí. Je jen jeho: izolace (`isolation.py`) pustí
  session tak, že mimo tenhle domov nevidí — takže paměť jednoho člověka se
  k druhému nedostane, i když oba běží na jednom stroji.
* **`.claude/hub-config.json`**, aby se jeho instance hubu nastavila sama:
  paměť míří do jeho vaultu, projekty do jeho složky. Hub žádné okno neotvírá,
  brána ho pustí s `--no-browser` a mluví s ním přes proxy.
* **přihlášení Claude Code**. Účet Anthropicu je **jeden, sdílený** (viz
  README brány) — všechny session ho čtou ze společného souboru. Sdílí se jen
  ten soubor s tokenem, ne zbytek konfigurace: projekty, historii a MCP má
  každý svoje.

Cesty jdou přebít proměnnými prostředí, aby šel modul otestovat i mimo server.
"""
import json
import os
import re

HUB_HOME = os.environ.get("HUB_GW_HOME", "/home/hub")
USERS_ROOT = os.environ.get("HUB_GW_USERS", os.path.join(HUB_HOME, "users"))
# Zdroj hubu, který se pouští každému uživateli. Do sandboxu se přiváže jen ke
# čtení — session do něj nesmí zapisovat.
REPO_DIR = os.environ.get("HUB_GW_REPO",
                          os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Sdílené přihlášení Claude Code: adresář `.claude` účtu, pod kterým brána běží.
# Do session se přiváže **jen soubor s tokenem**, a to pro čtení i zápis, aby
# obnovený token platil pro všechny.
SHARED_CLAUDE = os.environ.get("HUB_GW_CLAUDE", os.path.join(HUB_HOME, ".claude"))
SHARED_CRED = os.path.join(SHARED_CLAUDE, ".credentials.json")
PYTHON = os.environ.get("HUB_GW_PYTHON", "python3")

EMPTY_MEMORY = """\
## 🗺 Rozcestníky

## Projekty a reference

## Poznatky (learnings)

## Chyby (errors)

## Úspěchy (wins)
"""


def slug(user):
    """Bezpečné jméno složky. Vychází z id, ne z e-mailu — id se nemění a
    nemá znaky, které by v cestě vadily."""
    return "u%d" % int(user["id"])


def home_for(user):
    return os.path.join(USERS_ROOT, slug(user))


def vault_name(user):
    """Jak se jmenuje jeho Obsidian paměť. Když si nic nezvolil, odvodí se
    z e-mailu (část před zavináčem), ať to není holé `u7`."""
    name = (user.get("vault") or "").strip()
    if not name:
        name = (user.get("email") or "vault").split("@")[0]
    # Do jména složky pustíme jen to, co v cestě nezlobí.
    name = re.sub(r"[^\w.\- ]", "", name).strip() or "Brain"
    return name


def vault_dir(user, home=None):
    home = home or home_for(user)
    return os.path.join(home, "Obsidian", vault_name(user))


def claude_auth(user):
    """`central` (sdílené předplatné brány), nebo `own` (vlastní v jeho domově)."""
    return "own" if (user.get("claude_auth") or "central") == "own" else "central"


def _hub_config(user, home):
    vault = vault_dir(user, home)
    return {
        # Paměť míří do jeho vaultu; projekty do jeho složky.
        "brain_dir": vault,
        "project_dirs": [os.path.join(home, "projects")],
        # Hub běží na serveru bez okna — žádný prohlížeč neotevírá.
        "browser": "",
        "bash": "/bin/bash",
        # Onboarding už proběhl (nastavila ho brána), ať uživatele nevítá
        # průvodce prvním spuštěním.
        "onboarded": True,
        # Kdo to je — hub si to jinam nepíše, hodí se pro hlavičku.
        "gateway_user": {"email": user.get("email", ""),
                         "name": user.get("name", ""),
                         "role": user.get("role", "user")},
    }


def ensure(user):
    """Založí (nebo doplní) domov uživatele. Vrací slovník s cestami.

    Voláno při každém přihlášení — je to idempotentní: co existuje, nechá být.
    """
    home = home_for(user)
    vault = vault_dir(user, home)
    claude = os.path.join(home, ".claude")
    projects = os.path.join(home, "projects")

    for d in (home, os.path.join(vault, "memory"), os.path.join(vault, "skills"),
              os.path.join(vault, ".obsidian"), claude, projects):
        os.makedirs(d, exist_ok=True)

    index = os.path.join(vault, "memory", "MEMORY.md")
    if not os.path.isfile(index):
        with open(index, "w", encoding="utf-8") as fh:
            fh.write(EMPTY_MEMORY)

    # hub-config.json přepisujeme vždy: e-mail/jméno/vault se mohly změnit ve
    # správě účtů a hub si je čte odsud. Ostatní stav (projekty, MCP) si hub
    # drží jinde, tohle mu jen řekne, kde má paměť a projekty.
    with open(os.path.join(claude, "hub-config.json"), "w", encoding="utf-8") as fh:
        json.dump(_hub_config(user, home), fh, ensure_ascii=False, indent=2)

    # Přihlášení Claude Code závisí na volbě prostoru:
    #  * central — symlink na SDÍLENÝ token brány (přiváže se ke zápisu
    #    v session_spec). Prostor jede na centrálním předplatném.
    #  * own — žádný odkaz na sdílené; domov `.claude` je čistě uživatelův,
    #    přihlásí se vlastním účtem / API klíčem. Když se přepíná z central,
    #    starý symlink na sdílené se odstraní, ať se nemíchají.
    cred_link = os.path.join(claude, ".credentials.json")
    if claude_auth(user) == "central":
        try:
            if os.path.islink(cred_link) or os.path.exists(cred_link):
                if os.path.realpath(cred_link) != os.path.realpath(SHARED_CRED):
                    os.remove(cred_link)
                    os.symlink(SHARED_CRED, cred_link)
            else:
                os.symlink(SHARED_CRED, cred_link)
        except OSError:
            pass
    else:  # own: nechat jen uživatelovo, sdílený symlink pryč
        try:
            if os.path.islink(cred_link) and \
                    os.path.realpath(cred_link) == os.path.realpath(SHARED_CRED):
                os.remove(cred_link)
        except OSError:
            pass

    return {"home": home, "vault": vault, "claude": claude,
            "projects": projects}


def session_spec(user, isolation, mode):
    """Co předat pty backendu, aby se spustila izolovaná instance hubu.

    Vrací (argv, home). `argv` je už obalené izolací a limity; `home` je
    pracovní složka i jediné zapisovatelné místo session.
    """
    paths = ensure(user)
    home = paths["home"]
    inner = [PYTHON, os.path.join(REPO_DIR, "claude-hub.py"), "--no-browser"]
    # REPO_DIR ke čtení (zdroj hubu). Sdílený token se do sandboxu přiváže
    # (ke čtení i zápisu — obnova) JEN u prostorů na centrálním předplatném;
    # `own` prostor sdílené přihlášení vůbec nevidí.
    extra_ro = [REPO_DIR]
    extra_rw = []
    if claude_auth(user) == "central" and os.path.exists(SHARED_CRED):
        extra_rw = [SHARED_CRED]
    argv = isolation.wrap(mode, inner, home, extra_ro=extra_ro,
                          extra_rw=extra_rw)
    return argv, home
