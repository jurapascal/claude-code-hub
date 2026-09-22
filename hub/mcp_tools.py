"""
Nástroje pro napojení z appky Claude (MCP) — díl, který běží v prostoru.

Protokol, přihlášení a oprávnění drží brána (gateway/mcp.py). Tohle je jen
to, co se dá udělat **uvnitř** prostoru uživatele: hub tu běží v sandboxu,
ve kterém je zapisovatelný jen jeho domov a firemní či sdílené trezory jsou
přivázané jen ke čtení (a jen ty, na které má právo). Co tenhle modul
přečte nebo spustí, tedy nikdy nesáhne na cizí prostor — to hlídá sandbox,
ne tenhle kód. Cesty se tu navíc drží v domově, aby nástroj nečetl ani to,
co sandbox ukazuje ke čtení (kód hubu, systém).

Volá ho jen brána (`/api/mcp` s tokenem instance, který prohlížeč nemá);
zápis do firemního a sdílených trezorů dělá brána sama, sem se nedostane.
"""
import os
import re
import subprocess

from . import chats, core, cteni

MAX_READ = 512 * 1024          # kolik textu jde najednou do odpovědi
MAX_WRITE = 2 * 1024 * 1024
MAX_OUTPUT = 100 * 1024        # výstup příkazu
CMD_TIMEOUT = 120
CMD_TIMEOUT_MAX = 600
# Proměnné, které příkaz nedostane: přihlášení Clauda a klíče. Výstup příkazu
# odchází do konverzace v appce — kdyby ho někdo podstrčeným textem
# přemluvil vypsat prostředí, nemá co vynést.
SECRET_ENV = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|COOKIE|AUTH)", re.I)


class ToolError(Exception):
    """Chyba, kterou má vidět Claude (špatná cesta, chybějící soubor…)."""


def _home():
    return os.path.realpath(core.HOME)


def _in_home(rel, must_exist=True):
    """Cesta uvnitř domova (relativní k němu, nebo absolutní v něm).
    Odkazy se rozbalí a výsledek musí zůstat v domově."""
    home = _home()
    raw = str(rel or "").strip() or "."
    full = os.path.realpath(raw if os.path.isabs(raw) else os.path.join(home, raw))
    if os.path.commonpath([full, home]) != home:
        raise ToolError("Mimo tvůj prostor to nejde — cesta musí být v domově "
                        f"({home}).")
    if must_exist and not os.path.exists(full):
        raise ToolError(f"Tohle v prostoru není: {raw}")
    return full


def _vault(which):
    """osobni / firma / sdilene:<zkratka> → jak mu říká core ('' = osobní)."""
    which = str(which or "osobni").strip()
    if which in ("", "osobni", "osobní"):
        return ""
    if which == "firma":
        if not core.company_vault():
            raise ToolError("Firemní Obsidian tenhle účet nevidí.")
        return "firma"
    if re.fullmatch(r"sdilene:[a-z0-9][a-z0-9-]{0,49}", which):
        if which[8:] not in {v["slug"] for v in core.shared_state()}:
            raise ToolError("Do tohohle sdíleného Obsidianu nepatříš.")
        return which
    raise ToolError("Neznámý Obsidian — použij osobni, firma nebo sdilene:<zkratka>.")


# ── projekty a konverzace ────────────────────────────────────────────────────
def projekty(_args):
    out = []
    for p in core.get_projects():
        if p.get("archived"):
            continue
        out.append({"jmeno": p.get("label") or p["name"], "cesta": p["path"],
                    "typ": p.get("type", ""), "vetev": p.get("branch", ""),
                    "necommitnute": p.get("dirty", 0)})
    return {"projekty": out}


