"""
Služby na pár kliknutí: Freelo, Canva, Ecomail a Google — i s více účty.

Katalog v core.MCP_CATALOG je technický („zaregistruj server s těmahle údaji").
Tohle je vrstva pro člověka: karta služby, pod ní účty a u každého Přihlásit
a Odebrat (Nastavení → Napojení). Každý účet je samostatné napojení:

* **Freelo, Canva, Ecomail** — oficiální vzdálené MCP servery s OAuth, které si
  klienta zaregistrují samy (DCR — ověřeno na jejich metadatech). Účet je
  `claude mcp add` pod jménem `<služba>-<popisek>`. Claude Code ukládá
  přihlášení pod jménem serveru (`canva-firma|<otisk>`), takže dva účty téže
  služby se nepřepíšou.

  Přihlašuje `claude mcp login <jméno> --no-browser`: vypíše adresu a čeká,
  až se vloží adresa, kam prohlížeč po přihlášení přesměroval. Bez terminálu to
  odmítne („stdin isn't a terminal"), proto běží v pty. Na počítači
  přesměrování na localhost doběhne samo; na serveru (brána) localhost
  v prohlížeči není server, tak se adresa vloží ručně.

* **Google** — jeden server workspace-mcp (open source: Gmail, Disk, Kalendář,
  Dokumenty, Tabulky, Prezentace, Formuláře, Úkoly, Kontakty) pro všechny účty;
  nástroje berou `user_google_email`. Přihlášení obstará hub sám (OAuth s PKCE)
  a token uloží tam, kde ho workspace-mcp hledá
  (~/.google_workspace_mcp/credentials/<e-mail>.json). Server pak vlastní
  přihlašovací tok ani port 8000 nepotřebuje — na bráně by se o něj prostory
  jinak přetahovaly. Klienta OAuth založí správce jednou: na bráně přijde
  v prostředí (GOOGLE_OAUTH_CLIENT_ID/SECRET), na počítači se uloží do
  hub-config.json.
"""
import base64
import datetime
import hashlib
import html
import http.server
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

from . import core, pty_backend

SERVICES = {
    "freelo": {
        "label": "Freelo",
        "note": "Projekty, úkoly, komentáře a výkazy práce.",
        "kind": "mcp",
        "url": "https://mcp.freelo.io/mcp",
    },
    "canva": {
        "label": "Canva",
        "note": "Návrhy, šablony a export.",
        "kind": "mcp",
        "url": "https://mcp.canva.com/mcp",
        "warn": "Canva napojení pouští jen v placeném tarifu (Pro, Teams…). "
                "V týmovém účtu ho musí správce Canvy povolit: Canva AI Connector.",
    },
    "ecomail": {
        "label": "Ecomail",
        "note": "Kontakty, seznamy a kampaně.",
        "kind": "mcp",
        "url": "https://{account}.ecomailapp.cz/mcp",
        "field": {"name": "account", "label": "Název účtu v Ecomailu",
                  "help": "Z adresy, na které Ecomail otevíráš: název.ecomailapp.cz"},
    },
    "google": {
        "label": "Google",
        "note": "Gmail, Disk, Kalendář, Dokumenty, Tabulky, Prezentace, "
                "Formuláře, Úkoly a Kontakty.",
        "kind": "google",
    },
}

GOOGLE_MCP_NAME = "google"
# Chat a Apps Script schválně ne: u soukromých účtů Google jejich oprávnění
# odmítne a přihlášení by padlo celé.
GOOGLE_TOOLS = ["gmail", "drive", "calendar", "docs", "sheets", "slides",
                "forms", "tasks", "contacts"]
_G = "https://www.googleapis.com/auth/"
GOOGLE_SCOPES = ["openid", _G + "userinfo.email", _G + "userinfo.profile"] + [
    _G + s for s in (
        "gmail.readonly", "gmail.send", "gmail.compose", "gmail.modify",
        "gmail.labels", "gmail.settings.basic",
        "drive", "drive.readonly", "drive.file",
        "calendar", "calendar.readonly", "calendar.events",
        "documents", "documents.readonly",
        "spreadsheets", "spreadsheets.readonly",
        "presentations", "presentations.readonly",
        "forms.body", "forms.body.readonly", "forms.responses.readonly",
        "tasks", "tasks.readonly",
        "contacts", "contacts.readonly")]
GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO = "https://openidconnect.googleapis.com/v1/userinfo"

