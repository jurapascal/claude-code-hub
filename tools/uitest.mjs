/* Zkouška klientů — projde UI hubu na všech jádrech prohlížečů a velikostech,
 * na kterých má běžet, a ověří to, co se v praxi rozbíjelo: že se načte pole
 * na psaní, že jde přepnout model a že se nic neseká.
 *
 * Co to POKRÝVÁ: samotné UI. Windows a Mac se zkouší na tom jádru, které tam
 * lidi mají (Chromium, WebKit = Safari), takže chyby v rozvržení, v CSS a v JS
 * to najde stejně jako na cílovém stroji.
 *
 * Co to NEPOKRÝVÁ a musí se zkusit ručně: ConPTY na Windows, instalačky,
 * chování nativních aplikací a cokoli, co dělá operační systém pod prohlížečem.
 *
 * Použití:
 *     python3 claude-hub.py --no-browser        # v jiném okně, vypíše URL
 *     node tools/uitest.mjs <url> [profil…]
 */
import { chromium, firefox, webkit, devices }
  from '/home/pascaljura/.npm/_npx/9833c18b2d85bc59/node_modules/playwright/index.mjs';

const ENGINES = { chromium, firefox, webkit };

/* Profily: jádro a rozměry tak, jak je má ten který systém. Windows se testuje
 * se škálováním 1.25 — na noteboocích je to výchozí a právě při něm vycházejí
 * výšky řádků necelé. */
const PROFILY = {
  windows: { engine: 'chromium', ctx: { viewport: { width: 1536, height: 824 }, deviceScaleFactor: 1.25 } },
  linux:   { engine: 'chromium', ctx: { viewport: { width: 1920, height: 1000 } } },
  firefox: { engine: 'firefox',  ctx: { viewport: { width: 1600, height: 900 } } },
  mac:     { engine: 'webkit',   ctx: { viewport: { width: 1440, height: 860 }, deviceScaleFactor: 2 } },
  android: { engine: 'chromium', ctx: { ...devices['Pixel 7'] }, touch: true },
  ios:     { engine: 'webkit',   ctx: { ...devices['iPhone 13'] }, touch: true },
};

const [, , url, ...vybrane] = process.argv;
if (!url) { console.error('chybí URL hubu'); process.exit(2); }
const profily = vybrane.length ? vybrane : Object.keys(PROFILY);

/* Hlášky, které nejsou chyba, i když je prohlížeč hlásí jako chybu.
 * Safari nezná ve <meta viewport> klíč interactive-widget a řekne to nahlas.
 * Na Androidu ho ale potřebujeme, aby měkká klávesnice bublinu vytlačila
 * místo překrytí — nechat ho tam a v Safari přehlédnout je menší zlo než
 * přijít o to chování. Cokoli jiného je chyba a má zkoušku shodit. */
const NESKODNE = [/interactive-widget/i];

const zkousky = [];
const zapis = (profil, jmeno, ok, detail = '') =>
  zkousky.push({ profil, jmeno, ok, detail });

