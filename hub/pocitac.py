"""
Claude ze serveru na tomhle počítači.

V prostoru na serveru má člověk svého Clauda, ale projekty, dokumenty a programy
má často pořád tady. Tenhle modul je most mezi nimi a má dvě půlky:

* **Na počítači** (`start()` → `_loop`): hub se tokenem zařízení ptá brány,
  jestli pro něj Claude z prostoru nemá úkol — přečíst soubor, spustit příkaz —,
  provede ho a pošle výsledek (brána: `gateway/pocitac.py`). Spojení staví
  počítač: dlouhé dotazy po HTTPS tou cestou, kudy jde přihlášení. Žádný port
  se neotevírá.
* **V prostoru na serveru** (`register_mcp`): hub zaregistruje Claude Code
  MCP server `tools/pocitac_mcp.py`, přes který Claude úkoly posílá.

Pravidla, na kterých to stojí:

* **Vypnuto, dokud se nezapne.** Přístup se volí tady, v appce na počítači
  (Nastavení → Účet): vypnuto, jen čtení, plný přístup. Ověřuje se u každého
  úkolu znovu, podle toho, co platí teď. Ze serveru se změnit nedá — POST na
  /api musí přijít ze stránky hubu (server._origin_ok), ne z prostoru.
* **Běží, jen když běží appka.** Zavřené okno = počítač není připojený.
* **Přihlášení appky a Claude Code Claude ze serveru nepřečte** (`_guarded`):
  s tokenem zařízení by si v prostoru sám potvrdil, co má potvrdit člověk.
  Příkazům (plný přístup) se to zakázat nedá — proto je to zvlášť volba.
* Každý úkol jde do logu hubu a posledních pár je vidět v nastavení.
"""
import base64
import collections
import fnmatch
import getpass
import os
import platform
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

from . import __version__, account, core

LEVELS = ("", "cteni", "vse")
READ_OPS = frozenset({"info", "ls", "read", "find", "download"})
ALLOWED = {"cteni": READ_OPS,
           "vse": READ_OPS | frozenset({"write", "edit", "upload", "run"})}
MCP_NAME = "pocitac"

POLL_TIMEOUT = 45           # brána drží dotaz 25 s; tohle je strop na síť
MAX_TRANSFER = 15 * 1024 * 1024
READ_LINES = 2000
READ_LINE_CHARS = 2000
READ_TEXT = 64 * 1024
LS_MAX = 500
FIND_MAX = 300
FIND_SCAN = 200_000
FIND_FILE = 5 * 1024 * 1024
RUN_DEFAULT = 120
RUN_MAX = 600
# Výstup příkazu: začátek a konec, prostředek se vynechá (jako Bash v Claude Code).
OUT_LIMITS = {"stdout": (8 * 1024, 24 * 1024), "stderr": (4 * 1024, 12 * 1024)}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".cache",
             ".npm", ".gradle", ".Trash", "$RECYCLE.BIN", "System Volume Information"}

STATE = {"state": "off", "note": "", "since": 0.0}
RECENT = collections.deque(maxlen=12)
_WAKE = threading.Event()
_SLOTS = threading.BoundedSemaphore(6)
_LOCK = threading.Lock()
_STARTED = False


class TaskError(Exception):
    """Chyba úkolu — věta, kterou dostane Claude v prostoru."""


# ── nastavení ────────────────────────────────────────────────────────────────
def access_level():
    level = core.CONFIG.get("pocitac_access") or ""
    return level if level in LEVELS else ""


def device_id():
    """Stálé id tohohle počítače u brány (ne tajemství, jen rozlišení strojů)."""
    cid = core.CONFIG.get("pocitac_id") or ""
    if not re.fullmatch(r"[a-f0-9]{16}", cid):
        cid = secrets.token_hex(8)
        core.save_config({"pocitac_id": cid})
    return cid


def set_access(level):
    """Uloží volbu z nastavení. Vypnutí se bráně ohlásí hned, ne až po dotazu."""
    level = str(level or "")
    if level not in LEVELS:
        raise ValueError("Neznámá úroveň přístupu.")
    before = access_level()
    core.save_config({"pocitac_access": level, "pocitac_asked": True})
    if level != before:
        core.log(f"počítač: přístup pro Clauda ze serveru → {level or 'vypnuto'}")
        if not level:
            threading.Thread(target=_bye, daemon=True).start()
    wake()
    return status()


def wake():
    _WAKE.set()


