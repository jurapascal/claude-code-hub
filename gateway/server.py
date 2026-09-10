"""
Brána: přihlášení a reverzní proxy na hub každého uživatele.

Jak to drží pohromadě:

* Uživatel otevře adresu brány. Bez platné cookie dostane **přihlašovací
  stránku**; po přihlášení mu brána nastaví cookie s tokenem (v databázi jen
  jako otisk) a od té chvíle ho pozná.
* Pro přihlášeného uživatele brána zajistí, že běží **jeho** instance hubu —
  `claude-hub.py --no-browser` spuštěná v izolaci (`bwrap`) s jeho domovem —
  a všechen provoz (HTTP i WebSocket) do ní **proxuje**. Token té instance
  drží brána a vkládá ho hlavičkou; do prohlížeče se nikdy nedostane.

Žádná správcovská stránka: účty se zakládají a mění z příkazové řádky
(`python3 -m gateway.admin …`) na serveru, ne přes web.

TLS terminuje nginx před bránou, takže brána poslouchá na loopbacku. Bez
izolace (`none`) se pustí jen tam — `isolation.check()` to hlídá.
"""
import html
import http.client
import json
import os
import secrets
import socket
import subprocess
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config, isolation, workspace
from .accounts import Accounts

# Hlavičky, které se u proxy nepřeposílají — patří jednomu skoku spojení, ne
# tomu za ním.
HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate",
              "proxy-authorization", "te", "trailers", "transfer-encoding",
              "upgrade", "host", "content-length"}
HUB_URL_RE = "http://127.0.0.1:"

# Předání přihlášení z hubu na počítači: ten se k bráně přihlásí přes API,
# drží token — ale prohlížeč potřebuje cookie na doméně brány. Posílat token
# v adrese by ho zapsalo do historie, proto se za něj vymění jednorázový kód
# s minutovou platností, který se při prvním použití zahodí.
HANDOFF_TTL = 60
_handoffs = {}                       # kód -> (user_id, do kdy)
_handoff_lock = threading.Lock()


def _handoff_new(user_id):
    code = secrets.token_urlsafe(24)
    now = time.time()
    with _handoff_lock:
        for old, (_uid, exp) in list(_handoffs.items()):
            if exp < now:
                _handoffs.pop(old, None)
        _handoffs[code] = (user_id, now + HANDOFF_TTL)
    return code


def _handoff_take(code):
    """Vrátí user_id a kód zahodí. Druhé použití už nic nedostane."""
    if not code:
        return None
    with _handoff_lock:
        found = _handoffs.pop(code, None)
    if not found:
        return None
    user_id, expiry = found
    return user_id if expiry >= time.time() else None


def _errlog(where, exc):
    """Dočasná diagnostika: zapíše výjimku i s tracebackem do err.log."""
    try:
        import traceback
        with open(os.path.join(config.GATEWAY_DIR, "err.log"), "a",
                  encoding="utf-8") as fh:
            fh.write("%s  %s  %r\n%s\n" % (
                time.strftime("%H:%M:%S"), where, exc,
                traceback.format_exc()))
    except Exception:
        pass


