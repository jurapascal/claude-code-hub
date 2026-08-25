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
import shutil
import subprocess
import sys

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


def log(message):
    """Append one line to ~/.claude/hub.log.

    The Windows shortcut runs pythonw.exe, which has no console at all — without
    this a failed start leaves nothing behind to look at.
    """
    try:
        import datetime
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S}  {message}\n")
    except Exception:
        pass


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
def open_path(path):
    """Open a folder, file or URI with the system default handler."""
    try:
        if IS_WINDOWS:
            if "://" in path:
                subprocess.Popen(["cmd", "/c", "start", "", path], shell=False,
                                 creationflags=_NO_WINDOW)
            else:
                os.startfile(path)  # noqa: S606 — the whole point of this function
        elif IS_MAC:
            subprocess.Popen(["open", path])
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
            branch, dirty_count = "", 0
            if is_git and GIT:
                branch = run([GIT, "branch", "--show-current"], cwd=path)
                status = run([GIT, "status", "--porcelain"], cwd=path)
                dirty_count = len(status.split("\n")) if status else 0
            projects.append({
                "name": d, "path": path, "type": ptype,
                "branch": branch, "dirty": dirty_count,
                "deployable": os.path.isfile(os.path.join(path, ".ftp-deploy.json")),
            })
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
        return (["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
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


def clipboard_read(which="clipboard"):
    read_cmd, _ = _clipboard_tools(which)
    if not read_cmd:
        return None
    try:
        r = subprocess.run(read_cmd, capture_output=True, timeout=4,
                           creationflags=_NO_WINDOW)
        return r.stdout.decode("utf-8", "replace")
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
    try:
        proc.communicate(input=text.encode("utf-8"), timeout=1.0)
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
def cmd_project(path, slash=""):
    p = to_shell_path(path)
    w = to_shell_path(WRAPPER)
    arg = f' "{slash}"' if slash else ""
    return (f'cd "{p}" && bash "{w}"{arg}; '
            f'echo; echo "[ session ukončena — tab zůstává jako shell ]"; exec bash')


def cmd_shell():
    return "exec bash"


def cmd_deploy(path):
    """FTP script when the project has .ftp-deploy.json, otherwise the /deploy skill."""
    if os.path.isfile(os.path.join(path, ".ftp-deploy.json")) and \
            os.path.isfile(FTP_DEPLOY):
        return (f'bash "{to_shell_path(FTP_DEPLOY)}" "{to_shell_path(path)}"; '
                f'echo; echo "[ deploy hotový — tab zůstává jako shell ]"; exec bash')
    return cmd_project(path, "/deploy")


def doctor():
    """What the hub found on this machine — shown in the UI when something is off."""
    return {
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
