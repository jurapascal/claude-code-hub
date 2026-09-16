#!/bin/bash
# Claude Code Hub — brána na nový server (Ubuntu 24.04).
#
# Postaví server pro víc lidí od nuly: balíčky, Node a Claude Code, uživatele
# `hub`, izolaci (bwrap + AppArmor), bránu jako službu, nginx s HTTPS, firewall,
# fail2ban, swap, bezpečnostní aktualizace systému a noční aktualizaci hubu
# (gateway/update.sh). Jde pouštět znovu: co je hotové, nechá být, a zdroj hubu
# aktualizuje.
#
# Nastavuje totéž, co ručně běželo na prvním serveru (WEDOS, 9/2026), včetně
# pastí, které to tam stálo:
#   * Claude Code musí ležet v /usr (npm -g z NodeSource). Sandbox vidí ze
#     systému jen /usr a /etc — instalace do ~/.local/bin by v prostoru nebyla.
#   * Ubuntu 24.04 zakazuje neprivilegované user namespaces
#     (apparmor_restrict_unprivileged_userns=1) a bwrap bez vlastního profilu
#     padá na „setting up uid map: Permission denied".
#   * Brána běží jako UŽIVATELSKÁ služba (systemd --user + linger). Jinak
#     `systemd-run --user --scope` nemá kde zakládat prostory s limity paměti,
#     a bez dbus-user-session nemá `systemctl --user` sběrnici.
#
# Použití (jako root):
#     bash gateway/install.sh --domain test.alba-rosa.cz \
#         [--email ja@firma.cz] [--admin jmeno@firma.cz] [--ref v2.4.1] \
#         [--swap 4G] [--no-tls]
#
#     --domain   adresa brány; A záznam musí mířit na tenhle server
#     --email    kontakt pro Let's Encrypt (upozornění na vypršení certifikátu)
#     --admin    založí prvního správce (heslo se zeptá)
#     --ref      větev nebo značka hubu, výchozí main
#     --swap     velikost swapu, když žádný není (výchozí 2G)
#     --no-tls   certifikát neřešit (HTTPS terminuje něco před serverem)
#     --no-restart  bránu nerestartovat ani po změně (běžící prostory zůstanou)
#
# Na čistém serveru bez repa stačí stáhnout jen tenhle skript:
#     curl -fsSLO https://raw.githubusercontent.com/jurapascal/claude-code-hub/main/gateway/install.sh
#     bash install.sh --domain test.alba-rosa.cz --admin jmeno@firma.cz
#
# Celý skript je ve funkcích a spouští se až posledním řádkem: bash ho tak
# načte celý předem. Při opakovaném běhu se repo s tímhle souborem aktualizuje
# a bez toho by bash dočítal už přepsaný soubor.
set -euo pipefail

REPO_URL="https://github.com/jurapascal/claude-code-hub.git"
REPO_DIR="/opt/claude-code-hub"
HUB_USER="hub"
HUB_HOME="/home/hub"
PORT=8800
LOG="/var/log/claude-hub-install.log"

DOMAIN=""
EMAIL=""
ADMIN=""
REF="main"
SWAP="2G"
TLS=true
RESTART=true

A="\033[38;5;208m"; G="\033[38;5;114m"; Y="\033[38;5;180m"; D="\033[2m"; R="\033[0m"
ok()   { echo -e "  ${G}✓${R} $1"; }
info() { echo -e "  ${A}▸${R} $1"; }
warn() { echo -e "  ${Y}⚠${R} $1"; }
die()  { echo -e "  ${Y}✗${R} $1" >&2; exit 1; }
step() { echo ""; echo -e "  ${A}✦${R} $1"; }

usage() {
    echo "Použití: bash install.sh --domain <adresa> [--email <e-mail>] [--admin <e-mail>]"
    echo "                         [--ref <větev|značka>] [--swap 2G] [--no-tls] [--no-restart]"
    exit "${1:-0}"
}

