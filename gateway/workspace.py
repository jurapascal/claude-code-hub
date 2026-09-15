"""
Domov uživatele na bráně — jeho vault, jeho konfigurace hubu, jeho session.

Každý účet dostane vlastní domovskou složku pojmenovanou podle e-mailu
(`/home/hub/users/boucnik.jiri`, do 2.5.3 `u<id>` — viz migrate_home), a v ní:

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
import contextlib
import fcntl
import json
import os
import re
import shutil
import threading
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
    """Jméno složky domova do 2.5.3 (`u7`). Dnes jen pro starý domov, který
    ještě čeká na přejmenování, a pro odkaz na starou cestu v sandboxu."""
    return "u%d" % int(user["id"])


# Znaky, které jdou do jména systemd jednotky tak, jak jsou. Ostatní se píšou
# jako `\xNN` (stejně jako systemd-escape).
_UNIT_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789.-")


def _unit_escape(text):
    out = []
    for ch in text:
        if ch in _UNIT_CHARS:
            out.append(ch)
        else:
            out.extend("\\x%02x" % b for b in ch.encode("utf-8"))
    return "".join(out)


def legacy_unit_name(user):
    """Jméno scope do 2.5.1 (`claude-hub-u7`). Prostor s ním může běžet, dokud
    se brána po aktualizaci nerestartuje, takže ho správa musí dál poznat."""
    return SCOPE_PREFIX + slug(user)


def unit_name(user):
    """Jméno systemd scope, ve které prostor běží — podle e-mailu, ať je ze
    `sessions` hned vidět, čí je (`claude-hub-boucnik.jiri_gmail.com`).

    Zavináč systemd ve jménu nepřijme, proto `_`. Všechno mimo [a-z0-9.-]
    (i `_` z e-mailu) jde jako `\\xNN`, takže dva různé e-maily nikdy nedostanou
    stejné jméno. E-mail je v databázi unikátní a malými písmeny.
    """
    email = (user.get("email") or "").strip().lower()
    local, at, domain = email.rpartition("@")
    name = SCOPE_PREFIX + _unit_escape(local) + "_" + _unit_escape(domain)
    # Bez e-mailu, nebo tak dlouhý, že by přetekl limit systemd (255 i s .scope).
    if not at or not local or not domain or len(name) > 200:
        return legacy_unit_name(user)
    return name


# Kdo má kterou složku domova: {"boucnik.jiri": 1}. Leží vedle domovů, ne
# v nich — do svého domova session zapisuje, a kdyby šlo přiřazení změnit
# odtamtud, dal by si člověk cizí složku.
REGISTRY = os.path.join(USERS_ROOT, ".domovy.json")
_REGISTRY_LOCK = threading.Lock()


@contextlib.contextmanager
def _locked():
    """Registr mění brána (víc vláken) i claude-hub-admin (jiný proces)."""
    with _REGISTRY_LOCK:
        os.makedirs(USERS_ROOT, exist_ok=True)
        with open(REGISTRY + ".lock", "a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)


def _registry():
    try:
        with open(REGISTRY, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, int)}


def _save_registry(reg):
    tmp = REGISTRY + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(reg, fh, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, REGISTRY)


def _registered(reg, user):
    uid = int(user["id"])
    return next((name for name, owner in sorted(reg.items()) if owner == uid), "")


def _home_names(user):
    """Jména složky, o která se účet uchází, v tomhle pořadí: část e-mailu
    před zavináčem, pak s doménou, pak s číslem účtu. `u<číslo>` ne — tak se
    jmenovaly domovy do 2.5.3 a mohl by to být cizí."""
    def clean(text):
        text = re.sub(r"[^a-z0-9._-]", "-", text.lower())
        return re.sub(r"-{2,}", "-", text).strip("._-")[:40]
    local, _at, domain = (user.get("email") or "").strip().rpartition("@")
    base = clean(local)
    names = []
    if base and not re.fullmatch(r"u\d+", base):
        names.append(base)
        if clean(domain):
            names.append(f"{base}_{clean(domain)}")
    names.append(f"{base or 'ucet'}-{int(user['id'])}")
    return names


def _owner_email(path):
    try:
        with open(os.path.join(path, ".claude", "hub-config.json"), encoding="utf-8") as fh:
            owner = json.load(fh).get("gateway_user") or {}
        return str(owner.get("email") or "").strip().lower()
    except (OSError, ValueError, AttributeError):
        return ""


def _pick_home_name(user, reg):
    mine = _registered(reg, user)
    if mine:
        return mine
    email = (user.get("email") or "").strip().lower()
    for name in _home_names(user):
        if name in reg:
            continue
        path = os.path.join(USERS_ROOT, name)
        if not os.path.lexists(path):
            return name
        # Složka bez záznamu (registr se ztratil): vezme si ji jen ten, komu
        # podle hub-config.json patří. Kdo by si tam cizí e-mail zapsal sám,
        # nic nezíská — přišel by jen o vlastní domov.
        if os.path.isdir(path) and email and _owner_email(path) == email:
            return name
    return slug(user)


def home_for(user):
    """Domov účtu: složka podle e-mailu (`/home/hub/users/boucnik.jiri`).

    Dokud se starý `u<id>` nepřejmenoval — prostor od aktualizace ještě
    neběžel, přejmenovává ho až migrate_home před startem — je to on.
    """
    reg = _registry()
    name = _registered(reg, user)
    if name and os.path.isdir(os.path.join(USERS_ROOT, name)):
        return os.path.join(USERS_ROOT, name)
    legacy = os.path.join(USERS_ROOT, slug(user))
    if os.path.isdir(legacy) or not name:
        return legacy
    return os.path.join(USERS_ROOT, name)


def migrate_home(user):
    """Přidělí domovu jméno podle e-mailu a starý `u<id>` na něj přejmenuje.
    Vrací cestu domova.

    Volá se jen před startem prostoru, kdy prokazatelně neběží: přejmenovat
    složku, ze které zrovna jede Claude Code, by mu vzalo půdu pod nohama.
    Jméno se do registru zapíše dřív, než se složka přejmenuje — kdyby se to
    přerušilo mezi tím, další start pod stejným jménem dokončí, co zbylo.
    """
    uid = int(user["id"])
    with _locked():
        reg = _registry()
        name = _pick_home_name(user, reg)
        if reg.get(name) != uid:
            reg = {k: v for k, v in reg.items() if v != uid}
            reg[name] = uid
            _save_registry(reg)
        target = os.path.join(USERS_ROOT, name)
        legacy = os.path.join(USERS_ROOT, slug(user))
        if target != legacy and os.path.isdir(legacy) and not os.path.lexists(target):
            os.rename(legacy, target)
            rewrite_paths(target, legacy, target)
        return target


# Kam se při přejmenování domova nesahá: cache mají tisíce souborů a cesty
# v nich (skripty uv, npx) dál fungují přes odkaz, který na starém místě
# vyrobí sandbox (session_spec, aliases).
_SKIP_DIRS = {".npm", ".cache", "node_modules", ".git"}
_MAX_REWRITE = 64 * 1024 * 1024


def claude_slug(path):
    """Jak Claude Code pojmenuje složku projektu: cokoli mimo písmena a číslice
    je pomlčka (`/home/hub/users/boucnik.jiri` → `-home-hub-users-boucnik-jiri`)."""
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def _retarget(link, old, new):
    target = os.readlink(link)
    if target == old or target.startswith(old + "/"):
        os.remove(link)
        os.symlink(new + target[len(old):], link)
        return True
    return False


def rewrite_paths(home, old, new):
    """Po přejmenování domova přepíše starou cestu na novou: v textových
    souborech (nastavení, skilly, přepisy konverzací), v odkazech a ve jménech
    složek projektů Claude Code. Vrací počet změněných souborů."""
    path_re = re.compile(re.escape(old.encode()) + rb"(?![A-Za-z0-9._-])")
    old_slug, new_slug = claude_slug(old), claude_slug(new)
    slug_re = re.compile(re.escape(old_slug.encode()) + rb"(?![A-Za-z0-9])")
    changed = 0
    for root, dirs, files in os.walk(home):
        keep = []
        for d in dirs:
            full = os.path.join(root, d)
            if os.path.islink(full):
                changed += _retarget(full, old, new)
            elif d not in _SKIP_DIRS:
                keep.append(d)
        dirs[:] = keep
        for name in files:
            full = os.path.join(root, name)
            if os.path.islink(full):
                changed += _retarget(full, old, new)
                continue
            try:
                if os.path.getsize(full) > _MAX_REWRITE:
                    continue
                with open(full, "rb") as fh:
                    data = fh.read()
            except OSError:
                continue
            if b"\0" in data[:8192]:
                continue                  # binární soubor
            fixed = slug_re.sub(new_slug.encode(), path_re.sub(new.encode(), data))
            if fixed == data:
                continue
            tmp = full + ".hub-tmp"
            try:
                with open(tmp, "wb") as fh:
                    fh.write(fixed)
                shutil.copymode(full, tmp)
                os.replace(tmp, full)
                changed += 1
            except OSError:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
    # Složky projektů Claude Code nesou cestu ve jméně — bez přejmenování by
    # přišel o historii konverzací i paměť projektu.
    projects = os.path.join(home, ".claude", "projects")
    try:
        entries = os.listdir(projects)
    except OSError:
        entries = []
    for entry in entries:
        if entry == old_slug or entry.startswith(old_slug + "-"):
            dst = os.path.join(projects, new_slug + entry[len(old_slug):])
            if not os.path.lexists(dst):
                os.rename(os.path.join(projects, entry), dst)
    return changed


def retire(user):
    """Odloží domov smazaného účtu stranou. Vrací novou cestu, nebo ''.

    Nestačí ho nechat ležet: nový účet se stejným e-mailem (nebo po smazání
    posledního účtu i se stejným id) by dostal stejnou složku a zdědil paměť,
    projekty i napojení toho předchozího. Data se nemažou, jen přestanou být
    na cestě, kterou může dostat někdo jiný — a jméno se v registru uvolní.
    """
    uid = int(user["id"])
    with _locked():
        home = home_for(user)
        reg = _registry()
        if uid in reg.values():
            _save_registry({k: v for k, v in reg.items() if v != uid})
        if not os.path.isdir(home):
            return ""
        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = os.path.join(USERS_ROOT, f"_smazany-{os.path.basename(home)}-{stamp}")
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
    # Zbytek dřívějšího přihlášení vlastním účtem: token je pryč, ale údaje
    # o účtu zůstaly. Claude Code by podle nich ukazoval cizí e-mail a modelu
    # ho podával jako uživatelův (Claude s ním pak zkoušel i Google).
    stale = "oauthAccount" in data and not os.path.exists(
        os.path.join(home, ".claude", ".credentials.json"))
    if tail in approved and tail not in rejected and not stale:
        return
    if stale:
        data.pop("oauthAccount", None)
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
        # Společný firemní Obsidian (jen ke čtení) — hub ho ukáže v panelu.
        "company_vault": config.COMPANY_VAULT,
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

    # Firemní Obsidian: trezor, pokyny pro Clauda a přístup ke čtení. Bez něj
    # prostor nastartuje taky — jen o firemním nebude vědět.
    try:
        ensure_company_vault()
        _company_claude_md(claude)
        _company_settings(claude)
    except OSError:
        pass

    return {"home": home, "vault": vault, "claude": claude,
            "projects": projects}


# ── firemní Obsidian ─────────────────────────────────────────────────────────
# Jeden trezor pro všechny. V prostorech je svázaný jen ke čtení, takže Claude
# do něj sám nezapíše. Poznámku připraví nástrojem tools/firma.py do
# ~/.firma/ke-schvaleni/, hub ji ukáže s tlačítkem Nahrát a zapíše až brána
# (publish_company) — na požadavek z prohlížeče s přihlašovací cookie, kterou
# session nemá.
PENDING = os.path.join(".firma", "ke-schvaleni")
PENDING_ID = re.compile(r"\d{8}-\d{6}-[0-9a-f]{6}")
MAX_PROPOSAL = 1024 * 1024
FIRMA_MARK = ("<!-- claude-hub:firma -->", "<!-- /claude-hub:firma -->")

COMPANY_README = """# Firemní Obsidian

