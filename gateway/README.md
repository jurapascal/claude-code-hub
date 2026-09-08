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

Naměřeno, ne odhadnuto (`bwrap`): session přečte svůj vault a dostane se na
API, ale vault kolegy, přihlášení Claude Code, `/etc/shadow`, zápis mimo domov
ani projekty na serveru pro ni neexistují.

## Co izolace nevyřeší

Session se prokazuje přihlášením Claude Code, které musí vidět. **Kdo v ní
spustí Claude Code, dosáhne i na ten soubor.** Mezi kolegy, kteří si tak jako
tak věří, je to únosné. Pro lidi zvenku je jediná čistá cesta API klíč, který
drží brána a session ho nikdy nedostane do ruky.

Stejně tak: jedno osobní předplatné Claude sdílené víc lidmi je proti
podmínkám Anthropicu. Brána je proto psaná tak, aby přepnutí na API klíč byla
změna konfigurace, ne přepis.
