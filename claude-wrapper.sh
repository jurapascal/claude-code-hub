#!/bin/bash
# Claude Code wrapper
# - Shows a boot sequence (project, optional Obsidian Brain, git, hooks)
# - Restarts after an accidental ctrl+c
#
# Paths come from ~/.claude/hub-config.json (see hub-config.example.json).
# Everything Obsidian-related is optional — without a vault those steps are skipped.

CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
CONFIG="$CLAUDE_DIR/hub-config.json"

# brain_dir from the config, default ~/Obsidian/Claude-Brain
BRAIN=$(python3 - "$CONFIG" <<'PYEOF' 2>/dev/null
import json, os, sys
try:
    cfg = json.load(open(sys.argv[1]))
except Exception:
    cfg = {}
print(os.path.expanduser(cfg.get("brain_dir") or "~/Obsidian/Claude-Brain"))
PYEOF
)
[ -z "$BRAIN" ] && BRAIN="$HOME/Obsidian/Claude-Brain"

# ── Claude Code Colors ──
A="\033[38;5;208m"  # amber (primary)
G="\033[38;5;114m"  # soft green
Y="\033[38;5;180m"  # warm peach
D="\033[2m"         # dim
B="\033[1m"         # bold
W="\033[38;5;252m"  # cream
R="\033[0m"         # reset

# ── Boot Sequence ──
boot_sequence() {
    clear
    echo ""
    echo -e "  ${B}${A}✦${R}  ${B}${W}claude code${R}"
    echo -e "  ${D}──────────────────────────────────────${R}"
    echo ""
    echo -e "  ${A}▸${R} ${W}$(basename "$(pwd)")${R}"
    echo -e "    ${D}$(pwd)${R}"
    echo ""

    # Loading animation helper
    show_step() {
        local msg="$1"
        echo -ne "  ${D}  ${msg}...${R}"
    }
    done_step() {
        local msg="$1"
        local detail="$2"
        echo -e "\r  ${A}✦${R} ${W}${msg}${R} ${D}${detail}${R}      "
    }

    # 1. Skills + 2. Memory — only when an Obsidian Brain vault is present
    if [ -d "$BRAIN" ]; then
        show_step "Loading Obsidian Brain skills"
        SKILL_COUNT=$(find "$BRAIN/skills" -name "SKILL.md" 2>/dev/null | wc -l)
        CAT_COUNT=$(find "$BRAIN/skills" -maxdepth 1 -type d ! -name "_*" ! -name "skills" 2>/dev/null | wc -l)
        done_step "Skills loaded" "(${SKILL_COUNT} skills, ${CAT_COUNT} categories)"

        show_step "Reading memory"
        MEM_DIR="$BRAIN/memory"
        NOTE_COUNT=$(find "$MEM_DIR" -maxdepth 1 -name '*.md' ! -name 'MEMORY.md' ! -name 'session-state.md' 2>/dev/null | wc -l)
        LEARN_COUNT=$(find "$MEM_DIR" -maxdepth 1 -name 'learning_*.md' 2>/dev/null | wc -l)
        ERR_COUNT=$(find "$MEM_DIR" -maxdepth 1 -name 'error_*.md' 2>/dev/null | wc -l)
        done_step "Memory loaded" "(${NOTE_COUNT} notes • ${LEARN_COUNT} learnings, ${ERR_COUNT} errors)"

        # 3. Session state
        show_step "Loading session state"
        if [ -f "$MEM_DIR/session-state.md" ]; then
            LAST_DATE=$(grep "^\- \*\*Datum\*\*:" "$MEM_DIR/session-state.md" 2>/dev/null | head -1 | sed 's/.*: //')
            done_step "Session state" "(last: ${LAST_DATE})"
        else
            done_step "Session state" "(no previous session)"
        fi
    fi

    # 4. Git status
    show_step "Checking git status"
    if [ -d .git ]; then
        BRANCH=$(git branch --show-current 2>/dev/null)
        CHANGES=$(git status --porcelain 2>/dev/null | wc -l)
        if [ "$CHANGES" -gt 0 ]; then
            done_step "Git: ${Y}${BRANCH}${R}" "${Y}${CHANGES} uncommitted changes${R}"
        else
            done_step "Git: ${G}${BRANCH}${R}" "clean"
        fi
    else
        done_step "Git" "(not a git repo)"
    fi

    # 5. Hooks
    show_step "Initializing hooks"
    HOOK_COUNT=0
    [ -f "$CLAUDE_DIR/settings.json" ] && HOOK_COUNT=$(python3 -c "
import json, sys
try:
    d = json.load(open(sys.argv[1]))
    print(sum(len(v) for v in d.get('hooks', {}).values()))
except Exception:
    print(0)
" "$CLAUDE_DIR/settings.json" 2>/dev/null || echo 0)
    done_step "Hooks ready" "(${HOOK_COUNT} active)"

    echo ""
    echo -e "  ${D}──────────────────────────────────────${R}"
    echo -e "  ${A}✦${R} ${W}ready${R}"
    echo ""
    sleep 0.3
}

# ── Missing CLI → tell the user instead of failing silently ──
if ! command -v claude >/dev/null 2>&1; then
    echo ""
    echo -e "  ${Y}⚠ Claude Code CLI ('claude') nenalezeno v PATH${R}"
    echo -e "  ${D}Nainstaluj: npm install -g @anthropic-ai/claude-code${R}"
    echo -e "  ${D}Pak se přihlas svým účtem: claude  →  /login${R}"
    echo ""
    exec bash
fi

# ── Main Loop ──
FIRST_RUN=true

while true; do
    if $FIRST_RUN; then
        boot_sequence
        FIRST_RUN=false
    fi

    claude "$@"
    EXIT_CODE=$?

    # Exit code 130 = SIGINT (ctrl+c)
    if [ $EXIT_CODE -eq 130 ] || [ $EXIT_CODE -eq 2 ]; then
        echo ""
        echo -e "  ${Y}⚠ interrupted${R}"
        echo -e "  ${D}restarting in 2s... ctrl+c again to quit${R}"
        echo ""

        trap "echo '  Bye!'; exit 0" INT
        sleep 2
        trap - INT

        echo -e "  ${A}✦${R} ${W}restarting...${R}"
        echo ""
        continue
    fi

    break
done