def status():
    with _LOCK:
        recent = list(RECENT)[::-1]
    return {"access": access_level(), "asked": bool(core.CONFIG.get("pocitac_asked")),
            "state": STATE["state"], "note": STATE["note"],
            "server": account._host(account._base()) if account._base() else "",
            "name": _machine_name(), "recent": recent[:6]}


def _set(state, note=""):
    if STATE["state"] != state or STATE["note"] != note:
        if state == "online" and STATE["state"] != "online":
            core.log(f"počítač: připojeno k {account._host(account._base())}")
        STATE.update(state=state, note=note, since=time.time())


def _machine_name():
    return (platform.node() or "počítač").split(".")[0][:60]


def _system_label():
    if core.IS_WINDOWS:
        return f"Windows {platform.release()}".strip()
    if core.IS_MAC:
        return f"macOS {platform.mac_ver()[0]}".strip()
    try:
        with open("/etc/os-release", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("PRETTY_NAME="):
                    return "Linux (" + line.split("=", 1)[1].strip().strip('"') + ")"
    except OSError:
        pass
    return "Linux"


def _shell_argv(command):
    """Čím příkaz spustit: bash (na Windows Git Bash), jinak cmd / sh."""
    if core.BASH:
        return [core.BASH, "-lc", command], "bash"
    if core.IS_WINDOWS:
        return [os.environ.get("COMSPEC") or "cmd.exe", "/d", "/s", "/c", command], "cmd"
    return ["/bin/sh", "-lc", command], "sh"


def describe(access):
    return {"id": device_id(), "name": _machine_name(), "system": _system_label(),
            "home": core.HOME, "user": _user_name(), "shell": _shell_argv("")[1],
            "access": access, "version": __version__}


def _user_name():
    try:
        return getpass.getuser()
    except Exception:
        return ""


# ── spojení s bránou ─────────────────────────────────────────────────────────
def start():
    """Spustí most, jednou za běh hubu. V prostoru na serveru jen napojí MCP."""
    global _STARTED
    with _LOCK:
        if _STARTED:
            return
        _STARTED = True
    target = register_mcp if core.on_gateway() else _loop
    threading.Thread(target=target, daemon=True, name="pocitac").start()


def stop():
    """Appka končí — ať prostor hned ví, že počítač odešel."""
    if STATE["state"] == "online":
        _bye(timeout=3)


def _bye(timeout=8):
    token = core.CONFIG.get("gw_token") or ""
    if token and account._base():
        account._call("/gw/pocitac/poll", token=token, timeout=timeout,
                      payload={"bye": True, "computer": {"id": device_id()}})


def _supported():
    """Umí brána most? Starší na neznámou adresu odpoví 401 jako na špatný
    token — podle toho by se appka mylně odhlásila."""
    data, err, kind = account._call("/gw/info", timeout=10)
    if data is None:
        return None, err
    return "pocitac" in (data.get("features") or []), ""


def _loop():
    backoff = 0
    checked = ""                      # server, u kterého už víme, že most umí
    while True:
        # Probuzení (změna v nastavení) platí pro stav přečtený až po něm.
        _WAKE.clear()
        access = access_level()
        token = core.CONFIG.get("gw_token") or ""
        base = account._base()
        if not access:
            _set("off")
        elif not base or not token:
            _set("waiting", "Appka není přihlášená k serveru.")
        if not access or not base or not token:
            _WAKE.wait(60)
            backoff = 0
            continue

        if checked != base:
            ok, err = _supported()
            if ok is None:
                backoff = min(60, max(3, backoff * 2))
                _set("connecting", err)
                _WAKE.wait(backoff)
                continue
            if not ok:
                _set("unsupported", "Server tohle ještě neumí — potřebuje novější verzi "
                                    "Code Hubu (aktualizuje se sám v noci).")
                _WAKE.wait(15 * 60)
                continue
            checked = base

        data, err, kind = account._call("/gw/pocitac/poll", token=token, timeout=POLL_TIMEOUT,
                                        payload={"computer": describe(access)})
        if data is not None and isinstance(data.get("tasks"), list):
            _set("online")
            backoff = 0
            for task in data["tasks"]:
                if isinstance(task, dict):
                    threading.Thread(target=_handle, args=(task, token), daemon=True).start()
            continue
        if kind == account.AUTH:
            _set("error", "Přihlášení k serveru vypršelo — přihlas se znovu (Nastavení → Účet).")
            _WAKE.wait(300)
            continue
        checked = ""                   # po výpadku se ověří znovu (třeba jiná verze)
        backoff = min(60, max(3, backoff * 2))
        _set("connecting", err or "Server neodpovídá.")
        _WAKE.wait(backoff)


def _handle(task, token):
    rid = str(task.get("id") or "")
    op = str(task.get("op") or "")
    args = task.get("args") if isinstance(task.get("args"), dict) else {}
    started = time.time()
    with _SLOTS:
        try:
            reply = {"ok": True, "result": execute(op, args)}
        except TaskError as exc:
            reply = {"ok": False, "error": str(exc)}
        except Exception as exc:
            core.log_error(f"počítač: úkol {op} selhal", exc)
            reply = {"ok": False, "error": f"Na počítači se to nepovedlo: {exc}"}
    _remember(op, args, reply, started)
    payload = {"computer": device_id(), "id": rid, **reply}
    for attempt in range(3):
        data, _err, kind = account._call("/gw/pocitac/vysledek", token=token,
                                         payload=payload, timeout=120)
        if data is not None or kind == account.AUTH:
            return
        time.sleep(2 * (attempt + 1))


def _remember(op, args, reply, started):
    what = str(args.get("command") if op == "run" else args.get("path") or "~")
    if reply["ok"] and op == "run" and reply["result"].get("timed_out"):
        reply = {"ok": False, "error": "přerušeno po časovém limitu"}   # jen pro výpis
    entry = {"op": op, "what": what[:160], "at": started, "ok": reply["ok"],
             "error": "" if reply["ok"] else str(reply.get("error"))[:200],
             "seconds": round(time.time() - started, 1)}
    with _LOCK:
        RECENT.append(entry)
    core.log(f"počítač: {op} {what[:160]!r} — "
             + ("ok" if reply["ok"] else "chyba: " + entry["error"]),
             "info" if reply["ok"] else "warn")


# ── úkoly ────────────────────────────────────────────────────────────────────
def execute(op, args):
    level = access_level()
    if op not in ALLOWED.get(level, ()):
        if not level:
            raise TaskError("Na tomhle počítači je přístup pro Clauda ze serveru vypnutý.")
        raise TaskError("Na tomhle počítači je povolené jen čtení — zapisovat, nahrávat ani "
                        "spouštět příkazy nejde. Uživatel to může změnit v appce na počítači: "
                        "Nastavení → Účet.")
    return OPS[op](args)


def _path(raw):
    text = str(raw or "").strip()
    if "\0" in text:
        raise TaskError("Neplatná cesta.")
    if not text:
        return core.HOME
    if core.IS_WINDOWS:
        # Claude píše cesty i po bashovsku: /c/Users/… → C:\Users\…
        m = re.match(r"^/([A-Za-z])(/|$)", text)
        if m:
            text = m.group(1).upper() + ":\\" + text[3:]
    path = os.path.expanduser(text)
    if not os.path.isabs(path):
        path = os.path.join(core.HOME, path)
    return os.path.abspath(path)


# Soubory s přihlášením appky k serveru a Claude Code. S tokenem zařízení by si
# Claude v prostoru mohl sám potvrdit, co má potvrzovat člověk (nahrání do
# firemního Obsidianu), a s přihlášením Claude Code jet na cizí účet.
def _guarded():
    dirs = {core.CLAUDE_DIR, os.path.join(core.HOME, ".claude")}
    return [os.path.join(d, name) for d in dirs
            for name in ("hub-config.json", "hub-config.json.tmp", ".credentials.json")]


def _guard(path):
    real = os.path.normcase(os.path.realpath(path))
    for secret in _guarded():
        if real == os.path.normcase(os.path.realpath(secret)):
            raise TaskError("Tenhle soubor drží přihlášení appky nebo Claude Code — Claude "
                            "ze serveru ho číst ani měnit nesmí.")


def _atomic_write(path, data):
    target = os.path.realpath(path)
    parent = os.path.dirname(target)
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=parent, prefix="." + os.path.basename(target) + ".")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        if os.path.exists(target):
            try:
                shutil.copymode(target, tmp)
            except OSError:
                pass
        try:
            os.replace(tmp, target)
        except PermissionError:
            # Windows: soubor drží otevřený jiný program — zapsat aspoň přímo.
            with open(target, "wb") as fh:
                fh.write(data)
            os.remove(tmp)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def op_info(_args):
    return describe(access_level())


