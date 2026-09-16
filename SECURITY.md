# Bezpečnost

Hub je **místní aplikace, která umí spouštět shell**. Tenhle soubor říká, čím
je to ohraničené a co se v revizi našlo a opravilo — ať je vidět, na čem to
stojí, a ať je z čeho vyjít při další změně.

## Čím je server ohraničený

| | |
|---|---|
| **Poslouchá jen na `127.0.0.1`** | na náhodném portu, nikdy na `0.0.0.0` (o telefonu níž) |
| **Token na každý požadavek** | 24 bajtů z `secrets.token_urlsafe`, porovnává se `compare_digest`; stránka ho dostane v URL při startu |
| **Websocket navíc kontroluje `Origin`** | když hlavičku pošle prohlížeč, musí sedět na adresu, na kterou listener odpovídá |
| **Nic se neposílá ven** | jediné spojení do světa je kontrola verze na GitHubu a stažení aktualizace |

Token je celá obrana: bez něj vrací každý endpoint 403. Cizí stránka v prohlížeči
na port dosáhne, ale token neuhodne a odpověď si kvůli CORS stejně nepřečte.

## Přihlášení na server (brána)

| | |
|---|---|
| **Tři pokusy, pak hodina** | heslo, kód z aplikace i současné heslo při změně se počítají dohromady; po třetím neúspěchu na účet z téže adresy je přihlášení hodinu zablokované — i se správným heslem |
| **Pojistky proti rozesílání** | 10 neúspěchů z jedné adresy na různé účty zablokuje adresu, 10 na jeden účet z různých adres zablokuje účet |
| **Zámek přežije restart** | leží v databázi účtů (`login_fails`), ne v paměti brány — noční aktualizace ho nezruší |
| **Kolegové za jednou IP se nezamknou** | přísný limit je na účet + adresu; kancelář za jednou veřejnou IP tak nezablokuje jeden člověk |
| **Heslo i token jen jako otisk** | scrypt a SHA-256; povinné dvoufázové ověření |
| **HSTS** | `max-age=31536000; includeSubDomains`, takže prohlížeč na http nesáhne ani napoprvé; http vrací 301 na https |

Co to **neřeší**: kdo zná e-mail, může desíti špatnými pokusy z různých adres
účet na hodinu zablokovat. Je to vědomá cena za to, že se heslo nedá hádat
donekonečna; správce zámek zruší `claude-hub-admin zamky odemknout <e-mail>`.

## Prostory na serveru: čím jsou od sebe oddělené

Každý prostor běží v **bwrapu**, kde je připojený **jen jeho vlastní domov**.
Cizí domovy v sandboxu neexistují — ne že by byly zakázané, ony tam prostě
nejsou. Ověřeno zevnitř sandboxu: `ls /home/hub/users` ukáže jediný řádek,
ten vlastní.

| | |
|---|---|
| **Vlastní domov** | jediné zapisovatelné místo, na stejné cestě jako venku |
| **Zdroj hubu a firemní Obsidian** | jen ke čtení; do firemního zapisuje brána po potvrzení, ne session |
| **Sdílené vaulty** | připojí se **jen ty, kde je člověk členem** (`shared.vaults_for`) — nesdílené se neváže vůbec |
| **MCP napojení** | žijí v `~/.claude.json` v domově; na cizí napojení ani jeho token se nedá dosáhnout |
| **Domovy mají 0700** | prostory běží pod jedním systémovým účtem `hub`, takže práva je nedělí — tohle je druhá vrstva pro případ, že by sandbox někdo obešel |
| **`accounts.db` má 0600** | otisky hesel a tajemství pro dvoufázové ověření; nadřazená složka je navíc 750 |

**Na co to nestačí:** izolace stojí na bwrapu. Všechno běží pod týmž systémovým
účtem, takže kdo by sandbox obešel, dostane se na všechny prostory i na
databázi účtů. Práva 0700 to ztěžují, ale nenahradí oddělené systémové účty.
Disk serveru navíc **není šifrovaný** — kdo získá snapshot disku, přečte si
domovy i databázi. Hesla jsou v ní jen jako otisk (scrypt).

## Přístup z telefonu

