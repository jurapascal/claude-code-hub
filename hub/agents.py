"""Katalog AI agentů, které hub umí otevřít v tabu.

Do 1.6 uměl hub jediné CLI: `cmd_project()` složil `bash claude-wrapper.sh`
a wrapper spustil `claude`. Tady je to samé, ale pro libovolný nástroj —
jedno místo, které ví, jak se který agent jmenuje, čím se instaluje, jak se
přihlašuje a co umí jeho bublina.

Katalog je zároveň to, co se posílá do UI (stejně jako `MCP_CATALOG` v core),
takže frontend jen renderuje, co dostane, a novým agentem se nic nepřepisuje.

Vlastní CLI se přidává do `~/.claude/hub-config.json`:

    "agents": {"crush": {"label": "Crush", "bin": "crush", "color": "#c084fc"}}

Klíč, který už v katalogu je, se doplní (merge), takže se dá přepsat jen barva
nebo instalační příkaz, ne celý záznam.
"""

import os
import shutil
import subprocess

IS_WINDOWS = os.name == "nt"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WINDOWS else 0

OLLAMA_HOST = os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434"

# Modely se úmyslně nevypisují u agentů, jejichž nabídku neumíme spolehlivě
# znát — u nich chip v bublině zavolá jejich vlastní `/model` a vybírá se
# v jejich TUI. Vypsat sem jména, která se u poskytovatele mění každé dva
# měsíce, by znamenalo nabízet modely, které už neexistují.
CATALOG = {
    "claude": {
        "label": "Claude Code",
        "short": "claude",
        "color": "#d97757",
        "ansi": 208,
        "bin": "claude",
        "note": "Agent od Anthropicu. Jede na předplatném Max/Pro nebo na API klíči.",
        "install": {
            "linux": "curl -fsSL https://claude.ai/install.sh | bash",
            "mac": "curl -fsSL https://claude.ai/install.sh | bash",
            "windows": "winget install --id Anthropic.ClaudeCode",
        },
        "auth": {"cmd": "claude", "slash": "/login",
                 "note": "Spustí se Claude Code, přihlášení je přes /login."},
        "models": [["Opus 5", "opus"], ["Sonnet 5", "sonnet"],
                   ["Haiku 4.5", "haiku"], ["Fable 5", "fable"]],
        "model_arg": "--model {model}",
        "model_cmd": "/model {model}",
        "slash": ["/clear", "/compact", "/context", "/model", "/status",
                  "/resume", "/cost", "/help"],
        "skills": True,      # umí naše ~/.claude/skills
        "composer": "full",  # bublina umí dialogy, režimy i výšku pole
    },
    "codex": {
        "label": "Codex",
        "short": "codex",
        "color": "#10a37f",
        "ansi": 42,
        "bin": "codex",
        "note": "Agent od OpenAI. Jede na předplatném ChatGPT nebo na API klíči.",
        "install": {
            "linux": "npm install -g @openai/codex",
            "mac": "npm install -g @openai/codex",
            "windows": "npm install -g @openai/codex",
        },
        "auth": {"cmd": "codex login",
                 "note": "Přihlášení účtem ChatGPT; na stroji bez prohlížeče "
                         "má codex login kód do jiného zařízení."},
        "models": [],
        "model_arg": "-m {model}",
        "model_cmd": "/model",
        "slash": ["/model", "/approvals", "/new", "/status", "/help"],
        "skills": False,
        "composer": "basic",
    },
    "gemini": {
        "label": "Gemini",
        "short": "gemini",
        "color": "#4285f4",
        "ansi": 33,
        "bin": "gemini",
        "note": "Agent od Googlu. Přihlášení Google účtem nebo klíč z AI Studia.",
        "install": {
            "linux": "npm install -g @google/gemini-cli",
            "mac": "npm install -g @google/gemini-cli",
            "windows": "npm install -g @google/gemini-cli",
        },
        "auth": {"cmd": "gemini", "slash": "/auth",
                 "note": "Spustí se Gemini CLI, účet se vybírá v /auth."},
        "models": [],
        "model_arg": "-m {model}",
        "model_cmd": "/model",
        "slash": ["/auth", "/model", "/clear", "/stats", "/tools",
                  "/memory", "/help"],
        "skills": False,
        "composer": "basic",
    },
    "opencode": {
        "label": "opencode",
        "short": "opencode",
        "color": "#8b5cf6",
        "ansi": 141,
        "bin": "opencode",
        "note": "Otevřený agent, kterému se dá podstrčit jakýkoli model — "
                "včetně lokálního z Ollamy.",
        "install": {
            "linux": "curl -fsSL https://opencode.ai/install | bash",
            "mac": "curl -fsSL https://opencode.ai/install | bash",
            "windows": "npm install -g opencode-ai",
        },
        "auth": {"cmd": "opencode auth login",
                 "note": "Vybere se poskytovatel a vloží klíč. "
                         "Pro lokální modely přes Ollamu není potřeba nic."},
        "models": "ollama",          # k vlastní nabídce přidá lokální modely
        "model_prefix": "ollama/",   # jak opencode pojmenovává modely z Ollamy
        "model_arg": "--model {model}",
        "model_cmd": "/models",
        "slash": ["/models", "/sessions", "/new", "/share", "/help"],
        "skills": False,
        "composer": "basic",
    },
    "aider": {
        "label": "aider",
        "short": "aider",
        "color": "#14b8a6",
        "ansi": 43,
        "bin": "aider",
        "note": "Párové programování v terminálu, pracuje přímo s gitem.",
        "install": {
            "linux": "uv tool install --force --python python3.12 "
                     "--with pip aider-chat@latest",
            "mac": "uv tool install --force --python python3.12 "
                   "--with pip aider-chat@latest",
            "windows": "uv tool install --force --with pip aider-chat@latest",
        },
        "auth": {"env": ["OPENAI_API_KEY", "ANTHROPIC_API_KEY"],
                 "note": "Bere klíč z prostředí, nebo jede na lokálním modelu "
                         "z Ollamy, kde žádný klíč netřeba."},
        "models": "ollama",
        # Dokumentace aidera doporučuje ollama_chat/ místo ollama/ — přes chat
        # endpoint dává lokální model výrazně lepší výsledky.
        "model_prefix": "ollama_chat/",
        "model_arg": "--model {model}",
        "model_cmd": "/model",
        "slash": ["/add", "/drop", "/diff", "/undo", "/model", "/help"],
        "skills": False,
        "composer": "basic",
    },
    "ollama": {
        "label": "Ollama",
        "short": "ollama",
        "color": "#94a3b8",
        "ansi": 245,
        "bin": "ollama",
        "note": "Lokální model přímo v tabu. Neumí sahat na soubory — "
                "je to chat, ne agent.",
        "install": {
            "linux": "curl -fsSL https://ollama.com/install.sh | sh",
            "mac": "brew install ollama",
            "windows": "winget install --id Ollama.Ollama",
        },
        "auth": {"note": "Nic — běží u tebe na počítači."},
        "models": "ollama",
        "needs_model": True,     # `ollama run` bez modelu nedává smysl
        "run_arg": "run {model}",
        "models_only": True,     # nabídka je jen z toho, co je stažené
        "slash": ["/bye", "/clear", "/set", "/show", "/help"],
        "skills": False,
        "composer": "basic",
    },
}

