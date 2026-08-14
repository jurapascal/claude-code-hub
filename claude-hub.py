#!/usr/bin/env python3
"""
Claude Code Hub — a GTK GUI around Claude Code.

Layout:
  +--------------------+-------------------------------------------+
  |  SIDEBAR (hub)     |  NOTEBOOK (project tabs)                  |
  |  - welcome / brand |  [ project A ] [ project B ] [ shell ]    |
  |  - project list    |                                           |
  |  - obsidian memory |  <embedded real terminal running claude>  |
  +--------------------+-------------------------------------------+

The sidebar is always visible (the "main" that never disappears). Clicking a
project opens it as a new, nameable terminal tab next to the others.

Uses the same VTE engine as gnome-terminal, so the Claude Code TUI looks
exactly like it does in a normal terminal.
"""
import os
import json
import subprocess
import datetime
import urllib.parse

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Vte", "2.91")
from gi.repository import Gtk, Vte, GLib, Gdk, Pango, Gio  # noqa: E402

HOME = os.path.expanduser("~")
CLAUDE_DIR = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(HOME, ".claude")
CONFIG_PATH = os.path.join(CLAUDE_DIR, "hub-config.json")

# Everything machine-specific lives in hub-config.json — see hub-config.example.json.
# Missing file or missing keys → these defaults, all of them optional at runtime.
DEFAULTS = {
    "project_dirs": ["~/Desktop", "~/Projects", "~/dev", "/opt/lampp/htdocs"],
    "brain_dir": "~/Obsidian/Claude-Brain",
    "icon": "~/.local/share/icons/claude-code.png",
    "ftp_deploy_script": "~/.claude/ftp-deploy.sh",
}


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            cfg.update({k: v for k, v in json.load(fh).items() if v})
    except Exception:
        pass  # no config yet → defaults; the app must never fail to start on this
    return cfg


CONFIG = load_config()
WRAPPER = os.path.join(CLAUDE_DIR, "claude-wrapper.sh")
BRAIN = os.path.expanduser(CONFIG["brain_dir"])
MEMORY_DIR = os.path.join(BRAIN, "memory")
VAULT_NAME = os.path.basename(BRAIN.rstrip("/"))  # used in obsidian:// URIs
ICON_PATH = os.path.expanduser(CONFIG["icon"])
FTP_DEPLOY = os.path.expanduser(CONFIG["ftp_deploy_script"])
# Only keep folders that actually exist — a stock config lists several candidates.
PROJECT_DIRS = [p for p in (os.path.expanduser(d) for d in CONFIG["project_dirs"])
                if os.path.isdir(p)]
HAS_BRAIN = os.path.isdir(MEMORY_DIR)  # no vault → the whole memory UI stays hidden


def has_obsidian():
    """True if an obsidian:// URL handler is registered."""
    try:
        out = subprocess.run(
            ["xdg-mime", "query", "default", "x-scheme-handler/obsidian"],
            capture_output=True, text=True, timeout=3).stdout.strip()
        return bool(out)
    except Exception:
        return False

# ── Palettes (GitHub-style dark & light) ─────────────────────────────────────
# BG doubles as window + terminal background; FG is the terminal foreground.
DARK = {
    "AMBER": "#e0843c", "BG": "#0d1117", "BG_SIDEBAR": "#161b22",
    "BG_CARD": "#1b2129", "FG": "#d0d0d0", "FG_BRIGHT": "#e6edf3",
    "DIM": "#8b949e", "GREEN": "#3fb950", "RED": "#f85149",
    "CARD_HOVER": "#242b35", "BORDER": "#30363d", "SECTION": "#6e7681",
    # 16-colour terminal palette (Afterglow)
    "TERM_PALETTE": [
        "#151515", "#ac4142", "#7e8e50", "#e5b567", "#6c99bb", "#9f4e85",
        "#7dd6cf", "#d0d0d0", "#505050", "#ac4142", "#7e8e50", "#e5b567",
        "#6c99bb", "#9f4e85", "#7dd6cf", "#f5f5f5",
    ],
}
LIGHT = {
    "AMBER": "#bc5c1c", "BG": "#ffffff", "BG_SIDEBAR": "#f6f8fa",
    "BG_CARD": "#ffffff", "FG": "#24292f", "FG_BRIGHT": "#1f2328",
    "DIM": "#656d76", "GREEN": "#1a7f37", "RED": "#cf222e",
    "CARD_HOVER": "#eef1f4", "BORDER": "#d0d7de", "SECTION": "#8c959f",
    # 16-colour terminal palette (GitHub Light)
    "TERM_PALETTE": [
        "#24292e", "#cf222e", "#116329", "#953800", "#0969da", "#8250df",
        "#1b7c83", "#6e7781", "#57606a", "#a40e26", "#1a7f37", "#633c01",
        "#218bff", "#a475f9", "#3192aa", "#8c959f",
    ],
}