Zapíná se v nastavení a dokud se nezapne, žádný druhý listener nevzniká.
Když se zapne, platí navíc:

| | |
|---|---|
| **Nikdy na `0.0.0.0`** | buď `127.0.0.1` a před tím `tailscale serve` (tailscaled drží TLS), nebo přímo adresa `100.x` z tailnetu. Mimo tailnet žádný listener není. |
| **Vlastní dlouhodobý token** | 24 bajtů z `secrets.token_urlsafe` v `hub-config.json`, oddělený od tokenu okna. Tlačítko „Odpojit telefon" ho vymění a spárované telefony odhlásí. |
| **Token v cookie** | `HttpOnly`, `SameSite=Lax`, `Secure` za https — aby se QR kód skenoval jen poprvé. Nastaví se, jen když token dorazil v URL, tedy po naskenování kódu. |
| **`Origin` z cizí stránky neprojde** | povolené jsou adresy, na kterých listener běží, plus vlastní `Host` požadavku |
| **Odpárovaný telefon dostane vysvětlení** | místo holého 403 stránka „naskenuj QR znovu"; aplikace pro Android se na ni rovnou přepne do párování |

Za tenhle kus zodpovídá Tailscale: kdo není v tailnetu, nemá se kam připojit.
Token je druhá vrstva pro případ, že by se do tailnetu dostal někdo cizí.

## Claude ze serveru na počítači

Když je appka přihlášená k serveru (bráně), může Claude z prostoru na serveru
sahat na tenhle počítač (`hub/pocitac.py`). To je vědomě díra ven, takže:

| | |
|---|---|
| **Vypnuto, dokud se nezapne** | volí se na počítači: vypnuto / jen čtení / plný přístup. Dokud je vypnuto, hub se na úkoly vůbec neptá. |
| **Spojení staví počítač** | dlouhé dotazy po HTTPS s tokenem zařízení; na počítači se neotevírá žádný port |
| **Úroveň se ověřuje u každého úkolu** | podle toho, co je v `hub-config.json` teď, ne podle toho, co tvrdí brána |
| **Server ji změnit neumí** | stránka prostoru zná adresu i token hubu na počítači (`#local=`, cesta zpátky). POST na `/api` proto musí mít `Origin` stránky hubu — z prostoru neprojde. |
| **Přihlášení appky a Claude Code se nečte** | `hub-config.json` (token zařízení) a `.credentials.json` odmítne čtení, hledání, stažení i zápis. S tokenem zařízení by si Claude v prostoru sám potvrdil, co potvrzuje člověk (firemní Obsidian). |
| **Každý úkol do logu** | `hub.log` a posledních pár v ⚙ → Účet |
| **Úkol na později spouští počítač** | zadání, které Claude z prostoru nechal vypnutému počítači, otevře tab s Claude Code sám jen s plným přístupem — ten už stejně dovoluje příkazy. S přístupem jen ke čtení čeká na *Spustit* v okně appky (POST s `Origin` hubu, prostor ho nespustí). Tab běží v režimu oprávnění, který má Claude Code nastavený, a o souhlas se ptá jako jindy. |

Co to **neřeší**: s plným přístupem Claude spouští příkazy, a příkaz si
soubor s přihlášením přečte i tak. Plný přístup je proto stejná důvěra jako
Claude Code spuštěný přímo na tom počítači — a na počítač tak dosáhne i ten,
kdo spravuje server. Appka to říká u volby.

## Co revize našla a co se s tím udělalo

### Cizí domov přes symbolický odkaz na bráně (opraveno ve 2.14)

Brána zapisovala do domova uživatele (`settings.json`, `CLAUDE.md`,
`.claude.json`, `hub-config.json`) obyčejným `open()` přes pomocný
`….hub-tmp`. Session v prostoru si mohla ten soubor předem nahradit odkazem na
registr domovů `/home/hub/users/.domovy.json` a do `settings.json` napsat
`{"jmeno-kolegy": <své číslo účtu>}`. Brána pak registr přepsala a po restartu
prostoru dostala session domov kolegy — jeho Obsidian, projekty i přihlášení.
Odkazem šlo i přečíst cizí `CLAUDE.md` nebo `settings.json`. Ověřeno na kódu
2.13.0.