Společné know-how celého týmu. Každý má vedle toho svůj osobní trezor.

Nahrává se přes Claude Code: řekni mu „nahraj to do firemního", hub ukáže
kartu s náhledem a poznámka se uloží, až ji potvrdíš tlačítkem **Nahrát**.
"""


def ensure_company_vault():
    """Založí firemní trezor, když ještě není. Vrací jeho cestu."""
    vault = config.COMPANY_VAULT
    os.makedirs(vault, exist_ok=True)
    readme = os.path.join(vault, "README.md")
    if not os.path.exists(readme) and not [n for n in os.listdir(vault)
                                           if not n.startswith(".")]:
        with open(readme, "w", encoding="utf-8") as fh:
            fh.write(COMPANY_README)
    return vault


def _company_block():
    tool = os.path.join(REPO_DIR, "tools", "firma.py")
    return f"""{FIRMA_MARK[0]}
## Firemní Obsidian

Vedle osobního trezoru tohohle uživatele je společný **firemní Obsidian**
celého týmu: `{config.COMPANY_VAULT}`. Je jen ke čtení — firemní postupy,
kontakty a know-how hledej a čti tam.

Zapisovat do něj přímo nejde. Když tě uživatel požádá, ať něco nahraješ do
firemního:

1. Připrav poznámku v Markdownu a vyber pro ni cestu podle struktury, která už
   ve firemním trezoru je (třeba `postupy/fakturace.md`).
