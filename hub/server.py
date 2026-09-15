"""
Local HTTP + WebSocket server behind the hub UI.

It binds to 127.0.0.1 on a random port and every request must carry the token
printed into the page URL at startup — this process can spawn shells, so it is
never reachable by anything that didn't get the token from us.

Terminal sessions live in the server, not in the browser connection: reloading
the page re-attaches to the running Claude sessions and replays their recent
output instead of killing them.

Several pages can watch the same sessions at once — on the gateway the app and
a browser open the same user's hub. Output goes to every page, tabs opened,
closed or renamed in one show up in the others, and the pty takes the size of
the page that was used last (typing or focus), so pages don't fight over it.
"""
import base64
import codecs
import hashlib
import json
import mimetypes
import os
import posixpath
import secrets
import string
import struct
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import account, connect, core, pty_backend, qr, remote, stats

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_FRAME = 4 * 1024 * 1024          # a client frame is keystrokes; 4 MB is plenty
SCROLLBACK = 256 * 1024              # replayed to the page after a reload
COOKIE_NAME = "hub_phone"            # so a paired phone opens without the QR
COOKIE_MAX_AGE = 60 * 60 * 24 * 365

# A phone whose token was rotated still has the icon on its home screen; a bare
# 403 there looks like the hub is broken.
PAIR_AGAIN = """<!doctype html><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Claude Code Hub</title>
<style>body{font:16px/1.6 system-ui,sans-serif;margin:0;min-height:100vh;
display:grid;place-items:center;background:#16150f;color:#e8e3d3;padding:24px}
div{max-width:22rem;text-align:center}b{color:#e0a458}</style>
<div><p><b>Telefon už není spárovaný.</b></p>
<p>Otevři na počítači Claude Code Hub → Nastavení → Telefon a načti QR kód znovu.</p></div>
"""


