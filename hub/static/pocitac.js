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
 *                      připojený.
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

  /* ── v prostoru na serveru ───────────────────────────────────────────────── */
  async function computers() {
    const r = await fetch('/gw/pocitac', {credentials: 'same-origin'});
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const data = await r.json();
    return (data.computers || []).filter((c) => c.online && c.access);
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

    computers().then((list) => {
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
          'počítači) a funguje, dokud je appka otevřená.'));
      }
      if (global.HubServer && global.HubServer.localBack()) {
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
    const b = el('button', 'topbar-pc');
    b.type = 'button';
    b.hidden = true;
    b.appendChild(icon('i-laptop'));
    const label = el('span');
    b.appendChild(label);
    b.onclick = () => global.HubSettings.open({...io, state: io.state, tab: 'ucet'});
    host.appendChild(b);

    let timer = null;
    async function tick() {
      clearTimeout(timer);
      let wait = 15000;
      try {
        const list = await computers();
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

  global.HubPocitac = {LEVELS, panel, ask, serverBlock, chip, levelLabel};

})(window);
