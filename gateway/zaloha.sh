#!/bin/bash
# Noční šifrovaná záloha brány. Pouští ji claude-hub-zaloha.timer (viz
# gateway/install.sh) jako root, jednou denně.
#
# Zálohuje se /home/hub — domovy prostorů (Obsidian, projekty), firemní vault
# a databáze účtů. Cache se vynechávají: jsou to gigabajty, které se stáhnou
# znovu, a obnovu by jen zdržely.
#
# ŠIFRUJE SE VEŘEJNÝM KLÍČEM. Server má jen ten veřejný, takže zálohu umí
# zabalit, ale rozbalit ne — a nepotřebuje k tomu žádné heslo. Soukromý klíč
# patří mimo server (u správce v trezoru hesel). Kdo ukradne server i se
# zálohami, nepřečte z nich nic.
#
# Klíč se nasadí jednou:
#     gpg --armor --export <klíč> | ssh root@brána 'cat > /etc/claude-hub/zalohy.asc'
#
# Bez toho souboru se záloha neudělá a jen to řekne — radši žádná záloha než
# nešifrovaná, která by ležela na disku a tvářila se, že je o co opřít.
#
# Obnova (na stroji, kde je soukromý klíč):
#     gpg -d hub-RRRRMMDD.tar.zst.gpg | zstd -d | tar -xf - -C /kam
#
# Ručně: claude-hub-zaloha
set -uo pipefail

KEY=/etc/claude-hub/zalohy.asc
DEST=/var/backups/claude-hub
SRC=/home/hub
KEEP=${HUB_ZALOHA_KEEP:-14}          # kolik denních záloh se drží
LOG=/var/log/claude-hub-zaloha.log

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"$LOG"; }

main() {
    if [ ! -s "$KEY" ]; then
        log "CHYBA: chybí veřejný klíč $KEY — záloha se nedělá (nešifrovanou dělat nebudu)"
        echo "Chybí $KEY. Nasaď veřejný klíč, jinak se zálohovat nebude." >&2
        exit 1
    fi
    mkdir -p "$DEST"
    chmod 700 "$DEST"

    local stamp out tmp
    stamp="$(date +%Y%m%d-%H%M)"
    out="$DEST/hub-$stamp.tar.zst.gpg"
    tmp="$out.part"

    # Vlastní klíčenka v /etc: root nemá mít v ~/.gnupg nic, co sem nepatří,
    # a --trust-model always kvůli tomu, že klíč nikdo nepodepsal.
    local ring=/etc/claude-hub/zalohy-gnupg
    mkdir -p "$ring"; chmod 700 "$ring"
    GNUPGHOME="$ring" gpg --batch --quiet --import "$KEY" 2>/dev/null

    local recipient
    recipient="$(GNUPGHOME="$ring" gpg --batch --with-colons --list-keys 2>/dev/null \
                 | awk -F: '/^uid/{print $10; exit}')"
    if [ -z "$recipient" ]; then
        log "CHYBA: klíč $KEY se nepodařilo načíst"
        exit 1
    fi

    log "začínám (příjemce: $recipient)"
    # nice/ionice: na stroji zároveň pracují lidé, záloha jim nesmí ubrat výkon.
    # Vynechané cesty jsou cache (stáhnou se znovu) a koše po smazaných účtech.
    if nice -n 19 ionice -c3 tar -C "$(dirname "$SRC")" -cf - \
            --warning=no-file-changed --warning=no-file-ignored \
            --exclude='*/.cache' \
            --exclude='*/.npm' \
            --exclude='*/.local/share/uv' \
            --exclude='*/.cache/ms-playwright' \
            --exclude='*/node_modules' \
            --exclude='*/.claude/projects/*/[0-9a-f]*.jsonl.bak' \
            --exclude='users/_smazany-*' \
            "$(basename "$SRC")" 2>>"$LOG" \
        | nice -n 19 zstd -3 -T2 -q \
        | GNUPGHOME="$ring" gpg --batch --yes --quiet --trust-model always \
              --encrypt --recipient "$recipient" --output "$tmp"
    then
        mv -f "$tmp" "$out"
        chmod 600 "$out"
        log "hotovo: $(basename "$out") ($(du -h "$out" | cut -f1))"
    else
        # tar vrací 1 i když se jen soubor změnil během čtení; to je u živého
        # serveru běžné a záloha je použitelná. Prázdný výstup je ale chyba.
        if [ -s "$tmp" ]; then
            mv -f "$tmp" "$out"
            chmod 600 "$out"
            log "hotovo s výhradou (soubor se měnil během čtení): $(basename "$out") ($(du -h "$out" | cut -f1))"
        else
            rm -f "$tmp"
            log "CHYBA: záloha se nepovedla, nic jsem nezapsal"
            exit 1
        fi
    fi

    # Úklid: drží se posledních KEEP kusů.
    local old
    old="$(ls -1t "$DEST"/hub-*.tar.zst.gpg 2>/dev/null | tail -n +$((KEEP + 1)))"
    if [ -n "$old" ]; then
        echo "$old" | xargs -r rm -f
        log "smazáno starých záloh: $(echo "$old" | wc -l)"
    fi
    log "volno na disku: $(df -h "$DEST" | awk 'NR==2{print $4}')"
}

main "$@"