def op_ls(args):
    path = _path(args.get("path"))
    if not os.path.isdir(path):
        raise TaskError(f"Složka {path} na počítači není." +
                        (" Je to soubor — použij precist." if os.path.isfile(path) else ""))
    entries = []
    try:
        with os.scandir(path) as it:
            for e in it:
                try:
                    is_dir = e.is_dir()
                    st = e.stat() if not is_dir else None
                except OSError:
                    is_dir, st = False, None
                entries.append({"name": e.name, "type": "dir" if is_dir else "file",
                                "link": e.is_symlink(),
                                "size": st.st_size if st else None,
                                "mtime": int(st.st_mtime) if st else None})
    except PermissionError:
        raise TaskError(f"Do složky {path} nemá uživatel přístup.") from None
    entries.sort(key=lambda e: (e["type"] != "dir", e["name"].casefold()))
    return {"path": path, "entries": entries[:LS_MAX], "total": len(entries)}


def op_read(args):
    path = _path(args.get("path"))
    _guard(path)
    if os.path.isdir(path):
        raise TaskError(f"{path} je složka — použij slozka.")
    if not os.path.isfile(path):
        raise TaskError(f"Soubor {path} na počítači není.")
    try:
        start = max(1, int(args.get("offset") or 1))
        limit = max(1, min(10 * READ_LINES, int(args.get("limit") or READ_LINES)))
    except (TypeError, ValueError):
        raise TaskError("od a pocet musí být čísla.") from None
    lines, size, last, total, truncated = [], 0, start - 1, 0, False
    try:
        with open(path, "rb") as fh:
            if b"\0" in fh.read(8192):
                raise TaskError(f"{path} je binární soubor — zkopíruj ho na server přes stahnout.")
            fh.seek(0)
            for n, raw in enumerate(fh, 1):
                total = n
                if n < start or truncated:
                    if truncated and fh.tell() > 64 * 1024 * 1024:
                        total = None          # dál se nepočítá, soubor je obrovský
                        break
                    continue
                if n >= start + limit:
                    truncated = True
                    continue
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if len(line) > READ_LINE_CHARS:
                    line = line[:READ_LINE_CHARS] + " …"
                row = f"{n:>6}\t{line}"
                size += len(row) + 1
                if size > READ_TEXT:
                    truncated = True
                    continue
                lines.append(row)
                last = n
    except PermissionError:
        raise TaskError(f"Soubor {path} nemá uživatel právo číst.") from None
    if total and start > total:
        raise TaskError(f"Soubor {path} má jen {total} řádků.")
    return {"path": path, "text": "\n".join(lines), "from": start, "to": last,
            "lines_total": total, "truncated": truncated}


