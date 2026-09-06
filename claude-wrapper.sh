#!/bin/bash
# Claude Code wrapper — od 2.0.0 už jen zkratka na agent-wrapper.sh.
#
# Boot sekvence i restart po ctrl+c se přestěhovaly do společného wrapperu, aby
# se nemusely udržovat dvakrát. Tenhle soubor zůstal, protože na něj míří starší
# instalace, zástupci a řádek v `--doctor` — spouští se s identitou Claude Code.
# Původní samostatná verze leží v repu v legacy/claude-wrapper.sh.v1.

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export HUB_AGENT_ID="${HUB_AGENT_ID:-claude}"
export HUB_AGENT_BIN="${HUB_AGENT_BIN:-claude}"
export HUB_AGENT_LABEL="${HUB_AGENT_LABEL:-claude code}"
export HUB_AGENT_ANSI="${HUB_AGENT_ANSI:-208}"
export HUB_AGENT_INSTALL="${HUB_AGENT_INSTALL:-curl -fsSL https://claude.ai/install.sh | bash}"
export HUB_AGENT_AUTH="${HUB_AGENT_AUTH:-claude → /login}"
export HUB_AGENT_BRAIN="${HUB_AGENT_BRAIN:-1}"

exec bash "$DIR/agent-wrapper.sh" "$@"
