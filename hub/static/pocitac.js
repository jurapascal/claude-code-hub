/* Claude ze serveru na tomhle počítači (hub/pocitac.py, gateway/pocitac.py).
 *
 * Claude v prostoru na serveru může sahat i na počítač, ze kterého se k prostoru
 * přihlásilo — když to člověk na tom počítači povolí. Tenhle modul je všechno,
 * co z toho je vidět:
 *
 *  - `panel(io)`       na počítači v Nastavení → Účet: volba přístupu (vypnuto /
 *                      jen čtení / plný), stav spojení a co Claude naposledy dělal.
 *  - `ask(io, opts)`   karta s touž volbou. Ukáže se jednou, před prvním
 *                      přechodem do prostoru (server.js, go), a když se okno
 *                      ze serveru vrátí s `?mode=pocitac`.
 *  - `serverBlock(io)` v prostoru v Nastavení → Účet: které počítače jsou
 *                      připojené a cesta, jak to zapnout.
 *  - `chip(io)`        štítek v hlavičce prostoru, dokud je nějaký počítač
 *                      připojený. Hlídá i úkoly na později: jakmile si je
 *                      počítač převezme, ukáže to kartou (`ukolyNotice`).
 *  - `ukol(io, msg)`   na počítači: hláška od hubu o úkolu ze serveru (převzatý
 *                      čeká na potvrzení → karta, spuštěný → hláška).
 *  - `pending(io)`     na počítači po načtení: čekající úkoly ze serveru.
 *
 * Volba přístupu se ukládá jen na počítači (POST /api/pocitac). Prostor na
 * serveru ji změnit neumí — tlačítko tam jen vrátí okno sem.
 */
'use strict';