2. Pošli ji ke schválení:
   `python3 {tool} navrh "postupy/fakturace.md" poznamka.md`
   (místo souboru jde obsah poslat na standardní vstup: `-`).
3. Řekni uživateli, že mu hub ukázal kartu s náhledem: poznámka se nahraje, až
   ji potvrdí tlačítkem **Nahrát**. Sám ji potvrdit nemůžeš.

Existující firemní poznámku upravíš tak, že pošleš celý nový obsah na stejnou
cestu — karta upozorní, že se přepíše. Hesla, klíče a osobní údaje do
firemního nepatří, pokud o to uživatel výslovně nežádá.
{FIRMA_MARK[1]}
"""


def _company_claude_md(claude_dir):
    """Pokyny k firemnímu Obsidianu v ~/.claude/CLAUDE.md prostoru. Mezi
    značkami se vždy přepíšou, zbytek souboru patří uživateli a zůstane."""
    path = os.path.join(claude_dir, "CLAUDE.md")
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        text = ""
    except (OSError, ValueError):
        return
    block = _company_block()
    start, end = text.find(FIRMA_MARK[0]), text.find(FIRMA_MARK[1])
    if start >= 0 and end > start:
        new = text[:start] + block.rstrip("\n") + text[end + len(FIRMA_MARK[1]):]
    else:
        new = (text.rstrip("\n") + "\n\n" if text.strip() else "") + block
    if new != text:
        tmp = path + ".hub-tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(new)
        os.replace(tmp, path)


def _company_settings(claude_dir):
    """Firemní trezor mezi složkami, které Claude Code smí číst bez ptaní
    (permissions.additionalDirectories). Nečitelné nastavení se nepřepisuje."""
    path = os.path.join(claude_dir, "settings.json")
    try:
        with open(path, encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        data = {}
    except (OSError, ValueError):
        return
    if not isinstance(data, dict):
        return
    perms = data.get("permissions", {})
    if not isinstance(perms, dict):
        return
    dirs = perms.get("additionalDirectories", [])
    if not isinstance(dirs, list) or config.COMPANY_VAULT in dirs:
        return
    perms["additionalDirectories"] = dirs + [config.COMPANY_VAULT]
    data["permissions"] = perms
    tmp = path + ".hub-tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def company_rel(rel):
    """Cesta ve firemním trezoru: relativní, .md, bez `..` a skrytých částí.
    Absolutní cesta se odmítne — tiše ji brát jako relativní by jen mátlo."""
    raw = str(rel or "").replace("\\", "/").strip()
    rel = raw.strip("/")
    if rel and not rel.lower().endswith(".md"):
        rel += ".md"
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if (not parts or len(rel) > 300 or raw.startswith("/") or re.match(r"^[A-Za-z]:", rel)
            or any(p == ".." or p.startswith(".") for p in parts)):
        raise ValueError(f"Neplatná cesta ve firemním Obsidianu: {rel or '(prázdná)'}")
    return "/".join(parts)


def publish_company(user, pid, overwrite=False):
    """Nahraje návrh uživatele do firemního trezoru. Volá ji brána po kliknutí
    na Nahrát. Vrací {"ok", "path", ...}; když poznámka už existuje a
    `overwrite` není, nic nezapíše a vrátí `exists`.

    Návrh leží v domově uživatele, kam session zapisuje — může to být
    podvržený odkaz kamkoli na serveru. Čte se proto jen obyčejný soubor přímo
    na své cestě, bez následování odkazů.
    """
    pid = str(pid or "")
    if not PENDING_ID.fullmatch(pid):
        raise ValueError("Neplatný návrh.")
    home = os.path.realpath(home_for(user))
    src = os.path.join(home, PENDING, pid + ".json")
    gone = ValueError("Návrh už není — nejspíš byl nahraný nebo zahozený.")
    if os.path.realpath(src) != src:
        raise gone
    try:
        fd = os.open(src, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        raise gone from None
    with os.fdopen(fd, "rb") as fh:
        raw = fh.read(MAX_PROPOSAL + 1)
    if len(raw) > MAX_PROPOSAL:
        raise ValueError("Návrh je moc velký.")
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError:
        raise ValueError("Návrh je poškozený.") from None
    if not isinstance(data, dict) or not isinstance(data.get("text"), str):
        raise ValueError("Návrh je poškozený.")
    rel = company_rel(data.get("cil"))
    vault = os.path.realpath(ensure_company_vault())
    target = os.path.join(vault, *rel.split("/"))
    if os.path.commonpath([os.path.realpath(os.path.dirname(target)), vault]) != vault:
        raise ValueError("Neplatná cesta ve firemním Obsidianu.")
    existed = os.path.exists(target)
    if existed and not overwrite:
        return {"ok": False, "exists": True, "path": rel}
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = target + ".hub-tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(data["text"])
    os.replace(tmp, target)
    entry = {"cas": time.strftime("%Y-%m-%d %H:%M:%S"), "email": user.get("email", ""),
             "cil": rel, "prepsano": existed, "znaku": len(data["text"])}
    try:
        with open(config.COMPANY_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass
    try:
        os.remove(src)
    except OSError:
        pass
    return {"ok": True, "path": rel, "overwritten": existed}


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
    # Firemní Obsidian taky jen ke čtení: zapisuje do něj brána po potvrzení.
    extra_ro = [REPO_DIR, config.COMPANY_VAULT]
    unit = unit_name(user)
    # Stará cesta u<id> vede v sandboxu na nový domov: cache (uv, npx) mají
    # absolutní cesty zapečené uvnitř a přepisovat je by bylo křehké.
    legacy = os.path.join(USERS_ROOT, slug(user))
    argv = isolation.wrap(mode, inner, home, extra_ro=extra_ro, unit=unit,
                          aliases=[legacy] if legacy != home else [])
    if argv[:1] != ["systemd-run"]:
        unit = ""                 # bez scope (docker, stroj bez systemd)
    return argv, home, unit
