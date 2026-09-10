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
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Konzole na Windows píše v systémové stránce (na české instalaci cp1250) a
# `--doctor` na ní padal na UnicodeEncodeError hned u prvního rámečku. Naměřeno
# ve virtuálce s Windows 11 Pro. Znaky, které se do stránky nevejdou, ať radši
# vypadnou jako otazník, než aby shodily celý výpis.
if sys.platform == "win32":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass

from hub import core, pty_backend, server, window  # noqa: E402


MCP_MARKS = {"ok": "+", "auth": "!", "fail": "-", "local": ".",
             "unknown": "?"}


def doctor():
    info = core.doctor()
    ok, detail = pty_backend.selftest()
    # Zdravotní kontrola oslovuje každý MCP server zvlášť, takže tohle je
    # nejpomalejší část výpisu (~10 s). V --doctor to stojí za to: jinak se
    # „napojeno" pozná až tím, že v Claude Code nefunguje.
    mcp = core.mcp_list()
    agents_found = core.agents.detect(core._agents_extra())
    ollama = core.agents.ollama_state()
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
        ("AI agenti", _agents_summary(info)),
        ("agent-wrapper.sh", info.get("agent_wrapper") or
         "CHYBÍ — spusť install.sh, jinak se taby otevřou jako holý shell"),
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
        # Prohlížeč pro Claude Code: zajímavý je jen profil. Bez připnutého
        # profilu se každá přihlášená session ztratí s přepnutím projektu.
        ("prohlížeč (MCP)", f'{info["browser_mcp"]}  {info["browser_mcp_detail"]}'),
        ("napojení (MCP)", _mcp_summary(mcp)),
        ("pty test", "OK" if ok else f"SELHAL — {detail}"),
        ("log", core.LOG_PATH),
    ]:
        print(f"  {label:<20} {value}")
        # Rozpis patří hned pod svůj řádek, ne až za celou tabulku.
        # Rozpis patří hned pod svůj řádek, ne až za celou tabulku.
        if label == "AI agenti":
            for a in agents_found:
                mark = "+" if a["path"] else "-"
                extra = f' {a["version"]}' if a["version"] else ""
                where = a["path"] or ("není v PATH — " + (a["install"] or "?"))
                if a["id"] == "ollama" and a["path"]:
                    where += ("  " + _ollama_note(ollama))
                star = " (výchozí)" if a["id"] == info.get("default_agent") else ""
                print(f'  {"":<20} {mark} {a["label"]}{extra}{star} — {where}')
        if label == "napojení (MCP)":
            for server in mcp.get("servers") or []:
                mark = MCP_MARKS.get(server["state"], "?")
                print(f"  {'':<20} {mark} {server['name']} — {server['status']}")
    print()
    if not info["bash"]:
        print("  ⚠ Bez bash hub neumí spustit tab:")
        print("    winget install Git.Git" if core.IS_WINDOWS
              else "    sudo apt install bash")
        print()
    return 0 if (ok and info["bash"]) else 1


def _ollama_note(state):
    """Ollama je zvláštní: nestačí, že je nainstalovaná — musí i běžet."""
    if not state.get("running"):
        return "(neběží — spusť: ollama serve)"
    n = len(state.get("models") or [])
    slovo = "model" if n == 1 else ("modely" if n < 5 else "modelů")
    jmena = ", ".join((state.get("models") or [])[:3])
    return f"(běží, {n} {slovo}: {jmena})" if n else "(běží, žádný model stažený)"


def _agents_summary(info):
    """Kolik agentů je po ruce — detail se vypisuje pod tím řádkem."""
    found = [k for k, v in (info.get("agents") or {}).items() if v]
    if not found:
        return "žádný (tab bude obyčejný shell)"
    return f'{len(found)} z {len(info.get("agents") or {})} k dispozici'


def _mcp_summary(mcp):
    if not mcp.get("ok"):
        return mcp.get("detail") or "nepodařilo se zjistit"
    c = mcp.get("counts") or {}
    parts = [f'{c.get("ok", 0)} z {c.get("total", 0)} připojeno']
    if c.get("auth"):
        parts.append(f'{c["auth"]}x chce přihlásit')
    if c.get("fail"):
        parts.append(f'{c["fail"]}x nepřipojeno')
    if c.get("local"):
        parts.append(f'{c["local"]}x jen v projektu')
    return ", ".join(parts)


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
            # S "nechat běžet pro telefon" je zavřené okno jen zavřené okno —
            # server musí zůstat, jinak se z mobilu není kam připojit.
            if core.CONFIG.get("remote_enabled") and \
                    core.CONFIG.get("remote_keep_running"):
                continue
            core.log("okno zavřeno — končím")
            return


def _normalize_url(url):
    """Z holé adresy udělá https:// URL. `test.alba-rosa.cz` → `https://test.alba-rosa.cz`."""
    url = (url or "").strip()
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    return url.rstrip("/")


def run_server_client(url, prefer=""):
    """Tenký klient: okno appky míří rovnou na bránu, žádný lokální server.

    Přihlášení i celý hub servíruje server; okno má vlastní profil, který si
    drží přihlašovací cookie, takže po prvním přihlášení naskočí hub rovnou.
    """
    core.log(f"server režim: okno na {url}")
    host, proc, blocking = window.open_window(url, prefer)
    core.log(f"okno: {host}")
    try:
        if blocking:
            blocking()            # WebKitGTK: smyčka drží okno
        elif proc is not None:
            proc.wait()           # vlastní profil → proces je to okno
        else:
            while True:           # holá záložka v prohlížeči — není na co čekat
                time.sleep(3600)
    except KeyboardInterrupt:
        pass
    return 0


def main():
    args = sys.argv[1:]
    if "--doctor" in args:
        return doctor()

    prefer = ""
    for arg in args:
        if arg.startswith("--window="):
            prefer = arg.split("=", 1)[1]

    # Server (týmový) režim: appka je jen okno na bránu. Volba se pamatuje
    # v hub-config.json (server_url); `--server=URL` ji nastaví, `--local` zruší.
    server_url = ""
    for arg in args:
        if arg.startswith("--server="):
            server_url = _normalize_url(arg.split("=", 1)[1])
    if server_url:
        core.save_config({"server_url": server_url})
    if "--local" in args:
        core.save_config({"server_url": ""})
        server_url = ""
    else:
        server_url = server_url or _normalize_url(core.CONFIG.get("server_url", ""))
    if server_url:
        # --no-browser znamená „neotvírej okno" i tady. Bez téhle kontroly ho
        # serverový režim otevřel i tak a příznak platil jen pro lokální hub.
        if "--no-browser" in args:
            core.log(f"server režim: {server_url} (okno neotevírám, --no-browser)")
            return 0
        return run_server_client(server_url, prefer)

    httpd, url = server.start()
    core.log(f"start: port {httpd.server_address[1]}, platforma {core.doctor()['platform']}")
    if core.CONFIG.get("remote_enabled"):
        # Tailscale se ptáme přes CLI, což trvá — okno na to nesmí čekat.
        threading.Thread(target=server.start_remote, daemon=True).start()
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
        server.stop_remote()
        server.HUB.shutdown()
        httpd.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
