"""
Vlastní název a ikona appky — každý si hub pojmenuje a dá mu vlastní ikonu
(Nastavení → Vzhled). Platí v okně i na liště hubu, pro ikonu na ploše
telefonu (PWA) a pro spouštěč na počítači.

Ikonu kreslí prohlížeč (emoji na barevném pozadí, nebo nahraný obrázek) do
PNG ve třech velikostech a hub je jen uloží — žádná knihovna na obrázky.
Leží v ~/.claude/hub-ikona-<velikost>.png; na serveru tedy v domově
prostoru, odkud je pro manifest čte brána (gateway/server.py, podle tajného
kódu účtu v adrese — prohlížeč manifest a ikony stahuje bez přihlášení).
"""
import html
import json
import os
import re

from . import core

VYCHOZI = "Claude Code Hub"
VELIKOSTI = (512, 192, 32)
MAX_IKONA = 2 * 1024 * 1024
MAX_NAZEV = 40
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


def nazev():
    """Název, který si člověk nastavil, nebo ''."""
    text = str(core.CONFIG.get("app_name") or "")
    return re.sub(r"[\x00-\x1f\x7f]", "", text).strip()[:MAX_NAZEV]


def zobrazeny():
    return nazev() or VYCHOZI


def ikona(size):
    """Cesta k vlastní ikoně dané velikosti, nebo ''."""
    path = os.path.join(core.CLAUDE_DIR, f"hub-ikona-{int(size)}.png")
    return path if os.path.isfile(path) else ""


def ikona_nebo_vychozi(size):
    return ikona(size) or os.path.join(STATIC, f"icon-{int(size)}.png")


def verze():
    """Mění se s každou novou ikonou — do adres kvůli cache prohlížeče."""
    times = [os.path.getmtime(p) for p in (ikona(s) for s in VELIKOSTI) if p]
    return int(max(times)) if times else 0


def stav():
    return {"name": nazev(), "shown": zobrazeny(), "default": VYCHOZI,
            "icon": verze(), "pwa_id": str(core.CONFIG.get("pwa_id") or "")}


def uloz(name=None, icons=None, reset=False):
    """Uloží název a/nebo ikony. `icons` = {velikost: base64 PNG}."""
    import base64
    if reset:
        for s in VELIKOSTI:
            p = ikona(s)
            if p:
                os.remove(p)
        core.save_config({"app_name": ""})
        sync_desktop()
        return stav()
    if name is not None:
        clean = re.sub(r"[\x00-\x1f\x7f]", "", str(name)).strip()
        if len(clean) > MAX_NAZEV:
            raise ValueError(f"Název může mít nejvýš {MAX_NAZEV} znaků.")
        core.save_config({"app_name": clean})
    if icons:
        data = {}
        for s in VELIKOSTI:
            raw = icons.get(str(s)) if isinstance(icons, dict) else None
            if raw is None:
                raise ValueError("Chybí ikona ve velikosti %d." % s)
            try:
                png = base64.b64decode(str(raw).split(",", 1)[-1], validate=False)
            except ValueError:
                raise ValueError("Ikona je poškozená.") from None
            if not png.startswith(b"\x89PNG\r\n\x1a\n") or len(png) > MAX_IKONA:
                raise ValueError("Ikona musí být PNG do 2 MB.")
            data[s] = png
        os.makedirs(core.CLAUDE_DIR, exist_ok=True)
        for s, png in data.items():
            target = os.path.join(core.CLAUDE_DIR, f"hub-ikona-{s}.png")
            tmp = target + ".tmp"
            with open(tmp, "wb") as fh:
                fh.write(png)
            os.chmod(tmp, 0o644)
            os.replace(tmp, target)
    sync_desktop()
    return stav()


# ── stránka a manifest ───────────────────────────────────────────────────────
def adresy():
    """Kde si prohlížeč bez přihlášení vezme manifest a ikonu na plochu.
    Na serveru přes bránu s kódem účtu, na počítači z hubu samotného."""
    pid = str(core.CONFIG.get("pwa_id") or "")
    v = verze()
    # Adresa manifestu se nemění (bez verze): Chrome u nainstalované appky
    # hlídá manifest na téže adrese a novou ikonu či název převezme sám —
    # změnu pozná podle adres ikon, ty nesou verzi.
    if core.CONFIG.get("gateway_user") and pid:
        return {"manifest": f"/manifest.webmanifest?u={pid}",
                "touch": f"/app-ikona/{pid}/192.png?v={v}"}
    return {"manifest": "/manifest.webmanifest", "touch": f"/app-ikona/192.png?v={v}"}


def uprav_stranku(page):
    """index.html s vlastním názvem a odkazy na vlastní manifest a ikony."""
    text = page.decode("utf-8")
    name = html.escape(zobrazeny())
    a = adresy()
    v = verze()
    text = text.replace("<title>Claude Code Hub</title>", f"<title>{name}</title>", 1)
    text = text.replace('<link rel="manifest" href="/manifest.webmanifest">',
                        f'<link rel="manifest" href="{html.escape(a["manifest"])}">', 1)
    text = text.replace('<link rel="apple-touch-icon" href="/icon-192.png">',
                        f'<link rel="apple-touch-icon" href="{html.escape(a["touch"])}">', 1)
    text = text.replace('<meta name="apple-mobile-web-app-title" content="Claude Hub">',
                        f'<meta name="apple-mobile-web-app-title" content="{name}">', 1)
    if v:
        text = text.replace('href="/icon-32.png"', f'href="/app-ikona/32.png?v={v}"', 1)
        text = text.replace('href="/icon-256.png"', f'href="/app-ikona/192.png?v={v}"', 1)
    return text.encode("utf-8")


def kratky(name, limit=15):
    """Krátký název pod ikonu na ploše — po celých slovech, ne uprostřed."""
    if len(name) <= limit:
        return name
    out = ""
    for word in name.split():
        if len((out + " " + word).strip()) > limit:
            break
        out = (out + " " + word).strip()
    return out or name[:limit]


def manifest(name, icon_base, version=0):
    """Manifest PWA s vlastním názvem; `icon_base` = kam ikony (bez velikosti)."""
    with open(os.path.join(STATIC, "manifest.webmanifest"), encoding="utf-8") as fh:
        data = json.load(fh)
    data["name"] = name
    data["short_name"] = kratky(name)
    q = f"?v={version}" if version else ""
    data["icons"] = [
        {"src": f"{icon_base}192.png{q}", "sizes": "192x192", "type": "image/png"},
        {"src": f"{icon_base}512.png{q}", "sizes": "512x512", "type": "image/png",
         "purpose": "any"},
        {"src": f"{icon_base}512.png{q}", "sizes": "512x512", "type": "image/png",
         "purpose": "maskable"},
    ]
    return data


# ── počítač: spouštěč a okno ─────────────────────────────────────────────────
def sync_desktop():
    """Linux: položka v nabídce aplikací (claude-code-hub.desktop) s vlastním
    názvem a ikonou. Jiné systémy si ikonu okna berou ze stránky."""
    if core.IS_WINDOWS or core.IS_MAC or core.CONFIG.get("gateway_user"):
        return
    path = os.path.expanduser("~/.local/share/applications/claude-code-hub.desktop")
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return
    icon = ikona(512) or core.ICON_PATH
    out, changed = [], False
    for line in lines:
        new = line
        if line.startswith("Name="):
            new = "Name=" + zobrazeny()
        elif line.startswith("Icon=") and icon:
            new = "Icon=" + icon
        changed |= new != line
        out.append(new)
    if changed:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write("\n".join(out) + "\n")
        os.replace(tmp, path)
