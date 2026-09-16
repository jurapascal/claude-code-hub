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

from hub import __version__, qr

from . import config, isolation, pocitac, shared, totp, workspace
from .accounts import Accounts

HTML = "text/html; charset=utf-8"
TOO_MANY = "Moc neúspěšných pokusů. Zkus to znovu za čtvrt hodiny."

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
#
# Cookie dostane **tentýž token**, který drží appka — ne nový. Appka se předává
# při každém spuštění, takže nový token by každým startem přibyl v databázi.
# A hlavně: odhlášení v okně pak zneplatní i přihlášení appky, takže se po
# „Odhlásit se" příště opravdu zeptá, místo aby tiše naskočila zpátky.
HANDOFF_TTL = 60
_handoffs = {}                       # kód -> (token zařízení, do kdy)
_handoff_lock = threading.Lock()


def _handoff_new(token):
    code = secrets.token_urlsafe(24)
    now = time.time()
    with _handoff_lock:
        for old, (_tok, exp) in list(_handoffs.items()):
            if exp < now:
                _handoffs.pop(old, None)
        _handoffs[code] = (token, now + HANDOFF_TTL)
    return code


# Přihlášení ve dvou krocích. Po správném hesle brána ještě nevydá token, jen
# lístek na druhý krok — kód z aplikace, nebo první nastavení aplikace — s pěti
# minutami platnosti a pěti pokusy. Lístek žije jen v paměti brány.
LOGIN_TTL = 5 * 60
LOGIN_TRIES = 5
_tickets = {}                        # lístek -> {"uid", "exp", "tries", "secret", "label"}
_ticket_lock = threading.Lock()

# Hádání hesel a kódů: po FAIL_MAX neúspěších za FAIL_WINDOW se z téže adresy
# ani na tentýž e-mail nepřihlašuje. scrypt sám zdrží jen o desetinu vteřiny.
FAIL_MAX = 8
FAIL_WINDOW = 15 * 60
_fails = {}                          # klíč -> [časy neúspěchů]
_fail_lock = threading.Lock()


def _ticket_new(uid, label="", secret=""):
    ticket = secrets.token_urlsafe(24)
    now = time.time()
    with _ticket_lock:
        for old, t in list(_tickets.items()):
            if t["exp"] < now:
                _tickets.pop(old, None)
        _tickets[ticket] = {"uid": uid, "exp": now + LOGIN_TTL, "tries": 0,
                            "secret": secret, "label": label}
    return ticket


def _ticket_get(ticket):
    with _ticket_lock:
        found = _tickets.get(ticket or "")
        if found and found["exp"] < time.time():
            _tickets.pop(ticket, None)
            return None
        return found


def _ticket_drop(ticket):
    with _ticket_lock:
        _tickets.pop(ticket or "", None)


def _ticket_miss(ticket):
    """Špatný kód. Vrací, kolik pokusů zbývá; po posledním lístek propadne."""
    with _ticket_lock:
        found = _tickets.get(ticket or "")
        if not found:
            return 0
        found["tries"] += 1
        left = LOGIN_TRIES - found["tries"]
        if left <= 0:
            _tickets.pop(ticket, None)
        return max(0, left)


def _blocked(keys):
    now = time.time()
    with _fail_lock:
        for key in keys:
            hits = [t for t in _fails.get(key, []) if now - t < FAIL_WINDOW]
            if hits:
                _fails[key] = hits
            else:
                _fails.pop(key, None)
            if len(hits) >= FAIL_MAX:
                return True
    return False


def _failed(keys):
    now = time.time()
    with _fail_lock:
        if len(_fails) > 10000:                  # rozsypané pokusy na tisíce e-mailů
            for key in [k for k, v in _fails.items() if now - v[-1] >= FAIL_WINDOW]:
                _fails.pop(key, None)
        for key in keys:
            _fails.setdefault(key, []).append(now)


