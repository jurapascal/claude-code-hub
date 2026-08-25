#!/bin/bash
# Claude Code Hub — instalačka pro Linux a macOS.
#
# Repo je jen instalačka: obsah (projekty, paměť) má každý svůj na disku.
# Skript zjistí, kde ho má, uloží to do ~/.claude/hub-config.json a podle
# toho vyrenderuje aplikaci i slash příkazy. Nic nepřepíše bez zálohy
# a do ~/.claude/settings.json nesahá.
#
# Ve výchozím režimu doinstaluje všechno, co k hubu patří (Obsidian, GitHub CLI,
# Playwright MCP, hooky) a ptá se jen na to, co uhádnout nejde: kde máš paměť,
# jestli chceš bypass režim a jestli se má hned přihlásit.
#
# Windows má vlastní install.ps1.
#
# Použití: bash install.sh [--yes] [--minimal]
#     --yes      bez otázek — doinstaluje, co jde, přihlášení a bypass přeskočí
#     --minimal  jen hub: nic nedoinstalovává, do settings.json nesahá
set -u

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
ICON_DIR="$HOME/.local/share/icons"
APP_DIR="$HOME/.local/share/applications"
STAMP="$(date +%Y%m%d-%H%M%S)"
CLONED_VAULT=""
CONFIG="$CLAUDE_DIR/hub-config.json"

ASSUME_YES=false
MINIMAL=false
for arg in "$@"; do
    case "$arg" in
        --yes) ASSUME_YES=true ;;
        --minimal) MINIMAL=true ;;
    esac
done
# Přes `curl … | bash` je na stdin skript, ne klávesnice. Terminál uživatele
# je pořád na /dev/tty, takže se dá ptát dál.
# Zkouška v subshellu: existence /dev/tty nestačí (bez řídicího terminálu se
# otevřít nedá) a chybu neúspěšného `exec <` už nejde umlčet po faktu.
if [ ! -t 0 ] && (exec < /dev/tty) 2>/dev/null; then exec < /dev/tty; fi
[ -t 0 ] || ASSUME_YES=true   # opravdu bez terminálu se neptáme

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

# Diakritika: tab, který naběhne pod LANG=C, ořeže každý znak s háčkem. Hub si
# UTF-8 locale dopočítá sám (hub/core.py), tady jde jen o to říct, když ho
# systém nemá vůbec z čeho vzít.
case "${LC_ALL:-${LANG:-}}" in
    *UTF-8*|*utf8*|*UTF8*|*utf-8*) ok "locale: ${LC_ALL:-$LANG}" ;;
    *)
        if locale -a 2>/dev/null | grep -qiE '(^|\.)(c|C)\.utf-?8$|utf-?8$'; then
            info "locale je ${LC_ALL:-${LANG:-nenastavené}} — hub si pro taby vynutí UTF-8 sám"
        else
            warn "na stroji není žádné UTF-8 locale — čeština se bude v terminálu lámat"
            echo -e "     ${D}Debian/Ubuntu: sudo locale-gen cs_CZ.UTF-8 && sudo update-locale${R}"
        fi
        ;;
esac

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

# ── 2. Obsidian a GitHub CLI ─────────────────────────────────────────────────
# Hub běží i bez obojího, ale bez Obsidianu nemá paměť kde bydlet (/save, /learn,
# /project) a bez `gh` se z čerstvého stroje nedá klonovat ani pushovat. Nikdy
# neinstalujeme potichu: s --yes se jen vypíše, co si má člověk doinstalovat.
ask() {
    $ASSUME_YES && return 1
    local answer
    read -r -p "     $1 [a/N]: " answer
    [[ "$answer" =~ ^[aAyY] ]]
}

have_obsidian() {
    command -v obsidian >/dev/null 2>&1 && return 0
    flatpak info md.obsidian.Obsidian >/dev/null 2>&1 && return 0
    snap list obsidian >/dev/null 2>&1 && return 0
    [ -d /Applications/Obsidian.app ] && return 0
    ls "$HOME"/Applications/Obsidian*.AppImage >/dev/null 2>&1 && return 0
    return 1
}

install_obsidian() {
    if [ "$(uname)" = "Darwin" ]; then
        command -v brew >/dev/null 2>&1 && brew install --cask obsidian && return 0
    elif command -v flatpak >/dev/null 2>&1; then
        flatpak remote-add --if-not-exists --user \
            flathub https://flathub.org/repo/flathub.flatpakrepo >/dev/null 2>&1
        flatpak install -y --user flathub md.obsidian.Obsidian && return 0
    elif command -v snap >/dev/null 2>&1; then
        sudo snap install obsidian --classic && return 0
    fi
    return 1
}

