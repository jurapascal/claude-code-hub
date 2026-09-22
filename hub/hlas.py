"""
Hlas: diktování (řeč → text) a předčítání (text → řeč), česky a lokálně.

Nic neodchází ven: přepis dělá faster-whisper (model Whisper „small“, česky
nastavený natvrdo), hlas Piper s českým hlasem „Jirka“. Obojí běží na
procesoru toho stroje, kde je hub — na počítači, nebo na serveru.

Hub sám zůstává na holém Pythonu. Knihovny a modely leží ve vlastní složce
s vlastním prostředím (venv) a hub pouští jejich Python jako samostatný
proces (hub/hlas_worker.py) na každý přepis a každou větu:

* na serveru jedna společná složka pro všechny prostory,
  /usr/local/lib/claude-hub-hlas — sandbox ji vidí jen ke čtení (je pod
  /usr), takže si ji žádný prostor nestahuje zvlášť a nikdo ji nepřepíše.
  Instaluje ji gateway/install.sh (`python3 -m hub.hlas install <složka>`);
* na počítači ~/.claude/hlas — instaluje se tlačítkem v Nastavení → Hlas.

Kde je složka, se dá přebít proměnnou HUB_HLAS_DIR.
"""
import json
import os
import shutil
import subprocess
import sys
import urllib.request

from . import core

SERVER_DIR = "/usr/local/lib/claude-hub-hlas"
LOCAL_DIR = os.path.join(core.CLAUDE_DIR, "hlas")
WHISPER = "Systran/faster-whisper-small"
VOICE_URL = ("https://huggingface.co/rhasspy/piper-voices/resolve/main/"
             "cs/cs_CZ/jirka/medium/cs_CZ-jirka-medium.onnx")
PACKAGES = ["faster-whisper>=1.1", "piper-tts>=1.3"]
MAX_AUDIO = 12 * 1024 * 1024        # WAV 16 kHz mono = 32 kB/s → přes 6 minut
MAX_TEXT = 6000                     # znaků na jedno předčítání
WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hlas_worker.py")


def _python(root):
    if core.IS_WINDOWS:
        return os.path.join(root, "venv", "Scripts", "python.exe")
    return os.path.join(root, "venv", "bin", "python")


def _paths(root):
    return {"python": _python(root),
            "whisper": os.path.join(root, "modely", "whisper-small"),
            "voice": os.path.join(root, "modely", "cs_CZ-jirka-medium.onnx")}


def _ready(root):
    p = _paths(root)
    return (os.path.isfile(p["python"])
            and os.path.isfile(os.path.join(p["whisper"], "model.bin"))
            and os.path.isfile(p["voice"]) and os.path.isfile(p["voice"] + ".json"))


def root():
    """Složka s hotovým prostředím, nebo ''."""
    for cand in (os.environ.get("HUB_HLAS_DIR", ""), SERVER_DIR, LOCAL_DIR):
        if cand and _ready(cand):
            return cand
    return ""


def state():
    found = root()
    on_server = bool(core.CONFIG.get("gateway_user"))
    return {"ready": bool(found), "where": found,
            # Na serveru instaluje správce (install.sh), prostor sám nemůže.
            "can_install": not on_server,
            "installing": core.job_state("hlas").get("running", False)}


# ── instalace ────────────────────────────────────────────────────────────────
def _run(argv, step, log):
    log(step)
    res = subprocess.run(argv, capture_output=True, text=True, timeout=1800)
    if res.returncode != 0:
        raise RuntimeError(f"{step}: {(res.stderr or res.stdout).strip()[-400:]}")


def _download(url, target, log, step):
    log(step)
    tmp = target + ".part"
    with urllib.request.urlopen(url, timeout=120) as resp, open(tmp, "wb") as fh:
        shutil.copyfileobj(resp, fh)
    os.replace(tmp, target)