# Návod na klienta OAuth — jen pro hub na počítači; na serveru ho má správce.
GOOGLE_SETUP = list(core.MCP_CATALOG.get("google-workspace", {}).get("setup") or []) + [
    {"title": "Zveřejni aplikaci",
     "text": "Na přihlašovací obrazovce dej Publikovat aplikaci (V produkci). "
             "V testovacím režimu Google přihlášení po 7 dnech zruší.",
     "button": "Otevřít obrazovku",
     "url": "https://console.cloud.google.com/apis/credentials/consent"},
]

LOGIN_TTL = 15 * 60


# ── pomocné ──────────────────────────────────────────────────────────────────
def _claude():
    return shutil.which("claude") or ""


def _run(argv, timeout=60):
    try:
        return subprocess.run(argv, capture_output=True, text=True, cwd=core.HOME,
                              timeout=timeout, creationflags=core._NO_WINDOW)
    except Exception as exc:
        return subprocess.CompletedProcess(argv, 1, "", str(exc))


def _slug(text, limit=30):
    """„Firma s.r.o." → „firma-s-r-o". Z popisku se skládá jméno serveru."""
    ascii_text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")[:limit].strip("-")


def _user_servers():
    return core._claude_json().get("mcpServers") or {}


def _states():
    done = core.job_state("mcp").get("result") or {}
    return {s["name"]: (s["state"], s["status"]) for s in done.get("servers") or []}


def _clean(text):
    """Výstup terminálu bez řídicích sekvencí (barvy, odkazy OSC 8)."""
    text = re.sub(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)", "", text)
    text = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", text)
    return text.replace("\r", "")


def _find_url(raw):
    """Adresa k přihlášení z výstupu `claude mcp login`. V terminálu ji Claude
    Code posílá jako odkaz OSC 8 — z něj je celá, i kdyby se text zalomil."""
    found = re.findall(r"\x1b\]8;;(https://[^\x07\x1b]+)", raw)
    found += re.findall(r"https://\S+", _clean(raw))
    for url in found:
        if "authorize" in url or "response_type=" in url:
            return url
    return found[0] if found else ""


def _last_line(text, skip=r"^(https?://|or paste|waiting for|visit this url)"):
    for line in reversed([l.strip() for l in text.split("\n")]):
        if line and not re.search(skip, line, re.I):
            return line[:300]
    return ""


def _exit_code(pty):
    proc = getattr(pty, "proc", None)
    try:
        if hasattr(proc, "wait"):
            return proc.wait(timeout=5)
        return getattr(proc, "exitstatus", None)
    except Exception:
        return None


# ── katalog a účty ───────────────────────────────────────────────────────────
def google_client():
    """(client_id, client_secret, odkud) klienta OAuth pro Google."""
    cid = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "").strip()
    secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
    if cid and secret:
        return cid, secret, "server" if core.on_gateway() else "env"
    saved = core.CONFIG.get("google_oauth") or {}
    if isinstance(saved, dict) and saved.get("client_id") and saved.get("client_secret"):
        return saved["client_id"], saved["client_secret"], "hub"
    return "", "", ""


def _google_dir():
    base = (os.environ.get("WORKSPACE_MCP_CREDENTIALS_DIR")
            or os.environ.get("GOOGLE_MCP_CREDENTIALS_DIR"))
    if base:
        return os.path.expanduser(base)
    return os.path.join(core.HOME, ".google_workspace_mcp", "credentials")


def _google_file(email):
    # Stejně jako workspace-mcp: e-mail zakódovaný, @ . _ - zůstávají.
    return os.path.join(_google_dir(), urllib.parse.quote(email, safe="@._-") + ".json")


