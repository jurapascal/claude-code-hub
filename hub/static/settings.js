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
        const r = await io.api('vault', {action: 'git', repo: 'claude-brain'})
          .catch(e => ({ok: false, detail: e.message}));
        io.toast(r.ok ? 'Paměť je v privátním repu.' : ('Nepovedlo se: ' + r.detail));
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
        const r = await io.api('vault', {action: 'move', path: picked});
        io.toast('Paměť přesunuta do ' + r.path);
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
    const info = el('div', 'set-row');
    info.appendChild(el('span', 'set-ver', 'Nainstalováno: ' + state.version.version));
    box.appendChild(info);

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

    doIt.onclick = async () => {
      doIt.disabled = true;
      check.disabled = true;
      status.className = 'set-status busy';
      status.textContent = 'Stahuju a instaluju… (může to chvíli trvat)';
      try {
        const r = await io.api('update');
        if (!r.ok) {
          status.className = 'set-status warn';
          status.textContent = r.detail;
        } else if (r.changed) {
          status.className = 'set-status ok';
          status.textContent =
            `✓ Aktualizováno na ${r.now}. Zavři a znovu otevři aplikaci, ` +
            'ať se nová verze načte.';
          doIt.hidden = true;
        } else {
          status.className = 'set-status ok';
          status.textContent = '✓ ' + r.detail;
          doIt.hidden = true;
        }
      } catch (err) {
        status.className = 'set-status warn';
        status.textContent = 'Aktualizace selhala: ' + err.message;
      } finally {
        doIt.disabled = false;
        check.disabled = false;
      }
    };

    btns.appendChild(check);
    btns.appendChild(doIt);
    box.appendChild(btns);
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
