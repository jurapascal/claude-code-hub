"""
Platform-agnostic core of the hub: config, project/memory scanning, palettes.

Everything that differs between Linux, macOS and Windows is isolated here so
the server and the UI never have to know which OS they are running on.

The Windows story in one line: we run the same bash scripts everywhere by
going through Git for Windows' bash.exe, so `claude-wrapper.sh` and every
bash-based slash command work unchanged on all three platforms.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time

IS_WINDOWS = os.name == "nt"
IS_MAC = sys.platform == "darwin"

HOME = os.path.expanduser("~")
CLAUDE_DIR = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(HOME, ".claude")
CONFIG_PATH = os.path.join(CLAUDE_DIR, "hub-config.json")

# Everything machine-specific lives in hub-config.json — see hub-config.example.json.
# Missing file or missing keys → these defaults, all of them optional at runtime.
DEFAULTS = {
    "project_dirs": ["~/Desktop", "~/Projects", "~/dev", "/opt/lampp/htdocs"],
    "brain_dir": "~/Obsidian/Claude-Brain",
    "icon": "~/.local/share/icons/claude-code.png",
    "ftp_deploy_script": "~/.claude/ftp-deploy.sh",
    "bash": "",     # Windows: path to Git for Windows bash.exe; empty = autodetect
    "browser": "",  # empty = autodetect an app-window capable browser
}


def load_config():
    cfg = dict(DEFAULTS)
    try:
        # utf-8-sig: PowerShell 5.1 rád píše BOM a json.load by na něm spadl
        with open(CONFIG_PATH, encoding="utf-8-sig") as fh:
            cfg.update({k: v for k, v in json.load(fh).items() if v})
    except Exception:
        pass  # no config yet → defaults; the app must never fail to start on this
    return cfg


CONFIG = load_config()
WRAPPER = os.path.join(CLAUDE_DIR, "claude-wrapper.sh")
BRAIN = os.path.expanduser(CONFIG["brain_dir"])
MEMORY_DIR = os.path.join(BRAIN, "memory")
VAULT_NAME = os.path.basename(BRAIN.replace("\\", "/").rstrip("/"))  # obsidian:// URIs
ICON_PATH = os.path.expanduser(CONFIG["icon"])
FTP_DEPLOY = os.path.expanduser(CONFIG["ftp_deploy_script"])
SKILLS_DIR = os.path.join(CLAUDE_DIR, "skills")  # slash commands live here
# Where a pasted or dropped image is parked so the tab can hand Claude a path.
IMAGE_DIR = os.path.join(CLAUDE_DIR, "hub-images")
MAX_UPLOAD = 25 * 1024 * 1024
KEEP_IMAGES = 200
# Only keep folders that actually exist — a stock config lists several candidates.
PROJECT_DIRS = [p for p in (os.path.expanduser(d) for d in CONFIG["project_dirs"])
                if os.path.isdir(p)]
HAS_BRAIN = os.path.isdir(MEMORY_DIR)  # no vault → the whole memory UI stays hidden

# Hide the console window Windows would otherwise flash for every git call.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WINDOWS else 0


LOG_PATH = os.path.join(CLAUDE_DIR, "hub.log")
LOG_MAX = 1_000_000          # přes megabajt už se v tom nikdo nevyzná
LOG_KEEP = os.path.join(CLAUDE_DIR, "hub.log.1")
_LOG_LOCK = threading.Lock()


def log(message, level="info"):
    """Zapíše řádek do ~/.claude/hub.log.

    Zástupce na Windows běží přes pythonw.exe, který nemá konzoli — bez tohohle
    by po nepovedeném startu nezbylo vůbec nic ke čtení. Proto se sem logují
    i chyby z obsluhy požadavků a z běhů na pozadí, a proto se to dá vypsat
    přímo v aplikaci.

    Soubor se po megabajtu odloží stranou; drží se jeden předchozí, takže
    historie nezmizí, ale ani neroste donekonečna.
    """
    try:
        import datetime
        line = (f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S}  "
                f"{level.upper():<5} {message}\n")
        with _LOG_LOCK:
            try:
                if os.path.getsize(LOG_PATH) > LOG_MAX:
                    os.replace(LOG_PATH, LOG_KEEP)
            except OSError:
                pass
            os.makedirs(CLAUDE_DIR, exist_ok=True)
            with open(LOG_PATH, "a", encoding="utf-8") as fh:
                fh.write(line)
    except Exception:
        pass          # log, který shodí aplikaci, je horší než žádný


def log_error(message, exc=None):
    if exc is not None:
        message = f"{message}: {type(exc).__name__}: {exc}"
    log(message, "error")


def log_tail(lines=300):
    """Posledních `lines` řádků logu, i přes odložený soubor."""
    out = []
    for path in (LOG_KEEP, LOG_PATH):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                out.extend(fh.read().splitlines())
        except OSError:
            continue
    return out[-lines:]


def log_clear():
    for path in (LOG_PATH, LOG_KEEP):
        try:
            os.remove(path)
        except OSError:
            pass
    log("log vymazán z aplikace")


def report_bundle():
    """Jeden text pro nahlášení problému: co je na stroji + poslední log.

    Cesty a jména projektů v tom být můžou — je to soubor pro člověka, ne pro
    odeslání někam ven, a aplikace ho nikam sama neposílá.
    """
    import datetime
    info = doctor()
    head = [
        "Claude Code Hub — hlášení",
        f"pořízeno   {datetime.datetime.now():%Y-%m-%d %H:%M:%S}",
        f"verze      {version()}",
        "",
        "Prostředí",
        "-" * 44,
    ]
    for key, value in info.items():
        head.append(f"  {key:<18} {value}")
    head += ["", "Log (posledních 300 řádků)", "-" * 44]
    return "\n".join(head + log_tail(300)) + "\n"


# ── Running helper programs ──────────────────────────────────────────────────
def run(argv, cwd=None, timeout=5):
    """Run argv and return stdout, or '' on any failure. Never raises."""
    try:
        r = subprocess.run(argv, capture_output=True, text=True, cwd=cwd,
                           timeout=timeout, creationflags=_NO_WINDOW)
        return r.stdout.strip()
    except Exception:
        return ""


def find_bash():
    """Absolute path to a bash we can run scripts with.

    On Windows this must be Git for Windows' bash.exe. `where bash` there often
    finds C:\\Windows\\System32\\bash.exe, which is the WSL launcher and not a
    shell we can drive through ConPTY — so System32 is explicitly skipped.
    """
    if CONFIG.get("bash") and os.path.isfile(os.path.expanduser(CONFIG["bash"])):
        return os.path.expanduser(CONFIG["bash"])
    if not IS_WINDOWS:
        return shutil.which("bash") or "/bin/bash"

    env_bash = os.environ.get("CLAUDE_CODE_GIT_BASH_PATH", "")
    candidates = [env_bash] if env_bash else []
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
        if base:
            candidates.append(os.path.join(base, "Git", "bin", "bash.exe"))
    found = shutil.which("bash")
    if found and "system32" not in found.lower():  # System32\bash.exe = WSL
        candidates.append(found)
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return ""


def find_git():
    """git executable — falls back to the copy shipped with Git for Windows."""
    found = shutil.which("git")
    if found:
        return found
    bash = find_bash()
    if bash:  # <git>/bin/bash.exe → <git>/cmd/git.exe
        cand = os.path.join(os.path.dirname(os.path.dirname(bash)), "cmd", "git.exe")
        if os.path.isfile(cand):
            return cand
    return ""


BASH = find_bash()
GIT = find_git()


def to_shell_path(path):
    """Windows path → the /c/Users/... form Git Bash understands. No-op elsewhere."""
    if not IS_WINDOWS:
        return path
    p = os.path.abspath(path).replace("\\", "/")
    if len(p) > 1 and p[1] == ":":
        p = "/" + p[0].lower() + p[2:]
    return p


class BashMissing(RuntimeError):
    """No usable bash on this machine — on Windows that means Git for Windows."""


def bash_argv(script):
    """argv that runs a bash snippet, with the environment bash needs.

    -l gives Git Bash a usable PATH; CHERE_INVOKING keeps it in the directory we
    spawned it in instead of jumping to the home dir (Windows only, harmless
    elsewhere).
    """
    if not BASH:
        raise BashMissing(
            "Nenašel jsem bash — nainstaluj Git for Windows: winget install Git.Git"
            if IS_WINDOWS else "Nenašel jsem bash.")
    return [BASH, "-l", "-c", script]


def _installed_locales():
    """Locale names this machine actually has, normalised to lowercase .utf-8."""
    names = set()
    for line in run(["locale", "-a"], timeout=3).splitlines():
        names.add(line.strip().lower().replace(".utf8", ".utf-8"))
    return names


def utf8_locale(current, installed=None):
    """A UTF-8 locale name to use in place of `current`, or `current` if it is
    already UTF-8.

    A tab that starts under LANG=C truncates every accented character on its way
    through readline and the Claude Code TUI, and the app is launched from a
    desktop shortcut, which is exactly where a stripped environment comes from.
    We keep the language when there is one and it is actually generated here —
    naming a locale the machine does not have only earns a setlocale warning on
    every shell start.
    """
    low = (current or "").lower()
    if "utf-8" in low or "utf8" in low:
        return current
    if IS_WINDOWS:                       # Git Bash honours the name, not a locale DB
        return "en_US.UTF-8"
    if installed is None:
        installed = _installed_locales()
    lang = low.split(".")[0].split("@")[0]
    candidates = []
    if lang and lang not in ("c", "posix"):
        candidates.append(lang + ".utf-8")
    candidates += ["c.utf-8", "en_us.utf-8"]
    for cand in candidates:
        if cand in installed:
            # Give it back in the conventional casing: cs_CZ.UTF-8, C.UTF-8
            head, _, _ = cand.partition(".")
            return ("C" if head == "c" else
                    (head.split("_")[0] + "_" + head.split("_")[1].upper()
                     if "_" in head else head)) + ".UTF-8"
    return current or "C.UTF-8"


def child_env():
    env = dict(os.environ)
    env["TERM"] = "xterm-256color"
    env["COLORTERM"] = "truecolor"
    env["CHERE_INVOKING"] = "1"
    # Everything in a tab — bash, the Claude Code TUI, the hooks — has to agree
    # that the bytes on the wire are UTF-8, or diacritics arrive as mojibake.
    installed = None if IS_WINDOWS else _installed_locales()
    for var in ("LC_ALL", "LC_CTYPE", "LANG"):
        if env.get(var):
            env[var] = utf8_locale(env[var], installed)
    if not env.get("LANG") and not env.get("LC_ALL"):
        env["LANG"] = utf8_locale("", installed)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    env.pop("LINES", None)
    env.pop("COLUMNS", None)
    return env


# ── Opening things in the desktop's default app ──────────────────────────────
# Odkazy z výpisu terminálu chodí sem, a výpis může pocházet z cizího repa.
# Systémový handler otevře leccos — `file://…​.desktop` se pod ním klidně spustí —
# takže se pouští jen schémata, u kterých je jasné, co udělají.
SAFE_SCHEMES = ("http://", "https://", "mailto:", "obsidian://")


def open_path(path):
    """Otevře složku, soubor nebo URI výchozí aplikací. False = neotevřeno."""
    path = str(path)
    if "://" in path.split("?", 1)[0] or path.startswith("mailto:"):
        if not path.startswith(SAFE_SCHEMES):
            log(f"open_path odmítl schéma: {path[:80]}")
            return False
    elif not os.path.exists(path):
        return False
    try:
        if IS_WINDOWS:
            if "://" in path or path.startswith("mailto:"):
                subprocess.Popen(["cmd", "/c", "start", "", path], shell=False,
                                 creationflags=_NO_WINDOW)
            else:
                os.startfile(path)  # noqa: S606 — the whole point of this function
        elif IS_MAC:
            subprocess.Popen(["open", "--", path])
        else:
            subprocess.Popen(["xdg-open", path],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def has_obsidian():
    """True if an obsidian:// URL handler is registered on this machine."""
    try:
        if IS_WINDOWS:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "obsidian"):
                return True
        if IS_MAC:
            return os.path.isdir("/Applications/Obsidian.app")
        return bool(run(["xdg-mime", "query", "default",
                         "x-scheme-handler/obsidian"], timeout=3))
    except Exception:
        return False


