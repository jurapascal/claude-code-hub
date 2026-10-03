/* Pluginy — Nastavení → Pluginy a běh pluginů appky (hub/pluginy.py).
 *
 * Dvě záložky:
 *  - Claude Code: katalog z marketplace (instalace jedním klikem),
 *    nainstalované (zapnout / vypnout / aktualizovat / odinstalovat)
 *    a správa marketplace. Dělá to `claude plugin …` na serveru hubu.
 *  - Pro appku: rozšíření samotného hubu. Plugin je složka s plugin.json
 *    a JS/CSS; po zapnutí se načte do okna a dostane API:
 *
 *      HubPluginy.registruj('moje-id', (api) => {
 *        api.akce({label: 'Pozdrav', run: () => api.toast('Ahoj!')});
 *        api.on('nastroj', (e) => console.log('Claude spustil', e.name));
 *        api.on('odeslano', (e) => …);   // zpráva z pole na psaní
 *        api.on('tab', (e) => …);        // přepnutý tab
 *        api.on('prace', (e) => …);      // Claude začal / přestal pracovat
 *        api.posli('text');              // pošle zprávu do aktivního chatu
 *        api.aktivniTab();               // {id, title, path} nebo null
 *        api.uloziste.set('k', 1); api.uloziste.get('k');
 *        api.hub('state');               // volání API hubu (plná práva!)
 *        api.nastaveni((box) => { … });  // vlastní nastavení v Pluginy
 *      });
 *
 *    Plugin běží se stejnými právy jako appka — proto je po přidání vypnutý
 *    a zapnout ho jde jen vědomě.
 */
'use strict';