# Active theme globals — rebound by apply_palette(); the CSS and terminal
# colouring read these so switching themes is just a rebind + re-apply.
AMBER = BG = BG_SIDEBAR = BG_CARD = FG = FG_BRIGHT = DIM = GREEN = RED = \
    CARD_HOVER = BORDER = SECTION = ""
TERM_PALETTE = []


def apply_palette(pal):
    global AMBER, BG, BG_SIDEBAR, BG_CARD, FG, FG_BRIGHT, DIM, GREEN, RED
    global CARD_HOVER, BORDER, SECTION, TERM_PALETTE
    AMBER = pal["AMBER"]; BG = pal["BG"]; BG_SIDEBAR = pal["BG_SIDEBAR"]
    BG_CARD = pal["BG_CARD"]; FG = pal["FG"]; FG_BRIGHT = pal["FG_BRIGHT"]
    DIM = pal["DIM"]; GREEN = pal["GREEN"]; RED = pal["RED"]
    CARD_HOVER = pal["CARD_HOVER"]; BORDER = pal["BORDER"]; SECTION = pal["SECTION"]
    TERM_PALETTE = pal["TERM_PALETTE"]


apply_palette(DARK)  # sensible default until the window detects the desktop theme


def rgba(hex_str):
    c = Gdk.RGBA()
    c.parse(hex_str)
    return c


def sh(cmd, cwd=None, timeout=3):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                           cwd=cwd, timeout=timeout)
        return r.stdout.strip()
    except Exception:
        return ""


def detect_dark():
    """Follow the desktop's dark/light preference (GNOME color-scheme)."""
    scheme = sh("gsettings get org.gnome.desktop.interface color-scheme").strip("'\"")
    if scheme == "prefer-dark":
        return True
    if scheme == "prefer-light":
        return False
    # 'default' or unknown → fall back to the active GTK theme name
    theme = sh("gsettings get org.gnome.desktop.interface gtk-theme").lower()
    return "dark" in theme


# ── Data gathering ───────────────────────────────────────────────────────────
def get_projects():
    projects = []
    for base in PROJECT_DIRS:
        if not os.path.isdir(base):
            continue
        for d in sorted(os.listdir(base)):
            path = os.path.join(base, d)
            if not os.path.isdir(path):
                continue
            try:
                entries = os.listdir(path)
            except Exception:
                continue
            is_git = os.path.isdir(os.path.join(path, ".git"))
            has_php = any(f.endswith(".php") for f in entries
                         if os.path.isfile(os.path.join(path, f)))
            has_liquid = (os.path.isdir(os.path.join(path, "sections")) or
                          os.path.isdir(os.path.join(path, "templates")))
            has_pkg = os.path.isfile(os.path.join(path, "package.json"))
            has_composer = os.path.isfile(os.path.join(path, "composer.json"))
            if not (is_git or has_php or has_liquid or has_pkg or has_composer):
                continue
            if has_liquid:
                ptype = "Shopify"
            elif has_composer or has_php:
                ptype = "PHP"
            elif has_pkg:
                ptype = "Node"
            else:
                ptype = "Git"
            branch, dirty_count = "", 0
            if is_git:
                branch = sh("git branch --show-current", cwd=path)
                status = sh("git status --porcelain", cwd=path)
                dirty_count = len(status.split("\n")) if status else 0
            projects.append({
                "name": d, "path": path, "type": ptype,
                "branch": branch, "dirty": dirty_count,
            })
    return projects


def _note_title(path):
    """Human title for a per-note memory file: frontmatter description, else # heading, else name."""
    try:
        desc = heading = ""
        in_fm = False
        with open(path, encoding="utf-8") as fh:
            for i, line in enumerate(fh):
                s = line.strip()
                if i == 0 and s == "---":
                    in_fm = True
                    continue
                if in_fm:
                    if s == "---":
                        in_fm = False
                    elif s.startswith("description:"):
                        desc = s.split(":", 1)[1].strip()
                elif s.startswith("# "):
                    heading = s[2:].strip()
                    break
        return desc or heading or os.path.basename(path)[:-3]
    except Exception:
        return os.path.basename(path)[:-3]


def get_memory():
    """Return (counts dict, list of recent (kind, title, filename)) for the per-note memory vault."""
    kinds = {"learnings": "learning_", "errors": "error_", "wins": "win_"}
    counts = {k: 0 for k in kinds}
    entries = []
    try:
        names = os.listdir(MEMORY_DIR)
    except Exception:
        names = []
    for fname in names:
        if not fname.endswith(".md"):
            continue
        for kind, prefix in kinds.items():
            if fname.startswith(prefix):
                counts[kind] += 1
                fpath = os.path.join(MEMORY_DIR, fname)
                try:
                    mtime = os.path.getmtime(fpath)
                except Exception:
                    mtime = 0
                entries.append((mtime, kind, _note_title(fpath), fname))
                break
    entries.sort(key=lambda x: x[0], reverse=True)   # newest first
    recent = [(kind, title, fname) for _, kind, title, fname in entries[:8]]
    return counts, recent


