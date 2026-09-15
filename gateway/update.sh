#!/bin/bash
# Noční aktualizace brány Claude Code Hub. Pouští ji claude-hub-update.timer
# (nastaví gateway/install.sh) jako root, několikrát za noc.
#
# Když je venku novější vydání (značka vX.Y.Z na GitHubu) a zrovna nikdo
# nepracuje, přepne zdroj na ně, pustí install.sh se stejnými parametry jako
# při instalaci a restartuje bránu. Když někdo pracuje, počká na další pokus:
# restart brány by mu utnul session i s Claude Code. Jak to dopadlo, zapíše do
# /etc/claude-hub/update.json — prostor to ukáže v Nastavení → Aktualizace.
#
# Ručně: claude-hub-update
#
# Celé ve funkcích a spouštěné až posledním řádkem: install.sh tenhle soubor
# během běhu přepíše novou verzí a bash by jinak dočítal už jiný obsah.
set -uo pipefail

CONF=/etc/claude-hub/install.conf
STATUS=/etc/claude-hub/update.json
LOG=/var/log/claude-hub-update.log
REPO_URL="https://github.com/jurapascal/claude-code-hub.git"

# status <ok:true|false> <věta> [nová verze]
status() {
    python3 - "$STATUS" "$1" "$2" "${3:-}" <<'EOF'
import json, os, sys, time
path, ok, detail, version = sys.argv[1:5]
try:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
except Exception:
    data = {}
data.update({"checked_at": int(time.time()), "ok": ok == "true", "detail": detail})
if version:
    data.update({"version": version, "updated_at": int(time.time())})
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(data, fh, ensure_ascii=False)
os.chmod(tmp, 0o644)
os.replace(tmp, path)
EOF
    echo "$(date '+%F %T') $2" >>"$LOG"
}

main() {
    [ "$(id -u)" -eq 0 ] || { echo "Pusť jako root." >&2; exit 1; }
    [ -f "$CONF" ] || { echo "Chybí $CONF — pusť gateway/install.sh." >&2; exit 1; }
    DOMAIN="" EMAIL="" REPO_DIR="" TLS=true
    # shellcheck disable=SC1090
    . "$CONF"
    install -d -m 755 /etc/claude-hub

    if [ ! -d "$REPO_DIR/.git" ]; then
        status false "Zdroj v $REPO_DIR není git — aktualizace přeskočena (pusť gateway/install.sh)."
        return 0
    fi

    local latest current running
    latest="$(git ls-remote --tags --refs "$REPO_URL" 2>/dev/null \
        | sed 's#.*refs/tags/##' | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | sort -V | tail -n1)"
    if [ -z "$latest" ]; then
        status false "GitHub neodpověděl — zkusím to při dalším pokusu."
        return 0
    fi
    current="$(git -C "$REPO_DIR" describe --tags --exact-match 2>/dev/null || true)"
    if [ "$current" = "$latest" ]; then
        status true "Server má nejnovější verzi ${latest#v}."
        return 0
    fi

    running="$(claude-hub-admin sessions 2>/dev/null | grep -c '^claude-hub-' || true)"
    if [ "${running:-0}" -gt 0 ]; then
        status true "Je venku ${latest#v}, ale běží prostorů: $running — aktualizace počká na další pokus."
        return 0
    fi

    local tls=()
    [ "$TLS" = "false" ] && tls=(--no-tls)
    local email=()
    [ -n "$EMAIL" ] && email=(--email "$EMAIL")
    if ! {
        echo "=== $(date '+%F %T') ${current:-?} → $latest"
        git -C "$REPO_DIR" fetch --tags --quiet origin &&
            git -C "$REPO_DIR" checkout --quiet "$latest" &&
            bash "$REPO_DIR/gateway/install.sh" --domain "$DOMAIN" "${email[@]}" \
                --ref "$latest" "${tls[@]}"
    } >>"$LOG" 2>&1; then
        status false "Aktualizace na ${latest#v} selhala — podrobnosti v $LOG."
        return 1
    fi

    # install.sh bránu nerestartuje (zdroj už byl přepnutý předem), tak tady —
    # nikdo nepracuje, ověřeno o kus výš.
    local uid
    uid="$(id -u hub)"
    # Log patří rootovi (běží to jako root) — přesměrování mimo sudo je záměr.
    # shellcheck disable=SC2024
    sudo -u hub XDG_RUNTIME_DIR="/run/user/$uid" \
        DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$uid/bus" \
        systemctl --user restart claude-hub-gateway >>"$LOG" 2>&1
    status true "Aktualizováno na ${latest#v}." "${latest#v}"
}

main "$@"
exit