Oprava: `gateway/safefs.py` — složky po jedné s `O_NOFOLLOW`, soubory přes
`dir_fd`, zápis přes náhodný dočasný soubor s `O_EXCL` a `rename`, čtení bez
následování odkazů a bez blokování na pojmenované rouře. `~/.claude.json`
předvyplňuje hub uvnitř sandboxu, ne brána. Útoky (odkaz na `*.hub-tmp`,
na `CLAUDE.md`, celé `~/.claude` jako odkaz, roura místo `hub-config.json`,
přejmenování domova) jsou vyzkoušené a neprojdou.

### Spuštění cizího příkazu přes jméno složky (opraveno)

Příkaz pro nový tab se skládal jako text a cesta se do něj vkládala v dvojitých
uvozovkách. Ty ale nezastaví `$(…)` ani zpětné apostrofy — takže složka
pojmenovaná

```
projekt$(rm -rf ~)x
```

by svůj obsah spustila ve chvíli, kdy na ni člověk v panelu klikne. Takové jméno
přitom nevznikne jen naschvál: stačí naklonovat cizí repo nebo rozbalit archiv.

Cesty teď procházejí `sh_quote()`, tedy jednoduchými uvozovkami, ve kterých má
význam jediný znak — apostrof sám. Ověřeno na `$( )`, zpětných apostrofech,
středníku i uvozovce; a zvlášť na tom, že běžná cesta i cesta s apostrofem
a mezerou se pořád otevře.

### Podstrčení přepínače gitu (opraveno)

Adresa repa pro stažení paměti šla do `git clone` tak, jak přišla. Řetězec
začínající pomlčkou ale git nevezme jako adresu, nýbrž jako přepínač —
a `--upload-pack=…` umí spustit cizí příkaz. Adresa teď musí projít sítem
(`REPO_RE`), nesmí začínat pomlčkou a do příkazu jde až za `--`. Totéž platí
pro jméno nového repa předávané `gh`.

### Otevírání odkazů z výpisu terminálu (omezeno)

Odkaz ve výpisu se dá kliknutím otevřít systémovým handlerem, a výpis může
pocházet z cizího repa. `file://…​.desktop` by se pod tím handlerem klidně
spustil. Pouštějí se proto jen `http://`, `https://`, `mailto:` a `obsidian://`;
místní cesta se otevře, jen když opravdu existuje.

### Zápis briefingu (omezeno)

Briefing se ukládá do `CLAUDE.md` projektu. Zapisovat kamkoli na disk není
potřeba, takže cíl musí být složka, kterou hub zná jako projekt. Do cizího
obsahu se nesahá — blok je ohraničený značkami a jen se vyměňuje.

### Drobnosti

- **Statika**: shoda cesty se ověřuje přes `commonpath`, ne `startswith` —
  ten by pustil i sourozence jménem `static-cokoliv`.
- **Rozbalení aktualizace**: `extractall` s filtrem `data`, se starším Pythonem
  ručně; archiv nesmí zapsat mimo cílovou složku ani obsahovat odkazy.
- **Nahrané soubory**: jméno se očistí, přípona musí být z bílé listiny, strop
  25 MB, cíl vždy `~/.claude/hub-images`.
- **`hub-projects.json`** se zapisuje s právy `600` — briefingy bývají
  o klientech.

## Co hub záměrně smí

Držitel tokenu je uživatel sám, takže hub úmyslně umí věci, které by jinde byly
podezřelé: spustit shell, číst a zapisovat schránku, vypsat libovolnou složku
(výběr složky), přesunout vault, stáhnout a nainstalovat vlastní aktualizaci.
Ohraničené je to tím, kdo se k tokenu dostane — ne tím, co s ním jde dělat.

## Co v repu není a nikdy nebude

Žádná data, žádné projekty, žádná paměť, žádné přihlašovací údaje. Osobní věci
ze `~/.claude` (`settings.json`, `.credentials.json`, `.ftp-deploy.json`) jsou
v `.gitignore` a instalačka je nikdy nekopíruje ven.

## Když něco najdeš

Napiš do issues repa. Když jde o něco, co by se nemělo objevit veřejně, pošli to
rovnou majiteli repa.