# ── Tab label with rename + close ────────────────────────────────────────────
class TabLabel(Gtk.Box):
    def __init__(self, title, on_close):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._on_close = on_close

        self.label = Gtk.Label(label=title)
        self.label.set_ellipsize(Pango.EllipsizeMode.END)
        self.label.set_xalign(0)
        self.label.set_width_chars(12)      # minimum width → names stay readable
        self.label.set_max_width_chars(24)

        self.entry = Gtk.Entry()
        self.entry.set_width_chars(14)
        self.entry.connect("activate", self._finish_rename)
        self.entry.connect("focus-out-event", lambda *_: self._finish_rename())

        # double-click the label to rename
        evbox = Gtk.EventBox()
        evbox.add(self.label)
        evbox.connect("button-press-event", self._maybe_rename)
        self.evbox = evbox

        close_btn = Gtk.Button()
        close_btn.set_relief(Gtk.ReliefStyle.NONE)
        close_btn.set_focus_on_click(False)
        close_btn.add(Gtk.Image.new_from_icon_name(
            "window-close-symbolic", Gtk.IconSize.MENU))
        close_btn.connect("clicked", lambda *_: self._on_close())

        self.pack_start(evbox, True, True, 0)
        self.pack_start(close_btn, False, False, 0)
        self.show_all()

    def get_title(self):
        return self.label.get_text()

    def _maybe_rename(self, _w, event):
        if event.type == Gdk.EventType._2BUTTON_PRESS:
            self.start_rename()
            return True
        return False

    def start_rename(self):
        self.entry.set_text(self.label.get_text())
        self.remove(self.evbox)
        self.pack_start(self.entry, True, True, 0)
        self.reorder_child(self.entry, 0)
        self.entry.show()
        self.entry.grab_focus()

    def _finish_rename(self, *_):
        new = self.entry.get_text().strip()
        if new:
            self.label.set_text(new)
        if self.entry.get_parent() is self:
            self.remove(self.entry)
            self.pack_start(self.evbox, True, True, 0)
            self.reorder_child(self.evbox, 0)
            self.evbox.show_all()
        return False


