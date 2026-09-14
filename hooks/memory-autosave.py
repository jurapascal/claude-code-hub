#!/usr/bin/env python3
"""
Paměť se ukládá sama — bez /save, bez /project a bez ptaní.

Dokud to bylo tlačítko, uložilo se jen to, na co si člověk vzpomněl. Tenhle
skript to dělá za něj: když session ztichne, skončí nebo se zavře její tab,
poskládá z přepisu konverzace výtah (co se chtělo, co se udělalo, které
soubory a commity) a pustí na pozadí Claude Code bez okna, který podle něj
doplní poznámku k projektu a případně jeden poznatek, chybu nebo úspěch.

Kdy se spouští:

    Stop hook         zapíše, že session žije, a pohlídá, až ztichne
                      (výchozí 20 min) — pak uloží
    SessionEnd hook   /exit, /clear, Ctrl+D → uloží hned
    --tab <tag>       hub zavřel tab (nebo celé okno) → uloží session z tabu
    --watch <id>      (interní) čeká na ticho
    --run <id>        (interní) uloží hned

Co hlídá, aby to nebyla otrava ani díra do rozpočtu:
- drobnost (otázka a odpověď, samotné /save) se neukládá vůbec — model se
  ani nevolá;
- každý kus konverzace se zpracuje jednou: pamatuje se, kam až se došlo;
- podřízený Claude běží bez hooků, bez MCP, bez uložené session, s nástroji
  jen na čtení a zápis souborů a zapisovat smí jen do složky paměti;
- vypnout jde v ⚙ → Paměť (`memory_autosave: false` v hub-config.json).

Stav leží v ~/.claude/hub-autosave/, výsledky v log.jsonl — z něj hub bere
hlášku „Paměť doplněna".
"""
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HOME = os.path.expanduser("~")
CLAUDE_DIR = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(HOME, ".claude")
CONFIG_PATH = os.path.join(CLAUDE_DIR, "hub-config.json")
STATE_DIR = os.path.join(CLAUDE_DIR, "hub-autosave")
LOG_PATH = os.path.join(STATE_DIR, "log.jsonl")

CHILD_FLAG = "HUB_AUTOSAVE_CHILD"   # podřízený Claude — jeho vlastní konec neukládat
POLL = 60                           # jak často se hlídač probudí
IDLE_MIN = 20                       # výchozí ticho, po kterém se ukládá
DIGEST_CHARS = 60000                # strop výtahu, ať je cena předvídatelná
RUN_TIMEOUT = 900
LOCK_STALE = 20 * 60
KEEP_DAYS = 30

IS_WINDOWS = os.name == "nt"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WINDOWS else 0


# ── konfigurace ──────────────────────────────────────────────────────────────
def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8-sig") as fh:   # BOM: PowerShell 5.1
            return json.load(fh)
    except Exception:
        return {}


def memory_dir(cfg):
    brain = os.path.expanduser(cfg.get("brain_dir") or "~/Obsidian/Claude-Brain")
    path = os.path.join(brain, "memory")
    return os.path.realpath(path) if os.path.isdir(path) else ""


def enabled(cfg):
    return cfg.get("memory_autosave", True) is not False and bool(memory_dir(cfg))


def idle_seconds(cfg):
    try:
        return max(1.0, float(cfg.get("memory_autosave_idle", IDLE_MIN))) * 60
    except (TypeError, ValueError):
        return IDLE_MIN * 60


# ── stav jedné session ───────────────────────────────────────────────────────
def safe_id(value):
    """Id session jde do jména souboru — nic jiného než písmena, čísla, pomlčky."""
    value = str(value or "")
    return value if re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value) else ""


def state_path(sid, suffix):
    return os.path.join(STATE_DIR, f"{sid}{suffix}")


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {} if default is None else default


def write_json(path, data):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    os.replace(tmp, path)


def log_event(entry):
    entry = {"at": datetime.datetime.now().isoformat(timespec="seconds"), **entry}
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        if os.path.getsize(LOG_PATH) > 200_000:
            with open(LOG_PATH, encoding="utf-8") as fh:
                lines = fh.readlines()[-300:]
            write_lines(LOG_PATH, lines)
    except OSError:
        pass