# Dlouhé výpisy (apt, npm, certbot) jdou do logu; při chybě se ukáže jeho konec.
quiet() {
    if ! "$@" >>"$LOG" 2>&1; then
        tail -n 25 "$LOG" >&2
        die "Selhalo: $* (celý výpis v $LOG)"
    fi
}

as_hub() {
    local uid
    uid="$(id -u "$HUB_USER")"
    sudo -u "$HUB_USER" XDG_RUNTIME_DIR="/run/user/$uid" \
        DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$uid/bus" "$@"
}

parse_args() {
    while [ $# -gt 0 ]; do
        case "$1" in
            --domain) DOMAIN="${2:-}"; shift 2 ;;
            --email)  EMAIL="${2:-}"; shift 2 ;;
            --admin)  ADMIN="${2:-}"; shift 2 ;;
            --ref)    REF="${2:-}"; shift 2 ;;
            --swap)   SWAP="${2:-}"; shift 2 ;;
            --no-tls) TLS=false; shift ;;
            --no-restart) RESTART=false; shift ;;
            -h|--help) usage 0 ;;
            *) echo "Neznámý parametr: $1" >&2; usage 1 ;;
        esac
    done
    [ -n "$DOMAIN" ] || usage 1
}

preflight() {
    [ "$(id -u)" -eq 0 ] || die "Pusť jako root: sudo bash install.sh …"
    # shellcheck disable=SC1091
    . /etc/os-release
    if [ "${ID:-}" != "ubuntu" ]; then
        warn "Tohle není Ubuntu (${PRETTY_NAME:-?}) — skript počítá s apt a AppArmorem."
    elif [ "${VERSION_ID:-}" != "24.04" ]; then
        warn "Ubuntu ${VERSION_ID:-?}: odzkoušené je 24.04, AppArmor a bwrap se jinde můžou chovat jinak."
    else
        ok "Ubuntu 24.04"
    fi
    : >>"$LOG"
    # needrestart by se jinak uprostřed apt zeptal, které služby restartovat.
    export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a
}

install_packages() {
    step "Balíčky"
    quiet apt-get update -q
    quiet apt-get install -y -q python3 git curl ca-certificates gnupg sudo \
        bubblewrap dbus-user-session nginx certbot python3-certbot-nginx \
        fail2ban ufw unattended-upgrades
    ok "python3, git, bubblewrap, dbus-user-session, nginx, certbot, fail2ban, ufw"
}