def konverzace(args):
    hledat = core._fold(args.get("hledat") or "")
    try:
        limit = max(1, min(int(args.get("limit") or 30), 200))
    except (TypeError, ValueError):
        limit = 30
    out = []
    for c in chats.list_chats():
        text = core._fold(" ".join([c.get("title") or "", c.get("prompt") or "",
                                    c.get("project") or ""]))
        if hledat and hledat not in text:
            continue
        out.append({"id": c["id"], "nazev": c.get("title") or "", "projekt": c.get("project") or "",
                    "zacatek": (c.get("prompt") or "")[:200], "zmeneno": c.get("updated")})
        if len(out) >= limit:
            break
    return {"konverzace": out}


def konverzace_cti(args):
    cid = str(args.get("id") or "")
    if not re.fullmatch(r"[0-9a-f-]{8,64}", cid):
        raise ToolError("Chybí id konverzace (z nástroje konverzace).")
    path = cteni.path_for(cid)
    if not path:
        raise ToolError("Takovou konverzaci v prostoru nemáš.")
    # Od konce: u dlouhé konverzace je podstatné, kde skončila.
    data = cteni.read(path, 0, tail=True)
    lines, size = [], 0
    for b in data.get("blocks") or []:
        kind = b.get("kind")
        if kind == "me":
            line = "## Uživatel\n" + (b.get("text") or "")
        elif kind == "say":
            line = "## Claude\n" + (b.get("text") or "")
        elif kind == "tool":
            line = f"- nástroj {b.get('name', '')}: {(b.get('title') or '')[:200]}"
        else:
            continue
        size += len(line)
        lines.append(line)
    text = "\n\n".join(lines)
    if len(text) > MAX_READ:
        text = "… (začátek vynechán)\n\n" + text[-MAX_READ:]
    return {"id": cid, "text": text}


# ── Obsidian ─────────────────────────────────────────────────────────────────
def obsidian_seznam(args):
    vault = _vault(args.get("obsidian"))
    tree = core.vault_tree(vault)
    notes = [n["path"] for n in tree.get("notes") or []]
    return {"obsidian": tree.get("name", ""), "poznamky": notes[:2000],
            "zkraceno": len(notes) > 2000}


def obsidian_hledat(args):
    vault = _vault(args.get("obsidian"))
    q = str(args.get("dotaz") or "").strip()
    if not q:
        raise ToolError("Chybí dotaz.")
    return {"vysledky": core.vault_search(q, limit=40, vault=vault)}


def obsidian_cti(args):
    vault = _vault(args.get("obsidian"))
    note = core.vault_note(str(args.get("cesta") or ""), vault)
    if not note:
        raise ToolError("Taková poznámka v Obsidianu není.")
    return {"cesta": note["path"], "text": note["text"], "zkraceno": note.get("truncated", False)}


def obsidian_zapis_osobni(args):
    """Zápis do osobního trezoru — firemní a sdílené zapisuje brána."""
    rel = str(args.get("cesta") or "")
    text = args.get("text")
    if not isinstance(text, str):
        raise ToolError("Chybí text poznámky.")
    full = core.vault_target(rel, "")
    if not full:
        raise ToolError("Tuhle cestu v trezoru uložit nejde.")
    if os.path.isfile(full) and not args.get("prepsat"):
        return {"ok": False, "existuje": True,
                "zprava": "Poznámka už existuje — pošli znovu s prepsat: true, "
                          "pokud ji chceš přepsat."}
    result = core.vault_save(rel, text)
    if not result.get("ok"):
        raise ToolError(result.get("error") or "Uložit se nepodařilo.")
    return {"ok": True, "cesta": result.get("path", rel)}


# ── soubory a příkazy v prostoru ─────────────────────────────────────────────
def slozka(args):
    full = _in_home(args.get("cesta"))
    if not os.path.isdir(full):
        raise ToolError("Tohle není složka.")
    items = []
    with os.scandir(full) as it:
        for e in sorted(it, key=lambda e: e.name.lower()):
            if len(items) >= 1000:
                break
            try:
                is_dir = e.is_dir(follow_symlinks=False)
                size = 0 if is_dir else e.stat(follow_symlinks=False).st_size
            except OSError:
                continue
            items.append({"jmeno": e.name + ("/" if is_dir else ""), "velikost": size})
    return {"cesta": full, "polozky": items}


