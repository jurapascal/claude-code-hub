#!/bin/bash
# Claude Code Hub — instalačka pro Linux a macOS.
#
# Repo je jen instalačka: obsah (projekty, paměť) má každý svůj na disku.
# Skript zjistí, kde ho má, uloží to do ~/.claude/hub-config.json a podle
# toho vyrenderuje aplikaci i slash příkazy. Nic nepřepíše bez zálohy
# a do ~/.claude/settings.json nesahá.
#
# Windows má vlastní install.ps1.
#
# Použití: bash install.sh [--yes]     (--yes = bez otázek, jen detekce)
set -u

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
ICON_DIR="$HOME/.local/share/icons"
APP_DIR="$HOME/.local/share/applications"
STAMP="$(date +%Y%m%d-%H%M%S)"
CONFIG="$CLAUDE_DIR/hub-config.json"

ASSUME_YES=false
[ "${1:-}" = "--yes" ] && ASSUME_YES=true
[ -t 0 ] || ASSUME_YES=true   # bez terminálu se neptáme

A="\033[38;5;208m"; G="\033[38;5;114m"; Y="\033[38;5;180m"; D="\033[2m"; R="\033[0m"
ok()   { echo -e "  ${G}✓${R} $1"; }
info() { echo -e "  ${A}▸${R} $1"; }
warn() { echo -e "  ${Y}⚠${R} $1"; }

echo ""
echo -e "  ${A}✦${R} Claude Code Hub — instalace"
echo -e "  ${D}────────────────────────────────────${R}"

# ── 1. Závislosti ────────────────────────────────────────────────────────────
# Hub je web appka v lokálním okně: stačí Python 3 ze standardní knihovny.
# GTK ani VTE už potřeba nejsou.
if ! command -v python3 >/dev/null 2>&1; then
    warn "Chybí python3"
    echo -e "     ${D}Debian/Ubuntu/Zorin: sudo apt install python3${R}"
    echo -e "     ${D}Fedora:              sudo dnf install python3${R}"
    exit 1
fi
ok "Python 3 ($(python3 --version 2>&1 | cut -d' ' -f2))"

# Okno: chromium v --app režimu, nebo WebKitGTK, jinak zůstane obyčejná záložka.
WINDOW_HOST=""
for b in google-chrome google-chrome-stable chromium chromium-browser \
         brave-browser microsoft-edge vivaldi; do
    command -v "$b" >/dev/null 2>&1 && { WINDOW_HOST="$b"; break; }
done
if [ -n "$WINDOW_HOST" ]; then
    ok "okno: $WINDOW_HOST (--app režim)"
elif python3 -c "
import gi
for gtk, wk, name in (('4.0','6.0','WebKit'), ('3.0','4.1','WebKit2')):
    try:
        gi.require_version('Gtk', gtk); gi.require_version(name, wk)
        __import__('gi.repository', fromlist=['Gtk', name]); raise SystemExit(0)
    except SystemExit: raise
    except Exception: pass
raise SystemExit(1)" 2>/dev/null; then
    ok "okno: WebKitGTK (bez prohlížeče)"
else
    warn "žádný chromium ani WebKitGTK — hub se otevře jako záložka ve výchozím prohlížeči"
    echo -e "     ${D}Nativní okno: sudo apt install gir1.2-webkit2-4.1  (nebo chromium)${R}"
fi

if command -v claude >/dev/null 2>&1; then
    ok "Claude Code CLI ($(command -v claude))"
else
    warn "Claude Code CLI ('claude') není v PATH — Hub se spustí, ale taby zůstanou v shellu."
    echo -e "     ${D}curl -fsSL https://claude.ai/install.sh | bash${R}"
fi

# ── 2. Kde má tenhle počítač co ──────────────────────────────────────────────
detect_project_dirs() {
    local found=""
    for c in "$HOME/Desktop" "$HOME/Plocha" "$HOME/Projects" "$HOME/projects" \
             "$HOME/dev" "$HOME/code" "$HOME/git" "$HOME/src" "$HOME/www" \
             "/opt/lampp/htdocs" "/var/www/html"; do
        [ -d "$c" ] && found="$found${found:+, }$c"
    done
    echo "$found"
}

