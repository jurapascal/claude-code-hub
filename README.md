<p align="center">
  <img src="https://capsule-render.vercel.app/api?type=waving&color=0:e0843c,100:0d1117&height=210&section=header&text=Claude%20Code%20Hub&fontSize=48&fontAlignY=38&fontColor=ffffff&desc=Ostatn%C3%AD&descAlignY=60&descSize=16&animation=fadeIn" alt="Claude Code Hub" width="100%"/>
</p>

# Claude Code Hub

> GTK okno kolem Claude Code — projekty v postranním panelu, každý otevřený jako vlastní tab se skutečným terminálem.

**Repo je jen instalačka.** Žádná data, žádné projekty, žádná paměť, žádné přihlašovací
údaje. Instalátor se podívá, co máš na disku ty, zapíše to do `~/.claude/hub-config.json`
a aplikace i slash příkazy pak čtou odtud.

📂 **Kolekce:** Ostatní
🖥 **Platforma:** Linux (GTK 3 + VTE)
👤 **Autor:** [@jurapascal](https://github.com/jurapascal)

---

## O projektu

Claude Code se normálně spouští v terminálu, jeden projekt = jedno okno. Hub z toho
dělá jednu aplikaci:

- **Postranní panel** — seznam projektů z nastavených složek (typ, branch, počet
  nezacommitovaných souborů), hledání, tlačítko na libovolnou jinou složku.
- **Taby** — klik na projekt otevře **skutečný terminál** (stejný VTE engine jako
  gnome-terminal), takže TUI Claude Code vypadá přesně jako v terminálu. Když session
  skončí, tab zůstane jako obyčejný shell.
- **Akční panel vpravo** — tlačítka posílají do chatu rovnou slash příkazy
  (`/save`, `/project`, `/deploy`, `/push`, `/status`, `/screenshot`). Tlačítko se
  zobrazí, jen když je odpovídající příkaz nainstalovaný.
- **Dark/light** — řídí se motivem plochy, přepínač 🌙/☀ v hlavičce.
- **Obsidian paměť (volitelné)** — když máš vault, panel ukáže poslední poznámky
  (learnings/errors/wins) a klikem je otevře v Obsidianu. Bez vaultu se sekce
  vůbec nezobrazí.

## Požadavky

| Co | Proč |
|---|---|
| Linux s GTK 3 | okno a terminál |
| `python3`, `python3-gi`, `gir1.2-vte-2.91` | běh aplikace |
| [Claude Code CLI](https://docs.claude.com/en/docs/claude-code) | vlastní účet, viz níže |
| Node.js 20+ | jen pro volitelný Playwright MCP |

```bash
# Debian / Ubuntu / Zorin
sudo apt install python3 python3-gi gir1.2-gtk-3.0 gir1.2-vte-2.91
# Claude Code CLI
npm install -g @anthropic-ai/claude-code
```

## Instalace

```bash
git clone https://github.com/jurapascal/claude-code-hub.git
cd claude-code-hub
bash install.sh
```

Instalátor se zeptá (s předvyplněnou detekcí), kde máš projekty a jestli máš Obsidian
vault, zapíše to do `~/.claude/hub-config.json`, nakopíruje appku do `~/.claude/`,
vyrenderuje slash příkazy, nainstaluje ikonu a položku **Claude Code** do nabídky
aplikací. Existující soubory zálohuje (`*.backup-<datum>`) a do `~/.claude/settings.json`
nesahá. Na konci se ještě zeptá na **Playwright MCP** (viz níže).
`bash install.sh --yes` = bez otázek, jen detekce — MCP se v tom režimu přeskočí.

Spuštění: ikona **Claude Code** v nabídce, nebo `python3 ~/.claude/claude-hub.py`.

## Slash příkazy

Instalátor je vyrenderuje do `~/.claude/skills/<jméno>/SKILL.md` — **odsud si je
Claude Code 2.1+ načítá**; starší složka `~/.claude/commands/*.md` se v tomhle buildu
ignoruje (příkazy odtud hlásí „Unknown command"). Cesty v nich nejsou natvrdo, doplní
se z tvého konfigu při instalaci.

| Příkaz | Co dělá | Potřebuje vault |
|---|---|---|
| `/save` | uloží, co jsme právě dělali, jako poznámku do paměti | ✅ |
| `/learn <popis>` | uloží poznatek / chybu / úspěch podle klíčových slov | ✅ |
| `/project` | založí nebo aktualizuje poznámku k aktuálnímu projektu | ✅ |
| `/skill <název>` | načte skill z vaultu | ✅ |
| `/deploy` | nasadí projekt — FTP (`.ftp-deploy.json`) nebo git, auto-detekce | — |
| `/ftp` | FTP/FTPS/SFTP deploy, poprvé si vyžádá údaje | — |
| `/push` | commit + push na GitHub (nikdy force) | — |
| `/status` | přehled projektů a jejich git stavů | — |
| `/screenshot <url>` | screenshot webu (desktop + mobil) | — |
| `/audit <url>` | vizuální a technický audit webu | — |

Bez vaultu se čtyři paměťové příkazy vůbec neinstalují — a tlačítka na ně v Hubu
se nezobrazí. Vlastní příkazy si přidáš jako další složku do `~/.claude/skills/`;
instalátor je nemaže.

## Playwright MCP (volitelné)

Prohlížeč pro Claude Code — otevře stránku, klikne, přečte konzoli, udělá screenshot.
Pracuje nad accessibility tree, ne nad pixely, takže nepotřebuje vision model.
Instalátor ho nabídne na konci, ručně to jsou dva příkazy:

```bash
claude mcp add playwright -s user -- npx @playwright/mcp@latest --browser chromium
npx @playwright/mcp@latest install-browser chrome-for-testing
```

- `-s user` = platí ve všech projektech; zapíše se do `~/.claude.json`, ne do repa.
- `--browser chromium` jede na bundlovaném Chromiu — výchozí kanál `chrome` by chtěl
  systémový Google Chrome.
- Druhý příkaz je nutný: verze prohlížeče se váže na verzi MCP serveru, jinak první
  `browser_navigate` vrátí „Browser chrome-for-testing is not installed". Stahuje
  ~115 MB do `~/.cache/ms-playwright` (jen co chybí).
- Nástroje se pak jmenují `mcp__playwright__*` a naběhnou po restartu session.
- Bez vyskakujícího okna: přidat za `--browser chromium` ještě `--headless`.
- Snapshoty stránek si server ukládá do `.playwright-mcp/` v aktuální složce —
  hodí se do `.gitignore`.

Odebrání: `claude mcp remove playwright -s user`.
[Dokumentace](https://playwright.dev/docs/getting-started-mcp)

## Přihlášení vlastním účtem

Aplikace **žádné přihlašovací údaje neobsahuje ani nesdílí** — každý si pustí Claude Code
pod svým účtem:

1. Otevři v Hubu libovolný projekt (nebo tab **shell** a napiš `claude`).
2. V Claude Code napiš `/login` a projdi přihlášením v prohlížeči.
3. Token se uloží do `~/.claude/.credentials.json` na tvém počítači — do repa nepatří
   a je v `.gitignore`.

## Konfigurace

`~/.claude/hub-config.json` (vzor je [`hub-config.example.json`](hub-config.example.json)):

| Klíč | Význam | Výchozí |
|---|---|---|
| `project_dirs` | složky, ve kterých se hledají projekty (neexistující se ignorují) | `~/Desktop`, `~/Projects`, `~/dev`, `/opt/lampp/htdocs` |
| `brain_dir` | Obsidian vault s pamětí; když neexistuje, sekce paměti se skryje | `~/Obsidian/Claude-Brain` |
| `icon` | ikona okna | `~/.local/share/icons/claude-code.png` |
| `ftp_deploy_script` | skript pro FTP deploy (není součástí repa) | `~/.claude/ftp-deploy.sh` |

Projekt se do panelu dostane, když ve složce je `.git`, `package.json`, `composer.json`,
soubor `*.php` nebo Shopify struktura (`sections/`, `templates/`) — podle toho se pozná
i typ (Git / Node / PHP / Shopify).

## Obsah repa

```
install.sh                instalačka: detekce cest → konfig → appka + slash příkazy
claude-hub.py             hlavní aplikace (GTK okno, sidebar, taby, akční panel)
claude-wrapper.sh         boot sekvence před spuštěním claude + restart po ctrl+c
hooks/save-session.py     Stop hook — uloží stav projektů do session-state.md
skills/<jméno>/SKILL.md   šablony slash příkazů ({{MEMORY_DIR}} apod. doplní instalátor)
hub-config.example.json   vzor konfigurace
settings.example.json     vzor zapojení Stop hooku (bez jakýchkoli klíčů)
assets/                   ikona
```

## Co v repu záměrně není

Osobní věci ze `~/.claude`: `settings.json` s tokeny, `.credentials.json`, obsah paměti
(Obsidian vault), `.ftp-deploy.json` s FTP hesly a skript `ftp-deploy.sh`. Repo je
privátní, ale i tak sem tajemství nepatří.

## Aktualizace

```bash
cd claude-code-hub && git pull && bash install.sh
```

`hub-config.json` zůstane nedotčený, přepíší se soubory aplikace a znovu vyrenderují
slash příkazy z repa. Vlastní příkazy ve `~/.claude/skills/` zůstanou.
