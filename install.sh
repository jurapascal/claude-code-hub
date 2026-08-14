#!/bin/bash
# Claude Code Hub — installer
# Nakopíruje appku do ~/.claude, založí konfig, ikonu a položku v nabídce aplikací.
# Nic nepřepíše bez zálohy a nesahá na ~/.claude/settings.json.
set -u

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
ICON_DIR="$HOME/.local/share/icons"
APP_DIR="$HOME/.local/share/applications"
STAMP="$(date +%Y%m%d-%H%M%S)"

A="\033[38;5;208m"; G="\033[38;5;114m"; Y="\033[38;5;180m"; D="\033[2m"; R="\033[0m"
ok()   { echo -e "  ${G}✓${R} $1"; }
info() { echo -e "  ${A}▸${R} $1"; }
warn() { echo -e "  ${Y}⚠${R} $1"; }

echo ""
echo -e "  ${A}✦${R} Claude Code Hub — instalace"
echo -e "  ${D}────────────────────────────────────${R}"

# ── 1. Závislosti ────────────────────────────────────────────────────────────
MISSING=""
command -v python3 >/dev/null 2>&1 || MISSING="$MISSING python3"
python3 -c "import gi; gi.require_version('Gtk','3.0'); gi.require_version('Vte','2.91');
from gi.repository import Gtk, Vte" 2>/dev/null || MISSING="$MISSING python3-gi/gir1.2-vte-2.91"

if [ -n "$MISSING" ]; then
    warn "Chybí:$MISSING"
    echo -e "     ${D}Debian/Ubuntu/Zorin: sudo apt install python3 python3-gi gir1.2-vte-2.91 gir1.2-gtk-3.0${R}"
    echo -e "     ${D}Fedora:              sudo dnf install python3-gobject vte291-gtk3${R}"
    echo -e "     ${D}Arch:                sudo pacman -S python-gobject vte3${R}"
    exit 1
fi
ok "Python 3 + GTK 3 + VTE 2.91"

if command -v claude >/dev/null 2>&1; then
    ok "Claude Code CLI ($(command -v claude))"
else
    warn "Claude Code CLI ('claude') není v PATH — Hub se spustí, ale taby zůstanou v shellu."
    echo -e "     ${D}npm install -g @anthropic-ai/claude-code${R}"
fi

# ── 2. Soubory do ~/.claude ──────────────────────────────────────────────────
mkdir -p "$CLAUDE_DIR/hooks" "$ICON_DIR" "$APP_DIR"

copy() {  # copy <zdroj> <cíl> — existující jiný soubor zazálohuje
    local src="$1" dst="$2"
    if [ -f "$dst" ] && ! cmp -s "$src" "$dst"; then
        cp "$dst" "$dst.backup-$STAMP"
        info "záloha: $(basename "$dst").backup-$STAMP"
    fi
    cp "$src" "$dst"
}

copy "$SRC/claude-hub.py"          "$CLAUDE_DIR/claude-hub.py"
copy "$SRC/claude-wrapper.sh"      "$CLAUDE_DIR/claude-wrapper.sh"
copy "$SRC/hooks/save-session.py"  "$CLAUDE_DIR/hooks/save-session.py"
chmod +x "$CLAUDE_DIR/claude-hub.py" "$CLAUDE_DIR/claude-wrapper.sh" \
         "$CLAUDE_DIR/hooks/save-session.py"
ok "aplikace v $CLAUDE_DIR"

# ── 3. Konfig (nikdy nepřepisovat existující) ────────────────────────────────
if [ -f "$CLAUDE_DIR/hub-config.json" ]; then
    ok "konfig už existuje — nechávám být ($CLAUDE_DIR/hub-config.json)"
else
    cp "$SRC/hub-config.example.json" "$CLAUDE_DIR/hub-config.json"
    ok "konfig založen: $CLAUDE_DIR/hub-config.json"
    info "uprav si v něm 'project_dirs' (kde máš projekty)"
fi

# ── 4. Ikona + položka v nabídce ─────────────────────────────────────────────
cp "$SRC/assets/claude-code.png" "$ICON_DIR/claude-code.png" 2>/dev/null && \
    ok "ikona v $ICON_DIR/claude-code.png"

cat > "$APP_DIR/claude-code-hub.desktop" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Claude Code
Comment=Claude Code Hub — projekty a paměť v jednom okně s taby
Icon=$ICON_DIR/claude-code.png
Terminal=false
Categories=Development;Utility;
Exec=python3 $CLAUDE_DIR/claude-hub.py
StartupNotify=true
StartupWMClass=Claude Code
EOF
chmod +x "$APP_DIR/claude-code-hub.desktop"
update-desktop-database "$APP_DIR" >/dev/null 2>&1
ok "položka v nabídce aplikací: Claude Code"

# ── 5. Hook na ukládání session (jen upozornění, settings.json nesaháme) ─────
if grep -q "save-session.py" "$CLAUDE_DIR/settings.json" 2>/dev/null; then
    ok "Stop hook (save-session.py) je v settings.json zapojený"
else
    warn "Volitelné: přidej si do $CLAUDE_DIR/settings.json blok 'hooks' ze settings.example.json"
fi

echo ""
echo -e "  ${A}✦${R} Hotovo. Spusť: ${D}python3 $CLAUDE_DIR/claude-hub.py${R}  (nebo ikonu Claude Code v nabídce)"
echo -e "  ${D}První spuštění Claude Code: v tabu napiš /login a přihlas se svým účtem.${R}"
echo ""