detect_vault() {
    # Obsidian vault = složka s .obsidian/ nebo s podsložkou memory/
    for c in "$HOME/Obsidian"/*/ "$HOME/Documents/Obsidian"/*/ "$HOME/obsidian"/*/; do
        [ -d "$c" ] || continue
        if [ -d "${c}.obsidian" ] || [ -d "${c}memory" ]; then
            echo "${c%/}"; return
        fi
    done
    echo ""
}

if [ -f "$CONFIG" ]; then
    ok "konfig už existuje — nechávám ho být ($CONFIG)"
else
    PROJECT_DIRS_CSV="$(detect_project_dirs)"
    VAULT="$(detect_vault)"

    if ! $ASSUME_YES; then
        echo ""
        info "Kde máš projekty? ${D}(čárkou oddělený seznam)${R}"
        read -r -p "     [${PROJECT_DIRS_CSV}]: " ANSWER
        [ -n "$ANSWER" ] && PROJECT_DIRS_CSV="$ANSWER"

        info "Obsidian vault s pamětí? ${D}(Enter = nechat prázdné, paměť se vypne)${R}"
        read -r -p "     [${VAULT}]: " ANSWER
        [ -n "$ANSWER" ] && VAULT="$ANSWER"
        echo ""
    fi

    python3 - "$CONFIG" "$PROJECT_DIRS_CSV" "$VAULT" "$ICON_DIR/claude-code.png" \
             "$CLAUDE_DIR/ftp-deploy.sh" <<'PYEOF'
import json, os, sys
cfg_path, dirs_csv, vault, icon, ftp = sys.argv[1:6]
dirs = [d.strip() for d in dirs_csv.split(",") if d.strip()]
cfg = {
    "project_dirs": dirs or ["~/Desktop", "~/Projects"],
    "brain_dir": vault.strip() or "~/Obsidian/Claude-Brain",
    "icon": icon,
    "ftp_deploy_script": ftp,
}
os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
with open(cfg_path, "w", encoding="utf-8") as fh:
    json.dump(cfg, fh, ensure_ascii=False, indent=2)
    fh.write("\n")
PYEOF
    ok "konfig zapsán: $CONFIG"
fi

VAULT="$(python3 -c "
import json, os, sys
try:
    cfg = json.load(open(sys.argv[1]))
except Exception:
    cfg = {}
print(os.path.expanduser(cfg.get('brain_dir') or ''))" "$CONFIG" 2>/dev/null)"
MEMORY_DIR="$VAULT/memory"
BRAIN_SKILLS="$VAULT/skills"
HAS_VAULT=false
[ -n "$VAULT" ] && [ -d "$MEMORY_DIR" ] && HAS_VAULT=true

if $HAS_VAULT; then
    ok "paměť: $MEMORY_DIR"
else
    info "bez Obsidian vaultu — paměťové příkazy a panel paměti se přeskočí"
fi

# ── 3. Aplikace do ~/.claude ─────────────────────────────────────────────────
mkdir -p "$CLAUDE_DIR/hooks" "$CLAUDE_DIR/skills" "$ICON_DIR" "$APP_DIR"

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
# hub/ je celý náš — nahrazuje se vcelku, aby po updatu nezůstaly staré soubory
rm -rf "$CLAUDE_DIR/hub"
cp -r "$SRC/hub" "$CLAUDE_DIR/hub"
find "$CLAUDE_DIR/hub" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null
chmod +x "$CLAUDE_DIR/claude-hub.py" "$CLAUDE_DIR/claude-wrapper.sh" \
         "$CLAUDE_DIR/hooks/save-session.py"
ok "aplikace v $CLAUDE_DIR"

# ── 4. Slash příkazy ─────────────────────────────────────────────────────────
# Claude Code ≥ 2.1 čte vlastní příkazy z ~/.claude/skills/<jméno>/SKILL.md
# (složka ~/.claude/commands/ se v tomhle buildu ignoruje). Šablony ze skills/
# se sem vyrenderují s cestami z konfigu.
STATE_FILE="$CLAUDE_DIR/session-state.md"
$HAS_VAULT && STATE_FILE="$MEMORY_DIR/session-state.md"
VAULT_ONLY=" save learn project skill "   # bez vaultu nedávají smysl
INSTALLED=""

for dir in "$SRC"/skills/*/; do
    name="$(basename "$dir")"
    [ -f "$dir/SKILL.md" ] || continue
    if ! $HAS_VAULT && [[ "$VAULT_ONLY" == *" $name "* ]]; then
        continue
    fi
    mkdir -p "$CLAUDE_DIR/skills/$name"
    sed -e "s|{{MEMORY_DIR}}|$MEMORY_DIR|g" \
        -e "s|{{SKILLS_DIR}}|$BRAIN_SKILLS|g" \
        -e "s|{{CLAUDE_DIR}}|$CLAUDE_DIR|g" \
        -e "s|{{FTP_DEPLOY}}|$CLAUDE_DIR/ftp-deploy.sh|g" \
        -e "s|{{STATE_FILE}}|$STATE_FILE|g" \
        -e "s|{{PYTHON}}|python3|g" \
        "$dir/SKILL.md" > "$CLAUDE_DIR/skills/$name/SKILL.md"
    INSTALLED="$INSTALLED /$name"
done
ok "slash příkazy:$INSTALLED"

if ls "$CLAUDE_DIR"/commands/*.md >/dev/null 2>&1; then
    warn "$CLAUDE_DIR/commands/ tenhle build Claude Code nečte — příkazy teď běží ze skills/"
fi

# ── 5. Ikona + položka v nabídce ─────────────────────────────────────────────
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

# ── 6. Hook na ukládání session (settings.json nesaháme) ─────────────────────
if grep -q "save-session.py" "$CLAUDE_DIR/settings.json" 2>/dev/null; then
    ok "Stop hook (save-session.py) je v settings.json zapojený"
else
    warn "Volitelné: přidej si do $CLAUDE_DIR/settings.json blok 'hooks' ze settings.example.json"
fi

# ── 7. Playwright MCP (volitelné) ────────────────────────────────────────────
# Prohlížeč pro Claude Code — klikání, konzole a screenshoty přes accessibility
# tree místo vlastních Puppeteer skriptů. Registruje se do user scope
# (~/.claude.json), takže platí ve všech projektech. Bez zeptání se neinstaluje:
# poprvé stahuje ~115 MB prohlížeče.
add_playwright_mcp() {
    # --browser chromium = bundlovaný Chromium z ~/.cache/ms-playwright;
    # výchozí kanál "chrome" by chtěl systémový Google Chrome.
    if ! claude mcp add playwright -s user -- \
            npx @playwright/mcp@latest --browser chromium >/dev/null 2>&1; then
        warn "'claude mcp add playwright' selhalo — přidej si ho ručně"
        return 1
    fi
    ok "playwright MCP zaregistrován (user scope)"

    # Verze prohlížeče se váže na verzi MCP serveru; bez tohohle kroku vrací
    # první browser_navigate "Browser chrome-for-testing is not installed".
    info "stahuju prohlížeč (~115 MB, stahuje se jen co chybí)…"
    if npx -y @playwright/mcp@latest install-browser chrome-for-testing >/dev/null 2>&1; then
        ok "prohlížeč připraven v ~/.cache/ms-playwright"
    else
        warn "prohlížeč se nestáhl — dožeň to: npx @playwright/mcp@latest install-browser chrome-for-testing"
    fi
}

NODE_MAJOR="$(node -v 2>/dev/null | sed 's/^v//; s/\..*//')"
if ! command -v claude >/dev/null 2>&1; then
    info "Playwright MCP přeskočen — chybí Claude Code CLI"
elif claude mcp get playwright >/dev/null 2>&1; then
    ok "playwright MCP už je zaregistrovaný"
elif [ -z "$NODE_MAJOR" ] || [ "$NODE_MAJOR" -lt 20 ]; then
    warn "Playwright MCP přeskočen — chce Node.js 20+ (teď: ${NODE_MAJOR:-žádný})"
elif $ASSUME_YES; then
    info "Playwright MCP přeskočen (--yes) — přidáš ho: claude mcp add playwright -s user -- npx @playwright/mcp@latest --browser chromium"
else
    echo ""
    info "Přidat Playwright MCP? ${D}(prohlížeč pro Claude Code, stáhne ~115 MB)${R}"
    read -r -p "     [a/N]: " ANSWER
    case "$ANSWER" in
        [aAyY]*) add_playwright_mcp ;;
        *) info "přeskočeno — kdykoli později: claude mcp add playwright -s user -- npx @playwright/mcp@latest --browser chromium" ;;
    esac
fi

echo ""
echo -e "  ${A}✦${R} Hotovo. Spusť: ${D}python3 $CLAUDE_DIR/claude-hub.py${R}  (nebo ikonu Claude Code v nabídce)"
echo -e "  ${D}Kontrola prostředí:  python3 $CLAUDE_DIR/claude-hub.py --doctor${R}"
echo -e "  ${D}První spuštění Claude Code: v tabu napiš /login a přihlas se svým účtem.${R}"
echo ""