install_clipboard() {
    # Na Wayland sedí wl-clipboard, pod X xclip; obojí je malé, tak ať je co je.
    local pkgs="wl-clipboard xclip"
    [ "${XDG_SESSION_TYPE:-}" = "x11" ] && pkgs="xclip wl-clipboard"
    for pkg in $pkgs; do
        if command -v apt-get >/dev/null 2>&1 && apt-get install -s "$pkg" >/dev/null 2>&1; then
            sudo apt-get install -y "$pkg" && return 0
        elif command -v dnf >/dev/null 2>&1; then
            sudo dnf install -y "$pkg" && return 0
        elif command -v pacman >/dev/null 2>&1; then
            sudo pacman -S --noconfirm "$pkg" && return 0
        fi
    done
    return 1
}

install_gh() {
    if [ "$(uname)" = "Darwin" ]; then
        command -v brew >/dev/null 2>&1 && brew install gh && return 0
    # `apt-get install -s` je simulace: běží bez roota a nezávisí na jazyku výpisu
    elif command -v apt-get >/dev/null 2>&1 && apt-get install -s gh >/dev/null 2>&1; then
        sudo apt-get install -y gh && return 0
    elif command -v dnf >/dev/null 2>&1; then
        sudo dnf install -y gh && return 0
    elif command -v pacman >/dev/null 2>&1; then
        sudo pacman -S --noconfirm github-cli && return 0
    elif command -v snap >/dev/null 2>&1; then
        sudo snap install gh && return 0
    fi
    return 1
}

clone_vault() {  # clone_vault <owner/repo|URL> <rodičovská složka> → $CLONED_VAULT
    local repo="$1" parent="$2" name target
    name="$(basename "${repo%.git}")"
    target="$parent/$name"
    [ -d "$target" ] && { warn "$target už existuje"; return 1; }
    mkdir -p "$parent"
    # gh umí i privátní repo, na které má přihlášený účet přístup jako kolaborátor
    if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
        gh repo clone "$repo" "$target" >/dev/null 2>&1 || return 1
    else
        case "$repo" in
            *://*|git@*) git clone "$repo" "$target" >/dev/null 2>&1 || return 1 ;;
            *) git clone "https://github.com/$repo.git" "$target" >/dev/null 2>&1 || return 1 ;;
        esac
    fi
    CLONED_VAULT="$target"
    # vault z gitu nemusí mít memory/ (třeba když se commitovalo jen skills/)
    [ -d "$target/memory" ] || create_vault "$target"
    return 0
}