def op_find(args):
    root = _path(args.get("path"))
    if not os.path.isdir(root):
        raise TaskError(f"Složka {root} na počítači není.")
    pattern = str(args.get("name") or "*").strip() or "*"
    text = str(args.get("text") or "")
    try:
        rx = re.compile(text, re.IGNORECASE if args.get("ignore_case") else 0) if text else None
    except re.error as exc:
        raise TaskError(f"Neplatný regulární výraz: {exc}") from None
    try:
        limit = max(1, min(FIND_MAX, int(args.get("limit") or 100)))
    except (TypeError, ValueError):
        limit = 100
    by_path = "/" in pattern or "\\" in pattern
    guarded = {os.path.normcase(os.path.realpath(p)) for p in _guarded()}
    matches, scanned, truncated = [], 0, False
    deadline = time.time() + 60

    def hit(rel, name):
        if by_path:
            return fnmatch.fnmatch(rel.replace("\\", "/"), pattern.replace("\\", "/"))
        return fnmatch.fnmatch(name, pattern)

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        if not rx:
            for d in dirnames:
                full = os.path.join(dirpath, d)
                if hit(os.path.relpath(full, root), d):
                    matches.append({"path": full, "type": "dir"})
        for name in sorted(filenames):
            scanned += 1
            if scanned > FIND_SCAN or time.time() > deadline or len(matches) >= limit:
                truncated = True
                break
            full = os.path.join(dirpath, name)
            if not hit(os.path.relpath(full, root), name):
                continue
            if not rx:
                matches.append({"path": full, "type": "file"})
                continue
            if os.path.normcase(os.path.realpath(full)) in guarded:
                continue
            try:
                if os.path.getsize(full) > FIND_FILE:
                    continue
                with open(full, "rb") as fh:
                    raw = fh.read()
            except OSError:
                continue
            if b"\0" in raw[:8192]:
                continue
            found = 0
            for n, line in enumerate(raw.decode("utf-8", "replace").splitlines(), 1):
                if rx.search(line):
                    matches.append({"path": full, "line": n, "text": line.strip()[:200]})
                    found += 1
                    if found >= 20 or len(matches) >= limit:
                        break
        if truncated:
            break
    return {"path": root, "matches": matches[:limit],
            "truncated": truncated or len(matches) > limit}