(function (global) {

  const LEVELS = [
    ['', 'Vypnuto', 'Claude ze serveru na tenhle počítač nesahá.'],
    ['cteni', 'Jen čtení',
     'Prochází složky, čte a hledá soubory a kopíruje je na server. Nic nezmění ani nespustí.'],
    ['vse', 'Plný přístup',
     'Navíc zapisuje a upravuje soubory a spouští příkazy — jako Claude Code spuštěný tady.'],
  ];

  const OPS = {info: 'zjistil údaje', ls: 'prošel složku', read: 'četl', find: 'hledal v',
               download: 'stáhl na server', write: 'zapsal', edit: 'upravil',
               upload: 'nahrál', run: 'spustil'};

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function icon(name) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('class', 'ico');
    const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
    use.setAttribute('href', '#' + name);
    svg.appendChild(use);
    return svg;
  }

  function ago(seconds) {
    const s = Math.max(0, Math.round(seconds));
    if (s < 45) return 'právě teď';
    if (s < 3600) return 'před ' + Math.round(s / 60) + ' min';
    if (s < 86400) return 'před ' + Math.round(s / 3600) + ' h';
    return 'před ' + Math.round(s / 86400) + ' dny';
  }

  function levelLabel(level) {
    return (LEVELS.find(([id]) => id === level) || LEVELS[0])[1];
  }

  /* Tři dlaždice. `onPick(level)` dostane volbu; vrací element s .set(level). */
  function tiles(current, onPick) {
    const wrap = el('div', 'onb-tiles pc-tiles');
    const buttons = [];
    for (const [id, title, text] of LEVELS) {
      const b = el('button', 'onb-tile');
      b.type = 'button';
      b.dataset.level = id;
      b.appendChild(el('span', 'onb-tile-t', title));
      b.appendChild(el('span', 'onb-tile-s', text));
      b.onclick = () => onPick(id);
      buttons.push(b);
      wrap.appendChild(b);
    }
    wrap.set = (level) => {
      for (const b of buttons) b.classList.toggle('on', b.dataset.level === level);
    };
    wrap.set(current);
    return wrap;
  }

  function stamp(ts) {
    if (!ts) return '';
    const d = new Date(ts * 1000);
    return d.getDate() + '. ' + (d.getMonth() + 1) + '. ' +
      d.getHours() + ':' + String(d.getMinutes()).padStart(2, '0');
  }

  const TRUST_NOTE =
    'Platí jen pro tvůj prostor a jen dokud je tady appka otevřená. Každý přístup se ' +
    'zapíše do logu a posledních pár uvidíš v Nastavení → Účet. Na počítač tak ' +
    'dosáhne i ten, kdo spravuje server — zapínej jen u serveru, kterému věříš.';

  /* ── na počítači: Nastavení → Účet ───────────────────────────────────────── */
  function panel(io) {
    const box = el('div', 'acc-sec pc-panel');
    box.appendChild(el('div', 'set-title', 'Claude ze serveru na tomhle počítači'));
    box.appendChild(el('div', 'set-note',
      'Claude ve tvém prostoru na serveru může sahat i sem — na soubory a programy ' +
      'na tomhle počítači. ' + TRUST_NOTE));
    const cfg = (io.state && io.state.config) || {};
    const choice = tiles(cfg.pocitac_access || '', pick);
    box.appendChild(choice);
    const line = el('div', 'pc-state');
    box.appendChild(line);
    const recent = el('div', 'pc-recent');
    box.appendChild(recent);
    const tasks = el('div', 'pc-recent');
    box.appendChild(tasks);

    let timer = null;
    let busy = false;

    async function pick(level) {
      if (busy) return;
      busy = true;
      choice.set(level);
      try {
        const st = await io.api('pocitac', {access: level});
        if (st.error) throw new Error(st.error);
        cfg.pocitac_access = st.access;
        cfg.pocitac_asked = true;
        draw(st);
        io.toast(level ? 'Claude ze serveru teď na tenhle počítač dosáhne (' +
                         levelLabel(level).toLowerCase() + ').'
                       : 'Claude ze serveru na tenhle počítač už nesahá.');
      } catch (e) {
        choice.set(cfg.pocitac_access || '');
        io.toast('Nepovedlo se: ' + e.message);
      }
      busy = false;
      schedule(800);
    }

    function draw(st) {
      choice.set(st.access || '');
      line.textContent = '';
      line.className = 'pc-state';
      const server = st.server || 'server';
      if (!st.access) {
        line.hidden = true;
      } else {
        line.hidden = false;
        if (st.state === 'online') {
          line.appendChild(el('span', 'set-ok', '✓ Připojeno k ' + server));
          line.appendChild(document.createTextNode(
            ' — Claude ve tvém prostoru na tenhle počítač (' + st.name + ') dosáhne.'));
        } else if (st.state === 'connecting') {
          line.appendChild(el('span', 'set-dim', 'Připojuji se k ' + server + '…'));
          if (st.note) line.appendChild(el('div', 'set-dim', st.note));
        } else {
          line.appendChild(el('span', 'set-warn', '! ' + (st.note || 'Most neběží.')));
        }
      }
      drawTasks(st.ukoly || []);
      recent.textContent = '';
      const rows = st.recent || [];
      if (!rows.length) return;
      recent.appendChild(el('div', 'set-dim pc-recent-t', 'Naposledy Claude ze serveru:'));
      const now = Date.now() / 1000;
      for (const r of rows) {
        const row = el('div', 'pc-row' + (r.ok ? '' : ' bad'));
        row.appendChild(el('span', 'pc-mark', r.ok ? '✓' : '✗'));
        row.appendChild(el('span', 'pc-op', OPS[r.op] || r.op));
        row.appendChild(el('code', 'pc-what', r.what));
        row.appendChild(el('span', 'pc-when', ago(now - r.at)));
        if (!r.ok && r.error) row.title = r.error;
        recent.appendChild(row);
      }
    }

    /* Úkoly, které Claude ze serveru nechal na později (hub/pocitac.py). */
    function drawTasks(list) {
      tasks.textContent = '';
      if (!list.length) return;
      tasks.appendChild(el('div', 'set-dim pc-recent-t', 'Úkoly ze serveru:'));
      for (const u of list.slice(0, 8)) {
        const row = el('div', 'pc-row pc-task');
        row.appendChild(el('span', 'pc-op', UKOL_LABEL[u.state] || u.state));
        row.appendChild(el('span', 'pc-what', u.title));
        row.appendChild(el('span', 'pc-when', stamp(u.received)));
        if (u.state === 'ceka') {
          const b = el('button', 'btn ghost pc-task-btn', 'Zobrazit');
          b.onclick = () => card(io, u);
          row.appendChild(b);
        }
        tasks.appendChild(row);
      }
    }

    async function load() {
      timer = null;
      if (!box.isConnected) return;          // nastavení se zavřelo
      try { draw(await io.api('pocitac')); } catch (_) { /* příště */ }
      schedule(4000);
    }

    function schedule(ms) {
      clearTimeout(timer);
      timer = setTimeout(load, ms);
    }

    schedule(0);
    return box;
  }

  /* ── na počítači: karta s volbou ─────────────────────────────────────────── */
  /* opts.back = okno přišlo ze serveru, hlavní tlačítko ho tam vrátí.
     Resolve s volbou (i nezměněnou). */
  function ask(io, opts) {
    opts = opts || {};
    const cfg = (io.state && io.state.config) || {};
    // Kdo se ještě nerozhodl, má předvybraný plný přístup — kvůli němu tahle
    // funkce vznikla. Potvrdit ho ale musí sám.
    let level = cfg.pocitac_asked ? (cfg.pocitac_access || '') : 'vse';
    return new Promise((resolve) => {
      const root = el('div', 'onb pc-ask');
      const boxEl = el('div', 'onb-box');
      const head = el('div', 'onb-head');
      const mark = el('span', 'onb-mark');
      mark.appendChild(icon('i-laptop'));
      head.appendChild(mark);
      const titles = el('div');
      titles.appendChild(el('div', 'onb-title', 'Claude ze serveru na tomhle počítači'));
      titles.appendChild(el('div', 'onb-sub',
        global.HubServer ? global.HubServer.hostOf(cfg.gw_server || '') : ''));
      head.appendChild(titles);
      boxEl.appendChild(head);

      const body = el('div', 'onb-body');
      body.appendChild(el('p', 'onb-lead',
        'Ve svém prostoru na serveru máš vlastního Clauda. Může dosáhnout i sem — na ' +
        'soubory a programy na tomhle počítači. Kolik smí?'));
      const choice = tiles(level, (id) => { level = id; choice.set(id); });
      body.appendChild(choice);
      body.appendChild(el('p', 'onb-note pc-trust', TRUST_NOTE +
        ' Změníš to kdykoli v Nastavení → Účet.'));
      const err = el('div', 'srv-status err');
      err.hidden = true;
      body.appendChild(err);
      boxEl.appendChild(body);

      const foot = el('div', 'onb-foot');
      foot.appendChild(el('span', 'spacer'));
      const ok = el('button', 'btn primary', opts.back ? 'Uložit a zpět do prostoru' : 'Pokračovat');
      foot.appendChild(ok);
      boxEl.appendChild(foot);
      root.appendChild(boxEl);
      document.body.appendChild(root);
      ok.focus();

      ok.onclick = async () => {
        ok.disabled = true;
        try {
          const st = await io.api('pocitac', {access: level});
          if (st.error) throw new Error(st.error);
          cfg.pocitac_access = st.access;
          cfg.pocitac_asked = true;
          root.remove();
          resolve(st.access);
        } catch (e) {
          err.textContent = 'Uložit se nepovedlo: ' + e.message;
          err.hidden = false;
          ok.disabled = false;
        }
      };
    });
  }

  /* ── na počítači: úkoly ze serveru ────────────────────────────────────────── */
  const UKOL_LABEL = {ceka: 'čeká', spusteno: 'spuštěný', zahozeno: 'zahozený'};
  const shown = new Set();            // úkoly, které mají kartu nebo ji měly

  /* Karta s úkolem, který Claude ze serveru nechal na později. Spustí ho
     člověk tady — s přístupem jen ke čtení se sám nespustí. */
  function card(io, u) {
    const open = document.querySelector('.pc-ukol');
    if (open) open.remove();
    shown.add(u.id);
    const root = el('div', 'onb pc-ukol');
    const box = el('div', 'onb-box');
    const head = el('div', 'onb-head');
    const mark = el('span', 'onb-mark');
    mark.appendChild(icon('i-laptop'));
    head.appendChild(mark);
    const titles = el('div');
    titles.appendChild(el('div', 'onb-title', 'Úkol od Clauda ze serveru'));
    titles.appendChild(el('div', 'onb-sub',
      [u.server, u.created && 'zadáno ' + stamp(u.created)].filter(Boolean).join(' · ')));
    head.appendChild(titles);
    box.appendChild(head);

    const body = el('div', 'onb-body');
    body.appendChild(el('div', 'pc-ukol-name', u.title));
    body.appendChild(el('div', 'pc-ukol-text', u.text));
    if (u.folder) body.appendChild(el('div', 'pc-ukol-meta', 'Složka: ' + u.folder));
    if ((u.files || []).length) {
      body.appendChild(el('div', 'pc-ukol-meta', 'Přílohy: ' + u.files.join(', ')));
    }
    body.appendChild(el('p', 'onb-note',
      'Spustí se v novém tabu s Claude Code tady na počítači. Claude si zadání přečte ' +
      'a než začne, shrne, co udělá.'));
    const err = el('div', 'srv-status err');
    err.hidden = true;
    body.appendChild(err);
    box.appendChild(body);

    const foot = el('div', 'onb-foot');
    const drop = el('button', 'btn ghost', 'Zahodit');
    const later = el('button', 'btn ghost', 'Později');
    const run = el('button', 'btn primary', 'Spustit');
    foot.append(drop, later, el('span', 'spacer'), run);
    box.appendChild(foot);
    root.appendChild(box);
    document.body.appendChild(root);
    run.focus();

    async function act(action, done) {
      run.disabled = drop.disabled = true;
      try {
        const r = await io.api('pocitac', {action, id: u.id});
        if (r.error) throw new Error(r.error);
        root.remove();
        done(r);
        pending(io);                         // další čekající, když nějaký je
      } catch (e) {
        err.textContent = 'Nepovedlo se: ' + e.message;
        err.hidden = false;
        run.disabled = drop.disabled = false;
      }
    }
    run.onclick = () => act('ukol-spustit', (r) => {
      if (r.tab && io.focusTab) io.focusTab(r.tab.id);
    });
    drop.onclick = () => act('ukol-zahodit', () => io.toast('Úkol zahozen.'));
    later.onclick = () => {
      root.remove();
      io.toast('Úkol počká — najdeš ho v Nastavení → Účet.');
    };
  }

  /* Po načtení okna: čekající úkoly. `opts.focus` = okno se vrátilo ze
     serveru kvůli úkolu — když už běží, přepne se na jeho tab. */
  async function pending(io, opts) {
    opts = opts || {};
    let st;
    try { st = await io.api('pocitac'); } catch (_) { return; }
    const list = st.ukoly || [];
    const waiting = list.filter((u) => u.state === 'ceka' && !shown.has(u.id));
    if (waiting.length) return card(io, waiting[waiting.length - 1]);   // nejstarší
    if (opts.focus && io.focusTab) {
      const running = list.find((u) => u.state === 'spusteno' && u.tab);
      if (running) io.focusTab(running.tab);
    }
  }

  /* Hláška od hubu (websocket): úkol ze serveru dorazil nebo se spustil. */
  async function ukol(io, msg) {
    if (msg.state === 'spusteno') {
      io.toast('Úkol ze serveru „' + msg.title + '“ běží v novém tabu.');
      if (msg.auto && io.focusTab) io.focusTab(msg.tab, {idle: true});
      return;
    }
    if (msg.state === 'ceka' && !shown.has(msg.id) && !document.querySelector('.pc-ukol')) {
      pending(io);
    }
  }

  /* ── v prostoru na serveru ───────────────────────────────────────────────── */
  async function gwState() {
    const r = await fetch('/gw/pocitac', {credentials: 'same-origin'});
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return r.json();
  }

  async function computers() {
    const data = await gwState();
    return (data.computers || []).filter((c) => c.online && c.access);
  }

  /* Karta v prostoru: počítač si úkol převzal. Jednou na úkol a stav — co
     už člověk viděl, drží localStorage (bez něj se ukáže jen v tomhle načtení). */
  const SEEN_KEY = 'hub.ukolySeen';
  const seenHere = new Set();

  function seen(key) {
    if (seenHere.has(key)) return true;
    try { return (JSON.parse(localStorage.getItem(SEEN_KEY) || '[]')).includes(key); }
    catch (_) { return false; }
  }

  function markSeen(key) {
    seenHere.add(key);
    try {
      const list = JSON.parse(localStorage.getItem(SEEN_KEY) || '[]').filter((k) => k !== key);
      list.push(key);
      localStorage.setItem(SEEN_KEY, JSON.stringify(list.slice(-50)));
    } catch (_) { /* jen pohodlí */ }
  }

  function ukolyNotice(io, list) {
    if (document.querySelector('.pc-ukol')) return;
    const now = Date.now() / 1000;
    const u = (list || []).find((x) => (x.state === 'prevzato' || x.state === 'spusteno') &&
                                       now - x.changed < 30 * 60 && !seen(x.id + ':' + x.state));
    if (!u) return;
    markSeen(u.id + ':' + u.state);
    const started = u.state === 'spusteno';
    const root = el('div', 'onb pc-ukol');
    const box = el('div', 'onb-box');
    const head = el('div', 'onb-head');
    const mark = el('span', 'onb-mark');
    mark.appendChild(icon('i-laptop'));
    head.appendChild(mark);
    const titles = el('div');
    titles.appendChild(el('div', 'onb-title',
      started ? 'Úkol běží na počítači' : 'Úkol čeká na počítači'));
    titles.appendChild(el('div', 'onb-sub', u.by || ''));
    head.appendChild(titles);
    box.appendChild(head);
    const body = el('div', 'onb-body');
    body.appendChild(el('div', 'pc-ukol-name', u.title));
    body.appendChild(el('p', 'onb-lead', started
      ? 'Počítač ' + (u.by || '') + ' si úkol převzal a Claude na něm pracuje v novém tabu. ' +
        'Když bude potřebovat souhlas, zeptá se tam.'
      : 'Počítač ' + (u.by || '') + ' si úkol převzal. Má zapnutý přístup jen ke čtení, ' +
        'takže se nespustí sám — potvrď ho v appce na počítači.'));
    box.appendChild(body);
    const foot = el('div', 'onb-foot');
    const close = el('button', 'btn ghost', 'Zavřít');
    close.onclick = () => root.remove();
    foot.append(close, el('span', 'spacer'));
    const hs = global.HubServer;
    if (hs && hs.localBack() && hs.appAtLeast('2.15.0')) {
      const go = el('button', 'btn primary', started ? 'Přejít na počítač' : 'Potvrdit na počítači');
      go.title = 'Okno přejde do appky na počítači. Do prostoru se vrátíš v Nastavení → Účet.';
      go.onclick = () => hs.backTo('ukoly');
      foot.appendChild(go);
    }
    box.appendChild(foot);
    root.appendChild(box);
    document.body.appendChild(root);
  }

  function lastText(c) {
    if (!c.last) return '';
    return (OPS[c.last.op] || c.last.op) + ' „' + c.last.summary + '" ' + ago(c.last.ago);
  }

  function serverBlock(io) {
    const box = el('div', 'acc-sec pc-panel');
    box.appendChild(el('div', 'set-title', 'Tvůj počítač'));
    const body = el('div');
    body.appendChild(el('div', 'set-note', 'Zjišťuji, jestli je připojený…'));
    box.appendChild(body);

    gwState().then((data) => {
      const list = (data.computers || []).filter((c) => c.online && c.access);
      body.textContent = '';
      if (list.length) {
        body.appendChild(el('div', 'set-note',
          'Claude tady v prostoru sahá i na tyhle počítače (nástroje mcp__pocitac__*) — ' +
          'řekni mu třeba „podívej se na počítači do Stažených".'));
        for (const c of list) {
          const row = el('div', 'pc-comp');
          row.appendChild(el('span', 'set-ok', '● ' + c.name));
          row.appendChild(el('span', 'set-dim',
            [c.system, c.access_label, lastText(c) && 'naposledy ' + lastText(c)]
              .filter(Boolean).join(' · ')));
          body.appendChild(row);
        }
      } else {
        body.appendChild(el('div', 'set-note',
          'Claude v prostoru teď na žádný tvůj počítač nedosáhne. Zapíná se v appce ' +
          'Claude Code Hub na počítači (Nastavení → Účet → Claude ze serveru na tomhle ' +
          'počítači) a funguje, dokud je appka otevřená. Co má počkat, než se počítač ' +
          'připojí, řekni Claudovi — nechá mu úkol a Claude na počítači ho pak dodělá.'));
      }
      const tasks = data.ukoly || [];
      if (tasks.length) {
        body.appendChild(el('div', 'set-dim pc-recent-t', 'Úkoly na později:'));
        for (const u of tasks.slice(0, 8)) {
          const row = el('div', 'pc-row pc-task');
          row.appendChild(el('span', 'pc-op', u.state_label + (u.by ? ' (' + u.by + ')' : '')));
          row.appendChild(el('span', 'pc-what', u.title));
          row.appendChild(el('span', 'pc-when', stamp(u.changed || u.created)));
          body.appendChild(row);
        }
      }
      if (global.HubServer && global.HubServer.localBack() &&
          !global.HubServer.appAtLeast('2.14.1')) {
        body.appendChild(global.HubServer.oldAppNote());
      } else if (global.HubServer && global.HubServer.localBack()) {
        const row = el('div', 'set-row');
        const b = el('button', 'btn ghost',
          list.length ? 'Změnit na tomhle počítači' : 'Zapnout na tomhle počítači');
        b.title = 'Okno se na chvíli vrátí do appky na počítači, tam zvolíš přístup ' +
          'a pak se vrátíš sem.';
        b.onclick = () => global.HubServer.backTo('pocitac');
        row.appendChild(b);
        body.appendChild(row);
      }
    }, () => {
      body.textContent = '';
      body.appendChild(el('div', 'set-note', 'Server most na počítač zatím neumí.'));
    });
    return box;
  }

  /* Štítek v hlavičce: viditelný jen s připojeným počítačem. */
  function chip(io) {
    const host = document.querySelector('.topbar-title');
    if (!host || host.querySelector('.topbar-pc')) return;
    const b = el('span', 'topbar-pc plain');
    b.hidden = true;
    b.appendChild(icon('i-laptop'));
    const label = el('span');
    b.appendChild(label);
    /* Cedulka, ne tlačítko: říká, na který počítač Claude z prostoru
       dosáhne. Klikalo se tu do nastavení, což s tím jménem nesouvisí —
       podrobnosti jsou v popisku, který se ukáže po najetí. */
    host.appendChild(b);

    let timer = null;
    async function tick() {
      clearTimeout(timer);
      let wait = 15000;
      try {
        const data = await gwState();
        const list = (data.computers || []).filter((c) => c.online && c.access);
        ukolyNotice(io, data.ukoly);
        b.hidden = !list.length;
        if (list.length) {
          label.textContent = list.length === 1 ? list[0].name : list.length + ' počítače';
          b.title = list.map((c) => 'Claude v prostoru dosáhne na počítač ' + c.name +
            ' (' + c.access_label + ').' + (c.last ? ' Naposledy ' + lastText(c) + '.' : ''))
            .join('\n');
          // Zvýraznit, když Claude na počítači právě něco dělá; mezitím se
          // ptát častěji, ať štítek nezaostává.
          const newest = Math.min(...list.map((c) => (c.last ? c.last.ago : 1e9)));
          b.classList.toggle('busy', newest < 10);
          wait = newest < 60 ? 5000 : 15000;
        }
      } catch (_) {
        b.hidden = true;
        wait = 5 * 60 * 1000;                 // starší brána most nezná
      }
      timer = setTimeout(tick, wait);
    }
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') tick();
    });
    tick();
  }

  global.HubPocitac = {LEVELS, panel, ask, serverBlock, chip, levelLabel, ukol, pending};

})(window);
