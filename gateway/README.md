# Brána — hub pro víc lidí

Hub v `hub/` je jednouživatelský: běží na tvém stroji, poslouchá na loopbacku
a telefon se k němu dostane přes Tailscale. **Brána je něco jiného**: server,
na kterém má každý z týmu svůj účet, svůj Obsidian a svou session Claude Code,
a mobilní aplikace je jen spojnice — napíšeš, pošle se to sem, odpověď se vrátí.

```
[iOS / Android]  ──HTTPS──>  [brána]
   zadáš adresu                ├── účty a tokeny        accounts.py   ✔ hotovo
   píšeš, čteš odpovědi        ├── izolace session      isolation.py  ✔ hotovo
                               ├── pracovní prostor     workspace.py  … chybí
                               └── HTTP + websocket     server.py     … chybí
```

## Stav

| díl | co dělá | stav |
|---|---|---|
| `accounts.py` | účty, hesla (scrypt), tokeny pro zařízení | hotovo, otestováno |
| `isolation.py` | čím se session pouští, aby neviděla na cizí | hotovo, otestováno |
| `workspace.py` | domov uživatele, jeho vault, session Claude Code | chybí |
| `server.py` | přihlášení, websocket, směrování do session | chybí |
| `admin.py` | správa účtů z příkazové řádky | chybí |
| klienti | nativní iOS a Android | chybí |

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
