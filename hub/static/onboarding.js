/* První spuštění: co se dřív odpovídalo v instalačce, se teď nastavuje tady.
 *
 * Instalačka běží jednou, v terminálu, a kdo ji překlikal, neměl potom kde to
 * změnit. Průvodce v aplikaci to bere jinak: ptá se až na hotové appce, ukazuje
 * výsledek rovnou (motiv se přepne pod rukama) a dá se kdykoli spustit znovu.
 *
 * Zapisuje se přes /api/config a /api/vault, takže server si cesty přepočítá
 * hned — restart aplikace kvůli tomu potřeba není.
 */
'use strict';

(function (global) {

  let io = null;        // {api, setTheme, isDark, toast, pickFolder, reload}
  let state = null;     // payload z /api/state
  let step = 0;
  let chosen = {dirs: [], vault: '', backup: 'later', cloudPath: '', repo: 'claude-brain'};
  let root = null;

/* Kroky se liší podle režimu, který si člověk vybere hned na uvítanou.
 *
 * Základní režim nemá v nastavení Projekty, Paměť ani zálohu — ptát se na ně
 * v průvodci by znamenalo nastavit něco, k čemu se pak nedá vrátit. Zbydou
 * tedy tři kroky a hub naskočí na výchozích složkách; kdo je bude chtít jinak,
 * zapne si vývojářský režim a projde průvodce znovu. */
  const STEPS_ZAKLAD = ['vitej', 'vzhled', 'hotovo'];
  const STEPS_VYVOJ = ['vitej', 'vzhled', 'projekty', 'pamet', 'zaloha', 'hotovo'];

  function steps() {
    return chosen.dev ? STEPS_VYVOJ : STEPS_ZAKLAD;
  }

  /* ── kostra ─────────────────────────────────────────────────────────────── */
  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function mark(size) {
    // Značka aplikace — stejné tvary jako ikona, jen v currentColor.
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('width', size);
    svg.setAttribute('height', size);
    const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
    use.setAttribute('href', '#i-hub');
    svg.appendChild(use);
    return svg;
  }

  function open(opts) {
    io = opts;
    state = opts.state;
    step = 0;
    chosen.dirs = (state.config.project_dirs || []).slice();
    if (!chosen.dirs.length) chosen.dirs = (state.suggest_dirs || []).slice();
    chosen.vault = state.config.brain_dir || '';
    chosen.dev = !!(state.config && state.config.dev_mode);
    chosen.backup = state.vault_git.is_repo ? 'git' : 'later';
    build();
    render();
  }

  function build() {
    root = el('div', 'onb');
    root.innerHTML = `
      <div class="onb-box">
        <div class="onb-head">
          <span class="onb-mark"></span>
          <div>
            <div class="onb-title"></div>
            <div class="onb-sub"></div>
          </div>
          <div class="onb-dots"></div>
        </div>
        <div class="onb-body"></div>
        <div class="onb-foot">
          <button class="btn ghost onb-skip">Přeskočit</button>
          <span class="spacer"></span>
          <button class="btn ghost onb-back">Zpět</button>
          <button class="btn primary onb-next">Dál</button>
        </div>
      </div>`;
    root.querySelector('.onb-mark').appendChild(mark(34));
    root.querySelector('.onb-skip').onclick = () => finish(true);
    root.querySelector('.onb-back').onclick = () => { if (step > 0) { step--; render(); } };
    root.querySelector('.onb-next').onclick = next;
    document.body.appendChild(root);
  }

  function render() {
    const box = root.querySelector('.onb-body');
    box.textContent = '';
    const dots = root.querySelector('.onb-dots');
    dots.textContent = '';
    steps().forEach((_, i) => {
      const d = el('i', 'onb-dot' + (i === step ? ' on' : (i < step ? ' done' : '')));
      dots.appendChild(d);
    });
    root.querySelector('.onb-back').style.visibility = step === 0 ? 'hidden' : '';
    root.querySelector('.onb-skip').style.visibility =
      step === steps().length - 1 ? 'hidden' : '';
    root.querySelector('.onb-next').textContent =
      step === steps().length - 1 ? 'Začít' : 'Dál';
    ({vitej, vzhled, projekty, pamet, zaloha, hotovo})[steps()[step]](box);
  }

  function head(title, sub) {
    root.querySelector('.onb-title').textContent = title;
    root.querySelector('.onb-sub').textContent = sub || '';
  }

  /* ── kroky ──────────────────────────────────────────────────────────────── */
  function vitej(box) {
    head('Claude Code Hub', 'Chvilka nastavení a pak už jen práce.');
    // Věta se musí trefit do počtu kroků, které pak přijdou — jinak průvodce
    // slíbí nastavení projektů a paměti a hned skončí.
    const uvod = el('p', 'onb-lead');
    uvod.textContent = 'Každý projekt se otevře jako vlastní tab se skutečným ' +
      'terminálem. ' + (chosen.dev
        ? 'Teď si nastavíme vzhled, kde máš projekty a kde bydlí paměť.'
        : 'Zbývá vybrat vzhled a můžeme začít.');
    box.appendChild(uvod);

    // Týmový režim: appka se připojí k serveru (bráně). Po přihlášení běží
    // všechno na serveru — vlastní účet, vlastní paměť, vlastní MCP.
    const server = el('div');
    server.style.cssText = 'margin:2px 0 18px;padding:14px;border:1px solid var(--border,#322e20);border-radius:10px;background:rgba(224,164,88,.06)';
    const sBtn = el('button', null, '☁  Připojit se k serveru (týmový režim)');
    sBtn.style.cssText = 'width:100%;padding:9px;border:0;border-radius:8px;background:#2a2618;color:#e8b76a;font-weight:600;cursor:pointer';
    const sForm = el('div');
    sForm.style.cssText = 'display:none;gap:8px;margin-top:10px';
    const sInput = el('input');
    sInput.type = 'text';
    sInput.placeholder = 'adresa serveru, např. test.alba-rosa.cz';
    sInput.style.cssText = 'flex:1;padding:9px 11px;border-radius:8px;border:1px solid #3a3524;background:#14130d;color:#e8e3d3';
    if (state.config && state.config.server_url) sInput.value = state.config.server_url;
    const sGo = el('button', null, 'Přihlásit se');
    sGo.style.cssText = 'padding:9px 16px;border:0;border-radius:8px;background:#e0a458;color:#1a1710;font-weight:600;cursor:pointer;white-space:nowrap';
    const sMsg = el('div');
    sMsg.style.cssText = 'margin-top:8px;font-size:.85rem;color:#9a927c;min-height:1em';
    sBtn.onclick = () => {
      const open = sForm.style.display === 'none';
      sForm.style.display = open ? 'flex' : 'none';
      if (open) sInput.focus();
    };
    sGo.onclick = async () => {
      let url = (sInput.value || '').trim();
      if (!url) { sMsg.textContent = 'Zadej adresu serveru.'; return; }
      if (!/:\/\//.test(url)) url = 'https://' + url;
      url = url.replace(/\/+$/, '');
      sGo.disabled = true; sMsg.textContent = 'Ukládám a otevírám přihlášení…';
      try {
        await io.api('config', {server_url: url});
        location.href = url;   // dál už servíruje brána: přihlášení → hub
      } catch (e) { sMsg.textContent = e.message || 'Nepovedlo se.'; sGo.disabled = false; }
    };
    sInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') sGo.click(); });
    sForm.appendChild(sInput); sForm.appendChild(sGo);
    server.appendChild(sBtn); server.appendChild(sForm); server.appendChild(sMsg);
    box.appendChild(server);

    box.appendChild(el('p', 'onb-lead',
      'Nebo pokračuj a nastav si Hub na tomhle počítači:'));

    // Volba režimu. Rozhoduje o zbytku průvodce, tak patří sem, ne na konec.
    box.appendChild(el('div', 'onb-lead', 'Jak hub používáš?'));
    const rezim = el('div', 'onb-tiles');
    for (const [dev, label, note] of [
      [false, 'Píšu s agentem',
       'Terminál, paměť, agenti. Nic o nasazování a GitHubu.'],
      [true, 'Vyvíjím a nasazuju',
       'Navíc Deploy, Push na GitHub, složky projektů a logy.'],
    ]) {
      const t = el('button', 'onb-tile' + (chosen.dev === dev ? ' on' : ''));
      t.appendChild(el('span', 'onb-tile-t', label));
      t.appendChild(el('span', 'onb-tile-s', note));
      t.onclick = () => { chosen.dev = dev; render(); };
      rezim.appendChild(t);
    }
    box.appendChild(rezim);
    box.appendChild(el('div', 'onb-note',
      'Dá se přepnout kdykoli v Nastavení → Vzhled.'));

    const list = el('ul', 'onb-check');
    for (const [ok, text] of [
      [!!state.doctor.bash, 'bash — bez něj se tab neotevře'],
      [!!state.doctor.claude, 'Claude Code CLI'],
      [!!state.doctor.clipboard, 'schránka (kopírování v tabu)'],
      [!!state.obsidian, 'Obsidian (pro paměť, volitelné)'],
    ]) {
      const li = el('li', ok ? 'ok' : 'miss', text);
      list.appendChild(li);
    }
    box.appendChild(list);
  }

  function vzhled(box) {
    head('Vzhled', 'Přepne se hned, ať je vidět, do čeho jdeš.');
    const wrap = el('div', 'onb-tiles');
    const opts = [
      ['dark', 'Tmavý', true],
      ['light', 'Světlý', false],
      ['system', 'Podle systému', null],
    ];
    const current = localStorage.getItem('hub-theme') || 'system';
    for (const [key, label, dark] of opts) {
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
  }

  function projekty(box) {
    head('Projekty', 'Složky, ve kterých se hledají projekty do panelu.');
    const list = el('div', 'onb-list');
    const all = [...new Set([...chosen.dirs, ...(state.suggest_dirs || [])])];
    if (!all.length) {
      list.appendChild(el('div', 'empty', '(zatím žádná — přidej složku níž)'));
    }
    for (const dir of all) {
      const row = el('label', 'onb-row');
      const cb = el('input');
      cb.type = 'checkbox';
      cb.checked = chosen.dirs.includes(dir);
      cb.onchange = () => {
        chosen.dirs = cb.checked ? [...new Set([...chosen.dirs, dir])]
                                 : chosen.dirs.filter(d => d !== dir);
      };
      row.appendChild(cb);
      row.appendChild(el('span', null, dir));
      list.appendChild(row);
    }
    box.appendChild(list);
    const add = el('button', 'actionbtn', '＋ Přidat složku…');
    add.onclick = async () => {
      const picked = await io.pickFolder();
      if (picked) { chosen.dirs = [...new Set([...chosen.dirs, picked])]; render(); }
    };
    box.appendChild(add);
  }

  function pamet(box) {
    head('Paměť', 'Poznámky, které si Claude nese mezi sezeními.');
    box.appendChild(el('p', 'onb-lead',
      'Paměť je obyčejná složka s markdownem — dá se otevřít v Obsidianu. ' +
      'Teprve s ní fungují příkazy /save, /learn a /project.'));

    // Napojit existující vault je nejčastější případ: kdo Obsidian používá,
    // už poznámky někde má a nechce začínat znovu.
    const vaults = state.vaults || [];
    if (vaults.length) {
      box.appendChild(el('div', 'set-title', 'Našel jsem tyhle Obsidian vaulty'));
      const list = el('div', 'onb-list');
      for (const v of vaults) {
        const row = el('label', 'onb-row');
        const rb = el('input');
        rb.type = 'radio';
        rb.name = 'vault';
        rb.checked = chosen.vault === v.path;
        rb.onchange = () => { chosen.vault = v.path; renderPath(); };
        row.appendChild(rb);
        const col = el('span', 'onb-col');
        col.appendChild(el('span', null, v.name));
        col.appendChild(el('small', null, v.path + ' · ' +
          (v.has_memory ? v.notes + ' poznámek' : 'zatím bez paměti — založí se')));
        row.appendChild(col);
        list.appendChild(row);
      }
      box.appendChild(list);
    }

    const path = el('div', 'onb-path');
    path.id = 'onb-vault-path';
    path.textContent = chosen.vault || '(nenastaveno)';
    box.appendChild(path);

    const row = el('div', 'onb-btns');
    const pick = el('button', 'actionbtn', 'Vybrat jinou složku…');
    pick.onclick = async () => {
      const picked = await io.pickFolder();
      if (picked) { chosen.vault = picked; render(); }
    };
    const clone = el('button', 'actionbtn', 'Stáhnout z gitu…');
    clone.onclick = async () => {
      const repo = prompt('Adresa repa s pamětí (owner/repo nebo URL):', '');
      if (!repo) return;
      try {
        const r = await io.api('vault', {action: 'clone', repo: repo.trim()});
        chosen.vault = r.path;
        io.toast('Paměť stažena do ' + r.path);
        await io.refreshState();
        state = io.state;
        render();
      } catch (err) { io.toast(err.message); }
    };
    const make = el('button', 'actionbtn', 'Založit novou');
    make.onclick = () => {
      chosen.vault = (state.home || '~') + '/Obsidian/Claude-Brain';
      render();
    };
    row.appendChild(pick);
    row.appendChild(clone);
    row.appendChild(make);
    box.appendChild(row);
  }

  function renderPath() {
    const node = document.getElementById('onb-vault-path');
    if (node) node.textContent = chosen.vault || '(nenastaveno)';
  }

  function zaloha(box) {
    head('Záloha paměti', 'Aby poznámky nežily jen na jednom disku.');
    const cloud = state.cloud || [];
    const opts = [];
    // Tenhle krok se ukazuje jen ve vývojářském režimu, takže GitHub tu může
    // být bez další podmínky — kdo si režim vybral, o `gh` stojí.
    opts.push({
      key: 'git', label: 'Do privátního repa na GitHubu',
      note: state.doctor.git && state.vault_git.is_repo
        ? 'Vault už v gitu je — jen se zapne automatické posílání.'
        : 'Založí privátní repo a po každém sezení tam pošle změny.',
      disabled: !state.doctor.git,
    });
    for (const c of cloud) {
      opts.push({key: 'cloud:' + c.path, label: 'Přesunout do ' + c.name,
                 note: c.path});
    }
    opts.push({
      key: 'folder', label: 'Přesunout do vlastní složky…',
      note: cloud.length ? 'Když máš cloud jinde.'
                         : 'Žádného klienta (OneDrive, Dropbox…) jsem tu nenašel — ' +
                           'když si ho doinstaluješ, ukaž sem na jeho složku.',
    });
    opts.push({key: 'later', label: 'Zatím ne', note: 'Dá se zapnout kdykoli později.'});

    const list = el('div', 'onb-list');
    for (const o of opts) {
      const row = el('label', 'onb-row' + (o.disabled ? ' off' : ''));
      const rb = el('input');
      rb.type = 'radio';
      rb.name = 'zaloha';
      rb.disabled = !!o.disabled;
      rb.checked = chosen.backup === o.key ||
                   (o.key.startsWith('cloud:') && chosen.backup === 'cloud' &&
                    chosen.cloudPath === o.key.slice(6));
      rb.onchange = () => {
        if (o.key.startsWith('cloud:')) {
          chosen.backup = 'cloud';
          chosen.cloudPath = o.key.slice(6);
        } else {
          chosen.backup = o.key;
        }
      };
      row.appendChild(rb);
      const col = el('span', 'onb-col');
      col.appendChild(el('span', null, o.label));
      col.appendChild(el('small', null, o.note));
      row.appendChild(col);
      list.appendChild(row);
    }
    box.appendChild(list);
  }

  function hotovo(box) {
    head('Hotovo', 'Můžeme začít.');
    const list = el('ul', 'onb-check');
    list.appendChild(el('li', 'ok',
      'režim: ' + (chosen.dev ? 'vývojářský' : 'základní')));
    if (chosen.dev) {
      list.appendChild(el('li', 'ok', 'projekty: ' +
        (chosen.dirs.length ? chosen.dirs.join(', ') : '(žádné)')));
      list.appendChild(el('li', chosen.vault ? 'ok' : 'miss',
        'paměť: ' + (chosen.vault || 'vypnutá')));
      const zal = {git: 'privátní repo na GitHubu', cloud: chosen.cloudPath,
                   folder: 'vlastní složka', later: 'zatím ne'}[chosen.backup];
      list.appendChild(el('li', chosen.backup === 'later' ? 'miss' : 'ok',
        'záloha paměti: ' + zal));
    } else {
      // Nic z toho se v základním režimu neptalo — ať je vidět, co platí.
      list.appendChild(el('li', 'ok', 'projekty a paměť: výchozí nastavení'));
    }
    box.appendChild(list);
    box.appendChild(el('p', 'onb-lead',
      'Kdykoli později: tlačítko ⚙ v hlavičce.'));
  }

  /* ── posun ──────────────────────────────────────────────────────────────── */
  async function next() {
    const btn = root.querySelector('.onb-next');
    btn.disabled = true;
    try {
      if (steps()[step] === 'projekty') {
        await io.api('config', {project_dirs: chosen.dirs});
      } else if (steps()[step] === 'pamet' && chosen.vault) {
        // Existující složku jen napojíme; create by jinak přepsal rozcestník.
        const known = (state.vaults || []).some(v => v.path === chosen.vault);
        await io.api('vault',
          {action: known ? 'use' : 'create', path: chosen.vault});
      } else if (steps()[step] === 'zaloha') {
        // Nepovedenou zálohu nepřeskakujeme — ať je vidět, co se stalo.
        if (!await applyBackup()) return;
      } else if (steps()[step] === 'hotovo') {
        await finish(false);
        return;
      }
      step++;
      render();
    } catch (err) {
      io.toast(err.message || String(err));
    } finally {
      btn.disabled = false;
    }
  }

  /* Záloha běží na serveru na pozadí a stav se odečítá — dřív to byl jeden
     dlouhý požadavek a průvodce na něm zůstal viset. */
  function backupStatus(text, kind) {
    let node = root.querySelector('.onb-job');
    if (!node) {
      node = el('div', 'set-status onb-job');
      root.querySelector('.onb-body').appendChild(node);
    }
    node.className = 'set-status onb-job ' + (kind || 'busy');
    node.textContent = text;
  }

  async function waitForVault() {
    for (;;) {
      await new Promise(r => setTimeout(r, 900));
      let st;
      try {
        st = await io.api('job?name=vault');
      } catch (err) {
        backupStatus('Ztratil jsem spojení: ' + err.message, 'warn');
        return false;
      }
      if (st.running) {
        backupStatus('Zálohuju… ' + (st.step || ''), 'busy');
        continue;
      }
      const r = st.result || {};
      if (!r.ok) {
        backupStatus(r.detail || 'Zálohu se nepovedlo nastavit.', 'warn');
        return false;
      }
      if (r.path) chosen.vault = r.path;
      backupStatus('✓ ' + (r.path ? 'Paměť přesunuta do ' + r.path
                                  : 'Paměť je v privátním repu.'), 'ok');
      await io.refreshState();
      state = io.state;
      return true;
    }
  }

  async function applyBackup() {
    if (chosen.backup === 'later') return true;
    let payload = null;
    if (chosen.backup === 'git') {
      payload = {action: 'git', repo: chosen.repo};
    } else if (chosen.backup === 'cloud' && chosen.cloudPath) {
      payload = {action: 'move', path: chosen.cloudPath};
    } else if (chosen.backup === 'folder') {
      const picked = await io.pickFolder();
      if (!picked) return true;          // rozmyslel si to, jdeme dál
      payload = {action: 'move', path: picked};
    }
    if (!payload) return true;
    backupStatus('Zálohuju…', 'busy');
    try {
      await io.api('vault', payload);
    } catch (err) {
      backupStatus('Nepodařilo se spustit: ' + err.message, 'warn');
      return false;
    }
    return waitForVault();
  }

  async function finish(skipped) {
    try {
      await io.api('config', {onboarded: true, dev_mode: !!chosen.dev});
    } catch (_) { /* nastavení se nepovedlo uložit — průvodce přesto zavíráme */ }
    close();
    if (!skipped) io.toast('Nastaveno. Ať to jde od ruky.');
    io.reload();
  }

  function close() {
    if (root) root.remove();
    root = null;
  }

  global.HubOnboarding = {open, close};

})(window);