# ── instance hubu jednoho uživatele ──────────────────────────────────────────
class HubProc:
    """Jedna běžící instance `claude-hub.py --no-browser` pro jednoho uživatele."""

    def __init__(self, user, mode):
        self.user = user
        self.uid = user["id"]
        self.mode = mode
        self.proc = None
        self.port = None
        self.token = None
        self.home = None
        self.started = 0
        self.last_active = time.time()
        self._lock = threading.Lock()

    def start(self):
        argv, home = workspace.session_spec(self.user, isolation, self.mode)
        self.home = home
        env = dict(os.environ, HOME=home)
        # XDG_RUNTIME_DIR musí session dostat, jinak si systemd --user scope
        # nemá kam sáhnout (limity by tiše vypadly).
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        self.proc = subprocess.Popen(
            argv, cwd=home, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            bufsize=1, start_new_session=True)
        # První řádek výpisu je URL s portem a tokenem (viz claude-hub.py
        # --no-browser). Čekáme na něj s rozumným stropem.
        url = self._read_url(timeout=40)
        if not url:
            self.stop()
            raise RuntimeError("Instance hubu nenaběhla (nevypsala adresu).")
        parsed = urllib.parse.urlparse(url)
        self.port = parsed.port
        self.token = urllib.parse.parse_qs(parsed.query).get("t", [""])[0]
        self.started = time.time()
        self.last_active = time.time()
        # Zbytek výpisu jen odsáváme, ať se roura nezaplní a proces nezasekne.
        threading.Thread(target=self._drain, daemon=True).start()

    def _read_url(self, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = self.proc.stdout.readline()
            if not line:
                return ""  # proces skončil dřív, než něco vypsal
            line = line.strip()
            if line.startswith(HUB_URL_RE):
                return line
        return ""

    def _drain(self):
        try:
            for _ in self.proc.stdout:
                pass
        except Exception:
            pass

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def touch(self):
        self.last_active = time.time()

    def stop(self):
        # Session běží pod `systemd-run --scope` → `bwrap` → python. systemd-run
        # (jediné, na co máme Popen) v kontextu služby mezitím skončí, takže na
        # jeho PID/pgid spoléhat nejde. Sandbox proto zabíjíme podle domova
        # uživatele v cmdline — mezera na konci odliší u1 od u10.
        if self.home:
            try:
                subprocess.run(["pkill", "-f", self.home + " "], timeout=5)
            except Exception:
                pass
        p, self.proc = self.proc, None
        if p:
            try:
                os.killpg(os.getpgid(p.pid), 15)
            except Exception:
                try:
                    p.terminate()
                except Exception:
                    pass


class HubManager:
    """Které instance hubu běží a kdo je čí. Uspává nečinné, drží strop počtu."""

    def __init__(self, mode, max_sessions, idle):
        self.mode = mode
        self.max_sessions = max_sessions
        self.idle = idle
        self.procs = {}          # uid -> HubProc
        self._lock = threading.Lock()
        threading.Thread(target=self._reaper, daemon=True).start()

    def get(self, user):
        """Vrátí běžící instanci uživatele; když neběží, spustí ji.

        Vyhodí RuntimeError, když je server plný a nejde nic uvolnit.
        """
        uid = user["id"]
        with self._lock:
            proc = self.procs.get(uid)
            if proc and proc.alive():
                proc.touch()
                return proc
            if proc:                      # spadlá — ať se založí znovu
                self.procs.pop(uid, None)
            self._make_room()
            proc = HubProc(user, self.mode)
            proc.start()
            self.procs[uid] = proc
            return proc

    def _make_room(self):
        """Uvolní místo, když je dosažen strop: uspí nejdéle nečinnou."""
        live = [p for p in self.procs.values() if p.alive()]
        if len(live) < self.max_sessions:
            return
        idlest = min(live, key=lambda p: p.last_active)
        idlest.stop()
        self.procs.pop(idlest.uid, None)

    def _reaper(self):
        while True:
            time.sleep(60)
            now = time.time()
            with self._lock:
                for uid, proc in list(self.procs.items()):
                    if not proc.alive():
                        self.procs.pop(uid, None)
                    elif now - proc.last_active > self.idle:
                        proc.stop()
                        self.procs.pop(uid, None)

    def stop_all(self):
        with self._lock:
            for proc in self.procs.values():
                proc.stop()
            self.procs.clear()


# ── stránky, které patří bráně (ne hubu) ─────────────────────────────────────
def _page(title, body):
    return ("""<!doctype html><html lang=cs><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>""" + html.escape(title) + """</title>
<style>
:root{color-scheme:dark}
*{box-sizing:border-box}
body{font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;margin:0;
min-height:100vh;display:grid;place-items:center;background:#16150f;color:#e8e3d3;padding:24px}
.card{width:100%;max-width:24rem;background:#1f1d15;border:1px solid #322e20;
border-radius:12px;padding:28px}
h1{font-size:1.25rem;margin:0 0 4px}
p.sub{margin:0 0 20px;color:#9a927c;font-size:.9rem}
label{display:block;margin:14px 0 4px;font-size:.85rem;color:#c8c0a8}
input{width:100%;padding:10px 12px;border-radius:8px;border:1px solid #3a3524;
background:#14130d;color:#e8e3d3;font-size:1rem}
input:focus{outline:none;border-color:#e0a458}
button{margin-top:20px;width:100%;padding:11px;border:0;border-radius:8px;
background:#e0a458;color:#1a1710;font-weight:600;font-size:1rem;cursor:pointer}
button:hover{background:#e8b76a}
.err{margin-top:14px;color:#f0a0a0;font-size:.9rem;min-height:1.2em}
a{color:#e0a458}
</style>
""" + body + "</html>").encode("utf-8")


def login_page(error=""):
    err = f'<div class=err>{html.escape(error)}</div>' if error else ''
    return _page("Přihlášení — Code Hub", f"""
<form class=card method=post action="/login">
<h1>Code Hub</h1>
<p class=sub>Přihlaš se ke svému účtu na serveru.</p>
<label>E-mail</label>
<input name=email type=email autocomplete=username autofocus required>
<label>Heslo</label>
<input name=password type=password autocomplete=current-password required>
<button type=submit>Přihlásit se</button>
{err}
</form>""")


# ── HTTP handler ──────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    server_version = "ClaudeHubGateway"
    protocol_version = "HTTP/1.1"

    def log_message(self, *_a):
        pass

    # ---- pomůcky ----
    @property
    def accounts(self):
        return self.server.accounts

    @property
    def hubs(self):
        return self.server.hubs

    def _cookies(self):
        jar = {}
        for part in self.headers.get("Cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name:
                jar[name] = urllib.parse.unquote(value)
        return jar

    def _user(self):
        token = self._cookies().get(config.SESSION_COOKIE, "")
        return self.accounts.user_for_token(token) if token else None

    def _https(self):
        return (self.headers.get("X-Forwarded-Proto", "") == "https"
                or self.server.assume_https)

    def _public_host(self):
        return (self.headers.get("X-Forwarded-Host")
                or self.headers.get("Host", "") or "")

    def _same_origin(self):
        """Pro POST akce: Origin/Referer musí být tahle stránka (proti CSRF)."""
        host = self._public_host()
        src = self.headers.get("Origin") or self.headers.get("Referer") or ""
        if not src:
            return False
        return urllib.parse.urlsplit(src).netloc == host

    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False),
                   "application/json; charset=utf-8")

    def _redirect(self, where, extra=None):
        self._send(303, b"", extra={"Location": where, **(extra or {})})

    def _set_cookie(self, token):
        flags = "; Secure" if self._https() else ""
        return (f"{config.SESSION_COOKIE}={urllib.parse.quote(token)}; Path=/; "
                f"HttpOnly; SameSite=Lax; Max-Age={config.SESSION_MAX_AGE}{flags}")

    def _clear_cookie(self):
        return f"{config.SESSION_COOKIE}=; Path=/; HttpOnly; Max-Age=0"

    # ---- routování ----
    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_PUT(self):
        self._route("PUT")

    def do_DELETE(self):
        self._route("DELETE")

    def do_HEAD(self):
        self._route("GET")

    def _route(self, method):
        try:
            parsed = urllib.parse.urlparse(self.path)
            route = parsed.path

            if route == "/login":
                return self._login(method)
            if route == "/logout":
                return self._logout()
            # Rozhraní pro hub běžící na počítači: ověřuje se tokenem
            # v hlavičce, ne cookie, a nikdy se neproxuje do instance.
            if route == "/gw/me":
                return self._gw_me()
            if route == "/gw/handoff":
                return self._gw_handoff(method)

            user = self._user()

            if not user:
                # Navigaci pošli na přihlášení, API/WS ať dostane jasné 401.
                if method == "GET" and "text/html" in self.headers.get("Accept", ""):
                    return self._redirect("/login")
                return self._send(401, b"Neprihlaseno.")

            # Přihlášený → všechno ostatní jde do jeho instance hubu.
            return self._proxy(method, user)
        except BrokenPipeError:
            pass
        except Exception as exc:
            _errlog("route " + self.path, exc)
            try:
                self._json({"error": f"Brána: {exc}"}, 502)
            except Exception:
                pass

    # ---- přihlášení ----
    def _wants_json(self):
        return ("application/json" in (self.headers.get("Content-Type") or "")
                or "application/json" in (self.headers.get("Accept") or ""))

    def _login(self, method):
        if method != "POST":
            # Předané přihlášení z hubu na počítači: kód se vymění za cookie.
            # Musí to být obyčejná navigace GETem, jinak cookie se SameSite=Lax
            # prohlížeč neuloží.
            code = urllib.parse.parse_qs(
                urllib.parse.urlparse(self.path).query).get("handoff", [""])[0]
            if code:
                user_id = _handoff_take(code)
                user = self.accounts.by_id(user_id) if user_id else None
                if not user:
                    return self._send(200, login_page(
                        "Odkaz na přihlášení už platnost ztratil. "
                        "Zkus to z hubu znovu."), "text/html; charset=utf-8")
                token = self.accounts.issue_token(
                    user, self.headers.get("User-Agent", "")[:60])
                return self._redirect(
                    "/", extra={"Set-Cookie": self._set_cookie(token)})
            return self._send(200, login_page(),
                              "text/html; charset=utf-8")
        form = self._read_form()
        email = (form.get("email") or "").strip()
        password = form.get("password") or ""
        label = self.headers.get("User-Agent", "")[:60]
        token = self.accounts.login(email, password, label=label)
        if not token:
            if self._wants_json():
                return self._json({"error": "Špatný e-mail nebo heslo."}, 401)
            return self._send(200, login_page("Špatný e-mail nebo heslo."),
                              "text/html; charset=utf-8")
        if self._wants_json():
            # Hub na počítači si token uloží sám; cookie by mu byla k ničemu.
            return self._json({"token": token,
                               "user": self.accounts.user_for_token(token)})
        return self._redirect("/", extra={"Set-Cookie": self._set_cookie(token)})

    # ---- rozhraní pro hub na počítači ----
    def _bearer(self):
        head = self.headers.get("Authorization", "")
        token = head[7:].strip() if head[:7].lower() == "bearer " else ""
        return self.accounts.user_for_token(token) if token else None

    def _gw_me(self):
        """Komu patří token — hub se tím ptá, jestli je pořád přihlášený."""
        user = self._bearer()
        if not user:
            return self._json({"error": "Neplatný token."}, 401)
        return self._json({"user": user})

    def _gw_handoff(self, method):
        """Vymění platný token za jednorázový kód do adresy prohlížeče."""
        if method != "POST":
            return self._json({"error": "Jen POST."}, 405)
        user = self._bearer()
        if not user:
            return self._json({"error": "Neplatný token."}, 401)
        code = _handoff_new(user["id"])
        scheme = "https" if self._https() else "http"
        host = self._public_host()
        return self._json({"code": code, "ttl": HANDOFF_TTL,
                           "url": f"{scheme}://{host}/login?handoff={code}"})

    def _logout(self):
        token = self._cookies().get(config.SESSION_COOKIE, "")
        if token:
            self.accounts.revoke(token)
        return self._redirect("/login", extra={"Set-Cookie": self._clear_cookie()})

    def _read_form(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        ctype = self.headers.get("Content-Type", "")
        if ctype.startswith("application/json"):
            try:
                return json.loads(raw or b"{}")
            except Exception:
                return {}
        parsed = urllib.parse.parse_qs(raw.decode("utf-8", "replace"))
        return {k: v[0] for k, v in parsed.items()}


    # ---- proxy do instance hubu ----
    def _proxy(self, method, user):
        try:
            hub = self.hubs.get(user)
        except RuntimeError as exc:
            return self._send(503, str(exc).encode("utf-8"))
        hub.touch()
        if "websocket" in self.headers.get("Upgrade", "").lower():
            return self._proxy_ws(hub)
        return self._proxy_http(method, hub)

    def _forward_headers(self, hub, extra=None):
        out = {}
        for key in self.headers.keys():
            if key.lower() in HOP_BY_HOP:
                continue
            out[key] = self.headers.get(key)
        # Hub pozná původ podle shody Host == Origin; drž veřejný host a řekni
        # mu, že spojení bylo přes https (kvůli Secure cookie uvnitř).
        out["Host"] = self._public_host() or f"127.0.0.1:{hub.port}"
        out["X-Forwarded-Proto"] = "https" if self._https() else "http"
        out["X-Forwarded-Host"] = self._public_host()
        # Token instance hubu vkládá brána — do prohlížeče se nikdy nedostane.
        out["X-Hub-Token"] = hub.token
        out.update(extra or {})
        return out

    def _proxy_http(self, method, hub):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        headers = self._forward_headers(hub)
        idempotent = method in ("GET", "HEAD", "OPTIONS")
        resp = data = last = None
        # Instance hubu pod náporem paralelních požadavků občas spojení zavře
        # bez odpovědi (RemoteDisconnected). Reverzní proxy to nesmí propustit
        # jako 502 — zkusí to znovu na čerstvém spojení. `RemoteDisconnected`
        # znamená, že hub požadavek nezpracoval, takže opakovat je bezpečné
        # i u neidempotentních metod; jiné chyby opakujeme jen u GET/HEAD.
        for attempt in range(4):
            conn = http.client.HTTPConnection("127.0.0.1", hub.port, timeout=60)
            try:
                conn.request(method, self.path, body=body, headers=headers)
                resp = conn.getresponse()
                data = resp.read()
                conn.close()
                break
            except (http.client.RemoteDisconnected, ConnectionError,
                    OSError) as exc:
                last = exc
                try:
                    conn.close()
                except Exception:
                    pass
                retryable = idempotent or isinstance(
                    exc, http.client.RemoteDisconnected)
                if not retryable or attempt == 3:
                    break
                time.sleep(0.05 * (attempt + 1))
            except Exception as exc:
                last = exc
                try:
                    conn.close()
                except Exception:
                    pass
                break
        if data is None:
            _errlog("proxy_http " + self.path, last)
            return self._send(502, f"Instance hubu neodpověděla: {last}")
        self.send_response(resp.status)
        for key, value in resp.getheaders():
            low = key.lower()
            if low in HOP_BY_HOP or low == "content-length":
                continue
            # Cookie hubu (jeho token) nikdy ven do prohlížeče.
            if low == "set-cookie":
                continue
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if data and self.command != "HEAD":
            self.wfile.write(data)

    def _proxy_ws(self, hub):
        """Průhledný tunel WebSocketu mezi prohlížečem a instancí hubu.

        Handshake se přepošle beze změny (klíč klienta → stejný accept od
        hubu), pak už jen přeléváme bajty tam a zpět, dokud jedna strana
        nezavře. Rámce se neparsují — brána do obsahu nevidí, jen ho přepravuje.
        """
        try:
            up = socket.create_connection(("127.0.0.1", hub.port), timeout=15)
        except OSError as exc:
            return self._send(502, f"Instance hubu neodpověděla: {exc}")
        # Sestav upgrade požadavek pro hub z hlaviček klienta + token.
        head = [f"GET {self.path} HTTP/1.1"]
        for key, value in self._forward_headers(
                hub, {"Connection": "Upgrade", "Upgrade": "websocket"}).items():
            head.append(f"{key}: {value}")
        up.sendall(("\r\n".join(head) + "\r\n\r\n").encode("latin-1"))

        # Přečti odpověď hubu (hlavičky) a přepošli ji klientovi tak, jak je.
        resp = _read_until(up, b"\r\n\r\n")
        if not resp:
            up.close()
            return
        self.wfile.write(resp)
        self.wfile.flush()
        self.close_connection = True

        client = self.connection
        _pump_both(client, up)

    # BaseHTTPRequestHandler používá tyhle pro neznámé metody; ať projdou.
    def do_PATCH(self):
        self._route("PATCH")


def _read_until(sock, marker):
    buf = b""
    while marker not in buf:
        try:
            chunk = sock.recv(4096)
        except OSError:
            return b""
        if not chunk:
            return b""
        buf += chunk
        if len(buf) > 65536:
            break
    return buf


def _pump_both(a, b):
    """Přelévej bajty oběma směry, dokud jedna strana nezavře."""
    done = threading.Event()

    def pump(src, dst):
        try:
            while not done.is_set():
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(data)
        except OSError:
            pass
        finally:
            done.set()
            for s in (a, b):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    t1 = threading.Thread(target=pump, args=(a, b), daemon=True)
    t2 = threading.Thread(target=pump, args=(b, a), daemon=True)
    t1.start()
    t2.start()
    done.wait()
    t1.join(timeout=1)
    t2.join(timeout=1)
    try:
        b.close()
    except OSError:
        pass


# ── server ────────────────────────────────────────────────────────────────────
class Gateway(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, accounts, hubs, assume_https=False):
        super().__init__(address, Handler)
        self.accounts = accounts
        self.hubs = hubs
        self.assume_https = assume_https


def serve():
    problem = isolation.check(config.ISOLATION, config.HOST)
    if problem:
        raise SystemExit("Izolace: " + problem)
    # Úklid osiřelých instancí z minulého běhu: bez `--die-with-parent` může
    # po pádu/restartu brány zůstat běžet hub, který už nikdo nespravuje.
    try:
        subprocess.run(["pkill", "-f", "claude-hub.py --no-browser"], timeout=5)
    except Exception:
        pass
    accounts = Accounts(config.DB_PATH)
    hubs = HubManager(config.ISOLATION, config.MAX_SESSIONS, config.IDLE_SLEEP)
    # assume_https zapneme, když je za bránou nginx s TLS (řekne to env).
    assume_https = os.environ.get("HUB_GW_ASSUME_HTTPS", "") == "1"
    gw = Gateway((config.HOST, config.PORT), accounts, hubs, assume_https)
    where = f"{config.HOST}:{config.PORT}"
    print(f"brána poslouchá na {where}, izolace {config.ISOLATION}, "
          f"účtů {accounts.count()}", flush=True)
    try:
        gw.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        pass
    finally:
        hubs.stop_all()
        gw.shutdown()


if __name__ == "__main__":
    serve()
