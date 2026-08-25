# Bezpečnost

Hub je **místní aplikace, která umí spouštět shell**. Tenhle soubor říká, čím
je to ohraničené a co se v revizi našlo a opravilo — ať je vidět, na čem to
stojí, a ať je z čeho vyjít při další změně.

## Čím je server ohraničený

| | |
|---|---|
| **Poslouchá jen na `127.0.0.1`** | na náhodném portu, nikdy na `0.0.0.0` |
| **Token na každý požadavek** | 24 bajtů z `secrets.token_urlsafe`, porovnává se `compare_digest`; stránka ho dostane v URL při startu |
| **Websocket navíc kontroluje `Origin`** | když hlavičku pošle prohlížeč, musí sedět na náš port |
| **Nic se neposílá ven** | jediné spojení do světa je kontrola verze na GitHubu a stažení aktualizace |

Token je celá obrana: bez něj vrací každý endpoint 403. Cizí stránka v prohlížeči
na port dosáhne, ale token neuhodne a odpověď si kvůli CORS stejně nepřečte.

## Co revize našla a co se s tím udělalo

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