def soubor_cti(args):
    full = _in_home(args.get("cesta"))
    if not os.path.isfile(full):
        raise ToolError("Tohle není soubor.")
    size = os.path.getsize(full)
    with open(full, "rb") as fh:
        raw = fh.read(MAX_READ)
    if b"\0" in raw[:8192]:
        raise ToolError(f"Soubor je binární ({size} B) — přečíst jde jen text.")
    return {"cesta": full, "text": raw.decode("utf-8", "replace"),
            "zkraceno": size > MAX_READ}


def soubor_zapis(args):
    text = args.get("text")
    if not isinstance(text, str):
        raise ToolError("Chybí text souboru.")
    data = text.encode("utf-8")
    if len(data) > MAX_WRITE:
        raise ToolError("Soubor je moc velký (víc než 2 MB).")
    full = _in_home(args.get("cesta"), must_exist=False)
    parent = os.path.dirname(full)
    # Rodič se kontroluje znovu až po založení — odkaz v cestě by jinak mohl
    # vést ven z domova.
    os.makedirs(parent, exist_ok=True)
    _in_home(parent)
    if os.path.islink(full):
        raise ToolError("Na místě souboru je odkaz — přes něj se nezapisuje.")
    if os.path.exists(full) and not args.get("prepsat"):
        return {"ok": False, "existuje": True,
                "zprava": "Soubor už existuje — pošli znovu s prepsat: true."}
    tmp = full + ".hub-tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, full)
    return {"ok": True, "cesta": full, "bajtu": len(data)}


def prikaz(args):
    cmd = str(args.get("prikaz") or "").strip()
    if not cmd:
        raise ToolError("Chybí příkaz.")
    cwd = _in_home(args.get("slozka") or ".")
    if not os.path.isdir(cwd):
        raise ToolError("Pracovní složka není složka.")
    try:
        timeout = max(1, min(int(args.get("limit_sekund") or CMD_TIMEOUT), CMD_TIMEOUT_MAX))
    except (TypeError, ValueError):
        timeout = CMD_TIMEOUT
    env = {k: v for k, v in os.environ.items() if not SECRET_ENV.search(k)}
    env["HUB_MCP"] = "1"
    try:
        proc = subprocess.run(["/bin/bash", "-lc", cmd], cwd=cwd, env=env,
                              stdin=subprocess.DEVNULL, capture_output=True,
                              timeout=timeout)
        code, out, err = proc.returncode, proc.stdout, proc.stderr
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        code, out, err = -1, exc.stdout or b"", exc.stderr or b""
        timed_out = True

    def cut(b):
        text = b.decode("utf-8", "replace")
        return ("… (začátek vynechán)\n" + text[-MAX_OUTPUT:]) if len(text) > MAX_OUTPUT else text
    return {"kod": code, "vystup": cut(out), "chyby": cut(err),
            "vyprsel_cas": timed_out, "slozka": cwd}


TOOLS = {
    "projekty": projekty,
    "konverzace": konverzace,
    "konverzace_cti": konverzace_cti,
    "obsidian_seznam": obsidian_seznam,
    "obsidian_hledat": obsidian_hledat,
    "obsidian_cti": obsidian_cti,
    "obsidian_zapis_osobni": obsidian_zapis_osobni,
    "slozka": slozka,
    "soubor_cti": soubor_cti,
    "soubor_zapis": soubor_zapis,
    "prikaz": prikaz,
}


def call(name, args):
    """{"ok": True, "result": …} nebo {"ok": False, "error": "…"}."""
    fn = TOOLS.get(name)
    if not fn:
        return {"ok": False, "error": f"Neznámý nástroj: {name}"}
    if not isinstance(args, dict):
        args = {}
    try:
        return {"ok": True, "result": fn(args)}
    except ToolError as exc:
        return {"ok": False, "error": str(exc)}
    except OSError as exc:
        return {"ok": False, "error": f"{exc.strerror or exc}"}
