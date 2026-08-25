#!/usr/bin/env python3
"""
Claude Code Hub — projects, memory and Claude Code sessions in one window.

    +--------------------+-------------------------------------------+
    |  SIDEBAR (hub)     |  TABS                                     |
    |  - project list    |  [ project A ] [ project B ] [ shell ]  + |
    |  - obsidian memory |                                           |
    |                    |  <real terminal running claude>           |
    +--------------------+-------------------------------------------+

The window is a thin host around a local web UI served from 127.0.0.1; the
terminals inside it are real ptys (stdlib `pty` on Linux/macOS, ConPTY through
pywinpty on Windows). One code path, all three platforms.

    python3 claude-hub.py                 open the hub
    python3 claude-hub.py --doctor        print what this machine has, then exit
    python3 claude-hub.py --no-browser    start the server and print the URL
    python3 claude-hub.py --window=webkit force a window host
                                          (chromium | webkit | browser)
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hub import core, pty_backend, server, window  # noqa: E402


def doctor():
    info = core.doctor()
    ok, detail = pty_backend.selftest()
    browser = window.find_browser()
    webkit = window.has_webkit()
    host = ("chromium (" + os.path.basename(browser) + ")" if browser else
            "webkitgtk" if webkit else "výchozí prohlížeč (jen záložka)")
    print()
    print("  Claude Code Hub — kontrola prostředí")
    print("  " + "─" * 42)
    for label, value in [
        ("platforma", info["platform"]),
        ("bash", info["bash"] or "CHYBÍ"),
        ("git", info["git"] or "chybí"),
        ("claude CLI", info["claude"] or "chybí (tab bude obyčejný shell)"),
        ("claude-wrapper.sh", info["wrapper"] or "chybí"),
        # Na Windows je tohle jediná odpověď, která něco znamená: symlink tam
        # chce práva správce, křižovatka ne — a co projde, se dá jen vyzkoušet.
        ("odkaz na složku", info["link"] or
         ("NEJDE — " + info["link_error"] + " (paměť nepůjde napojit)")),
        ("paměť napojená", f'{info["memory_link"]}  {info["memory_link_path"]}'),
        ("ftp-deploy.sh", info["ftp_deploy"] or "není"),
        ("Obsidian Brain", info["brain"] or "není (paměť vypnutá)"),
        ("složky projektů", ", ".join(info["project_dirs"]) or "žádné"),
        ("slash příkazy",
         ", ".join("/" + s for s in core.installed_skills()) or "žádné"),
        ("okno", host),
        ("pty test", "OK" if ok else f"SELHAL — {detail}"),
        ("log", core.LOG_PATH),
    ]:
        print(f"  {label:<20} {value}")
    print()
    if not info["bash"]:
        print("  ⚠ Bez bash hub neumí spustit tab:")
        print("    winget install Git.Git" if core.IS_WINDOWS
              else "    sudo apt install bash")
        print()
    return 0 if (ok and info["bash"]) else 1


def wait_for_page(proc=None, grace=10, startup=60):
    """Stay alive while the page is open. The page — not the browser process — is
    the signal.

    A chromium launcher that hands its window to an already running browser exits
    within milliseconds. Waiting on that process meant shutting the server down
    while the window was still on screen, and the user got ERR_CONNECTION_REFUSED
    on a window that had never even loaded.
    """
    hub = server.HUB
    deadline = time.time() + startup
    while hub.clients == 0 and hub.last_empty_at is None:
        if time.time() > deadline:
            core.log("okno se do %d s nepřipojilo — končím" % startup)
            if proc is not None:
                try:
                    proc.terminate()
                except Exception:
                    pass
            return
        time.sleep(0.3)
    while True:
        time.sleep(0.5)
        if hub.clients > 0:
            continue
        if hub.last_empty_at and time.time() - hub.last_empty_at > grace:
            core.log("okno zavřeno — končím")
            return


def main():
    args = sys.argv[1:]
    if "--doctor" in args:
        return doctor()

    prefer = ""
    for arg in args:
        if arg.startswith("--window="):
            prefer = arg.split("=", 1)[1]

    httpd, url = server.start()
    core.log(f"start: port {httpd.server_address[1]}, platforma {core.doctor()['platform']}")
    try:
        if "--no-browser" in args:
            print(url, flush=True)
            while True:
                time.sleep(3600)
        host, proc, blocking = window.open_window(url, prefer)
        core.log(f"okno: {host}")
        if blocking:
            blocking()          # in-process loop owns the window's lifetime
        else:
            wait_for_page(proc)
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        core.log(f"CHYBA: {exc!r}")
        raise
    finally:
        server.HUB.shutdown()
        httpd.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
