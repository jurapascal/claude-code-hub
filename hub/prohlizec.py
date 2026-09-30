"""
Prohlížeč pro Claude Code, který je vidět přímo v appce.

Playwright MCP si normálně pouští vlastní (neviditelný) Chromium. Tady běží
jeden sdílený Chromium s ladicím portem na 127.0.0.1 a Playwright MCP se k němu
připojí (`--cdp-endpoint`, viz tools/playwright_bridge.py). Hub pak z téhož
prohlížeče přes CDP posílá obraz do plovoucího okna v appce (static/prohlizec.js)
a posílá zpátky klikání a psaní — člověk tak může něco dokončit sám (přihlášení,
captcha) a Claude pokračuje v té samé stránce.

Modul nemá žádné závislosti mimo standardní knihovnu a nic z hubu neimportuje,
aby ho uměl spustit i samotný most (tools/playwright_bridge.py).

Bezpečnost: ladicí port poslouchá jen na loopbacku a nemá přihlášení. Na
počítači s jedním uživatelem to stačí; na sdíleném serveru ho vidí všechny
prostory, proto je tam okno vypnuté, dokud správce nenastaví HUB_PROHLIZEC=1
(např. po oddělení sítě prostorů).
"""
import base64
import glob
import hashlib
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.request

IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
HOST = "127.0.0.1"


def claude_dir():
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")


def profile_dir():
    """Stejný profil jako dřív u Playwright MCP — přihlášení se nemění."""
    return os.path.join(claude_dir(), "browser-profile")


def port():
    """Stálý port na uživatele — víc účtů na jednom stroji se nepere."""
    env = os.environ.get("HUB_BROWSER_PORT", "")
    if env.isdigit():
        return int(env)
    if IS_WINDOWS or not hasattr(os, "getuid"):
        return 9333
    return 9300 + os.getuid() % 600


def endpoint():
    return f"http://{HOST}:{port()}"


def povoleno(server=False):
    """Na sdíleném serveru (prostor brány) je okno vypnuté, dokud to správce
    nepovolí: všechny prostory tam běží pod jedním systémovým účtem a sdílejí
    loopback, takže ladicí port jednoho vidí ostatní."""
    if os.environ.get("HUB_PROHLIZEC") == "1":
        return True
    if os.environ.get("HUB_PROHLIZEC") == "0":
        return False
    return not server


def _version_key(path):
    m = re.search(r"chromium-(\d+)", path)
    return int(m.group(1)) if m else 0