def _handoff_take(code):
    """Vrátí token zařízení a kód zahodí. Druhé použití už nic nedostane."""
    if not code:
        return ""
    with _handoff_lock:
        found = _handoffs.pop(code, None)
    if not found:
        return ""
    token, expiry = found
    return token if expiry >= time.time() else ""


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
    """Jedna běžící instance `claude-hub.py --no-browser` pro jednoho uživatele.

    Na serveru běží v systemd scope pojmenované podle účtu (`unit`). Podle ní
    se pozná, jestli ještě žije, a zastavuje se celá — proces, který brána
    spustila, to říct neumí: `systemd-run` se v kontextu služby od sandboxu
    odpojí a sandbox má vlastní init, který SIGTERM ignoruje. Podrobně
    v isolation.py u stop_scope.
    """

    # Jak často se smí živost číst znovu. Ptá se každý proxovaný požadavek a
    # stránka jich při načtení pošle desítky.
    CHECK_EVERY = 2.0

    def __init__(self, user, mode):
        self.user = user
        self.uid = user["id"]
        self.mode = mode
        self.proc = None
        self.port = None
        self.token = None
        self.home = None
        self.unit = ""
        self.cgroup = ""
        self.started = 0
        self.last_active = time.time()
        # Kolik prohlížečů je k prostoru právě připojených (websocket). Prostor
        # s připojením se kvůli místu neuspává — shodil by rozdělanou práci.
        self.conns = 0
        # Sdílené Obsidiany svázané při startu — nové se ukážou až po restartu.
        self.shared = []
        # Žeton, kterým se prostor u brány prokazuje, když Claude sahá na
        # počítač uživatele (gateway/pocitac.py). Nový s každým startem.
        self.pocitac_token = ""
        # S jakým přihlášením Clauda prostor nastartoval (workspace.auth_mark) —
        # připojené předplatné se do běžícího prostoru dostane až restartem.
        self.auth_mark = ""
        self._checked = 0.0
        self._alive = False
        self._lock = threading.Lock()

    def start(self):
        # Scope, která zbyla z minula (spadlá brána, instance, o které se
        # nevědělo), i pod starým jménem u<id>: systemd-run by na stejném jménu
        # skončil chybou a domov se pod běžícím prostorem přejmenovat nesmí.
        names = (workspace.unit_name(self.user), workspace.legacy_unit_name(self.user))
        running = [n for n in names
                   if not isolation.stop_scope(n) and isolation.scope_active(n)]
        if not running:
            # Domov podle e-mailu; starý u<id> se přejmenuje tady, kdy prostor
            # prokazatelně neběží.
            workspace.migrate_home(self.user)
        self.shared = [v["slug"] for v in shared.vaults_for(self.user)]
        argv, home, unit = workspace.session_spec(self.user, isolation, self.mode)
        self.home = home
        self.unit = unit
        env = dict(os.environ, HOME=home)
        # Klíč API brány jen tomu, kdo jede na `central` — `own` ho nesmí
        # zdědit ani z prostředí samotné brány.
        env.pop("ANTHROPIC_API_KEY", None)
        env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
        env.pop("GOOGLE_OAUTH_CLIENT_ID", None)
        env.pop("GOOGLE_OAUTH_CLIENT_SECRET", None)
        env.update(workspace.session_env(self.user))
        self.auth_mark = workspace.auth_mark(self.user)
        # Most na počítač uživatele: MCP server v prostoru (tools/pocitac_mcp.py)
        # volá bránu přímo na loopbacku, ne přes nginx — a tenhle žeton říká,
        # čí prostor volá. Jiné prostory ho nevidí (každý má svoje prostředí).
        self.pocitac_token = secrets.token_urlsafe(32)
        env["HUB_POCITAC_URL"] = _loopback_url()
        env["HUB_POCITAC_TOKEN"] = self.pocitac_token
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
        if unit:
            self.cgroup = isolation.scope_cgroup(unit)
        self.started = time.time()
        self.last_active = time.time()
        self._checked = 0.0
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
        if self.proc is None:
            return False
        if not self.unit:
            # Bez scope (docker, stroj bez systemd) je proces to jediné, co máme.
            return self.proc.poll() is None
        now = time.time()
        if now - self._checked >= self.CHECK_EVERY:
            self._alive = (isolation.cgroup_alive(self.cgroup) if self.cgroup
                           else isolation.scope_active(self.unit))
            self._checked = now
        return self._alive

    def touch(self):
        self.last_active = time.time()

    def stop(self):
        """Zastaví prostor i se vším, co v něm běží (hub, shelly, Claude Code)."""
        p, self.proc = self.proc, None
        self._alive = False
        if self.unit:
            isolation.stop_scope(self.unit)
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
            if proc:
                # Spadlá nebo zastavená zvenku (`gateway.admin stop`). Zbytky
                # uklidit, ať nová instance nevedle staré neběží dvě.
                self.procs.pop(uid, None)
                proc.stop()
            self._make_room()
            proc = HubProc(user, self.mode)
            proc.start()
            self.procs[uid] = proc
            return proc

    # Jak dlouho musí být prostor bez práce, než ho smí uspat někdo jiný.
    EVICT_IDLE = 5 * 60

    def _make_room(self):
        """Uvolní místo, když je dosažen strop: uspí nejdéle nečinný prostor,
        ke kterému není nikdo připojený. Když takový není, radši odmítne
        nového, než aby shodil někomu rozdělanou práci.

        Dřív se bral nejdéle nečinný bez ohledu na připojení. Když bylo lidí
        s otevřeným hubem víc než míst, prostory se v kolečku shazovaly:
        uspaný se za vteřinu znovu připojil a uspal dalšího (2.5.4 po
        restartu brány: 24 startů jednoho prostoru za 15 minut).
        """
        live = [p for p in self.procs.values() if p.alive()]
        if len(live) < self.max_sessions:
            return
        now = time.time()
        spare = [p for p in live
                 if not p.conns and now - p.last_active >= self.EVICT_IDLE]
        if not spare:
            raise RuntimeError(
                f"Server je právě plný (obsazeno {len(live)} z {self.max_sessions} "
                "míst). Zkus to za pár minut znovu.")
        idlest = min(spare, key=lambda p: p.last_active)
        self.procs.pop(idlest.uid, None)
        idlest.stop()

    def _reaper(self):
        # Kontroluje se častěji, než je doba uspání — jinak by se s krátkým
        # IDLE_SLEEP (test) čekalo celou minutu navíc.
        every = max(1.0, min(60.0, self.idle / 2))
        while True:
            time.sleep(every)
            now = time.time()
            to_stop = []
            with self._lock:
                for uid, proc in list(self.procs.items()):
                    if not proc.alive() or now - proc.last_active > self.idle:
                        self.procs.pop(uid, None)
                        to_stop.append(proc)
            # Zastavení může trvat pár vteřin (hub dostane čas skončit) —
            # mimo zámek, ať mezitím nečekají požadavky ostatních.
            for proc in to_stop:
                proc.stop()

    def user_for_pocitac(self, token):
        """Čí běžící prostor se tímhle žetonem prokazuje, nebo None."""
        if not token:
            return None
        with self._lock:
            procs = list(self.procs.values())
        for proc in procs:
            if proc.pocitac_token and secrets.compare_digest(proc.pocitac_token, token) \
                    and proc.alive():
                return proc.user
        return None

    def restart(self, user):
        """Zastaví prostor uživatele; další požadavek ho spustí znovu — i s nově
        svázanými sdílenými Obsidiany. Na přání z hubu."""
        with self._lock:
            proc = self.procs.pop(user["id"], None)
        if proc:
            proc.stop()
        return bool(proc)

    def stop_idle(self, uid):
        """Zastaví prostor, ke kterému není připojený žádný prohlížeč. Po odebrání
        ze sdíleného Obsidianu, ať přístup nevydrží do dalšího restartu."""
        with self._lock:
            proc = self.procs.get(uid)
            if not proc or proc.conns:
                return False
            self.procs.pop(uid, None)
        proc.stop()
        return True

    def stop_all(self):
        with self._lock:
            procs = list(self.procs.values())
            self.procs.clear()
        for proc in procs:
            proc.stop()


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
.card.wide{max-width:30rem}
.steps{padding-left:1.2rem;margin:10px 0;font-size:.92rem;line-height:1.55;color:#c8c0a8}
.qr{background:#fff;border-radius:10px;padding:10px;display:flex;justify-content:center;margin:12px 0}
.qr svg{display:block;width:220px;height:220px}
.hint{font-size:.85rem;color:#9a927c;margin:10px 0 0;line-height:1.5}
code{font-family:ui-monospace,monospace;color:#e0a458;overflow-wrap:anywhere}
.key{display:block;margin-top:4px;font-size:1rem;letter-spacing:.02em}
.codes{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:16px 0}
.codes code{background:#14130d;border:1px solid #3a3524;border-radius:6px;padding:7px;text-align:center;font-size:1rem;color:#e8e3d3}
a.button{display:block;text-align:center;margin-top:20px;padding:11px;border-radius:8px;
background:#e0a458;color:#1a1710;font-weight:600;text-decoration:none}
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


def code_page(ticket, error=""):
    """Druhý krok přihlášení: kód z aplikace (nebo záložní kód)."""
    err = f'<div class=err>{html.escape(error)}</div>' if error else ''
    return _page("Ověření — Code Hub", f"""
<form class=card method=post action="/login/2fa">
<h1>Ověření</h1>
<p class=sub>Opiš šesticiferný kód z aplikace v mobilu
(Google Authenticator, Microsoft Authenticator…).</p>
<input type=hidden name=ticket value="{html.escape(ticket)}">
<label>Kód</label>
<input name=code inputmode=numeric autocomplete=one-time-code autofocus required
 maxlength=16 placeholder="123 456">
<button type=submit>Ověřit</button>
<p class=hint>Nemáš u sebe telefon? Zadej jeden ze záložních kódů.</p>
{err}
</form>""")


def setup_page(ticket, secret, email, error=""):
    """Při prvním přihlášení: zapnutí dvoufázového ověření."""
    err = f'<div class=err>{html.escape(error)}</div>' if error else ''
    svg = qr.svg(totp.uri(secret, email), quiet=2, scale=5)
    return _page("Dvoufázové ověření — Code Hub", f"""
<form class="card wide" method=post action="/login/2fa">
<h1>Zapni dvoufázové ověření</h1>
<p class=sub>Přihlášení na server teď chce kromě hesla i kód z telefonu.</p>
<ol class=steps>
<li>Nainstaluj si do mobilu aplikaci na ověřovací kódy — třeba
<b>Google Authenticator</b> nebo <b>Microsoft Authenticator</b>.</li>
<li>V aplikaci přidej účet a naskenuj tenhle QR kód:</li>
</ol>
<div class=qr>{svg}</div>
<p class=hint>Nejde skenovat? Zadej v aplikaci ručně klíč
<code class=key>{html.escape(totp.grouped(secret))}</code></p>
<ol class=steps start=3><li>Opiš šesticiferný kód, který aplikace ukáže:</li></ol>
<input type=hidden name=ticket value="{html.escape(ticket)}">
<input name=code inputmode=numeric autocomplete=one-time-code autofocus required
 maxlength=8 placeholder="123 456">
<button type=submit>Zapnout a přihlásit</button>
{err}
</form>""")


def recovery_page(codes):
    """Záložní kódy hned po zapnutí ověřování — ukážou se jen tady."""
    items = "".join(f"<code>{html.escape(c)}</code>" for c in codes)
    return _page("Záložní kódy — Code Hub", f"""
<div class="card wide">
<h1>Záložní kódy</h1>
<p class=sub>Když ztratíš telefon, přihlásíš se jedním z nich — každý platí
jednou. Ulož si je (správce hesel, vytisknout). Znovu se neukážou.</p>
<div class=codes>{items}</div>
<a class=button href="/">Uloženo, pokračovat</a>
</div>""")


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
            if route == "/login/2fa":
                return self._login_second(method)
            # Rozhraní pro hub běžící na počítači: ověřuje se tokenem
            # v hlavičce, ne cookie, a nikdy se neproxuje do instance.
            if route == "/gw/info":
                return self._gw_info()
            if route == "/gw/me":
                return self._gw_me()
            if route == "/gw/handoff":
                return self._gw_handoff(method)
            # Most na počítač: počítač se ptá tokenem zařízení, prostor volá
            # svým žetonem. Ani jedno nejde přes cookie a nic z toho se neproxuje.
            if route == "/gw/pocitac/poll":
                return self._pocitac_poll(method)
            if route == "/gw/pocitac/vysledek":
                return self._pocitac_reply(method)
            if route == "/gw/pocitac/volani":
                return self._pocitac_call(method)
            if route == "/gw/pocitac/ukol":
                return self._pocitac_ukol(method)
            # Přihlášení Clauda v prostoru: appka tokenem zařízení, prostor
            # (prohlížeč) cookie — obojí řeší _gw_claude.
            if route == "/gw/claude":
                return self._gw_claude(method)

            user = self._user()

            if not user:
                # Navigaci pošli na přihlášení, API/WS ať dostane jasné 401.
                if method == "GET" and "text/html" in self.headers.get("Accept", ""):
                    return self._redirect("/login")
                return self._send(401, b"Neprihlaseno.")

            if route == "/gw/firma/publish":
                return self._firma_publish(method, user)
            # Zabezpečení účtu z Nastavení → Účet (hub v prostoru o heslech nic neví).
            if route == "/gw/account":
                return self._json({"user": user, "twofa": self.accounts.twofa(user["id"]),
                                   "required": config.REQUIRE_2FA})
            if route == "/gw/password":
                return self._gw_password(method, user)
            if route == "/gw/2fa/recovery":
                return self._gw_recovery(method, user)
            if route == "/gw/sdilene":
                return self._gw_shared(user)
            if route == "/gw/restart":
                return self._gw_restart(method, user)
            if route == "/gw/pocitac":
                # Které počítače jsou k prostoru připojené — štítek v hlavičce —
                # a kde jsou úkoly, které jim Claude nechal na později.
                return self._json({"computers": self.server.pocitac.list(user["id"]),
                                   "ukoly": self.server.pocitac.ukoly(user["id"])})

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

    # ---- firemní Obsidian ----
    def _firma_publish(self, method, user):
        """Nahrání návrhu do firemního Obsidianu — jen tlačítkem v hubu.

        Rozhoduje člověk v prohlížeči, ne Claude v prostoru: požadavek musí
        přijít z téhle stránky, s přihlašovací cookie (do prostoru se
        nepřeposílá, viz _forward_headers) a s hlavičkou, kterou cizí web
        bez povolení poslat nemůže.
        """
        if method != "POST":
            return self._json({"error": "Jen POST."}, 405)
        if not self._same_origin() or self.headers.get("X-Hub-Firma") != "1":
            return self._json({"error": "Nahrát jde jen tlačítkem v hubu."}, 403)
        form = self._read_form()
        try:
            result = workspace.apply_proposal(user, form.get("id", ""),
                                              form.get("prepsat") is True)
        except ValueError as exc:
            return self._json({"error": str(exc)}, 400)
        except OSError as exc:
            _errlog("firma publish", exc)
            return self._json({"error": f"Uložit se nepodařilo: {exc}"}, 500)
        # Odebraní ze sdíleného Obsidianu: kdo zrovna nepracuje, tomu se prostor
        # zastaví hned (přístup zmizí); ostatním při dalším startu.
        if self.hubs:
            for uid in result.get("revoked") or []:
                self.hubs.stop_idle(uid)
        result = {k: v for k, v in result.items() if k not in ("affected", "revoked")}
        return self._json(result)

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
                token = _handoff_take(code)
                # Token se ověřuje až teď, ne při vydání kódu: mezi tím mohl
                # být účet zablokovaný nebo zařízení odhlášené.
                if not token or not self.accounts.user_for_token(token):
                    return self._send(200, login_page(
                        "Odkaz na přihlášení už platnost ztratil. "
                        "Zkus to z appky znovu."), "text/html; charset=utf-8")
                return self._redirect(
                    "/", extra={"Set-Cookie": self._set_cookie(token)})
            return self._send(200, login_page(),
                              "text/html; charset=utf-8")
        form = self._read_form()
        email = (form.get("email") or "").strip()
        password = form.get("password") or ""
        label = self.headers.get("User-Agent", "")[:60]
        keys = self._fail_keys(email)
        if _blocked(keys):
            return self._login_error(TOO_MANY, 429)
        user = self.accounts.verify(email, password)
        if not user:
            _failed(keys)
            return self._login_error("Špatný e-mail nebo heslo.")
        state = self.accounts.twofa(user["id"])
        if not state["enabled"] and not config.REQUIRE_2FA:
            return self._login_done(self.accounts.issue_token(user, label), user)
        if state["enabled"]:
            ticket = _ticket_new(user["id"], label)
            if self._wants_json():
                return self._json({"need": "totp", "ticket": ticket})
            return self._send(200, code_page(ticket), HTML)
        # Ověřování ještě nemá: tajemství vznikne teď a do účtu se zapíše, až
        # ho člověk potvrdí prvním kódem z aplikace.
        secret = totp.new_secret()
        ticket = _ticket_new(user["id"], label, secret)
        if self._wants_json():
            return self._json({"need": "setup", "ticket": ticket, "secret": secret,
                               "uri": totp.uri(secret, user["email"])})
        return self._send(200, setup_page(ticket, secret, user["email"]), HTML)

    def _client_ip(self):
        # nginx před bránou posílá skutečnou adresu v X-Real-IP; brána sama
        # poslouchá jen na loopbacku, takže ji odjinud nikdo nepodvrhne.
        return self.headers.get("X-Real-IP") or self.client_address[0]

    def _fail_keys(self, email=""):
        keys = ["ip:" + self._client_ip()]
        if email:
            keys.append("email:" + email.strip().lower())
        return keys

    def _login_error(self, message, code=401):
        if self._wants_json():
            return self._json({"error": message}, code)
        return self._send(200, login_page(message), HTML)

    def _login_done(self, token, user, recovery=None):
        if self._wants_json():
            # Hub na počítači si token uloží sám; cookie by mu byla k ničemu.
            out = {"token": token, "user": user}
            if recovery:
                out["recovery"] = recovery
            return self._json(out)
        cookie = {"Set-Cookie": self._set_cookie(token)}
        if recovery:
            return self._send(200, recovery_page(recovery), HTML, cookie)
        return self._redirect("/", extra=cookie)

    def _login_second(self, method):
        """Druhý krok: kód z aplikace (nebo záložní), při prvním přihlášení
        zapnutí ověřování potvrzené prvním kódem."""
        if method != "POST":
            return self._redirect("/login")
        form = self._read_form()
        ticket_id = form.get("ticket") or ""
        code = str(form.get("code") or "")
        ticket = _ticket_get(ticket_id)
        user = self.accounts.by_id(ticket["uid"]) if ticket else None
        if not user:
            _ticket_drop(ticket_id)
            return self._login_error("Přihlášení vypršelo — zadej znovu e-mail a heslo.")
        keys = self._fail_keys(user["email"])
        if _blocked(keys):
            _ticket_drop(ticket_id)
            return self._login_error(TOO_MANY, 429)
        recovery = None
        if ticket["secret"]:
            ok = bool(totp.verify(ticket["secret"], code))
            if ok:
                recovery = self.accounts.enable_totp(user["id"], ticket["secret"])
                # Kód, kterým se nastavení potvrdilo, ať už podruhé neprojde.
                self.accounts.second_factor(user["id"], code, allow_recovery=False)
        else:
            ok = bool(self.accounts.second_factor(user["id"], code))
        if not ok:
            _failed(keys)
            left = _ticket_miss(ticket_id)
            message = "Kód nesedí." + (f" Zbývá pokusů: {left}." if left
                                       else " Přihlas se znovu.")
            if self._wants_json():
                return self._json({"error": message, "left": left}, 401)
            if not left:
                return self._send(200, login_page(message), HTML)
            page = (setup_page(ticket_id, ticket["secret"], user["email"], message)
                    if ticket["secret"] else code_page(ticket_id, message))
            return self._send(200, page, HTML)
        _ticket_drop(ticket_id)
        token = self.accounts.issue_token(user, ticket["label"], mfa=True)
        return self._login_done(token, user, recovery)

    def _account_post_ok(self):
        """Změny účtu jen z vlastní stránky a s hlavičkou, kterou cizí web nepošle."""
        return self._same_origin() and self.headers.get("X-Hub-Account") == "1"

    def _gw_password(self, method, user):
        """Změna hesla z Nastavení → Účet. Chce současné heslo, odhlásí ostatní
        zařízení a tomuhle prohlížeči vydá nové přihlášení."""
        if method != "POST":
            return self._json({"error": "Jen POST."}, 405)
        if not self._account_post_ok():
            return self._json({"error": "Heslo jde změnit jen v nastavení hubu."}, 403)
        keys = self._fail_keys(user["email"])
        if _blocked(keys):
            return self._json({"error": TOO_MANY}, 429)
        form = self._read_form()
        current = str(form.get("current") or "")
        new = str(form.get("new") or "")
        if not self.accounts.verify(user["email"], current):
            _failed(keys)
            return self._json({"error": "Současné heslo nesedí."}, 400)
        if new == current:
            return self._json({"error": "Nové heslo je stejné jako to současné."}, 400)
        try:
            self.accounts.set_password(user["email"], new)      # odhlásí všechna zařízení
        except ValueError as exc:
            return self._json({"error": str(exc)}, 400)
        token = self.accounts.issue_token(user, self.headers.get("User-Agent", "")[:60],
                                          mfa=True)
        return self._send(200, json.dumps({"ok": True}), "application/json; charset=utf-8",
                          {"Set-Cookie": self._set_cookie(token)})

    def _gw_recovery(self, method, user):
        """Nové záložní kódy — jen s platným kódem z aplikace, ne se záložním."""
        if method != "POST":
            return self._json({"error": "Jen POST."}, 405)
        if not self._account_post_ok():
            return self._json({"error": "Kódy jde vygenerovat jen v nastavení hubu."}, 403)
        keys = self._fail_keys(user["email"])
        if _blocked(keys):
            return self._json({"error": TOO_MANY}, 429)
        form = self._read_form()
        if not self.accounts.second_factor(user["id"], str(form.get("code") or ""),
                                           allow_recovery=False):
            _failed(keys)
            return self._json({"error": "Kód z aplikace nesedí."}, 400)
        return self._json({"recovery": self.accounts.new_recovery(user["id"])})

    def _gw_shared(self, user):
        """Sdílené Obsidiany uživatele živě z registru. `needs_restart` = je
        členem, ale běžící prostor ho ještě nemá svázaný."""
        proc = self.hubs.procs.get(user["id"]) if self.hubs else None
        bound = set(proc.shared) if proc and proc.alive() else None
        vaults = shared.vaults_for(user)
        for v in vaults:
            v.pop("path", None)
            v["needs_restart"] = bound is not None and v["slug"] not in bound
        gone = sorted(bound - {v["slug"] for v in vaults}) if bound else []
        return self._json({"vaults": vaults, "people": shared.people(), "gone": gone})

    def _gw_restart(self, method, user):
        """Restart vlastního prostoru z hubu (nové sdílené Obsidiany)."""
        if method != "POST":
            return self._json({"error": "Jen POST."}, 405)
        if not self._account_post_ok():
            return self._json({"error": "Restart jde jen z hubu."}, 403)
        if self.hubs:
            self.hubs.restart(user)
        return self._json({"ok": True})

    # ---- rozhraní pro hub na počítači ----
    def _bearer_token(self):
        head = self.headers.get("Authorization", "")
        return head[7:].strip() if head[:7].lower() == "bearer " else ""

    def _bearer(self):
        token = self._bearer_token()
        return self.accounts.user_for_token(token) if token else None

    def _gw_info(self):
        """Bez přihlášení: appka tím ověří, že na adrese je opravdu brána.

        Bez toho by šlo zjistit jen „něco odpovídá" — a na špatně napsané
        adrese by pak člověk psal heslo do cizího webu.
        """
        return self._json({"app": "claude-code-hub", "kind": "gateway",
                           "version": __version__,
                           # Co brána umí navíc — appka podle toho pozná, jestli
                           # se má ptát třeba na most na počítač.
                           "features": ["pocitac", "predplatne", "ukoly"],
                           "host": self._public_host()})

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
        code = _handoff_new(self._bearer_token())
        scheme = "https" if self._https() else "http"
        host = self._public_host()
        return self._json({"code": code, "ttl": HANDOFF_TTL,
                           "url": f"{scheme}://{host}/login?handoff={code}"})

    # ---- Claude na vlastním předplatném ----
    def _gw_claude(self, method):
        """Na čem Claude v prostoru jede; připojení a odpojení předplatného.

        Appka na počítači se prokazuje tokenem zařízení (token předplatného
        vyrobí `claude setup-token` u člověka na počítači a pošle ho sem).
        Z prohlížeče jen s cookie, ze stránky brány a s hlavičkou — Claude
        v prostoru cookie nemá, takže si předplatné nepřepíše.
        """
        bearer = self._bearer_token()
        user = self._bearer() if bearer else self._user()
        if not user:
            return self._json({"error": "Nepřihlášeno."}, 401)
        if method == "POST":
            if not bearer and not self._account_post_ok():
                return self._json({"error": "Jen z nastavení hubu."}, 403)
            form = self._read_form()
            try:
                if form.get("remove") is True:
                    workspace.remove_predplatne(user)
                else:
                    label = self.headers.get("User-Agent", "")[:60] if not bearer else \
                        str(form.get("label") or "")
                    workspace.save_predplatne(user, form.get("token"), label)
            except ValueError as exc:
                return self._json({"error": str(exc)}, 400)
            # Běžící prostor má přihlášení ze svého startu. Když v něm nikdo
            # nepracuje, zastaví se hned a další otevření ho pustí už s novým.
            if self.hubs:
                self.hubs.stop_idle(user["id"])
        state = workspace.claude_state(user)
        proc = self.hubs.procs.get(user["id"]) if self.hubs else None
        state["running"] = bool(proc and proc.alive())
        state["needs_restart"] = bool(state["running"] and
                                      proc.auth_mark != workspace.auth_mark(user))
        return self._json(state)

    # ---- most na počítač uživatele (gateway/pocitac.py) ----
    def _pocitac_poll(self, method):
        """Počítač čeká na úkoly. Drží se až POLL_WAIT sekund."""
        if method != "POST":
            return self._json({"error": "Jen POST."}, 405)
        user = self._bearer()
        if not user:
            return self._json({"error": "Neplatný token."}, 401)
        form = self._read_form()
        info = form.get("computer") if isinstance(form.get("computer"), dict) else {}
        broker = self.server.pocitac
        if form.get("bye") is True:
            return self._json({"ok": broker.bye(user["id"], info.get("id"))})
        try:
            wait = float(form.get("wait", pocitac.POLL_WAIT))
        except (TypeError, ValueError):
            wait = pocitac.POLL_WAIT
        try:
            tasks = broker.poll(user["id"], info, wait)
        except ValueError as exc:
            return self._json({"error": str(exc)}, 400)
        try:
            return self._json({"tasks": tasks,
                               "ukoly": broker.ukoly_offer(user["id"], info.get("id"))})
        except OSError:
            # Počítač mezitím spojení zavřel — úkoly nesmí propadnout.
            broker.requeue(user["id"], info.get("id"), tasks)
            raise

    def _pocitac_reply(self, method):
        if method != "POST":
            return self._json({"error": "Jen POST."}, 405)
        user = self._bearer()
        if not user:
            return self._json({"error": "Neplatný token."}, 401)
        form = self._read_form()
        result = {"ok": form.get("ok") is True, "result": form.get("result"),
                  "error": str(form.get("error") or "")[:2000]}
        ok = self.server.pocitac.reply(user["id"], form.get("computer"), form.get("id"), result)
        return self._json({"ok": ok})

    def _pocitac_ukol(self, method):
        """Úkol na později: počítač si ho stáhne (take) a hlásí, kde je (state)."""
        if method != "POST":
            return self._json({"error": "Jen POST."}, 405)
        user = self._bearer()
        if not user:
            return self._json({"error": "Neplatný token."}, 401)
        form = self._read_form()
        broker = self.server.pocitac
        if form.get("action") == "take":
            return self._json(broker.ukol_take(user["id"], form.get("computer"), form.get("id")))
        return self._json(broker.ukol_mark(user["id"], form.get("computer"), form.get("id"),
                                           str(form.get("state") or "")))

    def _pocitac_call(self, method):
        """Úkol od Clauda z prostoru pro počítač jeho uživatele.

        Volá se jen z prostoru přímo na loopback brány. Přes nginx ne: ten
        vždycky přidá X-Real-IP, takže žeton, kdyby unikl, zvenku nic neotevře.
        """
        if self.headers.get("X-Real-IP") or self.headers.get("X-Forwarded-For"):
            return self._send(404, b"404")
        if method != "POST":
            return self._json({"error": "Jen POST."}, 405)
        user = self.hubs.user_for_pocitac(self.headers.get("X-Hub-Pocitac", "")) \
            if self.hubs else None
        if not user:
            return self._json({"ok": False, "error": "Neplatný žeton prostoru."}, 401)
        form = self._read_form()
        op = str(form.get("op") or "")
        args = form.get("args") if isinstance(form.get("args"), dict) else {}
        broker = self.server.pocitac
        if op == "list":
            return self._json({"ok": True, "computers": broker.list(user["id"]),
                               "ukoly": broker.ukoly(user["id"])})
        # Úkoly na později: počítač teď připojený být nemusí.
        if op == "ukol-novy":
            return self._json(broker.ukol_new(user["id"], args))
        if op == "ukol-zrusit":
            return self._json(broker.ukol_cancel(user["id"], args.get("id")))
        return self._json(broker.call(user["id"], op, args, str(form.get("computer") or "")))

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
        # Přihlašovací cookie brány do prostoru nepatří: běží v něm Claude
        # a s ní by si mohl sám potvrdit třeba nahrání do firemního Obsidianu.
        # Hub se ověřuje tokenem X-Hub-Token, cookie brány nepotřebuje.
        for key in [k for k in out if k.lower() == "cookie"]:
            kept = [c.strip() for c in out[key].split(";")
                    if c.strip() and c.split("=", 1)[0].strip() != config.SESSION_COOKIE]
            if kept:
                out[key] = "; ".join(kept)
            else:
                del out[key]
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
        # Psaní do terminálu jde jen tudy, žádným HTTP požadavkem. Bez tohohle
        # by se prostor uspal po IDLE_SLEEP i člověku, který celou dobu píše.
        # Připojení se počítá po dobu tunelu (_pump_both čeká na konec):
        # prostor s otevřeným prohlížečem se kvůli místu neuspává.
        hub.conns += 1
        try:
            _pump_both(client, up, on_client=hub.touch)
        finally:
            hub.conns -= 1

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


def _pump_both(a, b, on_client=None):
    """Přelévej bajty oběma směry, dokud jedna strana nezavře.

    `on_client` se zavolá s každým kusem dat od prohlížeče (`a`) — tak brána
    pozná, že je prostor používaný. Výstup terminálu (`b` → `a`) se nepočítá:
    běžící příkaz, který něco vypisuje, ještě neznamená, že tam někdo je.
    """
    done = threading.Event()

    def pump(src, dst):
        try:
            while not done.is_set():
                data = src.recv(65536)
                if not data:
                    break
                if on_client and src is a:
                    on_client()
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
        self.pocitac = pocitac.Broker(config.POCITAC_UKOLY_DIR)


def _loopback_url():
    """Kudy prostor na tomhle stroji dosáhne na bránu — mimo nginx."""
    host = config.HOST if config.HOST not in ("", "0.0.0.0", "::") else "127.0.0.1"
    if ":" in host:
        host = f"[{host}]"
    return f"http://{host}:{config.PORT}"


def stop_orphans():
    """Zastaví prostory, o kterých brána neví. Vrací jejich počet."""
    names = [name for name, _desc in isolation.running_scopes()]
    for name in names:
        isolation.stop_scope(name)
    if names:
        print(f"zastaveno osiřelých prostorů: {len(names)}", flush=True)
    return len(names)


def serve():
    problem = isolation.check(config.ISOLATION, config.HOST)
    if problem:
        raise SystemExit("Izolace: " + problem)
    # Úklid osiřelých instancí z minulého běhu: bez `--die-with-parent` po
    # restartu brány zůstanou běžet huby, které už nikdo nespravuje — a každý
    # s Claude Code drží stovky MB.
    stop_orphans()
    accounts = Accounts(config.DB_PATH, require_mfa=config.REQUIRE_2FA)
    shared.ACCOUNTS = accounts
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
