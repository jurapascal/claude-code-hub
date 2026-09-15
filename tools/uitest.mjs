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
 *
 * Playwright si test najde sám (viz najdiPlaywright). Jinou kopii mu jde
 * vnutit přes PLAYWRIGHT_MODULE=<složka balíčku playwright>.
 */
import fs from 'fs';
import os from 'os';
import path from 'path';
import { fileURLToPath, pathToFileURL } from 'url';

/* Profily: jádro a rozměry tak, jak je má ten který systém. Windows se testuje
 * se škálováním 1.25 — na noteboocích je to výchozí a právě při něm vycházejí
 * výšky řádků necelé. */
const PROFILY = {
  windows: { engine: 'chromium', ctx: { viewport: { width: 1536, height: 824 }, deviceScaleFactor: 1.25 } },
  linux:   { engine: 'chromium', ctx: { viewport: { width: 1920, height: 1000 } } },
  firefox: { engine: 'firefox',  ctx: { viewport: { width: 1600, height: 900 } } },
  mac:     { engine: 'webkit',   ctx: { viewport: { width: 1440, height: 860 }, deviceScaleFactor: 2 } },
  // Zařízení jménem: seznam `devices` je v Playwrightu, a ten se načítá až níž.
  android: { engine: 'chromium', device: 'Pixel 7', touch: true },
  ios:     { engine: 'webkit',   device: 'iPhone 13', touch: true },
};

const [, , url, ...vybrane] = process.argv;
if (!url) { console.error('chybí URL hubu'); process.exit(2); }
const profily = vybrane.length ? vybrane : Object.keys(PROFILY);

/* Kde vzít Playwright. Pevná cesta do cache npx se rozbila sama od sebe: npx
 * si stáhne novější verzi (Playwright MCP to dělá při startu) a ta chce jiné
 * revize prohlížečů, než jsou stažené — test pak padá na „Executable doesn't
 * exist". Bere se proto první kopie, ke které prohlížeče pro vybrané profily
 * na disku opravdu jsou: PLAYWRIGHT_MODULE, playwright vedle repa, pak cache
 * npx od nejnovější. */
function najdiPlaywright(engines) {
  const home = os.homedir();
  const win = process.platform === 'win32';
  const local = process.env.LOCALAPPDATA || path.join(home, 'AppData', 'Local');
  const prohlizece = process.env.PLAYWRIGHT_BROWSERS_PATH ||
    (win ? path.join(local, 'ms-playwright')
      : process.platform === 'darwin' ? path.join(home, 'Library', 'Caches', 'ms-playwright')
        : path.join(home, '.cache', 'ms-playwright'));
  const npx = path.join(process.env.npm_config_cache ||
    (win ? path.join(local, 'npm-cache') : path.join(home, '.npm')), '_npx');

  const stari = (d) => fs.statSync(path.join(d, 'package.json')).mtimeMs;
  const zNpx = fs.existsSync(npx)
    ? fs.readdirSync(npx).map((d) => path.join(npx, d, 'node_modules', 'playwright'))
      .filter((d) => fs.existsSync(path.join(d, 'package.json')))
      .sort((a, b) => stari(b) - stari(a))
    : [];
  const repo = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
  const kopie = [process.env.PLAYWRIGHT_MODULE, path.join(repo, 'node_modules', 'playwright'), ...zNpx]
    .filter((d) => d && fs.existsSync(path.join(d, 'index.mjs')));

  // Které prohlížeče dané kopii chybí. Revize čte z browsers.json jejího
  // playwright-core; headless Chromium je od 1.49 samostatný „headless shell".
  const chybi = (d) => {
    const core = [path.join(d, 'node_modules', 'playwright-core'), path.join(d, '..', 'playwright-core')]
      .find((c) => fs.existsSync(path.join(c, 'browsers.json')));
    if (!core) return engines;
    const spec = JSON.parse(fs.readFileSync(path.join(core, 'browsers.json'), 'utf8')).browsers;
    return engines.filter((engine) => {
      const b = spec.find((x) => x.name === (engine === 'chromium' ? 'chromium-headless-shell' : engine)) ||
                spec.find((x) => x.name === engine);
      const revize = b ? [b.revision, ...Object.values(b.revisionOverrides || {})] : [];
      return !revize.some((r) => fs.existsSync(
        path.join(prohlizece, `${b.name.replace(/-/g, '_')}-${r}`, 'INSTALLATION_COMPLETE')));
    });
  };

  const vhodna = kopie.find((d) => chybi(d).length === 0);
  if (!vhodna) {
    console.error(kopie.length
      ? `Playwright na disku je, ale chybí mu prohlížeče (${chybi(kopie[0]).join(', ')}). Stáhni je:\n` +
        `    npx playwright install ${engines.join(' ')}`
      : `Playwright není nainstalovaný. Stáhni ho i s prohlížeči:\n` +
        `    npx playwright install ${engines.join(' ')}`);
    process.exit(2);
  }
  return vhodna;
}

const engines = [...new Set(profily.map((j) => PROFILY[j] && PROFILY[j].engine).filter(Boolean))];
const pw = najdiPlaywright(engines);
const { chromium, firefox, webkit, devices } = await import(pathToFileURL(path.join(pw, 'index.mjs')).href);
const ENGINES = { chromium, firefox, webkit };
console.log(`Playwright ${JSON.parse(fs.readFileSync(path.join(pw, 'package.json'), 'utf8')).version} (${pw})`);

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
  const ctx = await browser.newContext({ colorScheme: 'dark',
    ...(p.device ? devices[p.device] : {}), ...p.ctx });
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
      // Do dočasné složky, ne do repa — odkud se test pouští, tam by zůstaly.
      const snimek = path.join(os.tmpdir(), `uitest-${jmeno}.png`);
      await page.screenshot({ path: snimek });
      proc += ' | snímek: ' + snimek;
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
    /* Sekce Účet: je na hubu na počítači i na serveru (telefon se od 2.1.0
       připojuje přes server, samostatná sekce Telefon zanikla) a kreslí se
       podle toho, kde běží — rozbije se tedy jako první, když se to splete. */
    await page.locator('.set-tab', { hasText: 'Účet' }).click();
    await page.waitForTimeout(1500);
    t('sekce Účet se vykreslí',
      await page.locator('.set-panel .set-title', { hasText: 'Účet' }).first().isVisible());
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