# ── Palettes (GitHub-style dark & light) ─────────────────────────────────────
# Single source of truth: the CSS reads these as custom properties and xterm.js
# reads the same values as its terminal theme.
DARK = {
    "AMBER": "#e0843c", "BG": "#0d1117", "BG_SIDEBAR": "#161b22",
    "BG_CARD": "#1b2129", "FG": "#d0d0d0", "FG_BRIGHT": "#e6edf3",
    "DIM": "#8b949e", "GREEN": "#3fb950", "RED": "#f85149",
    "CARD_HOVER": "#242b35", "BORDER": "#30363d", "SECTION": "#6e7681",
    # Krok pro grafy: prošel pásem světlosti i kontrastem na tmavém podkladu.
    "CHART": "#cf752e",
    # 16-colour terminal palette (Afterglow)
    "TERM_PALETTE": [
        "#151515", "#ac4142", "#7e8e50", "#e5b567", "#6c99bb", "#9f4e85",
        "#7dd6cf", "#d0d0d0", "#505050", "#ac4142", "#7e8e50", "#e5b567",
        "#6c99bb", "#9f4e85", "#7dd6cf", "#f5f5f5",
    ],
}
LIGHT = {
    "AMBER": "#bc5c1c", "BG": "#ffffff", "BG_SIDEBAR": "#f6f8fa",
    "BG_CARD": "#ffffff", "FG": "#24292f", "FG_BRIGHT": "#1f2328",
    "DIM": "#656d76", "GREEN": "#1a7f37", "RED": "#cf222e",
    "CARD_HOVER": "#eef1f4", "BORDER": "#d0d7de", "SECTION": "#8c959f",
    "CHART": "#bc5c1c",
    # 16-colour terminal palette (GitHub Light)
    "TERM_PALETTE": [
        "#24292e", "#cf222e", "#116329", "#953800", "#0969da", "#8250df",
        "#1b7c83", "#6e7781", "#57606a", "#a40e26", "#1a7f37", "#633c01",
        "#218bff", "#a475f9", "#3192aa", "#8c959f",
    ],
}


# ── Data gathering ───────────────────────────────────────────────────────────
def get_projects():
    projects = []
    for base in PROJECT_DIRS:
        if not os.path.isdir(base):
            continue
        for d in sorted(os.listdir(base)):
            path = os.path.join(base, d)
            if not os.path.isdir(path):
                continue
            try:
                entries = os.listdir(path)
            except Exception:
                continue
            is_git = os.path.isdir(os.path.join(path, ".git"))
            has_php = any(f.endswith(".php") for f in entries
                          if os.path.isfile(os.path.join(path, f)))
            has_liquid = (os.path.isdir(os.path.join(path, "sections")) or
                          os.path.isdir(os.path.join(path, "templates")))
            has_pkg = os.path.isfile(os.path.join(path, "package.json"))
            has_composer = os.path.isfile(os.path.join(path, "composer.json"))
            if not (is_git or has_php or has_liquid or has_pkg or has_composer):
                continue
            if has_liquid:
                ptype = "Shopify"
            elif has_composer or has_php:
                ptype = "PHP"
            elif has_pkg:
                ptype = "Node"
            else:
                ptype = "Git"
            branch, dirty_count, remote = "", 0, ""
            if is_git and GIT:
                branch = run([GIT, "branch", "--show-current"], cwd=path)
                status = run([GIT, "status", "--porcelain"], cwd=path)
                dirty_count = len(status.split("\n")) if status else 0
                remote = github_slug(run([GIT, "remote", "get-url", "origin"],
                                         cwd=path))
            projects.append({
                "name": d, "path": path, "type": ptype,
                "branch": branch, "dirty": dirty_count,
                "deployable": os.path.isfile(os.path.join(path, ".ftp-deploy.json")),
                "remote": remote,
            })

    # Ručně přidané složky se skenem nenajdou — leží mimo nastavené cesty.
    known = {p["path"] for p in projects}
    for extra in CONFIG.get("extra_projects") or []:
        path = os.path.expanduser(extra)
        if not os.path.isdir(path) or path in known:
            continue
        branch, dirty_count = "", 0
        if os.path.isdir(os.path.join(path, ".git")) and GIT:
            branch = run([GIT, "branch", "--show-current"], cwd=path)
            status = run([GIT, "status", "--porcelain"], cwd=path)
            dirty_count = len(status.split("\n")) if status else 0
        projects.append({
            "name": os.path.basename(path.rstrip("/\\")) or path, "path": path,
            "type": "Git" if branch else "Složka", "branch": branch,
            "dirty": dirty_count, "manual": True,
            "deployable": os.path.isfile(os.path.join(path, ".ftp-deploy.json")),
        })

    meta = load_projects()
    for proj in projects:
        # Kdy se na projektu naposledy dělalo. `.git` je nepoužitelné — dotkne
        # se ho každý `git status`, který si pro panel pouštíme sami, takže by
        # všechny projekty vycházely jako „právě teď". Bereme proto obsah
        # pracovní kopie, a jen z první úrovně: procházet celý strom by
        # u velkých repozitářů stálo víc, než kolik ten údaj vydá.
        newest = 0
        try:
            for name in os.listdir(proj["path"]):
                if name in (".git", "node_modules", "vendor", ".venv"):
                    continue
                try:
                    newest = max(newest, os.path.getmtime(
                        os.path.join(proj["path"], name)))
                except OSError:
                    pass
        except OSError:
            pass
        proj["mtime"] = int(newest)
        info = meta.get(os.path.abspath(proj["path"]), {})
        proj["label"] = info.get("label", "")
        proj["brief"] = info.get("brief", "")
        proj["group"] = info.get("group", "")
        proj["image"] = info.get("image", "")
        # Ručně zadané repo přebíjí to z gitu — někdo může chtít ukázat jinam.
        proj["repo"] = info.get("repo") or proj.get("remote", "")
        proj["archived"] = bool(info.get("archived"))
    projects.sort(key=lambda p: (p["archived"], (p["group"] or "").lower(),
                                 (p["label"] or p["name"]).lower()))
    return projects


