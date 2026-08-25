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
    root.addEventListener('click', (ev) => { if (ev.target === root) close(); });
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
        status.className = 'set-status warn';
        status.textContent = 'Nepovedlo se: ' + err.message;
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

    // ── velká čísla ──────────────────────────────────────────────────────
    const tiles = el('div', 'st-tiles');
    tiles.appendChild(tile(cislo(t.out || 0), 'napsaných tokenů',
      `z toho ${cislo(t.think || 0)} přemýšlení`));
    tiles.appendChild(tile(cislo(data.prompts || 0), 'odeslaných zpráv',
      `${cislo(t.answers || 0)} odpovědí`));
    tiles.appendChild(tile(String(data.sessions || 0), 'sezení',
      `${data.active_days || 0} dnů s prací`));
    tiles.appendChild(tile(cislo(t.cache_r || 0), 'přečteno z cache',
      `zapsáno ${cislo(t.cache_w || 0)}`));
    body.appendChild(tiles);

    // ── denní doba ───────────────────────────────────────────────────────
    const hours = (data.hours || []).map((v, h) => ({
      value: v, label: h + ':00', full: `${h}:00–${h}:59`,
      tick: h % 6 === 0 ? String(h) : '',
    }));
    const peak = hours.reduce((a, b) => (b.value > a.value ? b : a), hours[0] || {});
    const s1 = section('Kdy píšeš',
      peak && peak.value ? `Nejvíc mezi ${peak.full} — ${peak.value} zpráv.` : '');
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
    const projects = data.projects || [];
    if (projects.length) {
      const s4 = section('Kde to padá', 'Napsané tokeny podle projektu.');
      const list = el('div', 'st-rows');
      const max = Math.max(1, ...projects.map(p => p.out));
      for (const p of projects) {
        const row = el('div', 'st-row');
        row.appendChild(el('span', 'st-row-name', p.name));
        const track = el('span', 'st-track');
        const fill = el('span', 'st-fill');
        fill.style.width = Math.max(1, p.out / max * 100) + '%';
        track.appendChild(fill);
        row.appendChild(track);
        row.appendChild(el('span', 'st-row-val', cislo(p.out)));
        row.title = `${p.path || p.name}\n${cislo(p.out)} tokenů · ${p.prompts} zpráv`;
        list.appendChild(row);
      }
      s4.appendChild(list);
      body.appendChild(s4);
    }

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

  global.HubStats = {open, close};

})(window);