def write_lines(path, lines):
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.writelines(lines)
    os.replace(tmp, path)


def acquire(sid):
    path = state_path(sid, ".lock")
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return True
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(path) < LOCK_STALE:
                    return False
                os.remove(path)          # po spadlém běhu — zkusit znovu
            except OSError:
                return False
        except OSError:
            return False
    return False


def release(sid):
    try:
        os.remove(state_path(sid, ".lock"))
    except OSError:
        pass


def cleanup():
    """Stav session starších než měsíc už k ničemu není."""
    limit = time.time() - KEEP_DAYS * 86400
    try:
        for name in os.listdir(STATE_DIR):
            path = os.path.join(STATE_DIR, name)
            if name != "log.jsonl" and os.path.getmtime(path) < limit:
                os.remove(path)
    except OSError:
        pass


# ── spouštění na pozadí ──────────────────────────────────────────────────────
def spawn(*args):
    """Pustí tenhle skript odpojený: hook ani hub na něj nečekají.

    Nová session (Unix) / odpojený proces (Windows) je nutnost, ne ozdoba —
    zavřený tab pošle SIGHUP celé skupině procesů a zabil by i ukládání.
    """
    python = sys.executable or "python3"
    if IS_WINDOWS:
        quiet = os.path.join(os.path.dirname(python), "pythonw.exe")
        if os.path.isfile(quiet):
            python = quiet
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
              "stderr": subprocess.DEVNULL, "close_fds": True, "cwd": HOME}
    if IS_WINDOWS:
        kwargs["creationflags"] = (subprocess.DETACHED_PROCESS |
                                   subprocess.CREATE_NEW_PROCESS_GROUP)
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen([python, os.path.abspath(__file__), *args], **kwargs)
    except OSError:
        pass


# ── hook ─────────────────────────────────────────────────────────────────────
def hook():
    if os.environ.get(CHILD_FLAG):
        return
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return
    cfg = load_config()
    if not enabled(cfg):
        return
    sid = safe_id(data.get("session_id"))
    transcript = data.get("transcript_path") or ""
    if not sid or not os.path.isfile(transcript):
        return
    info = read_json(state_path(sid, ".json"))
    info.update({"session": sid, "transcript": transcript,
                 "cwd": data.get("cwd") or info.get("cwd") or "",
                 "last": time.time()})
    if os.environ.get("HUB_TAB"):
        info["tab"] = os.environ["HUB_TAB"]
    write_json(state_path(sid, ".json"), info)

    if data.get("hook_event_name") == "SessionEnd":
        spawn("--run", sid, "konec session")
        return
    beat = state_path(sid, ".watch")
    try:
        alive = time.time() - os.path.getmtime(beat) < POLL * 3
    except OSError:
        alive = False
    if not alive:
        touch(beat)                      # ať dva rychlé Stopy nepustí dva hlídače
        spawn("--watch", sid)


def touch(path):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(str(time.time()))
    except OSError:
        pass


def pending(sid):
    """(je co ukládat, jak dlouho je session potichu)"""
    info = read_json(state_path(sid, ".json"))
    saved = read_json(state_path(sid, ".saved.json"))
    try:
        size = os.path.getsize(info.get("transcript") or "")
    except OSError:
        return False, 0
    done = max(saved.get("offset", 0), saved.get("checked", 0))
    return size > done, time.time() - float(info.get("last") or 0)


def watch(sid):
    cfg = load_config()
    idle = idle_seconds(cfg)
    beat = state_path(sid, ".watch")
    give_up = time.time() + 12 * 3600
    try:
        while time.time() < give_up:
            touch(beat)
            todo, quiet = pending(sid)
            if quiet >= idle:
                if not todo:
                    return
                run(sid, "ticho")
                # Mezitím mohla přibýt další práce — nebo běh držel zámek
                # někdo jiný (zavřený tab). Tak znovu, ale ne naprázdno dokola.
                time.sleep(POLL)
                continue
            time.sleep(max(5, min(POLL, idle - quiet + 1)))
    finally:
        try:
            os.remove(beat)
        except OSError:
            pass