def _note_title(path):
    """Human title for a memory note: frontmatter description, else # heading, else name."""
    try:
        desc = heading = ""
        in_fm = False
        with open(path, encoding="utf-8") as fh:
            for i, line in enumerate(fh):
                s = line.strip()
                if i == 0 and s == "---":
                    in_fm = True
                    continue
                if in_fm:
                    if s == "---":
                        in_fm = False
                    elif s.startswith("description:"):
                        desc = s.split(":", 1)[1].strip()
                elif s.startswith("# "):
                    heading = s[2:].strip()
                    break
        return desc or heading or os.path.basename(path)[:-3]
    except Exception:
        return os.path.basename(path)[:-3]


def get_memory():
    """(counts, recent) for the per-note memory vault; recent = newest 8 notes."""
    kinds = {"learnings": "learning_", "errors": "error_", "wins": "win_"}
    counts = {k: 0 for k in kinds}
    entries = []
    try:
        names = os.listdir(MEMORY_DIR)
    except Exception:
        names = []
    for fname in names:
        if not fname.endswith(".md"):
            continue
        for kind, prefix in kinds.items():
            if fname.startswith(prefix):
                counts[kind] += 1
                fpath = os.path.join(MEMORY_DIR, fname)
                try:
                    mtime = os.path.getmtime(fpath)
                except Exception:
                    mtime = 0
                entries.append((mtime, kind, _note_title(fpath), fname))
                break
    entries.sort(key=lambda x: x[0], reverse=True)  # newest first
    recent = [{"kind": k, "title": t, "file": f} for _, k, t, f in entries[:8]]
    return counts, recent


_SAFE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".avif",
             ".pdf", ".txt", ".md", ".csv", ".json", ".log"}


def save_upload(name, raw):
    """Park a pasted/dropped file on disk and return its path.

    The browser never tells us where a dropped file really lives, and a pasted
    screenshot has no path at all — so the only way to get one into a tab is to
    write our own copy and type that path at the prompt.
    """
    import datetime
    import re
    os.makedirs(IMAGE_DIR, exist_ok=True)
    stem, ext = os.path.splitext(os.path.basename(name or "obrazek.png"))
    ext = ext.lower() if ext.lower() in _SAFE_EXT else ".png"
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-.")[:40] or "obrazek"
    path = os.path.join(
        IMAGE_DIR, f"{datetime.datetime.now():%Y%m%d-%H%M%S}-{stem}{ext}")
    n = 1
    while os.path.exists(path):
        path = path[:-len(ext)] + f"-{n}" + ext
        n += 1
    with open(path, "wb") as fh:
        fh.write(raw)
    _prune_images()
    return path


def _prune_images():
    """Keep the folder from growing forever — screenshots add up fast."""
    try:
        files = [os.path.join(IMAGE_DIR, n) for n in os.listdir(IMAGE_DIR)]
        files = [f for f in files if os.path.isfile(f)]
        for old in sorted(files, key=os.path.getmtime, reverse=True)[KEEP_IMAGES:]:
            os.remove(old)
    except Exception:
        pass


# ── Konfigurace za běhu ──────────────────────────────────────────────────────
def refresh():
    """Načte hub-config.json znovu a přepočítá, co z něj plyne.

    Onboarding mění cesty za běhu aplikace. Bez tohohle by se změna projevila
    až po restartu, protože odvozené hodnoty vznikají při importu.
    """
    global CONFIG, BRAIN, MEMORY_DIR, VAULT_NAME, ICON_PATH, FTP_DEPLOY
    global PROJECT_DIRS, HAS_BRAIN
    CONFIG = load_config()
    BRAIN = os.path.expanduser(CONFIG["brain_dir"])
    MEMORY_DIR = os.path.join(BRAIN, "memory")
    VAULT_NAME = os.path.basename(BRAIN.replace("\\", "/").rstrip("/"))
    ICON_PATH = os.path.expanduser(CONFIG["icon"])
    FTP_DEPLOY = os.path.expanduser(CONFIG["ftp_deploy_script"])
    PROJECT_DIRS = [p for p in (os.path.expanduser(d) for d in CONFIG["project_dirs"])
                    if os.path.isdir(p)]
    HAS_BRAIN = os.path.isdir(MEMORY_DIR)


def save_config(updates):
    """Zapíše změny do hub-config.json (jen předané klíče) a zavolá refresh()."""
    cfg = dict(load_config())
    cfg.update(updates)
    # Nechceme do souboru vrátit výchozí hodnoty, které tam uživatel nemá —
    # zapisuje se to, co v něm bylo, plus změna.
    os.makedirs(CLAUDE_DIR, exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, CONFIG_PATH)
    refresh()
    return cfg


def suggest_project_dirs():
    """Složky, kde na tomhle stroji nejspíš bydlí projekty."""
    if IS_WINDOWS:
        cands = [os.path.join(HOME, d) for d in
                 ("Desktop", "Documents", r"source\repos", "projects", "dev", "code")]
        cands += [r"C:\xampp\htdocs", r"C:\wamp64\www", r"C:\laragon\www"]
    else:
        cands = [os.path.join(HOME, d) for d in
                 ("Desktop", "Plocha", "Projects", "projects", "dev", "code",
                  "git", "src", "www")]
        cands += ["/opt/lampp/htdocs", "/var/www/html"]
    return [p for p in cands if os.path.isdir(p)]


# ── Projekty: štítky, briefing, archiv ──────────────────────────────────────
# Panel se plní skenem složek, ale co si o projektu myslí člověk, z disku
# vyčíst nejde. Drží se to vedle, klíčované cestou, aby se skenování a poznámky
# navzájem nepřepisovaly.
PROJECTS_PATH = os.path.join(CLAUDE_DIR, "hub-projects.json")

BRIEF_START = "<!-- hub:briefing -->"
BRIEF_END = "<!-- /hub:briefing -->"


def github_slug(url):
    """Z git adresy udělá `owner/repo`, nebo prázdno, když to není GitHub."""
    url = (url or "").strip()
    if not url:
        return ""
    m = re.search(r"github\.com[:/]+([^/]+)/(.+?)(?:\.git)?/?$", url)
    return f"{m.group(1)}/{m.group(2)}" if m else ""


def load_projects():
    try:
        with open(PROJECTS_PATH, encoding="utf-8-sig") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_projects(data):
    os.makedirs(CLAUDE_DIR, exist_ok=True)
    tmp = PROJECTS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    try:
        os.chmod(tmp, 0o600)      # briefingy bývají o klientech
    except OSError:
        pass
    os.replace(tmp, PROJECTS_PATH)


def project_meta(path):
    return load_projects().get(os.path.abspath(path), {})


def set_project_meta(path, updates):
    """Uloží štítek/briefing/archivaci k projektu. Prázdné hodnoty klíč smažou."""
    path = os.path.abspath(os.path.expanduser(path))
    data = load_projects()
    entry = dict(data.get(path, {}))
    for key, value in updates.items():
        if value in ("", None, False) and key != "archived":
            entry.pop(key, None)
        else:
            entry[key] = value
    if entry:
        data[path] = entry
    else:
        data.pop(path, None)
    save_projects(data)
    return entry


def write_briefing(path, text):
    """Vloží briefing do CLAUDE.md projektu, aby ho Claude Code sám přečetl.

    Do cizího obsahu se nesahá: blok je ohraničený značkami a při dalším uložení
    se jen vymění. Když CLAUDE.md ještě není, založí se.
    """
    path = os.path.abspath(os.path.expanduser(path))
    target = os.path.join(path, "CLAUDE.md")
    block = f"{BRIEF_START}\n## O projektu\n\n{text.strip()}\n{BRIEF_END}"
    try:
        existing = ""
        if os.path.isfile(target):
            with open(target, encoding="utf-8") as fh:
                existing = fh.read()
        if BRIEF_START in existing and BRIEF_END in existing:
            head = existing.split(BRIEF_START)[0]
            tail = existing.split(BRIEF_END, 1)[1]
            new = head + block + tail
        elif existing.strip():
            new = existing.rstrip() + "\n\n" + block + "\n"
        else:
            new = block + "\n"
        if not text.strip():           # briefing smazán → vyhodit i blok
            if BRIEF_START in existing and BRIEF_END in existing:
                new = (existing.split(BRIEF_START)[0].rstrip() + "\n" +
                       existing.split(BRIEF_END, 1)[1].lstrip())
                new = new.strip() + "\n" if new.strip() else ""
            else:
                return {"ok": True, "written": False}
        if new.strip():
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(new)
        elif os.path.isfile(target):
            os.remove(target)          # zbyl by prázdný soubor
        return {"ok": True, "written": True, "file": target}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}


# ── Existující Obsidian paměť ────────────────────────────────────────────────
# Obsidian si seznam vaultů vede sám; je to nejspolehlivější zdroj, protože
# ví i o těch, které leží mimo obvyklé složky.
OBSIDIAN_CONFIGS = [
    "~/.config/obsidian/obsidian.json",                                  # nativní
    "~/.var/app/md.obsidian.Obsidian/config/obsidian/obsidian.json",     # flatpak
    "~/snap/obsidian/current/.config/obsidian/obsidian.json",            # snap
    "~/Library/Application Support/obsidian/obsidian.json",              # macOS
]


