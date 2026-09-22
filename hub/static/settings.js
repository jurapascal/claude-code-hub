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
    // `dev` = jen ve vývojářském režimu. Kdo hub používá na psaní s agentem,
    // neřeší složky s projekty, zálohu paměti ani tlačítka nových tabů.
    ['projekty',   'Projekty',  'i-folder',   () => projekty(), 'dev'],
    ['taby',       'Taby',      'i-terminal', () => taby(), 'dev'],
    ['agenti',     'AI agenti', 'i-hub',      () => agenti()],
    ['pamet',      'Paměť',     'i-book',     () => pamet()],
    ['ucet',       'Účet',      'i-user',     () => ucet()],
    ['napojeni',   'Napojení',  'i-hub',      () => napojeni()],   // MCP — Claude Code
    ['aktualizace','Aktualizace', 'i-up',     () => aktualizace()],
    ['logy',       'Logy',      'i-status',   () => logy(), 'dev'],
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
    if (opts.tab && SECTIONS.some(([id]) => id === opts.tab)) active = opts.tab;
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
    // Zavřít jen klikem, který na pozadí i začal: výběr textu tažením ven
    // z okna končí puštěním tlačítka na pozadí a prohlížeč to hlásí jako klik.
    let downOutside = false;
    root.addEventListener('pointerdown', (ev) => { downOutside = ev.target === root; });
    root.addEventListener('click', (ev) => { if (ev.target === root && downOutside) close(); });
    document.body.appendChild(root);
    render();
  }

  /* Sekce, které se teď mají ukazovat.
   *
   * Filtruje se až při kreslení, ne jednou při načtení: režim se přepíná
   * v nastavení samotném a panel se musí překreslit hned, ne po restartu. */
  function visibleSections() {
    const dev = !!(state && state.config && state.config.dev_mode);
    return SECTIONS.filter(([, , , , flag]) => flag !== 'dev' || dev);
  }

  function render() {
    const nav = root.querySelector('.set-nav');
    const panel = root.querySelector('.set-panel');
    const shown = visibleSections();
    // Vypnutím režimu může zmizet sekce, ve které uživatel zrovna stojí.
    if (!shown.some(([id]) => id === active)) active = shown[0][0];

    nav.textContent = '';
    for (const [id, label, ico] of shown) {
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
    const build = (shown.find(([id]) => id === active) || shown[0])[3];
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

    box.appendChild(el('div', 'set-title', 'Vývojářský režim'));
    box.appendChild(el('div', 'set-note',
      'Nasazování a GitHub. Dokud je vypnutý, hub o nic z toho nezavadí — ' +
      'žádné tlačítko Deploy ani Push, a nic se nenabízí doinstalovat.'));
    const row = el('label', 'onb-row');
    const cb = el('input');
    cb.type = 'checkbox';
    cb.checked = !!state.config.dev_mode;
    cb.onchange = () => save({dev_mode: cb.checked});
    row.appendChild(cb);
    row.appendChild(el('span', null,
      'Zapnout tlačítka Deploy a Push na GitHub a sekce Projekty, ' +
      'Taby a Logy'));
    box.appendChild(row);
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
      'Co má být vedle tabů. Terminál se ukazuje jen ve vývojářském režimu — ' +
      'kdo jede jen v agentovi, nemá vedle tabů tlačítko, které nikdy ' +
      'nezmáčkne.');
    const cfg = state.config.newtab || {};
    const def = (state.agents || []).find((a) => a.id === state.default_agent);
    const list = el('div', 'onb-list');
    for (const [key, label] of [['agent', 'Otevřít ' + (def ? def.label : 'agenta')],
                                ['shell', 'Otevřít terminál']]) {
      const row = el('label', 'onb-row');
      const cb = el('input');
      cb.type = 'checkbox';
      // `claude` je jméno klíče z verzí do 1.6 — starý konfig se tím pádem
      // nemusí přepisovat a zaškrtnutí se ukládá do obou, ať se nerozejdou.
      cb.checked = key === 'agent'
        ? (cfg.agent !== false && cfg.claude !== false) : cfg[key] !== false;
      cb.onchange = () => save({newtab: key === 'agent'
        ? {...cfg, agent: cb.checked, claude: cb.checked}
        : {...cfg, [key]: cb.checked}});
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
    if (state.config.brain_dir) box.appendChild(autosave());

    // Záloha paměti do privátního repa je práce s GitHubem: chce `gh`, umí si
    // říct o instalaci a o přihlášení. V základním režimu se o ní mlčí — i to
    // varování „bez zálohy" je pro někoho, kdo git nepoužívá, jen otrava.
    const git = state.vault_git || {};
    const line = el('div', 'set-row');
    if (!state.config.dev_mode) {
      // nic: ani stav, ani nabídka
    } else if (git.is_repo && git.remote) {
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

  /* Automatické ukládání: když session ztichne (20 min) nebo se tab zavře,
     Claude na pozadí sám doplní poznámku k projektu a případný poznatek.
     Stojí to trochu z předplatného, tak to jde vypnout — a je vidět, co zapsal. */
  function autosave() {
    const wrap = el('div');
    const row = el('label', 'onb-row');
    const cb = el('input');
    cb.type = 'checkbox';
    cb.checked = state.memory_autosave !== false;
    cb.onchange = () => save({memory_autosave: cb.checked});
    row.appendChild(cb);
    // div, ne span: `.onb-body label > span` by z popisu udělal tučný nadpis.
    const col = el('div', 'onb-col');
    col.appendChild(el('span', null, 'Ukládat do paměti samo'));
    col.appendChild(el('small', null,
      'Po skončení práce (zavřený tab, konec session nebo 20 minut ticha) si ' +
      'Claude na pozadí doplní poznámku k projektu a to, co stojí za zapamatování. ' +
      'Nemusíš klikat na Uložit ani psát /save. Jedno uložení bere zhruba tolik ' +
      'jako jedna delší odpověď.'));
    row.appendChild(col);
    wrap.appendChild(row);

    const recent = state.autosave_recent || [];
    if (recent.length) {
      wrap.appendChild(el('div', 'set-note', 'Naposledy uloženo:'));
      const list = el('div', 'onb-list');
      for (const e of recent) {
        const line = el('div', 'onb-row');
        const c = el('span', 'onb-col');
        const when = new Date(e.at).toLocaleString('cs-CZ',
          {day: 'numeric', month: 'numeric', hour: '2-digit', minute: '2-digit'});
        const what = e.status === 'saved'
          ? (e.files || []).filter((f) => f !== 'MEMORY.md').join(', ')
          : 'nepovedlo se: ' + (e.detail || 'neznámá chyba');
        c.appendChild(el('span', e.status === 'saved' ? null : 'set-warn', what));
        c.appendChild(el('small', null,
          [when, (e.projects || []).join(', ')].filter(Boolean).join(' · ')));
        line.appendChild(c);
        list.appendChild(line);
      }
      wrap.appendChild(list);
    }
    return wrap;
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

  /* ── AI agenti ─────────────────────────────────────────────────────────────
     Kdo je na stroji, kým se otevírají projekty, a jak doinstalovat zbytek.
     Verze se zjišťují spuštěním každého CLI, takže to jede na pozadí stejně
     jako MCP a mezitím je vidět, že se pracuje. */
  let agentsLast = null;

  function agenti() {
    const box = section('AI agenti',
      'Každý tab se otevírá jedním z nich. Volba u projektu se pamatuje, ' +
      'takže e-shop může jezdit v Claudeovi a experiment v něčem jiném.');
    const summary = el('div', 'set-row');
    const list = el('div', 'onb-list');
    const ollama = el('div', 'set-note');
    const btns = el('div', 'onb-btns');
    const check = el('button', 'btn ghost', 'Zkontrolovat znovu');
    btns.appendChild(check);
    box.append(summary, list, ollama, btns);

    function busy(text) {
      summary.textContent = '';
      summary.appendChild(el('span', 'set-dim', text));
      check.disabled = true;
    }

    function openIn(kind, title) {
      close();
      io.openTab({kind, path: state.home, title});
    }

    function row(a, def) {
      const r = el('div', 'onb-row');
      const dot = el('span', a.path ? 'set-ok' : 'set-dim', a.path ? '●' : '○');
      dot.style.color = a.path ? a.color : '';
      const col = el('span', 'onb-col');
      const head = el('span', null, a.label + (a.version ? '  ' + a.version : ''));
      if (a.id === def) head.appendChild(el('b', 'set-dim', '  · výchozí'));
      col.appendChild(head);
      col.appendChild(el('small', null, a.path
        ? (a.note || a.path)
        : (a.install ? 'není nainstalovaný — ' + a.install : 'není nainstalovaný')));
      r.append(dot, col, el('span', 'spacer'));

      if (!a.path && a.install) {
        const inst = el('button', 'btn ghost', 'Nainstalovat');
        inst.title = a.install + '\n\nPustí se v tabu, ať je vidět, co se děje.';
        inst.onclick = () => openIn('install:' + a.id, 'instalace: ' + a.label);
        r.appendChild(inst);
      }
      if (a.path && (a.auth || {}).cmd) {
        const auth = el('button', 'btn ghost', 'Přihlásit');
        auth.title = (a.auth.note || a.auth.cmd);
        auth.onclick = () => openIn('auth:' + a.id, 'přihlášení: ' + a.label);
        r.appendChild(auth);
      }
      if (a.path && a.id !== def) {
        const use = el('button', 'btn ghost', 'Jako výchozí');
        use.onclick = async () => {
          await save({default_agent: a.id});
          agentsLast = null;
          load(true);
        };
        r.appendChild(use);
      }
      return r;
    }

    function draw(data) {
      check.disabled = false;
      agentsLast = data;
      const all = data.agents || [];
      const ready = all.filter((a) => a.path);
      summary.textContent = '';
      summary.appendChild(el('span', null,
        ready.length + ' z ' + all.length + ' k dispozici'));
      list.textContent = '';
      for (const a of all) list.appendChild(row(a, data.default));
      // Ollama je zvláštní: nainstalovaná nestačí, musí i běžet — bez toho
      // z ní opencode ani aider nedostanou jediný model.
      const o = data.ollama || {};
      if (!o.installed) {
        ollama.textContent = 'Lokální modely: Ollama není nainstalovaná. ' +
          'S ní umí opencode i aider jet u tebe na počítači, bez placení za tokeny.';
      } else if (!o.running) {
        ollama.textContent = 'Lokální modely: Ollama je nainstalovaná, ale neběží ' +
          '(spusť ollama serve). Dokud neběží, opencode ani aider z ní model nedostanou.';
      } else if (!(o.models || []).length) {
        ollama.textContent = 'Lokální modely: Ollama běží, ale žádný model není ' +
          'stažený (třeba ollama pull qwen2.5-coder).';
      } else {
        ollama.textContent = 'Lokální modely z Ollamy: ' + o.models.join(', ') +
          ' — nabídnou se v bublině u opencode, aidera i v tabu Ollamy.';
      }
    }

    async function load(refresh) {
      busy(refresh ? 'zjišťuji, co je na stroji…' : 'načítám…');
      try {
        let data = await io.api('agents' + (refresh ? '?refresh=1' : ''));
        // Detekce běží na pozadí (spouští se každé CLI s --version), tak se
        // na ni počká stejně jako u MCP — po sekundách, ne blokujícím čekáním.
        for (let i = 0; data.running && i < 40; i++) {
          await new Promise((r) => setTimeout(r, 500));
          data = await io.api('agents');
        }
        if (data.running) { busy('pořád zjišťuji…'); return; }
        draw(data);
      } catch (err) {
        busy('nepodařilo se zjistit: ' + err);
      }
    }

    check.onclick = () => load(true);
    if (agentsLast) draw(agentsLast); else load(false);
    return box;
  }

  /* Účet na serveru.
   *
   * Hub běží na dvou místech: na počítači u projektů, na serveru v prostoru,
   * který je jen tvůj. Tahle sekce je to, co je spojuje — a vypadá jinak podle
   * toho, kde se zrovna kreslí:
   *
   *  - na počítači: přihlásit se (adresa → ověřit → heslo), přejít do prostoru
   *    na serveru, odhlásit;
   *  - na serveru: kdo jsi, zpátky na počítač, odhlásit.
   *
   * Přechod mezi nimi je přechod celého okna, ne nová záložka: appka si
   * pamatuje, kde jsi byl naposledy, a příště se otevře tam.
   */
  function ucet() {
    // Na serverovou instanci píše brána údaje o uživateli do konfigurace,
    // takže se pozná podle nich — ne podle adresy, ta může být za proxy jaká chce.
    const naServeru = !!(state.config && state.config.gateway_user);

    const box = section('Účet',
      naServeru
        ? 'Pracuješ ve svém prostoru na serveru. Projekty, paměť i napojení ' +
          'jsou tvoje a nikdo jiný na ně nevidí.'
        : 'Na serveru máš vlastní prostor, který běží pořád a dostaneš se na ' +
          'něj odkudkoli — z téhle appky i z telefonu.');

    const body = el('div');
    box.appendChild(body);

    function busy(text) {
      body.textContent = '';
      body.appendChild(el('div', 'set-note', text));
    }

    function who(u, extra) {
      body.appendChild(el('div', 'acc-who', u.name || u.email || ''));
      body.appendChild(el('div', 'set-note',
        [u.name ? u.email : '',
         u.role === 'admin' ? 'správce' : 'uživatel',
         u.vault ? 'paměť „' + u.vault + '"' : '',
         extra || ''].filter(Boolean).join(' · ')));
    }

    // ---- hub na serveru ----
    function drawServer() {
      const u = state.config.gateway_user || {};
      const zpet = HubServer.localBack();
      body.textContent = '';
      body.appendChild(el('div', 'set-ok', '✓ Jsi ve svém prostoru na serveru'));
      who(u, location.host);

      const row = el('div', 'set-row');
      if (zpet) {
        const local = el('button', 'btn primary', 'Pracovat na tomto počítači');
        local.title = 'Okno se vrátí na hub v tvém počítači. Prostor na ' +
          'serveru běží dál a přihlášení zůstává.';
        local.onclick = () => HubServer.backTo('local');
        row.appendChild(local);
        row.appendChild(el('span', 'spacer'));
      }
      const out = el('button', 'btn ghost', 'Odhlásit se');
      out.title = zpet
        ? 'Odhlásí appku z tohoto serveru. Příště se zeptá znovu.'
        : 'Odhlásí tenhle prohlížeč. Ostatní zařízení zůstanou přihlášená.';
      out.onclick = async () => {
        out.disabled = true;
        if (!zpet) { location.href = '/logout'; return; }
        // Cookie okna i token appky jsou tentýž token — brána ho zneplatní
        // jednou. Appka na počítači si ho pak ještě zahodí sama.
        try { await fetch('/logout', {credentials: 'same-origin'}); } catch (_) {}
        HubServer.backTo('logout');
      };
      row.appendChild(out);
      body.appendChild(row);

      if (!zpet) {
        body.appendChild(el('div', 'set-note',
          'Otevřeno v prohlížeči. Na počítači se mezi ním a serverem ' +
          'přepíná v appce Claude Code Hub.'));
      }
      body.appendChild(HubPredplatne.serverBlock(io));
      body.appendChild(HubPocitac.serverBlock(io));
      body.appendChild(zabezpeceni());
    }

    /* Zabezpečení na serveru: dvoufázové ověření a heslo. Mluví přímo s bránou
       (/gw/…), ne s hubem v prostoru — ten o heslech nic neví. */
    function zabezpeceni() {
      const wrap = el('div', 'acc-sec');
      wrap.appendChild(el('div', 'set-title', 'Zabezpečení'));
      const gw = async (path, payload) => {
        const r = await fetch(path, payload === undefined ? {credentials: 'same-origin'} : {
          method: 'POST', credentials: 'same-origin',
          headers: {'Content-Type': 'application/json', 'X-Hub-Account': '1'},
          body: JSON.stringify(payload),
        });
        const data = await r.json().catch(() => ({error: 'HTTP ' + r.status}));
        if (!r.ok) throw new Error(data.error || 'HTTP ' + r.status);
        return data;
      };

      const tfa = el('div', 'set-note', 'Dvoufázové ověření: zjišťuji…');
      wrap.appendChild(tfa);
      const codesBox = el('div');
      wrap.appendChild(codesBox);
      gw('/gw/account').then((d) => {
        const t = d.twofa || {};
        tfa.textContent = '';
        tfa.appendChild(el('span', t.enabled ? 'set-ok' : 'set-warn',
          t.enabled ? '✓ Dvoufázové ověření zapnuté' : '! Dvoufázové ověření není zapnuté'));
        if (!t.enabled) return;
        tfa.appendChild(document.createTextNode(' · záložních kódů zbývá ' + t.recovery_left));
        const row = el('div', 'set-row');
        const again = el('button', 'btn ghost', 'Nové záložní kódy');
        again.onclick = newCodes;
        row.appendChild(again);
        codesBox.appendChild(row);
      }, (e) => { tfa.textContent = 'Stav ověření se nenačetl: ' + e.message; });

      function newCodes() {
        codesBox.textContent = '';
        const row = el('div', 'set-row');
        const code = el('input', 'srv-input acc-code');
        code.placeholder = 'kód z aplikace';
        code.inputMode = 'numeric';
        code.autocomplete = 'one-time-code';
        const go = el('button', 'btn primary', 'Vygenerovat');
        row.appendChild(code);
        row.appendChild(go);
        codesBox.appendChild(row);
        codesBox.appendChild(el('div', 'set-note', 'Staré záložní kódy tím přestanou platit.'));
        const run = async () => {
          go.disabled = true;
          try {
            const d = await gw('/gw/2fa/recovery', {code: code.value});
            codesBox.textContent = '';
            codesBox.appendChild(el('div', 'set-note',
              'Nové záložní kódy — každý platí jednou. Ulož si je, znovu se neukážou.'));
            const list = el('div', 'srv-codes');
            for (const c of d.recovery) list.appendChild(el('code', '', c));
            codesBox.appendChild(list);
          } catch (e) {
            io.toast(e.message);
            go.disabled = false;
          }
        };
        go.onclick = run;
        code.onkeydown = (ev) => { if (ev.key === 'Enter') run(); };
        code.focus();
      }

      wrap.appendChild(el('div', 'set-title acc-sub', 'Změna hesla'));
      const form = el('div', 'acc-pass');
      const field = (placeholder, auto) => {
        const input = el('input', 'srv-input');
        input.type = 'password';
        input.placeholder = placeholder;
        input.autocomplete = auto;
        form.appendChild(input);
        return input;
      };
      const cur = field('současné heslo', 'current-password');
      const nw = field('nové heslo (aspoň 10 znaků)', 'new-password');
      const nw2 = field('nové heslo znovu', 'new-password');
      const row = el('div', 'set-row');
      const save = el('button', 'btn primary', 'Změnit heslo');
      row.appendChild(save);
      form.appendChild(row);
      form.appendChild(el('div', 'set-note',
        'Po změně se odhlásí ostatní zařízení i appka na počítači; tenhle ' +
        'prohlížeč zůstane přihlášený.'));
      wrap.appendChild(form);
      save.onclick = async () => {
        if (!cur.value) return io.toast('Vyplň současné heslo.');
        if (nw.value.length < 10) return io.toast('Nové heslo musí mít aspoň 10 znaků.');
        if (nw.value !== nw2.value) return io.toast('Nová hesla se neshodují.');
        save.disabled = true;
        try {
          await gw('/gw/password', {current: cur.value, new: nw.value});
          cur.value = nw.value = nw2.value = '';
          io.toast('Heslo změněno. Ostatní zařízení jsou odhlášená.');
        } catch (e) {
          io.toast(e.message);
        }
        save.disabled = false;
      };
      return wrap;
    }

    // ---- hub na počítači, nepřihlášený ----
    function drawLogin(data) {
      body.textContent = '';
      if (data.note) body.appendChild(el('div', 'set-warn', '! ' + data.note));
      body.appendChild(HubServer.panel(io, {
        server: data.server || state.config.gw_server,
        email: (data.user && data.user.email) || state.config.gw_email,
        autoProbe: false,
        onReady: (res) => {
          state.config.gw_server = res.server;
          state.config.gw_logged_in = true;
          io.toast('Přihlášeno.');
          draw(res);
        },
      }));
    }

    // ---- hub na počítači, přihlášený ----
    function drawIn(data) {
      const u = data.user || {};
      body.textContent = '';

      body.appendChild(el('div', data.offline ? 'set-warn' : 'set-ok',
        data.offline ? '! Server teď neodpovídá' : '✓ Přihlášeno'));
      who(u, data.host);
      if (data.offline && data.note) {
        body.appendChild(el('div', 'set-note', data.note +
          ' Zůstáváš přihlášený, jen se teď k serveru nedá.'));
      }

      const err = el('div', 'set-warn');
      err.hidden = true;

      const row = el('div', 'set-row');
      const open = el('button', 'btn primary', 'Otevřít můj prostor');
      open.title = 'Okno přejde na server. Taby na počítači běží dál a ' +
        'appka se příště otevře rovnou v prostoru.';
      open.disabled = !!data.offline;
      open.onclick = async () => {
        open.disabled = true;
        open.textContent = 'otevírám…';
        const res = await HubServer.go(io).catch((e) => ({error: e.message}));
        if (res.ok) return;
        err.textContent = '! ' + (res.error || 'Nepovedlo se.');
        err.hidden = false;
        open.disabled = false;
        open.textContent = 'Otevřít můj prostor';
        if (res.kind === 'auth') load();
      };
      row.appendChild(open);

      row.appendChild(el('span', 'spacer'));
      const out = el('button', 'btn ghost', 'Odhlásit se');
      out.title = 'Zahodí přihlášení tohohle počítače. Ostatní zařízení ' +
        'zůstanou přihlášená.';
      out.onclick = async () => {
        out.disabled = true;
        try {
          const res = await io.api('account', {action: 'logout'});
          state.config.gw_logged_in = false;
          state.config.server_mode = false;
          draw(res);
          io.toast('Odhlášeno.');
        } catch (e) {
          io.toast('Nepovedlo se: ' + e.message);
          out.disabled = false;
        }
      };
      row.appendChild(out);
      body.appendChild(row);
      body.appendChild(err);

      body.appendChild(el('div', 'set-note',
        'Appka si pamatuje, kde jsi pracoval naposledy. Ze serveru se sem ' +
        'vrátíš v jeho nastavení: Účet → Pracovat na tomto počítači. Heslo ' +
        'a záložní kódy změníš tamtéž, v Nastavení → Účet v prostoru na serveru.'));
      body.appendChild(HubPredplatne.panel(io));
      body.appendChild(HubPocitac.panel(io));
    }

    function draw(data) {
      if (data && data.logged_in) return drawIn(data);
      drawLogin(data || {});
    }

    function load() {
      busy('zjišťuji stav…');
      io.api('account', {action: 'status'}).then(draw,
        (err) => drawLogin({note: err.message}));
    }

    if (naServeru) drawServer(); else load();
    return box;
  }

  /* Služby — karta na službu, pod ní účty (hub/connect.py). Pro člověka, který
     chce „napojit Freelo", ne registrovat MCP server: popisek účtu, Přihlásit,
     a na serveru vložit adresu, kam přihlášení přesměrovalo. Víc účtů u jedné
     služby je normální (firma, osobní). */
  function sluzby() {
    const wrap = el('div', 'svc-list');
    wrap.appendChild(el('div', 'set-dim', 'Načítám služby…'));
    let data = null;
    let timer = null;
    const wait = (ms) => new Promise((res) => setTimeout(res, ms));

    async function load(refresh) {
      clearTimeout(timer);
      try {
        data = await io.api('connect' + (refresh ? '?refresh=1' : ''));
      } catch (err) {
        wrap.textContent = '';
        wrap.appendChild(el('div', 'set-warn', 'Služby se nenačetly: ' + err.message));
        return;
      }
      if (!wrap.isConnected && wrap.parentNode === null && data) { /* ještě nevložené */ }
      // Rozdělané přihlášení se nepřekresluje — člověk by přišel o vložený text.
      if (!wrap.querySelector('.svc-login')) draw();
      if (data.checking) timer = setTimeout(() => load(false), 2500);
    }

    function draw() {
      wrap.textContent = '';
      for (const svc of data.services || []) wrap.appendChild(card(svc));
    }

    function field(parent, label, placeholder, help) {
      const row = el('label', 'mcp-field');
      row.appendChild(el('span', null, label));
      const input = el('input');
      input.type = 'text';
      input.placeholder = placeholder || label;
      input.autocomplete = 'off';
      row.appendChild(input);
      if (help) row.appendChild(el('small', null, help));
      parent.appendChild(row);
      return input;
    }

    function card(svc) {
      const box = el('div', 'svc-card');
      box.dataset.service = svc.id;
      const head = el('div', 'svc-head');
      head.appendChild(el('strong', null, svc.label));
      head.appendChild(el('span', 'svc-note', svc.note));
      box.appendChild(head);

      const list = el('div', 'svc-accounts');
      if (!(svc.accounts || []).length) list.appendChild(el('div', 'svc-empty', 'Zatím žádný účet.'));
      for (const acc of svc.accounts || []) list.appendChild(accountRow(svc, acc));
      box.appendChild(list);
      if (svc.warn) box.appendChild(el('div', 'mcp-warn', svc.warn));

      if (svc.missing) {
        box.appendChild(el('div', 'set-warn svc-missing', svc.missing));
        if (svc.setup) {
          const toggle = el('button', 'btn ghost svc-add', 'Nastavit Google');
          const form = googleClient(svc);
          form.hidden = true;
          toggle.onclick = () => { form.hidden = !form.hidden; };
          box.appendChild(toggle);
          box.appendChild(form);
        }
        return box;
      }
      const add = el('button', 'btn ghost svc-add', '+ Přidat účet');
      const form = el('div', 'svc-form');
      form.hidden = true;
      add.onclick = () => {
        form.hidden = !form.hidden;
        add.textContent = form.hidden ? '+ Přidat účet' : 'Zavřít';
        if (!form.hidden) {
          addForm(svc, form);
          const first = form.querySelector('input');
          if (first) first.focus();
        }
      };
      box.appendChild(add);
      box.appendChild(form);
      return box;
    }

    function accountRow(svc, acc) {
      const [cls, dot] = MCP_STATES[acc.state] || MCP_STATES.unknown;
      const wrapRow = el('div', 'svc-acc-wrap');
      const row = el('div', 'svc-acc');
      row.appendChild(el('span', 'mcp-dot ' + cls, dot));
      const col = el('span', 'onb-col');
      col.appendChild(el('span', null, acc.label));
      col.appendChild(el('small', null, [acc.status, acc.detail].filter(Boolean).join(' · ')));
      row.appendChild(col);
      row.appendChild(el('span', 'spacer'));
      const slot = el('div', 'svc-slot');
      if (acc.state !== 'ok' && acc.state !== 'unknown') {
        const again = el('button', 'btn ghost', 'Přihlásit');
        again.onclick = async () => {
          again.disabled = true;
          try {
            const r = svc.kind === 'google'
              ? await io.api('connect', {action: 'add', service: svc.id})
              : await io.api('connect', {action: 'login', name: acc.name});
            startLogin(svc, r, slot);
          } catch (err) {
            io.toast('Nepovedlo se: ' + err.message);
            again.disabled = false;
          }
        };
        row.appendChild(again);
      }
      const del = el('button', 'set-x', '×');
      del.title = 'Odebrat účet';
      del.onclick = async () => {
        if (!confirm(`Odebrat účet ${acc.label} (${svc.label})?`)) return;
        try {
          const r = await io.api('connect', {action: 'remove', service: svc.id, name: acc.name});
          io.toast(r.detail || 'Odebráno.');
          load(true);
        } catch (err) { io.toast('Nepovedlo se: ' + err.message); }
      };
      row.appendChild(del);
      wrapRow.appendChild(row);
      wrapRow.appendChild(slot);
      return wrapRow;
    }

    function addForm(svc, form) {
      form.textContent = '';
      const inputs = {};
      if (svc.kind !== 'google') {
        inputs.label = field(form, 'Popisek účtu', 'třeba firma nebo osobní',
          'Podle něj účty poznáš ty i Claude.');
      }
      if (svc.field) inputs.account = field(form, svc.field.label, '', svc.field.help);
      const go = el('button', 'btn primary', svc.kind === 'google' ? 'Přihlásit Google účet' : 'Přihlásit');
      form.appendChild(go);
      const slot = el('div', 'svc-slot');
      form.appendChild(slot);
      for (const input of Object.values(inputs)) {
        input.onkeydown = (ev) => { if (ev.key === 'Enter') go.click(); };
      }
      go.onclick = async () => {
        const payload = {action: 'add', service: svc.id};
        for (const [key, input] of Object.entries(inputs)) {
          payload[key] = input.value.trim();
          if (!payload[key]) {
            io.toast(key === 'label' ? 'Pojmenuj účet, ať se dají poznat.' : 'Vyplň ' + svc.field.label + '.');
            input.focus();
            return;
          }
        }
        go.disabled = true;
        go.textContent = 'Připravuju přihlášení…';
        try {
          const r = await io.api('connect', payload);
          go.hidden = true;
          for (const input of Object.values(inputs)) input.disabled = true;
          startLogin(svc, r, slot);
        } catch (err) {
          io.toast('Nepovedlo se: ' + err.message);
          go.disabled = false;
          go.textContent = svc.kind === 'google' ? 'Přihlásit Google účet' : 'Přihlásit';
        }
      };
    }

    /* Přihlášení: tlačítko na stránku služby a pole na adresu, kam to potom
       přesměrovalo. Na počítači to většinou doběhne samo (hlídá se stav). */
    function startLogin(svc, r, slot) {
      const login = r.login || {};
      if (login.done) {
        io.toast(login.message || 'Přihlášeno.');
        load(true);
        return;
      }
      slot.textContent = '';
      const p = el('div', 'svc-login');
      p.appendChild(el('div', 'svc-step', '1. Otevři přihlášení a potvrď přístup.'));
      const openBtn = el('button', 'btn primary', 'Otevřít přihlášení — ' + svc.label);
      openBtn.onclick = () => io.open(login.url);
      p.appendChild(openBtn);
      p.appendChild(el('div', 'svc-step', data && data.on_server
        ? '2. Stránka, kam tě to pak pošle, se nenačte — to je v pořádku. Zkopíruj celou adresu z adresního řádku a vlož ji sem:'
        : '2. Hotovo se ukáže samo. Kdyby ne, vlož sem adresu z adresního řádku stránky, kam tě to poslalo:'));
      const row = el('div', 'svc-paste');
      const input = el('input');
      input.type = 'text';
      input.placeholder = 'http://localhost…';
      input.autocomplete = 'off';
      const done = el('button', 'btn primary', 'Dokončit');
      row.appendChild(input);
      row.appendChild(done);
      p.appendChild(row);
      const status = el('div', 'set-note svc-status');
      p.appendChild(status);
      const cancel = el('button', 'linkbtn', 'Zrušit');
      p.appendChild(cancel);
      slot.appendChild(p);

      let stopped = false;
      const finish = (res) => {
        stopped = true;
        io.toast(res.message || 'Napojeno.');
        slot.textContent = '';
        load(true);
      };
      done.onclick = async () => {
        const value = input.value.trim();
        if (!value) { input.focus(); return; }
        done.disabled = true;
        status.className = 'set-note svc-status';
        status.textContent = 'Ověřuju…';
        try {
          const res = await io.api('connect', {action: 'finish', id: login.id, url: value});
          if (res.done && res.ok) { finish(res); return; }
          status.className = 'set-warn svc-status';
          status.textContent = res.message || 'Přihlášení se nepovedlo.';
          if (res.done) stopped = true;
          else done.disabled = false;
        } catch (err) {
          status.className = 'set-warn svc-status';
          status.textContent = err.message;
          done.disabled = false;
        }
      };
      input.onkeydown = (ev) => { if (ev.key === 'Enter') done.click(); };
      cancel.onclick = () => {
        stopped = true;
        io.api('connect', {action: 'cancel', id: login.id}).catch(() => {});
        slot.textContent = '';
        load(false);
      };
      (async () => {
        while (!stopped) {
          await wait(1500);
          if (stopped || !p.isConnected) break;
          let st;
          try {
            st = await io.api('connect', {action: 'status', id: login.id});
          } catch (_) { continue; }
          if (stopped) break;
          if (st.done && st.ok) { finish(st); break; }
          if (st.done) {
            stopped = true;
            status.className = 'set-warn svc-status';
            status.textContent = st.message || 'Přihlášení se nepovedlo.';
            done.disabled = true;
          }
        }
      })();
    }

    // Klient OAuth pro Google na počítači (na serveru ho drží správce brány).
    function googleClient(svc) {
      const f = el('div', 'svc-form');
      const steps = el('ol', 'mcp-steps');
      for (const step of svc.setup || []) {
        const li = el('li', 'mcp-step');
        const head = el('div', 'mcp-step-head');
        head.appendChild(el('strong', null, step.title));
        if (step.url) {
          head.appendChild(el('span', 'spacer'));
          const go = el('button', 'btn ghost', step.button || 'Otevřít');
          go.onclick = () => io.open(step.url);
          head.appendChild(go);
        }
        li.appendChild(head);
        li.appendChild(el('div', 'set-note', step.text));
        steps.appendChild(li);
      }
      f.appendChild(steps);
      const id = field(f, 'Client ID', '….apps.googleusercontent.com');
      const secret = field(f, 'Client secret');
      secret.type = 'password';
      const save = el('button', 'btn primary', 'Uložit klienta');
      save.onclick = async () => {
        save.disabled = true;
        try {
          const r = await io.api('connect', {action: 'google-client',
                                             client_id: id.value, client_secret: secret.value});
          io.toast(r.detail || 'Uloženo.');
          load(false);
        } catch (err) {
          io.toast('Nepovedlo se: ' + err.message);
          save.disabled = false;
        }
      };
      f.appendChild(save);
      return f;
    }

    load(false);
    return wrap;
  }

  function napojeni() {
    const box = section('Napojení (MCP) — Claude Code',
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
    box.appendChild(sluzby());
    box.appendChild(el('div', 'set-title svc-tech', 'Všechna napojení'));
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

    /* Přidání z katalogu — „rozšíření": klikneš a napojí se.

       Údaje, na které se ptáme, jdou rovnou do `claude mcp add`; hub si je
       nikam neukládá a do logu se nedostanou. Rozsah je tu schválně na očích:
       globální napojení platí všude, projektové se zapíše do .mcp.json ve
       složce a veze se s repem, takže ho má i další člověk v týmu. */
    function pridat(key, spec) {
      const wrap = el('div', 'mcp-add');
      const head = el('div', 'set-row');
      head.appendChild(el('strong', null, spec.label));
      head.appendChild(el('span', 'spacer'));
      const open = el('button', 'btn ghost', 'Napojit');
      head.appendChild(open);
      wrap.appendChild(head);
      wrap.appendChild(el('div', 'set-note', spec.note));

      // Odkud server je. U cizího kódu, který se bude spouštět, to má být
      // vidět dřív, než se na něj klikne — ne až někde v dokumentaci.
      const meta = el('div', 'mcp-meta');
      if (spec.source) {
        meta.appendChild(el('span', 'mcp-tag', 'open source'));
        meta.appendChild(el('span', null, spec.source));
      }
      if (spec.license) meta.appendChild(el('span', null, spec.license));
      if (spec.needs) meta.appendChild(el('span', null, 'spouští ' + spec.needs));
      if ((spec.setup || []).length) {
        meta.appendChild(el('span', 'mcp-tag guide',
          'návod na ' + spec.setup.length + ' kroky'));
      }
      if (spec.docs) {
        const a = el('button', 'linkbtn', 'návod');
        a.onclick = () => io.api('open-path', {path: spec.docs})
          .catch(() => io.toast('Nepodařilo se otevřít odkaz.'));
        meta.appendChild(a);
      }
      if (meta.children.length) wrap.appendChild(meta);

      const form = el('div', 'mcp-form');
      form.hidden = true;

      /* Návod přímo v okně. Google klienta OAuth nerozdá a odkaz do
         dokumentace znamená hledání v cizí konzoli — tady je u každého kroku
         tlačítko, které otevře přesně tu stránku, o které krok mluví.
         Odškrtnuté kroky přežijí zavření nastavení, ať se člověk po přerušení
         vrátí tam, kde skončil. */
      const DONE_KEY = 'hub.mcp-setup:' + key;
      let done = [];
      try {
        const raw = JSON.parse(localStorage.getItem(DONE_KEY) || '[]');
        if (Array.isArray(raw)) done = raw;
      } catch (err) { /* soukromé okno */ }

      const steps = spec.setup || [];
      if (steps.length) {
        const list = el('ol', 'mcp-steps');
        steps.forEach((step, i) => {
          const li = el('li', 'mcp-step' + (done.includes(i) ? ' done' : ''));
          const head = el('div', 'mcp-step-head');
          head.appendChild(el('strong', null, step.title));
          if (step.url) {
            const go = el('button', 'btn ghost', step.button || 'Otevřít');
            go.onclick = () => {
              io.api('open-path', {path: step.url})
                .catch(() => io.toast('Nepodařilo se otevřít odkaz.'));
              if (!done.includes(i)) done.push(i);
              li.classList.add('done');
              try {
                localStorage.setItem(DONE_KEY, JSON.stringify(done));
              } catch (err) { /* nevadí, jen se to nezapamatuje */ }
            };
            head.appendChild(el('span', 'spacer'));
            head.appendChild(go);
          }
          li.appendChild(head);
          li.appendChild(el('div', 'set-note', step.text));
          list.appendChild(li);
        });
        form.appendChild(list);
      }
      if (spec.warn) form.appendChild(el('div', 'mcp-warn', spec.warn));

      const inputs = [];
      for (const field of spec.fields || []) {
        const row = el('label', 'mcp-field');
        row.appendChild(el('span', null, field.label));
        const input = el('input');
        input.type = field.secret ? 'password' : 'text';
        input.placeholder = field.label;
        input.autocomplete = 'off';
        row.appendChild(input);
        if (field.help) row.appendChild(el('small', null, field.help));
        form.appendChild(row);
        inputs.push([field.name, input]);
      }

      // Kam se to zapíše. Projekt si vybírá složku, jinak by nebylo kam.
      const where = el('div', 'set-row');
      const scope = el('select', 'set-input');
      for (const [value, label] of [['user', 'Všude (globálně)'],
                                    ['project', 'Jen v jedné složce']]) {
        scope.appendChild(Object.assign(el('option', null, label), {value}));
      }
      where.appendChild(el('span', null, 'Kde má platit'));
      where.appendChild(scope);
      const folder = el('button', 'btn ghost', 'Vybrat složku…');
      folder.hidden = true;
      let path = '';
      folder.onclick = async () => {
        const picked = await io.pickFolder();
        if (!picked) return;
        path = picked;
        folder.textContent = picked.split(/[\\/]/).pop() || picked;
        folder.title = picked;
      };
      where.appendChild(folder);
      scope.onchange = () => { folder.hidden = scope.value !== 'project'; };
      form.appendChild(where);

      const go = el('button', 'btn primary', 'Napojit');
      form.appendChild(go);
      wrap.appendChild(form);

      open.onclick = () => {
        form.hidden = !form.hidden;
        // Dvě tlačítka „Napojit" vedle sebe by mátla — tohle jen otevírá pole.
        open.textContent = form.hidden ? 'Napojit' : 'Zavřít';
        const first = inputs.length ? inputs[0][1] : null;
        if (!form.hidden && first) first.focus();
      };
      for (const [, input] of inputs) {
        input.onkeydown = (ev) => { if (ev.key === 'Enter') go.click(); };
      }
      go.onclick = async () => {
        const values = {};
        for (const [name, input] of inputs) {
          const v = input.value.trim();
          if (!v) { io.toast('Vyplň ' + name + '.'); input.focus(); return; }
          values[name] = v;
        }
        if (scope.value === 'project' && !path) {
          io.toast('Vyber složku, ve které to má platit.');
          return;
        }
        go.disabled = true;
        go.textContent = 'Napojuju…';
        try {
          const r = await io.api('mcp', {action: 'add', name: key,
                                         values, scope: scope.value, path});
          io.toast(r.detail || 'Hotovo.');
        } catch (err) {
          io.toast('Nepovedlo se: ' + err.message);
          go.disabled = false;
          go.textContent = 'Napojit';
          return;
        }
        for (const [, input] of inputs) input.value = '';
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

  /* Hub na serveru (brána): zdroj patří serveru a novou verzi si server
     připraví sám (gateway/update.sh). Instalovat do domova prostoru nemá
     smysl — nabízí se jen přepnutí prostoru na připravenou verzi. */
  function aktualizaceServer() {
    const box = section('Aktualizace aplikace',
      'Novou verzi si server připraví sám, na pozadí — tvému prostoru se nic ' +
      'nevymění pod rukama. Když je hotová, hub nabídne Aktualizovat: prostor ' +
      'se přepne a taby se vrátí i s konverzacemi.');
    const row = el('div', 'set-row');
    row.appendChild(el('span', 'set-ver', 'Tvůj prostor jede na: ' + state.version.version));
    box.appendChild(row);
    // Připravenou novější verzi jde zapnout i odsud (hub.js, hubServerUpdate).
    fetch('/gw/verze', {credentials: 'same-origin'})
      .then((r) => (r.ok ? r.json() : null))
      .then((v) => {
        const nova = v && v.nejnovejsi;
        if (!nova || nova === state.version.version || !window.hubServerUpdate) return;
        const btns = el('div', 'onb-btns');
        const go = el('button', 'actionbtn', 'Přepnout na verzi ' + nova);
        go.onclick = () => window.hubServerUpdate();
        btns.appendChild(go);
        row.after(btns);
      }).catch(() => { /* starší brána */ });
    const status = el('div', 'set-status');
    status.textContent = 'Zjišťuju, jak dopadla poslední aktualizace serveru…';
    box.appendChild(status);
    io.api('update-check').then((v) => {
      const s = (v && v.server) || {};
      if (!s.checked_at) {
        status.textContent = 'Aktualizace serveru zatím neproběhla.';
        return;
      }
      const when = new Date(s.checked_at * 1000).toLocaleString('cs-CZ');
      status.className = 'set-status ' + (s.ok === false ? 'warn' : 'ok');
      status.textContent = `Naposledy ${when}: ${s.detail || ''}`;
    }).catch(() => { status.textContent = ''; });
    return box;
  }

  function aktualizace() {
    if (state.config && state.config.gateway_user) return aktualizaceServer();
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
          doIt.hidden = true;
          info.textContent = 'Nainstalováno: ' + r.now;
          /* Nová verze se projeví až po startu, tak si hub sáhne na restart
             sám. Otevřené taby se před tím uloží a nové okno je zase otevře —
             u Clauda se pokračuje v téže konverzaci (`--resume`). */
          status.textContent = `✓ Nainstalována verze ${r.now}. Restartuju…`;
          try {
            const res = await io.api('restart');
            status.textContent = res && res.tabs
              ? `✓ Verze ${r.now}. Restartuju a vracím ${res.tabs} ` +
                (res.tabs === 1 ? 'tab…' : res.tabs < 5 ? 'taby…' : 'tabů…')
              : `✓ Verze ${r.now}. Restartuju…`;
          } catch (err) {
            status.className = 'set-status warn';
            status.textContent = `✓ Nainstalována verze ${r.now}, ale restart se ` +
              'nepovedl: ' + err.message + ' Zavři a znovu otevři aplikaci.';
          }
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
        // Přes io.copy: na serveru by /api/clipboard psal do schránky serveru.
        await io.copy(r.text);
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
      // Hub si obnoví svůj stav (kvůli liště tlačítek pod nastavením)…
      await io.refreshState();
      // …ale `io.state` je zmrazená kopie: nastavení se otevírá přes
      // `{...hubIO(), state: STATE}` a spread getter `state` vyhodnotí na
      // hodnotu. Čerstvá data si tedy musíme vzít sami, jinak se panel
      // překreslí ze starých — a přepnutí, které mění, co je vidět, vypadá
      // jako by nefungovalo.
      state = await io.api('state');
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
