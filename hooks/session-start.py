#!/usr/bin/env python3
"""
SessionStart hook: hand Claude the two things it cannot find on its own at the
start of a session — which skills the Obsidian vault holds, and where the last
session left off.

Prints one JSON object on stdout, the shape Claude Code expects from a hook.
Anything that goes wrong is swallowed: a broken hook must never be the reason a
session refuses to start, so the worst case is an empty additionalContext.

Paths come from ~/.claude/hub-config.json (brain_dir), same as the rest of the
hub; without a config it falls back to ~/Obsidian/Claude-Brain.
"""
import json
import os

HOME = os.path.expanduser("~")
CLAUDE_DIR = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(HOME, ".claude")
CONFIG_PATH = os.path.join(CLAUDE_DIR, "hub-config.json")

# A whole session-state file plus a long category list can crowd out the actual
# conversation, so the state gets a ceiling and loses its oldest part first.
MAX_STATE_CHARS = 12000


def brain_dir():
    try:
        # utf-8-sig: PowerShell 5.1 likes to leave a BOM behind
        with open(CONFIG_PATH, encoding="utf-8-sig") as fh:
            configured = json.load(fh).get("brain_dir")
        if configured:
            return os.path.expanduser(configured)
    except Exception:
        pass
    return os.path.join(HOME, "Obsidian", "Claude-Brain")


def skill_categories(brain):
    """Category folder names under <vault>/skills — not the skills themselves.

    A vault can hold several hundred skills; listing them all would spend the
    context window on a menu. The categories are enough for Claude to know what
    is there and go read the one it needs.
    """
    root = os.path.join(brain, "skills")
    try:
        return [d for d in sorted(os.listdir(root))
                if not d.startswith("_") and os.path.isdir(os.path.join(root, d))]
    except Exception:
        return []


def session_state(brain):
    path = os.path.join(brain, "memory", "session-state.md")
    if not os.path.isfile(path):
        path = os.path.join(CLAUDE_DIR, "session-state.md")
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except Exception:
        return ""
    if len(text) > MAX_STATE_CHARS:
        text = "…(zkráceno)…\n" + text[-MAX_STATE_CHARS:]
    return text


def main():
    parts = []
    try:
        brain = brain_dir()
        cats = skill_categories(brain)
        if cats:
            parts.append("Available skill categories: " + ", ".join(cats))
            parts.append(f"Use skills from {brain}/skills/<category>/<skill>/"
                         "SKILL.md when relevant.")
        state = session_state(brain)
        if state.strip():
            parts.append("\n--- PREVIOUS SESSION STATE ---")
            parts.append(state)
    except Exception:
        parts = []
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": "\n".join(parts),
    }}))


if __name__ == "__main__":
    main()
