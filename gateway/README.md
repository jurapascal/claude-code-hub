# Brána — hub pro víc lidí

Hub v `hub/` je jednouživatelský: běží na tvém stroji, poslouchá na loopbacku
a telefon se k němu dostane přes Tailscale. **Brána je něco jiného**: server,
na kterém má každý z týmu svůj účet, svůj Obsidian a svou session Claude Code.
Appka Claude Code Hub i prohlížeč na telefonu jsou jen okno do toho prostoru.

```
[appka / prohlížeč]  ──HTTPS──>  [nginx]  ──>  [brána]
   adresa → ověřit                               ├── účty a tokeny     accounts.py
   e-mail a heslo                                ├── izolace session   isolation.py
   tvůj prostor                                  ├── prostor uživatele workspace.py
                                                 ├── přihlášení + proxy server.py
                                                 └── správa účtů (CLI) admin.py
```

## Stav

| díl | co dělá | stav |
|---|---|---|
| `accounts.py` | účty, role, hesla (scrypt), tokeny pro zařízení | hotovo |
| `isolation.py` | čím se session pouští, aby neviděla na cizí | hotovo |
| `workspace.py` | domov uživatele, jeho vault, konfigurace jeho hubu | hotovo |
| `server.py` | přihlášení, HTTP + websocket proxy do instance hubu | hotovo |
| `admin.py` | správa účtů z příkazové řádky | hotovo |
| appka | výběr počítač / server, ověření adresy, přihlášení | hotovo (2.2.0) |

Účty se zakládají jen z příkazové řádky na serveru — webová správa schválně není:

    python3 -m gateway.admin add jmeno@firma.cz --name "Jméno" --role user
    python3 -m gateway.admin sessions            # které prostory běží a kolik berou
    python3 -m gateway.admin stop jmeno@firma.cz # zastavit prostor i s Claude Code
    python3 -m gateway.admin remove jmeno@firma.cz

Smazaný účet má složku odloženou jako `users/_smazany-u<id>-<datum>`. Nechat ji
na místě nejde: SQLite po smazání dá dalšímu účtu stejné číslo a ten by zdědil
cizí paměť, projekty i přihlášení.

## Nový server

Server se staví skriptem, ne ručně — na čistém Ubuntu 24.04 jako root:

    curl -fsSLO https://raw.githubusercontent.com/jurapascal/claude-code-hub/main/gateway/install.sh
    bash install.sh --domain test.alba-rosa.cz --email ja@firma.cz --admin jmeno@firma.cz

Skript doinstaluje balíčky, Node a Claude Code (do `/usr` — sandbox jinam
nevidí), založí uživatele `hub`, povolí bwrapu user namespaces přes AppArmor,
pustí bránu jako uživatelskou službu, nastaví nginx s certifikátem, firewall,
fail2ban, swap a bezpečnostní aktualizace. Když doména na server ještě
nemíří, certifikát přeskočí — stačí nastavit DNS a pustit ho znovu.

Opakovaný běh je bezpečný: co je hotové, nechá být, stáhne novější hub a bránu
restartuje, jen když se něco změnilo (`--no-restart` ji nechá běžet). Účty se
pak spravují přes `claude-hub-admin` (obal nad `python3 -m gateway.admin` se
správnými cestami).

**Aktualizace jsou automatické a nikomu nic neutnou.** `claude-hub-update.timer`
se každou hodinu podívá, jestli je na GitHubu novější vydání. Když ano:

1. **Hned ho připraví** — vybalí značku do `/opt/claude-code-hub-verze/<verze>`.
   Každý prostor jede ze své složky (brána ji do sandboxu přiváže na místo
   `/opt/claude-code-hub`), takže běžícím prostorům se nic nevymění pod rukama.