def find_chromium():
    """Nejnovější Chromium od Playwrightu, jinak systémový Chrome. '' = nic."""
    cache = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    home = os.path.expanduser("~")
    if IS_WINDOWS:
        roots = [cache or os.path.join(os.environ.get("LOCALAPPDATA", home), "ms-playwright")]
        pats = ["chromium-*/chrome-win*/chrome.exe"]
        system = [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                  r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                  r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"]
    elif IS_MAC:
        roots = [cache or os.path.join(home, "Library/Caches/ms-playwright")]
        pats = ["chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium"]
        system = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
    else:
        roots = [cache or os.path.join(home, ".cache/ms-playwright")]
        pats = ["chromium-*/chrome-linux*/chrome"]
        system = []
    found = []
    for root in roots:
        for pat in pats:
            found += glob.glob(os.path.join(root, pat))
    # Profil odmítne běžet ve starším Chrome, než v jakém byl naposled otevřen.
    found.sort(key=_version_key, reverse=True)
    for path in found:
        if os.access(path, os.X_OK):
            return path
    for path in system:
        if os.path.isfile(path):
            return path
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge"):
        hit = shutil.which(name)
        if hit:
            return hit
    return ""


def _get(path, timeout=1.5):
    with urllib.request.urlopen(endpoint() + path, timeout=timeout) as res:
        return json.loads(res.read().decode("utf-8", "replace") or "null")


def alive():
    try:
        return bool(_get("/json/version", 0.6))
    except Exception:
        return False


_launch_lock = threading.Lock()


def ensure(wait=15.0):
    """Zajistí běžící prohlížeč. Vrací (ok, zpráva)."""
    with _launch_lock:
        if alive():
            return True, ""
        exe = find_chromium()
        if not exe:
            return False, "Nenašel jsem Chromium (spusť instalačku nebo: npx @playwright/mcp install-browser chrome-for-testing)."
        os.makedirs(profile_dir(), exist_ok=True)
        args = [exe, f"--remote-debugging-port={port()}", f"--remote-debugging-address={HOST}",
                "--remote-allow-origins=*", f"--user-data-dir={profile_dir()}",
                "--no-first-run", "--no-default-browser-check", "--headless=new",
                "--window-size=1280,800", "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows", "about:blank"]
        if not IS_WINDOWS and hasattr(os, "geteuid") and os.geteuid() == 0:
            args.insert(1, "--no-sandbox")
        kw = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if IS_WINDOWS:
            kw["creationflags"] = 0x00000008 | 0x00000200        # DETACHED | NEW_GROUP
        else:
            kw["start_new_session"] = True
        try:
            proc = subprocess.Popen(args, **kw)
        except OSError as exc:
            return False, f"Prohlížeč se nepodařilo spustit: {exc}"
        end = time.time() + wait
        while time.time() < end:
            if alive():
                return True, ""
            if proc.poll() is not None:
                return False, "Prohlížeč hned skončil — nad profilem nejspíš běží jiný Chrome."
            time.sleep(0.25)
        return False, "Prohlížeč se nerozjel včas."


# ── Minimální websocket klient (jen standardní knihovna) ─────────────────────

class _WS:
    def __init__(self, url):
        m = re.match(r"ws://([^/:]+):(\d+)(/.*)$", url)
        if not m:
            raise ValueError("divná adresa " + url)
        self.sock = socket.create_connection((m.group(1), int(m.group(2))), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET {m.group(3)} HTTP/1.1\r\nHost: {m.group(1)}:{m.group(2)}\r\n"
                           f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
                           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(1024)
            if not chunk:
                raise OSError("spojení zavřeno při handshaku")
            head += chunk
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise OSError("handshake odmítnut")
        self._buf = head.split(b"\r\n\r\n", 1)[1]
        self.sock.settimeout(None)
        self._lock = threading.Lock()
        self.closed = False

    def _read(self, n):
        while len(self._buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise OSError("spojení zavřeno")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def recv(self):
        """Jedna celá textová zpráva (nebo None, když spojení skončilo)."""
        data, opcode0 = b"", None
        while True:
            b1, b2 = self._read(2)
            fin, opcode = b1 & 0x80, b1 & 0x0F
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            payload = self._read(n)
            if opcode == 0x8:
                return None
            if opcode == 0x9:
                self._send(payload, 0xA)
                continue
            if opcode == 0xA:
                continue
            if opcode != 0x0:
                opcode0, data = opcode, payload
            else:
                data += payload
            if fin:
                return data.decode("utf-8", "replace") if opcode0 == 0x1 else ""

    def _send(self, payload, opcode=0x1):
        head = bytearray([0x80 | opcode])
        n = len(payload)
        if n < 126:
            head.append(0x80 | n)
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        body = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        with self._lock:
            self.sock.sendall(bytes(head) + mask + body)

    def send(self, text):
        self._send(text.encode("utf-8"))

    def close(self):
        self.closed = True
        try:
            self.sock.shutdown(2)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


# ── Okno v appce: obraz ven, vstup dovnitř ───────────────────────────────────

KLAVESY = {"Enter": 13, "Backspace": 8, "Tab": 9, "Escape": 27, "Delete": 46,
           "ArrowLeft": 37, "ArrowUp": 38, "ArrowRight": 39, "ArrowDown": 40,
           "Home": 36, "End": 35, "PageUp": 33, "PageDown": 34, "Insert": 45}


class Okno:
    """Jedno vlákno na prohlížeč, kolik je pohledů. `viewers` = spojení hubu."""

    def __init__(self):
        self.viewers = set()
        self._lock = threading.Lock()
        self._thread = None
        self._cdp = None
        self._next_id = 0
        self._target = ""
        self._info = None
        self._frames = 0
        self._cekajici = []        # příkazy poslané dřív, než se prohlížeč připojil
        self._videne = None        # id stránek, které už jsme viděli (nová = Claude ji otevřel)
        self._poradi = {}          # id stránky → pořadí, v jakém se objevila (karty zleva doprava)

    # -- pohledy
    def pridej(self, conn):
        with self._lock:
            self.viewers.add(conn)
            start = not (self._thread and self._thread.is_alive())
            if start:
                self._thread = threading.Thread(target=self._bez, name="prohlizec", daemon=True)
                self._thread.start()
        self._info = None          # nový pohled chce hned znát stav

    def odeber(self, conn):
        with self._lock:
            self.viewers.discard(conn)

    def _vsem(self, obj):
        for conn in list(self.viewers):
            try:
                conn.send_json(obj)
            except Exception:
                self.viewers.discard(conn)

    # -- CDP
    def _posli(self, method, **params):
        cdp = self._cdp
        if not cdp or cdp.closed:
            return
        self._next_id += 1
        try:
            cdp.send(json.dumps({"id": self._next_id, "method": method, "params": params}))
        except OSError:
            pass

    def _strany(self):
        try:
            data = _get("/json/list", 1.5)
        except Exception:
            return []
        strany = [p for p in data if p.get("type") == "page" and p.get("webSocketDebuggerUrl")]
        # Chromium je vrací podle poslední aktivity — v okně mají karty zůstat
        # tam, kde vznikly, nejstarší vlevo, ať se při přepínání neskáčou.
        for p in strany:
            self._poradi.setdefault(p["id"], len(self._poradi))
        strany.sort(key=lambda p: self._poradi[p["id"]])
        return strany

    def _bez(self):
        ok, zprava = ensure()
        if not ok:
            self._vsem({"t": "br-stav", "ok": False, "zprava": zprava})
            return
        self._vsem({"t": "br-stav", "ok": True})
        while self.viewers:
            strany = self._strany()
            if not strany:
                self._posli_info([], None)
                time.sleep(0.8)
                continue
            nova = self._nova(strany)
            cil = next((p for p in strany if p["id"] == self._target), None)
            if nova:
                cil = nova                     # Claude otevřel novou kartu — okno jde za ní
            elif cil is None:
                cil = strany[0]
            self._target = cil["id"]
            self._posli_info(strany, cil)
            try:
                self._stream(cil)
            except Exception:
                pass
            time.sleep(0.4)
        self._cdp = None

    def _posli_info(self, strany, aktivni):
        info = {"t": "br-info", "active": aktivni["id"] if aktivni else "",
                "url": aktivni.get("url", "") if aktivni else "",
                "title": aktivni.get("title", "") if aktivni else "",
                "pages": [{"id": p["id"], "title": p.get("title", ""), "url": p.get("url", "")} for p in strany]}
        sig = json.dumps(info, sort_keys=True)
        if sig != self._info:
            self._info = sig
            self._vsem(info)

    def _stream(self, cil):
        cdp = _WS(cil["webSocketDebuggerUrl"])
        self._cdp = cdp
        self._posli("Page.enable")
        self._posli("Page.startScreencast", format="jpeg", quality=60, maxWidth=1400, maxHeight=1000, everyNthFrame=1)
        # Co člověk napsal, než se spojení rozjelo (adresa hned po otevření okna).
        cekajici, self._cekajici = self._cekajici, []
        for method, params in cekajici:
            self._posli(method, **params)
        stop = threading.Event()

        def hlidej():
            # Mění se stránka / adresa / aktivní záložka? Pak se přepne obraz.
            while not stop.wait(1.0):
                if not self.viewers:
                    break
                strany = self._strany()
                akt = next((p for p in strany if p["id"] == self._target), None)
                if akt is None or self._nova(strany, vzit=False):
                    break
                self._posli_info(strany, akt)
            stop.set()
            cdp.close()
        threading.Thread(target=hlidej, daemon=True).start()
        try:
            while not stop.is_set() and self.viewers:
                raw = cdp.recv()
                if raw is None:
                    break
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if msg.get("method") == "Page.screencastFrame":
                    p = msg["params"]
                    meta = p.get("metadata", {})
                    self._vsem({"t": "br-frame", "d": p["data"],
                                "w": meta.get("deviceWidth", 0), "h": meta.get("deviceHeight", 0)})
                    self._posli("Page.screencastFrameAck", sessionId=p["sessionId"])
        finally:
            stop.set()
            cdp.close()

    def _nova(self, strany, vzit=True):
        """Stránka, která se objevila od posledního pohledu (`vzit` ji označí
        za viděnou). Při prvním pohledu se jen zapamatují ty, které už jsou."""
        ids = [p["id"] for p in strany]
        if self._videne is None:
            self._videne = set(ids)
            return None
        nove = [p for p in strany if p["id"] not in self._videne]
        if nove and vzit:
            self._videne |= set(ids)
            self._target = nove[0]["id"]
        return nove[0] if nove else None

    # -- vstup od člověka
    def vstup(self, msg):
        a = msg.get("a")
        if a == "mouse":
            typ = {"down": "mousePressed", "up": "mouseReleased", "move": "mouseMoved"}.get(msg.get("type"))
            if typ:
                btn = {0: "left", 1: "middle", 2: "right"}.get(msg.get("button", 0), "left")
                self._posli("Input.dispatchMouseEvent", type=typ, x=float(msg.get("x", 0)), y=float(msg.get("y", 0)),
                            button=btn if typ != "mouseMoved" else "none", clickCount=int(msg.get("clicks", 1)),
                            modifiers=int(msg.get("mod", 0)))
        elif a == "wheel":
            self._posli("Input.dispatchMouseEvent", type="mouseWheel", x=float(msg.get("x", 0)), y=float(msg.get("y", 0)),
                        deltaX=float(msg.get("dx", 0)), deltaY=float(msg.get("dy", 0)))
        elif a == "key":
            key = str(msg.get("key", ""))
            vk = KLAVESY.get(key) or int(msg.get("vk") or 0)
            mod = int(msg.get("mod", 0))
            text = key if len(key) == 1 and not (mod & 0b1011) else ("\r" if key == "Enter" else "")
            if msg.get("type") == "down":
                self._posli("Input.dispatchKeyEvent", type="keyDown" if text else "rawKeyDown", key=key,
                            code=str(msg.get("code", "")), windowsVirtualKeyCode=vk, nativeVirtualKeyCode=vk,
                            modifiers=mod, **({"text": text} if text else {}))
            else:
                self._posli("Input.dispatchKeyEvent", type="keyUp", key=key, code=str(msg.get("code", "")),
                            windowsVirtualKeyCode=vk, nativeVirtualKeyCode=vk, modifiers=mod)
        elif a == "text":
            text = str(msg.get("text", ""))[:20000]
            if text:
                self._posli("Input.insertText", text=text)
        elif a == "go":
            url = str(msg.get("url", "")).strip()
            if url and not re.match(r"^[a-z][a-z0-9+.-]*:", url, re.I):
                url = "https://" + url
            if url and re.match(r"^(https?|about|chrome):", url, re.I):
                if self._cdp is None or self._cdp.closed:
                    self._cekajici = [("Page.navigate", {"url": url})]   # jen poslední adresa
                else:
                    self._posli("Page.navigate", url=url)
        elif a in ("back", "forward"):
            self._posli("Runtime.evaluate", expression="history.%s()" % ("back" if a == "back" else "forward"))
        elif a == "reload":
            self._posli("Page.reload")
        elif a == "tab":
            tid = str(msg.get("id", ""))
            if re.match(r"^[A-Za-z0-9-]+$", tid):
                try:
                    urllib.request.urlopen(endpoint() + "/json/activate/" + tid, timeout=2).read()
                except Exception:
                    pass
                self._target = tid
                if self._videne is not None:
                    self._videne.add(tid)
                cdp = self._cdp
                if cdp:
                    cdp.close()      # smyčka se znovu připojí na novou záložku
        elif a == "newtab":
            try:
                urllib.request.urlopen(urllib.request.Request(endpoint() + "/json/new?about:blank", method="PUT"), timeout=2).read()
            except Exception:
                pass


OKNO = Okno()


def zprava(conn, msg, server=False):
    """Volá hub pro každou websocketovou zprávu typu `br`."""
    if not povoleno(server):
        conn.send_json({"t": "br-stav", "ok": False,
                        "zprava": "Prohlížeč v appce je na tomhle serveru zatím vypnutý."})
        return
    a = msg.get("a")
    if a == "open":
        OKNO.pridej(conn)
    elif a == "close":
        OKNO.odeber(conn)
    else:
        OKNO.vstup(msg)


def odpoj(conn):
    OKNO.odeber(conn)


# ── napojení Playwright MCP na tenhle prohlížeč ──────────────────────────────

def na_serveru():
    """Běží hub jako prostor brány? Brána to zapisuje do hub-config.json."""
    try:
        with open(os.path.join(claude_dir(), "hub-config.json"), encoding="utf-8-sig") as fh:
            return bool(json.load(fh).get("server_mode"))
    except (OSError, ValueError):
        return False


def _zaznam():
    """Záznam `playwright` z ~/.claude.json: dict, nebo None. (Claude Code ho
    drží vedle složky, nebo v ní, když je nastavená CLAUDE_CONFIG_DIR.)"""
    cd = claude_dir().rstrip("/\\")
    for path in (cd + ".json", os.path.join(cd, ".claude.json")):
        try:
            with open(path, encoding="utf-8-sig") as fh:
                servers = json.load(fh).get("mcpServers") or {}
        except (OSError, ValueError):
            continue
        return servers.get("playwright") or None
    return None


def registrace():
    """Jak je v Claude Code zaregistrovaný Playwright MCP: most | jine | zadna."""
    entry = _zaznam()
    if not entry:
        return "zadna"
    text = " ".join([str(entry.get("command", ""))] + [str(a) for a in entry.get("args") or []])
    return "most" if "playwright_bridge" in text else "jine"


def migruj(log=lambda *a: None):
    """Jednou po aktualizaci: Playwright MCP zaregistrovaný po staru (vlastní
    neviditelný prohlížeč) se přepne na most, ať je prohlížeč vidět v appce.
    Co už člověk nastavil jinak (jiný server pod jménem `playwright`), nechá
    být. Běží na pozadí a nikdy nespadne."""
    try:
        if not povoleno(na_serveru()) or registrace() != "jine":
            return
        entry = _zaznam() or {}
        args = " ".join(str(a) for a in entry.get("args") or [])
        if "@playwright/mcp" not in args or "--cdp-endpoint" in args:
            return                           # vlastní nastavení — nesahat
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        bridge = os.path.join(here, "tools", "playwright_bridge.py")
        if not os.path.isfile(bridge) or not shutil.which("claude"):
            return
        res = subprocess.run([sys.executable or "python3", bridge, "--register"],
                             capture_output=True, text=True, timeout=60)
        log("Playwright MCP přepnutý na prohlížeč v appce" if res.returncode == 0
            else "přepnutí Playwright MCP se nepovedlo: " + (res.stderr or res.stdout).strip()[:200])
    except Exception as exc:          # noqa: BLE001 — pozadí, nic nesmí shodit
        log(f"přepnutí Playwright MCP: {exc}")