def obsidian_vaults():
    """Vaulty, o kterých na tomhle stroji víme. Nejdřív ty od Obsidianu."""
    found, seen = [], set()

    def add(path, source):
        path = os.path.abspath(os.path.expanduser(path))
        if path in seen or not os.path.isdir(path):
            return
        seen.add(path)
        found.append({
            "path": path,
            "name": os.path.basename(path),
            "source": source,
            # Paměť hubu žije v podsložce memory/ — když tam je, je vault
            # rovnou použitelný; když ne, dá se založit.
            "notes": len([n for n in os.listdir(os.path.join(path, "memory"))
                          if n.endswith(".md")])
                     if os.path.isdir(os.path.join(path, "memory")) else 0,
            "has_memory": os.path.isdir(os.path.join(path, "memory")),
        })

    configs = list(OBSIDIAN_CONFIGS)
    if IS_WINDOWS and os.environ.get("APPDATA"):
        configs.insert(0, os.path.join(os.environ["APPDATA"], "obsidian",
                                       "obsidian.json"))
    for cfg in configs:
        try:
            with open(os.path.expanduser(cfg), encoding="utf-8") as fh:
                data = json.load(fh)
            for entry in (data.get("vaults") or {}).values():
                if entry.get("path"):
                    add(entry["path"], "Obsidian")
        except Exception:
            continue

    # Záloha pro toho, kdo Obsidian nemá nainstalovaný, ale složku už má.
    import glob
    for pattern in ("~/Obsidian/*", "~/Documents/Obsidian/*", "~/obsidian/*",
                    "~/Nextcloud/*", "~/Dropbox/*", "~/OneDrive/*"):
        for path in sorted(glob.glob(os.path.expanduser(pattern))):
            if os.path.isdir(os.path.join(path, ".obsidian")) or \
                    os.path.isdir(os.path.join(path, "memory")):
                add(path, "na disku")
    return found


def memory_link_path():
    """Kam Claude Code ukládá vlastní paměť pro domovskou složku.

    Jméno složky je odvozené z cesty, ale hádat ho je krajní řešení — když už
    ji Claude Code jednou založil, použijeme tu jeho. Na Windows se navíc do
    jména promítá i disk, takže dohad by mohl sedět jen náhodou.
    """
    projects = os.path.join(CLAUDE_DIR, "projects")
    home_real = os.path.realpath(HOME)
    try:
        for name in os.listdir(projects):
            candidate = os.path.join(projects, name, "memory")
            # existující složka pro domovský adresář pozná podle jména
            plain = name.replace("-", "").lower()
            if plain and plain == home_real.replace(os.sep, "").replace(
                    "/", "").replace(":", "").lower():
                return candidate
    except OSError:
        pass
    slug = home_real.replace("\\", "/").replace(":", "").replace("/", "-")
    return os.path.join(projects, slug, "memory")


def _make_link(target, link):
    """Symlink, a na Windows křižovatka, když symlink nejde.

    Symlink na Windows chce buď práva správce, nebo zapnutý vývojářský režim —
    křižovatka (junction) nechce nic a pro složku dělá totéž.
    """
    try:
        os.symlink(target, link, target_is_directory=True)
        return "symlink"
    except (OSError, NotImplementedError, AttributeError):
        if not IS_WINDOWS:
            raise
    r = subprocess.run(["cmd", "/c", "mklink", "/J", link, target],
                       capture_output=True, text=True, creationflags=_NO_WINDOW)
    if r.returncode != 0:
        raise OSError((r.stderr or r.stdout or "mklink selhal").strip()[:200])
    return "junction"


def _is_link(path):
    """islink() na Windows křižovatku nepozná, ale readlink() ji přečte."""
    if os.path.islink(path):
        return True
    if IS_WINDOWS and os.path.isdir(path):
        try:
            os.readlink(path)
            return True
        except OSError:
            return False
    return False


def link_memory(vault):
    """Napojí paměť Claude Code na vault. Vrací popis toho, co se stalo.

    Samotný `brain_dir` v konfigu řídí jen hub — vlastní paměť si Claude Code
    hledá v `~/.claude/projects/<slug>/memory`. Teprve tenhle odkaz z toho
    udělá jedno a totéž, což je celý smysl „napojit existující paměť".
    """
    vault = os.path.abspath(os.path.expanduser(vault))
    memory = os.path.join(vault, "memory")
    os.makedirs(memory, exist_ok=True)
    index = os.path.join(memory, "MEMORY.md")
    if not os.path.isfile(index):
        with open(index, "w", encoding="utf-8") as fh:
            fh.write(EMPTY_MEMORY_INDEX)

    link = memory_link_path()
    os.makedirs(os.path.dirname(link), exist_ok=True)
    moved = ""
    if _is_link(link):
        if os.path.realpath(link) == os.path.realpath(memory):
            return {"linked": link, "target": memory, "moved": "", "how": "beze změny"}
        try:
            os.unlink(link)
        except OSError:
            os.rmdir(link)                 # křižovatka se ruší jako složka
    elif os.path.isdir(link):
        # Skutečná složka s poznámkami se nemaže — odsune se stranou.
        if os.listdir(link):
            import datetime
            moved = f"{link}-backup-{datetime.datetime.now():%Y%m%d-%H%M%S}"
            shutil.move(link, moved)
        else:
            os.rmdir(link)
    elif os.path.exists(link):
        os.unlink(link)
    how = _make_link(memory, link)
    return {"linked": link, "target": memory, "moved": moved, "how": how}


def clone_vault(repo, parent):
    """Naklonuje vault z gitu. repo = owner/repo nebo celá URL."""
    repo = _check_repo(repo)
    parent = os.path.abspath(os.path.expanduser(parent))
    name = os.path.basename(repo.rstrip("/")).replace(".git", "")
    if not name or name in (".", ".."):
        raise ValueError("Z adresy nejde odvodit jméno složky.")
    target = os.path.join(parent, name)
    if os.path.exists(target):
        raise ValueError(f"{target} už existuje.")
    if not GIT:
        raise ValueError("Na stroji není git.")
    os.makedirs(parent, exist_ok=True)
    url = repo if ("://" in repo or repo.startswith("git@")) \
        else f"https://github.com/{repo}.git"
    if shutil.which("gh") and "://" not in repo and not repo.startswith("git@"):
        cmd = ["gh", "repo", "clone", "--", repo, target]   # umí i privátní repo
    else:
        cmd = [GIT, "clone", "--quiet", "--", url, target]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise ValueError((r.stderr or "klonování nevyšlo").strip()[:300])
    return target


# ── Paměť v cloudu ───────────────────────────────────────────────────────────
# Vault je obyčejná složka s markdownem, takže „napojení na cloud" znamená
# jediné: ať leží uvnitř složky, kterou už nějaký klient synchronizuje.
CLOUD_DIRS = [
    ("OneDrive", ["~/OneDrive", "~/OneDrive - *", "~/Onedrive"]),
    ("Dropbox", ["~/Dropbox"]),
    ("Disk Google", ["~/Google Drive", "~/GoogleDrive", "~/Insync/*"]),
    ("Nextcloud", ["~/Nextcloud", "~/ownCloud"]),
    ("pCloud", ["~/pCloudDrive"]),
    ("MEGA", ["~/MEGA"]),
    ("Syncthing", ["~/Sync"]),
    ("Proton Drive", ["~/Proton Drive", "~/ProtonDrive"]),
    ("iCloud", ["~/Library/Mobile Documents/com~apple~CloudDocs"]),
]


def cloud_folders():
    """Složky synchronizovaných klientů, které na tomhle stroji opravdu jsou."""
    import glob
    found = []
    for name, patterns in CLOUD_DIRS:
        for pattern in patterns:
            for path in sorted(glob.glob(os.path.expanduser(pattern))):
                if os.path.isdir(path):
                    found.append({"name": name, "path": path})
    if IS_WINDOWS and os.environ.get("OneDrive"):
        p = os.environ["OneDrive"]
        if os.path.isdir(p) and not any(f["path"] == p for f in found):
            found.insert(0, {"name": "OneDrive", "path": p})
    return found


def _relink(old_root, new_root):
    """Přesměruje symlinky, které mířily do staré cesty vaultu.

    Paměť Claude Code je symlink do vaultu (`~/.claude/projects/<…>/memory`);
    kdyby po přesunu zůstal viset na staré cestě, paměť by prostě oslepla.
    """
    fixed = []
    base = os.path.join(CLAUDE_DIR, "projects")
    for root, dirs, files in os.walk(base):
        for name in list(dirs) + list(files):
            link = os.path.join(root, name)
            if not os.path.islink(link):
                continue
            target = os.readlink(link)
            if not target.startswith(old_root):
                continue
            new_target = new_root + target[len(old_root):]
            os.unlink(link)
            os.symlink(new_target, link)
            fixed.append(link)
    return fixed