def op_download(args):
    path = _path(args.get("path"))
    _guard(path)
    if not os.path.isfile(path):
        raise TaskError(f"Soubor {path} na počítači není.")
    size = os.path.getsize(path)
    if size > MAX_TRANSFER:
        raise TaskError(f"Soubor má {size // (1024 * 1024)} MB, najednou jde nejvýš "
                        f"{MAX_TRANSFER // (1024 * 1024)} MB — rozděl nebo zabal ho.")
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except PermissionError:
        raise TaskError(f"Soubor {path} nemá uživatel právo číst.") from None
    return {"path": path, "size": len(data), "data": base64.b64encode(data).decode("ascii")}


def op_write(args):
    path = _path(args.get("path"))
    _guard(path)
    content = args.get("content")
    if not isinstance(content, str):
        raise TaskError("Chybí obsah souboru.")
    data = content.encode("utf-8")
    if len(data) > MAX_TRANSFER:
        raise TaskError("Obsah je moc velký.")
    if os.path.isdir(path):
        raise TaskError(f"{path} je složka.")
    existed = os.path.exists(path)
    _atomic_write(path, data)
    return {"path": path, "bytes": len(data), "created": not existed}


def op_edit(args):
    path = _path(args.get("path"))
    _guard(path)
    old, new = args.get("old"), args.get("new")
    if not isinstance(old, str) or not old or not isinstance(new, str):
        raise TaskError("Chybí text, který se má nahradit, nebo čím.")
    if old == new:
        raise TaskError("Starý a nový text jsou stejné.")
    if not os.path.isfile(path):
        raise TaskError(f"Soubor {path} na počítači není.")
    if os.path.getsize(path) > MAX_TRANSFER:
        raise TaskError("Soubor je na úpravu moc velký.")
    with open(path, "rb") as fh:
        text = fh.read().decode("utf-8", "surrogateescape")
    count = text.count(old)
    if not count and "\r\n" in text and "\n" in old:
        # Soubor z Windows: Claude posílá konce řádků jen \n.
        old, new = old.replace("\r\n", "\n").replace("\n", "\r\n"), \
            new.replace("\r\n", "\n").replace("\n", "\r\n")
        count = text.count(old)
    if not count:
        raise TaskError("Text se v souboru nenašel — přečti si ho znovu, mohl se změnit.")
    replace_all = args.get("all") is True
    if count > 1 and not replace_all:
        raise TaskError(f"Text je v souboru {count}× — přidej okolní řádky, ať je jednoznačný, "
                        "nebo nahraď všechny výskyty.")
    text = text.replace(old, new) if replace_all else text.replace(old, new, 1)
    _atomic_write(path, text.encode("utf-8", "surrogateescape"))
    return {"path": path, "replaced": count if replace_all else 1}


def op_upload(args):
    path = _path(args.get("path"))
    _guard(path)
    try:
        data = base64.b64decode(str(args.get("data") or ""), validate=True)
    except ValueError:
        raise TaskError("Poškozená data.") from None
    if len(data) > MAX_TRANSFER:
        raise TaskError("Soubor je moc velký.")
    if os.path.isdir(path):
        path = os.path.join(path, os.path.basename(str(args.get("name") or "")) or "soubor")
        _guard(path)
    existed = os.path.exists(path)
    _atomic_write(path, data)
    return {"path": path, "bytes": len(data), "created": not existed}


class _Capture:
    """Čte výstup příkazu na pozadí; drží začátek a konec, prostředek zahodí."""

    def __init__(self, stream, head, tail):
        self.stream, self.head_max, self.tail_max = stream, head, tail
        self.head, self.tail, self.total = bytearray(), bytearray(), 0
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        try:
            while True:
                chunk = self.stream.read1(65536) if hasattr(self.stream, "read1") \
                    else self.stream.read(65536)
                if not chunk:
                    break
                self.total += len(chunk)
                room = self.head_max - len(self.head)
                if room > 0:
                    self.head += chunk[:room]
                    chunk = chunk[room:]
                if chunk:
                    self.tail += chunk
                    if len(self.tail) > self.tail_max:
                        del self.tail[:len(self.tail) - self.tail_max]
        except (OSError, ValueError):
            pass

    def text(self):
        skipped = self.total - len(self.head) - len(self.tail)
        head = bytes(self.head).decode("utf-8", "replace")
        tail = bytes(self.tail).decode("utf-8", "replace")
        if skipped > 0:
            return f"{head}\n… (vynecháno {skipped} bajtů) …\n{tail}"
        return head + tail


