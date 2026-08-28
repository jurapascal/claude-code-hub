<p align="center">
  <img src="assets/hub-wordmark.svg" alt="Claude Code Hub" width="420"/>
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
- **Bublina místo vstupního řádku** — spodek terminálu, kde Claude Code kreslí
  svoje vstupní pole, překryje chatovací bublina: pole na text, přepínač modelu,
  slash příkazy, příloha a režimy (Shift+Tab, Esc). Odeslané jde do stejného pty,
  takže Claude dostane přesně to, co by dostal z klávesnice. Jakmile se dole
  objeví dialog (výběr modelu, dotaz na oprávnění) nebo se odroluje nahoru,
  bublina se sama složí do proužku — nikdy nezakryje to, na co se máš dívat.
- **Akční panel vpravo** — tlačítka posílají do chatu rovnou slash příkazy
  (`/save`, `/project`, `/deploy`, `/push`, `/status`, `/screenshot`). Tlačítko se
  zobrazí, jen když je odpovídající příkaz nainstalovaný.
- **Reload nezabíjí session** — terminály běží v serveru, ne ve stránce. Když se okno
  načte znovu, hub se připojí zpátky k běžícím Claude session a dohraje jejich výpis.
- **Obrázky** — screenshot ze schránky (Ctrl+V) nebo soubor přetažený do tabu se uloží
  a do promptu se vypíše jeho cesta, takže si ho Claude rovnou přečte.
- **Diakritika** — háčky a čárky chodí přes vstupní metodu systému jako composition
  events; hub je bere na sebe (`hub/static/ime.js`), protože xterm.js je při rychlejším
  psaní slepuje a do řádku pak teče nashromážděný balast.
- **Průvodce prvním spuštěním** — vzhled, složky s projekty, kde bydlí paměť
  a jak se zálohuje. Umí napojit **existující Obsidian vault** (najde si ho sám,
  Obsidian si seznam vede) i stáhnout ho z gitu. Kdykoli později totéž pod ⚙.
- **Dvě „+" tlačítka** — nový tab s Claude Code, nebo holý terminál. V nastavení
  se dá kterékoli schovat.
- **Správa projektů** — u každého `⋯` s možnostmi: přejmenovat, zařadit do
  skupiny, dát fotku, přiřadit GitHub repo (samo se načte z `git remote`),
  archivovat, odebrat z panelu (složka na disku zůstane). Přidat se dá i složka
  mimo nastavené cesty.
- **Briefing projektu** — napíšeš vlastními slovy, o co jde, a uloží se do
  `CLAUDE.md` projektu, takže si to Claude Code přečte sám, jakmile ho otevřeš.
  Do cizího obsahu se nesahá, blok je ohraničený značkami. `/brief` z něj pak
  vytáhne strukturovaná fakta do poznámky v paměti.
- **Uvítání** — scéna podle denní doby, kde se naposledy dělalo, co zůstalo
  rozdělané a pár čísel o používání.
- **Statistiky** — kolik tokenů, kdy během dne píšeš, které projekty berou
  nejvíc, a commity z GitHubu. Počítá se z toho, co si Claude Code ukládá do
  `~/.claude`; nic se nikam neposílá.
- **Napojení (MCP)** — v nastavení je vidět, na co Claude Code dosáhne: konektory
  z účtu claude.ai i servery zaregistrované na stroji, u každého jestli opravdu
  odpovídá. Nečte se jen registrace — každý server se osloví, takže je poznat
  i ten, který je sice zapsaný, ale chce přihlásit. Clockify se dá přidat jedním
  klikem.
- **Nastavení po sekcích** — vzhled, projekty, taby, paměť, napojení, aktualizace
  a logy se přepínají tlačítky vlevo; vybraná sekce se pamatuje.
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

Jeden řádek, ať už na stroji je cokoli:

```bash
curl -fsSL https://raw.githubusercontent.com/jurapascal/claude-code-hub/main/get.sh | bash
```

Stáhne repo do `~/.claude/hub-src` a spustí instalačku. Podruhé spuštěný stejný
příkaz hub **zaktualizuje**.

### Windows

V PowerShellu (**bez** práv správce):

```powershell
irm https://raw.githubusercontent.com/jurapascal/claude-code-hub/main/get.ps1 | iex
```

Zástupce **Claude Code** v nabídce Start spouští `pythonw.exe`, takže se vedle okna
neotevírá černá konzole; kdyby okno zůstalo prázdné, důvod je v
`%USERPROFILE%\.claude\hub.log`.

### Windows — co k tomu patří