for (const jmeno of profily) {
  const p = PROFILY[jmeno];
  if (!p) { console.error(`neznámý profil ${jmeno}`); continue; }
  const engine = ENGINES[p.engine];
  const browser = await engine.launch(
    p.engine === 'chromium' ? { args: ['--no-sandbox'] } : {});
  const ctx = await browser.newContext({ colorScheme: 'dark', ...p.ctx });
  const page = await ctx.newPage();
  const chyby = [];
  page.on('pageerror', (e) => chyby.push(String(e).slice(0, 120)));
  page.on('console', (m) => {
    if (m.type() !== 'error') return;
    if (NESKODNE.some((re) => re.test(m.text()))) return;
    chyby.push(m.text().slice(0, 120));
  });

  const t = (n, ok, d) => zapis(jmeno, n, ok, d);
  try {
    await page.goto(url, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(2500);
    t('stránka se načte', await page.locator('#projects .card').count() > 0);
    t('terminálový tab jde otevřít', true);

    // Tab s agentem — teprve v něm je bublina s polem na psaní.
    await page.click('#btn-new-agent');
    const bublina = page.locator('.pane.active .composer.on .composer-input');
    /* Claude Code se u nové složky ptá na důvěru a bublina na to správně
       čeká — kartu je tedy potřeba odklepnout stejně, jako by to udělal
       člověk, jinak se pole na psaní nikdy neukáže. */
    const duvera = page.locator('.pane.active .ask-opt', { hasText: 'trust' });
    for (let i = 0; i < 30 && !(await bublina.isVisible().catch(() => false)); i++) {
      if (await duvera.count() && await duvera.first().isVisible().catch(() => false)) {
        await duvera.first().click({ force: true });
      }
      await page.waitForTimeout(2000);
    }
    let nacetla = await bublina.isVisible().catch(() => false);
    // Když se pole neukáže, je potřeba vidět proč — jinak se hádá naslepo.
    let proc = '';
    if (!nacetla) {
      proc = await page.evaluate(() => {
        const pane = document.querySelector('.pane.active');
        const rows = pane && pane.querySelector('.xterm-rows');
        const c = pane && pane.querySelector('.composer');
        return [
          c ? (c.classList.contains('on') ? 'bublina je on' : 'bublina složená') : 'bublina chybí',
          pane && pane.querySelector('.ask:not([hidden])') ? 'na obrazovce je karta s dotazem' : '',
          'spodek: ' + (rows ? rows.innerText.replace(/\s+/g, ' ').trim().slice(-70) : '?'),
        ].filter(Boolean).join(' | ');
      });
      await page.screenshot({ path: `uitest-${jmeno}.png` });
    }
    t('pole na psaní se načte', nacetla, proc);

    if (nacetla) {
      // Přepínání modelu — chip a jeho nabídka.
      const chip = page.locator('.pane.active [data-act=model]');
      t('chip modelu je vidět', await chip.isVisible());
      await chip.click();
      await page.waitForTimeout(700);
      const polozky = await page.locator('.ctxmenu button, #ctxmenu button').count();
      t('nabídka modelů se otevře', polozky > 0, `${polozky} položek`);
      await page.keyboard.press('Escape');
      await page.waitForTimeout(300);

      // Slash příkazy
      await page.locator('.pane.active [data-act=slash]').click();
      await page.waitForTimeout(700);
      const slashu = await page.locator('.ctxmenu button, #ctxmenu button').count();
      t('nabídka / příkazů se otevře', slashu > 0, `${slashu} položek`);
      await page.keyboard.press('Escape');
      await page.waitForTimeout(300);

      t('jde psát do pole', await (async () => {
        await bublina.fill('zkouška psaní');
        return (await bublina.inputValue()) === 'zkouška psaní';
      })());
      await bublina.fill('');

      /* Seká se? Počítáme, kolikrát se v klidu přepočítá velikost terminálu
         a kolik resize zpráv odteče do pty. V klidu má být obojí nula.

         Napřed se ale musí nechat doznít to, co jsme sami vyvolali: po
         otevření nabídek a psaní do pole se rozvržení chvíli usazuje a měřit
         do toho znamená počítat vlastní kroky jako vadu. Na iOS to tak
         vycházely dvě události, které při měření od klidu nejsou. */
      await page.waitForTimeout(3000);
      await page.evaluate(() => {
        window.__m = { resize: 0, styl: 0 };
        const orig = WebSocket.prototype.send;
        WebSocket.prototype.send = function (d) {
          try { if (JSON.parse(d).t === 'resize') window.__m.resize++; } catch (e) {}
          return orig.call(this, d);
        };
        new MutationObserver((muts) => {
          for (const m of muts) {
            if (m.target.classList && m.target.classList.contains('termbox')) window.__m.styl++;
          }
        }).observe(document.body, { subtree: true, attributes: true, attributeFilter: ['style'] });
      });
      await page.waitForTimeout(6000);
      const m = await page.evaluate(() => window.__m);
      t('v klidu se nepřepočítává', m.resize === 0 && m.styl === 0,
        `resize ${m.resize}, výška ${m.styl}`);
    }

    if (p.touch) {
      t('šuplík s projekty se otevře', await (async () => {
        await page.click('#btn-drawer');
        await page.waitForTimeout(400);
        return page.evaluate(() => document.body.classList.contains('drawer-open'));
      })());
      await page.keyboard.press('Escape');
      t('řádek kláves je vidět',
        await page.locator('.pane.active .composer-keys .composer-key').first().isVisible());
    }

    // Nastavení
    await page.click('#btn-settings');
    await page.waitForTimeout(800);
    t('nastavení se otevře', await page.locator('.set-modal').isVisible());
    await page.locator('.set-tab', { hasText: 'Telefon' }).click();
    await page.waitForTimeout(1200);
    t('sekce Telefon se vykreslí', await page.locator('.set-panel .set-title').first().isVisible());
    await page.locator('.set-close').click();

    t('žádné chyby v konzoli', chyby.length === 0, chyby.slice(0, 2).join(' | '));
  } catch (exc) {
    t('profil doběhl', false, String(exc).split('\n')[0].slice(0, 100));
  }
  await browser.close();
}

// ── výpis ────────────────────────────────────────────────────────────────────
let spatne = 0;
let aktualni = '';
for (const z of zkousky) {
  if (z.profil !== aktualni) { aktualni = z.profil; console.log(`\n── ${aktualni} ──`); }
  if (!z.ok) spatne++;
  console.log(`  ${z.ok ? 'OK   ' : 'CHYBA'} ${z.jmeno}${z.detail ? '  (' + z.detail + ')' : ''}`);
}
console.log(`\n${zkousky.length - spatne}/${zkousky.length} v pořádku`);
process.exit(spatne ? 1 : 0);
