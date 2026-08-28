#!/usr/bin/env python3
"""
One browser profile for Playwright MCP instead of one per project folder.

Playwright MCP derives the Chromium profile directory from the *current working
directory*: `mcp-<channel>-<sha256(cwd)[:7]>` in its cache. In the hub every
project tab runs Claude Code in its own folder, so every project got its own
profile — a Google login done in one tab was gone in the next one, and gone
again the next day in a folder that had drifted. Pinning `--user-data-dir` in
the MCP registration is what makes the login stick.

    python3 tools/playwright_profile.py --path                  # just the path
    python3 tools/playwright_profile.py --claude-dir ~/.claude  # prepare it
    python3 tools/playwright_profile.py --claude-dir ~/.claude --prune

Both installers call this, which is why it is a Python file and not a heredoc
in each of them. Prints one `note` line per decision; exits non-zero only when
the profile could not be prepared.
"""
import argparse
import os
import shutil
import sys
import time

# Chromium keeps these for speed only. Skipping them turns a 1.3 GB copy into
# a 50 MB one — and they are what makes a leftover profile look expensive.
DISPOSABLE = {"Cache", "Code Cache", "GPUCache", "DawnCache", "DawnGraphiteCache",
              "DawnWebGPUCache", "GraphiteDawnCache", "ShaderCache",
              "GrShaderCache", "component_crx_cache", "extensions_crx_cache",
              "BrowserMetrics", "BrowserMetrics-spare.pma", "Crashpad"}

# Live-instance markers. Copying them over would make Chromium think the new
# profile is already open somewhere.
SINGLETONS = {"SingletonLock", "SingletonCookie", "SingletonSocket", "lockfile"}


def profile_path(claude_dir):
    """Where the shared profile lives. Not in a cache dir on purpose — a disk
    cleanup would silently log the user out of everything."""
    return os.path.join(claude_dir, "browser-profile")


def cache_dir():
    """Playwright's own cache root, the same one playwright-core computes."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Caches")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "ms-playwright-mcp")


def is_locked(profile):
    """Is a browser running on this profile? Mirrors playwright-core's own check."""
    if sys.platform == "win32":
        lock = os.path.join(profile, "lockfile")
        try:
            os.close(os.open(lock, os.O_RDWR))
            return False
        except FileNotFoundError:
            return False
        except OSError:
            return True
    try:
        target = os.readlink(os.path.join(profile, "SingletonLock"))
        pid = int(target.rsplit("-", 1)[-1])
        os.kill(pid, 0)                      # signal 0 only asks "are you alive?"
        return True
    except (OSError, ValueError):
        return False


def has_login(profile):
    """A profile someone actually used, as opposed to a freshly created one."""
    return os.path.isdir(os.path.join(profile, "Default"))


def signed_in(profile):
    """Is there a Google account in it? `Accounts` shows up only once there is."""
    return os.path.isdir(os.path.join(profile, "Default", "Accounts"))


def rank(profile):
    """Best candidate to take over: signed into Google > fat cookie jar > recent.

    An `Accounts` directory appears only once the browser has a signed-in
    Google profile, which is exactly the thing worth keeping.
    """
    default = os.path.join(profile, "Default")
    try:
        cookies = os.path.getsize(os.path.join(default, "Cookies"))
    except OSError:
        cookies = 0
    try:
        used = os.path.getmtime(default)
    except OSError:
        used = 0
    return (signed_in(profile), cookies, used)


def legacy_profiles(pinned):
    """The per-folder profiles Playwright MCP made before this fix."""
    root = cache_dir()
    found = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return found
    for name in names:
        path = os.path.join(root, name)
        if name.startswith("mcp-") and os.path.isdir(path) and path != pinned:
            found.append(path)
    return found


def copy_profile(src, dst, notes):
    """Copy without the disposable caches, and without the live-instance marks."""
    def ignore(directory, names):
        skip = {n for n in names if n in DISPOSABLE or n in SINGLETONS}
        # Sockets and other odd files cannot be copied and are never worth it.
        for name in names:
            full = os.path.join(directory, name)
            if os.path.islink(full) or (os.path.exists(full) and
                                        not os.path.isdir(full) and
                                        not os.path.isfile(full)):
                skip.add(name)
        return skip

    shutil.copytree(src, dst, ignore=ignore, dirs_exist_ok=True)
    notes.append(f"přihlášení převzato z {os.path.basename(src)} (kopie bez cache)")