def closed_tabs(tags):
    """Hub zavřel tab: počkat, až Claude doběhne a dopíše přepis, pak uložit."""
    time.sleep(4)
    wanted = set(tags)
    try:
        names = os.listdir(STATE_DIR)
    except OSError:
        return
    for name in names:
        if not name.endswith(".json") or name.endswith(".saved.json"):
            continue
        info = read_json(os.path.join(STATE_DIR, name))
        if info.get("tab") in wanted and safe_id(info.get("session")):
            run(info["session"], "zavřený tab")


# ── výtah z přepisu ──────────────────────────────────────────────────────────
REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
TAG_BLOCK_RE = re.compile(
    r"<(local-command-stdout|local-command-stderr|task-notification|"
    r"command-message)>.*?</\1>", re.S)
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


def clean_user(text):
    if not isinstance(text, str):
        return ""
    text = REMINDER_RE.sub("", text)
    cmd = re.search(r"<command-name>(.*?)</command-name>", text, re.S)
    if cmd:
        args = re.search(r"<command-args>(.*?)</command-args>", text, re.S)
        return (cmd.group(1).strip() + " " + (args.group(1).strip() if args else "")).strip()
    text = TAG_BLOCK_RE.sub("", text).strip()
    if text.startswith("Caveat:") or text.startswith("[Request interrupted"):
        return ""
    return text


def commit_subject(command):
    m = re.search(r"git\s+commit[^\n]*?-m\s+(['\"])(.+?)\1", command, re.S)
    if m:
        return m.group(2).strip().splitlines()[0]
    lines = command.splitlines()
    for i, line in enumerate(lines):
        if "git commit" in line and "<<" in line:
            for body in lines[i + 1:]:
                if body.strip() and body.strip() not in ("EOF", "'EOF'"):
                    return body.strip()
    return ""


def git_root(path, stop):
    path = os.path.realpath(path)
    if os.path.isfile(path) or not os.path.exists(path):
        path = os.path.dirname(path)
    while path and path not in stop:
        if os.path.isdir(os.path.join(path, ".git")):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return ""


def inside(path, folder):
    try:
        path, folder = os.path.realpath(path), os.path.realpath(folder)
        return os.path.commonpath([path, folder]) == folder
    except ValueError:
        return False


def clip(text, limit):
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + " …"