(function (global) {
  let io = null;                    // {api, toast, posli, aktivniTab, renderActions}
  const registrace = new Map();     // id → {api, nastaveni}
  const posluchaci = new Map();     // událost → [{id, fn}]
  const akceSeznam = [];            // tlačítka v Rychlých akcích
  const nactene = new Set();
  let zakladni = null;              // seznam pluginů appky z poslední odpovědi

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  /* ── běh pluginů appky ──────────────────────────────────────────────── */
  function apiPro(id) {
    const klic = (k) => 'hub-plugin:' + id + ':' + k;
    return {
      id,
      toast: (t) => io.toast(String(t)),
      akce: ({label, run, title}) => {
        akceSeznam.push({id, label: String(label || id), title: title || '', run});
        if (io.renderActions) io.renderActions();
      },
      on: (udalost, fn) => {
        if (!posluchaci.has(udalost)) posluchaci.set(udalost, []);
        posluchaci.get(udalost).push({id, fn});
      },
      posli: (text) => io.posli(String(text || '')),
      aktivniTab: () => io.aktivniTab(),
      uloziste: {
        get: (k) => { try { return JSON.parse(localStorage.getItem(klic(k))); } catch (_) { return null; } },
        set: (k, v) => { try { localStorage.setItem(klic(k), JSON.stringify(v)); } catch (_) { /* plné */ } },
      },
      hub: (path, body) => io.api(path, body),
      nastaveni: (fn) => { const r = registrace.get(id); if (r) r.nastaveni = fn; },
    };
  }

  function registruj(id, init) {
    id = String(id || '');
    if (!id || registrace.has(id)) return;
    const reg = {api: apiPro(id), nastaveni: null};
    registrace.set(id, reg);
    try { init(reg.api); }
    catch (err) {
      console.error('plugin ' + id, err);
      io.toast('Plugin „' + id + '“ spadl při startu: ' + (err && err.message || err));
    }
  }

  function vysli(udalost, data) {
    for (const {id, fn} of posluchaci.get(udalost) || []) {
      try { fn(data); } catch (err) { console.error('plugin ' + id + ' (' + udalost + ')', err); }
    }
  }

  const url = (id, f) => '/api/plugin-soubor?id=' + encodeURIComponent(id) + '&f=' + encodeURIComponent(f) +
                         '&t=' + encodeURIComponent(io.token || '');

  function nactiPlugin(p) {
    if (!p.enabled || nactene.has(p.id)) return;
    nactene.add(p.id);
    if (p.style) {
      const l = el('link');
      l.rel = 'stylesheet';
      l.href = url(p.id, p.style);
      l.dataset.plugin = p.id;
      document.head.appendChild(l);
    }
    if (p.main) {
      const s = el('script');
      s.src = url(p.id, p.main);
      s.dataset.plugin = p.id;
      s.onerror = () => io.toast('Plugin „' + p.name + '“ se nenačetl.');
      document.body.appendChild(s);
    }
  }

  async function start(opts) {
    io = opts;
    // Události hubu → pluginy.
    document.addEventListener('hub-tool', (ev) => vysli('nastroj', ev.detail || {}));
    document.addEventListener('hub-odeslano', (ev) => vysli('odeslano', ev.detail || {}));
    document.addEventListener('hub-tab', (ev) => vysli('tab', ev.detail || {}));
    document.addEventListener('hub-prace', (ev) => vysli('prace', ev.detail || {}));
    try {
      zakladni = await io.api('pluginy?jen=appka');
      if (zakladni.allowed) for (const p of zakladni.plugins || []) nactiPlugin(p);
    } catch (_) { /* starší hub — pluginy nejsou */ }
  }

  /* ── Nastavení → Pluginy ────────────────────────────────────────────── */
  function sekce() {
    const box = el('div', 'set-sec plg');
    box.appendChild(el('div', 'set-title', 'Pluginy'));
    const tabs = el('div', 'plg-tabs');
    const telo = el('div', 'plg-telo');
    let kde = localStorage.getItem('hub-plg-tab') || 'cc';
    const prepni = (k) => {
      kde = k;
      try { localStorage.setItem('hub-plg-tab', k); } catch (_) { /* nic */ }
      for (const b of tabs.children) b.classList.toggle('on', b.dataset.k === k);
      telo.textContent = '';
      telo.appendChild(k === 'cc' ? claudeCode() : appka());
    };
    for (const [k, t] of [['cc', 'Claude Code'], ['appka', 'Pro appku']]) {
      const b = el('button', 'plg-tab', t);
      b.dataset.k = k;
      b.onclick = () => prepni(k);
      tabs.appendChild(b);
    }
    box.append(tabs, telo);
    prepni(kde);
    return box;
  }

  function radek(nazev, popis, meta, ico) {
    const r = el('div', 'plg-radek');
    r.appendChild(ikonka(nazev, ico));
    const info = el('div', 'plg-info');
    info.appendChild(el('b', '', nazev));
    if (meta) info.appendChild(el('span', 'plg-meta', meta));
    if (popis) { const d = el('div', 'plg-popis', popis); d.title = popis; info.appendChild(d); }
    r.appendChild(info);
    const tlacitka = el('div', 'plg-btns');
    r.appendChild(tlacitka);
    return {r, tlacitka};
  }

  function tlacitko(text, fn, cls) {
    const b = el('button', 'btn ' + (cls || 'ghost'), text);
    b.onclick = async () => {
      b.disabled = true;
      try { await fn(); } finally { b.disabled = false; }
    };
    return b;
  }

  /* Ikonka: barevný čtvereček s písmenem (barva z názvu) a kategorie podle slov. */
  const KATEGORIE = [
    ['design', 'Design', 'i-image', /design|figma|ui\b|ux|css|frontend|canva|logo|color|palette/i],
    ['kod', 'Kód a review', 'i-terminal', /code|review|debug|test|refactor|lint|typescript|python|git\b|github|pull request|tdd/i],
    ['data', 'Data a API', 'i-chart', /database|sql|data|api\b|postgres|supabase|analytics|openapi|query/i],
    ['web', 'Web a prohlížeč', 'i-globe', /browser|playwright|web|seo|scrap|search|crawl|http/i],
    ['produktivita', 'Práce a komunikace', 'i-note', /slack|notion|linear|jira|asana|email|calendar|docs|project|task|workflow|agent/i],
    ['bezpecnost', 'Bezpečnost', 'i-lock', /security|vulnerab|owasp|secret|auth|audit/i],
  ];
  const kategorie = (p) => (KATEGORIE.find((k) => k[3].test(p.name + ' ' + p.description)) || ['ostatni', 'Ostatní', 'i-star'])[0];
  const katInfo = (id) => KATEGORIE.find((k) => k[0] === id) || ['ostatni', 'Ostatní', 'i-star'];
  /* Ikonka řádku: barevný čtvereček (barva z názvu) s ikonkou appky
     (`i-…` ze sady v index.html), nebo s prvním písmenem názvu. */
  function ikonka(nazev, ico) {
    let h = 0;
    for (const c of nazev) h = (h * 31 + c.charCodeAt(0)) % 360;
    const i = el('span', 'plg-ikona');
    if (ico && ico.startsWith('i-')) i.innerHTML = '<svg class="ico"><use href="#' + ico + '"/></svg>';
    else i.textContent = (nazev.replace(/^[^A-Za-zÀ-ž0-9]+/, '')[0] || '?').toUpperCase();
    i.style.background = 'hsl(' + h + ' 45% 32%)';
    return i;
  }
  const svg = (ico) => '<svg class="ico"><use href="#' + ico + '"/></svg>';

  const kolik = (n) => n >= 1e6 ? (n / 1e6).toFixed(1).replace('.0', '') + ' mil.' : n >= 1000 ? Math.round(n / 1000) + ' tis.' : String(n);

  function claudeCode(mode) {
    const wrap = el('div');
    const objevit = mode === 'objevit';
    wrap.appendChild(el('div', 'set-note', objevit
      ? 'Katalog pluginů — instalace jedním klikem, projeví se v nově otevřených chatech.'
      : 'Pluginy přidávají Claudovi dovednosti, příkazy, pomocníky a napojení.'));
    const stav = el('div', 'set-note', 'Načítám katalog…');
    wrap.appendChild(stav);
    const obsah = el('div');
    wrap.appendChild(obsah);
    let data = null;

    async function akce(body, potvrzeni) {
      try {
        const out = await io.api('pluginy', body);
        if (out.potvrdit) {
          const ok = confirm(out.message + '\n\nPříkaz:\n' + out.potvrdit + '\n\nSpustit a nainstalovat?');
          if (ok) return akce({...body, sha: out.sha});
          return;
        }
        io.toast(out.message || 'Hotovo.');
        await nacti(false);
      } catch (err) {
        io.toast(err.message || 'Nepovedlo se.');
      }
    }

    async function nacti(obnovit) {
      try {
        const res = await io.api('pluginy' + (obnovit ? '?refresh=1' : ''));
        data = res.cc;
        stav.textContent = data.error ? 'Katalog se nenačetl: ' + data.error : '';
        stav.hidden = !data.error;
        kresli();
      } catch (err) {
        stav.textContent = 'Katalog se nenačetl: ' + err.message;
      }
    }

    function kresli() {
      obsah.textContent = '';
      // Moje: nainstalované
      if (!objevit && !data.installed.length) obsah.appendChild(el('div', 'set-note', 'Zatím žádné pluginy. Vyber si v záložce Objevit.'));
      for (const p of (objevit ? [] : data.installed)) {
        const [jmeno, trh] = p.id.split('@');
        const vKatalogu = data.available.find((a) => a.id === p.id);
        const {r, tlacitka} = radek(jmeno, vKatalogu ? vKatalogu.description : '', (trh || '') + (p.scope === 'project' ? ' · jen v projektu ' + (p.projectPath || '').split(/[\\/]/).pop() : '') +
                                    (p.enabled ? ' · zapnutý' : ' · vypnutý'),
                                    katInfo(vKatalogu ? kategorie(vKatalogu) : 'ostatni')[2]);
        if (p.scope === 'user') {
          tlacitka.appendChild(tlacitko(p.enabled ? 'Vypnout' : 'Zapnout', () => akce({akce: p.enabled ? 'disable' : 'enable', id: p.id})));
          tlacitka.appendChild(tlacitko('Aktualizovat', () => akce({akce: 'update', id: p.id})));
          tlacitka.appendChild(tlacitko('Odinstalovat', () => confirm('Odinstalovat „' + jmeno + '“?') && akce({akce: 'uninstall', id: p.id}), 'danger'));
        } else {
          tlacitka.appendChild(tlacitko('Nainstalovat všude', () => akce({akce: 'install', id: p.id})));
        }
        obsah.appendChild(r);
      }
      if (!objevit) {
        // Katalogy (marketplace) patří k mým pluginům — odkud se berou.
        obsah.appendChild(el('div', 'set-title plg-podnadpis', 'Katalogy (marketplace)'));
        for (const m of data.marketplaces) obsah.appendChild(radek(m.name, '', m.repo || m.url || m.source || '', 'i-folder').r);
        const pridat = el('div', 'set-row');
        const zdroj = el('input', 'set-input');
        zdroj.placeholder = 'GitHub owner/repo nebo adresa katalogu';
        pridat.append(zdroj, tlacitko('Přidat katalog', () => zdroj.value.trim() && akce({akce: 'marketplace-add', zdroj: zdroj.value.trim()})));
        pridat.appendChild(tlacitko('Aktualizovat katalogy', () => akce({akce: 'marketplace-update'})));
        obsah.appendChild(pridat);
        return;
      }
      const hledat = el('input', 'set-input plg-hledat');
      hledat.placeholder = 'Hledat plugin — třeba „figma", „github", „design"…';
      const radka = el('div', 'plg-filtry');
      let kat = '', razeni = 'popularita';
      const chipy = el('div', 'plg-chipy');
      const mkChip = (id, text, ico) => {
        const c = el('button', 'plg-chip' + (kat === id ? ' on' : ''));
        c.innerHTML = (ico ? svg(ico) : '') + '<span></span>';
        c.querySelector('span').textContent = text;
        c.onclick = () => { kat = id; for (const x of chipy.children) x.classList.toggle('on', x.dataset.k === id); vypis(); };
        c.dataset.k = id;
        chipy.appendChild(c);
      };
      mkChip('', 'Vše');
      for (const k of KATEGORIE) mkChip(k[0], k[1], k[2]);
      mkChip('ostatni', 'Ostatní', 'i-star');
      const sel = el('select', 'set-input plg-razeni');
      for (const [v, t] of [['popularita', 'Nejoblíbenější'], ['nazev', 'Podle abecedy']]) sel.appendChild(Object.assign(el('option', '', t), {value: v}));
      sel.onchange = () => { razeni = sel.value; vypis(); };
      radka.append(chipy, sel);
      obsah.append(hledat, radka);
      const seznam = el('div', 'plg-seznam');
      obsah.appendChild(seznam);
      let token = 0;
      const vypis = () => {
        const q = hledat.value.trim().toLowerCase();
        const mine = ++token;
        let hit = data.available.filter((p) => (!q || (p.name + ' ' + p.description + ' ' + p.marketplace).toLowerCase().includes(q)) &&
                                               (!kat || kategorie(p) === kat));
        if (razeni === 'nazev') hit = hit.slice().sort((x, y) => x.name.localeCompare(y.name));
        seznam.textContent = '';
        if (!hit.length) { seznam.appendChild(el('div', 'set-note', 'Nic takového v katalogu není.')); return; }
        const celkem = Math.min(hit.length, 120);
        let i = 0;
        const dalsi = () => {            // po jednom, postupně — okno se nezasekne a řádky naskakují
          if (mine !== token) return;
          const dalsiHit = hit[i++];
          const {r, tlacitka} = radek(dalsiHit.name, dalsiHit.description, dalsiHit.marketplace + ' · ' + kolik(dalsiHit.installs) + ' instalací',
                                      katInfo(kategorie(dalsiHit))[2]);
          r.classList.add('plg-vstup');
          if (data.installed.some((x) => x.id === dalsiHit.id && x.scope === 'user')) {
            tlacitka.appendChild(el('span', 'set-ok', '✓ Nainstalováno'));
          } else {
            tlacitka.appendChild(tlacitko('Nainstalovat', () => akce({akce: 'install', id: dalsiHit.id}), 'primary'));
          }
          seznam.appendChild(r);
          if (i < celkem) setTimeout(dalsi, 25);
          else if (hit.length > celkem) seznam.appendChild(el('div', 'set-note', 'A dalších ' + (hit.length - celkem) + ' — upřesni hledání nebo vyber kategorii.'));
        };
        dalsi();
      };
      let t = null;
      hledat.oninput = () => { clearTimeout(t); t = setTimeout(vypis, 150); };
      vypis();
    }
    nacti(false);
    return wrap;
  }

  /* Skilly: co Claude umí. Moje příkazy (slash), ze zapnutých pluginů a
     postupy v Obsidian Brainu, které si Claude načte, když je potřebuje. */
  const ZDROJE = [['moje', 'Moje příkazy', 'i-bolt'], ['plugin', 'Z pluginů', 'i-plus'], ['obsidian', 'Z Obsidianu', 'i-book']];
  function skilly() {
    const wrap = el('div');
    wrap.appendChild(el('div', 'set-note',
      'Skilly jsou návody, podle kterých Claude dělá konkrétní práci. Moje příkazy spustíš lomítkem ' +
      '(třeba /push), ostatní si Claude vezme sám, když se hodí.'));
    const stav = el('div', 'set-note', 'Načítám skilly…');
    wrap.appendChild(stav);
    io.api('pluginy?jen=skilly').then((d) => {
      const vse = d.skills || [];
      stav.remove();
      const hledat = el('input', 'set-input plg-hledat');
      hledat.placeholder = 'Hledat skill — třeba „newsletter", „seo", „deploy"…';
      const filtry = el('div', 'plg-filtry');
      const chipy = el('div', 'plg-chipy');
      let zdroj = '';
      const mk = (id, text, ico) => {
        const pocet = id ? vse.filter((x) => x.source === id).length : vse.length;
        if (id && !pocet) return;
        const c = el('button', 'plg-chip' + (zdroj === id ? ' on' : ''));
        c.innerHTML = (ico ? svg(ico) : '') + '<span></span>';
        c.querySelector('span').textContent = text + ' · ' + pocet;
        c.dataset.k = id;
        c.onclick = () => { zdroj = id; for (const x of chipy.children) x.classList.toggle('on', x.dataset.k === id); vypis(); };
        chipy.appendChild(c);
      };
      mk('', 'Vše');
      for (const [id, text, ico] of ZDROJE) mk(id, text, ico);
      filtry.appendChild(chipy);
      const seznam = el('div', 'plg-seznam');
      wrap.append(hledat, filtry, seznam);
      let tik = 0;
      const vypis = () => {
        const q = hledat.value.trim().toLowerCase();
        const mine = ++tik;
        const hit = vse.filter((x) => (!zdroj || x.source === zdroj) &&
          (!q || (x.name + ' ' + x.description + ' ' + (x.plugin || '') + ' ' + (x.category || '')).toLowerCase().includes(q)))
          .sort((a, b) => ZDROJE.findIndex((z) => z[0] === a.source) - ZDROJE.findIndex((z) => z[0] === b.source) ||
                          (a.category || '').localeCompare(b.category || '', 'cs') || a.name.localeCompare(b.name, 'cs'));
        seznam.textContent = '';
        if (!hit.length) { seznam.appendChild(el('div', 'set-note', 'Nic takového tu není.')); return; }
        const celkem = Math.min(hit.length, 150);
        let i = 0, skupina = null;
        const dalsi = () => {
          if (mine !== tik) return;
          const x = hit[i++];
          const nadpis = x.source === 'obsidian' ? 'Obsidian · ' + (x.category || '')
            : x.source === 'plugin' ? 'Plugin ' + (x.plugin || '') : 'Moje příkazy';
          if (nadpis !== skupina) {
            skupina = nadpis;
            seznam.appendChild(el('div', 'set-title plg-podnadpis plg-vstup', nadpis));
          }
          const ico = (ZDROJE.find((z) => z[0] === x.source) || [])[2];
          const {r} = radek(x.source === 'moje' ? '/' + x.name : x.name, x.description, '', ico);
          r.classList.add('plg-vstup');
          seznam.appendChild(r);
          if (i < celkem) setTimeout(dalsi, 15);
          else if (hit.length > celkem) seznam.appendChild(el('div', 'set-note', 'A dalších ' + (hit.length - celkem) + ' — upřesni hledání.'));
        };
        dalsi();
      };
      let t = null;
      hledat.oninput = () => { clearTimeout(t); t = setTimeout(vypis, 150); };
      vypis();
    }, (err) => { stav.textContent = 'Skilly se nenačetly: ' + err.message; });
    return wrap;
  }

  /* Katalog napojení: oficiální registr MCP serverů (přes 7 000). Bez hledání
     doporučená oficiální napojení firem, s hledáním výsledky z registru.
     Přidání = vlastní napojení (adresa serveru); přihlásí se v jeho kartě. */
  function katalogNapojeni(poPridani) {
    const wrap = el('div', 'plg-kat-napojeni');
    const claudeAi = el('div', 'plg-radek');
    claudeAi.appendChild(ikonka('claude.ai', 'i-globe'));
    const info = el('div', 'plg-info');
    info.appendChild(el('b', '', 'Konektory z claude.ai'));
    info.appendChild(el('div', 'plg-popis', 'Gmail, Kalendář, Drive, Notion, Slack a další z oficiálního adresáře claude.ai. ' +
      'Co tam zapneš, objeví se tu samo — pod účtem z Nastavení → Účet.'));
    claudeAi.appendChild(info);
    const btns = el('div', 'plg-btns');
    const otevrit = el('button', 'btn ghost', 'Otevřít adresář');
    otevrit.onclick = () => io.open('https://claude.ai/settings/connectors');
    btns.appendChild(otevrit);
    claudeAi.appendChild(btns);
    wrap.appendChild(claudeAi);

    const hledat = el('input', 'set-input plg-hledat');
    hledat.placeholder = 'Hledat v katalogu MCP serverů — třeba „slack", „jira", „shopify"…';
    const stav = el('div', 'set-note', 'Načítám doporučená napojení…');
    const seznam = el('div', 'plg-seznam');
    wrap.append(hledat, stav, seznam);
    let tik = 0;
    async function nacti() {
      const q = hledat.value.trim();
      const mine = ++tik;
      stav.hidden = false;
      stav.textContent = q ? 'Hledám v katalogu…' : 'Načítám doporučená napojení…';
      let d;
      try { d = await io.api('pluginy?jen=napojeni' + (q ? '&q=' + encodeURIComponent(q) : '')); }
      catch (err) { if (mine === tik) stav.textContent = 'Katalog se nenačetl: ' + err.message; return; }
      if (mine !== tik) return;
      seznam.textContent = '';
      stav.textContent = d.error || (q ? '' : 'Oficiální napojení firem:');
      stav.hidden = !stav.textContent;
      if (!d.items.length && !d.error) { stav.hidden = false; stav.textContent = 'Nic takového v katalogu není.'; }
      d.items.forEach((x, i) => setTimeout(() => {
        if (mine !== tik) return;
        const {r, tlacitka} = radek(x.title, x.description, (x.official ? 'oficiální · ' : '') + x.url.replace(/^https?:\/\//, ''));
        r.classList.add('plg-vstup');
        tlacitka.appendChild(tlacitko('Přidat', async () => {
          try {
            const out = await io.api('connect', {action: 'add', service: 'vlastni', label: x.title, account: x.url});
            if (out.ok === false) throw new Error(out.detail || 'Nepovedlo se.');
            io.toast(x.title + ' přidané — přihlas se u něj v kartě Vlastní napojení.');
            if (poPridani) poPridani();
          } catch (err) { io.toast(err.message); }
        }, 'primary'));
        seznam.appendChild(r);
      }, i * 35));
    }
    let t = null;
    hledat.oninput = () => { clearTimeout(t); t = setTimeout(nacti, 350); };
    nacti();
    return wrap;
  }

  let vypnutoNekdy = false;       // vypnutý plugin zmizí až po obnovení okna

  function appka() {
    const wrap = el('div');
    const varovani = el('div', 'set-note plg-varovani');
    varovani.innerHTML = svg('i-error') + '<span>Plugin appky běží uvnitř aplikace se stejnými právy jako ty — může ' +
      'číst chaty, posílat zprávy Claudovi a spouštět příkazy. Zapni jen plugin, kterému věříš.</span>';
    wrap.appendChild(varovani);
    const obsah = el('div');
    wrap.appendChild(obsah);

    async function akce(body) {
      try {
        const out = await io.api('pluginy', body);
        if (out.message) io.toast(out.message);
        zakladni = {...zakladni, plugins: out.appka.plugins, allowed: out.appka.allowed, dir: out.appka.dir};
        if (body.akce === 'appka-zapni') {
          const p = out.appka.plugins.find((x) => x.id === body.id);
          if (p) nactiPlugin(p);
        }
        kresli();
        return out;
      } catch (err) {
        io.toast(err.message || 'Nepovedlo se.');
        return null;
      }
    }

    function kresli() {
      obsah.textContent = '';
      const seznam = (zakladni && zakladni.plugins) || [];
      if (zakladni && !zakladni.allowed) obsah.appendChild(el('div', 'set-warn', 'Pluginy appky jsou tady vypnuté správcem.'));
      if (!seznam.length) obsah.appendChild(el('div', 'set-note', 'Zatím žádný plugin appky.'));
      for (const p of seznam) {
        const {r, tlacitka} = radek(p.name, p.error || p.description,
          [p.version && 'v' + p.version, p.author, p.enabled ? 'zapnutý' : 'vypnutý'].filter(Boolean).join(' · '));
        if (!p.error) {
          tlacitka.appendChild(tlacitko(p.enabled ? 'Vypnout' : 'Zapnout', async () => {
            if (!p.enabled && !confirm('Zapnout plugin „' + p.name + '“?\n\nPoběží v aplikaci se stejnými právy jako ty ' +
                                      '(chaty, zprávy Claudovi, příkazy). Zapni ho, jen když mu věříš.')) return;
            if (p.enabled) vypnutoNekdy = true;
            await akce({akce: p.enabled ? 'appka-vypni' : 'appka-zapni', id: p.id});
          }));
          const reg = registrace.get(p.id);
          if (p.enabled && reg && reg.nastaveni) {
            tlacitka.appendChild(tlacitko('Nastavení', () => {
              const panel = el('div', 'plg-nastaveni');
              r.after(panel);
              try { reg.nastaveni(panel); } catch (err) { panel.textContent = 'Nastavení pluginu spadlo: ' + err.message; }
            }));
          }
        }
        tlacitka.appendChild(tlacitko('Odebrat', () => confirm('Odebrat plugin „' + p.name + '“ i s jeho soubory?') && akce({akce: 'appka-odeber', id: p.id}), 'danger'));
        obsah.appendChild(r);
      }
      if (vypnutoNekdy || seznam.some((p) => !p.enabled && nactene.has(p.id))) {
        const row = el('div', 'set-row');
        row.appendChild(el('span', 'set-note', 'Vypnutý plugin zmizí po obnovení okna.'));
        row.appendChild(tlacitko('Obnovit okno', () => location.reload()));
        obsah.appendChild(row);
      }
      obsah.appendChild(el('div', 'set-title plg-podnadpis', 'Přidat plugin'));
      const row = el('div', 'set-row');
      const adresa = el('input', 'set-input');
      adresa.placeholder = 'GitHub owner/repo nebo https://… adresa repozitáře';
      row.append(adresa, tlacitko('Přidat z gitu', () => adresa.value.trim() && akce({akce: 'appka-pridej', url: adresa.value.trim()})));
      obsah.appendChild(row);
      const row2 = el('div', 'set-row');
      row2.appendChild(tlacitko('Vytvořit ukázkový plugin', () => akce({akce: 'appka-ukazka'})));
      obsah.appendChild(row2);
      if (zakladni && zakladni.dir) {
        obsah.appendChild(el('div', 'set-note', 'Vlastní plugin: složka v ' + zakladni.dir +
          ' s plugin.json (name, version, description, main, style). Ukázkový plugin je dobrá šablona.'));
      }
    }
    if (zakladni) kresli();
    else io.api('pluginy?jen=appka').then((d) => { zakladni = d; kresli(); }, () => kresli());
    return wrap;
  }

  global.HubPluginy = {
    start, registruj, sekce, ikonka,
    claudeCode, skilly, appka, katalogNapojeni,
    akce: () => akceSeznam.slice(),
  };
})(window);