def _google_accounts():
    try:
        names = sorted(os.listdir(_google_dir()))
    except OSError:
        return []
    out = []
    for fn in names:
        email = urllib.parse.unquote(fn[:-5]) if fn.endswith(".json") else ""
        if "@" not in email:
            continue
        try:
            with open(os.path.join(_google_dir(), fn), encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            data = {}
        ok = isinstance(data, dict) and bool(data.get("refresh_token"))
        out.append({"name": email, "label": email, "detail": "",
                    "state": "ok" if ok else "auth",
                    "status": "připojeno" if ok else "přihlásit znovu"})
    return out


def _mcp_accounts(service_id, spec, states):
    host = urllib.parse.urlparse(spec["url"].replace("{account}", "x")).hostname or ""
    per_account = "{account}" in spec["url"]
    suffix = host.split(".", 1)[1] if per_account else host
    out = []
    for name, entry in sorted(_user_servers().items()):
        if not isinstance(entry, dict):
            continue
        if name != service_id and not name.startswith(service_id + "-"):
            continue
        entry_host = urllib.parse.urlparse(entry.get("url") or "").hostname or ""
        if entry_host != suffix and not entry_host.endswith("." + suffix):
            continue
        state, status = states.get(name, ("unknown", "zjišťuju…"))
        out.append({"name": name,
                    "label": name[len(service_id) + 1:] if name != service_id else "hlavní",
                    "state": state, "status": status,
                    "detail": entry_host if per_account else ""})
    return out


def services(refresh=False):
    """Karty služeb s účty a jejich stavem. Stav je z `claude mcp list`, který
    se ptá každého serveru (~10 s) — počítá se na pozadí v úloze „mcp"."""
    job = core.job_state("mcp")
    if (refresh or not job.get("result")) and not job.get("running"):
        core.start_job("mcp", core.mcp_list)
        job = core.job_state("mcp")
    states = _states()
    out = []
    for sid, spec in SERVICES.items():
        item = {"id": sid, "label": spec["label"], "note": spec["note"],
                "kind": spec["kind"], "warn": spec.get("warn", ""),
                "field": spec.get("field")}
        if spec["kind"] == "google":
            cid, _, source = google_client()
            item["accounts"] = _google_accounts()
            item["client"] = source
            if not cid:
                if core.on_gateway():
                    item["missing"] = ("Google na tomhle serveru ještě nenastavil "
                                       "správce (claude-hub-admin google set).")
                else:
                    item["missing"] = ("Nejdřív je potřeba jednou založit klienta "
                                       "OAuth v Google Cloudu.")
                    item["setup"] = GOOGLE_SETUP
            elif not shutil.which("uvx"):
                item["missing"] = ("Chybí uv (uvx), na kterém napojení na Google "
                                   "běží: https://docs.astral.sh/uv/")
        else:
            item["accounts"] = _mcp_accounts(sid, spec, states)
        if not _claude():
            item["missing"] = "Claude Code (claude) tu není nainstalovaný."
        out.append(item)
    return {"services": out, "checking": bool(job.get("running")),
            "on_server": core.on_gateway()}


def add_account(service, label="", account=""):
    """Přidá účet a rovnou spustí přihlášení. Vrací {ok, name, login|detail}."""
    spec = SERVICES.get(service)
    if not spec:
        return {"ok": False, "detail": "Tuhle službu neznám."}
    if spec["kind"] == "google":
        return google_login_start()
    claude = _claude()
    if not claude:
        return {"ok": False, "detail": "Claude Code (claude) tu není nainstalovaný."}
    slug = _slug(label) or "ucet"
    name = f"{service}-{slug}"
    url = spec["url"]
    if "{account}" in url:
        text = (account or "").strip().lower()
        m = re.search(r"([a-z0-9][a-z0-9-]*)\.ecomailapp\.cz", text)
        acc = m.group(1) if m else re.sub(r"[^a-z0-9-]", "", text)
        if not acc:
            return {"ok": False, "detail": f"Chybí {spec['field']['label'].lower()}."}
        url = url.format(account=acc)
    if name in _user_servers():
        return {"ok": False, "detail": f"Účet „{slug}“ u služby {spec['label']} už je "
                                       "— zvol jiný popisek."}
    r = _run([claude, "mcp", "add", "--transport", "http", "-s", "user", name, url])
    if r.returncode != 0:
        return {"ok": False, "detail": (r.stderr or r.stdout or "nepovedlo se").strip()[:300]}
    core.log(f"služby: přidán účet {name}")
    started = login_start(name)
    if not started.get("ok"):
        # Přihlášení se ani nerozběhlo (typicky překlep v názvu účtu Ecomailu —
        # server pak registraci klienta odmítne). Nefunkční účet v seznamu by
        # jen mátl, tak se registrace vrátí zpátky.
        _run([claude, "mcp", "remove", name, "-s", "user"], 30)
        hint = (" Zkontroluj název účtu — musí sedět s adresou, na které "
                "Ecomail otevíráš." if "{account}" in spec["url"] else "")
        return {"ok": False, "detail": f"Přihlášení k {spec['label']} se nerozběhlo.{hint}"}
    return {**started, "name": name}


def remove_account(service, name):
    spec = SERVICES.get(service)
    if not spec:
        return {"ok": False, "detail": "Tuhle službu neznám."}
    claude = _claude()
    if spec["kind"] == "google":
        path = _google_file(name)
        if not os.path.isfile(path):
            return {"ok": False, "detail": "Tenhle Google účet tu není."}
        os.remove(path)
        if not _google_accounts() and GOOGLE_MCP_NAME in _user_servers() and claude:
            _run([claude, "mcp", "remove", GOOGLE_MCP_NAME, "-s", "user"], 30)
        core.log("služby: odebrán Google účet")
        core.start_job("mcp", core.mcp_list)
        return {"ok": True, "detail": f"Google účet {name} odebrán."}
    if (name != service and not name.startswith(service + "-")) or \
            name not in _user_servers():
        return {"ok": False, "detail": "Tenhle účet tu není."}
    # Odhlásit dřív, než zmizí registrace — jinak by token zůstal v úložišti.
    _run([claude, "mcp", "logout", name], 30)
    r = _run([claude, "mcp", "remove", name, "-s", "user"], 30)
    if r.returncode != 0:
        return {"ok": False, "detail": (r.stderr or r.stdout or "nepovedlo se").strip()[:300]}
    core.log(f"služby: odebrán účet {name}")
    core.start_job("mcp", core.mcp_list)
    return {"ok": True, "detail": f"Účet {name} odebrán."}


def _ensure_google_server():
    """Server workspace-mcp v Claude Code — jeden pro všechny Google účty."""
    claude = _claude()
    cid, secret, _ = google_client()
    args = ["workspace-mcp", "--tool-tier", "extended", "--tools", *GOOGLE_TOOLS]
    entry = _user_servers().get(GOOGLE_MCP_NAME)
    if isinstance(entry, dict) and entry.get("args") == args and \
            (entry.get("env") or {}).get("GOOGLE_OAUTH_CLIENT_ID") == cid:
        return {"ok": True}
    if entry:
        _run([claude, "mcp", "remove", GOOGLE_MCP_NAME, "-s", "user"], 30)
    r = _run([claude, "mcp", "add", GOOGLE_MCP_NAME, "-s", "user",
              "-e", f"GOOGLE_OAUTH_CLIENT_ID={cid}",
              "-e", f"GOOGLE_OAUTH_CLIENT_SECRET={secret}",
              "--", "uvx", *args])
    if r.returncode != 0:
        return {"ok": False, "detail": (r.stderr or r.stdout or "nepovedlo se").strip()[:300]}
    return {"ok": True}


def save_google_client(client_id, client_secret):
    """Klient OAuth pro hub na počítači. Na serveru ho drží správce brány."""
    if core.on_gateway():
        return {"ok": False, "detail": "Na serveru klienta nastavuje správce "
                                       "(claude-hub-admin google set)."}
    cid = (client_id or "").strip()
    secret = (client_secret or "").strip()
    if not cid.endswith(".apps.googleusercontent.com"):
        return {"ok": False, "detail": "Client ID končí na .apps.googleusercontent.com "
                                       "— zkontroluj, co jsi vložil."}
    if not secret:
        return {"ok": False, "detail": "Chybí client secret."}
    core.save_config({"google_oauth": {"client_id": cid, "client_secret": secret}})
    return {"ok": True, "detail": "Klient Google je uložený — teď už jen přidej účet."}


# ── přihlašování ─────────────────────────────────────────────────────────────
_LOGINS = {}
_LOCK = threading.Lock()


class _McpLogin:
    """`claude mcp login --no-browser` v pty: adresa ven, přesměrování dovnitř."""
    kind = "mcp"

    def __init__(self, name):
        self.id = secrets.token_urlsafe(12)
        self.name = name
        self.started = time.time()
        self.raw = ""
        self.since = ""          # výstup od posledního vložení adresy
        self.url = ""
        self.done = False
        self.ok = False
        self.message = ""
        env = core.child_env()
        env["BROWSER"] = "true"   # prohlížeč na stroji hubu neotvírat nikdy
        # Široký terminál: dlouhá adresa se nesmí zalomit do dvou řádků.
        self.pty = pty_backend.spawn([_claude(), "mcp", "login", name, "--no-browser"],
                                     cwd=core.HOME, env=env, cols=4000, rows=40)
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        while True:
            chunk = self.pty.read()
            if not chunk:
                break
            text = chunk.decode("utf-8", "replace")
            with _LOCK:
                self.raw = (self.raw + text)[-20000:]
                self.since = (self.since + text)[-8000:]
                if not self.url:
                    self.url = _find_url(self.raw)
        code = _exit_code(self.pty)
        text = _clean(self.raw)
        with _LOCK:
            self.done = True
            self.ok = code == 0 and not re.search(r"couldn.t complete|failed", text[-800:], re.I)
            self.message = (f"Účet {self.name} je přihlášený." if self.ok
                            else _last_line(text) or "Přihlášení se nepovedlo.")
        core.start_job("mcp", core.mcp_list)

    def paste(self, url, wait=20):
        with _LOCK:
            self.since = ""
            self.message = ""
        self.pty.write((url.strip() + "\r").encode("utf-8"))
        end = time.time() + wait
        while time.time() < end:
            time.sleep(0.25)
            with _LOCK:
                if self.done:
                    return
                since = _clean(self.since)
            if re.search(r"doesn.t look like|invalid|mismatch|expired|try again", since, re.I):
                with _LOCK:
                    self.message = ("Tahle adresa k přihlášení nepatří — vlož celou "
                                    "adresu ze stránky, kam tě přihlášení poslalo.")
                return
        with _LOCK:
            self.message = "Zatím nic — zkontroluj, že je adresa celá, a zkus to znovu."

    def cancel(self):
        try:
            self.pty.close()
        except Exception:
            pass


class _GoogleLogin:
    """OAuth s PKCE, který obstará hub. Na počítači přesměrování přijme malý
    posluchač na 127.0.0.1; na serveru se adresa vloží (complete)."""
    kind = "google"

    def __init__(self):
        self.client_id, self.client_secret, _ = google_client()
        self.id = secrets.token_urlsafe(12)
        self.started = time.time()
        self.done = False
        self.ok = False
        self.message = ""
        self.lock = threading.Lock()
        self.state = secrets.token_urlsafe(24)
        self.verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(self.verifier.encode()).digest()).rstrip(b"=").decode()
        login = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                query = urllib.parse.urlparse(self.path).query
                if "state=" not in query:
                    self.send_response(404)
                    self.end_headers()
                    return
                login.complete("http://127.0.0.1/?" + query)
                text = ("✓ Hotovo — Google účet je napojený. Okno můžeš zavřít."
                        if login.ok else "Přihlášení se nepovedlo: " + login.message)
                body = ("<!doctype html><meta charset=utf-8><title>Google</title>"
                        "<body style='font:16px sans-serif;padding:40px'>"
                        + html.escape(text)).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.redirect = f"http://127.0.0.1:{self.server.server_address[1]}/"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = GOOGLE_AUTH + "?" + urllib.parse.urlencode({
            "client_id": self.client_id, "redirect_uri": self.redirect,
            "response_type": "code", "scope": " ".join(GOOGLE_SCOPES),
            "code_challenge": challenge, "code_challenge_method": "S256",
            "state": self.state, "access_type": "offline", "prompt": "consent"})

    def complete(self, url):
        with self.lock:
            if self.done:
                return
            q = urllib.parse.parse_qs(urllib.parse.urlparse((url or "").strip()).query)
            if q.get("error"):
                self.message = "Google přihlášení odmítl: " + q["error"][0]
                return
            if (q.get("state") or [""])[0] != self.state or not q.get("code"):
                self.message = ("Tahle adresa k tomuhle přihlášení nepatří — vlož "
                                "celou adresu ze stránky, kam tě Google poslal.")
                return
            try:
                token = _post_form(GOOGLE_TOKEN, {
                    "code": q["code"][0], "client_id": self.client_id,
                    "client_secret": self.client_secret, "redirect_uri": self.redirect,
                    "grant_type": "authorization_code", "code_verifier": self.verifier})
                info = _get_json(GOOGLE_USERINFO, token.get("access_token", ""))
            except Exception as exc:
                self.message = f"Google přihlášení nedokončil: {exc}"
                self.done = True
                self._stop()
                return
            email = (info.get("email") or "").strip()
            if not email:
                self.message = "Google neřekl, o jaký účet jde (chybí e-mail)."
                self.done = True
                self._stop()
                return
            _write_google_token(email, token, self.client_id, self.client_secret)
            ensured = _ensure_google_server()
            self.done = True
            self.ok = ensured["ok"]
            self.message = (f"Google účet {email} je napojený." if self.ok
                            else ensured.get("detail") or "Napojení se nepovedlo.")
            if self.ok and not token.get("refresh_token"):
                self.message += " Google ale nevydal trvalé přihlášení — po hodině se přihlas znovu."
        core.log("služby: napojen Google účet")
        core.start_job("mcp", core.mcp_list)
        self._stop()

    def _stop(self):
        # Z obsluhy požadavku by shutdown() čekal sám na sebe.
        threading.Thread(target=self.server.shutdown, daemon=True).start()

    def cancel(self):
        self._stop()


