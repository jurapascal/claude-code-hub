"""
Domov uživatele na bráně — jeho vault, jeho konfigurace hubu, jeho session.

Každý účet dostane vlastní domovskou složku (`/home/hub/users/u<id>`), a v ní:

* **Obsidian vault** s jeho pamětí. Je jen jeho: izolace (`isolation.py`) pustí
  session tak, že mimo tenhle domov nevidí — takže paměť jednoho člověka se
  k druhému nedostane, i když oba běží na jednom stroji.
* **`.claude/hub-config.json`**, aby se jeho instance hubu nastavila sama:
  paměť míří do jeho vaultu, projekty do jeho složky. Hub žádné okno neotvírá,
  brána ho pustí s `--no-browser` a mluví s ním přes proxy.
* **přihlášení Claude Code**. Na `central` (výchozí) dostane prostor klíč API
  brány (`ANTHROPIC_API_KEY`, viz session_env) a nikdo se nepřihlašuje; platí
  se podle spotřeby. Na `own` se člověk přihlásí vlastním účtem. Sdílet jedno
  osobní přihlášení mezi víc lidí (dřívější `central`) je proti podmínkám
  Anthropicu, proto to brána už nedělá.

Cesty jdou přebít proměnnými prostředí, aby šel modul otestovat i mimo server.
"""
import json
import os
import re
import time

from . import config
from .isolation import SCOPE_PREFIX

HUB_HOME = os.environ.get("HUB_GW_HOME", "/home/hub")
USERS_ROOT = os.environ.get("HUB_GW_USERS", os.path.join(HUB_HOME, "users"))
# Zdroj hubu, který se pouští každému uživateli. Do sandboxu se přiváže jen ke
# čtení — session do něj nesmí zapisovat.
REPO_DIR = os.environ.get("HUB_GW_REPO",
                          os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Sdílené přihlášení Claude Code z dob dřívějšího `central`. Už se nepoužívá —
# ensure() jen uklízí symlinky, které na něj v domovech zůstaly.
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


def unit_name(user):
    """Jméno systemd scope, ve které prostor běží (`claude-hub-u7`)."""
    return SCOPE_PREFIX + slug(user)


def home_for(user):
    return os.path.join(USERS_ROOT, slug(user))


def retire(user):
    """Odloží domov smazaného účtu stranou. Vrací novou cestu, nebo ''.

    Nestačí ho nechat ležet: SQLite po smazání posledního účtu dá dalšímu
    stejné id, tedy i stejnou složku `u<id>` — a nový člověk by zdědil paměť,
    projekty i přihlášení Claude Code toho předchozího. Data se nemažou,
    jen přestanou být na cestě, kterou může dostat někdo jiný.
    """
    home = home_for(user)
    if not os.path.isdir(home):
        return ""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = os.path.join(USERS_ROOT, f"_smazany-{slug(user)}-{stamp}")
    os.rename(home, target)
    return target


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
    """`central` (klíč API brány), nebo `own` (vlastní přihlášení v jeho domově)."""
    return "own" if (user.get("claude_auth") or "central") == "own" else "central"


def api_key():
    """Klíč API brány, nebo ''. Čte se při každém startu prostoru, takže
    `claude-hub-admin apikey set` platí od dalšího startu bez restartu brány."""
    try:
        with open(config.API_KEY_FILE, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def google_client():
    """(client_id, client_secret) klienta OAuth pro Google, nebo ('', '')."""
    try:
        with open(config.GOOGLE_CLIENT_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        return ((data.get("client_id") or "").strip(),
                (data.get("client_secret") or "").strip())
    except (OSError, ValueError, AttributeError):
        return "", ""


def session_env(user):
    """Proměnné prostředí navíc pro prostor: klíč API (jen `central`) a klient
    OAuth pro napojení na Google (všem — účty si každý přidává sám)."""
    env = {}
    key = api_key() if claude_auth(user) == "central" else ""
    if key:
        env["ANTHROPIC_API_KEY"] = key
    cid, secret = google_client()
    if cid and secret:
        env["GOOGLE_OAUTH_CLIENT_ID"] = cid
        env["GOOGLE_OAUTH_CLIENT_SECRET"] = secret
    return env


def _approve_api_key(home, key):
    """Předschválí klíč v ~/.claude.json prostoru.

    Claude Code se na klíč z prostředí napoprvé ptá („Do you want to use this
    API key?") a bez odpovědi nepustí dál. Zapíše se to stejně, jak by to udělal
    on sám: posledních 20 znaků klíče v customApiKeyResponses.approved. Soubor
    se přepisuje atomicky a jen tehdy, když jde přečíst — rozbitý nechat být je
    lepší než uživateli smazat nastavení. Volá se před startem hubu, kdy Claude
    Code v prostoru neběží a do souboru nepíše."""
    path = os.path.join(home, ".claude.json")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        data = {}
    except (OSError, ValueError):
        return
    if not isinstance(data, dict):
        return
    tail = key.strip()[-20:]
    resp = data.get("customApiKeyResponses")
    resp = resp if isinstance(resp, dict) else {}
    approved = [k for k in (resp.get("approved") or []) if isinstance(k, str)]
    rejected = [k for k in (resp.get("rejected") or []) if isinstance(k, str)]
    if tail in approved and tail not in rejected:
        return
    data["customApiKeyResponses"] = {
        **resp,
        "approved": approved if tail in approved else approved + [tail],
        "rejected": [k for k in rejected if k != tail],
    }
    tmp = path + ".hub-tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


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

    # Přihlášení Claude Code (claude_auth):
    #  * central — klíč API brány přijde do prostředí (session_env); tady se
    #    jen předschválí, ať se Claude Code neptá.
    #  * own — člověk se přihlásí sám, nic se nepřidává.
    # Symlink na sdílené přihlášení z dřívějšího central se ruší v obou
    # případech: jedno osobní předplatné pro víc lidí je proti podmínkám.
    cred_link = os.path.join(claude, ".credentials.json")
    try:
        if os.path.islink(cred_link) and \
                os.path.realpath(cred_link) == os.path.realpath(SHARED_CRED):
            os.remove(cred_link)
    except OSError:
        pass
    key = api_key() if claude_auth(user) == "central" else ""
    if key:
        _approve_api_key(home, key)

    return {"home": home, "vault": vault, "claude": claude,
            "projects": projects}


def session_spec(user, isolation, mode):
    """Co předat pty backendu, aby se spustila izolovaná instance hubu.

    Vrací (argv, home, unit). `argv` je už obalené izolací a limity; `home`
    je pracovní složka i jediné zapisovatelné místo session; `unit` je jméno
    systemd scope, podle kterého se prostor pozná a zastaví (prázdné, když
    na stroji systemd-run není a limity se nepoužijí).
    """
    paths = ensure(user)
    home = paths["home"]
    inner = [PYTHON, os.path.join(REPO_DIR, "claude-hub.py"), "--no-browser"]
    # REPO_DIR ke čtení (zdroj hubu). Nic víc: klíč API přijde v prostředí
    # (session_env), žádný soubor mimo domov se do sandboxu nepřivazuje.
    extra_ro = [REPO_DIR]
    unit = unit_name(user)
    argv = isolation.wrap(mode, inner, home, extra_ro=extra_ro, unit=unit)
    if argv[:1] != ["systemd-run"]:
        unit = ""                 # bez scope (docker, stroj bez systemd)
    return argv, home, unit
