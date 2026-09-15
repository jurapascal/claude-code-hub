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

**Aktualizace jsou noční a automatické.** `claude-hub-update.timer` zkouší
2:15–5:15, jestli je na GitHubu novější vydání. Když ano a nikdo nepracuje,
přepne zdroj, pustí `install.sh` se stejnými parametry (`/etc/claude-hub/install.conf`)
a restartuje bránu; když někdo pracuje, počká na další pokus. Výsledek je
v `/etc/claude-hub/update.json` a v prostoru v Nastavení → Aktualizace —
tlačítko Aktualizovat tam není, zdroj patří serveru.

**Claude přes klíč API.** `claude-hub-admin apikey set` uloží klíč (ověří ho
u Anthropicu) a prostory na `central` ho při startu dostanou jako
`ANTHROPIC_API_KEY`, předschválený, takže se nikdo nepřihlašuje. Platí se podle
spotřeby — v Anthropic Console nastav měsíční limit. `auth <e-mail> own` je
vlastní účet Claude.

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
- **Hádání:** druhý krok má lístek s platností 5 minut a pěti pokusy; po osmi
  neúspěších (heslo i kód) za čtvrt hodinu brána z téže adresy (`X-Real-IP`)
  ani na tentýž e-mail nepustí. Stejný kód z aplikace podruhé neprojde.
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
- **Zápis:** Claude poznámku jen připraví — `python3 tools/firma.py navrh
  CÍL [SOUBOR | -]` ji uloží do `~/.firma/ke-schvaleni/`. Hub ukáže kartu
  s náhledem a cílovou cestou; **Nahrát** pošle prohlížeč na
  `/gw/firma/publish` a zapíše brána (`workspace.publish_company`).
  Existující poznámku přepíše jen tlačítko **Přepsat**.
- **Proč to Claude sám potvrdit nemůže:** zápis vyžaduje přihlašovací cookie
  brány, stejný původ stránky a hlavičku `X-Hub-Firma`. Cookie brány se do
  prostoru nepřeposílá (hub se ověřuje vlastním tokenem) a firemní trezor je
  v sandboxu jen ke čtení. Návrh z domova se čte bez následování odkazů
  a cílová cesta nesmí ven z trezoru.
- Kdo co nahrál: `/home/hub/firma/nahrano.jsonl` (mimo trezor).

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
- **1,5 GB na session** (`MemoryMax` + `MemorySwapMax=0`)
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
