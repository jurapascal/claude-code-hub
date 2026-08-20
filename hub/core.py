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
# Only keep folders that actually exist — a stock config lists several candidates.
PROJECT_DIRS = [p for p in (os.path.expanduser(d) for d in CONFIG["project_dirs"])
                if os.path.isdir(p)]
HAS_BRAIN = os.path.isdir(MEMORY_DIR)  # no vault → the whole memory UI stays hidden

# Hide the console window Windows would otherwise flash for every git call.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WINDOWS else 0


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


def child_env():
    env = dict(os.environ)
    env["TERM"] = "xterm-256color"
    env["COLORTERM"] = "truecolor"
    env["CHERE_INVOKING"] = "1"
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
        "project_dirs": PROJECT_DIRS,
    }
