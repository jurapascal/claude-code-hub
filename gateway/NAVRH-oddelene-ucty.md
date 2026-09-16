# Návrh: každý prostor pod vlastním systémovým účtem

**Stav: hotové a odzkoušené ve virtuálce. Na produkci NENÍ ZAPNUTÉ.**

Zapíná se `HUB_GW_UCTY=1` v prostředí brány; bez toho se nic nemění a všechno
běží po starém. Kód je v repu od 2.21.0, takže se na servery dostane noční
aktualizací — ale **sám se nezapne**.

Odzkoušeno 16. 9. 2026 v QEMU virtuálce (`~/.cache/hub-vm/vm.sh`) se stejným
Ubuntu 24.04 jako produkce: **12 kontrol z 12**.

## Proč

Dnes běží všechny prostory pod jedním systémovým účtem `hub`. Od sebe je dělí
**jen bwrap** — cizí domov se do sandboxu nepřiváže, takže tam prostě není
(ověřeno). Domovy mají `0700`, což je druhá vrstva, ale vlastníkem je u všech
týž `hub`, takže kdo by bwrap obešel, dostane se na všechno: cizí Obsidiany,
`accounts.db` i klíč API.

Cíl: aby ani obejití sandboxu nestačilo, protože prostor poběží jako **jiný
uživatel systému** a na cizí domov nedosáhne ani přes práva.

## Co tomu dnes stojí v cestě

1. **`isolation.py` pouští prostory přes `systemd-run --user --scope`** — tedy
   ve vlastním systemd manažeru účtu `hub`. Na tom visí limity paměti
   (`MemoryMax`), zjišťování, jestli prostor žije (`scope_cgroup`), i zastavení
   (`stop_scope`). Pod jiným uživatelem tohle nefunguje: manažer účtu `hub`
   neumí poslat signál procesu, který patří jinému uid.
2. **Brána běží jako `hub` a do domovů zapisuje** — zakládá je, píše
   `hub-config.json`, sype tam firemní vault a sdílené Obsidiany (`safefs`).
   Kdyby domov patřil cizímu uživateli, nedosáhne na něj.
3. **Přepnout se na jiného uživatele brána nesmí** — nemá práva. Buď poběží
   jako root (což je samo o sobě zhoršení), nebo dostane úzké `sudo`.

## Navržené řešení

**Prostor:** účet `hub-u<N>` na každý prostor, bez hesla, bez shellu zvenčí.

**Domov:** vlastník `hub-u<N>`, skupina `hub`, práva `2770` (setgid, ať nové
soubory drží skupinu).
- prostor = vlastník → píše si k sobě ✓
- brána (`hub`, primární skupina `hub`) → čte i píše ✓
- cizí prostor → není vlastník ani ve skupině `hub` → **nedosáhne** ✓

**Spouštění:** ne přes `systemd-run --user`, ale přes systémovou scope
založenou rootem:

```
sudo /usr/local/sbin/claude-hub-prostor spustit u<N> claude-hub-<jméno> -- bwrap …
  → systemd-run --scope --uid=hub-u<N> --gid=hub-u<N> \
      -p MemoryMax=… -p TasksMax=… --unit=claude-hub-<jméno> \
      bwrap … -- python3 /opt/claude-code-hub/claude-hub.py --no-browser
```

Spouštěč dostane hotové argv, ale **všechno si ověří**: musí to být `bwrap`,
musí to končit právě naším hubem, zápisový `--bind` musí mířit na domov
v `/home/hub/users`, účet musí mít tvar `u<číslo>` a jméno scope náš prefix.
Kdyby `hub` směl volat `systemd-run` přímo, bylo by to rovnou root pro kohokoli,
kdo bránu ovládne.

**Zastavení:** `sudo /usr/local/sbin/claude-hub-prostor zastavit <jednotka>` →
`systemctl stop <jednotka>.scope`. Účet `hub` scope sám nezabije: patří rootovi
a procesy uvnitř cizímu uid.

**sudoers:** účet `hub` smí spustit **jen tenhle jeden skript**, nic jiného.

## Co se musí změnit

| Soubor | Co |
|---|---|
| `gateway/prostor.py` (nový) | spouštěč: ověří argv, založí účet, srovná domov, pustí scope; umí i zastavit |
| `gateway/isolation.py` | `HUB_GW_UCTY=1` → místo `systemd-run --user` volat spouštěč; `systemctl` bez `--user`; zastavení přes spouštěč |
| `gateway/workspace.py` | předá účet (`u<id>`); scope se zachová i když argv začíná `sudo` |
| `gateway/install.sh` | spouštěč do `/usr/local/sbin`, sudoers, balíček `acl`, `751` na cestě k domovům |
| migrace | **není potřeba zvlášť** — domov převede spouštěč při prvním startu prostoru |

## Co se ve virtuálce ověřilo

| | Výsledek |
|---|---|
| Čistá instalace a start prostoru | ✅ |
| Dva prostory zároveň, každý pod svým uid | ✅ `hub-u1`, `hub-u2` |
| Prostor nepřečte cizí domov **mimo sandbox** | ✅ `Permission denied` |
| Prostor nevypíše ani složku s domovy | ✅ |
| Prostor nepřečte `accounts.db` | ✅ |
| Prostor nezapíše do zdroje hubu | ✅ |
| Prostor čte a zapisuje svůj domov | ✅ |
| Brána zapíše do domova každého | ✅ |
| Účty prostorů nejsou ve skupině `hub` | ✅ |
| Limit paměti na scope platí | ✅ `MemoryMax=1500M` |
| Zastavení zabije jen ten jeden prostor | ✅ druhý běžel dál |
| Restart serveru: účty i práva zůstaly, nic neosiřelo | ✅ |
| Druhý běh instalace (idempotence) | ✅ |

### Dvě věci, které to odhalilo

**Nadřazené složky musí jít projít.** `/home/hub` a `/home/hub/users` měly 750,
takže se účet prostoru k vlastnímu domovu vůbec nedostal
(`bwrap: Can't find source path … Permission denied`). Řeší `751` — projít ano,
vypsat ne.

**Samotné `chown` nestačí.** Podsložky domova zůstaly 700/755, takže brána do
nich nezapsala a padala na `.claude/hub-config.json`. Řeší **ACL**
(`setfacl -R` i `-R -d`), protože `chmod` by spravil jen to, co tam je teď —
co si prostor vyrobí potom, vznikne podle jeho umask.

⚠️ Když se převod domova zastaví na půl cesty (vlastník přepsaný, ACL ne),
brána do domova nezapíše a **nespraví se to sama** — `ensure()` běží dřív než
spouštěč. Náprava je jeden příkaz:
`setfacl -R -m g:hub:rwX <domov> && setfacl -R -d -m g:hub:rwX <domov>`

## Zbývá ověřit před produkcí

- **Migrace na skutečných datech** (7 domovů, 3,2 GB) — ve virtuálce běželo na
  dvou prázdných
- **Záloha a obnova** po změně vlastnictví
- Přihlášení a otevření prostoru přes webové rozhraní (ve virtuálce se
  spouštělo kódem brány, ne klikáním)

## Známé riziko migrace

Přepis vlastnictví domovů je nevratný krok na datech sedmi lidí. Před migrací
**stáhnout zálohu k sobě** (`~/.claude/zalohy-brany/stahnout.sh`), ne jen
spoléhat na tu na serveru — migrace běží na tomtéž disku.