def install(target=LOCAL_DIR, log=print):
    """Prostředí, knihovny a modely do `target`. Opakovat jde bezpečně."""
    target = os.path.abspath(target)
    p = _paths(target)
    os.makedirs(os.path.join(target, "modely"), exist_ok=True)
    uv = shutil.which("uv")
    if not os.path.isfile(p["python"]):
        if uv:
            # Python, kterým běží hub — ne ten, který si uv stáhne do domova
            # (na serveru by ležel u roota a prostory by ho ze sandboxu neviděly).
            _run([uv, "venv", "--python", sys.executable, os.path.join(target, "venv")],
                 "zakládám prostředí", log)
        else:
            _run([sys.executable, "-m", "venv", os.path.join(target, "venv")],
                 "zakládám prostředí", log)
    if uv:
        _run([uv, "pip", "install", "--python", p["python"], *PACKAGES],
             "instaluju Whisper a Piper (pár minut)", log)
    else:
        _run([p["python"], "-m", "pip", "install", "-q", *PACKAGES],
             "instaluju Whisper a Piper (pár minut)", log)
    if not os.path.isfile(os.path.join(p["whisper"], "model.bin")):
        _run([p["python"], "-c",
              "import sys; from huggingface_hub import snapshot_download; "
              "snapshot_download(sys.argv[1], local_dir=sys.argv[2])",
              WHISPER, p["whisper"]], "stahuju model pro přepis (asi 480 MB)", log)
    if not os.path.isfile(p["voice"]):
        _download(VOICE_URL, p["voice"], log, "stahuju český hlas (asi 60 MB)")
    if not os.path.isfile(p["voice"] + ".json"):
        _download(VOICE_URL + ".json", p["voice"] + ".json", log, "stahuju nastavení hlasu")
    # Na serveru to čtou prostory pod jiným účtem — všechno jen ke čtení.
    if target.startswith("/usr/"):
        subprocess.run(["chmod", "-R", "a+rX", target], check=False)
    log("hotovo")
    return {"ok": True, "where": target}


def start_install():
    if not state()["can_install"]:
        return False
    return core.start_job("hlas", lambda: _install_job())


def _install_job():
    try:
        return install(LOCAL_DIR, log=lambda t: core.job_step("hlas", t))
    except Exception as exc:
        return {"ok": False, "detail": str(exc)[:400]}


# ── přepis a předčítání ──────────────────────────────────────────────────────
def _worker(what, arg, data, timeout):
    found = root()
    if not found:
        raise RuntimeError("Hlas tu není nainstalovaný — Nastavení → Hlas.")
    p = _paths(found)
    env = dict(os.environ)
    # Nic se nestahuje za běhu a nic se nezapisuje do sdílené složky.
    env.update({"HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
                "OMP_NUM_THREADS": str(max(1, min(4, os.cpu_count() or 1)))})
    res = subprocess.run([p["python"], WORKER, what, p[arg]], input=data,
                         capture_output=True, timeout=timeout, env=env)
    if res.returncode != 0:
        tail = res.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or ["?"]
        raise RuntimeError(f"Hlas selhal: {tail[0][:200]}")
    return res.stdout


def prepis(wav):
    if not wav or len(wav) > MAX_AUDIO:
        raise ValueError("Nahrávka chybí, nebo je moc dlouhá.")
    if wav[:4] != b"RIFF":
        raise ValueError("Nahrávka není WAV.")
    return _worker("prepis", "whisper", wav, 180).decode("utf-8", "replace").strip()


def rec(text):
    text = str(text or "").strip()[:MAX_TEXT]
    if not text:
        raise ValueError("Není co přečíst.")
    return _worker("rec", "voice", text.encode("utf-8"), 120)


if __name__ == "__main__":
    # Instalace pro server (gateway/install.sh) nebo ručně:
    #   python3 -m hub.hlas install /usr/local/lib/claude-hub-hlas
    if len(sys.argv) >= 2 and sys.argv[1] == "install":
        print(json.dumps(install(sys.argv[2] if len(sys.argv) > 2 else LOCAL_DIR)))
    else:
        print(json.dumps(state()))
