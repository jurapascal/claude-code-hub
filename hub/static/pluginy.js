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

  function radek(nazev, popis, meta) {
    const r = el('div', 'plg-radek');
    const info = el('div', 'plg-info');
    info.appendChild(el('b', '', nazev));
    if (meta) info.appendChild(el('span', 'plg-meta', meta));
    if (popis) info.appendChild(el('div', 'plg-popis', popis));
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

  const kolik = (n) => n >= 1e6 ? (n / 1e6).toFixed(1).replace('.0', '') + ' mil.' : n >= 1000 ? Math.round(n / 1000) + ' tis.' : String(n);

  function claudeCode() {
    const wrap = el('div');
    wrap.appendChild(el('div', 'set-note',
      'Pluginy přidávají Claudovi dovednosti, příkazy, pomocníky a napojení. Instalují se z katalogů ' +
      '(marketplace) a projeví se v nově otevřených chatech.'));
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
      // Nainstalované
      obsah.appendChild(el('div', 'set-title plg-podnadpis', 'Nainstalované'));
      if (!data.installed.length) obsah.appendChild(el('div', 'set-note', 'Zatím žádné — vyber si níž v katalogu.'));
      for (const p of data.installed) {
        const [jmeno, trh] = p.id.split('@');
        const {r, tlacitka} = radek(jmeno, '', (trh || '') + (p.scope === 'project' ? ' · jen v projektu ' + (p.projectPath || '').split(/[\\/]/).pop() : '') +
                                    (p.enabled ? ' · zapnutý' : ' · vypnutý'));
        if (p.scope === 'user') {
          tlacitka.appendChild(tlacitko(p.enabled ? 'Vypnout' : 'Zapnout', () => akce({akce: p.enabled ? 'disable' : 'enable', id: p.id})));
          tlacitka.appendChild(tlacitko('Aktualizovat', () => akce({akce: 'update', id: p.id})));
          tlacitka.appendChild(tlacitko('Odinstalovat', () => confirm('Odinstalovat „' + jmeno + '“?') && akce({akce: 'uninstall', id: p.id}), 'danger'));
        } else {
          tlacitka.appendChild(tlacitko('Nainstalovat všude', () => akce({akce: 'install', id: p.id})));
        }
        obsah.appendChild(r);
      }
      // Katalog
      obsah.appendChild(el('div', 'set-title plg-podnadpis', 'Katalog (' + data.available.length + ')'));
      const hledat = el('input', 'set-input plg-hledat');
      hledat.placeholder = 'Hledat plugin — třeba „figma", „github", „design"…';
      obsah.appendChild(hledat);
      const seznam = el('div', 'plg-seznam');
      obsah.appendChild(seznam);
      const vypis = () => {
        const q = hledat.value.trim().toLowerCase();
        const hit = data.available.filter((p) => !q || (p.name + ' ' + p.description + ' ' + p.marketplace).toLowerCase().includes(q));
        seznam.textContent = '';
        for (const p of hit.slice(0, 60)) {
          const {r, tlacitka} = radek(p.name, p.description, p.marketplace + ' · ' + kolik(p.installs) + ' instalací');
          if (p.installed && data.installed.some((i) => i.id === p.id && i.scope === 'user')) {
            tlacitka.appendChild(el('span', 'set-ok', '✓ Nainstalováno'));
          } else {
            tlacitka.appendChild(tlacitko('Nainstalovat', () => akce({akce: 'install', id: p.id}), 'primary'));
          }
          seznam.appendChild(r);
        }
        if (hit.length > 60) seznam.appendChild(el('div', 'set-note', 'A dalších ' + (hit.length - 60) + ' — upřesni hledání.'));
        if (!hit.length) seznam.appendChild(el('div', 'set-note', 'Nic takového v katalogu není.'));
      };
      let t = null;
      hledat.oninput = () => { clearTimeout(t); t = setTimeout(vypis, 150); };
      vypis();
      // Marketplace
      obsah.appendChild(el('div', 'set-title plg-podnadpis', 'Katalogy (marketplace)'));
      for (const m of data.marketplaces) {
        const {r, tlacitka} = radek(m.name, '', m.repo || m.url || m.source || '');
        obsah.appendChild(r);
      }
      const pridat = el('div', 'set-row');
      const zdroj = el('input', 'set-input');
      zdroj.placeholder = 'GitHub owner/repo nebo adresa katalogu';
      pridat.append(zdroj, tlacitko('Přidat katalog', () => zdroj.value.trim() && akce({akce: 'marketplace-add', zdroj: zdroj.value.trim()})));
      pridat.appendChild(tlacitko('Aktualizovat katalogy', () => akce({akce: 'marketplace-update'})));
      obsah.appendChild(pridat);
    }
    nacti(false);
    return wrap;
  }

  let vypnutoNekdy = false;       // vypnutý plugin zmizí až po obnovení okna

  function appka() {
    const wrap = el('div');
    wrap.appendChild(el('div', 'set-note plg-varovani',
      '⚠ Plugin appky běží uvnitř aplikace se stejnými právy jako ty — může číst chaty, ' +
      'posílat zprávy Claudovi a spouštět příkazy. Zapni jen plugin, kterému věříš.'));
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
    start, registruj, sekce,
    akce: () => akceSeznam.slice(),
  };
})(window);