create_vault() {  # create_vault <cesta> — prázdný vault, ať má paměť kam psát
    mkdir -p "$1/memory" "$1/skills" "$1/.obsidian"
    if [ ! -f "$1/memory/MEMORY.md" ] && [ -f "$SRC/assets/vault/MEMORY.md" ]; then
        cp "$SRC/assets/vault/MEMORY.md" "$1/memory/MEMORY.md"
    fi
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

echo ""
if $MINIMAL; then
    info "--minimal: Obsidian ani gh nedoinstalovávám"
else
    # Doinstalovává se bez ptaní: obojí k hubu patří (paměť, push) a otázka
    # navíc byla hlavní důvod, proč instalace nebyla „rychlá".
    if have_obsidian; then
        ok "Obsidian"
    else
        info "doinstalovávám Obsidian ${D}(paměť: /save, /learn, /project)${R}"
        if install_obsidian >/dev/null 2>&1; then ok "Obsidian nainstalován"
        else warn "nevyšlo — stáhni ho z https://obsidian.md/download"; fi
    fi

    # Bez tohohle se v tabu nedá kopírovat: WebKitGTK stránku ke schránce
    # nepustí, takže na ni sahá server hubu — a potřebuje k tomu nástroj.
    if [ "$(uname)" != "Darwin" ] && ! command -v wl-copy >/dev/null 2>&1 \
            && ! command -v xclip >/dev/null 2>&1 && ! command -v xsel >/dev/null 2>&1; then
        info "doinstalovávám nástroj na schránku ${D}(kopírování v tabu)${R}"
        if install_clipboard >/dev/null 2>&1; then ok "schránka připravena"
        else warn "nevyšlo — doinstaluj ručně: apt install wl-clipboard  (nebo xclip)"; fi
    fi

    if command -v gh >/dev/null 2>&1; then
        ok "GitHub CLI ($(command -v gh))"
    else
        info "doinstalovávám GitHub CLI ${D}(klonování a push)${R}"
        if install_gh >/dev/null 2>&1; then ok "gh nainstalován"
        else warn "nevyšlo — návod: https://github.com/cli/cli#installation"; fi
    fi
fi
if command -v gh >/dev/null 2>&1 && ! gh auth status >/dev/null 2>&1; then
    info "gh není přihlášený k GitHubu"
    if ask "Přihlásit se teď (otevře prohlížeč)?"; then
        gh auth login || warn "přihlášení nedoběhlo — kdykoli později: gh auth login"
        gh auth setup-git >/dev/null 2>&1 && ok "git umí přes gh i push přes HTTPS"
    else
        echo -e "     ${D}Později: gh auth login${R}"
    fi
elif command -v gh >/dev/null 2>&1; then
    ok "gh přihlášený ($(gh api user --jq .login 2>/dev/null || echo 'ok'))"
fi

# Vault až po gh: cizí stroj si tvůj vault naklonuje z privátního repa, a na to
# musí být `gh` napřed přihlášený.
VAULT_FOUND="$(detect_vault)"
if [ -n "$VAULT_FOUND" ]; then
    ok "vault: $VAULT_FOUND"
elif $ASSUME_YES; then
    info "bez vaultu — paměť zůstane vypnutá (spusť instalačku bez --yes a založíš ho)"
else
    echo ""
    info "Máš paměť (Obsidian vault) v gitu? ${D}owner/repo nebo URL — Enter = nemám${R}"
    read -r -p "     []: " VAULT_REPO
    if [ -n "$VAULT_REPO" ]; then
        if clone_vault "$VAULT_REPO" "$HOME/Obsidian"; then
            ok "vault naklonován: $CLONED_VAULT  ${D}(v Obsidianu: Open folder as vault)${R}"
        else
            warn "klonování nevyšlo — ověř přístup: gh repo view $VAULT_REPO"
        fi
    elif ask "Založit prázdný vault $HOME/Obsidian/Claude-Brain pro paměť?"; then
        create_vault "$HOME/Obsidian/Claude-Brain"
        ok "vault založen: $HOME/Obsidian/Claude-Brain  ${D}(v Obsidianu: Open folder as vault)${R}"
    else
        info "bez vaultu poběží hub taky, jen bez paměti"
    fi
fi

# ── 3. Kde má tenhle počítač co ──────────────────────────────────────────────
detect_project_dirs() {
    local found=""
    for c in "$HOME/Desktop" "$HOME/Plocha" "$HOME/Projects" "$HOME/projects" \
             "$HOME/dev" "$HOME/code" "$HOME/git" "$HOME/src" "$HOME/www" \
             "/opt/lampp/htdocs" "/var/www/html"; do
        [ -d "$c" ] && found="$found${found:+, }$c"
    done
    echo "$found"
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

# Co skutečně stojí v konfigu (i když ho instalačka teď nepsala) — šablony
# skillů to potřebují, aby /newsletter a spol. hledaly projekty na správném místě.
PROJECT_DIRS_LIST="$(python3 -c "
import json, os, sys
try:
    cfg = json.load(open(sys.argv[1]))
except Exception:
    cfg = {}
dirs = [os.path.expanduser(d) for d in cfg.get('project_dirs') or []]
print(', '.join(d for d in dirs if os.path.isdir(d)))" "$CONFIG" 2>/dev/null)"

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

# ── 4. Aplikace do ~/.claude ─────────────────────────────────────────────────
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
copy "$SRC/hooks/session-start.py" "$CLAUDE_DIR/hooks/session-start.py"
# hub/ a tools/ jsou celé naše — nahrazují se vcelku, aby po updatu nezůstaly
# staré soubory. tools/ potřebuje sekce 7 (merge settings.json) a je fajn ho mít
# po ruce i bez repa (memory_index_trim.py).
rm -rf "$CLAUDE_DIR/hub" "$CLAUDE_DIR/tools"
cp -r "$SRC/hub" "$CLAUDE_DIR/hub"
cp -r "$SRC/tools" "$CLAUDE_DIR/tools"
find "$CLAUDE_DIR/hub" "$CLAUDE_DIR/tools" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null
chmod +x "$CLAUDE_DIR/claude-hub.py" "$CLAUDE_DIR/claude-wrapper.sh" \
         "$CLAUDE_DIR/hooks/save-session.py" "$CLAUDE_DIR/hooks/session-start.py"
ok "aplikace v $CLAUDE_DIR"

# ── 5. Slash příkazy ─────────────────────────────────────────────────────────
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
        -e "s|{{PROJECT_DIRS}}|$PROJECT_DIRS_LIST|g" \
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

# ── 6. Ikona + položka v nabídce ─────────────────────────────────────────────
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

# ── 7. Hooky a režim oprávnění v settings.json ───────────────────────────────
# settings.json je uživatelův (klíče, model, vlastní hooky), takže se do něj
# nesází šablona — tools/settings_merge.py přidá jen to, co chybí, a předtím
# udělá zálohu. Vlastního hooku na stejnou událost se nedotkne.
BYPASS_FLAG=""
if ! $MINIMAL && ! $ASSUME_YES; then
    echo ""
    info "Zapnout ${A}bypass režim${R}? ${D}Claude pak nebude ptát na potvrzení u každého${R}"
    echo -e "     ${D}příkazu a úpravy souboru — rychlejší práce, ale běží bez brzdy.${R}"
    echo -e "     ${D}Zapni jen na vlastním stroji, kde víš, co ti Claude spouští.${R}"
    echo -e "     ${D}Kdykoli později: /permissions v Claude Code.${R}"
    ask "Zapnout bypass režim?" && BYPASS_FLAG="--bypass"
fi

if $MINIMAL; then
    info "--minimal: do settings.json nesahám"
else
    python3 "$CLAUDE_DIR/tools/settings_merge.py" --claude-dir "$CLAUDE_DIR" \
        --python python3 --hooks $BYPASS_FLAG 2>&1 | while read -r line; do
            case "$line" in
                chyba:*)  warn "${line#chyba: }" ;;
                *"nechávám být"*|*"beze změny"*|*"už "*) ok "$line" ;;
                *"vlastní hook"*) warn "$line" ;;
                *) info "$line" ;;
            esac
        done
