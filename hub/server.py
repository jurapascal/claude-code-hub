"""
Local HTTP + WebSocket server behind the hub UI.

It binds to 127.0.0.1 on a random port and every request must carry the token
printed into the page URL at startup — this process can spawn shells, so it is
never reachable by anything that didn't get the token from us.

Terminal sessions live in the server, not in the browser connection: reloading
the page re-attaches to the running Claude sessions and replays their recent
output instead of killing them.
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

from . import core, pty_backend

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_FRAME = 4 * 1024 * 1024          # a client frame is keystrokes; 4 MB is plenty
SCROLLBACK = 256 * 1024              # replayed to the page after a reload


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

    def __init__(self, sid, title, kind, path, argv, cwd, cols, rows):
        self.id = sid
        self.title = title
        self.kind = kind
        self.path = path
        self.pty = pty_backend.spawn(argv, cwd=cwd, env=core.child_env(),
                                     cols=cols, rows=rows)
        self.buffer = bytearray()
        self.conn = None
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
                if text and self.conn is not None:
                    self.conn.send_json({"t": "out", "id": self.id, "d": text})
        self.exited = True
        if self.conn is not None:
            self.conn.send_json({"t": "exit", "id": self.id})

    def attach(self, conn):
        """Point the session at a (new) page and replay what it missed."""
        with self._lock:
            self.conn = conn
            backlog = bytes(self.buffer)
            if backlog:
                # Decoded separately: the live decoder carries partial-character
                # state for the stream and replay must not disturb it.
                conn.send_json({"t": "out", "id": self.id,
                                "d": backlog.decode("utf-8", "replace")})
        if self.exited:
            conn.send_json({"t": "exit", "id": self.id})

    def close(self):
        self.conn = None
        try:
            self.pty.close()
        except Exception:
            pass

    def info(self):
        return {"id": self.id, "title": self.title, "kind": self.kind,
                "path": self.path, "exited": self.exited}


class Hub:
    """Server-side state: the open sessions and who is currently watching them."""

    def __init__(self):
        self.sessions = {}
        self.clients = 0
        self.last_empty_at = None
        self._next_id = 1
        self._lock = threading.Lock()

    def new_id(self):
        with self._lock:
            sid = self._next_id
            self._next_id += 1
            return sid

    def open(self, kind, path, title, cols, rows):
        script, cwd = self._command_for(kind, path)
        sid = self.new_id()
        session = Session(sid, title, kind, path, core.bash_argv(script), cwd,
                          cols, rows)
        self.sessions[sid] = session
        return session

    @staticmethod
    def _command_for(kind, path):
        if kind == "shell":
            return core.cmd_shell(), (path or core.HOME)
        if kind == "deploy":
            return core.cmd_deploy(path), path
        if kind.startswith("slash:"):
            return core.cmd_project(path, "/" + kind.split(":", 1)[1]), path
        return core.cmd_project(path), path

    def close(self, sid):
        session = self.sessions.pop(sid, None)
        if session:
            session.close()

    def shutdown(self):
        for sid in list(self.sessions):
            self.close(sid)


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

    def _authorised(self, query):
        given = (query.get("t", [""])[0]
                 or self.headers.get("X-Hub-Token", ""))
        return secrets.compare_digest(given, self.token)

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
                return self._send(403, b"Neplatny token.")
            return self._static("index.html")
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

    def _static(self, relpath):
        # posixpath.normpath + strip leading separators keeps this inside STATIC_DIR
        rel = posixpath.normpath("/" + relpath).lstrip("/")
        full = os.path.join(STATIC_DIR, *rel.split("/"))
        if not os.path.isfile(full) or not os.path.abspath(full).startswith(STATIC_DIR):
            return self._send(404, b"404")
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        with open(full, "rb") as fh:
            body = fh.read()
        self._send(200, body, ctype)

    def _api(self, name, query, payload=None):
        payload = payload or {}
        if name == "state":
            counts, recent = core.get_memory()
            return self._json({
                "projects": core.get_projects(),
                "memory": {"counts": counts, "recent": recent,
                           "enabled": core.HAS_BRAIN},
                "skills": core.installed_skills(),
                "palette": {"dark": core.DARK, "light": core.LIGHT},
                "doctor": core.doctor(),
                "user": os.environ.get("USER") or os.environ.get("USERNAME") or "",
                "obsidian": core.has_obsidian(),
                "onboarded": bool(core.CONFIG.get("onboarded")),
                "config": {"project_dirs": core.CONFIG.get("project_dirs") or [],
                           "brain_dir": core.CONFIG.get("brain_dir") or "",
                           "newtab": core.CONFIG.get("newtab") or {},
                           "show_archived": bool(core.CONFIG.get("show_archived"))},
                "cloud": core.cloud_folders(),
                "vaults": core.obsidian_vaults(),
                "memory_link": core.memory_link_path(),
                "suggest_dirs": core.suggest_project_dirs(),
                "home": core.HOME,
                "version": core.version_info(),
                "vault_git": dict(zip(("is_repo", "remote"), core.vault_git_state())),
                "vault_autosync": bool(core.CONFIG.get("vault_autosync")),
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
        if name == "config":
            allowed = ("project_dirs", "brain_dir", "onboarded", "vault_autosync",
                       "newtab", "extra_projects", "show_archived")
            updates = {k: v for k, v in payload.items() if k in allowed}
            if not updates:
                return self._json({"error": "Nic k uložení."}, 400)
            try:
                core.save_config(updates)
            except Exception as exc:
                return self._json({"error": f"Konfig nejde zapsat: {exc}"}, 500)
            return self._json({"ok": True, "brain_dir": core.BRAIN,
                               "project_dirs": core.PROJECT_DIRS})
        if name == "project":
            return self._project(payload)
        if name == "vault":
            return self._vault(payload)
        if name == "update-check":
            # Síťový dotaz je zvlášť, aby se na něj nečekalo při každém načtení.
            return self._json(core.version_info(check_remote=True))
        if name == "update":
            started = core.start_update()
            return self._json({"started": started, **core.update_state()})
        if name == "update-status":
            return self._json(core.update_state())
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
                updates = {}
                for key in ("label", "brief"):
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
            if action == "move":
                return self._json({"ok": True, **core.move_vault(path)})
            if action == "git":
                name = str(payload.get("repo") or "claude-brain").strip()
                ok, detail = core.vault_git_setup(name)
                return self._json({"ok": ok, "detail": detail},
                                  200 if ok else 400)
        except ValueError as exc:
            return self._json({"error": str(exc)}, 400)
        except Exception as exc:
            return self._json({"error": f"Nepovedlo se: {exc}"}, 500)
        return self._json({"error": "Neznámá akce."}, 400)

    # ---- websocket ----
    def _websocket(self, query):
        origin = self.headers.get("Origin", "")
        expected = f"http://127.0.0.1:{self.server.server_address[1]}"
        if not self._authorised(query) or (origin and origin != expected):
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
        HUB.last_empty_at = None
        try:
            self._ws_loop(conn)
        finally:
            HUB.clients -= 1
            if HUB.clients <= 0:
                HUB.last_empty_at = time.time()
            for session in HUB.sessions.values():
                if session.conn is conn:
                    session.conn = None
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
                                   int(msg.get("cols", 80)), int(msg.get("rows", 24)))
            except (pty_backend.PtyUnavailable, core.BashMissing) as exc:
                conn.send_json({"t": "error", "ref": msg.get("ref"), "d": str(exc)})
                return
            except Exception as exc:
                conn.send_json({"t": "error", "ref": msg.get("ref"),
                                "d": f"Nepodařilo se otevřít terminál: {exc}"})
                return
            conn.send_json({"t": "opened", "ref": msg.get("ref"), **session.info()})
            session.attach(conn)
        elif kind == "attach" and session:
            session.attach(conn)
        elif kind == "in" and session:
            session.pty.write(msg.get("d", "").encode("utf-8"))
        elif kind == "resize" and session:
            session.pty.resize(int(msg.get("cols", 80)), int(msg.get("rows", 24)))
        elif kind == "rename" and session:
            session.title = str(msg.get("title", session.title))[:60]
        elif kind == "close":
            HUB.close(sid)


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

    def __init__(self, token):
        super().__init__(("127.0.0.1", 0), Handler)
        self.token = token


def start():
    """Start the server on a random loopback port. Returns (server, url)."""
    token = secrets.token_urlsafe(24)
    httpd = HubHTTPServer(token)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.2},
                     daemon=True).start()
    return httpd, f"http://127.0.0.1:{port}/?t={urllib.parse.quote(token)}"
