"""Jev od TypeSafe AI — levný model na hromadná rozhodnutí, přes Cloudflare.

Jev nepíše text. Dostane `state` (text nebo JSON) a otázky a vrátí typované
odpovědi s pravděpodobností: ano/ne (`noul`), výběr z možností (`choice`)
nebo stupeň na škále (`score`). Hodí se tam, kde by Claude jinak četl stovky
e-mailů nebo objednávek jednu po druhé.

Přímé API TypeSafe je jen na pozvánku, ale Cloudflare Workers AI má stejný
model jako `typesafe/jev`. Stačí tedy Account ID a API token z Cloudflare —
ty člověk vyplní v Nastavení → AI agenti a tady se uloží do
`~/.claude/jev.json` (jen pro majitele). Claude ho pak volá přes
`tools/jev.py` (skill `jev`), hub sám nic neposílá.

Tvar vstupu podle Cloudflare / TypeSafe:

    {"state": "…", "questions": {"spam": {"type": "noul",
                                         "instructions": "Je to spam?"}}}
"""
import json
import os
import re
import urllib.error
import urllib.request

CLAUDE_DIR = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
CONFIG_PATH = os.path.join(CLAUDE_DIR, "jev.json")
API = "https://api.cloudflare.com/client/v4"
MODEL = "typesafe/jev"
TIMEOUT = 20

ACCOUNT_RE = re.compile(r"[0-9a-f]{32}")

# Postup pro nastavení — UI ho jen vykreslí, ať je text na jednom místě.
SETUP = [
    {"title": "Účet na Cloudflare",
     "text": "Zdarma, stačí e-mail a heslo. Doménu přidávat nemusíš.",
     "url": "https://dash.cloudflare.com/sign-up", "button": "Založit účet"},
    {"title": "Kredit na AI",
     "text": "Jev je model třetí strany, platí se z kreditů AI Gateway "
             "(Credits Available → Manage → Top-up credits). Bez kreditu vrátí "
             "chybu placení. Účtuje se jen vstup, výstup je zdarma.",
     "url": "https://dash.cloudflare.com/?to=/:account/ai/ai-gateway",
     "button": "Otevřít AI Gateway"},
    {"title": "Account ID",
     "text": "Na stránce Workers AI je vpravo pole Account ID (32 znaků) — "
             "zkopíruj ho do pole níž.",
     "url": "https://dash.cloudflare.com/?to=/:account/ai/workers-ai",
     "button": "Otevřít Workers AI"},
    {"title": "API token",
     "text": "Create Token → šablona Workers AI → Use template → Continue to "
             "summary → Create Token. Token se ukáže jen jednou, zkopíruj ho hned.",
     "url": "https://dash.cloudflare.com/profile/api-tokens",
     "button": "Otevřít API Tokens"},
]


def _load():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        return {}
    return cfg if isinstance(cfg, dict) else {}


def settings():
    """(account_id, token), nebo None, když Jev není nastavený."""
    cfg = _load()
    acc, tok = str(cfg.get("account_id") or ""), str(cfg.get("api_token") or "")
    return (acc, tok) if acc and tok else None


def _write(cfg):
    os.makedirs(CLAUDE_DIR, exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    # Token se nesmí ani na chvíli objevit čitelný pro ostatní.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
    os.replace(tmp, CONFIG_PATH)
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass


def _request(token, method, path, body=None):
    """(HTTP kód, JSON odpověď). Chyba sítě → (0, {"error": …})."""
    req = urllib.request.Request(
        API + path, method=method,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers={"Authorization": "Bearer " + token,
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
            return res.status, json.loads(res.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"{}")
        except ValueError:
            return exc.code, {}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return 0, {"error": str(exc)}


def _errors(data):
    errs = data.get("errors") if isinstance(data, dict) else None
    if isinstance(errs, list) and errs:
        return "; ".join(str(e.get("message") if isinstance(e, dict) else e)
                         for e in errs)
    return str((data or {}).get("error") or "") if isinstance(data, dict) else ""


def run(input_obj, cfg=None):
    """Zavolá Jev. Vrací {"ok": True, "result": …} nebo {"ok": False, "error": …}."""
    cfg = cfg or settings()
    if not cfg:
        return {"ok": False, "error": "Jev není nastavený — Nastavení → AI agenti → Jev."}
    acc, tok = cfg
    code, data = _request(tok, "POST", f"/accounts/{acc}/ai/run/{MODEL}", input_obj)
    if code == 200 and isinstance(data, dict) and data.get("success", True):
        return {"ok": True, "result": data.get("result", data)}
    return {"ok": False, "code": code, "error": explain(code, _errors(data))}


def explain(code, detail):
    """Chyba z Cloudflare jako věta, se kterou člověk něco udělá."""
    low = (detail or "").lower()
    if code == 0:
        return "Cloudflare není dostupný: " + (detail or "chyba sítě")
    if code in (401, 403) or "authentication" in low or "unauthorized" in low:
        return "Token neplatí nebo nemá právo na Workers AI (vytvoř ho ze šablony Workers AI)."
    if code == 404 or "could not route" in low or "no route" in low:
        return "Account ID nesedí s tokenem, nebo účet model typesafe/jev nevidí."
    if code == 503 or "billing" in low or "credit" in low:
        return "Chybí kredit na AI — dobij ho v AI Gateway (Credits Available → Top-up)."
    return f"Cloudflare vrátil {code}: {detail or 'bez popisu'}"


# Nejmenší možný dotaz — ověří token, účet, model i kredit naráz a stojí
# zlomek haléře (účtuje se jen pár vstupních tokenů).
PROBE = {"state": "Dobrý den, mám dotaz k objednávce.",
         "questions": {"ok": {"type": "noul",
                              "instructions": "Is this a message from a customer?"}}}


def status():
    cfg = _load()
    acc = str(cfg.get("account_id") or "")
    return {
        "configured": bool(settings()),
        "account_id": acc,
        # Token zpátky do prohlížeče nikdy — jen konec, ať se pozná, který to je.
        "token_hint": ("…" + str(cfg.get("api_token"))[-4:]) if cfg.get("api_token") else "",
        "verified": bool(cfg.get("verified")),
        "model": MODEL,
        "setup": SETUP,
    }


def save(account_id, api_token):
    """Uloží údaje, jen když s nimi Jev doopravdy odpoví."""
    acc = (account_id or "").strip().lower()
    tok = (api_token or "").strip()
    if not tok and settings():
        tok = settings()[1]        # mění se jen Account ID, token zůstává
    if not ACCOUNT_RE.fullmatch(acc):
        return {"ok": False, "error": "Account ID má 32 znaků 0–9 a a–f."}
    if not tok or len(tok) < 20 or any(c.isspace() for c in tok):
        return {"ok": False, "error": "Vlož celý API token z Cloudflare."}
    res = run(PROBE, (acc, tok))
    if not res["ok"]:
        return res
    _write({"account_id": acc, "api_token": tok, "verified": True})
    return {"ok": True, "message": "Jev je napojený — Claude ho může používat.",
            **status()}


def remove():
    try:
        os.remove(CONFIG_PATH)
    except FileNotFoundError:
        pass
    return {"ok": True, **status()}
