#!/usr/bin/env python3
"""
Spouštěč Playwright MCP pro vestavěný prohlížeč.

Zaregistrovaný jako MCP `playwright` místo holého `npx @playwright/mcp`. Než
pustí Playwright MCP, zajistí, že běží sdílený Chromium (hub/prohlizec.py), a
nasměruje MCP na jeho ladicí port (`--cdp-endpoint`). Tentýž prohlížeč pak vidí
člověk v okně v appce a může mu do toho sáhnout.

Funguje i bez spuštěného hubu: prohlížeč si spustí sám, stejný profil jako dřív
(`~/.claude/browser-profile`), takže se nic neztratí. Když se Chromium spustit
nepodaří, jede Playwright MCP po staru s vlastním prohlížečem — Claude bez
prohlížeče nezůstane.

    python3 tools/playwright_bridge.py            # spustí MCP (stdio)
    python3 tools/playwright_bridge.py --register # přepíše registraci v Claude Code
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PW_MCP = "@playwright/mcp@0.0.83"   # pevná verze: @latest se při každém startu ptá npm a občas rozbije revizi prohlížeče
sys.path.insert(0, os.path.dirname(HERE))

from hub import prohlizec  # noqa: E402


def register():
    claude = shutil.which("claude")
    if not claude:
        print("claude nenalezen", file=sys.stderr)
        return 1
    subprocess.run([claude, "mcp", "remove", "playwright", "-s", "user"], capture_output=True)
    res = subprocess.run([claude, "mcp", "add", "playwright", "-s", "user", "--",
                          sys.executable or "python3", os.path.join(HERE, "playwright_bridge.py")])
    return res.returncode


def main():
    if "--register" in sys.argv:
        return register()
    npx = shutil.which("npx") or "npx"
    profile = prohlizec.profile_dir()
    ok = False
    if prohlizec.povoleno(server=prohlizec.na_serveru()):
        ok, why = prohlizec.ensure()
        if not ok:
            print(f"playwright_bridge: {why} — jedu s vlastním prohlížečem", file=sys.stderr)
    if ok:
        args = [npx, PW_MCP, "--cdp-endpoint", prohlizec.endpoint()]
    else:
        args = [npx, PW_MCP, "--browser", "chromium", "--user-data-dir", profile]
    if os.name == "nt":
        return subprocess.call(args)
    os.execvp(args[0], args)


if __name__ == "__main__":
    sys.exit(main() or 0)