def _post_form(url, data):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(),
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    return _open_json(req)


def _get_json(url, access_token):
    return _open_json(urllib.request.Request(
        url, headers={"Authorization": f"Bearer {access_token}"}))


def _open_json(req):
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as exc:
        try:
            err = json.load(exc)
            detail = err.get("error_description") or err.get("error") or ""
        except Exception:
            detail = ""
        raise RuntimeError(detail or f"HTTP {exc.code}") from None


def _write_google_token(email, token, client_id, client_secret):
    """Token ve tvaru, který čte workspace-mcp (credential_store.py)."""
    folder = _google_dir()
    os.makedirs(folder, exist_ok=True)
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    data = {
        "token": token.get("access_token"),
        "refresh_token": token.get("refresh_token"),
        "token_uri": GOOGLE_TOKEN,
        "client_id": client_id,
        "client_secret": client_secret,
        "scopes": (token.get("scope") or "").split() or GOOGLE_SCOPES,
        "expiry": (now + datetime.timedelta(
            seconds=int(token.get("expires_in") or 3600))).isoformat(),
    }
    path = _google_file(email)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, path)


def _cleanup():
    now = time.time()
    with _LOCK:
        old = [k for k, v in _LOGINS.items() if now - v.started > LOGIN_TTL]
        stale = [_LOGINS.pop(k) for k in old]
    for login in stale:
        login.cancel()


