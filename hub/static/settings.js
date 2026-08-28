/* Nastavení — to, co průvodce nastaví napoprvé, se tu dá změnit kdykoli.
 *
 * Sekce se přepínají tlačítky vlevo a v těle je vždycky jen jedna. Jako jeden
 * dlouhý svitek se v tom ztrácelo: kvůli přepínači tabů se scrollovalo přes
 * paměť, napojení i logy. Vybraná sekce se pamatuje, takže po zavření a
 * otevření je člověk tam, kde skončil.
 *
 * Je tu i aktualizace aplikace. Schválně daleko od ⟳ v hlavičce: to jen znovu
 * přečte projekty a paměť, kdežto tohle stáhne novou verzi hubu a přeinstaluje
 * ji. Dvě různé věci, dvě různá místa, dvě různá jména.
 */
'use strict';

(function (global) {

  let io = null;
  let state = null;
  let root = null;
  let active = localStorage.getItem('hub-set-tab') || 'vzhled';

  // Pořadí je i pořadím v panelu vlevo: napřed to, co se mění nejčastěji,
  // servis (aktualizace, logy) až na konci.
  const SECTIONS = [
    ['vzhled',     'Vzhled',    'i-sun',      () => vzhled()],
    ['projekty',   'Projekty',  'i-folder',   () => projekty()],
    ['taby',       'Taby',      'i-terminal', () => taby()],
    ['pamet',      'Paměť',     'i-book',     () => pamet()],
    ['napojeni',   'Napojení',  'i-hub',      () => napojeni()],
    ['aktualizace','Aktualizace', 'i-up',     () => aktualizace()],
    ['logy',       'Logy',      'i-status',   () => logy()],
  ];

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function section(title, note) {
    const box = el('div', 'set-sec');
    box.appendChild(el('div', 'set-title', title));
    if (note) box.appendChild(el('div', 'set-note', note));
    return box;
  }

  async function open(opts) {
    io = opts;
    state = opts.state;
    root = el('div', 'onb set-modal');
    root.innerHTML = `
      <div class="onb-box">
        <div class="onb-head">
          <span class="onb-mark"></span>
          <div>
            <div class="onb-title">Nastavení</div>
            <div class="onb-sub"></div>
          </div>
        </div>
        <div class="onb-body set-body">
          <nav class="set-nav"></nav>
          <div class="set-panel"></div>
        </div>
        <div class="onb-foot">
          <button class="btn ghost set-wizard">Spustit průvodce znovu</button>
          <span class="spacer"></span>
          <button class="btn primary set-close">Zavřít</button>
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
    root.querySelector('.onb-sub').textContent = 'Verze ' + state.version.version;
    root.querySelector('.set-close').onclick = close;
    root.querySelector('.set-wizard').onclick = () => {
      close();
      io.openWizard();
    };
    root.addEventListener('click', (ev) => { if (ev.target === root) close(); });
    document.body.appendChild(root);
    render();
  }

  function render() {
    const nav = root.querySelector('.set-nav');
    const panel = root.querySelector('.set-panel');
    if (!SECTIONS.some(([id]) => id === active)) active = SECTIONS[0][0];

    nav.textContent = '';
    for (const [id, label, ico] of SECTIONS) {
      const b = el('button', 'set-tab' + (id === active ? ' on' : ''));
      b.innerHTML = '<svg class="ico"><use href="#' + ico + '"/></svg><span></span>';
      b.querySelector('span').textContent = label;
      b.onclick = () => {
        active = id;
        localStorage.setItem('hub-set-tab', id);
        render();
        panel.scrollTop = 0;
      };
      nav.appendChild(b);
    }

    panel.textContent = '';
    const build = (SECTIONS.find(([id]) => id === active) || SECTIONS[0])[3];
    panel.appendChild(build());
  }

  function vzhled() {
    const box = section('Vzhled');
    const wrap = el('div', 'onb-tiles');
    const current = localStorage.getItem('hub-theme') || 'system';
    for (const [key, label, dark] of [['dark', 'Tmavý', true],
                                      ['light', 'Světlý', false],
                                      ['system', 'Podle systému', null]]) {
      const t = el('button', 'onb-tile' + (current === key ? ' on' : ''));
      t.appendChild(el('span', 'onb-swatch ' + key));
      t.appendChild(el('span', null, label));
      t.onclick = () => {
        if (key === 'system') {
          localStorage.removeItem('hub-theme');
          io.setTheme(!matchMedia('(prefers-color-scheme: light)').matches, false);
        } else {
          io.setTheme(dark, true);
        }
        render();
      };
      wrap.appendChild(t);
    }
    box.appendChild(wrap);
    return box;
  }

  function projekty() {
    const box = section('Složky s projekty',
      'Odsud se plní panel vlevo.');
    const dirs = (state.config.project_dirs || []).slice();
    const list = el('div', 'onb-list');
    if (!dirs.length) list.appendChild(el('div', 'empty', '(žádné)'));
    for (const dir of dirs) {
      const row = el('div', 'onb-row');
      row.appendChild(el('span', null, dir));
      const del = el('button', 'set-x', '×');
      del.title = 'Odebrat';
      del.onclick = async () => {
        const next = dirs.filter(d => d !== dir);
        await save({project_dirs: next});
      };
      row.appendChild(el('span', 'spacer'));
      row.appendChild(del);
      list.appendChild(row);
    }
    box.appendChild(list);
    const add = el('button', 'actionbtn', '＋ Přidat složku…');
    add.onclick = async () => {
      const picked = await io.pickFolder();
      if (picked) await save({project_dirs: [...new Set([...dirs, picked])]});
    };
    box.appendChild(add);
    return box;
  }

  function taby() {
    const box = section('Tlačítka nových tabů',
      'Co má být vedle tabů. Kdo jede jen v Claude Code, nechce vedle sebe ' +
      'pořád tlačítko na holý shell — a naopak.');
    const cfg = state.config.newtab || {};
    const list = el('div', 'onb-list');
    for (const [key, label] of [['claude', 'Otevřít Claude Code'],
                                ['shell', 'Otevřít terminál']]) {
      const row = el('label', 'onb-row');
      const cb = el('input');
      cb.type = 'checkbox';
      cb.checked = cfg[key] !== false;
      cb.onchange = () => save({newtab: {...cfg, [key]: cb.checked}});
      row.appendChild(cb);
      row.appendChild(el('span', null, label));
      list.appendChild(row);
    }
    box.appendChild(list);
    return box;
  }

  function pamet() {
    const box = section('Paměť',
      'Složka s poznámkami, které si Claude nese mezi sezeními.');
    box.appendChild(Object.assign(el('div', 'onb-path'),
      {textContent: state.config.brain_dir || '(vypnutá)'}));

    const git = state.vault_git || {};
    const line = el('div', 'set-row');
    if (git.is_repo && git.remote) {
      line.appendChild(el('span', 'set-ok', '✓ zálohuje se do gitu'));
      line.appendChild(el('small', null, git.remote));
    } else {
      line.appendChild(el('span', 'set-warn', '! bez zálohy'));
      const b = el('button', 'btn ghost', 'Zálohovat do privátního repa');
      b.onclick = async () => {
        b.disabled = true;
        b.textContent = 'Zakládám…';
        try {
          await io.api('vault', {action: 'git', repo: 'claude-brain'});
        } catch (err) {
          io.toast('Nepodařilo se spustit: ' + err.message);
          b.disabled = false;
          return;
        }
        // Běží to na pozadí, tak se ptáme na stav — jinak by tlačítko viselo.
        for (;;) {
          await new Promise(r => setTimeout(r, 900));
          const st = await io.api('job?name=vault').catch(() => null);
          if (!st) break;
          if (st.running) { b.textContent = st.step || 'Zakládám…'; continue; }
          const r = st.result || {};
          io.toast(r.ok ? 'Paměť je v privátním repu.'
                        : ('Nepovedlo se: ' + (r.detail || '')));
          break;
        }
        await io.refreshState();
        state = io.state;
        render();
      };
      line.appendChild(b);
    }
    box.appendChild(line);

    const vaults = (state.vaults || []).filter(v => v.path !== state.config.brain_dir);
    if (vaults.length) {
      box.appendChild(el('div', 'set-note', 'Napojit jiný Obsidian vault:'));
      const list = el('div', 'onb-list');
      for (const v of vaults) {
        const row = el('div', 'onb-row');
        const col = el('span', 'onb-col');
        col.appendChild(el('span', null, v.name));
        col.appendChild(el('small', null, v.path + ' · ' +
          (v.has_memory ? v.notes + ' poznámek' : 'zatím bez paměti')));
        row.appendChild(col);
        row.appendChild(el('span', 'spacer'));
        const b = el('button', 'btn ghost', 'Napojit');
        b.onclick = async () => {
          try {
            await io.api('vault', {action: 'use', path: v.path});
            io.toast('Paměť napojena: ' + v.path);
            await io.refreshState();
            state = io.state;
            render();
          } catch (err) { io.toast(err.message); }
        };
        row.appendChild(b);
        list.appendChild(row);
      }
      box.appendChild(list);
    }

    const move = el('button', 'actionbtn', 'Přesunout paměť do jiné složky…');
    move.onclick = async () => {
      const picked = await io.pickFolder();
      if (!picked) return;
      try {
        await io.api('vault', {action: 'move', path: picked});
        move.disabled = true;
        for (;;) {
          await new Promise(r => setTimeout(r, 900));
          const st = await io.api('job?name=vault').catch(() => null);
          if (!st) break;
          if (st.running) { move.textContent = st.step || 'Přesouvám…'; continue; }
          const r = st.result || {};
          io.toast(r.ok ? ('Paměť přesunuta do ' + r.path)
                        : ('Nepovedlo se: ' + (r.detail || '')));
          break;
        }
        move.disabled = false;
        await io.refreshState();
        state = io.state;
        render();
      } catch (err) {
        io.toast(err.message);
      }
    };
    box.appendChild(move);
    return box;
  }

  /* Napojení (MCP) — na co Claude Code na tomhle stroji dosáhne.
   *
   * Kontrola nečte jen registrace: každý server se opravdu osloví, protože
   * „zaregistrovaný" a „odpovídá" jsou dvě různé věci — konektor s vypršeným
   * přihlášením vypadá v souborech úplně stejně jako ten funkční. Trvá to
   * kolem deseti sekund, tak to počítá server na pozadí a sekce se dokreslí.
   */
  let mcpLast = null;      // poslední doběhlý výsledek, ať sekce mezi otevřeními nebliká

  const MCP_STATES = {
    ok:      ['set-ok', '●', 'připojeno'],
    auth:    ['set-warn', '●', 'chce přihlásit'],
    fail:    ['set-bad', '●', 'nepřipojeno'],
    local:   ['set-dim', '○', 'jen v projektu'],
    unknown: ['set-dim', '○', 'neznámý stav'],
  };

  function napojeni() {
    const box = section('Napojení (MCP)',
      'Služby, do kterých Claude Code vidí — konektory z účtu claude.ai i ' +
      'servery zaregistrované na tomhle stroji. Kontrola se každého zeptá, ' +
      'takže je vidět i to, co je sice zapsané, ale nefunguje.');

    const acct = el('div', 'mcp-acct');
    const summary = el('div', 'set-row');
    const list = el('div', 'onb-list');
    const store = el('div');            // katalog: co se dá přidat
    const btns = el('div', 'onb-btns');
    const check = el('button', 'actionbtn', 'Zkontrolovat znovu');
    btns.appendChild(check);
    box.appendChild(acct);
    box.appendChild(summary);
    box.appendChild(list);
    box.appendChild(store);
    box.appendChild(btns);

    function busy(text) {
      summary.textContent = '';
      summary.appendChild(el('span', 'set-dim', text));
      check.disabled = true;
    }

    /* Konektory „claude.ai …" v seznamu visely bez souvislosti: nejsou v žádném
       souboru, patří k účtu. Druhá schránka (další Gmail) se k nim nepřidá
       vedle první — napojí se v claude.ai pod tím účtem, nebo se přepne účet
       celý. Tady je proto vidět, o který jde, a odsud se dá přepnout. */
    function drawAccount(a) {
      acct.textContent = '';
      const col = el('span', 'onb-col');
      if (a && a.email) {
        col.appendChild(el('span', null, a.name ? a.name + ' · ' + a.email : a.email));
        const meta = ['konektory z účtu patří sem'];
        if (a.plan) meta.push(a.plan);
        if (a.org) meta.push(a.org);
        col.appendChild(el('small', null, meta.join(' · ')));
      } else {
        col.appendChild(el('span', 'set-warn', 'Nikdo přihlášený'));
        col.appendChild(el('small', null,
          'Bez přihlášení nejsou konektory z účtu claude.ai vidět.'));
      }
      acct.appendChild(col);
      acct.appendChild(el('span', 'spacer'));
      const swap = el('button', 'btn ghost', a && a.email ? 'Přepnout účet' : 'Přihlásit');
      swap.title = 'Otevře Claude Code s /login. Jiný účet = jiné konektory ' +
                   '(třeba druhá gmailová schránka).';
      swap.onclick = () => { close(); io.login(); };
      acct.appendChild(swap);
    }

    function draw(data) {
      check.disabled = false;
      mcpLast = data;
      drawAccount(data.account);
      const servers = data.servers || [];
      const c = data.counts || {};

      summary.textContent = '';
      if (!data.ok && data.detail) {
        summary.appendChild(el('span', 'set-warn', data.detail));
      } else {
        summary.appendChild(el('span', 'set-ok',
          (c.ok || 0) + ' z ' + (c.total || 0) + ' připojeno'));
        const rest = [];
        if (c.auth) rest.push(c.auth + '× chce přihlásit');
        if (c.fail) rest.push(c.fail + '× nepřipojeno');
        if (c.local) rest.push(c.local + '× jen v projektu');
        if (rest.length) summary.appendChild(el('small', null, rest.join(' · ')));
      }

      list.textContent = '';
      if (!servers.length) list.appendChild(el('div', 'empty', '(žádné napojení)'));
      for (const s of servers) {
        const [cls, dot, fallback] = MCP_STATES[s.state] || MCP_STATES.unknown;
        const row = el('div', 'onb-row mcp-row');
        row.appendChild(Object.assign(el('span', 'mcp-dot ' + cls), {textContent: dot}));
        const col = el('span', 'onb-col');
        col.appendChild(el('span', null, s.name));
        const where = [s.status || fallback];
        if (s.where) where.push(s.where);
        col.appendChild(el('small', null, where.join(' · ')));
        if (s.target) col.appendChild(el('small', 'mcp-target', s.target));
        row.appendChild(col);
        row.appendChild(el('span', 'spacer'));
        if (s.removable) {
          const del = el('button', 'set-x', '×');
          del.title = 'Odebrat napojení';
          del.onclick = async (ev) => {
            ev.stopPropagation();
            if (!confirm('Odebrat napojení ' + s.name + '?')) return;
            try {
              await io.api('mcp', {action: 'remove', name: s.name});
              io.toast(s.name + ' odebrán.');
              load(true);
            } catch (err) { io.toast('Nepovedlo se: ' + err.message); }
          };
          row.appendChild(del);
        }
        list.appendChild(row);
      }

      store.textContent = '';
      const catalog = data.catalog || {};
      for (const key of (data.available || [])) {
        const spec = catalog[key];
        if (spec) store.appendChild(pridat(key, spec));
      }
    }

    /* Přidání z katalogu. Klíč se zadává tady a putuje rovnou do
       `claude mcp add` — hub si ho nikam neukládá a do logu se nedostane. */
    function pridat(key, spec) {
      const wrap = el('div', 'mcp-add');
      const head = el('div', 'set-row');
      head.appendChild(el('strong', null, 'Přidat ' + spec.label));
      head.appendChild(el('span', 'spacer'));
      const open = el('button', 'btn ghost', 'Napojit');
      head.appendChild(open);
      wrap.appendChild(head);
      wrap.appendChild(el('div', 'set-note', spec.note));

      const form = el('div', 'mcp-form');
      form.hidden = true;
      const input = el('input');
      input.type = 'password';
      input.placeholder = spec.key_label || 'API klíč';
      input.autocomplete = 'off';
      form.appendChild(input);
      const go = el('button', 'btn primary', 'Napojit');
      form.appendChild(go);
      wrap.appendChild(form);
      if (spec.key_help) {
        const help = el('div', 'set-note', 'Kde ho vzít: ' + spec.key_help + ' ');
        if (spec.docs) {
          const a = el('button', 'linkbtn', 'návod');
          a.onclick = () => io.api('open-path', {path: spec.docs})
            .catch(() => io.toast('Nepodařilo se otevřít odkaz.'));
          help.appendChild(a);
        }
        wrap.appendChild(help);
      }

      open.onclick = () => {
        form.hidden = !form.hidden;
        // Dvě tlačítka „Napojit" vedle sebe by mátla — tohle jen otevírá pole.
        open.textContent = form.hidden ? 'Napojit' : 'Zavřít';
        if (!form.hidden) input.focus();
      };
      input.onkeydown = (ev) => { if (ev.key === 'Enter') go.click(); };
      go.onclick = async () => {
        const value = input.value.trim();
        if (!value) { io.toast('Bez klíče se server nepřihlásí.'); return; }
        go.disabled = true;
        go.textContent = 'Napojuju…';
        try {
          const r = await io.api('mcp', {action: 'add', name: key, key: value});
          io.toast(r.detail || 'Hotovo.');
        } catch (err) {
          io.toast('Nepovedlo se: ' + err.message);
          go.disabled = false;
          go.textContent = 'Napojit';
          return;
        }
        input.value = '';
        load(true);
      };
      return wrap;
    }

    async function load(refresh) {
      busy(refresh ? 'Ptám se serverů…' : 'Načítám…');
      let data;
      try {
        data = await io.api('mcp' + (refresh ? '?refresh=1' : ''));
        drawAccount(data.account);
      } catch (err) {
        summary.textContent = '';
        summary.appendChild(el('span', 'set-warn', 'Nepovedlo se: ' + err.message));
        check.disabled = false;
        return;
      }
      while (data.running) {
        busy(data.step || 'Ptám se serverů…');
        drawAccount(data.account);
        await new Promise(r => setTimeout(r, 1200));
        try {
          data = await io.api('mcp');
        } catch (err) {
          summary.textContent = '';
          summary.appendChild(el('span', 'set-warn', 'Nepovedlo se: ' + err.message));
          check.disabled = false;
          return;
        }
      }
      draw(data);
    }

    check.onclick = () => load(true);
    if (mcpLast) draw(mcpLast);          // ať je hned vidět minulý výsledek
    load(false);                         // a na pozadí se dotáhne aktuální
    return box;
  }

  function aktualizace() {
    const box = section('Aktualizace aplikace',
      'Stáhne novou verzi hubu a přeinstaluje ji. (Tlačítko ⟳ v hlavičce jen ' +
      'znovu načte projekty — s tímhle nemá nic společného.)');
    const info = el('span', 'set-ver', 'Nainstalováno: ' + state.version.version);
    const infoRow = el('div', 'set-row');
    infoRow.appendChild(info);
    box.appendChild(infoRow);

    const status = el('div', 'set-status');
    box.appendChild(status);

    const btns = el('div', 'onb-btns');
    const check = el('button', 'actionbtn', 'Zjistit, jestli je novější');
    const doIt = el('button', 'actionbtn', 'Aktualizovat');
    doIt.hidden = true;

    check.onclick = async () => {
      check.disabled = true;
      status.className = 'set-status busy';
      status.textContent = 'Kontroluju…';
      try {
        const v = await io.api('update-check');
        if (!v.latest) {
          status.className = 'set-status warn';
          status.textContent = v.why || 'Nepodařilo se zjistit.';
        } else if (v.update_available) {
          status.className = 'set-status warn';
          status.textContent = `Je dostupná verze ${v.latest} (máš ${v.version}).`;
          doIt.hidden = false;
        } else {
          status.className = 'set-status ok';
          status.textContent = `Máš nejnovější verzi (${v.version}).`;
        }
      } catch (err) {
        status.className = 'set-status warn';
        status.textContent = 'Kontrola selhala: ' + err.message;
      } finally {
        check.disabled = false;
      }
    };

    // Aktualizace běží na serveru na pozadí a stav se odečítá — dřív to byl
    // jeden dlouhý požadavek a stránka na něm zůstala viset s „Stahuju…".
    async function watchUpdate() {
      for (;;) {
        await new Promise(r => setTimeout(r, 1200));
        let st;
        try {
          st = await io.api('update-status');
        } catch (err) {
          status.className = 'set-status warn';
          status.textContent = 'Ztratil jsem spojení se serverem: ' + err.message;
          return;
        }
        if (st.running) {
          status.textContent = 'Aktualizuju… ' + (st.step || '');
          continue;
        }
        const r = st.result || {};
        if (!r.ok) {
          status.className = 'set-status warn';
          status.textContent = r.detail || 'Aktualizace se nepovedla.';
        } else if (r.changed) {
          status.className = 'set-status ok';
          status.textContent = `✓ Nainstalována verze ${r.now}. ` +
            'Zavři a znovu otevři aplikaci, ať se načte.';
          doIt.hidden = true;
          info.textContent = 'Nainstalováno: ' + r.now;
        } else {
          status.className = 'set-status ok';
          status.textContent = '✓ ' + (r.detail || 'Nic nového.');
          doIt.hidden = true;
        }
        doIt.disabled = false;
        check.disabled = false;
        await io.refreshState();
        state = io.state;
        return;
      }
    }

    doIt.onclick = async () => {
      doIt.disabled = true;
      check.disabled = true;
      status.className = 'set-status busy';
      status.textContent = 'Aktualizuju…';
      try {
        await io.api('update');
      } catch (err) {
        status.className = 'set-status warn';
        status.textContent = 'Nepodařilo se spustit: ' + err.message;
        doIt.disabled = false;
        check.disabled = false;
        return;
      }
      watchUpdate();
    };

    // Když se stránka načte během běžící aktualizace, navážeme na ni.
    io.api('update-status').then(st => {
      if (st.running) {
        status.className = 'set-status busy';
        status.textContent = 'Aktualizuju…';
        doIt.disabled = true;
        check.disabled = true;
        watchUpdate();
      }
    }).catch(() => {});

    btns.appendChild(check);
    btns.appendChild(doIt);
    box.appendChild(btns);
    return box;
  }

  function logy() {
    const box = section('Logy',
      'Co se v aplikaci dělo — starty, otevřené taby, běhy na pozadí a chyby. ' +
      'Leží to v ~/.claude/hub.log a nikam se to samo neposílá.');
    const view = el('pre', 'log-view', 'Načítám…');
    box.appendChild(view);

    const btns = el('div', 'onb-btns');
    const refresh = el('button', 'actionbtn', 'Načíst znovu');
    const copy = el('button', 'actionbtn', 'Zkopírovat hlášení');
    const save = el('button', 'actionbtn', 'Uložit hlášení do souboru');
    const clear = el('button', 'actionbtn', 'Vymazat log');

    const load = async () => {
      view.textContent = 'Načítám…';
      try {
        const r = await io.api('log?lines=400');
        const lines = r.lines || [];
        view.textContent = '';
        if (!lines.length) { view.textContent = '(log je prázdný)'; return; }
        for (const line of lines) {
          const row = el('span', 'log-line');
          if (/\bERROR\b/.test(line)) row.classList.add('err');
          else if (/\bWARN\b/.test(line)) row.classList.add('warn');
          row.textContent = line;
          view.appendChild(row);
        }
        view.scrollTop = view.scrollHeight;   // konec je to zajímavé
      } catch (err) {
        view.textContent = 'Nepovedlo se: ' + err.message;
      }
    };

    refresh.onclick = load;
    copy.onclick = async () => {
      try {
        const r = await io.api('report');
        await io.api('clipboard', {text: r.text, which: 'clipboard'});
        io.toast('Hlášení je ve schránce — stačí vložit.');
      } catch (err) { io.toast('Nepovedlo se: ' + err.message); }
    };
    save.onclick = async () => {
      try {
        const r = await io.api('report');
        // Uloží se stejnou cestou jako přiložené obrázky, takže se pak dá
        // rovnou přetáhnout nebo poslat.
        const data = btoa(unescape(encodeURIComponent(r.text)));
        const out = await io.api('upload', {name: 'hlaseni-hub.txt', data});
        io.toast('Uloženo: ' + out.path);
      } catch (err) { io.toast('Nepovedlo se: ' + err.message); }
    };
    clear.onclick = async () => {
      if (!confirm('Vymazat log?')) return;
      try { await io.api('log-clear', {}); await load(); }
      catch (err) { io.toast(err.message); }
    };

    for (const b of [refresh, copy, save, clear]) btns.appendChild(b);
    box.appendChild(btns);
    load();
    return box;
  }

  async function save(updates) {
    try {
      await io.api('config', updates);
      await io.refreshState();
      state = io.state;
      render();
    } catch (err) {
      io.toast(err.message);
    }
  }

  function close() {
    if (root) root.remove();
    root = null;
    io.reload();
  }

  global.HubSettings = {open, close};

})(window);
