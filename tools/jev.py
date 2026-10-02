#!/usr/bin/env python3
"""Jev z příkazové řádky — tudy ho volá Claude (skill `jev`).

    jev.py status
    jev.py run < vstup.json        # {"state": …, "questions": {…}}
    jev.py run < davka.json        # [{…}, {…}] = víc dotazů za sebou

Výstup je vždy JSON na stdout. Údaje bere z ~/.claude/jev.json, které
vyplní Nastavení → AI agenti → Jev v Claude Code Hubu.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hub import jev  # noqa: E402

MAX_BATCH = 200


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd == "status":
        st = jev.status()
        st.pop("setup", None)
        print(json.dumps(st, ensure_ascii=False, indent=2))
        return 0 if st["configured"] else 1
    if cmd != "run":
        print(__doc__, file=sys.stderr)
        return 2
    try:
        data = json.load(sys.stdin)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": f"Vstup není JSON: {exc}"}))
        return 2
    if isinstance(data, list):
        if len(data) > MAX_BATCH:
            print(json.dumps({"ok": False, "error": f"Nejvýš {MAX_BATCH} dotazů naráz."}))
            return 2
        out = [jev.run(item) for item in data]
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if all(r["ok"] for r in out) else 1
    res = jev.run(data)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
