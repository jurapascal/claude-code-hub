<p align="center">
  <img src="https://capsule-render.vercel.app/api?type=waving&color=0:e0843c,100:0d1117&height=210&section=header&text=Claude%20Code%20Hub&fontSize=48&fontAlignY=38&fontColor=ffffff&desc=Ostatn%C3%AD&descAlignY=60&descSize=16&animation=fadeIn" alt="Claude Code Hub" width="100%"/>
</p>

# Claude Code Hub

> Jedno okno kolem Claude Code — projekty v postranním panelu, každý otevřený jako vlastní tab se skutečným terminálem. Linux, Windows i macOS z jednoho kódu.

**Repo je jen instalačka.** Žádná data, žádné projekty, žádná paměť, žádné přihlašovací
údaje. Instalátor se podívá, co máš na disku ty, zapíše to do `~/.claude/hub-config.json`
a aplikace i slash příkazy pak čtou odtud.

📂 **Kolekce:** Ostatní
🖥 **Platforma:** Linux · Windows 10/11 · macOS
👤 **Autor:** [@jurapascal](https://github.com/jurapascal)

---

## O projektu

Claude Code se normálně spouští v terminálu, jeden projekt = jedno okno. Hub z toho
dělá jednu aplikaci:

- **Postranní panel** — seznam projektů z nastavených složek (typ, branch, počet
  nezacommitovaných souborů), hledání, tlačítko na libovolnou jinou složku.
  Pravý klik na projekt = Deploy, shell, poznámka do paměti, otevřít složku.
- **Taby** — klik na projekt otevře **skutečný terminál** (pty + xterm.js), takže TUI
  Claude Code vypadá přesně jako v terminálu. Když session skončí, tab zůstane jako
  obyčejný shell. Taby jdou přejmenovat dvojklikem a přetáhnout myší.
- **Akční panel vpravo** — tlačítka posílají do chatu rovnou slash příkazy
  (`/save`, `/project`, `/deploy`, `/push`, `/status`, `/screenshot`). Tlačítko se
  zobrazí, jen když je odpovídající příkaz nainstalovaný.
- **Reload nezabíjí session** — terminály běží v serveru, ne ve stránce. Když se okno
  načte znovu, hub se připojí zpátky k běžícím Claude session a dohraje jejich výpis.
- **Dark/light** — řídí se motivem systému, přepínač v hlavičce.
- **Obsidian paměť (volitelné)** — když máš vault, panel ukáže poslední poznámky
  (learnings/errors/wins) a klikem je otevře v Obsidianu. Bez vaultu se sekce
  vůbec nezobrazí.

## Jak to funguje

Okno je tenká slupka kolem lokální web appky — díky tomu je UI na všech systémech
jedno a totéž a liší se jen dvě věci pod ním:

```
  okno (chromium --app / WebKitGTK / prohlížeč)
        │  http + websocket, jen 127.0.0.1, na token
  Python server  ──►  pty  ──►  bash  ──►  claude
   (stdlib)            │
                       ├─ Linux/macOS: modul `pty` ze standardní knihovny
                       └─ Windows:     ConPTY přes pywinpty
```

- Server poslouchá **jen na 127.0.0.1** na náhodném portu a každý požadavek musí mít
  token, který se generuje při startu a předává se oknu v URL. Zvenčí se k němu nedá
  dostat.
- **Windows jede na Git for Windows** — `claude-wrapper.sh` i bashové slash příkazy
  (`/deploy`, `/ftp`, `/audit`) tak běží beze změny na všech systémech.
- Terminál je [xterm.js](https://xtermjs.org) přibalený v repu (`hub/static/vendor/`),
  nic se nestahuje z internetu za běhu.

## Požadavky

| Co | Proč | Kde |
|---|---|---|
| Python 3.9+ | běh aplikace (jinak jen standardní knihovna) | všude |
| [Claude Code CLI](https://code.claude.com/docs/en/setup) | vlastní účet, viz níže | všude |
| [Git for Windows](https://git-scm.com/downloads/win) | dodává `bash.exe` — bez něj se tab neotevře | **Windows (povinné)** |
| `pywinpty` | ConPTY terminál; instalačka ho doinstaluje sama | Windows |
| chromium / WebKitGTK | okno bez adresního řádku (jinak se hub otevře jako záložka) | Linux, macOS |
| Node.js 20+ | jen pro volitelný Playwright MCP | všude |
| [Obsidian](https://obsidian.md/download) | paměť (`/save`, `/learn`, `/project`) a panel poznámek | volitelné, všude |
| [GitHub CLI](https://github.com/cli/cli#installation) | `gh auth login` → klonování a `/push` z čerstvého stroje | volitelné, všude |

Na Linuxu **už není potřeba GTK 3 ani VTE**. Chceš-li nativní okno bez prohlížeče:

```bash
sudo apt install gir1.2-webkit2-4.1     # nebo prostě chromium
```

## Instalace

### Linux / macOS

```bash
git clone https://github.com/jurapascal/claude-code-hub.git
cd claude-code-hub
bash install.sh
```

### Windows

V PowerShellu (**bez** práv správce):

```powershell
git clone https://github.com/jurapascal/claude-code-hub.git
cd claude-code-hub
powershell -ExecutionPolicy Bypass -File install.ps1
```

Instalačka nabídne doinstalovat, co chybí (Python, Git for Windows, Claude Code CLI,
Obsidian, GitHub CLI — všechno přes `winget`), sama přidá `pywinpty`, vyrobí zástupce
**Claude Code** v nabídce Start a volitelně na ploše. Zástupce spouští `pythonw.exe`,
takže se vedle okna neotevírá černá konzole; kdyby okno zůstalo prázdné, důvod je
v `%USERPROFILE%\.claude\hub.log`.

### Obě platformy

Instalátor se zeptá (s předvyplněnou detekcí), kde máš projekty a jestli máš Obsidian
vault, zapíše to do `~/.claude/hub-config.json`, nakopíruje appku do `~/.claude/`
a vyrenderuje slash příkazy. Existující soubory zálohuje (`*.backup-<datum>`) a do
`~/.claude/settings.json` nesahá.

Na čerstvém stroji cestou nabídne i to, bez čeho by hub sice běžel, ale nebylo by
s ním co dělat — vždycky otázkou, nikdy potichu:

| Nabídne | Co udělá |
|---|---|
| **Obsidian** | `winget install Obsidian.Obsidian`, na Linuxu flatpak z Flathubu (jinak snap), na macOS `brew --cask` |
| **prázdný vault** | když žádný nenajde: `~/Obsidian/Claude-Brain` s `memory/`, `skills/` a rozcestníkem `MEMORY.md` — teprve tím se zapnou `/save`, `/learn`, `/project`, `/skill` |
| **GitHub CLI** | `winget install GitHub.cli` / apt / dnf / pacman / snap / brew, pak `gh auth login` + `gh auth setup-git` |
| **Playwright MCP** | prohlížeč pro Claude Code (~115 MB, potřebuje Node.js 20+) |
| **přihlášení** | spustí `claude`, projdeš `/login` a dáš `/exit` |

`--yes` (resp. `-Yes`) = bez otázek, jen detekce — v tom režimu se nic z tabulky
neinstaluje, jen se vypíše, co chybí.

Když něco nehraje:

```bash
python3 ~/.claude/claude-hub.py --doctor     # co na tomhle stroji je a co chybí
```

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
| `bash` | cesta k `bash.exe` (Windows); prázdné = najde se sám | `""` |
| `browser` | čím otevřít okno; prázdné = chromium → WebKitGTK → výchozí prohlížeč | `""` |

Projekt se do panelu dostane, když ve složce je `.git`, `package.json`, `composer.json`,
soubor `*.php` nebo Shopify struktura (`sections/`, `templates/`) — podle toho se pozná
i typ (Git / Node / PHP / Shopify).

## Obsah repa

```
install.sh                instalačka pro Linux/macOS
make-zip.sh               balíček k rozeslání (ZIP + návod pro příjemce, bez gitu a GitHubu)
install.ps1               instalačka pro Windows (winget, zástupci, pywinpty)
claude-hub.py             launcher — nastartuje server a otevře okno (--doctor, --no-browser)
hub/core.py               konfig, skenování projektů a paměti, platformové rozdíly
hub/server.py             lokální HTTP + websocket server, správa pty session
hub/pty_backend.py        pty: stdlib na Linux/macOS, pywinpty na Windows
hub/window.py             hostitel okna: chromium --app → WebKitGTK → prohlížeč
hub/static/               UI (index.html, hub.css, hub.js) + přibalený xterm.js
claude-wrapper.sh         boot sekvence před spuštěním claude + restart po ctrl+c
hooks/save-session.py     Stop hook — uloží stav projektů do session-state.md
skills/<jméno>/SKILL.md   šablony slash příkazů ({{MEMORY_DIR}} apod. doplní instalátor)
legacy/claude-hub-gtk.py  původní GTK 3 + VTE verze (Linux only, už se neinstaluje)
hub-config.example.json   vzor konfigurace
settings.example.json     vzor zapojení Stop hooku (bez jakýchkoli klíčů)
assets/                   ikona (.png pro Linux, .ico pro Windows)
assets/vault/MEMORY.md    rozcestník paměti pro nově založený vault
```

## Co v repu záměrně není

Osobní věci ze `~/.claude`: `settings.json` s tokeny, `.credentials.json`, obsah paměti
(Obsidian vault), `.ftp-deploy.json` s FTP hesly a skript `ftp-deploy.sh`. Repo je
privátní, ale i tak sem tajemství nepatří.

## Aktualizace

```bash
cd claude-code-hub && git pull && bash install.sh          # Linux / macOS
```
```powershell
cd claude-code-hub; git pull; powershell -ExecutionPolicy Bypass -File install.ps1
```

`hub-config.json` zůstane nedotčený, přepíší se soubory aplikace a znovu vyrenderují
slash příkazy z repa. Vlastní příkazy ve `~/.claude/skills/` zůstanou.