2. **Lidem v prostoru to nabídne**: „Na serveru je nová verze · Aktualizovat ·
   Později". Aktualizovat uloží taby i rozepsané zprávy, brána prostor
   restartuje už na nové verzi a taby se vrátí, Claude v nich pokračuje
   v konverzaci. Když Claude zrovna pracuje, počká se, až doběhne. Později =
   připomene se za hodinu (a v hlavičce svítí „Nová verze").
3. **Bránu samotnou přepne**, až nepoběží žádný prostor: přepne zdroj, pustí
   `install.sh` se stejnými parametry (`/etc/claude-hub/install.conf`)
   a restartuje ji.

Při každém běhu se taky srovná **Claude Code** v `/usr` s nejnovější verzí na
npm (`npm install -g @anthropic-ai/claude-code@<verze>`). Prostory ho mají jen
ke čtení, takže se sám neaktualizuje nikdy — bez toho zůstal na verzi
z instalace a v prostorech chyběly nové modely. Běžící Claude jede dál, nová
konverzace začne na nové verzi.

Starší verze se mažou (nechávají se tři poslední a každá, ze které ještě jede
prostor). Výsledek je v `/etc/claude-hub/update.json` a v prostoru v Nastavení
→ Aktualizace.

**Claude přes klíč API.** `claude-hub-admin apikey set` uloží společný klíč
(ověří ho u Anthropicu) a prostory na `central` ho při startu dostanou jako
`ANTHROPIC_API_KEY`, předschválený, takže se nikdo nepřihlašuje. Platí se podle
spotřeby — v Anthropic Console nastav měsíční limit.

**Claude bez klíče API — na předplatném.** Klíč API není potřeba: kdo nemá
klíč ani přihlášení, tomu appka na počítači při vstupu do prostoru sama připojí
**jeho vlastní předplatné** (`claude setup-token` u něj na počítači, v prohlížeči
jen Authorize) a pošle token na `/gw/claude`. Brána ho uloží do
`claude-predplatne/<id účtu>.json` (0600) a prostoru ho dá jako
`CLAUDE_CODE_OAUTH_TOKEN` — s přednost před klíčem API. Nečinný prostor
restartuje hned, jinak hub nabídne restart. Přehled a ruční správa:

    claude-hub-admin predplatne status              # kdo jede na čem, kdy token vyprší
    claude-hub-admin predplatne set jmeno@firma.cz  # vložit token ručně (ze setup-token)
    claude-hub-admin predplatne remove jmeno@firma.cz

Jedno předplatné patří jednomu člověku; sdílet ho mezi účty nejde (podmínky
Anthropicu).

`apikey set --user <e-mail>` dá jednomu účtu **vlastní klíč**: má pak v Console
svůj strop, ve spotřebě je poznat a kdo si klíč v prostoru přečte z prostředí,
přečte jen ten svůj. Kdo vlastní nemá, jede na společném; `apikey status`
vypíše, kdo je na čem, a `apikey remove --user <e-mail>` ho vrátí na společný.
`auth <e-mail> own` je vlastní účet Claude, `auth --vsechny central` přepne
naráz všechny.

**Služby v prostorech.** V Nastavení → Napojení má každý karty Freelo, Canva,
Ecomail a Google, pod nimi svoje účty (i víc u jedné služby) a u každého
Přihlásit. Freelo, Canva a Ecomail jsou oficiální MCP servery s OAuth, které se
zaregistrují samy — nic se nenastavuje. Pro Google jednou založ klienta OAuth
v Google Cloudu (typ Desktopová aplikace, zapnout API Gmail, Disk, Kalendář,
Dokumenty, Tabulky, Prezentace, Formuláře, Úkoly, Kontakty a aplikaci
**zveřejnit** — v testovacím režimu Google přihlášení po 7 dnech zruší) a ulož
ho: `claude-hub-admin google set`. Google běží přes workspace-mcp na `uv`,
který doinstaluje `install.sh`.

## Přihlášení a dvoufázové ověření

Po hesle brána chce ještě šesticiferný kód z aplikace v mobilu (TOTP,
`gateway/totp.py` — Google Authenticator, Microsoft Authenticator, Authy…).
Povinné je pro všechny (`HUB_GW_REQUIRE_2FA=0` ho vypne).

- **První přihlášení:** po správném heslu stránka ukáže QR kód a ruční klíč;
  ověřování se zapne, až ho člověk potvrdí prvním kódem. Pak ukáže osm
  **záložních kódů** pro ztracený telefon (každý platí jednou, v databázi jen
  otisk). Appka na počítači má tytéž kroky — QR si kreslí sama z odkazu.
- **Tokeny:** platí jen ty vydané po druhém kroku (`tokens.mfa`). Přihlášení
  z doby před 2.7.0 skončila — každý se jednou přihlásí znovu.
- **Hádání:** po **třech** neúspěších (heslo, kód z aplikace i současné heslo
  při změně — počítají se dohromady) se přihlášení k účtu z téže adresy
  (`X-Real-IP`) na **hodinu** zablokuje; hláška předem řekne, kolik pokusů
  zbývá. Povedené přihlášení překlepy před ním odpustí. Pojistky proti
  rozesílání: 10 neúspěchů z jedné adresy na různé účty, nebo 10 na jeden účet
  z různých adres, zablokuje taky na hodinu tu adresu, resp. ten účet. Přísný
  limit je na účet + adresu, ne na adresu samotnou — z kanceláře za jednou
  veřejnou IP by jinak tři překlepy jednoho zamkly všechny. Zámky leží
  v databázi účtů (`login_fails`), takže je restart brány nezruší. Lístek na
  druhý krok platí 5 minut, stejný kód z aplikace podruhé neprojde.
- **Zámky:** `claude-hub-admin zamky` vypíše, co je zablokované;
  `claude-hub-admin zamky odemknout <e-mail|ip>` zámek zruší. Limity jdou změnit
  v prostředí služby: `HUB_GW_LOGIN_TRIES` (3), `HUB_GW_LOGIN_LOCK` (3600 s),
  `HUB_GW_LOGIN_WIDE` (10).
- **Nastavení → Účet** v prostoru: stav ověření, nové záložní kódy (s kódem
  z aplikace) a změna hesla (se současným heslem; odhlásí ostatní zařízení).
- **Ztracený telefon i kódy:** `claude-hub-admin 2fa reset <e-mail>` — ověřování
  se zruší a při příštím přihlášení se nastaví znovu. `claude-hub-admin 2fa
  status` ukáže, kdo ho má.

## Firemní Obsidian

Vedle osobního trezoru v každém prostoru je jeden společný trezor pro všechny:
`/home/hub/firma/Firemní Brain` (`HUB_GW_COMPANY_DIR`, `HUB_GW_COMPANY_VAULT`).

- **Čtení:** v každém prostoru je svázaný jen ke čtení (bwrap `--ro-bind`),
  Claude Code ho má v `permissions.additionalDirectories` a pokyny k němu
  v `~/.claude/CLAUDE.md` (blok mezi `<!-- claude-hub:firma -->`, zbytek
  souboru patří uživateli). V hubu je sekce **Firemní Obsidian** s náhledem.
- **Dva taby:** v liště hubu je **Claude Code osobní** a **Claude Code firemní**
  (druhý jen tam, kde firemní trezor je). Firemní tab se pouští
  s `HUB_VAULT=firma` a pokynem jen pro to sezení
  (`--append-system-prompt`, `core.firma_tab_prompt`): Claude v něm čte
  z firemního trezoru a zápis posílá rovnou — `tools/firma.py` v tomhle režimu
  nechce `--potvrzeno`, označí návrh `auto` a hub ho hned nahraje přes
  `/gw/firma/publish` bez karty, jen s hláškou dole v okně. Osobní tab zůstává
  jako dřív.
- **Dvojí potvrzení (osobní tab):** Claude se nejdřív v chatu zeptá — shrne, co
  a kam nahraje a jestli něco přepíše — a pokračuje až po výslovném „ano".
  Nástroj bez přepínače `--potvrzeno` návrh nevytvoří a Claudovi napíše, ať se
  zeptá. Druhé potvrzení je karta v hubu.
- **Co se ani ve firemním tabu nenahraje samo:** text, který vypadá jako heslo,
  klíč nebo token (`core._SECRET`), hub tiše nenahraje — ukáže kartu a řekne
  proč. Pokyn pro firemní tab navíc Claudovi zakazuje nosit do společného
  trezoru osobní poznámky uživatele a nastavení napojení (MCP, Google, účty).
- **Zápis:** Claude poznámku jen připraví — `python3 tools/firma.py navrh
  CÍL [SOUBOR | -] --potvrzeno` ji uloží do `~/.firma/ke-schvaleni/`. Hub ukáže kartu
  s náhledem a cílovou cestou; **Nahrát** pošle prohlížeč na
  `/gw/firma/publish` a zapíše brána (`workspace.publish_company`).
  Existující poznámku přepíše jen tlačítko **Přepsat**. Totéž dělá editor
  v hubu: **Uložit** ve firemním (i sdíleném) Obsidianu poznámku nezapíše,
  jen připraví stejný návrh (`core.vault_proposal`) a čeká na kartu.
- **Proč to Claude sám potvrdit nemůže:** zápis vyžaduje přihlašovací cookie
  brány, stejný původ stránky a hlavičku `X-Hub-Firma`. Cookie brány se do
  prostoru nepřeposílá (hub se ověřuje vlastním tokenem) a firemní trezor je
  v sandboxu jen ke čtení. Návrh z domova se čte bez následování odkazů
  a cílová cesta nesmí ven z trezoru.
- Kdo co nahrál: `/home/hub/firma/nahrano.jsonl` (mimo trezor).

## Role a přístup k firemnímu Obsidianu

Účet je `admin`, nebo člen (`user`) — `claude-hub-admin role <e-mail> admin|user`.
Admin má do firemního Obsidianu přístup vždycky (čte i zapisuje) a spravuje,
kdo další ho vidí: ve firemním Obsidianu v hubu tlačítko **Přístupy** — u každého
člověka **Nevidí / Čte / Čte i zapisuje** (brána `/gw/firma/pristupy`, jen admin,
jen ze stránky hubu). Z příkazové řádky totéž:

    claude-hub-admin firma                       # kdo co smí
    claude-hub-admin firma jmeno@firma.cz read   # none / read / write

- **Nevidí:** trezor se do prostoru vůbec nepřiváže (bwrap), z CLAUDE.md zmizí
  pokyny k němu a hub ho neukáže. Běžící prostor bez připojeného prohlížeče se
  zastaví hned, ostatní při dalším startu; napojení z appky Claude ho ztratí hned.
- **Čte:** jen ke čtení, návrhy (`tools/firma.py`, editor, karta Nahrát) brána
  odmítne.
- **Čte i zapisuje:** jako dřív — zápis přes kartu Nahrát nebo firemní tab.
- Bez záznamu platí `HUB_GW_COMPANY_DEFAULT` (výchozí `write`, tedy stav před
  správou přístupů). Změny se zapisují do `firma/pristupy.jsonl`.

## Kdo vidí kterou firemní poznámku

U každé poznámky firemního Obsidianu jde vybrat, kdo ji vidí
(`gateway/poznamky.py`). Bez nastavení ji vidí každý, kdo vidí firemní
Obsidian; s vybranými lidmi jen oni a **správci poznámek**.

- **Kdo to nastavuje:** jen správci poznámek — pevný seznam účtů, nezávislý na
  roli admin:

      claude-hub-admin poznamky spravci adam@… jiri@… petr@…   # nahradí seznam
      claude-hub-admin poznamky                                 # co je omezené a pro koho

- **V hubu:** správce má u otevřené firemní poznámky tlačítko **Vidí: …**
  (Všichni / Jen vybraní), v seznamu u omezených zámek a v grafu kroužek kolem
  puntíku; pravé tlačítko (na telefonu podržení) na puntík otevře totéž.
  Brána: `GET/POST /gw/firma/poznamky` (změna jen správce, jen ze stránky hubu).
- **Natvrdo, ne jen schované:** firemní trezor se do sandboxu přiváže celý
  ke čtení a přes každou složku, ve které je něco skrytého, se položí prázdný
  tmpfs, do kterého se zpátky přivážou jen viditelné položky (`mask_args`).
  Claude, terminál, hub ani napojení z appky Claude o skryté poznámce nevědí.
  Zápis na její cestu brána odmítne. Bez bwrap (docker) se trezor tomu, kdo
  má něco skryté, radši nepřiváže vůbec.
- **Kdy se to projeví:** při dalším startu prostoru. Kdo poznámku přestal vidět
  a zrovna nepracuje, tomu brána prostor zastaví hned; ostatním hub ve firemním
  Obsidianu ukáže „Přístupy se změnily · Restartovat prostor".
- **Složky se skrytou poznámkou** mají v prostorech každý soubor přivázaný
  zvlášť, takže nový soubor v nich se objeví až po restartu. Existující
  poznámky v nich brána přepisuje na místě, aby změnu viděli hned.
- Registr `firma/poznamky-pristupy.json` (podle id účtů a cest), záznam změn
  `firma/poznamky-pristupy.jsonl`. **Přesun poznámky mimo bránu** (git,
  Obsidian na počítači) omezení nepřenese — na nové cestě ji uvidí všichni.

## Napojení z appky Claude (MCP)

Každý si v appce Claude (claude.ai na webu, desktop, mobil) přidá vlastní
konektor: **Nastavení → Konektory → Přidat vlastní konektor**, adresa
`https://<brána>/mcp`. Při připojení se otevře přihlášení brány: e-mail, heslo,
kód z aplikace a souhlas. Claude Code: `claude mcp add --transport http hub
https://<brána>/mcp` a pak `/mcp` → přihlásit.

Claude pak v appce vidí **jen prostor toho, kdo se přihlásil**: projekty,
konverzace s Claude Code, osobní Obsidian, sdílené Obsidiany, kde je členem,
a firemní podle jeho práva; umí číst a zapisovat soubory v jeho domově
a spouštět v něm příkazy. Nástroje (`gateway/mcp.py`): `prostor`, `projekty`,
`konverzace`, `konverzace_cti`, `obsidian_seznam`, `obsidian_hledat`,
`obsidian_cti`, `obsidian_zapis`, `slozka`, `soubor_cti`, `soubor_zapis`,
`prikaz`.

Zabezpečení:

- **Přihlášení** je stejně přísné jako do hubu: heslo + povinný kód z aplikace,
  stejné zámky proti hádání, pokaždé znovu (cookie z prohlížeče se nepřebírá),
  stránky nejdou vložit do rámu, formuláře jen ze stejného původu.
- **OAuth 2.1 s PKCE (S256)** a samoregistrací klienta, ale kód se smí vrátit
  jen appce Claude (`claude.ai`/`claude.com`) nebo na smyčku vlastního počítače
  (Claude Code). Další adresy jen výslovně: `HUB_GW_MCP_REDIRECTS`. Kód platí
  minutu a jednou.
- **Tokeny** jen jako otisky: přístupový hodinu, obnovovací měsíc a při každém
  použití se vymění — znovu použitý zruší celé přihlášení. Svázané s adresou
  `/mcp` této brány. Změna hesla, blokace účtu a reset 2FA napojení zruší.
- **Izolace:** každý nástroj běží v instanci hubu toho účtu, v jeho sandboxu
  (bwrap) — cizí domov v něm vůbec není. Cesty se navíc drží v domově, příkazy
  nedostanou proměnné s klíči a tokeny. Firemní Obsidian kontroluje brána při
  každém volání a zapisuje do něj (i do sdílených) sama. Vnitřní `/api/mcp-tool`
  hubu je z prohlížeče přes proxy nedostupné.
- **Záznam:** `gateway/mcp.jsonl` (kdo, kterým nástrojem, cesty a příkazy —
  bez obsahu souborů). Napojení vypíše a zruší:

      claude-hub-admin mcp                          # kdo má napojení
      claude-hub-admin mcp zrusit jmeno@firma.cz    # zrušit (appka se přihlásí znovu)

## Sdílená napojení (MCP)

Napojení na službu (WordPress, Ecomail s klíčem, cokoli s MCP) si nastaví
jeden člověk a nasdílí ho vybraným lidem (`gateway/mcp_sdilene.py`). Klíče
drží brána — do prostorů se nedostanou nikomu, ani vlastníkovi.

- **V hubu:** Nastavení → Napojení → **Sdílená napojení**: šablona (WordPress,
  vlastní adresa, vlastní příkaz), název, klíče, s kým sdílet. Prohlížeč posílá
  tajemství rovnou bráně (`/gw/mcp-sdilene`, stejný původ + `X-Hub-Account`),
  hub v prostoru je nevidí. Po založení se napojení samo vyzkouší
  (initialize + tools/list). Vlastník mění sdílení a maže; ostatní vidí jen
  název a kdo ho nasdílel.
- **Dva druhy:** *adresa* — vzdálený MCP server přes HTTPS, brána přidá
  uložené hlavičky (`Authorization: Bearer …`); *příkaz* — třeba
  `npx -y balíček`, klíče v proměnných. Příkaz pouští brána sama v bwrap
  (systém ke čtení, domov jen `mcp-sdilene/cache/<zkratka>`, žádné domovy,
  trezory ani databáze), s `--die-with-parent`; proces na člověka a sezení
  Claude Code, nečinný se ukončí po `HUB_GW_MCP_SHARED_IDLE` (15 min), naráz
  nejvýš `HUB_GW_MCP_SHARED_PROCS` (24).
- **V prostoru:** hub při startu (a po změně v nastavení) zaregistruje za každé
  napojení stdio most `tools/sdilene_mcp.py <zkratka>` jako `sdilene-<zkratka>`
  a nepotřebné odebere. Most posílá zprávy na `/gw/mcp-sdilene/volani` —
  loopback, žeton prostoru (`X-Hub-Pocitac`), přes nginx 404. Nové napojení
  uvidí Claude Code od další konverzace.
- **Odebrání platí hned:** členství se ověřuje u každé zprávy, odebranému
  brána spojení ukončí.
- **Co to neumí:** služby, které chtějí přihlášení přes OAuth (oficiální MCP
  Ecomailu, Freelo, Canva) — ty si dál napojuje každý sám. Sdílet jde jen to,
  co jde ověřit klíčem nebo heslem.
- Registr `gateway/mcp-sdilene/registr.json` (0600, i s tajemstvími), záznam
  změn bez tajemství `zmeny.jsonl`. Přehled: `claude-hub-admin mcp-sdilene`.
  Smazání účtu ho odebere ze sdílení a jeho vlastní napojení smaže.

## Firemní skilly

Hotové postupy, ze kterých si Claude sám vybírá, leží ve firemním trezoru:
`<trezor>/skills/<kategorie>/<skill>/SKILL.md`. Tím je má celý tým z jednoho
místa a jen ke čtení — nikdo je nemá zvlášť u sebe a z prostoru je nikdo
nepřepíše.

    claude-hub-admin skills install        # stáhnout do firemního trezoru
    claude-hub-admin skills install --from /cesta/ke/skillum   # z vlastní složky
    claude-hub-admin skills update         # novější verze z gitu
    claude-hub-admin skills status         # kolik jich je, jaké kategorie, odkud

Claude o nich ví z pokynů, které brána píše do `~/.claude/CLAUDE.md` prostoru
při každém jeho startu (`workspace._company_block`): dostane cestu, seznam
kategorií a pokyn vybrat podle zadání nejvýš dva tři a přečíst je, než začne
pracovat — celý seznam skillů by sežral kontext. **Nové skilly se v prostoru
objeví po jeho dalším startu**; `claude-hub-admin stop <e-mail>` to uspíší.

Instalačka je zavádí sama (`setup_company_skills`) a při dalším běhu
aktualizuje; nahrané z vlastní složky nechá být. Zdroj je
`jurapascal/claude-brain-skills`, přebije ho `HUB_SKILLS_REPO`. Přepsat je
`skills install --force` — co tam bylo, se odloží vedle trezoru jako
`_skilly-<čas>`.

## Sdílené Obsidiany

Trezory jen pro vybrané lidi: `/home/hub/sdilene/<zkratka>` (`HUB_GW_SHARED_DIR`),
registr kdo je založil a kdo v nich je `/home/hub/sdilene/.sdilene.json`
(podle id účtů), změny v `/home/hub/sdilene/zmeny.jsonl`.

- **Kdo co může:** založit kdokoli (s aspoň jedním dalším člověkem), zapisovat
  členové, měnit členy a mazat jen zakladatel nebo správce, členové můžou
  odejít. Smazaný trezor se přesune stranou do `_smazany-<zkratka>-<čas>`.
- **Přes Clauda se dvojím potvrzením**, stejně jako firemní: pokyny v bloku
  `<!-- claude-hub:sdilene -->` v `~/.claude/CLAUDE.md`, nástroj
  `python3 tools/sdilene.py` (`lide`, `seznam`, `zalozit`, `navrh`, `clenove`,
  `odejit`, `smazat`) bez `--potvrzeno` nic nevytvoří. Návrh leží
  v `~/.firma/ke-schvaleni/` s polem `druh`, hub ukáže kartu a potvrzení jde
  přes `/gw/firma/publish` — brána znovu ověří členství a oprávnění
  (`workspace.apply_proposal` → `shared.py`).
- **Sandbox:** prostor má svázané jen ke čtení ty sdílené trezory, jejichž je
  uživatel členem při startu. Nový trezor nebo nový člen se načte po restartu
  prostoru — hub to ukáže v sekci **Sdílené Obsidiany** (`/gw/sdilene`)
  a restartuje po dotazu (`POST /gw/restart`). Odebranému členovi brána
  nečinný prostor zastaví hned; do běžícího už trezor nepřibude.
- `claude-hub-admin sdilene` vypíše trezory, zakladatele a členy. Smazání
  účtu ho ze všech trezorů odebere.

## Most na počítač uživatele

Claude v prostoru sahá i na počítač, ze kterého se k prostoru přihlásil — podrobně
pro uživatele v [README](../README.md#claude-ze-serveru-na-tvém-počítači). Na
bráně k tomu patří `gateway/pocitac.py` a tři adresy (tabulka níž):

- **Počítač se ptá sám.** Hub na počítači s tokenem zařízení drží dlouhý dotaz
  `/gw/pocitac/poll`; brána mu vrátí úkoly, jakmile nějaké jsou. Nic se na
  počítač nepřipojuje zvenku. `/gw/info` hlásí `features: ["pocitac"]`, podle
  toho appka pozná, že brána most umí.
- **Prostor volá mimo nginx.** Brána dá prostoru při startu do prostředí
  `HUB_POCITAC_URL` (loopback brány) a `HUB_POCITAC_TOKEN` (nový s každým
  startem). Hub v prostoru zaregistruje Claude Code MCP server
  `tools/pocitac_mcp.py` (`claude mcp add pocitac -s user`) a ten posílá úkoly
  na `/gw/pocitac/volani`. Požadavek s `X-Real-IP` (tedy přes nginx) dostane
  404 — žeton zvenku nic neotevře. Cizí prostor žeton nezná: každý má svoje
  prostředí.
- **Brána obsah jen přepravuje.** Fronty a čekání drží v paměti; co smí,
  rozhoduje počítač u každého úkolu (vypnuto / jen čtení / plný přístup).
  Brána přístup zná jen kvůli srozumitelné odpovědi dřív, než úkol odejde.
- **Úkoly na později** (`features: ["ukoly"]`). Když počítač připojený není,
  nechá mu Claude z prostoru zadání a přílohy (`volani`, `op: ukol-novy`). Brána
  je drží v `HUB_GW_POCITAC_UKOLY_DIR` (výchozí `<gateway>/pocitac-ukoly/<účet>/<id>`,
  0700/0600) a počítači je nabídne v odpovědi na `poll` (`ukoly`); nový úkol
  čekající dotaz probudí hned. Počítač si úkol stáhne (`/gw/pocitac/ukol`,
  `take`) a hlásí stav (`prevzato` → `spusteno` / `zahozeno`). Přílohy se smažou
  po převzetí, nevyzvednutý úkol po 30 dnech, záznam o vyřízeném po 14.
- **Limity:** soubor do 15 MB (nginx má `client_max_body_size 25m`, base64
  přidá třetinu), příkaz nejvýš 10 minut, 32 čekajících úkolů na počítač.
  Úkol na později: zadání 8 000 znaků, přílohy 15 MB a 20 souborů, 20
  nevyzvednutých úkolů na účet.
- V `docker` izolaci most nefunguje — kontejner na loopback brány nedosáhne.

## Běžící prostory

Každý prostor běží ve vlastní systemd scope pojmenované podle e-mailu —
`boucnik.jiri@gmail.com` → `claude-hub-boucnik.jiri_gmail.com.scope` (zavináč
systemd nepřijme; znaky mimo `a-z0-9.-` jdou jako `\xNN`). Do 2.5.1 se
jmenovala `claude-hub-u<id>`; `claude-hub-admin` pozná obě.

Domov má taky jméno podle e-mailu: `/home/hub/users/boucnik.jiri` (když už
ho má jiný účet, s doménou `boucnik.jiri_gmail.com`, pak s číslem účtu). Kdo má
kterou složku, drží `/home/hub/users/.domovy.json` — vedle domovů, ne v nich,
aby si přiřazení nešlo změnit ze session. Do 2.5.3 se domovy jmenovaly
`u<id>`; brána je přejmenuje při dalším startu prostoru (jen když neběží)
a přepíše starou cestu v nastavení, skillech a přepisech konverzací
i ve jménech složek projektů Claude Code. Cache (`.npm`, `.cache`) se
nepřepisují: stará cesta vede v sandboxu odkazem na nový domov. Podle scope
brána pozná, jestli prostor žije (čte `cgroup.procs`, nespouští nic), a přes ni
ho zastavuje — celý, i s Claude Code a vším, co v něm běží. Nečinný se uspí po
`HUB_GW_IDLE_SLEEP`; psaní do terminálu (websocket) se počítá jako aktivita.

Naráz smí běžet `HUB_GW_MAX_SESSIONS` prostorů — když není nastavené, podle
paměti stroje (600 MB na prostor, nejmíň 4). Při plném stropu brána uspí
jen prostor, ke kterému není připojený žádný prohlížeč a kde se aspoň
5 minut nic neděje; když takový není, nový člověk dostane „Server je právě
plný". Do 2.5.4 se uspával nejdéle nečinný bez ohledu na připojení, a s víc
lidmi než místy se prostory v kolečku navzájem shazovaly.

Proč ne `pkill` podle domova, jak to bylo do 2.2.0 — naměřeno na Ubuntu 24.04:

- **bwrap s `--unshare-pid` má vlastní init** (druhý bwrap, v namespace PID 1).
  Jádro mu SIGTERM nedoručí, protože na něj nemá obsluhu. `pkill` zabil vnější
  bwrap a zbytek i s Claude Code jel dál, jen o něm nikdo nevěděl.
- **Hub ani Claude Code uvnitř nemají domov v příkazové řádce**, takže je vzor
  nenašel vůbec.
- **`systemd-run` se v kontextu služby od sandboxu odpojí.** Brána pak prostor
  považovala za mrtvý a na další požadavek spustila druhý.

Výsledek na testovacím serveru: dva zapomenuté prostory jednoho účtu, 900 MB.
Zastavení teď jde přes cgroup: SIGTERM všem procesům, po 8 s SIGKILL (ten init
namespace dostane vždycky). Při startu brána zastaví i prostory, o kterých neví.

## Rozhraní pro appku

Appka se s bránou baví přes pár adres. Všechno ostatní brána po přihlášení
proxuje do instance hubu toho uživatele.

| adresa | k čemu | ověření |
|---|---|---|
| `GET /gw/info` | appka pozná, že je na adrese opravdu brána Code Hubu | žádné |
| `POST /login` (JSON) | e-mail a heslo → token zařízení | heslo |
| `GET /gw/me` | komu token patří, jestli ještě platí | `Authorization: Bearer` |
| `POST /gw/handoff` | token → jednorázový kód do adresy okna (60 s) | `Authorization: Bearer` |
| `GET /login?handoff=kód` | okno dostane cookie s **tímtéž** tokenem | kód |
| `GET /logout` | zneplatní token z cookie | cookie |
| `POST /gw/pocitac/poll` | počítač čeká na úkoly od Clauda z prostoru (až 25 s), `bye` = odpojit | `Authorization: Bearer` |
| `POST /gw/pocitac/vysledek` | počítač vrací výsledek úkolu | `Authorization: Bearer` |
| `POST /gw/pocitac/volani` | MCP server v prostoru posílá úkol počítači, nechává a ruší úkoly na později (`ukol-novy`, `ukol-zrusit`); jen přímo na loopback, přes nginx 404 | `X-Hub-Pocitac` (žeton prostoru) |
| `POST /gw/pocitac/ukol` | počítač si stáhne úkol na později (`take`) nebo hlásí jeho stav (`state`) | `Authorization: Bearer` |
| `GET /gw/pocitac` | připojené počítače a úkoly na později — štítek a karta v prostoru | cookie |
| `GET /gw/claude` | na čem Claude v prostoru jede (předplatné / klíč API / přihlášení / nic) a jestli čeká na restart | `Authorization: Bearer` nebo cookie |
| `POST /gw/claude` | připojit (`token`) nebo odpojit (`remove`) vlastní předplatné | `Authorization: Bearer`, z prohlížeče cookie + `X-Hub-Account` |

Appka i prohlížeč můžou být otevřené naráz — obě okna jsou jen pohled do
jednoho hubu. Výpis tabů jde do všech, tab otevřený, přejmenovaný nebo zavřený
v jednom se ukáže i v druhém a psát jde z kteréhokoli. Terminál má ale jen
jeden rozměr: drží ho okno, ve kterém se naposledy psalo nebo na které se
přepnulo, takže se Claude Code v tom druhém může do přepnutí vykreslit na cizí
šířku.

Předání při každém spuštění nevyrábí nový token: cookie okna nese token appky.
Databáze tak nenarůstá s každým startem a *Odhlásit se* v okně odhlásí i appku —
příště se opravdu zeptá. Brána z doby před `/gw/info` se pozná podle toho, jak
`/gw/me` odmítne cizí token, takže novější appka se přihlásí i k ní.

## Zápis do domovů

Domov patří session — kdo v prostoru pracuje, může v něm místo souboru nechat
symbolický odkaz kamkoli na server. Brána přitom do domovů zapisuje bez
sandboxu (nastavení hubu, pokyny v `CLAUDE.md`, `settings.json`, přejmenování
domova). Do 2.13 to šlo obyčejným `open()`, a odkaz
`~/.claude/settings.json.hub-tmp` → `/home/hub/users/.domovy.json` s připraveným
`settings.json` stačil, aby brána přepsala registr domovů a session po restartu
dostala **cizí domov** (ověřeno na kódu 2.13.0).

Od 2.14 jde všechno přes `gateway/safefs.py`: složky se otevírají po jedné
s `O_NOFOLLOW` a soubory relativně k nim (`dir_fd`), zápis přes dočasný soubor
s náhodným jménem (`O_EXCL`) a `rename`. Odkaz se nikdy nenásleduje — při čtení
se bere jako chybějící soubor (obsah cizího souboru se do domova nezkopíruje),
při zápisu se nahradí; složka `~/.claude`, která je odkazem, se odloží stranou
a založí se skutečná. `~/.claude.json` brána už vůbec nepíše — klíč API
a dokončené nastavení Claude Code předvyplní hub uvnitř sandboxu
(`hub/predplatne.py`).

## Zálohy

Každou noc v 1:30 běží `claude-hub-zaloha` (systemd timer, nastaví ho
`install.sh`): zabalí `/home/hub` — domovy prostorů, firemní vault, databázi
účtů — a uloží do `/var/backups/claude-hub/`. Cache se vynechávají, takže
z 3,2 GB je ~71 MB. Drží se posledních 14 kusů.

**Šifruje se veřejným klíčem.** Server má jen ten a zálohu proto umí zabalit,
ale rozbalit ne — ověřeno, hlásí `No secret key`. Soukromý klíč patří mimo
server, do trezoru hesel správce. Kdo ukradne server i se zálohami, nepřečte
z nich nic, a přitom se při zálohování nikam nezadává heslo.

Klíč se nasadí jednou:

```bash
gpg --armor --export <klíč> | ssh root@brána 'cat > /etc/claude-hub/zalohy.asc'
```

**Bez toho souboru se zálohovat nebude** a skript to řekne. Je to schválně:
nešifrovaná záloha by jen ležela na disku a tvářila se, že je o co opřít.

Obnova (na stroji, kde je soukromý klíč):

```bash
gpg --decrypt hub-RRRRMMDD-HHMM.tar.zst.gpg | zstd -d | tar -xf - -C /kam
```

⚠️ Zálohy leží na stejném disku jako data — dokud si je někdo nestáhne pryč,
neochrání to proti ztrátě disku. Stahování patří na stroj správce, ne sem.

## Izolace není volitelná

Claude Code na bráně čte soubory a spouští příkazy. U jednoho člověka na
vlastním stroji je to jeho věc; s víc lidmi je to způsob, jak si přečíst
navzájem Obsidian i zbytek serveru. Proto:

- `none` — bez izolace. Jen na vývoj. Brána ho **odmítne pustit**, jakmile
  poslouchá jinam než na `127.0.0.1`.
- `bwrap` — [bubblewrap](https://github.com/containers/bubblewrap). Session vidí
  systém jen ke čtení a zapisuje jedině do svého domova. Nechce démona ani
  práva správce.
- `docker` — kontejner na session, s limity na paměť a procesy.

K izolaci se přidávají **limity přes cgroup** (`systemd-run --scope`): paměť,
počet procesů a podíl na procesoru. bwrap odděluje, co session *vidí*, ale ne
kolik si vezme — na stroji s weby a mailem je to zásadní rozdíl.

Naměřeno, ne odhadnuto (`bwrap`): session přečte svůj vault a dostane se na
API, ale vault kolegy, přihlášení Claude Code, `/etc/shadow`, zápis mimo domov
ani projekty na serveru pro ni neexistují.

## Kolik se toho vejde

Měřeno na živých session, ne odhadnuto: **jedna session Claude Code drží
400–500 MB** (naměřeno 386, 403 a 464 MB na třech běžících).

Rozvaha pro VPS 8 GB / 4 vCPU (sdílené 1:3) / 60 GB, kde vedle běží weby a mail:

| položka | GB |
|---|---|
| Ubuntu 24.04 + systemd | 0,4 |
| nginx + PHP-FPM | 0,8 |
| MariaDB | 0,8 |
| Postfix + Dovecot + rspamd | 0,7 |
| rezerva na špičky | 1,0 |
| **zbývá na session** | **4,3** |

Papírově je to devět session. Skutečný strop je ale jinde: **stačí, aby si dvě
session vzaly svoje maximum, a je po rezervě.** Proto brána nesmí spoléhat na
průměr — potřebuje strop na počet současných session a uspávání nečinných.

Doporučení pro tuhle konfiguraci:

- **nejvýš 4 současné session**, další čekají
- **uspat session po 30 minutách bez psaní** — největší jediná úspora
- **strop na prostor, ne na session** (`MemoryMax` + `MemorySwapMax=0`):
  prostor je hub se všemi taby a jeden tab s Claude Code je i s MCP servery
  kolem **850 MB a 75 procesů** (naměřeno 22. 9. 2026). Pevných 1,5 GB tak
  stačilo sotva na jeden tab a jádro zabíjelo Clauda uprostřed práce. Brána
  proto bere **45 % paměti stroje** (1,5–6 GB; `HUB_GW_MEMORY`), **1024 úloh**
  (`HUB_GW_TASKS`) a všechny prostory dohromady drží pod **85 % stroje**
  (`install.sh` → `user@<hub>.service`). Když ho jádro přece zabije, tab to
  napíše a Claude v konverzaci pokračuje (`agent-wrapper.sh`).
- **odkládací soubor 2–4 GB**. Bez něj sáhne OOM killer po tom, co má nejvíc
  paměti — tedy nejspíš po databázi, ne po session, která to způsobila.
- **ClamAV v mailu si vezme dalších ~1,3 GB.** Když ho chceš, počítej se dvěma
  session místo čtyř.
- **60 GB je sdílených s weby, mailem i zálohami.** Vaulty jsou v řádu MB, ale
  `node_modules` a klony repozitářů narostou rychle — chce to kvótu na domov.

Limity nejsou teorie: se samotným `MemoryMax` proces neumře, jen přeteče do
swapu a stroj se plazí. Naměřeno — 300 MB při stropu 200 MB v klidu projde;
teprve s `MemorySwapMax=0` skončí zabito. Otestované je i to, že limity a
izolace fungují dohromady, ne každé zvlášť.

## Co izolace nevyřeší

Session se prokazuje přihlášením Claude Code, které musí vidět. **Kdo v ní
spustí Claude Code, dosáhne i na ten soubor.** Mezi kolegy, kteří si tak jako
tak věří, je to únosné. Pro lidi zvenku je jediná čistá cesta API klíč, který
drží brána a session ho nikdy nedostane do ruky.

Stejně tak: jedno osobní předplatné Claude sdílené víc lidmi je proti
podmínkám Anthropicu — proto `central` od 2.4.4 znamená klíč API brány, ne
sdílené přihlášení. Klíč přichází do prostoru v prostředí, takže ho kdo
v prostoru spustí Claude Code, taky přečte. Mezi kolegy únosné; mimo firmu
dej každému `own`.
