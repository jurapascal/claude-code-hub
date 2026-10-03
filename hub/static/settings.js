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
  // Propojené služby a Pluginy jsou od 2.57.2 jedna sekce.
  if (active === 'napojeni' || active === 'pluginy') active = 'rozsireni';

  // Pořadí je i pořadím v panelu vlevo: napřed to, co se mění nejčastěji,
  // servis (aktualizace, logy) až na konci.
  const SECTIONS = [
    ['vzhled',     'Vzhled',    'i-sun',      () => vzhled()],
    // `dev` = jen ve vývojářském režimu. Kdo hub používá na psaní s agentem,
    // neřeší složky s projekty, zálohu paměti ani tlačítka nových tabů.
    ['projekty',   'Projekty',  'i-folder',   () => projekty(), 'dev'],
    ['taby',       'Taby',      'i-terminal', () => taby(), 'dev'],
    // Bez vývojářského režimu jen modely přes API (Jev) — CLI agenti ne.
    ['agenti',     'AI agenti', 'i-hub',      () => agenti()],
    ['pamet',      'Paměť',     'i-book',     () => pamet(), 'dev'],
    ['ucet',       'Účet',      'i-user',     () => ucet()],
    ['hlas',       'Hlas',      'i-mic',      () => hlas()],
    // Všechno, čím se Claude rozšiřuje — služby, pluginy, skilly, MCP, pluginy appky.
    ['rozsireni',  'Napojení a pluginy', 'i-hub', () => rozsireni()],
    ['aktualizace','Aktualizace', 'i-up',     () => aktualizace()],
    ['logy',       'Logy',      'i-status',   () => logy(), 'dev'],
    ['ostatni',    'Ostatní',   'i-gear',     () => ostatni()],
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
    if (opts.tab === 'napojeni' || opts.tab === 'pluginy') opts.tab = 'rozsireni';
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
    const dev = advanced();
    const u = (state && state.config && state.config.gateway_user) || null;
    const admin = !!(u && u.role === 'admin');
    return SECTIONS.filter(([, , , , flag]) => (flag !== 'dev' || dev) && (flag !== 'admin' || admin));
  }

  // Pro pokročilé (config.dev_mode) — jinak jednoduchý režim bez techniky.
  const advanced = () => !!(state && state.config && state.config.dev_mode);

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
          io.api('config', {theme: ''}).catch(() => {});
          io.setTheme(!matchMedia('(prefers-color-scheme: light)').matches, false);
        } else {
          io.setTheme(dark, true);
        }
        render();
      };
      wrap.appendChild(t);
    }
    box.appendChild(wrap);

    box.appendChild(appka());
    return box;
  }

  /* Jednoduchý režim je výchozí: aplikace se chová jako chat s Claudem a nic
     technického neukazuje. Vývojářský režim se zapíná jen tady (ne v průvodci)
     a ukáže všechno do detailu. */
  function ostatni() {
    const box = section('Ostatní');
    box.appendChild(el('div', 'set-title', 'Vývojářský režim'));
    box.appendChild(el('div', 'set-note',
      'Aplikace je nastavená jednoduše — na psaní s Claudem, poznámky a soubory. ' +
      'Vývojářský režim ukáže všechno do detailu. Běžně ho nepotřebuješ.'));
    const row = el('label', 'onb-row');
    const cb = el('input');
    cb.type = 'checkbox';
    cb.checked = advanced();
    cb.onchange = () => save({dev_mode: cb.checked});
    row.appendChild(cb);
    row.appendChild(el('span', null, 'Zapnout vývojářský režim'));
    box.appendChild(row);
    box.appendChild(el('div', 'set-note',
      'Přibude: projekty a jejich složky, terminál, výběr modelu a příkazy v poli ' +
      'na psaní, nahrávání webů a GitHub, spotřeba tokenů, jiní AI pomocníci, ' +
      'technické podrobnosti napojení, cesty na disku a záznam chyb. Vypnutím se ' +
      'zase schová — nic se nesmaže.'));
    return box;
  }

  /* Vlastní název a ikona appky (hub/vzhled.py). Ikonu kreslí prohlížeč —
     emoji na barevném pozadí, nebo nahraný obrázek — do PNG 512/192/32 a hub
     je uloží. Platí v liště, pro ikonu na ploše telefonu i pro spouštěč. */
  const EMOJI = ['🏠', '👨‍👩‍👧‍👦', '🐱', '🐶', '🦄', '🌻', '⭐', '🚀', '🌈', '❤️',
                 '🎨', '📚', '🧸', '🍀', '☀️', '🐝', '🦊', '🐼', '🎈', '🤖'];
  const BARVY = ['#e0843c', '#4c97f0', '#3fb950', '#a371f7', '#f85149',
                 '#f2cc60', '#ff7ab6', '#1b2129', '#ffffff'];

  function appka() {
    const app = state.app || {};
    const box = el('div', 'app-edit');
    box.appendChild(el('div', 'set-title', 'Název a ikona appky'));
    box.appendChild(el('div', 'set-note',
      'Pojmenuj si appku a vyber ikonu — ukáže se v liště, na ploše telefonu ' +
      '(po znovupřidání na plochu) i v nabídce aplikací na počítači.'));

    let volba = {emoji: '🏠', barva: BARVY[0], obrazek: null};
    let zmenaIkony = false;

    const radek = el('div', 'app-row');
    const nahled = el('canvas', 'app-nahled');
    nahled.width = nahled.height = 96;
    const jmeno = el('input', 'set-input app-name');
    jmeno.type = 'text';
    jmeno.maxLength = 40;
    jmeno.placeholder = app.default || 'Claude Code Hub';
    jmeno.value = app.name || '';
    radek.append(nahled, jmeno);
    box.appendChild(radek);

    const emo = el('div', 'app-grid');
    // Vlastní obrázek na prvním místě — fotka rodiny, kreslený obrázek, logo.
    const vlastni = el('button', 'app-emoji app-vlastni');
    vlastni.type = 'button';
    vlastni.title = 'Nahrát vlastní obrázek (fotku, logo…)';
    vlastni.innerHTML = '<svg class="ico"><use href="#i-image"/></svg><span>Vlastní</span>';
    vlastni.onclick = () => soubor.click();
    emo.appendChild(vlastni);
    for (const e of EMOJI) {
      const b = el('button', 'app-emoji', e);
      b.type = 'button';
      b.onclick = () => { volba = {...volba, emoji: e, obrazek: null}; zmenaIkony = true; kresli(); };
      emo.appendChild(b);
    }
    box.appendChild(emo);

    const barvy = el('div', 'app-grid');
    for (const c of BARVY) {
      const b = el('button', 'app-barva');
      b.type = 'button';
      b.style.background = c;
      b.title = c;
      b.onclick = () => { volba = {...volba, barva: c}; zmenaIkony = true; kresli(); };
      barvy.appendChild(b);
    }
    box.appendChild(barvy);

    const soubor = el('input');
    soubor.type = 'file';
    soubor.accept = 'image/*';
    soubor.hidden = true;
    soubor.onchange = () => {
      const f = soubor.files && soubor.files[0];
      if (!f) return;
      const img = new Image();
      img.onload = () => { volba = {...volba, obrazek: img}; zmenaIkony = true; kresli(); };
      img.onerror = () => io.toast('Tenhle obrázek se nepodařilo načíst.');
      img.src = URL.createObjectURL(f);
    };
    box.appendChild(soubor);

    const btns = el('div', 'onb-btns');
    const nahrat = el('button', 'actionbtn', 'Nahrát vlastní obrázek');
    nahrat.title = 'Fotka nebo obrázek z počítače či telefonu — ořízne se na čtverec';
    nahrat.onclick = () => soubor.click();
    const ulozit = el('button', 'actionbtn primary', 'Uložit');
    const vychozi = el('button', 'actionbtn', 'Vrátit výchozí');
    btns.append(nahrat, ulozit, vychozi);
    box.appendChild(btns);

    function vykresli(size) {
      const c = document.createElement('canvas');
      c.width = c.height = size;
      const g = c.getContext('2d');
      if (volba.obrazek) {
        // Obrázek vyplní celý čtverec (střed, oříznutý na výšku/šířku).
        const im = volba.obrazek;
        const k = Math.max(size / im.naturalWidth, size / im.naturalHeight);
        const w = im.naturalWidth * k, h = im.naturalHeight * k;
        g.drawImage(im, (size - w) / 2, (size - h) / 2, w, h);
      } else {
        g.fillStyle = volba.barva;
        g.fillRect(0, 0, size, size);
        // Emoji zabere zhruba polovinu — zbytek je okraj, který si Android
        // (maskovatelná ikona) může ořezat do kruhu nebo kapky.
        g.font = Math.round(size * 0.52) + 'px "Apple Color Emoji","Segoe UI Emoji","Noto Color Emoji",sans-serif';
        g.textAlign = 'center';
        g.textBaseline = 'middle';
        g.fillText(volba.emoji, size / 2, size * 0.54);
      }
      return c;
    }

    function kresli() {
      const g = nahled.getContext('2d');
      g.clearRect(0, 0, 96, 96);
      g.drawImage(vykresli(512), 0, 0, 96, 96);
    }

    // Zatím uložená ikona jako výchozí náhled.
    if (app.icon) {
      const im = new Image();
      im.onload = () => { volba = {...volba, obrazek: im}; kresli(); };
      im.src = '/app-ikona/512.png?v=' + app.icon;
    } else {
      kresli();
    }

    ulozit.onclick = async () => {
      ulozit.disabled = true;
      try {
        const payload = {name: jmeno.value.trim()};
        if (zmenaIkony) {
          payload.icons = {};
          for (const n of [512, 192, 32]) payload.icons[n] = vykresli(n).toDataURL('image/png');
        }
        await io.api('app-vzhled', payload);
        await io.refreshState();
        state = await io.api('state');
        io.toast('Uloženo. V appce platí hned. Ikonu na ploše Android obnoví sám ' +
                 '(při dalším spuštění, nejpozději do dne), na iPhonu appku přidej na plochu znovu.');
        render();
      } catch (err) {
        io.toast(err.message);
      }
      ulozit.disabled = false;
    };
    vychozi.onclick = async () => {
      try {
        await io.api('app-vzhled', {reset: true});
        await io.refreshState();
        state = await io.api('state');
        render();
      } catch (err) { io.toast(err.message); }
    };
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
      'Claude si pamatuje, na čem jste spolu pracovali — i v dalším rozhovoru. ' +
      'Najdeš to v Mých poznámkách.');
    if (advanced()) {
      box.appendChild(Object.assign(el('div', 'onb-path'),
        {textContent: state.config.brain_dir || '(vypnutá)'}));
    }
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

    // Jiná složka s pamětí a přesun — jen vývojářský režim.
    if (!advanced()) return box;
    const vaults = (state.vaults || []).filter(v => v.path !== state.config.brain_dir);
    if (vaults.length) {
      box.appendChild(el('div', 'set-note', 'Použít jinou složku s poznámkami:'));
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
      'Když rozhovor skončí (zavřeš ho, nebo 20 minut nikdo nepíše), Claude si ' +
      'sám zapíše, co stojí za zapamatování. Nemusíš nic ukládat.'));
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
    if (!advanced()) {
      const only = section('AI agenti',
        'Modely, které si Claude umí zavolat na pomoc. Vyplníš údaje a je napojeno.');
      only.appendChild(jev());
      return only;
    }
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
        : 'není nainstalovaný'));
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
    box.appendChild(jev());
    return box;
  }

  /* Jev (TypeSafe) — model, ne agent: neotevírá se v tabu, volá ho Claude
     na hromadná rozhodnutí (skill jev). Postup je u něj, ať ho každý najde
     tam, kde se to vyplňuje; text kroků posílá server (hub/jev.py). */
  function jev() {
    const wrap = el('div', 'svc-card');
    const head = el('div', 'onb-row');
    const dot = el('span', 'set-dim', '○');
    const col = el('span', 'onb-col');
    col.appendChild(el('span', null, 'Jev · TypeSafe AI'));
    const sub = el('small', null, 'načítám…');
    col.appendChild(sub);
    head.append(dot, col, el('span', 'spacer'));
    const toggle = el('button', 'btn ghost', 'Nastavit');
    const off = el('button', 'btn ghost', 'Odpojit');
    off.hidden = true;
    head.append(toggle, off);
    const form = el('div', 'svc-form');
    form.hidden = true;
    wrap.append(head, form);

    function input(label, placeholder, help) {
      const row = el('label', 'mcp-field');
      row.appendChild(el('span', null, label));
      const i = el('input');
      i.type = 'text';
      i.placeholder = placeholder;
      i.autocomplete = 'off';
      i.spellcheck = false;
      row.appendChild(i);
      if (help) row.appendChild(el('small', null, help));
      form.appendChild(row);
      return i;
    }

    function draw(s) {
      dot.className = s.configured ? 'set-ok' : 'set-dim';
      dot.textContent = s.configured ? '●' : '○';
      sub.textContent = s.configured
        ? 'Napojeno přes Cloudflare (účet ' + s.account_id.slice(0, 6) + '…, token ' +
          s.token_hint + '). Claude ho volá na hromadné třídění — stačí mu to říct.'
        : 'Levný model na hromadná rozhodnutí (ano/ne, kategorie, skóre). ' +
          'Claude si ho zavolá třeba na třídění stovek e-mailů.';
      toggle.textContent = s.configured ? 'Změnit údaje' : 'Nastavit';
      off.hidden = !s.configured;

      form.textContent = '';
      const steps = el('ol', 'mcp-steps');
      for (const step of s.setup || []) {
        const li = el('li', 'mcp-step');
        const h = el('div', 'mcp-step-head');
        h.appendChild(el('strong', null, step.title));
        if (step.url) {
          h.appendChild(el('span', 'spacer'));
          const go = el('button', 'btn ghost', step.button || 'Otevřít');
          go.onclick = () => io.open(step.url);
          h.appendChild(go);
        }
        li.appendChild(h);
        li.appendChild(el('div', 'set-note', step.text));
        steps.appendChild(li);
      }
      form.appendChild(steps);
      const acc = input('Account ID', '32 znaků, např. 0123456789abcdef…');
      acc.value = s.account_id || '';
      const tok = input('API token', s.configured ? 'nech prázdné = token zůstane' : 'token z Cloudflare',
        'Uloží se jen k tobě do ~/.claude/jev.json, do prohlížeče se už nevrací.');
      tok.type = 'password';
      const status = el('div', 'set-note');
      const go = el('button', 'btn primary', 'Ověřit a napojit');
      go.onclick = async () => {
        go.disabled = true;
        status.className = 'set-note';
        status.textContent = 'Ověřuju — posílám Jevovi zkušební otázku…';
        try {
          const r = await io.api('jev', {action: 'save', account_id: acc.value,
                                         api_token: tok.value});
          io.toast(r.message || 'Napojeno.');
          form.hidden = true;
          draw(r);
        } catch (err) {
          status.className = 'set-warn';
          status.textContent = err.message;
          go.disabled = false;
        }
      };
      tok.onkeydown = (ev) => { if (ev.key === 'Enter') go.click(); };
      form.append(go, status);
    }

    toggle.onclick = () => {
      form.hidden = !form.hidden;
      if (!form.hidden) (form.querySelector('input') || {focus() {}}).focus();
    };
    off.onclick = async () => {
      if (!confirm('Odpojit Jev? Údaje se z tohohle počítače smažou.')) return;
      try { draw(await io.api('jev', {action: 'remove'})); }
      catch (err) { io.toast(err.message); }
    };
    io.api('jev').then(draw).catch((err) => { sub.textContent = 'nepodařilo se zjistit: ' + err.message; });
    return wrap;
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
  /* Účet claude.ai, pod kterým je Claude Code přihlášený. Na něm visí
     konektory z claude.ai (Gmail, Drive…) — jiný účet = jiné konektory. */
  function claudeUcet() {
    const wrap = el('div', 'acc-claude');
    wrap.appendChild(el('div', 'set-title plg-podnadpis', 'Předplatné Claude'));
    const radek = el('div', 'mcp-acct');
    radek.appendChild(el('span', 'set-dim', 'Zjišťuji účet…'));
    wrap.appendChild(radek);
    io.api('claude-ucet').then(({account: a}) => {
      radek.textContent = '';
      const col = el('span', 'onb-col');
      if (a && a.email) {
        col.appendChild(el('span', null, a.email));
        col.appendChild(el('small', null, 'Předplatné: ' + (a.plan || 'neznámé') +
          (a.org ? ' · ' + a.org : '') + (a.name ? ' · ' + a.name : '')));
        col.appendChild(el('small', null, 'Konektory z claude.ai patří k tomuhle účtu.'));
      } else {
        col.appendChild(el('span', 'set-warn', 'Claude Code není přihlášený'));
        col.appendChild(el('small', null, 'Bez přihlášení Claude nepracuje a konektory z claude.ai nejsou vidět.'));
      }
      radek.appendChild(col);
      radek.appendChild(el('span', 'spacer'));
      const swap = el('button', 'btn ghost', a && a.email ? 'Přepnout účet' : 'Přihlásit');
      swap.title = 'Přihlásí Claude k jinému účtu. Jiný účet = jiné konektory (třeba druhá gmailová schránka).';
      swap.onclick = () => { close(); io.login(); };
      radek.appendChild(swap);
    }, () => { radek.textContent = ''; radek.appendChild(el('span', 'set-warn', 'Účet se nepodařilo zjistit.')); });
    return wrap;
  }

  function ucet() {
    // Na serverovou instanci píše brána údaje o uživateli do konfigurace,
    // takže se pozná podle nich — ne podle adresy, ta může být za proxy jaká chce.
    const naServeru = !!(state.config && state.config.gateway_user);

    const box = section('Účet',
      naServeru
        ? ''
        : 'Na serveru máš vlastní prostor, který běží pořád a dostaneš se na ' +
          'něj odkudkoli — z téhle appky i z telefonu.');

    const body = el('div');
    box.appendChild(body);
    const claudeBox = claudeUcet();
    box.appendChild(claudeBox);

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
    // Karta s ikonkou v nadpisu — Claude, počítač, zabezpečení vedle sebe
    // přehledně, ne jako jeden dlouhý text pod sebou.
    function card(block, ico) {
      block.classList.add('acc-card');
      const title = block.querySelector('.set-title');
      if (title) {
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('class', 'ico');
        const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
        use.setAttribute('href', '#' + ico);
        svg.appendChild(use);
        title.prepend(svg);
      }
      return block;
    }

    function drawServer() {
      const u = state.config.gateway_user || {};
      const zpet = HubServer.localBack();
      body.textContent = '';

      // Kdo jsem: iniciály, jméno, e-mail a role — a odhlášení hned vedle.
      const prof = el('div', 'acc-profile');
      const name = u.name || u.email || '';
      const initials = name.split(/[\s.@]+/).filter(Boolean).slice(0, 2)
        .map((w) => w[0].toUpperCase()).join('');
      prof.appendChild(el('span', 'acc-avatar', initials || '?'));
      const col = el('span', 'acc-id');
      col.appendChild(el('strong', null, name));
      col.appendChild(el('small', null, [u.name ? u.email : '',
        u.role === 'admin' ? 'správce' : ''].filter(Boolean).join(' · ')));
      prof.appendChild(col);
      const acts = el('span', 'acc-acts');
      if (zpet) {
        const local = el('button', 'btn primary', 'Na tomto počítači');
        local.title = 'Okno se vrátí na hub v tvém počítači. Prostor na ' +
          'serveru běží dál a přihlášení zůstává.';
        local.onclick = () => HubServer.backTo('local');
        acts.appendChild(local);
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
      acts.appendChild(out);
      prof.appendChild(acts);
      body.appendChild(prof);
      body.appendChild(claudeBox);      // účet a předplatné hned pod profilem

      const cards = el('div', 'acc-cards');
      cards.appendChild(card(HubPredplatne.serverBlock(io), 'i-hub'));
      cards.appendChild(card(HubPocitac.serverBlock(io), 'i-laptop'));
      cards.appendChild(card(zabezpeceni(), 'i-lock'));
      body.appendChild(cards);
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
        const data = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(data.error || 'Server teď neodpovídá, zkus to za chvíli.');
        return data;
      };

      const tfa = el('div', 'set-note', 'Dvoufázové ověření: zjišťuji…');
      wrap.appendChild(tfa);
      // Tlačítka vedle sebe; formuláře se rozbalí až po kliknutí.
      const actions = el('div', 'set-row');
      const passBtn = el('button', 'btn ghost', 'Změnit heslo');
      actions.appendChild(passBtn);
      wrap.appendChild(actions);
      const codesBox = el('div');
      wrap.appendChild(codesBox);
      gw('/gw/account').then((d) => {
        const t = d.twofa || {};
        tfa.textContent = '';
        tfa.appendChild(el('span', t.enabled ? 'set-ok' : 'set-warn',
          t.enabled ? '✓ Dvoufázové ověření zapnuté' : '! Dvoufázové ověření není zapnuté'));
        if (!t.enabled) return;
        tfa.appendChild(document.createTextNode(' · záložních kódů zbývá ' + t.recovery_left));
        const again = el('button', 'btn ghost', 'Nové záložní kódy');
        again.onclick = newCodes;
        actions.prepend(again);
        passkeys();
      }, (e) => { tfa.textContent = 'Stav ověření se nenačetl: ' + e.message; });

      /* Passkey jako druhý krok přihlášení (gateway/webauthn.py): místo
         opisování kódu otisk prstu, obličej, PIN nebo bezpečnostní klíč.
         Kód z aplikace zůstává jako záloha a pro appku na počítači. */
      const pkBox = el('div', 'acc-pk');
      wrap.insertBefore(pkBox, actions.nextSibling);
      const b64d = (t) => Uint8Array.from(atob(t.replace(/-/g, '+').replace(/_/g, '/') + '==='.slice((t.length + 3) % 4)), (c) => c.charCodeAt(0));
      const b64e = (b) => btoa(String.fromCharCode(...new Uint8Array(b))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
      const kdy = (ts) => ts ? new Date(ts * 1000).toLocaleDateString('cs-CZ') : 'zatím ne';
      function kresliPk(list) {
        pkBox.textContent = '';
        pkBox.appendChild(el('div', 'set-note', list.length
          ? 'Passkey — přihlášení otiskem prstu, obličejem nebo bezpečnostním klíčem místo kódu:'
          : 'Passkey: přihlašuj se otiskem prstu, obličejem nebo bezpečnostním klíčem místo opisování kódu.'));
        for (const k of list) {
          const r = el('div', 'set-row acc-pk-radek');
          r.appendChild(el('span', 'acc-pk-jmeno', '🔑 ' + (k.name || 'Passkey')));
          r.appendChild(el('span', 'set-note', 'přidán ' + kdy(k.created) + ' · naposledy ' + kdy(k.used)));
          const del = el('button', 'btn ghost', 'Odebrat');
          del.onclick = async () => {
            if (!confirm('Odebrat passkey „' + (k.name || 'Passkey') + '“? Přihlásíš se pak kódem z aplikace.')) return;
            try { kresliPk((await gw('/gw/passkey', {action: 'delete', id: k.id})).passkeys); }
            catch (e) { io.toast(e.message); }
          };
          r.appendChild(del);
          pkBox.appendChild(r);
        }
        const add = el('button', 'btn ghost', '+ Přidat passkey');
        add.onclick = pridatPk;
        if (!window.PublicKeyCredential) {
          add.disabled = true;
          add.title = 'Tenhle prohlížeč passkey neumí.';
        }
        pkBox.appendChild(add);
      }
      async function passkeys() {
        try { kresliPk((await gw('/gw/passkey')).passkeys || []); }
        catch (_) { /* starší brána bez passkeyů — nic neukazovat */ }
      }
      async function pridatPk() {
        const vychozi = /iPhone|iPad/.test(navigator.userAgent) ? 'iPhone' : /Android/.test(navigator.userAgent) ? 'Android'
          : /Mac/.test(navigator.platform) ? 'Mac' : /Win/.test(navigator.platform) ? 'Windows' : 'Počítač';
        const name = prompt('Jak se má passkey jmenovat? (třeba „Můj telefon“)', vychozi);
        if (name === null) return;
        try {
          const o = await gw('/gw/passkey', {action: 'options'});
          const c = await navigator.credentials.create({publicKey: {
            challenge: b64d(o.challenge), rp: o.rp,
            user: {id: b64d(o.user.id), name: o.user.name, displayName: o.user.displayName},
            pubKeyCredParams: o.algs.map((alg) => ({type: 'public-key', alg})),
            excludeCredentials: o.exclude.map((id) => ({type: 'public-key', id: b64d(id)})),
            authenticatorSelection: {residentKey: 'preferred', userVerification: 'preferred'},
            attestation: 'none', timeout: 120000}});
          const d = await gw('/gw/passkey', {action: 'register', name: name.trim(),
            clientData: b64e(c.response.clientDataJSON), attestation: b64e(c.response.attestationObject)});
          kresliPk(d.passkeys);
          io.toast('Passkey přidán — příště se přihlásíš bez opisování kódu.');
        } catch (e) {
          io.toast(e && e.name === 'NotAllowedError' ? 'Přidání passkeye bylo zrušené.'
            : e && e.name === 'InvalidStateError' ? 'Tenhle passkey už je přidaný.' : (e.message || 'Passkey se nepřidal.'));
        }
      }

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

      const form = el('div', 'acc-pass');
      form.hidden = true;
      passBtn.onclick = () => {
        form.hidden = !form.hidden;
        if (!form.hidden) form.querySelector('input').focus();
      };
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
          form.hidden = true;
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

  // Brána: sdílená napojení a sdílení účtů ze služeb (gateway/mcp_sdilene.py).
  async function gwShared(payload) {
    const r = await fetch('/gw/mcp-sdilene', payload === undefined ? {credentials: 'same-origin'} : {
      method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', 'X-Hub-Account': '1'},
      body: JSON.stringify(payload),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || 'Server teď neodpovídá, zkus to za chvíli.');
    return data;
  }

  /* Výběr lidí zaškrtávátky — kdo účet nebo napojení uvidí. */
  function peopleChecks(people, chosen) {
    const box = el('div', 'svc-people');
    const me = ((state.config && state.config.gateway_user) || {}).email || '';
    const others = people.filter((p) => p.email !== me);
    if (!others.length) box.appendChild(el('span', 'set-dim', 'Na serveru zatím nikdo další není.'));
    for (const p of others) {
      const row = el('label', 'svc-person');
      const cb = el('input');
      cb.type = 'checkbox';
      cb.value = p.email;
      cb.checked = chosen.has(p.email);
      row.append(cb, el('span', null, p.name || p.email));
      box.appendChild(row);
    }
    box.value = () => [...box.querySelectorAll('input:checked')].map((c) => c.value);
    return box;
  }

  /* Ikonka služby, ať ji člověk pozná na první pohled (hub/static/sluzby/,
     oficiální ikony z webů služeb — nic se nenačítá zvenku). */
  const SVC_ICONS = ['freelo', 'canva', 'ecomail', 'clockify', 'google', 'wordpress',
                     'facebook', 'reklamy'];
  const SVC_ICON_FILE = {facebook: 'facebook-instagram', reklamy: 'meta'};
  function svcIcon(id) {
    const key = String(id || '').toLowerCase();
    if (!SVC_ICONS.includes(key)) return null;
    const img = el('img', 'svc-ico');
    img.src = '/sluzby/' + (SVC_ICON_FILE[key] || key) + '.png';
    img.alt = '';
    return img;
  }

  /* Služby — karta na službu, pod ní účty (hub/connect.py). Pro člověka, který
     chce „napojit Freelo", ne registrovat MCP server: popisek účtu, Přihlásit,
     a na serveru vložit adresu, kam přihlášení přesměrovalo. Víc účtů u jedné
     služby je normální (firma, osobní). */
  function sluzby(onData) {
    const wrap = el('div', 'svc-list');
    wrap.appendChild(el('div', 'set-dim', 'Načítám služby…'));
    let data = null;
    // Na serveru: kdo co s kým sdílí (brána). Účet jde nasdílet dalším lidem
    // a nasdílené od ostatních se ukážou v kartě služby.
    const onServer = !!(state.config && state.config.gateway_user);
    let shared = null;
    const sharedItems = (svcId) => ((shared && shared.napojeni) || [])
      .filter((n) => n.kind === 'ucet' && n.service === svcId);
    let timer = null;
    const wait = (ms) => new Promise((res) => setTimeout(res, ms));

    async function load(refresh) {
      clearTimeout(timer);
      try {
        [data, shared] = await Promise.all([
          io.api('connect' + (refresh ? '?refresh=1' : '')),
          onServer ? gwShared().catch(() => null) : null,
        ]);
      } catch (err) {
        wrap.textContent = '';
        wrap.appendChild(el('div', 'set-warn', 'Služby se nenačetly: ' + err.message));
        return;
      }
      if (!wrap.isConnected && wrap.parentNode === null && data) { /* ještě nevložené */ }
      // Rozdělané přihlášení se nepřekresluje — člověk by přišel o vložený text.
      // …ani rozdělaný výběr lidí u sdílení.
      if (!wrap.querySelector('.svc-login, .svc-share')) draw();
      if (onData) onData(data);
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
      const ico = svcIcon(svc.id);
      if (ico) head.appendChild(ico);
      head.appendChild(el('strong', null, svc.label));
      head.appendChild(el('span', 'svc-note', svc.note));
      box.appendChild(head);

      const list = el('div', 'svc-accounts');
      const fromOthers = sharedItems(svc.id).filter((n) => !n.is_owner);
      if (!(svc.accounts || []).length && !fromOthers.length) {
        list.appendChild(el('div', 'svc-empty',
          svc.kind === 'custom' ? 'Zatím žádné vlastní napojení.' : 'Zatím žádný účet.'));
      }
      for (const acc of svc.accounts || []) list.appendChild(accountRow(svc, acc));
      for (const n of fromOthers) list.appendChild(sharedRow(n));
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
      const addText = svc.kind === 'custom' ? '+ Přidat napojení' : '+ Přidat účet';
      const add = el('button', 'btn ghost svc-add', addText);
      const form = el('div', 'svc-form');
      form.hidden = true;
      add.onclick = () => {
        form.hidden = !form.hidden;
        add.textContent = form.hidden ? addText : 'Zavřít';
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
      const mine = sharedItems(svc.id).find((n) => n.is_owner && n.account === acc.name);
      const members = (mine && mine.members) || [];
      if (members.length) {
        col.appendChild(el('small', 'svc-sharing',
          'vidí i: ' + members.map((m) => m.name || m.email).join(', ')));
      }
      row.appendChild(col);
      row.appendChild(el('span', 'spacer'));
      const slot = el('div', 'svc-slot');
      if (onServer && shared) {
        const share = el('button', 'btn ghost', members.length ? 'Sdíleno' : 'Sdílet');
        share.title = 'Vyber, kdo další může tenhle účet používat';
        share.onclick = () => {
          if (slot.querySelector('.svc-share')) { slot.textContent = ''; return; }
          slot.textContent = '';
          slot.appendChild(shareForm(svc, acc, members, slot));
        };
        row.appendChild(share);
      }
      // API klíč se znovu nepřihlašuje — špatný klíč = odebrat a přidat nový.
      if (acc.state !== 'ok' && acc.state !== 'unknown' && svc.kind !== 'apikey' && svc.kind !== 'token') {
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
        if (!confirm(svc.kind === 'custom' ? `Odebrat napojení ${acc.label}?`
                                           : `Odebrat účet ${acc.label} (${svc.label})?`)) return;
        try {
          const r = await io.api('connect', {action: 'remove', service: svc.id, name: acc.name});
          if (mine) {
            await gwShared({akce: 'sdilet-ucet', service: svc.id, account: acc.name, emaily: []})
              .catch(() => {});
          }
          io.toast(r.detail || 'Odebráno.');
          load(true);
        } catch (err) { io.toast('Nepovedlo se: ' + err.message); }
      };
      row.appendChild(del);
      wrapRow.appendChild(row);
      wrapRow.appendChild(slot);
      return wrapRow;
    }

    /* Kdo další účet uvidí. Klíče ani přihlášení se nikam nekopírují — brána
       je čte z tvého prostoru, ostatní dostanou jen most (sdilene-…). */
    function shareForm(svc, acc, members, slot) {
      const f = el('div', 'svc-form svc-share');
      f.appendChild(el('div', 'set-note', 'Kdo další může účet ' + acc.label +
        ' používat? Budou pracovat pod tvým přihlášením' +
        (svc.id === 'google' ? ' — uvidí tvůj Gmail, Disk i Kalendář.' : '.') +
        ' Přihlašovací údaje neuvidí nikdo z nich.'));
      const picker = peopleChecks((shared && shared.people) || [],
                                  new Set(members.map((m) => m.email)));
      const save = el('button', 'btn primary', 'Uložit');
      const cancel = el('button', 'btn ghost', 'Zrušit');
      const btns = el('div', 'svc-acc');
      btns.append(save, cancel);
      f.append(picker, btns);
      cancel.onclick = () => { slot.textContent = ''; };
      save.onclick = async () => {
        save.disabled = true;
        const emaily = picker.value();
        try {
          await gwShared({akce: 'sdilet-ucet', service: svc.id, account: acc.name,
                          label: acc.label, emaily});
          io.toast(emaily.length ? 'Účet ' + acc.label + ' teď vidí i vybraní lidé.'
                                 : 'Účet ' + acc.label + ' už s nikým nesdílíš.');
          load(false);
        } catch (err) {
          io.toast('Nepovedlo se: ' + err.message);
          save.disabled = false;
        }
      };
      return f;
    }

    // Účet, který mi nasdílel někdo jiný — jen ke čtení, spravuje ho vlastník.
    function sharedRow(n) {
      const row = el('div', 'svc-acc-wrap');
      const r = el('div', 'svc-acc');
      r.appendChild(el('span', 'mcp-dot set-ok', '●'));
      const col = el('span', 'onb-col');
      col.appendChild(el('span', null, n.label || n.account));
      col.appendChild(el('small', null, 'nasdílel ' + (n.owner_name || n.owner) +
        ' · v Claude jako ' + n.mcp_name));
      r.appendChild(col);
      row.appendChild(r);
      return row;
    }

    function addForm(svc, form) {
      form.textContent = '';
      const inputs = {};
      if (svc.kind === 'custom') {
        inputs.label = field(form, 'Název', 'třeba Notion',
          'Podle něj napojení poznáš ty i Claude (jeho nástroje budou mcp__název__…).');
      } else if (svc.kind !== 'google') {
        inputs.label = field(form, 'Popisek účtu', 'třeba firma nebo osobní',
          'Podle něj účty poznáš ty i Claude.');
      }
      // Návod krok za krokem (token Mety) — tlačítko otevře přesně tu stránku.
      if (svc.kind === 'token' && (svc.setup || []).length) {
        const steps = el('ol', 'mcp-steps');
        for (const step of svc.setup) {
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
        form.appendChild(steps);
      }
      if (svc.field) {
        inputs.account = field(form, svc.field.label,
          svc.kind === 'custom' ? 'https://…' : '', svc.field.help);
        if (svc.field.secret) inputs.account.type = 'password';
      }
      // Nepovinné jako v oficiální appce: jen pro servery, které si klienta
      // OAuth neumí zaregistrovat samy.
      const optional = {};
      if (svc.kind === 'custom') {
        const adv = el('details', 'svc-adv');
        adv.appendChild(el('summary', null, 'Pokročilá nastavení'));
        optional.client_id = field(adv, 'OAuth Client ID (nepovinné)', '');
        optional.client_secret = field(adv, 'OAuth Client Secret (nepovinné)', '');
        optional.client_secret.type = 'password';
        // Správce hesel jinak vyplní heslo do Secretu a e-mail do Client ID před ním.
        optional.client_secret.autocomplete = 'new-password';
        optional.client_id.autocomplete = 'off';
        optional.client_id.setAttribute('data-lpignore', 'true');
        optional.client_secret.setAttribute('data-lpignore', 'true');
        adv.appendChild(el('small', 'set-note',
          'Vyplň, jen když služba vydává vlastní přihlašovací klienty. ' +
          'Jako adresu pro přesměrování (redirect URI) jim zadej ' + (svc.redirect || '') + '.'));
        form.appendChild(adv);
      }
      const goText = svc.kind === 'google' ? 'Přihlásit Google účet'
        : svc.kind === 'custom' ? 'Přidat'
        : (svc.kind === 'apikey' || svc.kind === 'token') ? 'Napojit' : 'Přihlásit';
      const go = el('button', 'btn primary', goText);
      form.appendChild(go);
      const slot = el('div', 'svc-slot');
      form.appendChild(slot);
      for (const input of [...Object.values(inputs), ...Object.values(optional)]) {
        input.onkeydown = (ev) => { if (ev.key === 'Enter') go.click(); };
      }
      go.onclick = async () => {
        const payload = {action: 'add', service: svc.id};
        for (const [key, input] of Object.entries(inputs)) {
          payload[key] = input.value.trim();
          if (!payload[key]) {
            io.toast(key === 'label' ? (svc.kind === 'custom' ? 'Pojmenuj napojení.'
                                                               : 'Pojmenuj účet, ať se dají poznat.')
                                     : 'Vyplň ' + svc.field.label + '.');
            input.focus();
            return;
          }
        }
        for (const [key, input] of Object.entries(optional)) {
          if (input.value.trim()) payload[key] = input.value.trim();
        }
        go.disabled = true;
        go.textContent = svc.kind === 'custom' ? 'Připojuju se k serveru…' : 'Připravuju přihlášení…';
        try {
          const r = await io.api('connect', payload);
          go.hidden = true;
          for (const input of [...Object.values(inputs), ...Object.values(optional)]) input.disabled = true;
          startLogin(svc, r, slot);
        } catch (err) {
          io.toast('Nepovedlo se: ' + err.message);
          go.disabled = false;
          go.textContent = goText;
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
      const who = svc.kind === 'custom' && r.name ? r.name : svc.label;
      const openBtn = el('button', 'btn primary', 'Otevřít přihlášení — ' + who);
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

  /* Napojení a pluginy: jedna sekce se záložkami. Co do které patří:
       Služby (Freelo, Google… naklikáním), Pluginy Claude Code (katalog),
       Skilly (co Claude umí — moje příkazy, z pluginů, z Obsidianu),
       MCP servery (všechna napojení technicky, se stavem) a Pro appku. */
  const ROZ_TABS = [
    ['mcp', 'Napojení (MCP)', 'i-hub'], ['pluginy', 'Pluginy', 'i-plus'],
    ['skilly', 'Skilly', 'i-book'], ['appka', 'Pro appku', 'i-gear'],
  ];
  function rozsireni() {
    const box = section('Napojení a pluginy',
      'Všechno, čím se Claude rozšiřuje: služby, pluginy, dovednosti (skilly) a napojení.');
    const tabs = el('div', 'plg-tabs roz-tabs');
    const telo = el('div', 'roz-telo');
    let kde = localStorage.getItem('hub-roz-tab') || 'mcp';
    if (!ROZ_TABS.some(([id]) => id === kde)) kde = 'mcp';     // i dřívější „Služby"
    const P = window.HubPluginy;
    const kresli = (id) => {
      kde = id;
      try { localStorage.setItem('hub-roz-tab', id); } catch (_) { /* soukromé okno */ }
      for (const b of tabs.children) b.classList.toggle('on', b.dataset.k === id);
      telo.textContent = '';
      const obsah = id === 'mcp' ? napojeni()
        : !P ? el('div', 'set-note', 'Načítám…')
        : id === 'pluginy' ? P.claudeCode()
        : id === 'skilly' ? P.skilly()
        : P.appka();
      telo.appendChild(obsah);
    };
    for (const [id, text, ico] of ROZ_TABS) {
      const b = el('button', 'plg-tab');
      b.innerHTML = '<svg class="ico"><use href="#' + ico + '"/></svg><span></span>';
      b.querySelector('span').textContent = text;
      b.dataset.k = id;
      b.onclick = () => kresli(id);
      tabs.appendChild(b);
    }
    box.append(tabs, telo);
    kresli(kde);
    return box;
  }

  /* Napojení (MCP): nahoře služby naklikáním (účty, přihlášení, sdílení na
     serveru), pod nimi ostatní MCP servery, které žádná služba nemá —
     z pluginů, z účtu claude.ai, ručně přidané. Nic se neukazuje dvakrát. */
  /* Napojení, která jsou součástí appky (hub je zakládá a spravuje sám). */
  function soucastAppky(s) {
    const t = s.target || '';
    if (s.name === 'playwright' && /playwright_bridge/.test(t)) {
      return {nazev: 'Prohlížeč pro Clauda', ico: 'i-globe', popis: 'vestavěný prohlížeč, který vidíš v appce'};
    }
    if (s.name === 'pocitac' || /pocitac_mcp/.test(t)) {
      return {nazev: 'Spojení s počítačem', ico: 'i-laptop', popis: 'Claude ze serveru sahá na tvůj počítač'};
    }
    if (/^sdilene-/.test(s.name) || /sdilene_mcp/.test(t)) {
      return {nazev: 'Sdílené: ' + s.name.replace(/^sdilene-/, ''), ico: 'i-user', popis: 'napojení, které s tebou sdílí kolega'};
    }
    return null;
  }

  function napojeni() {
    const box = el('div');
    box.appendChild(el('div', 'set-note',
      'Služby a nástroje, se kterými Claude umí pracovat — e-mail, kalendář, úkoly, design… ' +
      'Přidáš je tlačítkem u služby a přihlásíš se jako obvykle.'));
    box.appendChild(el('div', 'set-title plg-podnadpis', 'Služby'));
    let sluzbyServery = null;            // MCP servery, které už mají karty služeb
    box.appendChild(sluzby((d) => {
      const nove = (d.servers || []).join('|');
      if (sluzbyServery && [...sluzbyServery].join('|') === nove) return;   // bez blikání při kontrole
      sluzbyServery = new Set(d.servers || []);
      if (mcpLast) draw(mcpLast);
    }));
    // Přidat další: konektory z claude.ai a katalog MCP serverů z registru.
    if (window.HubPluginy) {
      box.appendChild(el('div', 'set-title plg-podnadpis', 'Přidat další napojení'));
      box.appendChild(HubPluginy.katalogNapojeni(() => {
        // Nové vlastní napojení: překreslit karty služeb, ať jde hned přihlásit.
        const stare = box.querySelector('.svc-list');
        if (stare) stare.replaceWith(sluzby((d) => { sluzbyServery = new Set(d.servers || []); }));
      }));
    }
    box.appendChild(el('div', 'set-title plg-podnadpis', 'Ostatní napojení'));
    const summary = el('div', 'set-row');
    const list = el('div', 'onb-list');
    const btns = el('div', 'onb-btns');
    const check = el('button', 'actionbtn', 'Zkontrolovat znovu');
    btns.appendChild(check);
    const tech = el('div');
    const appBox = el('div', 'mcp-appka');
    appBox.hidden = true;
    tech.append(summary, list, appBox, btns);
    box.appendChild(tech);

    function busy(text) {
      summary.textContent = '';
      summary.appendChild(el('span', 'set-dim', text));
      check.disabled = true;
    }

    function draw(data) {
      check.disabled = false;
      mcpLast = data;
      const servers = data.servers || [];
      const c = data.counts || {};

      if (!data.ok && data.detail) {
        summary.textContent = '';
        summary.appendChild(el('span', 'set-warn', data.detail));
      }
      list.textContent = '';
      if (!servers.length) list.appendChild(el('div', 'empty', '(žádné napojení)'));
      // Funkční nahoře, pak co chce přihlásit, pak zbytek; uvnitř podle abecedy.
      const PORADI = {ok: 0, auth: 1, local: 2};
      const mimoSluzby = sluzbyServery ? servers.filter((x) => !sluzbyServery.has(x.name)) : servers;
      const appky = mimoSluzby.filter((x) => soucastAppky(x));
      const ostatni = mimoSluzby.filter((x) => !soucastAppky(x));
      // Počty jen za to, co je tady vidět (služby mají stav ve svých kartách).
      summary.textContent = '';
      if (data.ok !== false && ostatni.length) {
        const ok = ostatni.filter((x) => x.state === 'ok').length;
        summary.appendChild(el('span', 'set-ok', ok + ' z ' + ostatni.length + ' připojeno'));
      }
      if (!ostatni.length && servers.length) list.appendChild(el('div', 'set-note', 'Žádná další napojení — všechna jsou mezi službami výš.'));
      // Součást appky: prohlížeč, spojení s počítačem, sdílená napojení od
      // kolegů. Spravuje je hub sám — odebrat nejdou, rozbilo by to appku.
      if (appky.length) {
        appBox.textContent = '';
        appBox.hidden = false;
        appBox.appendChild(el('div', 'set-title plg-podnadpis', 'Součást appky'));
        for (const x of appky) {
          const info = soucastAppky(x);
          const [cls, dot] = MCP_STATES[x.state] || MCP_STATES.unknown;
          const row = el('div', 'onb-row mcp-row');
          row.appendChild(window.HubPluginy ? HubPluginy.ikonka(info.nazev, info.ico) : el('span'));
          row.appendChild(Object.assign(el('span', 'mcp-dot ' + cls), {textContent: dot}));
          const col = el('span', 'onb-col');
          col.appendChild(el('span', null, info.nazev));
          col.appendChild(el('small', null, info.popis + (x.state !== 'ok' && x.status ? ' · ' + x.status : '')));
          row.appendChild(col);
          appBox.appendChild(row);
        }
      } else {
        appBox.hidden = true;
      }
      const serazene = ostatni.slice().sort((a, b) =>
        ((PORADI[a.state] ?? 3) - (PORADI[b.state] ?? 3)) ||
        a.name.replace(/^claude\.ai /, '').localeCompare(b.name.replace(/^claude\.ai /, ''), 'cs'));
      const tik = ++drawTik;
      serazene.forEach((s, i) => setTimeout(() => {
        if (tik !== drawTik) return;       // mezitím přišel novější výsledek
        const [cls, dot, fallback] = MCP_STATES[s.state] || MCP_STATES.unknown;
        const row = el('div', 'onb-row mcp-row plg-vstup');
        const jmeno = s.name.replace(/^claude\.ai /, '');
        row.appendChild(window.HubPluginy ? HubPluginy.ikonka(jmeno, s.name.startsWith('claude.ai ') ? 'i-globe' : 'i-hub') : el('span'));
        row.appendChild(Object.assign(el('span', 'mcp-dot ' + cls), {textContent: dot}));
        const col = el('span', 'onb-col');
        col.appendChild(el('span', null, jmeno + (s.name.startsWith('claude.ai ') ? '  · z účtu claude.ai' : '')));
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
      }, i * 45));
    }

    let drawTik = 0;
    async function load(refresh) {
      busy(refresh ? 'Ptám se serverů…' : 'Načítám…');
      let data;
      try {
        data = await io.api('mcp' + (refresh ? '?refresh=1' : ''));
        } catch (err) {
        summary.textContent = '';
        summary.appendChild(el('span', 'set-warn', 'Nepovedlo se: ' + err.message));
        check.disabled = false;
        return;
      }
      while (data.running) {
        busy('Zjišťuji, která napojení odpovídají… (každé se ptá zvlášť, chvíli to trvá)');
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
      'nevymění pod rukama. Když je hotová, aplikace nabídne Aktualizovat: ' +
      'přepne se a otevřené rozhovory se vrátí.');
    const row = el('div', 'set-row');
    row.appendChild(el('span', 'set-ver', 'Tvoje verze: ' + state.version.version));
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

  /* Diktování a předčítání česky (hub/hlas.py, hlas.js). Na počítači se
     instaluje odsud, na serveru ho připravuje správce pro všechny. */
  function hlas() {
    const box = section('Hlas', 'Mluv místo psaní a nech si odpovědi přečíst nahlas.');
    const status = el('div', 'set-status', 'Zjišťuju…');
    box.appendChild(status);
    const btns = el('div', 'onb-btns');
    box.appendChild(btns);
    const ready = el('div', 'hlas-ready');

    // Dlaždice s ikonkou: nadpis a krátký popisek pod ním.
    function tile(ico, title, sub, on) {
      const t = el('button', 'onb-tile hlas-tile' + (on ? ' on' : ''));
      t.innerHTML = '<svg class="hlas-ico"><use href="#' + ico + '"/></svg>';
      const col = el('span');
      col.append(el('span', 'onb-tile-t', title), el('span', 'onb-tile-s', sub));
      t.appendChild(col);
      return t;
    }
    function group(ico, title) {
      const h = el('div', 'hlas-head');
      h.innerHTML = '<svg class="ico"><use href="#' + ico + '"/></svg>';
      h.appendChild(el('span', null, title));
      ready.appendChild(h);
      const tiles = el('div', 'onb-tiles');
      ready.appendChild(tiles);
      return tiles;
    }

    function drawReady(s) {
      ready.textContent = '';
      // Přesnost, nebo rychlost přepisu (hub/hlas.py, MODELY).
      const prepis = group('i-mic', 'Diktování');
      const moznosti = [
        ['turbo', 'i-star', 'Přesně', 'méně chyb · asi 10 s'],
        ['small', 'i-bolt', 'Rychle', 'asi 3 s · víc chyb'],
      ];
      for (const [id, ico, nazev, popis] of moznosti) {
        const m = (s.models || {})[id] || {};
        const t = tile(ico, nazev, m.installed ? popis : 'tady není', s.model === id);
        t.disabled = !m.installed;
        t.onclick = async () => {
          await save({hlas_model: id});
          s.model = id;
          drawReady(s);
          if (window.HubHlas) HubHlas.refresh();
        };
        prepis.appendChild(t);
      }

      // Předčítání: hlas jako dlaždice (jako model u diktování) s ukázkou
      // uvnitř, pod ním obyčejné zaškrtávátko „číst samo".
      const cteni = group('i-speak', 'Předčítání');
      const hlasTile = tile('i-speak', 'Jirka', 'český hlas', true);
      hlasTile.classList.add('hlas-voice');
      hlasTile.onclick = null;
      const play = el('span', 'hlas-play');
      play.title = 'Přehrát ukázku';
      play.setAttribute('role', 'button');
      // Během přípravy se točí kolečko, během čtení je tam stop.
      play.innerHTML = '<svg class="hlas-i-play"><use href="#i-play"/></svg>' +
                       '<svg class="hlas-i-stop"><use href="#i-stop"/></svg>' +
                       '<span class="hlas-spin"></span>';
      play.onclick = () => window.HubHlas && HubHlas.speak(
        'Ahoj, tady je hub. Tohle je český hlas, kterým ti budu číst odpovědi.', play, io.toast);
      hlasTile.appendChild(play);
      cteni.appendChild(hlasTile);

      const row = el('label', 'onb-row hlas-auto');
      const cb = el('input');
      cb.type = 'checkbox';
      cb.checked = !!(window.HubHlas && HubHlas.auto.get());
      cb.onchange = () => { if (window.HubHlas) HubHlas.auto.set(cb.checked); };
      row.append(cb, el('span', null, 'Číst nové odpovědi samy'));
      row.title = 'Jen v tabu, na který se díváš, a jen na tomhle zařízení.';
      ready.appendChild(row);
    }

    let poll = null;
    async function nacti() {
      let s;
      try { s = await io.api('hlas'); } catch (_) { s = {ready: false}; }
      btns.textContent = '';
      if (s.ready) {
        status.hidden = true;
        drawReady(s);
        if (!ready.parentNode) box.appendChild(ready);
        if (window.HubHlas) HubHlas.refresh();
        return;
      }
      status.hidden = false;
      if (ready.parentNode) ready.remove();
      const job = s.job || {};
      if (job.running || s.installing) {
        status.className = 'set-status';
        status.textContent = 'Instaluju: ' + (job.step || 'začínám…');
        clearTimeout(poll);
        poll = setTimeout(nacti, 2000);
        return;
      }
      if (job.done && job.result && job.result.ok === false) {
        status.className = 'set-status warn';
        status.textContent = 'Instalace se nepovedla: ' + (job.result.detail || '');
      } else if (!s.can_install) {
        status.className = 'set-status warn';
        status.textContent = 'Na serveru hlas ještě není — přibude s další ' +
          'aktualizací serveru.';
        return;
      } else {
        status.className = 'set-status';
        status.textContent = 'Hlas tu zatím není. Instalace stáhne asi 1 GB ' +
          '(knihovny a modely) a zabere pár minut.';
      }
      const go = el('button', 'actionbtn', 'Nainstalovat hlas');
      go.onclick = async () => {
        go.disabled = true;
        try { await io.api('hlas-install', {}); } catch (err) { io.toast(err.message); }
        nacti();
      };
      btns.appendChild(go);
    }
    nacti();
    return box;
  }

  function aktualizace() {
    if (state.config && state.config.gateway_user) return aktualizaceServer();
    const box = section('Aktualizace aplikace',
      'Stáhne novou verzi aplikace a nainstaluje ji. Otevřené rozhovory se po ' +
      'restartu vrátí.');
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
          status.textContent = `Je k dispozici nová verze ${v.latest} (máš ${v.version}).`;
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
                (res.tabs === 1 ? 'rozhovor…' : res.tabs < 5 ? 'rozhovory…' : 'rozhovorů…')
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

    // Když se stránka načte během běžící aktualizace, navážeme na ni. Jinak
    // se hned zeptáme na novou verzi — kdo sem přišel přes „Nová verze",
    // má ji vidět a jen kliknout. Tlačítko Aktualizovat na úvodu ji rovnou
    // i spustí (startUpdate).
    const start = !!io.startUpdate;
    io.startUpdate = false;
    io.api('update-status').then(async (st) => {
      if (st.running) {
        status.className = 'set-status busy';
        status.textContent = 'Aktualizuju…';
        doIt.disabled = true;
        check.disabled = true;
        watchUpdate();
        return;
      }
      await check.onclick();
      if (start && !doIt.hidden) doIt.onclick();
    }).catch(() => {});

    // Co v které verzi přibylo — vydání na GitHubu.
    const gh = el('button', 'actionbtn', 'Co je nového');
    gh.hidden = !advanced();
    gh.onclick = () => io.open('https://github.com/' +
      ((state.version && state.version.repo) || 'jurapascal/claude-code-hub') + '/releases');

    btns.appendChild(check);
    btns.appendChild(doIt);
    btns.appendChild(gh);
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