def drop_singletons(profile):
    """A moved profile brings the marks of its last run; Chromium reads them as
    "this profile is open elsewhere". Cheaper to remove them than to explain."""
    for name in SINGLETONS:
        try:
            os.remove(os.path.join(profile, name))
        except OSError:
            pass


def adopt(pinned, notes):
    """Give the pinned profile a past, so nobody has to log in again."""
    candidates = [p for p in legacy_profiles(pinned) if has_login(p)]
    best = max(candidates, key=rank) if candidates else None

    if best is None:
        os.makedirs(pinned, exist_ok=True)
        notes.append(f"nový profil: {pinned} (přihlásíš se jednou a zůstane to)")
        return 0

    # A profile that has already been used is left alone — unless nobody ever
    # signed in to it and one of the old ones has an account waiting. That is
    # the case where the browser ran once before the old login was taken over.
    if has_login(pinned) and (signed_in(pinned) or not signed_in(best)):
        notes.append(f"profil už je připravený: {pinned}")
        return 0

    if is_locked(best):
        os.makedirs(pinned, exist_ok=True)
        notes.append(f"varování: nad {os.path.basename(best)} běží prohlížeč — "
                     "zavři ho a spusť tenhle skript znovu, jinak se přihlášení nepřenese")
        return 0
    if is_locked(pinned):
        notes.append(f"varování: nad {pinned} běží prohlížeč — "
                     "zavři ho a spusť tenhle skript znovu")
        return 0

    os.makedirs(os.path.dirname(pinned), exist_ok=True)   # rename needs the parent
    if has_login(pinned):
        # Never throw away what the browser already wrote there, even if it is
        # only a day of cookies. Aside, dated, and the user can delete it.
        aside = pinned + time.strftime(".backup-%Y%m%d-%H%M%S")
        os.rename(pinned, aside)
        notes.append(f"nepřihlášený profil odložen do {os.path.basename(aside)}")
    try:
        # Same filesystem: a rename is instant and leaves nothing behind twice.
        os.rename(best, pinned)
        notes.append(f"přihlášení převzato z {os.path.basename(best)}")
    except OSError:
        try:
            copy_profile(best, pinned, notes)
        except Exception as exc:
            os.makedirs(pinned, exist_ok=True)
            notes.append(f"varování: starý profil se nepodařilo převzít ({exc}) — "
                         "přihlas se v prohlížeči znovu")
    drop_singletons(pinned)
    return 0


def prune(pinned, notes):
    """Throw away the leftover per-folder profiles. Only when asked for."""
    freed = 0
    for path in legacy_profiles(pinned):
        if is_locked(path):
            notes.append(f"{os.path.basename(path)}: běží prohlížeč, nechávám být")
            continue
        size = 0
        for root, _, files in os.walk(path):
            for name in files:
                try:
                    size += os.path.getsize(os.path.join(root, name))
                except OSError:
                    pass
        try:
            shutil.rmtree(path)
            freed += size
            notes.append(f"smazán starý profil {os.path.basename(path)}")
        except OSError as exc:
            notes.append(f"varování: {os.path.basename(path)} nejde smazat ({exc})")
    if freed:
        notes.append(f"uvolněno {freed / (1024 * 1024):.0f} MB")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--claude-dir",
                        default=os.environ.get("CLAUDE_CONFIG_DIR") or
                        os.path.join(os.path.expanduser("~"), ".claude"))
    parser.add_argument("--path", action="store_true",
                        help="jen vypsat cestu k profilu, nic neměnit")
    parser.add_argument("--prune", action="store_true",
                        help="smazat staré profily po jednotlivých složkách")
    args = parser.parse_args()

    pinned = profile_path(os.path.abspath(os.path.expanduser(args.claude_dir)))
    if args.path:
        print(pinned)
        return 0

    notes = []
    try:
        adopt(pinned, notes)
        if args.prune:
            prune(pinned, notes)
    except OSError as exc:
        print(f"chyba: profil {pinned} nejde připravit ({exc})")
        return 1

    for note in notes:
        print(note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