# ── WebSocket plumbing (RFC 6455, only what we need) ─────────────────────────
class WSConn:
    """One websocket connection. Safe to send from several threads."""

    def __init__(self, sock):
        self.sock = sock
        self._lock = threading.Lock()
        self.closed = False

    def send(self, payload, opcode=0x1):
        header = bytearray([0x80 | opcode])
        n = len(payload)
        if n < 126:
            header.append(n)
        elif n < 65536:
            header.append(126)
            header += struct.pack(">H", n)
        else:
            header.append(127)
            header += struct.pack(">Q", n)
        with self._lock:
            if self.closed:
                return
            try:
                self.sock.sendall(bytes(header) + payload)
            except OSError:
                self.closed = True

    def send_json(self, obj):
        self.send(json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def close(self):
        self.closed = True
        try:
            self.sock.shutdown(2)
        except OSError:
            pass


def read_frame(rfile):
    """Return (fin, opcode, payload) or None at end of stream."""
    head = rfile.read(2)
    if len(head) < 2:
        return None
    b1, b2 = head[0], head[1]
    fin, opcode = b1 & 0x80, b1 & 0x0F
    masked, length = b2 & 0x80, b2 & 0x7F
    if length == 126:
        length = struct.unpack(">H", rfile.read(2))[0]
    elif length == 127:
        length = struct.unpack(">Q", rfile.read(8))[0]
    if length > MAX_FRAME:
        return None
    mask = rfile.read(4) if masked else b""
    payload = rfile.read(length) if length else b""
    if len(payload) < length:
        return None
    if masked:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return bool(fin), opcode, payload


# ── Terminal sessions ────────────────────────────────────────────────────────
class Session:
    """A pty running one bash session, plus the scrollback we replay on reload."""

    def __init__(self, sid, title, kind, path, argv, cwd, cols, rows,
                 agent="", model="", env=None):
        self.id = sid
        self.title = title
        self.kind = kind
        self.path = path
        # Čím tab jede, se drží tady: po reloadu stránky se sessions obnovují
        # ze serveru, takže bez toho by tab po F5 zapomněl, kdo v něm běží.
        self.agent = agent
        self.model = model
        child = core.child_env()
        child.update(env or {})
        self.pty = pty_backend.spawn(argv, cwd=cwd, env=child,
                                     cols=cols, rows=rows)
        self.buffer = bytearray()
        # Tab může vidět víc oken naráz — appka i prohlížeč na tentýž prostor.
        # Výpis jde všem; pty ale má jen jeden rozměr a ten drží okno, které se
        # naposledy používalo (`driver`). Rozměry ostatních se pamatují, aby
        # rozměr šlo předat hned, jak se začne pracovat tam.
        self.conns = set()
        self.sizes = {}
        self.driver = None
        self.size = (cols, rows)
        self.exited = False
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._lock = threading.Lock()
        self.thread = threading.Thread(target=self._pump, daemon=True)
        self.thread.start()

    def _pump(self):
        while True:
            data = self.pty.read()
            if not data:
                break
            # Appending and sending under one lock is what keeps a replay from
            # interleaving with live output when a page attaches mid-stream.
            with self._lock:
                self.buffer += data
                if len(self.buffer) > SCROLLBACK:
                    del self.buffer[:len(self.buffer) - SCROLLBACK]
                text = self._decoder.decode(data)
                if text:
                    msg = {"t": "out", "id": self.id, "d": text}
                    for conn in self.conns:
                        conn.send_json(msg)
        # Pod zámkem spolu s attach(): každé okno dostane „exit" právě jednou.
        with self._lock:
            self.exited = True
            conns = list(self.conns)
        for conn in conns:
            conn.send_json({"t": "exit", "id": self.id})

    def attach(self, conn):
        """Add a page to the viewers and replay what it missed.

        The replay goes only to that page — the others already show it."""
        with self._lock:
            self.conns.add(conn)
            backlog = bytes(self.buffer)
            if backlog:
                # Decoded separately: the live decoder carries partial-character
                # state for the stream and replay must not disturb it.
                conn.send_json({"t": "out", "id": self.id,
                                "d": backlog.decode("utf-8", "replace")})
            exited = self.exited
        if exited:
            conn.send_json({"t": "exit", "id": self.id})

    def detach(self, conn):
        """The page went away. If it held the pty size, another page takes it."""
        with self._lock:
            self.conns.discard(conn)
            self.sizes.pop(conn, None)
            if self.driver is conn:
                # Převezme ho okno, které svůj rozměr hlásilo naposledy.
                self.driver = next(reversed(self.sizes), None)
                if self.driver is not None:
                    self._apply(self.sizes[self.driver])

    def resize(self, conn, cols, rows):
        """Remember the page's terminal size; the pty follows only the driver."""
        with self._lock:
            self.sizes.pop(conn, None)       # na konec: „hlásil naposledy"
            self.sizes[conn] = (cols, rows)
            if self.driver is None:
                self.driver = conn
            if self.driver is conn:
                self._apply((cols, rows))

    def drive(self, conn):
        """The page is in use now (typing, focus) — the pty takes its size.

        Bez toho si okna rozměr přetahují: každé ho posílá při každém přepočtu
        a Claude Code se pak v tom druhém vykreslí na cizí šířku."""
        if self.driver is conn:              # při psaní skoro vždycky
            return
        with self._lock:
            size = self.sizes.get(conn)
            if size is None:
                return
            self.driver = conn
            self._apply(size)

    def _apply(self, size):
        # Pod zámkem. Stejný rozměr znovu neposílat: ConPTY na Windows při
        # každém resize překreslí celou obrazovku.
        if size != self.size:
            self.size = size
            self.pty.resize(*size)

    def close(self):
        with self._lock:
            self.conns.clear()
            self.sizes.clear()
            self.driver = None
        try:
            self.pty.close()
        except Exception:
            pass

    def info(self):
        return {"id": self.id, "title": self.title, "kind": self.kind,
                "path": self.path, "exited": self.exited,
                "agent": self.agent, "model": self.model}


class Hub:
    """Server-side state: the open sessions and who is currently watching them."""

    def __init__(self):
        self.sessions = {}
        self.clients = 0
        self.conns = set()               # otevřená okna — komu poslat hlášku
        self.last_empty_at = None
        self._next_id = 1
        self._lock = threading.Lock()

    def new_id(self):
        with self._lock:
            sid = self._next_id
            self._next_id += 1
            return sid

    def open(self, kind, path, title, cols, rows, agent="", model=""):
        script, cwd, env = self._command_for(kind, path, agent, model)
        # Agent si model mohl doplnit sám (Ollama bez modelu nespustíš) —
        # ať se to dozví i tab, jinak by chip v bublině hlásil „výchozí".
        model = env.get("HUB_AGENT_MODEL") or model
        sid = self.new_id()
        # Podle téhle značky Stop hook pozná, ve kterém tabu session běží,
        # a zavření tabu pak uloží do paměti právě ji.
        env = {**env, "HUB_TAB": core.tab_tag(sid)}
        session = Session(sid, title, kind, path, core.bash_argv(script), cwd,
                          cols, rows, agent=agent, model=model, env=env)
        self.sessions[sid] = session
        return session

    @staticmethod
    def _command_for(kind, path, agent="", model=""):
        """(příkaz, pracovní složka, prostředí navíc) pro daný druh tabu."""
        if kind == "shell":
            return core.cmd_shell(), (path or core.HOME), {}
        if kind == "deploy":
            return core.cmd_deploy(path), path, {}
        if kind.startswith("install:"):
            return core.cmd_install(kind.split(":", 1)[1]), (path or core.HOME), {}
        if kind.startswith("auth:"):
            return core.cmd_auth(kind.split(":", 1)[1]), (path or core.HOME), {}
        if kind.startswith("slash:"):
            # Slash příkazy jsou naše skilly z ~/.claude/skills — rozumí jim jen
            # agent, který je čte. Jinému by se předal jako holý text promptu.
            spec = core.agent_spec(agent)
            if not spec.get("skills"):
                agent = ""
            script, env = core.cmd_agent(path, agent,
                                         slash="/" + kind.split(":", 1)[1])
            return script, path, env
        script, env = core.cmd_agent(path, agent, model=model)
        return script, path, env

    def close(self, sid, autosave=True):
        session = self.sessions.pop(sid, None)
        if session:
            session.close()
            if autosave:
                core.autosave_tabs_closed([sid])

    def shutdown(self):
        sids = list(self.sessions)
        for sid in sids:
            self.close(sid, autosave=False)
        core.autosave_tabs_closed(sids)      # jeden proces na všechny taby

    def broadcast(self, message, skip=None):
        for conn in list(self.conns):
            if conn is skip:
                continue
            try:
                conn.send_json(message)
            except Exception:
                pass


HUB = Hub()


# ── HTTP ─────────────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    server_version = "ClaudeCodeHub"
    protocol_version = "HTTP/1.1"

    # ---- helpers ----
    def log_message(self, *_args):
        pass  # the terminal belongs to the app, not to request logging

    @property
    def token(self):
        return self.server.token

    def _cookie_token(self):
        """Token left behind by pairing, so the phone's icon opens straight in."""
        for part in self.headers.get("Cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE_NAME:
                return urllib.parse.unquote(value)
        return ""

    def _authorised(self, query):
        given = (query.get("t", [""])[0]
                 or self.headers.get("X-Hub-Token", "")
                 or self._cookie_token())
        return secrets.compare_digest(given, self.token)

    def _secure(self):
        return self.headers.get("X-Forwarded-Proto", "") == "https"

    def _origin_ok(self):
        """Reject an Origin that is not this very page.

        Behind `tailscale serve` the page is https://<stroj>.ts.net while we
        listen on loopback, so the fixed loopback URL is no longer the whole
        story: the addresses the listener was started for are kept on the
        server object, and the request's own Host header is accepted too.
        """
        origin = self.headers.get("Origin", "")
        if not origin:
            return True
        if origin in self.server.origins:
            return True
        host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host", "")
        return bool(host) and urllib.parse.urlsplit(origin).netloc == host

    def _pair_cookie(self):
        """Set-Cookie value that keeps this phone paired."""
        flags = "; Secure" if self._secure() else ""
        return (f"{COOKIE_NAME}={urllib.parse.quote(self.token)}; Path=/; "
                f"Max-Age={COOKIE_MAX_AGE}; SameSite=Lax; HttpOnly{flags}")

    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    # ---- routes ----
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        route = parsed.path

        if route == "/ws":
            return self._websocket(query)
        if route == "/":
            if not self._authorised(query):
                if self.server.remote:
                    return self._send(403, PAIR_AGAIN.encode("utf-8"),
                                      "text/html; charset=utf-8")
                return self._send(403, b"Neplatny token.")
            extra = {"Set-Cookie": self._pair_cookie()} if (
                self.server.remote and query.get("t")) else None
            return self._static("index.html", extra)
        if route.startswith("/api/"):
            if not self._authorised(query):
                return self._send(403, b"Neplatny token.")
            return self._api(route[5:], query)
        return self._static(route.lstrip("/"))

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        if not parsed.path.startswith("/api/") or not self._authorised(query):
            return self._send(403, b"Neplatny token.")
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            payload = {}
        return self._api(parsed.path[5:], query, payload)

    def _static(self, relpath, extra=None):
        # posixpath.normpath + strip leading separators keeps this inside STATIC_DIR
        rel = posixpath.normpath("/" + relpath).lstrip("/")
        full = os.path.join(STATIC_DIR, *rel.split("/"))
        # Porovnání přes commonpath, ne startswith: to by pustilo i sourozence
        # jménem `static-cokoliv`, který do aplikace nepatří.
        full = os.path.abspath(full)
        try:
            inside = os.path.commonpath([full, STATIC_DIR]) == STATIC_DIR
        except ValueError:                       # jiný disk na Windows
            inside = False
        if not inside or not os.path.isfile(full):
            return self._send(404, b"404")
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        with open(full, "rb") as fh:
            body = fh.read()
        self._send(200, body, ctype, extra)

    def _api(self, name, query, payload=None):
        try:
            return self._api_inner(name, query, payload)
        except Exception as exc:
            # Bez tohohle by chyba skončila jen ve stacktrace, který nikdo
            # nevidí — okno na Windows nemá konzoli.
            core.log_error(f"/api/{name} selhalo", exc)
            return self._json({"error": f"Chyba serveru: {exc}"}, 500)

    def _api_inner(self, name, query, payload=None):
        payload = payload or {}
        if name == "state":
            counts, recent = core.get_memory()
            return self._json({
                "projects": core.get_projects(),
                "memory": {"counts": counts, "recent": recent,
                           "enabled": core.HAS_BRAIN},
                "skills": core.installed_skills(),
                # Bez verzí (ty stojí spuštění každého CLI) — na nabídku
                # „otevřít v…" a odznak na tabu stačí jméno, barva a PATH.
                "agents": core.agents.detect(core._agents_extra(),
                                             with_version=False),
                "default_agent": core.default_agent(),
                "model": core.current_model(),
                "palette": {"dark": core.DARK, "light": core.LIGHT},
                "doctor": core.doctor(),
                "user": os.environ.get("USER") or os.environ.get("USERNAME") or "",
                "obsidian": core.has_obsidian(),
                "onboarded": bool(core.CONFIG.get("onboarded")),
                "config": {"project_dirs": core.CONFIG.get("project_dirs") or [],
                           "brain_dir": core.CONFIG.get("brain_dir") or "",
                           "newtab": core.CONFIG.get("newtab") or {},
                           "show_archived": bool(core.CONFIG.get("show_archived")),
                           "dev_mode": bool(core.CONFIG.get("dev_mode")),
                           # Server, na kterém má appka účet, a jestli se má
                           # otevírat rovnou tam. Token sem nepatří — stránka
                           # ho nepotřebuje a /api/state se kreslí všude.
                           "gw_server": core.CONFIG.get("gw_server") or "",
                           "gw_email": (core.CONFIG.get("gw_user") or {}).get("email", ""),
                           "gw_logged_in": bool(core.CONFIG.get("gw_token")),
                           "server_mode": bool(core.CONFIG.get("server_mode")),
                           # Vyplněné jen na instanci běžící na bráně — podle
                           # toho nastavení pozná, že je na serveru.
                           "gateway_user": core.CONFIG.get("gateway_user") or None},
                "cloud": core.cloud_folders(),
                "vaults": core.obsidian_vaults(),
                "memory_link": core.memory_link_path(),
                "suggest_dirs": core.suggest_project_dirs(),
                "home": core.HOME,
                "version": core.version_info(),
                "vault_git": dict(zip(("is_repo", "remote"), core.vault_git_state())),
                "vault_autosync": bool(core.CONFIG.get("vault_autosync")),
                "memory_autosave": core.autosave_enabled(),
                "autosave_recent": core.autosave_recent(),
            })
        if name == "open-path":
            target = payload.get("path", "")
            if payload.get("kind") == "brain":
                target = (f"obsidian://open?vault="
                          f"{urllib.parse.quote(core.VAULT_NAME)}"
                          if core.has_obsidian() else core.BRAIN)
            elif payload.get("kind") == "note":
                fname = payload.get("file", "")
                if core.has_obsidian():
                    note = "memory/" + (fname[:-3] if fname.endswith(".md") else fname)
                    target = (f"obsidian://open?vault="
                              f"{urllib.parse.quote(core.VAULT_NAME)}"
                              f"&file={urllib.parse.quote(note)}")
                else:
                    target = os.path.join(core.MEMORY_DIR, fname)
            return self._json({"ok": core.open_path(target)})
        if name == "image":
            raw = os.path.abspath(os.path.expanduser(
                query.get("path", [""])[0]))
            try:
                inside = os.path.commonpath(
                    [raw, core.IMAGE_DIR]) == core.IMAGE_DIR
            except ValueError:
                inside = False
            if not inside or not os.path.isfile(raw):
                return self._send(404, b"404")
            ctype = mimetypes.guess_type(raw)[0] or "application/octet-stream"
            with open(raw, "rb") as fh:
                return self._send(200, fh.read(), ctype)
        if name == "listdir":
            return self._json(listdir(query.get("path", [""])[0]))
        if name == "clipboard":
            which = (payload.get("which") or query.get("which", ["clipboard"])[0])
            if which not in ("clipboard", "primary"):
                which = "clipboard"
            if "text" in payload:                  # POST = zápis
                return self._json({"ok": core.clipboard_write(
                    str(payload["text"]), which)})
            text = core.clipboard_read(which)      # GET = čtení
            if text is None:
                return self._json(
                    {"error": "Na tomhle stroji chybí nástroj na schránku "
                              "(xclip / wl-clipboard)."}, 501)
            if not text and which == "clipboard":
                # Screenshot na schránce žádný text nemá a terminálu se obrázek
                # podat nedá — jen cesta k odložené kopii.
                path = core.clipboard_image()
                if path:
                    return self._json({"image": path})
            return self._json({"text": text})
        if name == "upload":
            try:
                raw = base64.b64decode(payload.get("data", ""), validate=True)
            except Exception:
                return self._json({"error": "Poškozená data."}, 400)
            if not raw:
                return self._json({"error": "Prázdný soubor."}, 400)
            if len(raw) > core.MAX_UPLOAD:
                return self._json(
                    {"error": f"Soubor je větší než "
                              f"{core.MAX_UPLOAD // (1024 * 1024)} MB."}, 413)
            try:
                return self._json({"path": core.save_upload(
                    payload.get("name", ""), raw)})
            except Exception as exc:
                return self._json({"error": f"Nepodařilo se uložit: {exc}"}, 500)
        if name == "account":
            # Účet na bráně: ověření adresy, přihlášení, stav a přechod okna
            # do prostoru na serveru.
            payload = payload or {}
            action = payload.get("action", "status")
            if action == "probe":
                return self._json(account.probe(payload.get("server", "")))
            if action == "login":
                return self._json(account.login(
                    payload.get("server", ""),
                    payload.get("email", ""),
                    payload.get("password", "")))
            if action == "logout":
                return self._json(account.logout())
            if action in ("handoff", "connect"):
                # `connect` = přejít na server a pamatovat si to: appka se
                # příště otevře rovnou tam. Zapíše se, až když předání vyšlo —
                # jinak by se příští start pokoušel o server, který nechce.
                res = account.handoff(timeout=8)
                if action == "connect" and res.get("url"):
                    core.save_config({"server_mode": True})
                return self._json(res)
            if action == "local":
                core.save_config({"server_mode": False})
                return self._json({"ok": True})
            return self._json(account.status(
                timeout=5 if payload.get("quick") else account.TIMEOUT))
        if name == "remote":
            # Telefon: stav, párovací QR a zapnutí/vypnutí druhého listeneru.
            action = (payload or {}).get("action", "")
            if action == "enable":
                core.save_config({"remote_enabled": True})
                state = start_remote()
            elif action == "disable":
                core.save_config({"remote_enabled": False})
                stop_remote()
                state = remote_status()
            elif action == "rotate":
                remote.rotate()
                state = start_remote() if REMOTE["server"] or \
                    core.CONFIG.get("remote_enabled") else remote_status()
            elif action == "port":
                try:
                    wanted = int((payload or {}).get("port") or 0)
                except (TypeError, ValueError):
                    wanted = 0
                if not 1024 <= wanted <= 65535:
                    return self._json({"error": "Port musí být 1024-65535."}, 400)
                core.save_config({"remote_port": wanted})
                state = start_remote() if core.CONFIG.get("remote_enabled") \
                    else remote_status()
            else:
                state = remote_status()
            if state.get("url"):
                try:
                    state["qr"] = qr.svg(state["url"])
                except Exception as exc:
                    core.log_error("QR kód se nepovedl", exc)
            return self._json(state)
        if name == "config":
            allowed = ("project_dirs", "brain_dir", "onboarded", "vault_autosync",
                       "newtab", "extra_projects", "show_archived",
                       "agents", "default_agent", "project_agents",
                       "remote_keep_running", "dev_mode", "memory_autosave")
            updates = {k: v for k, v in payload.items() if k in allowed}
            if not updates:
                return self._json({"error": "Nic k uložení."}, 400)
            try:
                core.save_config(updates)
            except Exception as exc:
                return self._json({"error": f"Konfig nejde zapsat: {exc}"}, 500)
            return self._json({"ok": True, "brain_dir": core.BRAIN,
                               "project_dirs": core.PROJECT_DIRS})
        if name == "agents":
            # Verze se zjišťují spuštěním každého CLI, takže to jede na pozadí
            # stejně jako MCP — stránka se na to nesmí zdržet.
            state = core.job_state("agents")
            if query.get("refresh") or not state.get("done"):
                core.start_job("agents", lambda: core.agents_state())
                state = core.job_state("agents")
            if state.get("done"):
                return self._json({"running": False, **(state.get("result") or {})})
            return self._json({"running": True, "step": state.get("step") or ""})
        if name == "project":
            return self._project(payload)
        if name == "vault":
            return self._vault(payload)
        if name == "update-check":
            # Síťový dotaz je zvlášť, aby se na něj nečekalo při každém načtení.
            return self._json(core.version_info(check_remote=True))
        if name == "update":
            if core.on_gateway():
                # Na serveru aktualizuje server sám (gateway/update.sh). Instalace
                # do domova prostoru by nic nezměnila, jen by mátla verzí.
                return self._json({"started": False, "running": False, "result": {
                    "ok": False,
                    "detail": "Hub na serveru se aktualizuje sám každou noc."}})
            started = core.start_update()
            return self._json({"started": started, **core.update_state()})
        if name == "log":
            if "text" in payload:
                # Chyby ze stránky patří do stejného souboru jako ty ze serveru,
                # jinak by se problém hledal na dvou místech.
                core.log(f"stránka: {str(payload['text'])[:500]}",
                         payload.get("level") or "error")
                return self._json({"ok": True})
            return self._json({"lines": core.log_tail(
                int(query.get("lines", ["300"])[0] or 300))})
        if name == "log-clear":
            core.log_clear()
            return self._json({"ok": True})
        if name == "report":
            return self._json({"text": core.report_bundle()})
        if name == "stats":
            # První výpočet čte skoro gigabajt přepisů, takže na pozadí;
            # další už jedou z mezipaměti a vrátí se hned.
            cached = core.job_state("stats")
            if cached.get("running"):
                return self._json({"running": True, "step": cached.get("step", "")})
            if query.get("refresh") or not (cached.get("result") or {}).get("tokens"):
                core.start_job("stats", lambda: stats.collect(
                    progress=lambda m: core.job_step("stats", m)))
                return self._json({"running": True, "step": "počítám…"})
            return self._json({"running": False, **(cached.get("result") or {})})
        if name == "connect":
            # Služby pro člověka — Freelo, Canva, Ecomail, Google, i více účtů
            # (hub/connect.py). Nad technickým katalogem /api/mcp.
            action = payload.get("action") or ""
            if action == "add":
                result = connect.add_account(payload.get("service", ""),
                                             payload.get("label", ""),
                                             payload.get("account", ""))
                return self._json(result, 200 if result.get("ok") else 400)
            if action == "login":
                result = connect.login_start(payload.get("name", ""))
                return self._json(result, 200 if result.get("ok") else 400)
            if action == "finish":
                return self._json(connect.login_finish(payload.get("id", ""),
                                                       payload.get("url", "")))
            if action == "status":
                return self._json(connect.login_status(payload.get("id", "")))
            if action == "cancel":
                return self._json(connect.login_cancel(payload.get("id", "")))
            if action == "remove":
                result = connect.remove_account(payload.get("service", ""),
                                                payload.get("name", ""))
                return self._json(result, 200 if result.get("ok") else 400)
            if action == "google-client":
                result = connect.save_google_client(payload.get("client_id", ""),
                                                    payload.get("client_secret", ""))
                return self._json(result, 200 if result.get("ok") else 400)
            return self._json(connect.services(
                bool(query.get("refresh") or payload.get("refresh"))))
        if name == "mcp":
            # Napojení na cizí služby. Zdravotní kontrola oslovuje každý server
            # zvlášť (~10 s), takže stejný postup jako u statistik: spočítat na
            # pozadí a stránce vracet, co už je hotové.
            action = payload.get("action") or ""
            if action == "add":
                result = core.mcp_add(payload.get("name", ""),
                                      payload.get("values") or {},
                                      payload.get("scope") or "user",
                                      payload.get("path") or "")
                return self._json(result, 200 if result.get("ok") else 400)
            if action == "remove":
                result = core.mcp_remove(payload.get("name", ""),
                                         payload.get("scope") or "user",
                                         payload.get("path") or "")
                return self._json(result, 200 if result.get("ok") else 400)
            cached = core.job_state("mcp")
            # Účet se čte ze souboru, ne ze sítě — posílá se i do rozdělané
            # kontroly, ať je hned vidět, ke komu konektory patří.
            acct = core.claude_account()
            if cached.get("running"):
                return self._json({"running": True, "catalog": core.MCP_CATALOG,
                                   "account": acct,
                                   "step": cached.get("step", "")})
            done = cached.get("result") or {}
            if query.get("refresh") or payload.get("refresh") or not done:
                core.start_job("mcp", core.mcp_list)
                return self._json({"running": True, "catalog": core.MCP_CATALOG,
                                   "account": acct,
                                   "step": "ptám se serverů…"})
            return self._json({"running": False, "catalog": core.MCP_CATALOG,
                               "account": acct, **done})
        if name == "update-status":
            return self._json(core.update_state())
        if name == "job":
            return self._json(core.job_state(
                query.get("name", ["vault"])[0]))
        return self._json({"error": "unknown"}, 404)

    def _project(self, payload):
        """Štítek, briefing, archivace, přidání a odebrání projektu.

        „Odebrat" znamená odebrat z Hubu — na složku se nesahá. Mazat cizí
        práci z launcheru je poslední věc, kterou by kdo čekal.
        """
        action = payload.get("action")
        path = os.path.abspath(os.path.expanduser(str(payload.get("path", ""))))
        if not path or path == os.path.abspath(os.sep):
            return self._json({"error": "Chybí cesta."}, 400)
        try:
            if action == "save":
                # Zapisovat CLAUDE.md kamkoli na disk není potřeba — omezíme se
                # na složky, které hub zná jako projekty.
                known = {os.path.abspath(pr["path"]) for pr in core.get_projects()}
                if path not in known:
                    return self._json(
                        {"error": "Tuhle složku hub nezná jako projekt."}, 400)
                updates = {}
                for key in ("label", "brief", "group", "repo", "image"):
                    if key in payload:
                        updates[key] = str(payload[key]).strip()
                if "archived" in payload:
                    updates["archived"] = bool(payload["archived"])
                core.set_project_meta(path, updates)
                written = None
                if "brief" in updates and os.path.isdir(path):
                    written = core.write_briefing(path, updates["brief"])
                return self._json({"ok": True, "briefing": written})
            if action == "add":
                if not os.path.isdir(path):
                    return self._json({"error": "Taková složka není."}, 400)
                extra = list(core.CONFIG.get("extra_projects") or [])
                if path not in extra:
                    extra.append(path)
                    core.save_config({"extra_projects": extra})
                return self._json({"ok": True, "path": path})
            if action == "remove":
                extra = [p for p in (core.CONFIG.get("extra_projects") or [])
                         if os.path.abspath(os.path.expanduser(p)) != path]
                core.save_config({"extra_projects": extra})
                data = core.load_projects()
                data.pop(path, None)
                core.save_projects(data)
                # Uvnitř nastavených složek ho sken najde znovu — pak zbývá archiv.
                still = any(os.path.abspath(p["path"]) == path
                            for p in core.get_projects())
                return self._json({"ok": True, "rescanned": still})
        except Exception as exc:
            return self._json({"error": f"Nepovedlo se: {exc}"}, 500)
        return self._json({"error": "Neznámá akce."}, 400)

    def _vault(self, payload):
        """Založit / vybrat / přesunout vault, nebo z něj udělat git zálohu."""
        action = payload.get("action")
        path = os.path.expanduser(str(payload.get("path", "")))
        try:
            if action == "create":
                if not path:
                    return self._json({"error": "Chybí cesta."}, 400)
                for sub in ("memory", "skills", ".obsidian"):
                    os.makedirs(os.path.join(path, sub), exist_ok=True)
                index = os.path.join(path, "memory", "MEMORY.md")
                if not os.path.isfile(index):
                    with open(index, "w", encoding="utf-8") as fh:
                        fh.write(core.EMPTY_MEMORY_INDEX)
                core.save_config({"brain_dir": path})
                link = core.link_memory(path)
                return self._json({"ok": True, "path": core.BRAIN, **link})
            if action == "use":
                if not os.path.isdir(path):
                    return self._json({"error": "Taková složka není."}, 400)
                core.save_config({"brain_dir": path})
                link = core.link_memory(path)
                return self._json({"ok": True, "path": core.BRAIN,
                                   "has_memory": core.HAS_BRAIN, **link})
            if action == "clone":
                repo = str(payload.get("repo", "")).strip()
                if not repo:
                    return self._json({"error": "Chybí adresa repa."}, 400)
                parent = path or os.path.join(core.HOME, "Obsidian")
                target = core.clone_vault(repo, parent)
                core.save_config({"brain_dir": target})
                link = core.link_memory(target)
                return self._json({"ok": True, "path": target, **link})
            # Přesun i první push trvají; držet na nich požadavek znamená, že
            # se stránka zasekne a po zavření okna se výsledek nemá kam vrátit.
            if action == "move":
                started = core.start_job(
                    "vault", lambda: {"ok": True, **core.move_vault(path)})
                return self._json({"started": started, **core.job_state("vault")})
            if action == "git":
                name = str(payload.get("repo") or "claude-brain").strip()
                started = core.start_job("vault", lambda: dict(
                    zip(("ok", "detail"), core.vault_git_setup(name))))
                return self._json({"started": started, **core.job_state("vault")})
        except ValueError as exc:
            return self._json({"error": str(exc)}, 400)
        except Exception as exc:
            return self._json({"error": f"Nepovedlo se: {exc}"}, 500)
        return self._json({"error": "Neznámá akce."}, 400)

    # ---- websocket ----
    def _websocket(self, query):
        if not self._authorised(query) or not self._origin_ok():
            return self._send(403, b"Neplatny token.")
        key = self.headers.get("Sec-WebSocket-Key", "")
        if not key:
            return self._send(400, b"Neni websocket.")
        accept = base64.b64encode(
            hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
        self.wfile.write(
            b"HTTP/1.1 101 Switching Protocols\r\n"
            b"Upgrade: websocket\r\nConnection: Upgrade\r\n"
            b"Sec-WebSocket-Accept: " + accept.encode() + b"\r\n\r\n")
        self.wfile.flush()
        self.close_connection = True  # we own the socket from here on

        conn = WSConn(self.connection)
        HUB.clients += 1
        HUB.conns.add(conn)
        HUB.last_empty_at = None
        try:
            self._ws_loop(conn)
        finally:
            HUB.clients -= 1
            HUB.conns.discard(conn)
            if HUB.clients <= 0:
                HUB.last_empty_at = time.time()
            for session in list(HUB.sessions.values()):
                session.detach(conn)
            conn.close()

    def _ws_loop(self, conn):
        buffer, buf_opcode = bytearray(), None
        while not conn.closed:
            frame = read_frame(self.rfile)
            if frame is None:
                return
            fin, opcode, payload = frame
            if opcode == 0x8:            # close
                return
            if opcode == 0x9:            # ping
                conn.send(payload, 0xA)
                continue
            if opcode == 0xA:            # pong
                continue
            if opcode == 0x0:            # continuation
                buffer += payload
            else:
                buffer, buf_opcode = bytearray(payload), opcode
            if not fin:
                continue
            message, buffer = bytes(buffer), bytearray()
            if buf_opcode == 0x1:
                self._ws_message(conn, message)

    def _ws_message(self, conn, raw):
        try:
            msg = json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            return
        kind = msg.get("t")
        sid = msg.get("id")
        session = HUB.sessions.get(sid)

        if kind == "hello":
            conn.send_json({"t": "sessions",
                            "list": [s.info() for s in HUB.sessions.values()]})
        elif kind == "open":
            try:
                session = HUB.open(msg.get("kind", "project"), msg.get("path", ""),
                                   msg.get("title", "shell"),
                                   int(msg.get("cols", 80)), int(msg.get("rows", 24)),
                                   agent=str(msg.get("agent", "") or ""),
                                   model=str(msg.get("model", "") or ""))
            except (pty_backend.PtyUnavailable, core.BashMissing) as exc:
                core.log_error("tab se nepodařilo otevřít", exc)
                conn.send_json({"t": "error", "ref": msg.get("ref"), "d": str(exc)})
                return
            except Exception as exc:
                core.log_error("tab se nepodařilo otevřít", exc)
                conn.send_json({"t": "error", "ref": msg.get("ref"),
                                "d": f"Nepodařilo se otevřít terminál: {exc}"})
                return
            core.log(f"tab otevřen: {session.kind} {session.path or '~'}"
                     + (f" [{session.agent}]" if session.agent else ""))
            conn.send_json({"t": "opened", "ref": msg.get("ref"), **session.info()})
            # Rozměr drží okno, které tab otevřelo — zatím ho nikdo jiný nevidí.
            session.resize(conn, int(msg.get("cols", 80)), int(msg.get("rows", 24)))
            session.attach(conn)
            # Tentýž hub může mít otevřený i jiné okno (appka + prohlížeč).
            HUB.broadcast({"t": "tab-opened", **session.info()}, skip=conn)
        elif kind == "attach" and session:
            session.attach(conn)
        elif kind == "in" and session:
            session.drive(conn)
            session.pty.write(msg.get("d", "").encode("utf-8"))
        elif kind == "focus" and session:
            session.drive(conn)
        elif kind == "resize" and session:
            session.resize(conn, int(msg.get("cols", 80)), int(msg.get("rows", 24)))
        elif kind == "rename" and session:
            session.title = str(msg.get("title", session.title))[:60]
            HUB.broadcast({"t": "tab-renamed", "id": sid, "title": session.title},
                          skip=conn)
        elif kind == "close" and session:
            HUB.close(sid)
            HUB.broadcast({"t": "tab-closed", "id": sid}, skip=conn)


def listdir(path):
    """Directory listing for the folder picker (no native dialog in a browser)."""
    path = os.path.expanduser(path or core.HOME)
    if not os.path.isdir(path):
        path = core.HOME
    path = os.path.abspath(path)
    try:
        names = sorted(n for n in os.listdir(path)
                       if not n.startswith(".") and
                       os.path.isdir(os.path.join(path, n)))
    except Exception:
        names = []
    roots = [{"name": "Domů", "path": core.HOME}]
    roots += [{"name": os.path.basename(p) or p, "path": p} for p in core.PROJECT_DIRS]
    if core.IS_WINDOWS:
        roots += [{"name": f"{d}:", "path": f"{d}:\\"} for d in string.ascii_uppercase
                  if os.path.isdir(f"{d}:\\")]
    parent = os.path.dirname(path)
    return {
        "path": path,
        "parent": parent if parent and parent != path else "",
        "dirs": [{"name": n, "path": os.path.join(path, n)} for n in names],
        "roots": roots,
    }


class HubHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    # Prohlížeč (a přes bránu i reverzní proxy) otevře najednou spoustu spojení
    # na assety. Výchozí backlog 5 některé odmítne (RemoteDisconnected) — proto
    # větší fronta příchozích spojení.
    request_queue_size = 128

    def __init__(self, token, address=("127.0.0.1", 0), remote=False):
        super().__init__(address, Handler)
        self.token = token
        self.remote = remote
        self.origins = set()


def start():
    """Start the server on a random loopback port. Returns (server, url)."""
    token = secrets.token_urlsafe(24)
    httpd = HubHTTPServer(token)
    port = httpd.server_address[1]
    httpd.origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
    threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.2},
                     daemon=True).start()
    threading.Thread(target=watch_autosave, daemon=True).start()
    return httpd, f"http://127.0.0.1:{port}/?t={urllib.parse.quote(token)}"



def watch_autosave():
    """Hlídá log automatického ukládání a každé uložení ohlásí oknům.

    Ukládá se v odpojeném procesu (tab už je zavřený, hub možná taky), takže
    jiná cesta zpátky než přes soubor není. Stačí se dívat na jeho velikost.
    """
    try:
        seen = os.path.getsize(core.AUTOSAVE_LOG)
    except OSError:
        seen = 0
    while True:
        time.sleep(4)
        try:
            size = os.path.getsize(core.AUTOSAVE_LOG)
        except OSError:
            continue
        if size < seen:                  # log se zkrátil (rotace)
            seen = 0
        if size == seen:
            continue
        try:
            with open(core.AUTOSAVE_LOG, "rb") as fh:
                fh.seek(seen)
                chunk = fh.read(size - seen)
        except OSError:
            continue
        seen = size
        for line in chunk.decode("utf-8", "replace").splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if entry.get("status") == "saved":
                HUB.broadcast({"t": "memory-saved", "files": entry.get("files") or [],
                               "projects": entry.get("projects") or []})
            elif entry.get("status") == "error":
                core.log(f"paměť se neuložila: {entry.get('detail')}", "warn")


# ── the phone listener ───────────────────────────────────────────────────────
# Kept apart from the desktop one on purpose: turning the phone access on or off
# must never touch the loopback server the open window is talking to.
REMOTE = {"server": None, "mode": "", "url": "", "note": ""}
_REMOTE_LOCK = threading.Lock()


def remote_status():
    """Everything the settings screen needs to draw the phone section."""
    tail = remote.tailscale_state()
    with _REMOTE_LOCK:
        running = REMOTE["server"] is not None
        state = {"running": running, "mode": REMOTE["mode"],
                 "url": REMOTE["url"], "note": REMOTE["note"]}
    state["port"] = remote.port()
    state["tailscale"] = tail
    state["wanted"] = bool(core.CONFIG.get("remote_enabled"))
    return state


def start_remote():
    """Bring the phone listener up. Returns the same dict as remote_status()."""
    stop_remote()
    tail = remote.tailscale_state()
    if not tail["installed"]:
        return _remote_failed("Tailscale na tomhle stroji není nainstalovaný.")
    if not tail["running"]:
        return _remote_failed("Tailscale běží, ale nejsi přihlášený — spusť "
                              "`tailscale up`.")

    token = remote.token()
    port = remote.port()
    note = ""
    mode = "serve"
    address = ("127.0.0.1", port)
    ok, message = remote.serve_start(port)
    if not ok:
        # No TLS front — bind straight to the tailnet address instead.
        if not tail["ip"]:
            return _remote_failed(message or "Tailscale nemá přidělenou adresu.")
        mode, address, note = "bind", (tail["ip"], port), message

    try:
        httpd = HubHTTPServer(token, address, remote=True)
    except OSError as exc:
        if mode == "serve":
            remote.serve_stop()
        return _remote_failed(f"Port {port} nejde obsadit: {exc}")

    httpd.origins = {f"https://{tail['host']}", f"http://{tail['host']}",
                     f"http://{tail['ip']}:{port}", f"http://127.0.0.1:{port}"}
    threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.2},
                     daemon=True).start()
    with _REMOTE_LOCK:
        REMOTE.update(server=httpd, mode=mode, note=note,
                      url=remote.url(mode, tail["host"], tail["ip"], port, token))
    core.log(f"telefon: {mode} na {address[0]}:{port}")
    return remote_status()


def _remote_failed(note):
    with _REMOTE_LOCK:
        REMOTE.update(server=None, mode="", url="", note=note)
    core.log(f"telefon: nespuštěno — {note}")
    return remote_status()


def stop_remote():
    with _REMOTE_LOCK:
        httpd, mode = REMOTE["server"], REMOTE["mode"]
        REMOTE.update(server=None, mode="", url="", note="")
    if httpd:
        httpd.shutdown()
        httpd.server_close()
    if mode == "serve":
        remote.serve_stop()