def move_vault(target_parent):
    """Přesune vault do zadané složky a všechno na něj přesměruje."""
    src = os.path.abspath(BRAIN)
    parent = os.path.abspath(os.path.expanduser(target_parent))
    if not os.path.isdir(src):
        raise ValueError("Vault na původním místě není.")
    dst = os.path.join(parent, os.path.basename(src))
    if os.path.abspath(dst) == src:
        raise ValueError("Vault už tam je.")
    if os.path.exists(dst):
        raise ValueError(f"{dst} už existuje — přesun by přepsal cizí data.")
    os.makedirs(parent, exist_ok=True)
    shutil.move(src, dst)
    relinked = _relink(src, dst)
    save_config({"brain_dir": dst})
    return {"path": dst, "relinked": relinked}


# ── Paměť v gitu ─────────────────────────────────────────────────────────────
EMPTY_MEMORY_INDEX = """# Paměť

Jeden soubor = jedna poznámka. Sem patří jenom **rozcestník**: na každou
poznámku jeden řádek do 120 znaků, detail žije v odkazovaném souboru.
Když index přeroste zhruba 25 kB, načte se ho jen kus a na zbytek se zapomene.

## Projekty a reference

## Poznatky (learnings)

## Chyby (errors)

## Úspěchy (wins)
"""

VAULT_GITIGNORE = """# Obsidian si sem ukládá stav okna — do historie nepatří
.obsidian/workspace*
.obsidian/cache
.trash/
.DS_Store
"""


def vault_git_state():
    """(je_repo, remote_url) pro vault."""
    if not os.path.isdir(BRAIN):
        return False, ""
    if not os.path.isdir(os.path.join(BRAIN, ".git")):
        return False, ""
    return True, run([GIT, "remote", "get-url", "origin"], cwd=BRAIN) if GIT else ""


NAME_RE = re.compile(r"^[A-Za-z0-9][\w.-]{0,99}$")


def vault_git_setup(repo_name):
    """Založí z vaultu privátní repo na GitHubu a pošle tam první commit.

    Vrací (ok, hlášku). Nikdy nevyhazuje — onboarding musí umět pokračovat
    i když tohle nevyjde.
    """
    repo_name = str(repo_name).strip()
    if not NAME_RE.match(repo_name):
        return False, "Jméno repa smí být jen písmena, číslice, tečka a pomlčka."
    if not GIT:
        return False, "Na stroji není git."
    if not shutil.which("gh"):
        return False, "Chybí GitHub CLI (gh)."
    if not os.path.isdir(BRAIN):
        return False, "Vault neexistuje."
    try:
        if subprocess.run(["gh", "auth", "status"], capture_output=True,
                          timeout=20).returncode != 0:
            return False, "gh není přihlášený — spusť: gh auth login"
    except Exception:
        return False, "gh se nepodařilo spustit."

    gitignore = os.path.join(BRAIN, ".gitignore")
    if not os.path.isfile(gitignore):
        with open(gitignore, "w", encoding="utf-8") as fh:
            fh.write(VAULT_GITIGNORE)

    try:
        if not os.path.isdir(os.path.join(BRAIN, ".git")):
            job_step("vault", "zakládám repo…")
            subprocess.run([GIT, "init", "-q"], cwd=BRAIN, check=True,
                           capture_output=True, timeout=30)
        job_step("vault", "přidávám poznámky…")
        subprocess.run([GIT, "add", "-A"], cwd=BRAIN, check=True,
                       capture_output=True, timeout=120)
        # Prázdný commit projde taky — jde o to mít co pushnout.
        subprocess.run([GIT, "commit", "-q", "--allow-empty",
                        "-m", "paměť: první záloha"],
                       cwd=BRAIN, capture_output=True, timeout=120)
        job_step("vault", "posílám na GitHub…")
        existing = run([GIT, "remote", "get-url", "origin"], cwd=BRAIN)
        if not existing:
            r = subprocess.run(
                ["gh", "repo", "create", "--private",
                 "--source", ".", "--push", "--", repo_name],
                cwd=BRAIN, capture_output=True, text=True, timeout=300)
            if r.returncode != 0:
                return False, (r.stderr or r.stdout or "gh repo create selhalo").strip()[:300]
        else:
            r = subprocess.run([GIT, "push", "-u", "origin", "HEAD"], cwd=BRAIN,
                               capture_output=True, text=True, timeout=300)
            if r.returncode != 0:
                return False, (r.stderr or "push selhal").strip()[:300]
    except subprocess.CalledProcessError as exc:
        return False, (exc.stderr or b"").decode("utf-8", "replace")[:300] or str(exc)
    except Exception as exc:
        return False, str(exc)[:300]

    save_config({"vault_autosync": True})
    return True, run([GIT, "remote", "get-url", "origin"], cwd=BRAIN)


def vault_git_push(message="paměť: automatická záloha"):
    """Commit + push vaultu. Používá to Stop hook, takže musí být tichý."""
    if not GIT or not os.path.isdir(os.path.join(BRAIN, ".git")):
        return False, "vault není git repo"
    try:
        if not run([GIT, "status", "--porcelain"], cwd=BRAIN):
            return True, "beze změn"
        subprocess.run([GIT, "add", "-A"], cwd=BRAIN, capture_output=True, timeout=120)
        subprocess.run([GIT, "commit", "-q", "-m", message], cwd=BRAIN,
                       capture_output=True, timeout=120)
        r = subprocess.run([GIT, "push", "-q", "origin", "HEAD"], cwd=BRAIN,
                           capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            return False, (r.stderr or "push selhal").strip()[:200]
        return True, "posláno"
    except Exception as exc:
        return False, str(exc)[:200]


# ── Verze a aktualizace ──────────────────────────────────────────────────────
# „Aktualizovat" a „načíst znovu" jsou dvě různé věci: ⟳ v hlavičce jen přečte
# projekty a paměť, tohle mění samotnou aplikaci. Proto se to jmenuje jinak
# a proto se tu drží číslo verze, ať je vidět, co je nainstalované.
REPO = "jurapascal/claude-code-hub"
SRC_DIR = os.path.join(CLAUDE_DIR, "hub-src")


def version():
    """Verze, která je NAINSTALOVANÁ — čte se ze souboru, ne z importu.

    Běžící proces si modul drží v paměti z okamžiku startu, takže po aktualizaci
    hlásil pořád tu starou: „aktualizováno" a hned pod tím „je dostupná novější".
    """
    path = os.path.join(CLAUDE_DIR, "hub", "__init__.py")
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("__version__"):
                    return line.split("=", 1)[1].strip().strip("\"'")
    except Exception:
        pass
    from . import __version__      # spouštěno z klonu, ne z instalace
    return __version__


def _as_tuple(text):
    """'v1.2.3' → (1, 2, 3). Co nejde přečíst, je (0,), tedy nejstarší."""
    parts = []
    for chunk in str(text or "").lstrip("vV").split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts) or (0,)


def source_dir():
    """Složka se zdrojem, ze kterého se hub instaluje."""
    for cand in (CONFIG.get("src_dir"), SRC_DIR):
        if not cand:
            continue
        path = os.path.expanduser(cand)
        if os.path.isfile(os.path.join(path, "install.sh")):
            return path
    return ""


def latest_version():
    """(nejnovější vydaná verze, důvod prázdna).

    Rozlišuje „není síť" od „repo zatím nic nevydalo" — pro toho, kdo se dívá
    do nastavení, je to úplně jiná zpráva.
    """
    import urllib.error
    import urllib.request
    reachable = False
    for url, key in ((f"https://api.github.com/repos/{REPO}/releases/latest", "tag_name"),
                     (f"https://api.github.com/repos/{REPO}/tags", None)):
        try:
            req = urllib.request.Request(url, headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "claude-code-hub"})
            with urllib.request.urlopen(req, timeout=6) as r:
                data = json.load(r)
            reachable = True
            tag = data.get(key) if key else (data[0]["name"] if data else "")
            if tag:
                return str(tag), ""
        except urllib.error.HTTPError:
            reachable = True          # server odpověděl, jen tam nic není
            continue
        except Exception:
            continue
    return "", ("Repo zatím nemá žádné vydání." if reachable
                else "Nepodařilo se spojit s GitHubem.")


def version_info(check_remote=False):
    src = source_dir()
    info = {"version": version(), "src": src, "repo": REPO, "latest": "",
            "update_available": False}
    if src and GIT:
        info["commit"] = run([GIT, "-C", src, "rev-parse", "--short", "HEAD"])
    if check_remote:
        latest, why = latest_version()
        info["latest"] = latest
        info["why"] = why
        info["update_available"] = bool(
            latest and _as_tuple(latest) > _as_tuple(version()))
    return info