fi

# ── 8. Playwright MCP (volitelné) ────────────────────────────────────────────
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
echo ""
if $MINIMAL; then
    info "--minimal: Playwright MCP přeskočen"
elif ! command -v claude >/dev/null 2>&1; then
    info "Playwright MCP přeskočen — chybí Claude Code CLI"
elif claude mcp get playwright >/dev/null 2>&1; then
    ok "playwright MCP už je zaregistrovaný"
elif [ -z "$NODE_MAJOR" ] || [ "$NODE_MAJOR" -lt 20 ]; then
    warn "Playwright MCP přeskočen — chce Node.js 20+ (teď: ${NODE_MAJOR:-žádný})"
    echo -e "     ${D}Doinstaluj Node 20+ a spusť instalačku znovu.${R}"
else
    add_playwright_mcp
fi

# ── 9. Přihlášení do Claude Code ─────────────────────────────────────────────
# Bez přihlášení se v každém tabu objeví login obrazovka. Spustit `claude` tady
# je nejrychlejší cesta: uživatel projde /login a dá /exit.
if command -v claude >/dev/null 2>&1; then
    if [ -f "$CLAUDE_DIR/.credentials.json" ] || [ -n "${ANTHROPIC_API_KEY:-}" ]; then
        ok "Claude Code je přihlášený"
    elif ask "Přihlásit se teď do Claude Code? (spustí claude, projdi /login a dej /exit)"; then
        claude || true
    else
        echo -e "     ${D}Později: spusť claude a napiš /login${R}"
    fi
fi

# Závěrečná kontrola: jeden výpis, ze kterého je vidět, co na stroji opravdu je.
python3 "$CLAUDE_DIR/claude-hub.py" --doctor || true

echo ""
echo -e "  ${A}✦${R} Hotovo. Spusť: ${D}python3 $CLAUDE_DIR/claude-hub.py${R}  (nebo ikonu Claude Code v nabídce)"
echo -e "  ${D}Kontrola prostředí:  python3 $CLAUDE_DIR/claude-hub.py --doctor${R}"
echo -e "  ${D}Když okno zůstane prázdné, důvod je v $CLAUDE_DIR/hub.log${R}"
echo ""
