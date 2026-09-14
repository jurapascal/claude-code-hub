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

## Běžící prostory

Každý prostor běží ve vlastní systemd scope `claude-hub-u<id>.scope`. Podle ní
brána pozná, jestli prostor žije (čte `cgroup.procs`, nespouští nic), a přes ni
ho zastavuje — celý, i s Claude Code a vším, co v něm běží. Nečinný se uspí po
`HUB_GW_IDLE_SLEEP`; psaní do terminálu (websocket) se počítá jako aktivita.

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
podmínkám Anthropicu. Brána je proto psaná tak, aby přepnutí na API klíč byla
změna konfigurace, ne přepis.