def build_digest(transcript, start, mem):
    with open(transcript, "rb") as fh:
        fh.seek(start)
        raw = fh.read()
    cut = raw.rfind(b"\n")
    if cut < 0:
        return None
    end = start + cut + 1
    events, edited, commits, mem_files = [], {}, [], set()
    tools = user_msgs = 0
    session_cwd = ""
    skip_dirs = [mem, CLAUDE_DIR, tempfile.gettempdir(), "/tmp"]

    def note_file(path):
        if not path:
            return
        if inside(path, mem):
            mem_files.add(os.path.basename(path))
        elif not any(inside(path, d) for d in skip_dirs[1:]):
            edited[path] = edited.get(path, 0) + 1

    for line in raw[:cut + 1].splitlines():
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("isSidechain"):
            continue
        kind = d.get("type")
        session_cwd = d.get("cwd") or session_cwd
        msg = d.get("message") or {}
        content = msg.get("content")
        if kind == "user" and not d.get("isMeta"):
            texts = [content] if isinstance(content, str) else [
                b.get("text", "") for b in content or [] if b.get("type") == "text"]
            for text in texts:
                text = clean_user(text)
                if text:
                    events.append(["USER", clip(text, 3000)])
                    user_msgs += 1
        elif kind == "attachment":
            att = d.get("attachment") or {}
            if att.get("type") == "queued_command" and \
                    att.get("commandMode") != "task-notification":
                text = clean_user(att.get("prompt"))
                if text:
                    events.append(["USER", clip(text, 3000)])
                    user_msgs += 1
        elif kind == "summary" and d.get("summary"):
            events.append(["SHRNUTÍ", clip(str(d["summary"]), 2000)])
        elif kind == "assistant" and isinstance(content, list):
            for block in content:
                btype = block.get("type")
                if btype == "text" and block.get("text", "").strip():
                    events.append(["CLAUDE", clip(block["text"], 4000)])
                elif btype == "tool_use":
                    tools += 1
                    name, inp = block.get("name"), block.get("input") or {}
                    if name in EDIT_TOOLS:
                        note_file(inp.get("file_path") or inp.get("notebook_path"))
                    elif name == "Bash":
                        command = str(inp.get("command") or "")
                        first = command.strip().splitlines()[0] if command.strip() else ""
                        if first:
                            events.append(["BASH", clip(first, 140)])
                        if "git commit" in command:
                            subject = commit_subject(command)
                            commits.append((d.get("cwd") or session_cwd, subject))
                            if subject:
                                events.append(["COMMIT", subject])
                    elif name == "Skill":
                        events.append(["SKILL", str(inp.get("skill") or "")])

    stop = {HOME, os.path.dirname(HOME), "/"}
    counts = {}
    for path, n in edited.items():
        root = git_root(path, stop)
        if root:
            counts[root] = counts.get(root, 0) + n
    for cwd, _ in commits:
        root = git_root(cwd, stop) if cwd else ""
        if root:
            counts[root] = counts.get(root, 0) + 3
    if session_cwd and tools:
        root = git_root(session_cwd, stop)
        if root and not inside(root, mem):
            counts.setdefault(root, 0)
    # Vault sám projekt není, i když je to git repo s pamětí uvnitř.
    brain = os.path.dirname(mem)
    projects = [r for r, _ in sorted(counts.items(), key=lambda kv: -kv[1])
                if not inside(r, brain) and not inside(mem, r)
                and not inside(r, CLAUDE_DIR)][:4]

    work = len(commits) + len(edited)
    return {
        "end": end,
        "text": render_events(events),
        "projects": projects,
        "edited": sorted(edited),
        "commits": [s for _, s in commits if s],
        "mem_files": sorted(mem_files),
        "cwd": session_cwd,
        # Drobnost se neukládá: dotaz s odpovědí nebo samotné /save.
        "substantive": work > 0 or (tools >= 8 and user_msgs >= 1),
    }