ORDER = ["claude", "codex", "gemini", "opencode", "aider", "ollama"]


def platform_key():
    if IS_WINDOWS:
        return "windows"
    import sys
    return "mac" if sys.platform == "darwin" else "linux"


def catalog(extra=None):
    """Vestavěný katalog plus to, co si člověk dopsal do hub-config.json."""
    out = {k: dict(v) for k, v in CATALOG.items()}
    for key, spec in (extra or {}).items():
        if not isinstance(spec, dict):
            continue
        key = str(key).strip().lower()
        if not key or not key.replace("-", "").replace("_", "").isalnum():
            continue  # jméno agenta jde do jména proměnné i do příkazu
        base = out.get(key, {"label": key, "short": key, "color": "#94a3b8",
                             "bin": key, "composer": "basic", "skills": False,
                             "models": [], "slash": []})
        base.update(spec)
        base.setdefault("bin", key)
        out[key] = base
    return out


def order(cat):
    """Pořadí pro UI: nejdřív ti vestavění, pak vlastní podle abecedy."""
    known = [k for k in ORDER if k in cat]
    return known + sorted(k for k in cat if k not in ORDER)


def resolve(agent_id, extra=None):
    """Záznam agenta i s doplněným id, nebo None."""
    cat = catalog(extra)
    spec = cat.get((agent_id or "").strip().lower())
    if not spec:
        return None
    spec = dict(spec)
    spec["id"] = (agent_id or "").strip().lower()
    return spec