install_node_claude() {
    step "Node a Claude Code"
    local major
    major="$(node -v 2>/dev/null | sed 's/^v\([0-9]*\).*/\1/' || true)"
    if [ "${major:-0}" -lt 18 ]; then
        info "Node z NodeSource (22.x)"
        install -d -m 0755 /usr/share/keyrings
        curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
            | gpg --dearmor --yes -o /usr/share/keyrings/nodesource.gpg
        echo "deb [signed-by=/usr/share/keyrings/nodesource.gpg] https://deb.nodesource.com/node_22.x nodistro main" \
            >/etc/apt/sources.list.d/nodesource.list
        quiet apt-get update -q
        quiet apt-get install -y -q nodejs
    fi
    ok "Node $(node -v)"

    local claude=""
    command -v claude >/dev/null 2>&1 && claude="$(readlink -f "$(command -v claude)")"
    case "$claude" in
        /usr/*) ;;
        *) info "Claude Code přes npm -g"
           quiet npm install -g @anthropic-ai/claude-code
           claude="$(readlink -f "$(command -v claude)")" ;;
    esac
    case "$claude" in
        /usr/*) ok "Claude Code $(claude --version 2>/dev/null | head -n1) ($claude)" ;;
        *) die "claude leží v $claude — prostor ho neuvidí (vidí jen /usr). Nainstaluj ho přes npm -g." ;;
    esac

    # uv (uvx) spouští napojení na Google (workspace-mcp). Taky do /usr, jinak
    # ho prostor neuvidí.
    local uvx=""
    command -v uvx >/dev/null 2>&1 && uvx="$(readlink -f "$(command -v uvx)")"
    case "$uvx" in
        /usr/*) ;;
        *) info "uv (napojení na Google)"
           quiet sh -c 'curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh' ;;
    esac
    ok "uv $(uv --version 2>/dev/null | cut -d' ' -f2)"
}

setup_swap() {
    step "Swap"
    if [ -n "$(swapon --show=NAME --noheadings)" ]; then
        ok "swap už je ($(swapon --show=SIZE --noheadings | head -n1 | tr -d ' '))"
    else
        # Bez swapu sáhne OOM killer po tom, co má nejvíc paměti — ne nutně
        # po session, která to způsobila (README brány).
        [ -f /swapfile ] || fallocate -l "$SWAP" /swapfile
        chmod 600 /swapfile
        quiet mkswap /swapfile
        swapon /swapfile
        grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >>/etc/fstab
        ok "swap $SWAP (/swapfile)"
    fi
    echo 'vm.swappiness=10' >/etc/sysctl.d/99-claude-hub.conf
    sysctl -q -p /etc/sysctl.d/99-claude-hub.conf
}

setup_firewall() {
    step "Firewall a ochrana SSH"
    local ssh_port
    ssh_port="$(sshd -T 2>/dev/null | awk '/^port /{print $2; exit}' || true)"
    ssh_port="${ssh_port:-22}"
    # SSH se povolí dřív, než se ufw zapne — jinak by se server zavřel i tomu,
    # kdo ho zrovna nastavuje.
    quiet ufw allow "$ssh_port/tcp"
    quiet ufw allow 80/tcp
    quiet ufw allow 443/tcp
    quiet ufw --force enable
    ok "ufw: $ssh_port (SSH), 80, 443"

    if [ ! -f /etc/fail2ban/jail.local ]; then
        cat >/etc/fail2ban/jail.local <<'EOF'
[sshd]
enabled = true
backend = systemd
maxretry = 5
bantime = 1h
findtime = 10m
EOF
    fi
    quiet systemctl enable fail2ban
    quiet systemctl restart fail2ban
    ok "fail2ban: SSH, 5 pokusů, ban na hodinu"

    cat >/etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
    quiet systemctl enable unattended-upgrades
    ok "automatické bezpečnostní aktualizace"
}

setup_user() {
    step "Uživatel $HUB_USER"
    if ! id "$HUB_USER" >/dev/null 2>&1; then
        useradd --system --create-home --home-dir "$HUB_HOME" --shell /bin/bash "$HUB_USER"
    fi
    # 750: domovy uživatelů brány (vaulty, přihlášení Claude) nemá kdo jiný číst.
    for dir in "$HUB_HOME" "$HUB_HOME/users" "$HUB_HOME/gateway" \
               "$HUB_HOME/.config" "$HUB_HOME/.config/systemd" "$HUB_HOME/.config/systemd/user"; do
        install -d -o "$HUB_USER" -g "$HUB_USER" -m 750 "$dir"
    done
    ok "$HUB_USER (uid $(id -u "$HUB_USER")), domov $HUB_HOME"
}

setup_isolation() {
    step "Izolace (bwrap)"
    if [ "$(sysctl -n kernel.apparmor_restrict_unprivileged_userns 2>/dev/null || echo 0)" = "1" ]; then
        # Povolí user namespaces jen bwrapu; zbytek hardeningu Ubuntu zůstává.
        cat >/etc/apparmor.d/bwrap <<'EOF'
abi <abi/4.0>,
include <tunables/global>

profile bwrap /usr/bin/bwrap flags=(unconfined) {
  userns,

  include if exists <local/bwrap>
}
EOF
        quiet apparmor_parser -r /etc/apparmor.d/bwrap
        ok "AppArmor profil /etc/apparmor.d/bwrap"
    fi
    local err
    err="$(mktemp)"
    if sudo -u "$HUB_USER" bwrap --ro-bind / / --proc /proc --dev /dev \
            --unshare-pid --chdir / -- /bin/true 2>"$err"; then
        ok "bwrap jako $HUB_USER funguje"
        rm -f "$err"
    else
        die "bwrap jako $HUB_USER nejde: $(cat "$err")"
    fi
}

REPO_CHANGED=false
fetch_repo() {
    step "Zdroj hubu ($REF)"
    local before=""
    if [ -d "$REPO_DIR/.git" ]; then
        before="$(git -C "$REPO_DIR" rev-parse HEAD)"
        quiet git -C "$REPO_DIR" fetch --tags origin
        quiet git -C "$REPO_DIR" checkout "$REF"
        # Větev dotáhnout; značka je pevná (detached HEAD).
        if git -C "$REPO_DIR" symbolic-ref -q HEAD >/dev/null; then
            quiet git -C "$REPO_DIR" merge --ff-only "origin/$REF"
        fi
    elif [ -n "$(ls -A "$REPO_DIR" 2>/dev/null)" ]; then
        warn "$REPO_DIR existuje a není to git — nechávám ho, jak je"
    else
        quiet git clone "$REPO_URL" "$REPO_DIR"
        quiet git -C "$REPO_DIR" checkout "$REF"
    fi
    [ -f "$REPO_DIR/gateway/server.py" ] || die "V $REPO_DIR chybí gateway/server.py"
    # Brána i prostory (sandbox ho má jen ke čtení) ho čtou jako hub.
    chmod -R a+rX "$REPO_DIR"
    if [ -d "$REPO_DIR/.git" ]; then
        local after
        after="$(git -C "$REPO_DIR" rev-parse HEAD)"
        [ "$before" = "$after" ] || REPO_CHANGED=true
        ok "$REPO_DIR @ $(git -C "$REPO_DIR" describe --tags --always 2>/dev/null)"
    else
        ok "$REPO_DIR"
    fi
}

setup_service() {
    step "Služba brány"
    local uid unit new changed=false
    uid="$(id -u "$HUB_USER")"
    loginctl enable-linger "$HUB_USER"
    # Uživatelský systemd naběhne s lingerem sám. Když dbus-user-session přibyl
    # až po jeho startu, sběrnice nevznikne, dokud se nerestartuje.
    for _ in $(seq 1 40); do [ -S "/run/user/$uid/bus" ] && break; sleep 0.25; done
    if [ ! -S "/run/user/$uid/bus" ]; then
        quiet systemctl restart "user@$uid.service"
        for _ in $(seq 1 40); do [ -S "/run/user/$uid/bus" ] && break; sleep 0.25; done
    fi
    [ -S "/run/user/$uid/bus" ] || die "Uživatelský systemd pro $HUB_USER nemá sběrnici (/run/user/$uid/bus)."

    unit="$HUB_HOME/.config/systemd/user/claude-hub-gateway.service"
    new="$(cat <<EOF
[Unit]
Description=Claude Code Hub - viceuzivatelska brana
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$REPO_DIR
Environment=HUB_GW_DIR=$HUB_HOME/gateway
Environment=HUB_GW_REPO=$REPO_DIR
Environment=HUB_GW_CLAUDE=$HUB_HOME/.claude
Environment=HUB_GW_ISOLATION=bwrap
Environment=HUB_GW_HOST=127.0.0.1
Environment=HUB_GW_PORT=$PORT
Environment=HUB_GW_ASSUME_HTTPS=1
ExecStart=/usr/bin/python3 -m gateway.server
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
EOF
)"
    if [ ! -f "$unit" ] || [ "$(cat "$unit")" != "$new" ]; then
        printf '%s\n' "$new" >"$unit"
        chown "$HUB_USER:$HUB_USER" "$unit"
        changed=true
    fi
    quiet as_hub systemctl --user daemon-reload

    if as_hub systemctl --user is-active --quiet claude-hub-gateway; then
        if { $changed || $REPO_CHANGED; } && ! $RESTART; then
            warn "Brána běží se starou verzí (--no-restart). Restartuj ji, až nikdo nepracuje:"
            echo "      sudo -u $HUB_USER XDG_RUNTIME_DIR=/run/user/$uid systemctl --user restart claude-hub-gateway"
        elif $changed || $REPO_CHANGED; then
            # Restart zastaví i běžící prostory (i s Claude Code v nich).
            warn "Restartuji bránu kvůli nové verzi — běžící prostory se zastaví:"
            claude-hub-admin sessions 2>/dev/null | sed 's/^/      /' || true
            quiet as_hub systemctl --user restart claude-hub-gateway
        else
            ok "brána běží, beze změny"
        fi
    else
        quiet as_hub systemctl --user enable --now claude-hub-gateway
    fi

    for _ in $(seq 1 40); do
        curl -fsS "http://127.0.0.1:$PORT/gw/info" >/dev/null 2>&1 && break
        sleep 0.25
    done
    if ! curl -fsS "http://127.0.0.1:$PORT/gw/info" >/dev/null 2>&1; then
        journalctl "_SYSTEMD_USER_UNIT=claude-hub-gateway.service" -n 20 --no-pager >&2 || true
        die "Brána na 127.0.0.1:$PORT neodpovídá."
    fi
    ok "brána odpovídá na 127.0.0.1:$PORT"
}

install_admin_tool() {
    # Správa účtů bez opisování proměnných prostředí: claude-hub-admin add …
    cat >/usr/local/bin/claude-hub-admin <<EOF
#!/bin/bash
# Správa účtů brány Claude Code Hub (vygeneroval gateway/install.sh).
#   claude-hub-admin add jmeno@firma.cz --name "Jméno" [--role admin]
#   claude-hub-admin list | sessions | passwd <e-mail> | auth <e-mail> own|central
#   claude-hub-admin stop <e-mail> | remove <e-mail>
cd "$REPO_DIR" || exit 1
exec sudo -u "$HUB_USER" env HUB_GW_DIR="$HUB_HOME/gateway" HUB_GW_REPO="$REPO_DIR" \\
    HUB_GW_CLAUDE="$HUB_HOME/.claude" HUB_GW_ISOLATION=bwrap \\
    XDG_RUNTIME_DIR="/run/user/\$(id -u "$HUB_USER")" \\
    python3 -m gateway.admin "\$@"
EOF
    chmod 755 /usr/local/bin/claude-hub-admin
}

setup_company_skills() {
    step "Firemní skilly"
    # Hotové postupy pro celý tým leží ve firemním Obsidianu — jeden trezor,
    # v prostorech jen ke čtení — a ne u každého v domově. Tohle je jediné
    # místo, kde se zavádějí při instalaci; ručně pak `claude-hub-admin skills`.
    local out
    if out="$(claude-hub-admin skills update 2>&1)"; then          # jsou z gitu
        ok "${out%%$'\n'*}"
    elif out="$(claude-hub-admin skills install 2>&1)"; then       # ještě tam nejsou
        ok "${out%%$'\n'*}"
    else
        # Vlastní skilly, které z gitu nejsou — do těch se nevrtáme.
        info "${out%%$'\n'*}"
        echo "$out" >>"$LOG"
    fi
}

# Míří doména na tenhle server? Bez toho Let's Encrypt certifikát nevydá.
dns_points_here() {
    local here v4 v6
    here=" $(hostname -I) "
    v4="$(getent ahostsv4 "$DOMAIN" | awk 'NR==1{print $1}' || true)"
    if [ -z "$v4" ] || [[ "$here" != *" $v4 "* ]]; then
        warn "$DOMAIN míří na ${v4:-nic}, tenhle server má:$here"
        return 1
    fi
    # AAAA záznam jinam (třeba zděděný wildcard) shodí ověření: Let's Encrypt
    # zkouší IPv6 přednostně. getent bez AAAA vrací ::ffff:<IPv4>.
    v6="$(getent ahostsv6 "$DOMAIN" | awk 'NR==1{print $1}' || true)"
    if [ -n "$v6" ] && [[ "$v6" != ::ffff:* ]] && [[ "$here" != *" $v6 "* ]]; then
        warn "$DOMAIN má AAAA záznam $v6, který nemíří sem — smaž ho, nebo ho nasměruj na tenhle server"
        return 1
    fi
    return 0
}

write_site() {
    local cert="/etc/letsencrypt/live/$DOMAIN/fullchain.pem" site=/etc/nginx/sites-available/claude-hub
    local proxy
    proxy="$(cat <<EOF
    client_max_body_size 25m;

    # Veřejné stránky o aplikaci a ochraně soukromí — Google je chce, aby šlo
    # zveřejnit přihlášení OAuth. Bez přihlášení brány, rovnou ze zdroje.
    location = /o-aplikaci {
        default_type text/html;
        charset utf-8;
        alias $REPO_DIR/gateway/public/o-aplikaci.html;
    }
    location = /ochrana-soukromi {
        default_type text/html;
        charset utf-8;
        alias $REPO_DIR/gateway/public/ochrana-soukromi.html;
    }

    location / {
        proxy_pass http://127.0.0.1:$PORT;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Forwarded-Host \$host;
        # Terminál i taby jedou přes websocket; bez upgradu a dlouhého
        # timeoutu by se po minutě ticha odpojily.
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection \$hub_connection_upgrade;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
EOF
)"
    if [ -f "$cert" ]; then
        {
            echo "server {"
            echo "    listen 443 ssl;"
            echo "    listen [::]:443 ssl;"
            echo "    server_name $DOMAIN;"
            echo "    ssl_certificate /etc/letsencrypt/live/$DOMAIN/fullchain.pem;"
            echo "    ssl_certificate_key /etc/letsencrypt/live/$DOMAIN/privkey.pem;"
            [ -f /etc/letsencrypt/options-ssl-nginx.conf ] && \
                echo "    include /etc/letsencrypt/options-ssl-nginx.conf;"
            [ -f /etc/letsencrypt/ssl-dhparams.pem ] && \
                echo "    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;"
            # HSTS: bez něj je první návštěva zranitelná — prohlížeč zkusí http
            # a přesměrování na https může podvrhnout někdo po cestě. `always`,
            # ať hlavička odejde i s chybovou odpovědí.
            # Pozor: platí rok a odvolat se dá jen tím, že se pošle
            # `max-age=0` a počká, až to prohlížeče převezmou. Na téhle doméně
            # nic než https neběží, takže je to bez rizika.
            echo "    add_header Strict-Transport-Security \"max-age=31536000; includeSubDomains\" always;"
            # Hub je terminál — vložit ho do rámu cizí stránky nemá důvod
            # a dá se tím klikat za přihlášeného člověka.
            echo "    add_header X-Frame-Options \"DENY\" always;"
            echo "    add_header X-Content-Type-Options \"nosniff\" always;"
            echo "    add_header Referrer-Policy \"same-origin\" always;"
            echo "$proxy"
            echo "}"
            echo "server {"
            echo "    listen 80;"
            echo "    listen [::]:80;"
            echo "    server_name $DOMAIN;"
            echo "    return 301 https://\$host\$request_uri;"
            echo "}"
        } >"$site"
    else
        {
            echo "server {"
            echo "    listen 80;"
            echo "    listen [::]:80;"
            echo "    server_name $DOMAIN;"
            echo "$proxy"
            echo "}"
        } >"$site"
    fi
    ln -sf "$site" /etc/nginx/sites-enabled/claude-hub
}

setup_nginx() {
    step "nginx a HTTPS ($DOMAIN)"
    # shellcheck disable=SC2016  # proměnné nginx, ne shellu — musí zůstat doslova
    # Vlastní jméno proměnné: `$connection_upgrade` mívají i konfigurace jiných
    # webů na stejném serveru a dvě definice téže proměnné nginx odmítne.
    echo 'map $http_upgrade $hub_connection_upgrade { default upgrade; "" close; }' \
        >/etc/nginx/conf.d/hub-upgrade.conf
    # Nová konfigurace, která neprojde kontrolou, by v souboru zůstala a nginx
    # by padl až při dalším restartu — proto záloha a návrat.
    local site=/etc/nginx/sites-available/claude-hub
    [ -f "$site" ] && cp -a "$site" "$site.bak"
    write_site
    if ! nginx -t >>"$LOG" 2>&1; then
        [ -f "$site.bak" ] && mv -f "$site.bak" "$site"
        die "Konfigurace nginx neprošla kontrolou — vrácena předchozí (podrobnosti v $LOG)."
    fi
    rm -f "$site.bak"
    quiet systemctl enable nginx
    quiet systemctl reload-or-restart nginx

    local cert="/etc/letsencrypt/live/$DOMAIN/fullchain.pem"
    if [ -f "$cert" ]; then
        ok "certifikát už je, obnovuje ho certbot sám"
    elif ! $TLS; then
        warn "certifikát přeskočen (--no-tls) — appka se přihlašuje jen přes HTTPS"
    elif dns_points_here; then
        local contact=(--register-unsafely-without-email)
        [ -n "$EMAIL" ] && contact=(--email "$EMAIL")
        quiet certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos "${contact[@]}" --redirect
        # certbot si site upravil po svém; přepíše se naší verzí s TLS, ať je
        # při dalším běhu co porovnávat.
        write_site
        quiet nginx -t
        quiet systemctl reload nginx
        ok "certifikát Let's Encrypt, obnova automaticky"
    else
        warn "certifikát zatím nejde — nastav DNS a pusť skript znovu (stejný příkaz)"
    fi
}

create_admin() {
    [ -n "$ADMIN" ] || return 0
    step "Správce"
    if claude-hub-admin list 2>/dev/null | awk '{print tolower($1)}' | grep -qxF "$(echo "$ADMIN" | tr '[:upper:]' '[:lower:]')"; then
        ok "účet $ADMIN už je"
    elif [ -t 0 ]; then
        info "Zakládám správce $ADMIN — zadej mu heslo"
        claude-hub-admin add "$ADMIN" --role admin
    else
        warn "Bez terminálu se na heslo nejde zeptat. Založ ho potom: claude-hub-admin add $ADMIN --role admin"
    fi
}

summary() {
    echo ""
    echo -e "  ${A}✦${R} Hotovo"
    echo -e "  ${D}────────────────────────────────────${R}"
    echo -e "  Adresa pro appku:  ${G}https://$DOMAIN${R}"
    echo "  Účty:              claude-hub-admin add jmeno@firma.cz --name \"Jméno\""
    echo "                     claude-hub-admin list | sessions | passwd <e-mail>"
    echo "  Klíč API:          claude-hub-admin apikey set  → prostory na central se nepřihlašují"
    echo "                     (claude-hub-admin auth <e-mail> own = vlastní účet Claude)"
    echo "  Aktualizace:       sama každou noc, když nikdo nepracuje (ručně: claude-hub-update)"
    echo -e "  ${D}Log instalace: $LOG${R}"
}

# Parametry instalace pro noční aktualizaci — ta pouští install.sh se stejnými.
write_conf() {
    install -d -m 755 /etc/claude-hub
    {
        echo "# Parametry instalace brány — čte je noční aktualizace (claude-hub-update)."
        printf 'DOMAIN=%q\nEMAIL=%q\nREPO_DIR=%q\nTLS=%q\n' "$DOMAIN" "$EMAIL" "$REPO_DIR" "$TLS"
    } >/etc/claude-hub/install.conf
    chmod 644 /etc/claude-hub/install.conf
}

setup_updater() {
    step "Noční aktualizace"
    # Ze zdroje, aby se updater aktualizoval s ním; skript stažený samotný (curl)
    # a starší zdroj bez update.sh vezmou kopii vedle sebe.
    local src="$REPO_DIR/gateway/update.sh"
    [ -f "$src" ] || src="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/update.sh"
    if [ ! -f "$src" ]; then
        warn "update.sh nenalezen — noční aktualizace nenastavena"
        return 0
    fi
    install -m 755 "$src" /usr/local/sbin/claude-hub-update
    cat >/etc/systemd/system/claude-hub-update.service <<'EOF'
[Unit]
Description=Claude Code Hub - nocni aktualizace brany
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/claude-hub-update
EOF
    cat >/etc/systemd/system/claude-hub-update.timer <<'EOF'
[Unit]
Description=Claude Code Hub - nocni aktualizace brany

[Timer]
# Několik pokusů za noc: když někdo pracuje, aktualizace počká na další.
OnCalendar=*-*-* 02,03,04,05:15:00
RandomizedDelaySec=10min

[Install]
WantedBy=timers.target
EOF
    quiet systemctl daemon-reload
    quiet systemctl enable --now claude-hub-update.timer
    ok "každou noc 2:15–5:15, když nikdo nepracuje (ručně: claude-hub-update)"
}

setup_zaloha() {
    step "Noční šifrovaná záloha"
    local src="$REPO_DIR/gateway/zaloha.sh"
    [ -f "$src" ] || src="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/zaloha.sh"
    if [ ! -f "$src" ]; then
        warn "zaloha.sh nenalezen — záloha nenastavena"
        return 0
    fi
    install -m 755 "$src" /usr/local/sbin/claude-hub-zaloha
    cat >/etc/systemd/system/claude-hub-zaloha.service <<'EOF'
[Unit]
Description=Claude Code Hub - nocni sifrovana zaloha brany

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/claude-hub-zaloha
EOF
    cat >/etc/systemd/system/claude-hub-zaloha.timer <<'EOF'
[Unit]
Description=Claude Code Hub - nocni sifrovana zaloha brany

[Timer]
# Před aktualizací (2:15), ať se zálohuje ještě stará verze.
OnCalendar=*-*-* 01:30:00
RandomizedDelaySec=15min
Persistent=true

[Install]
WantedBy=timers.target
EOF
    quiet systemctl daemon-reload
    quiet systemctl enable --now claude-hub-zaloha.timer
    if [ -s /etc/claude-hub/zalohy.asc ]; then
        ok "každou noc v 1:30 do /var/backups/claude-hub (ručně: claude-hub-zaloha)"
    else
        # Bez klíče se zálohovat nebude — nešifrovaná záloha by jen ležela na
        # disku a tvářila se, že je o co opřít.
        warn "chybí /etc/claude-hub/zalohy.asc — zálohy zatím NEBĚŽÍ"
        echo -e "     ${D}Nasaď veřejný klíč:  gpg --armor --export <klíč> | ssh root@$DOMAIN 'cat > /etc/claude-hub/zalohy.asc'${R}"
    fi
}

main() {
    parse_args "$@"
    echo ""
    echo -e "  ${A}✦${R} Claude Code Hub — brána na server"
    echo -e "  ${D}────────────────────────────────────${R}"
    preflight
    install_packages
    setup_firewall
    install_node_claude
    setup_swap
    setup_user
    setup_isolation
    fetch_repo
    write_conf
    install_admin_tool
    setup_company_skills
    setup_service
    setup_updater
    setup_zaloha
    setup_nginx
    create_admin
    summary
}

main "$@"
exit