| Co | Proč |
|---|---|
| **Git for Windows** | dodává `bash.exe`, na kterém stojí každý tab a všechny slash příkazy — bez něj se tab neotevře |
| **pywinpty** | ConPTY terminál; instalačka ho doinstaluje sama |
| **Python 3.9+** | běh aplikace |

Zástupce **Claude Code** v nabídce Start spouští `pythonw.exe`, takže se vedle
okna neotevírá černá konzole. Schránka jede přes `Get-Clipboard` a `clip`;
`PRIMARY` (výběr myší) Windows nezná, takže tam prostřední tlačítko nevkládá.
Napojení paměti používá **křižovatku** (`mklink /J`), ne symlink — ten by chtěl
práva správce nebo vývojářský režim.

Kdyby okno zůstalo prázdné, důvod je v `%USERPROFILE%\.claude\hub.log`.

Když si chceš ověřit, že na tvém stroji sedí i to, co se z Linuxu vyzkoušet
nedá (odkazy na složku, ConPTY, jméno složky s pamětí), spusť:

```powershell
powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\.claude\tools\windows-check.ps1"
```

### Z klonu

```bash
git clone https://github.com/jurapascal/claude-code-hub.git
cd claude-code-hub
bash install.sh                                          # Linux / macOS
powershell -ExecutionPolicy Bypass -File install.ps1     # Windows
```

### Co instalačka udělá

Zapíše, kde máš projekty a paměť, do `~/.claude/hub-config.json`, nakopíruje appku
do `~/.claude/` a vyrenderuje slash příkazy. Existující soubory zálohuje
(`*.backup-<datum>`).

Všechno, bez čeho by hub sice běžel, ale nebylo by s ním co dělat, **doinstaluje
sama** — otázka navíc byla hlavní důvod, proč instalace nebyla rychlá:

| Nasadí | Co udělá |
|---|---|
| **Obsidian** | `winget install Obsidian.Obsidian`, na Linuxu flatpak z Flathubu (jinak snap), na macOS `brew --cask` |
| **vault** | naklonuje ten tvůj z gitu, nebo založí `~/Obsidian/Claude-Brain` s `memory/`, `skills/` a rozcestníkem `MEMORY.md` — teprve tím se zapnou `/save`, `/learn`, `/project`, `/skill` |
| **GitHub CLI** | `winget install GitHub.cli` / apt / dnf / pacman / snap / brew, pak `gh auth login` + `gh auth setup-git` |
| **Playwright MCP** | prohlížeč pro Claude Code (~115 MB, potřebuje Node.js 20+) |
| **Clockify MCP** | volitelně, jen když zadáš API klíč (nebo ho máš v `CLOCKIFY_API_KEY`) |
| **hooky** | `Stop` (uloží stav session) a `SessionStart` (načte kategorie skillů z vaultu a stav minulé session) do `settings.json` |
| **skilly** | 571 hotových postupů z [claude-brain-skills](https://github.com/jurapascal/claude-brain-skills) do vaultu — existující složku nikdy nepřepíše |

Zeptá se jen na to, co uhádnout nejde:

- **kde máš paměť** — adresa vault repa, nebo Enter a založí prázdný,
- **bypass režim** — jestli má Claude přestat ptát se na potvrzení u každého
  příkazu a úpravy souboru. Rychlejší, ale běží bez brzdy; zapínej to jen na
  vlastním stroji. Kdykoli později `/permissions`.
- **přihlášení** do GitHubu a do Claude Code.

`settings.json` je tvůj: instalačka do něj jen **přidá**, co chybí, předtím ho
zazálohuje a vlastního hooku na stejnou událost se nedotkne. Rozbitý JSON nechá být.

| Přepínač | Co udělá |
|---|---|
| `--yes` / `-Yes` | bez otázek — doinstaluje, co jde, přihlášení a bypass přeskočí |
| `--minimal` | jen hub: nic nedoinstalovává, do `settings.json` nesahá |

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
claude mcp add playwright -s user -- npx @playwright/mcp@latest \
    --browser chromium --user-data-dir ~/.claude/browser-profile
npx @playwright/mcp@latest install-browser chrome-for-testing
```

- `-s user` = platí ve všech projektech; zapíše se do `~/.claude.json`, ne do repa.
- `--browser chromium` jede na bundlovaném Chromiu — výchozí kanál `chrome` by chtěl
  systémový Google Chrome.
- **`--user-data-dir` je to, díky čemu přihlášení vydrží.** Bez něj si Playwright MCP
  odvozuje profil z pracovní složky (`mcp-<kanál>-<hash cwd>` v cache), takže každý
  projekt v Hubu dostane vlastní prohlížeč — přihlásíš se do Googlu, přepneš tab
  a jsi zase odhlášený. Připnutý profil je jeden pro všechny projekty.
- Druhý příkaz je nutný: verze prohlížeče se váže na verzi MCP serveru, jinak první
  `browser_navigate` vrátí „Browser chrome-for-testing is not installed". Stahuje
  ~115 MB do `~/.cache/ms-playwright` (jen co chybí).
- Nástroje se pak jmenují `mcp__playwright__*` a naběhnou po restartu session.
- Bez vyskakujícího okna: přidat za `--browser chromium` ještě `--headless`.
- Snapshoty stránek si server ukládá do `.playwright-mcp/` v aktuální složce —
  hodí se do `.gitignore`.

### Profil prohlížeče

`~/.claude/browser-profile` je obyčejný profil Chromia: cookies, přihlášení, uložená
hesla. Záměrně **není v cache** — úklid disku by tichým smazáním odhlásil všechno.

```bash
python3 ~/.claude/tools/playwright_profile.py --path            # kde profil je
python3 ~/.claude/tools/playwright_profile.py --prune           # zahodit staré profily
```

Instalačka profil založí sama a přihlášení do něj **převezme** z toho nejpoužívanějšího
ze starých profilů po jednotlivých složkách (pozná ho podle přihlášeného účtu Google),
takže se nikdo nemusí přihlašovat znovu. Když nad starým profilem zrovna běží prohlížeč,
řekne to a nechá ho být — zavři ho a dožeň přenos jedním příkazem:

```bash
python3 ~/.claude/tools/playwright_profile.py            # přenese přihlášení
```

Přeskočí to, jakmile v novém profilu nějaký účet Google je; nepřihlášený profil
předtím odloží stranou (`browser-profile.backup-<datum>`), nikdy nemaže.
`--prune` zahodí staré profily po složkách; klidně to jsou stovky MB.

Daň za společný profil: **jeden profil = jeden běžící prohlížeč.** Když si o něj řeknou
dva taby naráz, druhý dostane „Browser is already in use for …" — buď zavřít ten první
(`browser_close`), nebo tomu druhému přidat `--isolated`.

Stav registrace ukáže `python3 ~/.claude/claude-hub.py --doctor` na řádku
`prohlížeč (MCP)`.

Odebrání: `claude mcp remove playwright -s user`.
[Dokumentace](https://playwright.dev/docs/getting-started-mcp)

## Napojení na cizí služby (MCP)

**Nastavení → Napojení** ukáže všechno, na co Claude Code dosáhne, a u každého
jestli to opravdu odpovídá:

- **konektory z účtu** (`claude.ai …`) — ty na disku v žádném souboru nejsou,
  ví o nich jen Claude Code sám,
- **servery ze stroje** — `~/.claude.json`, user scope, platí ve všech projektech,
- **servery z projektu** — `.mcp.json` ve složce projektu; platí jen tam, tak
  jsou v seznamu označené.

Zdrojem pravdy je `claude mcp list`, který každý server rovnou osloví — proto to
pár sekund trvá a proto se pozná i registrace, která sice existuje, ale vypršelo
jí přihlášení. Totéž vypíše i `--doctor`:

```bash
python3 ~/.claude/claude-hub.py --doctor
#   napojení (MCP)       9 z 13 připojeno, 4x chce přihlásit
#                        + figma — připojeno
#                        ! claude.ai Gmail — chce přihlásit
```

### Clockify

Výkazy času, projekty a spuštěné stopky přímo z Claude Code. V nastavení stačí
vložit API klíč (Clockify → foto profilu → Preferences → Advanced → Manage API
keys → Generate New), ručně to je jeden příkaz:

```bash
claude mcp add clockify https://api.clockify.me/mcp-server/mcp \
    -s user --transport http --header "x-api-key: <klíč>"
```

Klíč si zapíše Claude Code do `~/.claude.json` — hub ho nikam neukládá a do logu
se nedostane. Instalačka ho vezme i z proměnné `CLOCKIFY_API_KEY`, takže projde
i běh s `--yes`.

## Přihlášení vlastním účtem

Aplikace **žádné přihlašovací údaje neobsahuje ani nesdílí** — každý si pustí Claude Code
pod svým účtem:

1. Otevři v Hubu libovolný projekt (nebo tab **shell** a napiš `claude`).
2. V Claude Code napiš `/login` a projdi přihlášením v prohlížeči.
3. Token se uloží do `~/.claude/.credentials.json` na tvém počítači — do repa nepatří
   a je v `.gitignore`.

## Záloha paměti

Vault je obyčejná složka s markdownem, takže „napojení na cloud" znamená jedinou
věc: ať leží uvnitř složky, kterou už něco synchronizuje. Nastavení nabídne, co
na stroji najde:

| Volba | Kdy dává smysl |
|---|---|
| **privátní repo na GitHubu** | funguje bez doinstalování čehokoli, a na Linuxu je to jediná spolehlivá cesta — OneDrive tam oficiálního klienta nemá; po každém sezení se změny pošlou samy |
| **složka v cloudu** | OneDrive, Dropbox, Nextcloud, pCloud, MEGA, Syncthing, iCloud — co je na disku, to se nabídne |
| **vlastní složka** | když máš sync jinde |

Přesun opravuje i symlink `~/.claude/projects/<…>/memory`; bez toho by paměť
po přesunu oslepla.

## Aktualizace

**Aktualizovat a načíst znovu nejsou totéž.** ⟳ v hlavičce jen přečte projekty
a paměť. Aktualizace mění samotnou aplikaci a bydlí v ⚙ → *Aktualizace aplikace*:
zjistí, jestli je na GitHubu novější vydání, a když ano, stáhne ho a přeinstaluje.

Verze se čte **ze souboru nainstalované kopie**, ne z modulu v paměti — jinak
by aplikace po aktualizaci hlásila pořád tu starou. Porovnává se s poslední
značkou v repu po složkách, ne jako text, takže `1.10.0` je novější než `1.9.0`.
Samotná aktualizace běží na serveru na pozadí a stránka se na stav ptá, takže
ji přežije i reload okna. Aktualizace
funguje i bez klonu: zdroj si stáhne do `~/.claude/hub-src`.

Z příkazové řádky je to pořád ten samý jeden řádek:

```bash
curl -fsSL https://raw.githubusercontent.com/jurapascal/claude-code-hub/main/get.sh | bash
```

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
get.sh                    jednořádková instalace (curl … | bash) — stáhne repo a spustí install.sh
get.ps1                   totéž pro Windows (irm … | iex)
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
hub/static/ime.js         vstup s diakritikou — composition events místo xterm.js
hub/static/clipboard.js   schránka přes server (WebKitGTK stránku k ní nepustí)
hub/static/onboarding.js  průvodce prvním spuštěním
hub/static/settings.js    nastavení po sekcích (vzhled, projekty, paměť, napojení, aktualizace, logy)
hub/static/composer.js    bublina místo vstupního řádku (text, model, slash příkazy, přílohy, režimy)
hub/static/stats.js       statistiky používání
hub/stats.py              počítání statistik z ~/.claude (přírůstkově, s mezipamětí)
hooks/save-session.py     Stop hook — uloží stav projektů do session-state.md
hooks/session-start.py    SessionStart hook — kategorie skillů z vaultu + stav minulé session
tools/settings_merge.py   přidá hooky (a volitelně bypass) do settings.json, se zálohou
tools/windows-check.ps1   kontrola na Windows: odkazy, ConPTY, schránka, složka paměti
tools/make-icons.py       ze značky vyrobí .png, .ico i favicony (jediná cesta, jak vznikají)
skills/<jméno>/SKILL.md   šablony slash příkazů ({{MEMORY_DIR}} apod. doplní instalátor)
legacy/claude-hub-gtk.py  původní GTK 3 + VTE verze (Linux only, už se neinstaluje)
hub-config.example.json   vzor konfigurace
settings.example.json     vzor zapojení hooků (bez jakýchkoli klíčů)
assets/hub-mark.svg       značka — zdroj všech ikon (tools/make-icons.py)
assets/hub-wordmark.svg   logo se jménem (řídí se motivem čtenáře)
assets/claude-code.png    ikona okna a nabídky aplikací na Linuxu
assets/claude-code.ico    ikona zástupců na Windows (16–64 px jako BMP, 128/256 jako PNG)
hub/static/favicon.ico    ikona okna na Windows — bere se z favicony, ne ze zástupce
assets/vault/MEMORY.md    rozcestník paměti pro nově založený vault
```

## Když něco nehraje

⚙ → **Logy**: co se v aplikaci dělo — starty, otevřené taby, běhy na pozadí,
chyby ze serveru i ze stránky (obojí končí ve stejném souboru, aby se problém
nehledal na dvou místech). Tlačítko **Zkopírovat hlášení** dá do schránky
prostředí i posledních 300 řádků logu; **Uložit hlášení** z toho udělá soubor.
Aplikace nikam nic sama neposílá.

Soubor leží v `~/.claude/hub.log` a po megabajtu se odloží stranou
(`hub.log.1`), takže neroste donekonečna.

## Bezpečnost

Hub umí spouštět shell, takže stojí za to vědět, čím je to ohraničené: server
poslouchá jen na `127.0.0.1`, na náhodném portu a na token. Co revize našla a co
se s tím udělalo — hlavně spuštění cizího příkazu přes jméno složky — je
v [SECURITY.md](SECURITY.md).

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
