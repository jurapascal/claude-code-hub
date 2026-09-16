/* Stopky do Clockify (hub/clockify.py) — Start/Stop u otevřeného projektu.
 *
 * Schválně mimo agenta: rychlé akce vedle jsou slash příkazy, které píše Claude
 * do terminálu, tohle je obyčejné tlačítko na backend. Žádný prompt se nikam
 * neposílá, takže měřit čas jde i v tabu, kde zrovna nic neběží.
 *
 *  - `panel(io, path)`  vykreslí blok do #clockify podle otevřeného projektu
 *  - `tick()`           přepíše jen běžící čas (každou sekundu, bez dotazu na síť)
 *
 * Projekt v Clockify se u složky vybere jednou a hub si ho pamatuje
 * (`clockify_map` v hub-config.json), takže podruhé stačí Start.
 */
'use strict';

(function (global) {

  // Stav panelu. `path` = složka projektu v otevřeném tabu, kvůli ní se
  // předvybírá projekt v Clockify a pod ní se ukládá volba.
  const S = {io: null, path: '', data: null, loading: false, at: 0};

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function hms(sec) {
    sec = Math.max(0, Math.round(sec));
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const s = sec % 60;
    const pad = (n) => String(n).padStart(2, '0');
    return (h ? h + ':' + pad(m) : pad(m)) + ':' + pad(s);
  }

  /* Kolik už běží: server pošle `seconds` k okamžiku dotazu, zbytek se
     dopočítá z hodin v prohlížeči, ať se kvůli tikání neptáme na síť. */
  function elapsed() {
    const run = S.data && S.data.running;
    if (!run) return 0;
    return run.seconds + (Date.now() - S.at) / 1000;
  }

  function projectName(id) {
    const list = (S.data && S.data.projects) || [];
    const hit = list.find((p) => p.id === id);
    return hit ? hit.name : 'projekt';
  }

  async function call(body) {
    try {
      const res = await S.io.api('clockify', Object.assign({path: S.path}, body));
      if (res && res.error) { S.io.toast(res.error); return; }
      S.data = res;
      S.at = Date.now();
      draw();
      return res;
    } catch (exc) {
      S.io.toast('Clockify: ' + exc.message);
    }
  }

  function draw() {
    const box = document.getElementById('clockify');
    if (!box) return;
    box.textContent = '';
    const data = S.data;

    // Nenastavený Clockify není chyba — jen tu stopky nemají co dělat.
    if (!data || !data.ready) {
      box.hidden = true;
      done();
      return;
    }
    box.hidden = false;

    const run = data.running;
    if (run) {
      const line = el('div', 'clk-run');
      line.appendChild(el('span', 'clk-dot'));
      line.appendChild(el('span', 'clk-proj', projectName(run.projectId)));
      const time = el('span', 'clk-time', hms(elapsed()));
      time.id = 'clk-time';
      line.appendChild(time);
      box.appendChild(line);
      if (run.description) box.appendChild(el('div', 'clk-desc', run.description));
      const stop = el('button', 'barbtn clk-stop', '■ Stop');
      stop.onclick = async () => {
        stop.disabled = true;
        const res = await call({action: 'stop'});
        if (res && res.stopped) S.io.toast('Zapsáno do Clockify: ' + hms(res.stopped));
      };
      box.appendChild(stop);
      done();
      return;
    }

    const pick = el('select', 'clk-pick');
    pick.appendChild(el('option', null, 'Vyber projekt…')).value = '';
    for (const p of data.projects) {
      const opt = el('option', null, p.name);
      opt.value = p.id;
      if (p.id === data.selected) opt.selected = true;
      pick.appendChild(opt);
    }
    // Volba se pamatuje hned, i když se Start nezmáčkne — příště je předvyplněná.
    pick.onchange = () => call({action: 'choose', project: pick.value});
    box.appendChild(pick);

    const note = el('input', 'clk-note');
    note.type = 'text';
    note.placeholder = 'na čem děláš (nepovinné)';
    box.appendChild(note);

    const start = el('button', 'barbtn clk-start', '▶ Start');
    start.onclick = async () => {
      if (!pick.value) { S.io.toast('Nejdřív vyber projekt.'); return; }
      start.disabled = true;
      await call({action: 'start', project: pick.value, description: note.value});
    };
    box.appendChild(start);
    done();
  }

  /* Panel se kreslí až po odpovědi ze sítě — hub.js podle téhle události
     dopočítá, jestli má být postranní lišta vůbec vidět. */
  function done() {
    document.dispatchEvent(new CustomEvent('clockify-drawn'));
  }

  /* Přepíše jen číslo, ne celý panel — jinak by psaní do pole přišlo o fokus. */
  function tick() {
    if (!S.data || !S.data.running) return;
    const node = document.getElementById('clk-time');
    if (node) node.textContent = hms(elapsed());
  }

  async function panel(io, path) {
    S.io = io;
    const changed = path !== S.path;
    S.path = path || '';
    if (S.data && !changed) { draw(); return; }
    if (S.loading) return;
    S.loading = true;
    try {
      await call({});
    } finally {
      S.loading = false;
    }
  }

  setInterval(tick, 1000);

  global.HubClockify = {panel, tick, hms};

})(window);