def which(spec):
    return shutil.which(spec.get("bin") or "") or ""


_NPM_PREFIX = None


def npm_user_prefix():
    """Kam smí `npm install -g` psát bez práv správce. Prázdno = stačí systémový.

    Na tomhle typu instalace (node z distribuce, prefix /usr) skončí `npm -g`
    na EACCES a tlačítko Nainstalovat by jen vypsalo chybu. Uživatelský prefix
    to řeší bez sudo — a ~/.local/bin bývá v PATH, takže binárku pak vidíme.
    """
    global _NPM_PREFIX
    if _NPM_PREFIX is not None:
        return _NPM_PREFIX
    _NPM_PREFIX = ""
    npm = shutil.which("npm")
    if npm:
        try:
            r = subprocess.run([npm, "config", "get", "prefix"], capture_output=True,
                               text=True, timeout=6, creationflags=_NO_WINDOW)
            prefix = (r.stdout or "").strip()
            probe = os.path.join(prefix, "lib")
            if prefix and not os.access(probe if os.path.isdir(probe) else prefix,
                                        os.W_OK):
                _NPM_PREFIX = os.path.join(os.path.expanduser("~"), ".local")
        except Exception:
            pass
    return _NPM_PREFIX


def install_cmd(spec):
    inst = spec.get("install") or {}
    cmd = inst if isinstance(inst, str) else (inst.get(platform_key()) or "")
    if cmd.startswith("npm install -g "):
        prefix = npm_user_prefix()
        if prefix:
            cmd = cmd.replace("npm install -g ",
                              f'npm install -g --prefix "{prefix}" ', 1)
    return cmd


def _version(path, spec):
    """Verze agenta, když ji řekne rychle. Prázdno není chyba."""
    if not path:
        return ""
    try:
        r = subprocess.run([path, "--version"], capture_output=True, text=True,
                           timeout=6, creationflags=_NO_WINDOW)
        out = (r.stdout or r.stderr or "").strip().splitlines()
        if not out:
            return ""
        # `codex-cli 0.4.2` i `aider 0.86.1` — chceme jen to číslo
        first = out[0].strip()
        for word in first.split():
            if word[:1].isdigit():
                return word
        return first[:40]
    except Exception:
        return ""


def detect(extra=None, with_version=True):
    """Co je na tomhle stroji doopravdy k dispozici.

    Vrací seznam v pořadí pro UI. `path` prázdné = není v PATH, a UI podle toho
    nabídne instalaci místo otevření tabu.
    """
    cat = catalog(extra)
    out = []
    for key in order(cat):
        spec = cat[key]
        path = which(spec)
        out.append({
            "id": key,
            "label": spec.get("label") or key,
            "short": spec.get("short") or key,
            "color": spec.get("color") or "#94a3b8",
            "note": spec.get("note") or "",
            "bin": spec.get("bin") or key,
            "path": path,
            "version": _version(path, spec) if (path and with_version) else "",
            "install": install_cmd(spec),
            "auth": spec.get("auth") or {},
            "models": models_for(spec),
            "needs_model": bool(spec.get("needs_model")),
            "slash": spec.get("slash") or [],
            "skills": bool(spec.get("skills")),
            "composer": spec.get("composer") or "basic",
            "model_cmd": spec.get("model_cmd") or "",
        })
    return out