def _fetch_source():
    """Zajistí, že v ~/.claude/hub-src je aktuální zdroj. Vrací (ok, detail).

    Klon se používá, když už tam je; jinak se stáhne tarball, takže aktualizace
    funguje i tomu, kdo hub dostal jako ZIP a git nemá.
    """
    if GIT and os.path.isdir(os.path.join(SRC_DIR, ".git")):
        r = subprocess.run([GIT, "-C", SRC_DIR, "pull", "--ff-only", "--quiet"],
                           capture_output=True, text=True, timeout=180)
        if r.returncode != 0:
            return False, (r.stderr or "git pull selhal").strip()[:300]
        return True, "git pull"
    if GIT:
        parent = os.path.dirname(SRC_DIR)
        os.makedirs(parent, exist_ok=True)
        if os.path.isdir(SRC_DIR):
            shutil.rmtree(SRC_DIR, ignore_errors=True)
        r = subprocess.run([GIT, "clone", "--quiet", "--depth", "1",
                            f"https://github.com/{REPO}.git", SRC_DIR],
                           capture_output=True, text=True, timeout=300)
        if r.returncode == 0:
            return True, "klon"
        return False, (r.stderr or "klonování selhalo").strip()[:300]

    import tarfile
    import tempfile
    import urllib.request
    url = f"https://codeload.github.com/{REPO}/tar.gz/refs/heads/main"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            archive = os.path.join(tmp, "hub.tgz")
            urllib.request.urlretrieve(url, archive)
            with tarfile.open(archive) as tf:
                # extractall bez filtru umí zapsat i mimo cílovou složku,
                # když archiv obsahuje `../` nebo absolutní cesty.
                try:
                    tf.extractall(tmp, filter="data")        # Python 3.12+
                except TypeError:
                    for member in tf.getmembers():
                        dest = os.path.realpath(os.path.join(tmp, member.name))
                        if not dest.startswith(os.path.realpath(tmp) + os.sep):
                            raise ValueError("archiv chtěl zapsat mimo složku")
                        if member.issym() or member.islnk():
                            raise ValueError("archiv obsahuje odkazy")
                    tf.extractall(tmp)
            inner = [os.path.join(tmp, n) for n in os.listdir(tmp)
                     if os.path.isdir(os.path.join(tmp, n))]
            if not inner:
                return False, "stažený balík byl prázdný"
            if os.path.isdir(SRC_DIR):
                shutil.rmtree(SRC_DIR, ignore_errors=True)
            shutil.move(inner[0], SRC_DIR)
        return True, "tarball"
    except Exception as exc:
        return False, f"stažení nevyšlo: {exc}"[:300]


# Cokoli, co trvá déle než okamžik, se nesmí dělat uvnitř HTTP požadavku:
# stránka na něm visí, a když se okno mezitím zavře nebo načte znovu, výsledek
# se nemá kam vrátit. Přesně to se stalo u aktualizace i u zálohy paměti do
# gitu (`gh repo create --push` na patnáctimegovém vaultu chvíli trvá).
# Běží proto na pozadí pod jménem a stav se odečítá.
_JOBS = {}
_JOBS_LOCK = threading.Lock()


def job_state(name):
    with _JOBS_LOCK:
        return dict(_JOBS.get(name) or
                    {"running": False, "done": False, "result": None, "step": ""})


def job_step(name, text):
    with _JOBS_LOCK:
        if name in _JOBS:
            _JOBS[name]["step"] = text


def start_job(name, fn):
    """Spustí fn() na pozadí pod jménem. False, když už jedna běží."""
    with _JOBS_LOCK:
        if (_JOBS.get(name) or {}).get("running"):
            return False
        _JOBS[name] = {"running": True, "done": False, "result": None,
                       "step": "začínám…"}

    log(f"úloha {name}: start")

    def worker():
        started = time.time()
        try:
            result = fn()
        except Exception as exc:          # pojistka, ať stav nezůstane viset
            log_error(f"úloha {name} spadla", exc)
            result = {"ok": False, "detail": str(exc)[:300]}
        took = time.time() - started
        if isinstance(result, dict) and result.get("ok") is False:
            log(f"úloha {name}: neúspěch za {took:.1f}s — "
                f"{str(result.get('detail'))[:200]}", "warn")
        else:
            log(f"úloha {name}: hotovo za {took:.1f}s")
        with _JOBS_LOCK:
            _JOBS[name] = {"running": False, "done": True, "result": result,
                           "step": ""}

    threading.Thread(target=worker, daemon=True).start()
    return True


def update_state():
    return job_state("update")


def start_update():
    return start_job("update", update_hub)


def update_hub():
    """Stáhne nejnovější verzi a přeinstaluje ji. Nikdy nevyhazuje."""
    was = version()
    ok, detail = _fetch_source()
    if not ok:
        return {"ok": False, "detail": detail}
    job_step("update", "instaluju…")
    installer = os.path.join(SRC_DIR, "install.sh")
    if IS_WINDOWS:
        installer = os.path.join(SRC_DIR, "install.ps1")
        argv = ["powershell", "-ExecutionPolicy", "Bypass", "-File", installer, "-Yes"]
    else:
        if not BASH:
            return {"ok": False, "detail": "Na stroji není bash."}
        argv = [BASH, installer, "--yes"]
    if not os.path.isfile(installer):
        return {"ok": False, "detail": "Ve staženém zdroji chybí instalačka."}
    try:
        r = subprocess.run(argv, cwd=SRC_DIR, capture_output=True, text=True,
                           timeout=1800)
    except subprocess.TimeoutExpired:
        return {"ok": False, "detail": "Aktualizace trvala moc dlouho."}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)[:300]}
    if r.returncode != 0:
        return {"ok": False,
                "detail": ("Instalace selhala:\n" + (r.stderr or r.stdout))[:500]}
    now = version()                # ze souboru, tedy to, co je fakticky nasazené
    changed = bool(now and now != was)
    return {"ok": True, "changed": changed, "was": was, "now": now or was,
            "restart": changed,
            "detail": (f"Nainstalována verze {now}." if changed
                       else f"Verze {was} je nejnovější.")}


# ── Systémová schránka ───────────────────────────────────────────────────────
# WebKitGTK (okno hubu na Linuxu) odmítá navigator.clipboard bez uživatelského
# gesta a document.execCommand('copy') tam vrací false, takže se v tabu nedalo
# kopírovat vůbec. Hub ale běží jako místní proces, takže na schránku dosáhne
# přímo — a rovnou i na PRIMARY, na kterou je člověk na Linuxu zvyklý.
def _clipboard_tools(which):
    """(příkaz pro čtení, příkaz pro zápis) pro tenhle stroj, nebo (None, None)."""
    primary = which == "primary"
    if IS_WINDOWS:
        if primary:
            return None, None                      # Windows PRIMARY nezná
        # Naměřeno na Windows 11 Pro (kontrola ve virtuálce): „příliš žluťoučký
        # kůň" projde tam a zpět jako „p??li? ?lu?ou?k? k??". PowerShell sype
        # stdout v kódování konzole (OEM), ne v UTF-8, a clip.exe čte stdin
        # stejně — Unicode pozná jen podle UTF-16LE BOM. Obojí se musí říct.
        return (["powershell", "-NoProfile", "-Command",
                 "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
                 "Get-Clipboard -Raw"],
                ["clip"])
    if IS_MAC:
        if primary:
            return None, None
        return (["pbpaste"], ["pbcopy"])
    if shutil.which("wl-copy") and shutil.which("wl-paste"):
        sel = ["--primary"] if primary else []
        return (["wl-paste", "--no-newline"] + sel, ["wl-copy"] + sel)
    if shutil.which("xclip"):
        target = "primary" if primary else "clipboard"
        return (["xclip", "-selection", target, "-o"],
                ["xclip", "-selection", target, "-i"])
    if shutil.which("xsel"):
        flag = "--primary" if primary else "--clipboard"
        return (["xsel", flag, "-o"], ["xsel", flag, "-i"])
    return None, None


# xclip je hotový v řádu milisekund, jenže na Windows se spouští PowerShell a
# ten studený startuje klidně několik sekund. Se čtyřmi sekundami tam čtení
# schránky padalo na TimeoutExpired a hub z toho hlásil prázdno — naměřeno na
# Windows 11 Pro, kde `clipboard_read()` vracelo None u textu, který na schránce
# prokazatelně byl.
_CLIP_TIMEOUT = 20 if IS_WINDOWS else 4


def clipboard_read(which="clipboard"):
    read_cmd, _ = _clipboard_tools(which)
    if not read_cmd:
        return None
    try:
        r = subprocess.run(read_cmd, capture_output=True, timeout=_CLIP_TIMEOUT,
                           creationflags=_NO_WINDOW)
        text = r.stdout.decode("utf-8", "replace")
        if IS_WINDOWS:
            # Get-Clipboard vrací řádky s CRLF a jeden navíc na konci
            text = text.replace("\r\n", "\n")
            if text.endswith("\n"):
                text = text[:-1]
        return text
    except Exception:
        return None


# Obrázek na schránce (screenshot) je zvláštní případ: `xclip -o` na něm hlásí
# „target STRING not available" a vrátí prázdno, takže Ctrl+V vyzní naprázdno.
# Terminálu se ale obrázek podat nedá — jen cesta k němu. Odložíme si ho tedy
# na disk stejně jako vložený nebo přetažený soubor a vrátíme cestu.
_CLIP_IMAGE_TYPES = (("image/png", ".png"), ("image/jpeg", ".jpg"),
                     ("image/webp", ".webp"), ("image/bmp", ".bmp"))


