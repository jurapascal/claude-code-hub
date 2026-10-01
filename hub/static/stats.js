/* Statistiky používání — z toho, co si Claude Code sám ukládá na disk.
 *
 * Všechny grafy tu měří jedno a totéž: velikost. Žádná série se nerozlišuje
 * barvou, takže tu není co plést a nepotřebují legendu — nadpis říká, co je
 * na svislé ose, a hodnotu ukáže popisek po najetí. Barva je jantarová
 * aplikace ve dvou krocích (`--chart`), jeden pro světlý a jeden pro tmavý
 * podklad: stejný odstín na obojím by na jednom z nich neměl dost kontrastu.
 */
'use strict';

(function (global) {

  let io = null;
  let root = null;
  let data = null;

  const DNY = ['po', 'út', 'st', 'čt', 'pá', 'so', 'ne'];

  /* Jednoduchý režim (výchozí) × Pro pokročilé (hub.js → window.HUB_ADVANCED).
     V jednoduchém se ukazuje jen to, čemu rozumí každý — zprávy, rozhovory,
     dny s prací. Tokeny, cache, GitHub a cesty na disku jsou pro pokročilé. */
  const pokrocile = () => !!global.HUB_ADVANCED;

  // Tvar slova podle počtu: 1 rozhovor, 2–4 rozhovory, 5+ rozhovorů.
  function tvar(n, jedna, dve, pet) {
    return n === 1 ? jedna : n >= 2 && n <= 4 ? dve : pet;
  }

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  /* Velká čísla se nekreslí jako graf — jedno číslo je samo o sobě sdělení. */
  function tile(value, label, note) {
    const box = el('div', 'st-tile');
    box.appendChild(el('div', 'st-value', value));
    box.appendChild(el('div', 'st-label', label));
    if (note) box.appendChild(el('div', 'st-note', note));
    return box;
  }

  function cislo(n) {
    if (n >= 1e9) return (n / 1e9).toFixed(1).replace('.', ',') + ' mld';
    if (n >= 1e6) return (n / 1e6).toFixed(1).replace('.', ',') + ' M';
    if (n >= 1e3) return Math.round(n / 1e3) + ' tis.';
    return String(n);
  }

  /* Sloupcový graf: tenké značky posazené na základně, mezera 2 px, hodnota
     po najetí. Popisek se píše jen pod vybrané sloupce, ne pod každý. */
  function bars(items, opts) {
    opts = opts || {};
    const max = Math.max(1, ...items.map(i => i.value));
    const wrap = el('div', 'st-chart');
    const plot = el('div', 'st-plot');
    for (const item of items) {
      const col = el('div', 'st-col');
      const bar = el('div', 'st-bar');
      bar.style.height = Math.max(item.value ? 2 : 0, item.value / max * 100) + '%';
      if (opts.dim && opts.dim(item)) bar.classList.add('dim');
      col.appendChild(bar);
      col.title = `${item.full || item.label}: ${cislo(item.value)}${opts.unit || ''}`;
      if (item.tick) col.appendChild(el('span', 'st-tick', item.tick));
      plot.appendChild(col);
    }
    wrap.appendChild(plot);
    return wrap;
  }

  function section(title, note) {
    const box = el('div', 'st-sec');
    box.appendChild(el('div', 'set-title', title));
    if (note) box.appendChild(el('div', 'set-note', note));
    return box;
  }

  async function open(opts) {
    io = opts;
    root = el('div', 'onb');
    root.innerHTML = `
      <div class="onb-box st-box">
        <div class="onb-head">
          <span class="onb-mark"></span>
          <div>
            <div class="onb-title">Statistiky</div>
            <div class="onb-sub">Z toho, co si Claude Code ukládá na disk</div>
          </div>
        </div>
        <div class="onb-body"></div>
        <div class="onb-foot">
          <button class="btn ghost st-refresh">Přepočítat</button>
          <span class="spacer"></span>
          <button class="btn primary st-close">Zavřít</button>
        </div>
      </div>`;
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('width', '34');
    svg.setAttribute('height', '34');
    const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
    use.setAttribute('href', '#i-hub');
    svg.appendChild(use);
    root.querySelector('.onb-mark').appendChild(svg);
    root.querySelector('.st-close').onclick = close;
    root.querySelector('.st-refresh').onclick = () => load(true);
    // Zavřít jen klikem, který na pozadí i začal: výběr textu tažením ven
    // z okna končí puštěním tlačítka na pozadí a prohlížeč to hlásí jako klik.
    let downOutside = false;
    root.addEventListener('pointerdown', (ev) => { downOutside = ev.target === root; });
    root.addEventListener('click', (ev) => { if (ev.target === root && downOutside) close(); });
    document.body.appendChild(root);
    load(false);
  }

  async function load(refresh) {
    const body = root.querySelector('.onb-body');
    body.textContent = '';
    const status = el('div', 'set-status busy', 'Počítám…');
    body.appendChild(status);
    for (;;) {
      let res;
      try {
        res = await io.api('stats' + (refresh ? '?refresh=1' : ''));
      } catch (err) {
        console.warn('stats:', err);
        status.className = 'set-status warn';
        status.textContent = pokrocile() ? 'Nepovedlo se: ' + err.message
          : 'Statistiky se teď nepodařilo spočítat, zkus to za chvíli znovu.';
        return;
      }
      refresh = false;
      if (res.running) {
        status.textContent = res.step || 'Počítám…';
        await new Promise(r => setTimeout(r, 1000));
        continue;
      }
      data = res;
      render();
      return;
    }
  }

  function render() {
    const body = root.querySelector('.onb-body');
    body.textContent = '';
    const t = data.tokens || {};
    const pro = pokrocile();
    root.querySelector('.onb-sub').textContent = pro
      ? 'Z toho, co si Claude Code ukládá na disk'
      : 'Jak často s Claudem pracuješ';

    // ── velká čísla ──────────────────────────────────────────────────────
    const tiles = el('div', 'st-tiles');
    if (pro) {
      tiles.appendChild(tile(cislo(t.out || 0), 'napsaných tokenů',
        `z toho ${cislo(t.think || 0)} přemýšlení`));
      tiles.appendChild(tile(cislo(data.prompts || 0), 'odeslaných zpráv',
        `${cislo(t.answers || 0)} odpovědí`));
      tiles.appendChild(tile(String(data.sessions || 0), 'sezení',
        `${data.active_days || 0} dnů s prací`));
      tiles.appendChild(tile(cislo(t.cache_r || 0), 'přečteno z cache',
        `zapsáno ${cislo(t.cache_w || 0)}`));
      if (data.cost) tiles.appendChild(tile(dolary(data.cost), 'odhad ceny', 'podle ceníku API, na předplatném se neplatí'));
    } else {
      const zprav = data.prompts || 0;
      const odpovedi = t.answers || 0;
      const rozhovoru = data.sessions || 0;
      const dni = data.active_days || 0;
      tiles.appendChild(tile(cislo(zprav),
        tvar(zprav, 'odeslaná zpráva', 'odeslané zprávy', 'odeslaných zpráv'),
        odpovedi ? `${cislo(odpovedi)} ${tvar(odpovedi, 'odpověď', 'odpovědi', 'odpovědí')} od Clauda` : ''));
      tiles.appendChild(tile(String(rozhovoru),
        tvar(rozhovoru, 'rozhovor', 'rozhovory', 'rozhovorů')));
      tiles.appendChild(tile(String(dni), tvar(dni, 'den s prací', 'dny s prací', 'dní s prací')));
    }
    body.appendChild(tiles);

    // ── denní doba ───────────────────────────────────────────────────────
    const hours = (data.hours || []).map((v, h) => ({
      value: v, label: h + ':00', full: `${h}:00–${h}:59`,
      tick: h % 6 === 0 ? String(h) : '',
    }));
    const peak = hours.reduce((a, b) => (b.value > a.value ? b : a), hours[0] || {});
    const s1 = section('Kdy píšeš', peak && peak.value
      ? `Nejvíc mezi ${peak.full} — ${peak.value} ${tvar(peak.value, 'zpráva', 'zprávy', 'zpráv')}.` : '');
    s1.appendChild(bars(hours, {unit: ' zpráv'}));
    body.appendChild(s1);

    // ── dny v týdnu ──────────────────────────────────────────────────────
    const wd = (data.weekdays || []).map((v, i) => ({
      value: v, label: DNY[i], full: DNY[i], tick: DNY[i],
    }));
    const s2 = section('Dny v týdnu');
    s2.appendChild(bars(wd, {unit: ' zpráv', dim: (i) => i.label === 'so' || i.label === 'ne'}));
    body.appendChild(s2);

    // ── poslední dny ─────────────────────────────────────────────────────
    const days = (data.days || []).slice(-60);
    if (days.length) {
      const items = days.map((d, i) => ({
        value: d.prompts, label: d.day, full: d.day,
        tick: i === 0 || i === days.length - 1 ? d.day.slice(5) : '',
      }));
      const s3 = section('Posledních ' + days.length + ' dnů',
        'Počet odeslaných zpráv za den.');
      s3.appendChild(bars(items, {unit: ' zpráv'}));
      body.appendChild(s3);
    }

    // ── projekty ─────────────────────────────────────────────────────────
    // Pokročilí vidí tokeny, ostatní počet zpráv — to je, co člověk napsal.
    const kolik = (p) => (pro ? p.out : p.prompts) || 0;
    const projects = (data.projects || []).slice();
    if (!pro) projects.sort((a, b) => kolik(b) - kolik(a));
    if (projects.length) {
      const s4 = section(pro ? 'Kde to padá' : 'Na čem pracuješ',
        pro ? 'Napsané tokeny podle projektu.' : 'Počet zpráv podle projektu.');
      const list = el('div', 'st-rows');
      const max = Math.max(1, ...projects.map(kolik));
      for (const p of projects) {
        const row = el('div', 'st-row');
        row.appendChild(el('span', 'st-row-name', p.name));
        const track = el('span', 'st-track');
        const fill = el('span', 'st-fill');
        fill.style.width = Math.max(1, kolik(p) / max * 100) + '%';
        track.appendChild(fill);
        row.appendChild(track);
        row.appendChild(el('span', 'st-row-val', cislo(kolik(p))));
        row.title = pro
          ? `${p.path || p.name}\n${cislo(p.out)} tokenů · ${p.prompts} zpráv · ${dolary(p.cost)}`
          : `${p.name}\n${p.prompts || 0} ${tvar(p.prompts || 0, 'zpráva', 'zprávy', 'zpráv')}`;
        list.appendChild(row);
      }
      s4.appendChild(list);
      body.appendChild(s4);
    }

    // Zbytek (GitHub, odkud se počítá) je jen pro pokročilé.
    if (!pro) return;

    // ── GitHub ───────────────────────────────────────────────────────────
    const gh = data.github || {};
    const s5 = section('GitHub', gh.ok ? '@' + gh.login : '');
    if (!gh.ok) {
      const warn = el('div', 'set-status warn', gh.detail || 'Nedostupné.');
      s5.appendChild(warn);
    } else {
      const g = el('div', 'st-tiles');
      g.appendChild(tile(String(gh.commits_year), 'commitů za rok',
        `ve ${gh.repos_touched} repozitářích`));
      g.appendChild(tile(String(gh.contributions), 'příspěvků celkem',
        `${gh.private_repos} privátních repozitářů`));
      s5.appendChild(g);
      const ghDays = (gh.days || []).slice(-60).map((d, i, arr) => ({
        value: d.count, label: d.day, full: d.day,
        tick: i === 0 || i === arr.length - 1 ? d.day.slice(5) : '',
      }));
      if (ghDays.length) s5.appendChild(bars(ghDays, {unit: ' příspěvků'}));
    }
    body.appendChild(s5);

    const foot = el('div', 'set-note',
      'Počítáno z ~/.claude — tokeny z přepisů sezení, zprávy z historie. ' +
      (data.rescanned ? `Nově přečteno ${data.rescanned} souborů.` : 'Z mezipaměti.'));
    body.appendChild(foot);
  }

  function close() {
    if (root) root.remove();
    root = null;
  }

  /* ── statistiky jednoho projektu ─────────────────────────────────────────
     Tokeny, odhad ceny, modely, dny a nejdražší sezení. Cena je podle ceníku
     API (hub/pricing.py) — na předplatném se po tokenech neplatí, je to míra,
     co projekt „váží". */
  const dolary = (c) => {
    c = Number(c) || 0;
    if (c >= 100) return Math.round(c).toLocaleString('cs-CZ') + ' $';
    if (c >= 1) return c.toFixed(1).replace('.', ',') + ' $';
    return c.toFixed(2).replace('.', ',') + ' $';
  };
  const datum = (iso) => (iso ? new Date(iso).toLocaleDateString('cs-CZ') : '–');
  const kratce = (iso) => (iso ? new Date(iso).toLocaleDateString('cs-CZ', {day: 'numeric', month: 'numeric'}) : '–');
  const jmenoModelu = (m) => String(m || '?').replace(/^claude-/, '').replace(/-(\d{8})$/, '')
    .replace(/-(\d+)-(\d+)$/, ' $1.$2').replace(/-(\d+)$/, ' $1').replace(/^\w/, (c) => c.toUpperCase());

  async function openProject(opts, project) {
    const wrap = el('div', 'onb st-projekt');
    wrap.innerHTML = `
      <div class="onb-box st-box">
        <div class="onb-head">
          <div><div class="onb-title"></div><div class="onb-sub">Využití tokenů a odhad ceny</div></div>
          <span class="spacer"></span>
          <button class="set-x" title="Zavřít">×</button>
        </div>
        <div class="onb-body"></div>
      </div>`;
    wrap.querySelector('.onb-title').textContent = project.label || project.name;
    const zavri = () => wrap.remove();
    wrap.querySelector('.set-x').onclick = zavri;
    let downOutside = false;
    wrap.addEventListener('pointerdown', (ev) => { downOutside = ev.target === wrap; });
    wrap.addEventListener('click', (ev) => { if (ev.target === wrap && downOutside) zavri(); });
    document.body.appendChild(wrap);
    const body = wrap.querySelector('.onb-body');
    body.appendChild(el('div', 'set-status busy', 'Počítám…'));
    let d;
    try {
      d = await opts.api('project-stats?path=' + encodeURIComponent(project.path));
    } catch (err) {
      body.textContent = '';
      body.appendChild(el('div', 'set-status warn', 'Nepovedlo se: ' + err.message));
      return;
    }
    body.textContent = '';
    const t = d.tokens || {};
    if (!t.answers) {
      body.appendChild(el('div', 'set-note', 'U tohohle projektu zatím nejsou žádná sezení s Claudem.'));
      return;
    }
    const tiles = el('div', 'st-tiles');
    tiles.appendChild(tile(dolary(d.cost), 'odhad ceny', 'podle ceníku API'));
    tiles.appendChild(tile(cislo(t.out || 0), 'napsaných tokenů', `z toho ${cislo(t.think || 0)} přemýšlení`));
    tiles.appendChild(tile(cislo(d.prompts || 0), tvar(d.prompts || 0, 'zpráva', 'zprávy', 'zpráv'), `${cislo(t.answers)} odpovědí`));
    tiles.appendChild(tile(String(d.sessions_count || 0), tvar(d.sessions_count || 0, 'sezení', 'sezení', 'sezení'),
      `${d.active_days || 0} dnů s prací`));
    body.appendChild(tiles);

    const s1 = section('Tokeny', 'Z čeho se cena skládá.');
    const rows = [
      ['Vstup (nový)', t.in, 'tokeny, které model četl poprvé'],
      ['Výstup', t.out, 'co Claude napsal a promyslel'],
      ['Zápis do cache', t.cache_w, 'kontext uložený pro další zprávy'],
      ['Čtení z cache', t.cache_r, 'kontext znovu použitý (levné)'],
    ];
    const max = Math.max(1, ...rows.map((r) => r[1] || 0));
    const list = el('div', 'st-rows');
    for (const [name, val, note] of rows) {
      const row = el('div', 'st-row');
      row.appendChild(el('span', 'st-row-name', name));
      const track = el('span', 'st-track');
      const fill = el('span', 'st-fill');
      fill.style.width = Math.max(1, (val || 0) / max * 100) + '%';
      track.appendChild(fill);
      row.appendChild(track);
      row.appendChild(el('span', 'st-row-val', cislo(val || 0)));
      row.title = note;
      list.appendChild(row);
    }
    s1.appendChild(list);
    body.appendChild(s1);

    if ((d.models || []).length) {
      const s2 = section('Modely', 'Odhad ceny podle toho, čím Claude odpovídal.');
      const l2 = el('div', 'st-rows');
      const mc = Math.max(0.01, ...d.models.map((m) => m.cost));
      for (const m of d.models) {
        const row = el('div', 'st-row');
        row.appendChild(el('span', 'st-row-name', jmenoModelu(m.model)));
        const track = el('span', 'st-track');
        const fill = el('span', 'st-fill');
        fill.style.width = Math.max(1, m.cost / mc * 100) + '%';
        track.appendChild(fill);
        row.appendChild(track);
        row.appendChild(el('span', 'st-row-val', dolary(m.cost)));
        row.title = `${m.model}\n${cislo(m.out)} tokenů výstupu · ${m.answers} odpovědí`;
        l2.appendChild(row);
      }
      s2.appendChild(l2);
      body.appendChild(s2);
    }

    const dny = (d.days || []);
    if (dny.length) {
      const s3 = section('Cena po dnech', `Posledních ${dny.length} dnů s prací.`);
      s3.appendChild(bars(dny.map((x, i, a) => ({
        value: x.cost, label: x.day, full: x.day, tick: i === 0 || i === a.length - 1 ? x.day.slice(5) : '',
      })), {unit: ' $'}));
      body.appendChild(s3);
    }

    if ((d.sessions || []).length) {
      const s4 = section('Nejdražší sezení', 'Jedno sezení = jeden rozhovor s Claudem.');
      const l4 = el('div', 'st-rows');
      for (const s of d.sessions.slice(0, 6)) {
        const row = el('div', 'st-row');
        const nm = el('span', 'st-row-name', kratce(s.first) + (s.last && s.last.slice(0, 10) !== (s.first || '').slice(0, 10) ? ' – ' + kratce(s.last) : ''));
        nm.style.minWidth = '110px';
        row.appendChild(nm);
        row.appendChild(el('span', 'st-track'));
        row.appendChild(el('span', 'st-row-val', dolary(s.cost)));
        row.title = `${cislo(s.out)} tokenů · ${s.answers} odpovědí\n${s.id}`;
        l4.appendChild(row);
      }
      s4.appendChild(l4);
      body.appendChild(s4);
    }
    body.appendChild(el('div', 'set-note',
      `${datum(d.first)} – ${datum(d.last)}. ${d.note || ''}`));
  }

  global.HubStats = {open, close, openProject, dolary, cislo};

})(window);
