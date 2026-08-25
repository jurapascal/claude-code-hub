/* Nastavení — to, co průvodce nastaví napoprvé, se tu dá změnit kdykoli.
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
    root = el('div', 'onb');
    root.innerHTML = `
      <div class="onb-box">
        <div class="onb-head">
          <span class="onb-mark"></span>
          <div>
            <div class="onb-title">Nastavení</div>
            <div class="onb-sub"></div>
          </div>
        </div>
        <div class="onb-body"></div>
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
    const body = root.querySelector('.onb-body');
    body.textContent = '';
    body.appendChild(vzhled());
    body.appendChild(projekty());
    body.appendChild(taby());
    body.appendChild(pamet());
    body.appendChild(aktualizace());
    body.appendChild(logy());
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
    const doIt = el('button', 'actionbtn set-update', 'Aktualizovat');
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
