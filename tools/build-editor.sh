#!/bin/bash
# Sestaví editor poznámek (CodeMirror 6) do hub/static/vendor/codemirror.js.
#
# Knihovna se v hubu veze hotová, jako xterm — hub běží i bez sítě a instalace
# nemá co stahovat. Spouštět jen při aktualizaci CodeMirroru; výsledek patří
# do gitu. Zdrojový soubor je tools/editor-entry.js, obsidianovské rozšíření
# ([[odkazy]], ==zvýraznění==, #štítky) je v hub/static/vault.js.
#
#     bash tools/build-editor.sh
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$PWD"
OUT="$REPO/hub/static/vendor/codemirror.js"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

cp "$REPO/tools/editor-entry.js" "$WORK/entry.js"
cd "$WORK"
npm init -y >/dev/null
npm install --no-audit --no-fund --silent \
    @codemirror/state @codemirror/view @codemirror/commands @codemirror/language \
    @codemirror/lang-markdown @codemirror/autocomplete @codemirror/search \
    @lezer/markdown @lezer/highlight esbuild
./node_modules/.bin/esbuild entry.js --bundle --format=iife --global-name=CM \
    --minify --target=es2019 --legal-comments=none --outfile="$OUT"

printf 'Hotovo: %s (%s B)\n' "$OUT" "$(wc -c <"$OUT")"
printf 'Verze: '
node -e 'const p = ["@codemirror/state","@codemirror/view","@codemirror/lang-markdown"];
         console.log(p.map((n) => n + " " + require("./node_modules/" + n + "/package.json").version).join(", "))'