def _clipboard_image_unix():
    if shutil.which("wl-paste"):
        r = subprocess.run(["wl-paste", "--list-types"],
                           capture_output=True, timeout=4)
        have = r.stdout.decode("utf-8", "replace").split()
        for mime, ext in _CLIP_IMAGE_TYPES:
            if mime in have:
                r = subprocess.run(["wl-paste", "--type", mime],
                                   capture_output=True, timeout=8)
                return r.stdout, ext
        return None, None
    if shutil.which("xclip"):
        r = subprocess.run(["xclip", "-selection", "clipboard", "-t", "TARGETS", "-o"],
                           capture_output=True, timeout=4)
        have = r.stdout.decode("utf-8", "replace").split()
        for mime, ext in _CLIP_IMAGE_TYPES:
            if mime in have:
                r = subprocess.run(["xclip", "-selection", "clipboard", "-t", mime, "-o"],
                                   capture_output=True, timeout=8)
                return r.stdout, ext
    return None, None                      # xsel o cílech neumí říct nic


def _clipboard_image_windows():
    """Snipping Tool a Win+Shift+S nechávají obrázek jen jako bitmapu."""
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        script = ("Add-Type -AssemblyName System.Windows.Forms,System.Drawing; "
                  "$i=[Windows.Forms.Clipboard]::GetImage(); "
                  "if($i){$i.Save('%s',"
                  "[System.Drawing.Imaging.ImageFormat]::Png)}"
                  % tmp.replace("'", "''"))
        # -STA: bez apartmentu pro jedno vlákno schránku Windows nepustí.
        subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", script],
                       capture_output=True, timeout=12, creationflags=_NO_WINDOW)
        if os.path.getsize(tmp):
            with open(tmp, "rb") as fh:
                return fh.read(), ".png"
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return None, None


def _clipboard_image_mac():
    if shutil.which("pngpaste"):
        r = subprocess.run(["pngpaste", "-"], capture_output=True, timeout=8)
        if r.returncode == 0 and r.stdout:
            return r.stdout, ".png"
        return None, None
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        script = ('try\n'
                  '  set d to the clipboard as \u00abclass PNGf\u00bb\n'
                  'on error\n'
                  '  return\n'
                  'end try\n'
                  'set f to open for access POSIX file "%s" with write permission\n'
                  'write d to f\n'
                  'close access f' % tmp)
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)
        if os.path.getsize(tmp):
            with open(tmp, "rb") as fh:
                return fh.read(), ".png"
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return None, None


def clipboard_image():
    """Cesta k obrázku odloženému ze schránky, nebo None, když tam žádný není."""
    try:
        if IS_WINDOWS:
            raw, ext = _clipboard_image_windows()
        elif IS_MAC:
            raw, ext = _clipboard_image_mac()
        else:
            raw, ext = _clipboard_image_unix()
    except Exception:
        return None
    if not raw or len(raw) > MAX_UPLOAD:
        return None
    try:
        return save_upload("schranka" + ext, raw)
    except Exception:
        return None


def clipboard_write(text, which="clipboard"):
    """Zapíše text do schránky. True, když se to povedlo.

    Pod X (a tedy i XWayland) drží obsah výběru **proces, který ho zapsal** —
    `xclip -i` se proto po zápisu odpojí a běží dál, dokud výběr někdo
    nepřepíše. Čekat na jeho konec by znamenalo čekat až do timeoutu a pak
    hlásit neúspěch u zápisu, který ve skutečnosti prošel. `clip` na Windows
    a `pbcopy` na macOS se naopak ukončí hned, takže krátké čekání rozliší
    obojí: TimeoutExpired tady znamená „drží výběr", ne chybu.
    """
    _, write_cmd = _clipboard_tools(which)
    if not write_cmd:
        return False
    try:
        proc = subprocess.Popen(write_cmd, stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                creationflags=_NO_WINDOW)
    except Exception:
        return False
    # clip.exe bere UTF-8 jako OEM znaky; BOM je jediné, čemu uvěří.
    payload = (b"\xff\xfe" + text.encode("utf-16-le") if IS_WINDOWS
               else text.encode("utf-8"))
    # Na Windows a macOS se čeká na skutečný konec procesu (a tedy na skutečný
    # návratový kód); pod X je čekání jen krátké, protože tam `xclip` schválně
    # běží dál a doběhnout nemá proč.
    wait = _CLIP_TIMEOUT if (IS_WINDOWS or IS_MAC) else 1.0
    try:
        proc.communicate(input=payload, timeout=wait)
        return proc.returncode == 0
    except subprocess.TimeoutExpired:
        return True          # drží výběr, jak má
    except Exception:
        return False


def has_clipboard():
    return _clipboard_tools("clipboard")[0] is not None


def installed_skills():
    """Slash commands actually installed in ~/.claude/skills/."""
    try:
        return sorted(n for n in os.listdir(SKILLS_DIR)
                      if os.path.isfile(os.path.join(SKILLS_DIR, n, "SKILL.md")))
    except Exception:
        return []


# ── Terminal command builders ────────────────────────────────────────────────
# Every tab is a bash session, on every platform. Paths are converted to the
# shell's own form so the same snippet works under Git Bash too.
def sh_quote(text):
    """Uzavře řetězec tak, aby ho bash vzal doslova.

    Do příkazu pro tab se skládají cesty ze skenu složek, a ty nemusí být
    nevinné: složka pojmenovaná `projekt$(rm -rf ~)x` — což vznikne třeba
    naklonováním cizího repa — by se v dvojitých uvozovkách vyhodnotila a
    spustila ve chvíli, kdy na ni člověk v panelu klikne. Jednoduché uvozovky
    to zastaví; jediné, co v nich má význam, je apostrof sám.
    """
    return "'" + str(text).replace("'", "'\\''") + "'"


def cmd_project(path, slash=""):
    p = sh_quote(to_shell_path(path))
    w = sh_quote(to_shell_path(WRAPPER))
    arg = f" {sh_quote(slash)}" if slash else ""
    return (f'cd {p} && bash {w}{arg}; '
            f'echo; echo "[ session ukončena — tab zůstává jako shell ]"; exec bash')


def cmd_shell():
    return "exec bash"


def cmd_deploy(path):
    """FTP script when the project has .ftp-deploy.json, otherwise the /deploy skill."""
    if os.path.isfile(os.path.join(path, ".ftp-deploy.json")) and \
            os.path.isfile(FTP_DEPLOY):
        return (f'bash {sh_quote(to_shell_path(FTP_DEPLOY))} '
                f'{sh_quote(to_shell_path(path))}; '
                f'echo; echo "[ deploy hotový — tab zůstává jako shell ]"; exec bash')
    return cmd_project(path, "/deploy")


def link_selftest():
    """Zkusí nanečisto vyrobit odkaz na složku. (jak, detail)

    Na Windows je to jediná odpověď, která něco znamená: symlink tam chce práva
    správce nebo vývojářský režim, křižovatka nechce nic — a co z toho na tomhle
    stroji projde, se nedá uhádnout, jen vyzkoušet.
    """
    import tempfile
    try:
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "cil")
            os.makedirs(target)
            link = os.path.join(tmp, "odkaz")
            how = _make_link(target, link)
            ok = os.path.realpath(link) == os.path.realpath(target)
            return (how if ok else ""), ("" if ok else "odkaz nevede na cíl")
    except Exception as exc:
        return "", str(exc)[:200]


def memory_link_state():
    """Jak je na tom napojení paměti: (stav, detail).

    Hádaná složka je slabé místo na Windows — jméno si Claude Code tvoří sám
    a my ho můžeme leda najít. Když tam ještě není, je poctivější to říct než
    tvářit se, že je napojeno.
    """
    link = memory_link_path()
    parent = os.path.dirname(link)
    if _is_link(link):
        return "napojeno", os.path.realpath(link)
    if os.path.isdir(link):
        return "vlastní složka", link
    if os.path.isdir(parent):
        return "nenapojeno", parent
    return "složka Claude Code zatím není", parent


def browser_profile():
    """Společný profil prohlížeče pro Playwright MCP.

    Musí sedět s tools/playwright_profile.py — tam ho zakládají obě instalačky.
    """
    return os.path.join(CLAUDE_DIR, "browser-profile")


def browser_state():
    """Jak je na tom prohlížeč pro Claude Code: (stav, detail).

    Playwright MCP si bez `--user-data-dir` odvozuje profil z pracovní složky,
    takže každý projekt dostane vlastní prohlížeč a přihlášení (Google a spol.)
    zmizí s přepnutím tabu. Registraci drží ~/.claude.json.
    """
    for path in (CLAUDE_DIR.rstrip("/\\") + ".json",
                 os.path.join(CLAUDE_DIR, ".claude.json")):
        try:
            with open(path, encoding="utf-8-sig") as fh:
                servers = json.load(fh).get("mcpServers") or {}
        except (OSError, ValueError):
            continue
        entry = servers.get("playwright")
        if not entry:
            return "neregistrovaný", "volitelný — přidá ho instalačka"
        args = " ".join(str(a) for a in (entry.get("args") or []))
        if "--user-data-dir" in args:
            return "přihlášení se drží", browser_profile()
        return "profil podle složky", "přihlášení se ztrácí — spusť instalačku znovu"
    return "neregistrovaný", "~/.claude.json se nepodařilo přečíst"