def login_start(name):
    """Přihlášení existujícího účtu (Freelo, Canva, Ecomail)."""
    _cleanup()
    if name not in _user_servers():
        return {"ok": False, "detail": "Tenhle účet tu není."}
    try:
        login = _McpLogin(name)
    except (pty_backend.PtyUnavailable, OSError) as exc:
        return {"ok": False, "detail": f"Přihlášení nejde spustit: {exc}"}
    with _LOCK:
        _LOGINS[login.id] = login
    end = time.time() + 25
    while time.time() < end:
        with _LOCK:
            if login.url or login.done:
                break
        time.sleep(0.2)
    with _LOCK:
        if login.url and not login.done:
            return {"ok": True, "login": {"id": login.id, "url": login.url}}
        if login.done and login.ok:
            return {"ok": True, "login": {"id": login.id, "url": "", "done": True,
                                          "message": login.message}}
        detail = login.message or _last_line(_clean(login.raw)) or \
            "Claude Code adresu k přihlášení nevypsal."
    login.cancel()
    return {"ok": False, "detail": detail}


def google_login_start():
    cid, _, _ = google_client()
    if not cid:
        return {"ok": False, "detail": "Google zatím nemá klienta OAuth."}
    if not shutil.which("uvx"):
        return {"ok": False, "detail": "Chybí uv (uvx): https://docs.astral.sh/uv/"}
    if not _claude():
        return {"ok": False, "detail": "Claude Code (claude) tu není nainstalovaný."}
    _cleanup()
    login = _GoogleLogin()
    with _LOCK:
        _LOGINS[login.id] = login
    return {"ok": True, "login": {"id": login.id, "url": login.url}}


def login_status(login_id):
    login = _LOGINS.get(login_id)
    if not login:
        return {"done": True, "ok": False, "message": "Přihlášení vypršelo — začni znovu."}
    return {"done": login.done, "ok": login.ok, "message": login.message}


def login_finish(login_id, url):
    login = _LOGINS.get(login_id)
    if not login:
        return login_status(login_id)
    if not (url or "").strip():
        return {"done": False, "ok": False, "message": "Vlož adresu z adresního řádku."}
    if login.kind == "google":
        login.complete(url)
    else:
        login.paste(url)
    return login_status(login_id)


def login_cancel(login_id):
    with _LOCK:
        login = _LOGINS.pop(login_id, None)
    if login:
        login.cancel()
    return {"ok": True}