# ── Ollama ───────────────────────────────────────────────────────────────────
def ollama_models():
    """Stažené lokální modely. Prázdný seznam = neběží nebo nic stažené."""
    path = shutil.which("ollama")
    if not path:
        return []
    try:
        r = subprocess.run([path, "list"], capture_output=True, text=True,
                           timeout=6, creationflags=_NO_WINDOW)
        names = []
        for line in (r.stdout or "").splitlines()[1:]:   # první řádek je hlavička
            name = line.split()[0] if line.split() else ""
            if name:
                names.append(name)
        return names
    except Exception:
        return []


def ollama_state():
    """(běží, kolik modelů, seznam) — pro nastavení i pro --doctor."""
    path = shutil.which("ollama")
    if not path:
        return {"installed": False, "running": False, "models": []}
    running = False
    try:
        import urllib.request
        with urllib.request.urlopen(OLLAMA_HOST + "/api/tags", timeout=2):
            running = True
    except Exception:
        running = False
    return {"installed": True, "running": running, "models": ollama_models()}


def models_for(spec):
    """Nabídka modelů pro chip v bublině.

    Statický seznam u Claudea (ten známe), lokální modely u všeho, co umí
    Ollamu, a prázdno tam, kde si nabídku drží agent sám — tam chip zavolá
    jeho vlastní `/model` a vybírá se v jeho TUI.
    """
    models = spec.get("models")
    if isinstance(models, list):
        return [list(m) for m in models]
    if models == "ollama":
        prefix = spec.get("model_prefix") or ""
        return [[m, prefix + m] for m in ollama_models()]
    return []


# ── Jak se agent doopravdy spustí ────────────────────────────────────────────
def env_for(spec):
    """HUB_AGENT_* proměnné pro agent-wrapper.sh.

    Wrapper tím pádem nemusí znát katalog ani parsovat JSON — dostane hotovou
    identitu a jen ji vypíše v hlavičce, případně poradí instalaci.
    """
    auth = spec.get("auth") or {}
    hint = auth.get("cmd") or ""
    if hint and auth.get("slash"):
        hint = f'{hint} → {auth["slash"]}'
    return {
        "HUB_AGENT_ID": spec.get("id") or spec.get("bin") or "",
        "HUB_AGENT_BIN": spec.get("bin") or "",
        "HUB_AGENT_LABEL": (spec.get("label") or spec.get("bin") or "").lower(),
        "HUB_AGENT_ANSI": str(spec.get("ansi") or 208),
        "HUB_AGENT_INSTALL": install_cmd(spec),
        "HUB_AGENT_AUTH": hint,
        "HUB_AGENT_BRAIN": "1" if spec.get("skills") else "0",
    }


def launch_args(spec, model="", prompt=""):
    """Argumenty za jméno binárky: volba modelu a případný úvodní prompt."""
    args = []
    if model:
        # `ollama run <model>` je celý příkaz, ne přepínač
        template = spec.get("run_arg") or spec.get("model_arg") or ""
        if template:
            args += [part.replace("{model}", model)
                     for part in template.split(" ") if part]
    elif spec.get("needs_model"):
        # Ollama bez modelu nemá co spustit; ať to řekne rovnou v tabu.
        first = (models_for(spec) or [["", ""]])[0][1]
        if first:
            args += ["run", first]
    if prompt:
        args.append(prompt)
    return args