# ── Napojení na cizí služby (MCP) ────────────────────────────────────────────
# Zdrojem pravdy je `claude mcp list`. Jako jediný ví i o konektorech z účtu
# („claude.ai …"), které na disku v žádném souboru nejsou, a rovnou každý server
# zkusí oslovit — takže odpoví i na to, co se jen tváří zaregistrovaně. Stojí to
# jednotky sekund, proto to jede na pozadí a výsledek se drží v úloze "mcp".

# Co se dá přidat jedním klikem. Klíč = jméno serveru u `claude mcp add`.
MCP_CATALOG = {
    "clockify": {
        "label": "Clockify",
        "note": "Výkazy času, projekty a spuštěné stopky přímo z Claude Code.",
        "url": "https://api.clockify.me/mcp-server/mcp",
        "header": "x-api-key",
        "key_label": "API klíč z Clockify",
        "key_help": "Clockify → foto profilu → Preferences → Advanced → "
                    "Manage API keys → Generate New",
        "docs": "https://clockify.me/help/integrations-and-add-ons/"
                "use-clockify-mcp-server-to-connect-to-ai-agent",
    },
}

_MCP_STATES = (
    # (co hledat ve zbytku řádku, stav, český popis)
    ("needs authentication", "auth", "chce přihlásit"),
    ("authentication", "auth", "chce přihlásit"),
    ("failed", "fail", "nepřipojeno"),
    ("connected", "ok", "připojeno"),
)


def _mcp_status(text):
    low = text.lower()
    for needle, state, label in _MCP_STATES:
        if needle in low:
            return state, label
    return "unknown", text.strip("✔✘✗! ").strip() or "neznámý stav"


def mcp_scopes():
    """Odkud se který server registruje — čte se jen z disku, bez sítě.

    `claude mcp list` scope neukazuje, ale právě podle něj se pozná, co si
    můžeme odregistrovat sami (user scope) a co leží v účtu na claude.ai.
    """
    user, project = set(), {}
    for path in (CLAUDE_DIR.rstrip("/\\") + ".json",
                 os.path.join(CLAUDE_DIR, ".claude.json")):
        try:
            with open(path, encoding="utf-8-sig") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        user |= set((data.get("mcpServers") or {}).keys())
        for proj, meta in (data.get("projects") or {}).items():
            if not isinstance(meta, dict):
                continue
            for name in (meta.get("mcpServers") or {}):
                project.setdefault(name, proj)
        break
    return user, project


def mcp_project_files():
    """Servery z `.mcp.json` v projektech — platí jen v té složce.

    V seznamu z hubu (běží z domovské složky) se neobjeví, takže by jinak
    vypadaly jako neexistující. Tady jsou vidět i s tím, kam patří.
    """
    found = []
    for base in PROJECT_DIRS:
        if not os.path.isdir(base):
            continue
        try:
            entries = sorted(os.listdir(base))
        except OSError:
            continue
        for d in entries:
            path = os.path.join(base, d, ".mcp.json")
            if not os.path.isfile(path):
                continue
            try:
                with open(path, encoding="utf-8-sig") as fh:
                    servers = json.load(fh).get("mcpServers") or {}
            except (OSError, ValueError):
                continue
            for name, entry in servers.items():
                found.append({"name": name, "project": os.path.join(base, d),
                              "target": _mcp_target(entry)})
    return found


def _mcp_target(entry):
    """Krátký popis, na co server sahá — URL, nebo příkaz, který se spouští."""
    if not isinstance(entry, dict):
        return ""
    if entry.get("url"):
        return entry["url"]
    argv = [entry.get("command") or ""] + [str(a) for a in (entry.get("args") or [])]
    return " ".join(a for a in argv if a).strip()


def mcp_list():
    """Seznam napojení i s tím, jestli odpovídají. Nikdy nevyhazuje."""
    claude = shutil.which("claude")
    if not claude:
        return {"ok": False, "detail": "Claude Code CLI (claude) není v PATH.",
                "servers": [], "counts": {}}

    # Zdravotní kontrola oslovuje každý server zvlášť; 9 s bývá běžných,
    # minuta je strop, aby se úloha nezasekla na jednom mrtvém serveru.
    out = run([claude, "mcp", "list"], cwd=HOME, timeout=90)
    user, project = mcp_scopes()
    servers = []
    for line in out.split("\n"):
        line = line.strip()
        if not line or ":" not in line or " - " not in line:
            continue          # hlavička „Checking MCP server health…" a prázdné
        name, rest = line.split(":", 1)
        target, _, status_text = rest.rpartition(" - ")
        name = name.strip()
        state, label = _mcp_status(status_text)
        if name.startswith("claude.ai "):
            scope, where = "account", "účet claude.ai"
        elif name in user:
            scope, where = "user", "všechny projekty"
        elif name in project:
            scope, where = "project", project[name]
        else:
            scope, where = "", ""
        servers.append({"name": name, "target": target.strip(),
                        "state": state, "status": label,
                        "scope": scope, "where": where,
                        "removable": scope == "user"})

    seen = {s["name"] for s in servers}
    for entry in mcp_project_files():
        if entry["name"] in seen:
            continue
        servers.append({"name": entry["name"], "target": entry["target"],
                        "state": "local", "status": "jen v tomhle projektu",
                        "scope": "project", "where": entry["project"],
                        "removable": False})

    counts = {"total": len(servers)}
    for s in servers:
        counts[s["state"]] = counts.get(s["state"], 0) + 1
    # Co z katalogu ještě chybí — UI z toho dělá tlačítka „Přidat".
    missing = [k for k in MCP_CATALOG if not any(
        s["name"] == k or s["name"].startswith(k + " ") for s in servers)]
    return {"ok": True, "servers": servers, "counts": counts,
            "available": missing, "checked": time.time()}


def mcp_add(name, api_key=""):
    """Zaregistruje server z katalogu do user scope. Vrací {ok, detail}."""
    spec = MCP_CATALOG.get(name)
    if not spec:
        return {"ok": False, "detail": f"Napojení {name} neznám."}
    claude = shutil.which("claude")
    if not claude:
        return {"ok": False, "detail": "Claude Code CLI (claude) není v PATH."}
    api_key = (api_key or "").strip()
    if spec.get("header") and not api_key:
        return {"ok": False, "detail": "Bez klíče se server nepřihlásí."}
    argv = [claude, "mcp", "add", name, spec["url"],
            "-s", "user", "--transport", "http"]
    if spec.get("header"):
        argv += ["--header", f'{spec["header"]}: {api_key}']
    try:
        r = subprocess.run(argv, capture_output=True, text=True, cwd=HOME,
                           timeout=60, creationflags=_NO_WINDOW)
    except Exception as exc:
        return {"ok": False, "detail": str(exc)[:200]}
    if r.returncode != 0:
        # Klíč do logu nepatří, tak jen to, co řeklo CLI.
        detail = (r.stderr or r.stdout or "").strip()[:300] or "nepovedlo se"
        log(f"MCP {name}: registrace selhala — {detail}", "warn")
        return {"ok": False, "detail": detail}
    log(f"MCP {name}: zaregistrováno (user scope)")
    return {"ok": True, "detail": f"{spec['label']} je napojený."}


def mcp_remove(name):
    """Odregistruje server z user scope."""
    claude = shutil.which("claude")
    if not claude:
        return {"ok": False, "detail": "Claude Code CLI (claude) není v PATH."}
    user, _ = mcp_scopes()
    if name not in user:
        return {"ok": False, "detail": "Tenhle server tu nezaložil hub — "
                                       "odeber ho tam, kde je zapsaný."}
    out = run([claude, "mcp", "remove", name, "-s", "user"], cwd=HOME, timeout=30)
    log(f"MCP {name}: odebráno z user scope")
    return {"ok": True, "detail": out or f"{name} odebrán."}


def doctor():
    """What the hub found on this machine — shown in the UI when something is off."""
    how, detail = link_selftest()
    stav, kde = memory_link_state()
    browser, browser_detail = browser_state()
    # Jen jména z disku — jestli servery opravdu odpovídají, se ptá /api/mcp
    # na pozadí. Tenhle výpis se čte při každém načtení stránky a čekat na síť
    # by znamenalo čekat na každý mrtvý server.
    mcp_user, mcp_project = mcp_scopes()
    return {
        "link": how, "link_error": detail,
        "memory_link": stav, "memory_link_path": kde,
        "browser_mcp": browser, "browser_mcp_detail": browser_detail,
        "mcp_user": sorted(mcp_user),
        "mcp_project": sorted(mcp_project),
        "platform": "windows" if IS_WINDOWS else ("mac" if IS_MAC else "linux"),
        "bash": BASH,
        "git": GIT,
        "claude": shutil.which("claude") or "",
        "wrapper": WRAPPER if os.path.isfile(WRAPPER) else "",
        "ftp_deploy": FTP_DEPLOY if os.path.isfile(FTP_DEPLOY) else "",
        "brain": BRAIN if HAS_BRAIN else "",
        "clipboard": (_clipboard_tools("clipboard")[1] or [""])[0],
        "project_dirs": PROJECT_DIRS,
    }