# ── Main window ──────────────────────────────────────────────────────────────
class ClaudeHub(Gtk.Window):
    def __init__(self):
        super().__init__(title="Claude Code")
        self.set_default_size(1280, 820)
        self.has_obsidian = has_obsidian()
        self.set_icon_from_file_safe(ICON_PATH)

        # theme state — follows the desktop, toggleable in the header
        self._terminals = []
        self._provider = None
        self.dark = detect_dark()
        self.pal = DARK if self.dark else LIGHT
        apply_palette(self.pal)
        self._sync_gtk_theme()
        self._apply_css()

        header = Gtk.HeaderBar(title="Claude Code")
        header.set_subtitle("Hub")
        header.set_show_close_button(True)
        refresh = Gtk.Button()
        refresh.add(Gtk.Image.new_from_icon_name(
            "view-refresh-symbolic", Gtk.IconSize.BUTTON))
        refresh.connect("clicked", lambda *_: self.reload_sidebar())
        self._theme_btn = Gtk.Button(label="🌙" if self.dark else "☀")
        self._theme_btn.connect("clicked", lambda *_: self.set_theme(not self.dark))
        header.pack_end(refresh)
        header.pack_end(self._theme_btn)
        self.set_titlebar(header)

        # follow the desktop's dark/light changes automatically
        try:
            self._desktop_settings = Gio.Settings.new(
                "org.gnome.desktop.interface")
            self._desktop_settings.connect(
                "changed::color-scheme",
                lambda *_: self.set_theme(detect_dark()))
        except Exception:
            self._desktop_settings = None

        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)

        self.sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.sidebar.get_style_context().add_class("sidebar")
        sidebar_scroll = Gtk.ScrolledWindow()
        sidebar_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        # Fixed-width hub that is ALWAYS visible (no draggable divider that can
        # collapse it to zero).
        sidebar_scroll.set_size_request(310, -1)
        sidebar_scroll.add(self.sidebar)
        hbox.pack_start(sidebar_scroll, False, False, 0)
        hbox.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL),
                        False, False, 0)

        self.notebook = Gtk.Notebook()
        self.notebook.set_scrollable(True)
        self.notebook.popup_disable()
        # permanent "+" tab that always sits right after the real tabs (browser-style)
        self._plus_page = Gtk.Box()
        plus_lbl = Gtk.Label(label="+")
        plus_lbl.get_style_context().add_class("tabplus")
        self.notebook.append_page(self._plus_page, plus_lbl)
        self.notebook.set_tab_reorderable(self._plus_page, False)
        self._welcome_page = None
        self._closing = False
        hbox.pack_start(self.notebook, True, True, 0)

        # quick-action panel on the far right — hidden until a chat tab is active
        self.action_sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        self.action_sep.set_no_show_all(True)
        hbox.pack_start(self.action_sep, False, False, 0)
        self.action_panel = self._build_action_panel()
        self.action_panel.show_all()
        self.action_panel.set_no_show_all(True)
        self.action_panel.hide()
        hbox.pack_start(self.action_panel, False, False, 0)

        self.add(hbox)

        self.build_sidebar()
        self.add_welcome_tab()
        # connect AFTER the initial tabs exist, so startup selection can't trigger "+"
        self.notebook.connect("switch-page", self._on_switch_page)

        self.connect("destroy", Gtk.main_quit)

    # ----- helpers -----
    def set_icon_from_file_safe(self, path):
        try:
            if os.path.isfile(path):
                self.set_icon_from_file(path)
        except Exception:
            pass

    def _apply_css(self):
        css = f"""
        window, .background {{ background-color: {BG}; }}

        /* dark client-side header — cohesive with the dark app (was light) */
        headerbar, .titlebar {{
            background-image: none;
            background-color: {BG_SIDEBAR};
            border-bottom: 1px solid {BORDER};
            box-shadow: none;
            color: {FG_BRIGHT};
            padding: 4px 6px;
        }}
        headerbar button {{
            background-image: none; background-color: transparent;
            border: none; box-shadow: none; text-shadow: none;
            color: {FG_BRIGHT}; border-radius: 7px; padding: 4px 10px;
        }}
        headerbar button:hover {{ background-color: {BG_CARD}; }}
        headerbar .title {{ color: {FG_BRIGHT}; font-weight: bold; }}
        headerbar .subtitle {{ color: {DIM}; }}

        .sidebar {{ background-color: {BG_SIDEBAR}; }}
        .brand {{ color: {AMBER}; font-weight: bold; font-size: 16px; }}
        .brand-sub {{ color: {DIM}; font-size: 11px; }}
        .section {{ color: {SECTION}; font-size: 10px; font-weight: bold;
                    letter-spacing: 1.5px; }}

        .sidebar entry {{
            background-color: {BG}; color: {FG_BRIGHT};
            border: 1px solid {BORDER}; border-radius: 8px;
            padding: 6px 8px; caret-color: {AMBER};
        }}
        .sidebar entry:focus {{ border-color: {AMBER}; }}

        .proj-name {{ color: {FG_BRIGHT}; font-weight: bold; font-size: 13px; }}
        .proj-meta {{ color: {DIM}; font-size: 10px; }}
        .dot-dirty {{ color: {RED}; font-size: 13px; }}
        .dot-clean {{ color: {GREEN}; font-size: 13px; }}

        .projbtn {{
            background-color: {BG_CARD}; border: 1px solid {BORDER};
            border-radius: 8px; padding: 8px 10px; margin: 3px 8px;
            transition: all 140ms ease;
        }}
        .projbtn:hover {{ background-color: {CARD_HOVER}; }}

        /* clickable open-area + Deploy button inside a project card */
        .cardopen {{
            background: transparent; border: none; box-shadow: none;
            border-radius: 6px; padding: 6px 6px;
        }}
        .cardbtn {{
            background: transparent; border: none; box-shadow: none;
            color: {AMBER}; font-weight: bold; font-size: 15px;
            border-radius: 6px; padding: 4px 10px;
        }}
        .cardbtn:hover {{ background-color: {AMBER}; color: {BG}; }}

        /* tooltips — themed box instead of a bare white square */
        tooltip {{ background-color: {BG_SIDEBAR}; border: 1px solid {BORDER};
                   border-radius: 6px; }}
        tooltip label {{ color: {FG_BRIGHT}; padding: 1px 3px; }}

        /* "+" new-tab button in the notebook tab bar */
        .tabplus {{ color: {AMBER}; font-weight: bold; font-size: 16px;
                    background: transparent; border: none; padding: 2px 12px; }}
        .tabplus:hover {{ background-color: {CARD_HOVER}; }}

        /* right-side quick-action bar next to an open chat */
        .actionbar {{ background-color: {BG_SIDEBAR}; padding: 12px 10px; }}
        .barlabel {{ color: {SECTION}; font-size: 10px; font-weight: bold;
                     letter-spacing: 1.5px; }}
        .barbtn {{ background-color: {BG_CARD}; border: 1px solid {BORDER};
                   color: {FG_BRIGHT}; border-radius: 8px; padding: 9px 10px;
                   margin: 2px 0; font-size: 12px; font-weight: bold;
                   transition: all 140ms ease; }}
        .barbtn:hover {{ background-color: {AMBER}; color: {BG};
                         border-color: {AMBER}; }}

        /* primary actions (open folder / open brain) — accent outline */
        .actionbtn {{
            background-color: {BG_CARD}; border: 1px solid {AMBER};
            color: {AMBER}; font-weight: bold;
            border-radius: 8px; padding: 9px 11px; margin: 6px 8px;
            transition: all 140ms ease;
        }}
        .actionbtn:hover {{ background-color: {AMBER}; color: {BG}; }}

        /* memory cards — colored left accent per type */
        .mem-card {{
            background-color: {BG_CARD}; border: 1px solid {BORDER};
            border-left: 3px solid {BORDER};
            border-radius: 6px; padding: 7px 10px; margin: 2px 8px;
            transition: all 140ms ease;
        }}
        .mem-card:hover {{ background-color: {CARD_HOVER}; }}
        .mem-card-learnings {{ border-left-color: {AMBER}; }}
        .mem-card-errors {{ border-left-color: {RED}; }}
        .mem-card-wins {{ border-left-color: {GREEN}; }}
        .mem-line {{ color: {FG_BRIGHT}; font-size: 11px; }}
        .mem-tag {{ color: {AMBER}; font-size: 10px; font-weight: bold; }}

        .footer {{ color: {SECTION}; font-size: 10px; }}
        .welcome-big {{ color: {AMBER}; font-weight: bold; font-size: 28px; }}
        .welcome-sub {{ color: {DIM}; font-size: 13px; }}

        separator {{ background-color: {BORDER}; }}

        /* square the inner terminal — kill ALL rounding in the notebook subtree */
        notebook, notebook *,
        scrolledwindow, scrolledwindow *,
        vte-terminal {{
            border-radius: 0;
            box-shadow: none;
        }}
        """
        if self._provider is None:
            self._provider = Gtk.CssProvider()
            Gtk.StyleContext.add_provider_for_screen(
                Gdk.Screen.get_default(), self._provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self._provider.load_from_data(css.encode())

    def _sync_gtk_theme(self):
        try:
            Gtk.Settings.get_default().set_property(
                "gtk-application-prefer-dark-theme", self.dark)
        except Exception:
            pass

    def set_theme(self, dark):
        """Switch dark/light live: rebind palette, re-apply CSS, recolour terminals."""
        self.dark = bool(dark)
        self.pal = DARK if self.dark else LIGHT
        apply_palette(self.pal)
        self._sync_gtk_theme()
        self._apply_css()
        for term in list(self._terminals):
            self._style_terminal(term)
        if getattr(self, "_theme_btn", None):
            self._theme_btn.set_label("🌙" if self.dark else "☀")

    def _style_terminal(self, term):
        try:
            palette = [rgba(c) for c in TERM_PALETTE]
            term.set_colors(rgba(FG), rgba(BG), palette)
            term.set_color_cursor(rgba(AMBER))
        except Exception:
            pass

    def _forget_terminal(self, term):
        if term in self._terminals:
            self._terminals.remove(term)

    def _section(self, text):
        lbl = Gtk.Label(label=text, xalign=0)
        lbl.get_style_context().add_class("section")
        lbl.set_margin_start(12)
        lbl.set_margin_top(14)
        lbl.set_margin_bottom(4)
        return lbl

    # ----- sidebar -----
    def build_sidebar(self):
        # brand / welcome header
        brand_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        brand_box.set_margin_top(16)
        brand_box.set_margin_start(12)
        brand_box.set_margin_bottom(4)
        b = Gtk.Label(label="✦  CLAUDE CODE", xalign=0)
        b.get_style_context().add_class("brand")
        sub = Gtk.Label(label="vyber projekt → otevře se jako tab", xalign=0)
        sub.get_style_context().add_class("brand-sub")
        brand_box.pack_start(b, False, False, 0)
        brand_box.pack_start(sub, False, False, 0)
        self.sidebar.pack_start(brand_box, False, False, 0)

        # projects
        self.sidebar.pack_start(self._section("PROJEKTY"), False, False, 0)
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Hledat projekt…")
        self.search_entry.set_margin_start(8)
        self.search_entry.set_margin_end(8)
        self.search_entry.set_margin_bottom(4)
        self.search_entry.connect("search-changed", self._on_search)
        self.sidebar.pack_start(self.search_entry, False, False, 0)
        self.proj_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.sidebar.pack_start(self.proj_container, False, False, 0)

        # browse-any-folder button
        browse = Gtk.Button(label="📂  Otevřít jinou složku…")
        browse.get_style_context().add_class("actionbtn")
        browse.connect("clicked", lambda *_: self.browse_folder())
        self.sidebar.pack_start(browse, False, False, 0)

        # memory — only when a vault is configured and present on disk
        self.mem_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        if HAS_BRAIN:
            self.sidebar.pack_start(self._section("OBSIDIAN PAMĚŤ"), False, False, 0)
            brain_btn = Gtk.Button(label="📖  Otevřít Obsidian Brain")
            brain_btn.get_style_context().add_class("actionbtn")
            brain_btn.connect("clicked", lambda *_: self.open_brain())
            self.sidebar.pack_start(brain_btn, False, False, 0)
            self.sidebar.pack_start(self.mem_container, False, False, 0)

        # footer
        foot = Gtk.Label(
            label=f"{os.environ.get('USER','')}  ·  "
                  f"{datetime.datetime.now():%d.%m.%Y}", xalign=0)
        foot.get_style_context().add_class("footer")
        foot.set_margin_start(12)
        foot.set_margin_top(16)
        foot.set_margin_bottom(12)
        self.sidebar.pack_end(foot, False, False, 0)

        self._all_projects = get_projects()
        self.populate_projects()
        self.populate_memory()
        self.sidebar.show_all()

    def reload_sidebar(self):
        self._all_projects = get_projects()
        for c in self.proj_container.get_children():
            self.proj_container.remove(c)
        for c in self.mem_container.get_children():
            self.mem_container.remove(c)
        self.populate_projects(self.search_entry.get_text())
        self.populate_memory()
        self.sidebar.show_all()

    def _on_search(self, entry):
        for c in self.proj_container.get_children():
            self.proj_container.remove(c)
        self.populate_projects(entry.get_text())
        self.proj_container.show_all()

    def populate_projects(self, filter_text=""):
        ft = filter_text.strip().lower()
        shown = 0
        for p in self._all_projects:
            if ft and ft not in p["name"].lower():
                continue
            shown += 1
            btn = Gtk.Button()
            btn.get_style_context().add_class("projbtn")
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            dot = Gtk.Label(label="●")
            dot.get_style_context().add_class(
                "dot-dirty" if p["dirty"] else "dot-clean")
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            name = Gtk.Label(label=p["name"], xalign=0)
            name.get_style_context().add_class("proj-name")
            name.set_ellipsize(Pango.EllipsizeMode.END)
            meta_txt = p["type"]
            if p["branch"]:
                meta_txt += f"  ·  {p['branch']}"
            if p["dirty"]:
                meta_txt += f"  ·  {p['dirty']} změn"
            meta = Gtk.Label(label=meta_txt, xalign=0)
            meta.get_style_context().add_class("proj-meta")
            col.pack_start(name, False, False, 0)
            col.pack_start(meta, False, False, 0)
            row.pack_start(dot, False, False, 0)
            row.pack_start(col, True, True, 0)
            btn.add(row)
            btn.connect("clicked",
                        lambda _w, pp=p: self.open_project(pp["path"], pp["name"]))
            self.proj_container.pack_start(btn, False, False, 0)

        if shown == 0:
            empty = Gtk.Label(label="(nic nenalezeno)", xalign=0)
            empty.get_style_context().add_class("proj-meta")
            empty.set_margin_start(12)
            self.proj_container.pack_start(empty, False, False, 0)

    def populate_memory(self):
        if not HAS_BRAIN:  # section isn't in the sidebar at all
            return
        counts, recent = get_memory()
        summary = Gtk.Label(
            label=f"💡 {counts.get('learnings',0)} learnings   "
                  f"✗ {counts.get('errors',0)} errors   "
                  f"★ {counts.get('wins',0)} wins", xalign=0)
        summary.get_style_context().add_class("mem-line")
        summary.set_margin_start(12)
        summary.set_margin_bottom(6)
        self.mem_container.pack_start(summary, False, False, 0)

        tag_symbol = {"learnings": "💡", "errors": "✗", "wins": "★"}
        for key, text, fname in recent:
            card = Gtk.Button()           # clickable → opens the note in Obsidian
            card.get_style_context().add_class("mem-card")
            card.get_style_context().add_class(f"mem-card-{key}")
            card.set_relief(Gtk.ReliefStyle.NONE)
            line = Gtk.Label(label=f"{tag_symbol.get(key,'·')}  {text}",
                             xalign=0)
            line.get_style_context().add_class("mem-line")
            line.set_ellipsize(Pango.EllipsizeMode.END)
            card.add(line)
            card.connect("clicked", lambda _w, f=fname: self.open_memory_file(f))
            self.mem_container.pack_start(card, False, False, 0)

        if not recent:
            empty = Gtk.Label(label="(zatím prázdné)", xalign=0)
            empty.get_style_context().add_class("proj-meta")
            empty.set_margin_start(12)
            self.mem_container.pack_start(empty, False, False, 0)

    # ----- tabs / terminals -----
    def _on_switch_page(self, notebook, page, num):
        # keep the action panel in sync with the active tab
        self._update_action_panel(page)
        if self._closing:
            return
        # clicking the permanent "+" tab opens a new shell (never stays on it)
        if page is self._plus_page:
            GLib.idle_add(self._plus_activated)

    def _plus_activated(self):
        self.open_shell()
        return False

    def _ensure_selection(self):
        """After a tab closes: never rest on '+'; if no real tabs left, show Welcome."""
        reals = [self.notebook.get_nth_page(i)
                 for i in range(self.notebook.get_n_pages())]
        reals = [p for p in reals if p is not self._plus_page]
        if not reals:
            self.add_welcome_tab()
            return
        cur = self.notebook.get_nth_page(self.notebook.get_current_page())
        if cur is self._plus_page:
            self.notebook.set_current_page(self.notebook.page_num(reals[-1]))

    def add_welcome_tab(self):
        if self._welcome_page is not None:
            return
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)
        big = Gtk.Label(label="✦  Claude Code")
        big.get_style_context().add_class("welcome-big")
        sub = Gtk.Label(
            label="Vyber projekt vlevo — otevře se tady jako nový tab.")
        sub.get_style_context().add_class("welcome-sub")
        sub.set_margin_top(8)
        box.pack_start(big, False, False, 0)
        box.pack_start(sub, False, False, 0)
        pos = self.notebook.page_num(self._plus_page)   # insert before "+"
        self.notebook.insert_page(box, Gtk.Label(label="Welcome"), pos)
        box.show_all()
        self._welcome_page = box
        self.notebook.set_current_page(self.notebook.page_num(box))

    def _new_terminal(self, workdir, argv):
        term = Vte.Terminal()
        term.set_scrollback_lines(100000)
        term.set_mouse_autohide(True)
        try:
            term.set_font(Pango.FontDescription("Monospace 12"))
        except Exception:
            pass
        self._style_terminal(term)
        self._terminals.append(term)
        term.connect("destroy", self._forget_terminal)
        term.spawn_async(
            Vte.PtyFlags.DEFAULT,
            workdir,
            argv,
            None,
            GLib.SpawnFlags.DEFAULT,
            None, None,
            -1,
            None,
            None,
        )
        return term

    def _feed(self, term, text):
        """Type text (e.g. a slash command) into this chat's live session and run it."""
        for attempt in (
            lambda: term.feed_child(text.encode()),
            lambda: term.feed_child(text, len(text)),
            lambda: term.feed_child_binary(text.encode()),
        ):
            try:
                attempt()
                break
            except Exception:
                continue
        term.grab_focus()

    def _current_term(self):
        """The VTE terminal of the currently active chat tab (or None)."""
        idx = self.notebook.get_current_page()
        if idx < 0:
            return None
        page = self.notebook.get_nth_page(idx)
        if isinstance(page, Gtk.ScrolledWindow):
            child = page.get_child()
            if isinstance(child, Vte.Terminal):
                return child
        return None

    def _run_action(self, cmd):
        term = self._current_term()
        if term is None:
            return
        if cmd.endswith("\r"):
            # feed the text first, then send Enter as a SEPARATE keystroke a moment
            # later — a \r bundled with pasted text is treated as a newline, not submit
            self._feed(term, cmd[:-1])
            GLib.timeout_add(180, self._submit_later, term)
        else:
            self._feed(term, cmd)

    def _submit_later(self, term):
        self._feed(term, "\r")
        return False

    def _build_action_panel(self):
        """Quick-action panel on the right — visible only when a chat tab is active.
        A click sends the slash command into that chat; '\\r'-terminated ones run
        immediately, the '…' ones (need a URL) wait for you to finish typing."""
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        panel.get_style_context().add_class("actionbar")
        panel.set_size_request(184, -1)

        # Custom slash commands don't load in this Claude Code build, so we feed a
        # plain instruction instead — Claude has the memory/deploy rules in CLAUDE.md.
        actions = []
        if HAS_BRAIN:  # memory buttons make no sense without a vault
            actions += [
                ("💾  Uložit do paměti",
                 f"Ulož do Obsidian paměti ({MEMORY_DIR}/) co jsme právě "
                 "dělali: samostatná poznámka s frontmatterem (name, description, "
                 "metadata.type). DŮLEŽITÉ pro graf Obsidianu — štědře propoj "
                 "[[wikilinky]] na související existující poznámky i na příslušný "
                 "rozcestník (moc_*). Přidej řádek do MEMORY.md a odkaz i do té MOC.\r"),
                ("📝  Poznámka projektu",
                 "Založ nebo aktualizuj poznámku project_<slug>.md k tomuto projektu v "
                 f"{MEMORY_DIR}/ (stack, hosting, deploy, TODO) a propoj "
                 "ji [[odkazy]].\r"),
            ]
        actions += [
            ("⬆  Deploy",
             "Nasaď tento projekt: je-li v něm .ftp-deploy.json, spusť "
             f"bash {FTP_DEPLOY} . --yes ; jinak git add -A, commit a push.\r"),
            ("🐙  Push na GitHub",
             "Zacommituj a pushni tenhle projekt na GitHub: ukaž mi krátce git status, "
             "pak git add -A a commit s výstižnou zprávou odvozenou z diffu (česky, "
             "formát typ(rozsah): popis), pak git push. Nemá-li branch upstream, použij "
             "git push -u origin <branch>. Nikdy nedělej force push a při konfliktu se "
             "nejdřív zeptej. Na konci mi napiš hash commitu a kam se to pushlo.\r"),
            ("📊  Přehled projektů",
             f"Spusť python3 {CLAUDE_DIR}/hooks/save-session.py a ukaž mi přehled "
             "projektů a jejich git stav ze session-state.md.\r"),
            ("🖼  Screenshot…",
             "Udělej screenshot webu (headless Chrome, desktop i mobil) této URL: "),
        ]
        for label, cmd in actions:
            b = Gtk.Button(label=label)
            b.get_style_context().add_class("barbtn")
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.connect("clicked", lambda _w, c=cmd: self._run_action(c))
            panel.pack_start(b, False, False, 0)
        return panel

    def _update_action_panel(self, page):
        """Show the action panel only when the active tab is a chat (VTE terminal)."""
        is_chat = (isinstance(page, Gtk.ScrolledWindow) and
                   isinstance(page.get_child(), Vte.Terminal))
        self.action_panel.set_visible(is_chat)
        self.action_sep.set_visible(is_chat)

    def _add_terminal_tab(self, title, workdir, argv):
        # drop the placeholder welcome tab when the first real tab opens
        if self._welcome_page is not None:
            widx = self.notebook.page_num(self._welcome_page)
            if widx != -1:
                self._closing = True
                self.notebook.remove_page(widx)
                self._closing = False
            self._welcome_page = None

        term = self._new_terminal(workdir, argv)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.add(term)
        scroll.show_all()

        def close_tab():
            idx = self.notebook.page_num(scroll)
            if idx == -1:
                return
            self._closing = True
            self.notebook.remove_page(idx)
            self._closing = False
            self._ensure_selection()

        tab_label = TabLabel(title, close_tab)
        pos = self.notebook.page_num(self._plus_page)   # insert before "+"
        idx = self.notebook.insert_page(scroll, tab_label, pos)
        self.notebook.set_tab_reorderable(scroll, True)
        self.notebook.set_current_page(idx)
        term.grab_focus()

        # close the tab when the shell finally exits
        term.connect("child-exited", lambda *_: close_tab())
        return term

    def open_project(self, path, name):
        cmd = (f'cd "{path}" && bash "{WRAPPER}"; '
               f'echo; echo "[ session ukončena — tab zůstává jako shell ]"; '
               f'exec bash')
        self._add_terminal_tab(name, path, ["/bin/bash", "-c", cmd])

    def open_shell(self):
        self._add_terminal_tab("shell", HOME, ["/bin/bash"])

    def deploy_project(self, p):
        """Deploy in a new terminal tab: FTP if .ftp-deploy.json exists, else /deploy."""
        path, name = p["path"], p["name"]
        if os.path.isfile(os.path.join(path, ".ftp-deploy.json")) and \
                os.path.isfile(FTP_DEPLOY):
            cmd = (f'bash "{FTP_DEPLOY}" "{path}"; '
                   f'echo; echo "[ deploy hotový — tab zůstává jako shell ]"; '
                   f'exec bash')
        else:
            cmd = (f'cd "{path}" && bash "{WRAPPER}" "/deploy"; '
                   f'echo; echo "[ session ukončena — tab zůstává jako shell ]"; '
                   f'exec bash')
        self._add_terminal_tab(f"deploy: {name}", path, ["/bin/bash", "-c", cmd])

    def _project_menu(self, widget, p):
        """Options popup for a project card."""
        path, name = p["path"], p["name"]
        menu = Gtk.Menu()

        def add(label, cb):
            mi = Gtk.MenuItem(label=label)
            mi.connect("activate", lambda *_: cb())
            menu.append(mi)

        add("▸   Otevřít v Claude", lambda: self.open_project(path, name))
        add("⬆   Deploy", lambda: self.deploy_project(p))
        add("📝   Poznámka do paměti (/project)", lambda: self._add_terminal_tab(
            f"note: {name}", path,
            ["/bin/bash", "-c",
             f'cd "{path}" && bash "{WRAPPER}" "/project"; '
             f'echo; echo "[ hotovo — tab zůstává jako shell ]"; exec bash']))
        add("💻   Shell tady", lambda: self._add_terminal_tab(
            name, path, ["/bin/bash"]))
        add("📁   Otevřít složku", lambda: self.open_path(path))
        menu.show_all()
        menu.popup_at_widget(widget, Gdk.Gravity.SOUTH_WEST,
                             Gdk.Gravity.NORTH_WEST, None)

    def open_path(self, path):
        """Open a folder, file or URI with the system default handler."""
        try:
            subprocess.Popen(["xdg-open", path],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        except Exception:
            pass

    def open_brain(self):
        """Open the whole Claude-Brain vault in Obsidian (or the folder)."""
        if self.has_obsidian:
            self.open_path(f"obsidian://open?vault="
                           f"{urllib.parse.quote(VAULT_NAME)}")
        else:
            self.open_path(BRAIN)

    def open_memory_file(self, fname):
        """Open a memory note (e.g. learnings.md) in Obsidian (or the file)."""
        if not fname:
            return self.open_brain()
        if self.has_obsidian:
            note = "memory/" + (fname[:-3] if fname.endswith(".md") else fname)
            self.open_path(
                f"obsidian://open?vault={urllib.parse.quote(VAULT_NAME)}"
                f"&file={urllib.parse.quote(note)}")
        else:
            self.open_path(os.path.join(MEMORY_DIR, fname))

    def browse_folder(self):
        dlg = Gtk.FileChooserDialog(
            title="Vyber složku projektu", parent=self,
            action=Gtk.FileChooserAction.SELECT_FOLDER)
        dlg.add_buttons("Zrušit", Gtk.ResponseType.CANCEL,
                        "Otevřít", Gtk.ResponseType.OK)
        if dlg.run() == Gtk.ResponseType.OK:
            path = dlg.get_filename()
            self.open_project(path, os.path.basename(path))
        dlg.destroy()


def main():
    win = ClaudeHub()
    win.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