def _kill_tree(proc):
    if core.IS_WINDOWS:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                       capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except OSError:
            return
        try:
            proc.wait(3)
            return
        except subprocess.TimeoutExpired:
            continue


def op_run(args):
    command = str(args.get("command") or "").strip()
    if not command:
        raise TaskError("Chybí příkaz.")
    cwd = _path(args.get("cwd"))
    if not os.path.isdir(cwd):
        raise TaskError(f"Složka {cwd} na počítači není.")
    try:
        timeout = max(1, min(RUN_MAX, int(args.get("timeout") or RUN_DEFAULT)))
    except (TypeError, ValueError):
        timeout = RUN_DEFAULT
    argv, shell = _shell_argv(command)
    env = core.child_env()
    # Nikdo u terminálu nesedí: žádné barvy, stránkování ani dotazy na heslo.
    env.update(TERM="dumb", NO_COLOR="1", PAGER="cat", GIT_PAGER="cat",
               GIT_TERMINAL_PROMPT="0", HUB_POCITAC="1")
    env.pop("COLORTERM", None)
    kwargs = {}
    if core.IS_WINDOWS:
        kwargs["creationflags"] = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
                                   | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    else:
        kwargs["start_new_session"] = True
    started = time.time()
    try:
        proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
    except OSError as exc:
        raise TaskError(f"Příkaz nejde spustit: {exc}") from None
    out = _Capture(proc.stdout, *OUT_LIMITS["stdout"])
    err = _Capture(proc.stderr, *OUT_LIMITS["stderr"])
    timed_out = False
    try:
        proc.wait(timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(proc)
    note = ""
    for cap in (out, err):
        cap.thread.join(5)
        if cap.thread.is_alive():
            # Proces na pozadí (server, `… &`) drží výstup otevřený dál.
            note = "(Příkaz nechal běžet proces na pozadí; jeho další výstup se už nečte.)"
    return {"exit_code": proc.returncode, "timed_out": timed_out, "shell": shell, "cwd": cwd,
            "seconds": round(time.time() - started, 1), "stdout": out.text(),
            "stderr": err.text(), "note": note}


OPS = {"info": op_info, "ls": op_ls, "read": op_read, "find": op_find,
       "download": op_download, "write": op_write, "edit": op_edit,
       "upload": op_upload, "run": op_run}


# ── v prostoru na serveru ────────────────────────────────────────────────────
def register_mcp():
    """Napojí MCP server tools/pocitac_mcp.py pro Claude Code v prostoru.

    Jen tam, kde brána prostoru dala most (HUB_POCITAC_URL). Registruje se
    přes `claude mcp add` — ~/.claude.json patří Claude Code a do jeho souboru
    se nepíše ručně. Hub startuje dřív než kterýkoli Claude v prostoru, takže
    se nikomu pod rukama nic nemění.
    """
    if not os.environ.get("HUB_POCITAC_URL"):
        return
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = os.path.join(root, "tools", "pocitac_mcp.py")
    claude = shutil.which("claude")
    if not os.path.isfile(script) or not claude:
        return
    python = sys.executable or "python3"
    have = (core._claude_json().get("mcpServers") or {}).get(MCP_NAME) or {}
    if have.get("command") == python and have.get("args") == [script]:
        return
    if have:
        core.run([claude, "mcp", "remove", MCP_NAME, "-s", "user"], cwd=core.HOME, timeout=60)
    try:
        r = subprocess.run([claude, "mcp", "add", MCP_NAME, "-s", "user", "--", python, script],
                           capture_output=True, text=True, cwd=core.HOME, timeout=60)
        ok = r.returncode == 0
        detail = (r.stderr or r.stdout or "").strip()[:200]
    except Exception as exc:
        ok, detail = False, str(exc)
    core.log("počítač: MCP most pro Clauda napojen" if ok
             else f"počítač: MCP most se nepodařilo napojit — {detail}", "info" if ok else "warn")
