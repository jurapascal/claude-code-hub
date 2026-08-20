"""
Putting a window around the local UI.

The hub is a web app, but it should not look or feel like a browser tab. Three
hosts are tried in order, and the first one that works wins:

  1. a chromium-family browser in --app mode  (Windows: Edge is always there)
  2. WebKitGTK through PyGObject             (Linux, no browser needed at all)
  3. whatever the desktop's default browser is (last resort, plain tab)

Each host returns a `wait()` that blocks until the window is gone, so the
launcher can shut the server down at the right moment.
"""
import os
import shutil
import subprocess
import webbrowser

from . import core

PROFILE_DIR = os.path.join(core.CLAUDE_DIR, "hub-browser-profile")

BROWSERS_WINDOWS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
]
BROWSERS_MAC = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
]
BROWSERS_UNIX = ["google-chrome", "google-chrome-stable", "chromium",
                 "chromium-browser", "brave-browser", "microsoft-edge", "vivaldi"]


def find_browser():
    """Path to a chromium-family browser that understands --app, or ''."""
    configured = core.CONFIG.get("browser", "")
    if configured:
        found = shutil.which(configured)
        if found:
            return found
        if os.path.isfile(configured):
            return configured
    if core.IS_WINDOWS:
        candidates = BROWSERS_WINDOWS
    elif core.IS_MAC:
        candidates = BROWSERS_MAC
    else:
        candidates = BROWSERS_UNIX
    for name in candidates:
        if os.path.isabs(name):
            if os.path.isfile(name):
                return name
        else:
            found = shutil.which(name)
            if found:
                return found
    return ""


def has_webkit():
    """(gtk_version, webkit_module) if WebKitGTK is importable here, else None."""
    if core.IS_WINDOWS or core.IS_MAC:
        return None
    try:
        import gi
    except ImportError:
        return None
    for gtk_ver, wk_ver, wk_name in (("4.0", "6.0", "WebKit"),
                                     ("3.0", "4.1", "WebKit2"),
                                     ("3.0", "4.0", "WebKit2")):
        try:
            gi.require_version("Gtk", gtk_ver)
            gi.require_version(wk_name, wk_ver)
            __import__("gi.repository", fromlist=["Gtk", wk_name])
            return gtk_ver, wk_name
        except Exception:
            continue  # a different version pair may already be pinned
    return None


def _open_chromium(browser, url):
    argv = [
        browser,
        f"--app={url}",
        f"--user-data-dir={PROFILE_DIR}",   # our own process to wait on
        "--no-first-run",
        "--no-default-browser-check",
        "--window-size=1360,860",
    ]
    if not core.IS_WINDOWS and not core.IS_MAC:
        argv.append("--class=Claude Code")  # matches StartupWMClass in the .desktop
    proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc.wait


def _open_webkit(url, versions):
    """Native GTK window with a WebKit view — no browser involved."""
    gtk_ver, wk_name = versions
    import gi
    gi.require_version("Gtk", gtk_ver)
    gi.require_version(wk_name, "6.0" if wk_name == "WebKit" else "4.1")
    from gi.repository import GLib, Gtk  # noqa: E402
    webkit = __import__("gi.repository", fromlist=[wk_name])
    WebKit = getattr(webkit, wk_name)

    # prgname is what Wayland/X11 map to claude-code-hub.desktop, which is where
    # the window gets its icon and its place in the dock.
    GLib.set_prgname("claude-code-hub")
    GLib.set_application_name("Claude Code")

    view = WebKit.WebView()
    view.load_uri(url)

    if gtk_ver == "4.0":
        Gtk.init()
        window = Gtk.Window()
        window.set_default_size(1360, 860)
        window.set_title("Claude Code")
        window.set_child(view)
        loop = GLib.MainLoop()
        window.connect("close-request", lambda *_: (loop.quit(), False)[1])
        window.present()
        return loop.run

    Gtk.init([])
    window = Gtk.Window(title="Claude Code")
    window.set_default_size(1360, 860)
    if core.ICON_PATH and os.path.isfile(core.ICON_PATH):
        try:
            window.set_icon_from_file(core.ICON_PATH)
        except Exception:
            pass
    window.add(view)
    window.connect("destroy", Gtk.main_quit)
    window.show_all()
    return Gtk.main


def open_window(url, prefer=""):
    """Open the hub UI. Returns (host_name, wait_callable)."""
    order = ["chromium", "webkit", "browser"]
    if prefer in order:
        order.remove(prefer)
        order.insert(0, prefer)

    for host in order:
        if host == "chromium":
            browser = find_browser()
            if browser:
                return os.path.basename(browser), _open_chromium(browser, url)
        elif host == "webkit":
            versions = has_webkit()
            if versions:
                try:
                    return "webkitgtk", _open_webkit(url, versions)
                except Exception:
                    continue  # broken WebKit install → next host
    webbrowser.open(url)
    return "browser", None  # no process to wait on; the launcher watches the page
