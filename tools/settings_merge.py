#!/usr/bin/env python3
"""
Wire the hub's hooks (and optionally bypass mode) into ~/.claude/settings.json.

That file is the user's, not ours: it can hold API keys, a model choice, hooks
they wrote themselves. So this merges instead of writing a template over it —
it only ever adds the keys it is asked for, backs the file up before the first
change, and leaves an existing hook of the same kind alone rather than stacking
a second one next to it.

Both installers call this, which is the reason it is a Python file and not a
heredoc in each of them.

    python3 tools/settings_merge.py --claude-dir ~/.claude --python python3 \
            [--hooks] [--bypass] [--dry-run]

Prints one `key: message` line per decision, and exits non-zero only if the
file could not be written.
"""
import argparse
import datetime
import json
import os
import shutil
import sys


def load(path):
    """Existing settings, or {} — and whether the file was actually there.

    A settings.json we cannot parse is a stop sign: overwriting it would throw
    away whatever the user had, so the caller gets told and nothing is touched.
    """
    if not os.path.isfile(path):
        return {}, False
    with open(path, encoding="utf-8-sig") as fh:      # BOM: PowerShell 5.1
        text = fh.read().strip()
    if not text:
        return {}, True
    return json.loads(text), True


def hook_entry(command, message, timeout=10):
    return {"hooks": [{"type": "command", "command": command,
                       "timeout": timeout, "statusMessage": message}]}


def has_hook(settings, event, needle):
    """True if some hook for `event` already runs something matching `needle`."""
    for group in settings.get("hooks", {}).get(event, []) or []:
        for hook in group.get("hooks", []) or []:
            if needle in str(hook.get("command", "")):
                return True
    return False


def ensure_hook(settings, event, needle, command, message, notes):
    existing = settings.get("hooks", {}).get(event) or []
    if has_hook(settings, event, needle):
        notes.append(f"{event}: už je zapojený, nechávám být")
        return False
    if existing:
        # Their own hook for this event is already doing a job we know nothing
        # about. Adding ours next to it would run both; that is their call.
        notes.append(f"{event}: máš tam vlastní hook — náš nepřidávám "
                     f"(ručně: {command})")
        return False
    settings.setdefault("hooks", {}).setdefault(event, []).append(
        hook_entry(command, message))
    notes.append(f"{event}: zapojen")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--claude-dir", required=True)
    ap.add_argument("--python", default="python3")
    ap.add_argument("--hooks", action="store_true")
    ap.add_argument("--bypass", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    claude_dir = os.path.expanduser(args.claude_dir)
    path = os.path.join(claude_dir, "settings.json")
    notes = []

    try:
        settings, existed = load(path)
    except Exception as exc:
        print(f"chyba: {path} nejde přečíst ({exc}) — nesahám na něj")
        return 1

    before = json.dumps(settings, sort_keys=True, ensure_ascii=False)

    if args.hooks:
        for event, script, msg in (
                ("Stop", "save-session.py", "Auto-saving session state..."),
                ("SessionStart", "session-start.py", "Loading Brain & session state...")):
            target = os.path.join(claude_dir, "hooks", script)
            ensure_hook(settings, event, script,
                        f'{args.python} "{target}"', msg, notes)

    if args.bypass:
        perms = settings.setdefault("permissions", {})
        if perms.get("defaultMode") == "bypassPermissions":
            notes.append("bypass: už byl nastavený")
        else:
            perms["defaultMode"] = "bypassPermissions"
            notes.append("bypass: zapnut (permissions.defaultMode)")
        # Without this every session opens with the "are you sure" screen,
        # which defeats the point of turning the mode on in the first place.
        settings["skipDangerousModePermissionPrompt"] = True

    if json.dumps(settings, sort_keys=True, ensure_ascii=False) == before:
        for note in notes:
            print(note)
        print("settings.json: beze změny")
        return 0

    if args.dry_run:
        for note in notes:
            print(note)
        print("settings.json: (dry-run, nic se nezapsalo)")
        return 0

    try:
        os.makedirs(claude_dir, exist_ok=True)
        if existed:
            stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            shutil.copy2(path, f"{path}.backup-{stamp}")
            notes.append(f"záloha: settings.json.backup-{stamp}")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(settings, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, path)                 # atomic: never a half-written file
    except Exception as exc:
        print(f"chyba: {path} nejde zapsat ({exc})")
        return 1

    for note in notes:
        print(note)
    print("settings.json: uloženo")
    return 0


if __name__ == "__main__":
    sys.exit(main())