def render_events(events):
    """Výtah pod strop znaků: nejdřív pryč staré příkazy, pak staré texty."""
    def total():
        return sum(len(k) + len(t) + 4 for k, t in events)
    i = 0
    while total() > DIGEST_CHARS and i < len(events):
        if events[i][0] == "BASH":
            events.pop(i)
        else:
            i += 1
    while total() > DIGEST_CHARS and len(events) > 20:
        # Začátek (zadání) a konec (výsledek) jsou nejcennější — ubírá se střed.
        events.pop(len(events) // 3)
    out, prev = [], None
    for kind, text in events:
        if kind == "BASH" and prev == text:
            continue
        prev = text
        out.append(f"[{kind}] {text}")
    return "\n".join(out)


# ── fakta o projektu ─────────────────────────────────────────────────────────
def git(root, *args):
    try:
        return subprocess.run(["git", "-C", root, *args], capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=10, creationflags=_NO_WINDOW).stdout.strip()
    except Exception:
        return ""


def slugify(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "projekt"


def short(path):
    return "~" + path[len(HOME):] if path.startswith(HOME + os.sep) else path


def find_project_note(mem, slug, root):
    try:
        notes = [n for n in os.listdir(mem) if n.startswith("project_") and n.endswith(".md")]
    except OSError:
        return ""
    if f"project_{slug}.md" in notes:
        return f"project_{slug}.md"
    for pattern in (short(root), root):
        for name in notes:
            try:
                with open(os.path.join(mem, name), encoding="utf-8", errors="replace") as fh:
                    if pattern in fh.read(20000):
                        return name
            except OSError:
                continue
    return ""


def project_facts(root, mem):
    top = set()
    try:
        top = set(os.listdir(root))
    except OSError:
        pass
    stack = []
    if "composer.json" in top or any(n.endswith(".php") for n in top):
        stack.append("PHP")
    if {"sections", "templates"} <= top:
        stack.append("Shopify theme")
    if "package.json" in top:
        stack.append("Node")
    if {"pyproject.toml", "requirements.txt"} & top or any(n.endswith(".py") for n in top):
        stack.append("Python")
    remote = git(root, "remote", "get-url", "origin")
    slug = slugify(os.path.basename(root))
    return {
        "name": os.path.basename(root),
        "path": short(root),
        "slug": slug,
        "note": find_project_note(mem, slug, root),
        "stack": ", ".join(stack) or "neznámý",
        "git_remote": remote,
        "branch": git(root, "branch", "--show-current"),
        "deploy": "FTP (.ftp-deploy.json)" if ".ftp-deploy.json" in top
                  else ("git" if remote else "neznámý"),
        "uncommitted": len([ln for ln in git(root, "status", "--porcelain").splitlines() if ln]),
        "recent_commits": git(root, "log", "-6", "--date=short", "--format=%h %ad %s"),
    }


# ── podřízený Claude ─────────────────────────────────────────────────────────
PROMPT = """You keep the user's Obsidian memory up to date. The current working
directory IS the memory folder. A Claude Code session has just {reason}. Below
are facts about the projects it touched and a digest of the conversation.
Nobody will read your reply — only the files you write matter.

Today is {today}.

## Rules
1. Write notes in Czech. One note = one file with YAML frontmatter:
   `name` (= filename without .md), `description` (one Czech line, ~100 chars,
   used for recall), `metadata:` → `type:` (project | reference | feedback | user).
   Body uses [[wikilinks]] to related notes and ends with `**Souvisí:** [[...]]`.
2. PROJECT NOTE — for every project under "Projekty": if it has a note, update
   it: add a dated entry at the top of `## Historie` (create the section if
   missing) saying what changed and why, in 2–6 lines; tick finished items and
   add new ones in `## TODO`. Keep everything else intact. If it has no note,
   create `project_<slug>.md` with **Stack:**, **Hosting:**, **Deploy:**,
   **Git:**, **Složka:**, `## Cíl`, `## Historie`, `## TODO` — only from the facts
   and the digest, never guessed (write "neznámé" instead).
3. INSIGHT — only if the session produced something worth knowing next time
   (a non-obvious bug and its cause, a reusable technique, a notable success),
   write or update ONE note: `error_<slug>.md`, `learning_<slug>.md` or
   `win_<slug>.md` (type: reference), with **Kontext / Detail / Poučení**.
   Routine work gets no extra note.
4. Never duplicate: Glob/Grep for an existing note first and update it instead.
   Notes already written for this session are listed below — update those.
5. For every NEW file add one line to MEMORY.md under the matching section
   (`## Projekty a reference`, `## Poznatky`, `## Chyby`, `## Úspěchy`):
   `- [Title](file.md) — hook`. Do not otherwise rewrite MEMORY.md.
6. Do not touch session-state.md, hub-postup.md or anything outside this folder.
   Never write secrets (tokens, passwords, API keys, private URLs with tokens).
7. If nothing is worth remembering, change nothing.
8. Your final line must be exactly `HUB-AUTOSAVE: <comma-separated file names
   you created or changed>` or `HUB-AUTOSAVE: nic`.

## Projekty
{projects}

## Poznámky už zapsané v této session
{notes}

## Upravené soubory mimo paměť
{edited}

## Výtah z konverzace
Lines: [USER] what the user wrote, [CLAUDE] Claude's replies, [BASH] commands
run, [COMMIT] commit subjects, [SKILL] skills used.

{digest}
"""


def find_claude():
    found = shutil.which("claude")
    if found:
        return found
    for cand in (os.path.join(HOME, ".local", "bin", "claude"),
                 os.path.join(HOME, ".claude", "local", "claude"),
                 os.path.join(HOME, ".local", "bin", "claude.exe")):
        if os.path.isfile(cand):
            return cand
    return ""


def child_settings():
    """Nastavení pro podřízeného Clauda jako soubor.

    Jako JSON v argumentu by ho na Windows rozbilo přeuvozovkování přes
    claude.cmd. `disableAllHooks` je podstatné: bez něj by jeho konec spustil
    Stop hooky — tenhle skript, stav session i cizí hooky (Clockify…).
    """
    path = os.path.join(STATE_DIR, "child-settings.json")
    write_json(path, {"disableAllHooks": True})
    return path


def ask_claude(prompt, mem, cfg):
    exe = find_claude()
    if not exe:
        return False, "claude není v PATH", [], 0.0
    cmd = [exe, "-p", "--model", str(cfg.get("memory_autosave_model") or "sonnet"),
           "--no-session-persistence", "--strict-mcp-config",
           "--settings", child_settings(),
           "--permission-mode", "acceptEdits",
           "--tools", "Read,Write,Edit,Glob,Grep",
           "--max-budget-usd", "3",
           "--output-format", "json"]
    env = dict(os.environ)
    env[CHILD_FLAG] = "1"
    for var in ("HUB_TAB", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"):
        env.pop(var, None)
    try:
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", cwd=mem, env=env,
                              timeout=RUN_TIMEOUT, creationflags=_NO_WINDOW)
    except subprocess.TimeoutExpired:
        return False, "vypršel čas", [], 0.0
    except OSError as exc:
        return False, str(exc), [], 0.0
    try:
        out = json.loads(proc.stdout)
    except Exception:
        return False, clip(proc.stderr or proc.stdout or f"exit {proc.returncode}", 300), [], 0.0
    result = str(out.get("result") or "")
    cost = float(out.get("total_cost_usd") or 0)
    if out.get("is_error"):
        return False, clip(result or "chyba", 300), [], cost
    files = []
    for line in reversed(result.strip().splitlines()):
        if line.strip().startswith("HUB-AUTOSAVE:"):
            tail = line.split(":", 1)[1].strip()
            if tail and tail.lower() != "nic":
                files = [f.strip().strip("`") for f in tail.split(",") if f.strip()]
            break
    return True, "", files, cost


def run(sid, reason="uložení"):
    sid = safe_id(sid)
    cfg = load_config()
    if not sid or not enabled(cfg) or not acquire(sid):
        return
    try:
        cleanup()
        mem = memory_dir(cfg)
        info = read_json(state_path(sid, ".json"))
        saved = read_json(state_path(sid, ".saved.json"))
        transcript = info.get("transcript") or ""
        try:
            size = os.path.getsize(transcript)
        except OSError:
            return
        offset = int(saved.get("offset", 0))
        if size <= max(offset, int(saved.get("checked", 0))):
            return
        digest = build_digest(transcript, offset, mem)
        if not digest:
            return
        if not digest["substantive"]:
            saved["checked"] = digest["end"]
            write_json(state_path(sid, ".saved.json"), saved)
            return
        projects = [project_facts(root, mem) for root in digest["projects"]]
        names = [p["name"] for p in projects]
        written = sorted(set(saved.get("notes", [])) | set(digest["mem_files"]))
        prompt = PROMPT.format(
            reason={"ticho": "gone quiet", "zavřený tab": "been closed"}.get(
                reason, "ended"),
            today=datetime.date.today().isoformat(),
            projects="\n".join(json.dumps(p, ensure_ascii=False) for p in projects)
                     or "(žádný git projekt — ulož jen případný poznatek)",
            notes=", ".join(written) or "(žádné)",
            edited="\n".join(short(p) for p in digest["edited"][:60]) or "(žádné)",
            digest=digest["text"] or "(prázdné)")
        started = time.time()
        ok, detail, files, cost = ask_claude(prompt, mem, cfg)
        saved["checked"] = digest["end"]
        if ok:
            saved["offset"] = digest["end"]
            saved["notes"] = sorted(set(saved.get("notes", [])) | set(files))
        saved["at"] = time.time()
        write_json(state_path(sid, ".saved.json"), saved)
        log_event({"session": sid, "reason": reason, "projects": names,
                   "status": ("saved" if files else "nothing") if ok else "error",
                   "files": files, "detail": detail,
                   "cost": round(cost, 3), "seconds": round(time.time() - started)})
    finally:
        release(sid)


def main(argv):
    if len(argv) >= 2 and argv[0] == "--watch":
        return watch(safe_id(argv[1]))
    if len(argv) >= 2 and argv[0] == "--run":
        return run(argv[1], argv[2] if len(argv) > 2 else "uložení")
    if len(argv) >= 2 and argv[0] == "--tab":
        return closed_tabs(argv[1:])
    return hook()


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except Exception:
        pass            # hook nikdy nesmí shodit session; stav si zapíše příště
