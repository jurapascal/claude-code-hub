/* Claude Code Hub — browser front-end.
 *
 * Tabs are thin: the pty and its scrollback live in the Python server, so a
 * reload re-attaches to the running Claude sessions instead of killing them.
 */
'use strict';

const TOKEN = new URLSearchParams(location.search).get('t') || '';
const $ = (id) => document.getElementById(id);

let STATE = null;          // /api/state payload
let WS = null;
let TABS = [];             // {ref,id,title,kind,path,term,fit,pane,el,exited}
let ACTIVE = null;
let DARK = true;
let refSeq = 0;

/* ── server calls ─────────────────────────────────────────────────────────── */
async function api(path, body) {
  const url = `/api/${path}${path.includes('?') ? '&' : '?'}t=${encodeURIComponent(TOKEN)}`;
  const res = await fetch(url, body === undefined ? {} : {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

function send(msg) {
  if (WS && WS.readyState === WebSocket.OPEN) WS.send(JSON.stringify(msg));
}

function openExternal(path, kind, file) {
  api('open-path', {path, kind, file}).catch(() => toast('Nepodařilo se otevřít: ' + path));
}

/* ── theme ────────────────────────────────────────────────────────────────── */
const CSS_VARS = {
  AMBER: '--amber', BG: '--bg', BG_SIDEBAR: '--bg-sidebar', BG_CARD: '--bg-card',
  FG: '--fg', FG_BRIGHT: '--fg-bright', DIM: '--dim', GREEN: '--green',
  RED: '--red', CARD_HOVER: '--card-hover', BORDER: '--border', SECTION: '--section',
};

function palette() { return DARK ? STATE.palette.dark : STATE.palette.light; }

function termTheme() {
  const p = palette(), t = p.TERM_PALETTE;
  return {
    background: p.BG, foreground: p.FG, cursor: p.AMBER, cursorAccent: p.BG,
    selectionBackground: DARK ? 'rgba(224,132,60,.30)' : 'rgba(188,92,28,.22)',
    black: t[0], red: t[1], green: t[2], yellow: t[3], blue: t[4],
    magenta: t[5], cyan: t[6], white: t[7],
    brightBlack: t[8], brightRed: t[9], brightGreen: t[10], brightYellow: t[11],
    brightBlue: t[12], brightMagenta: t[13], brightCyan: t[14], brightWhite: t[15],
  };
}

function applyTheme() {
  const p = palette();
  for (const [key, cssVar] of Object.entries(CSS_VARS)) {
    document.documentElement.style.setProperty(cssVar, p[key]);
  }
  $('btn-theme').firstElementChild.firstElementChild
    .setAttribute('href', DARK ? '#i-moon' : '#i-sun');
  const theme = termTheme();
  for (const tab of TABS) tab.term.options.theme = theme;
}

function setTheme(dark, remember) {
  DARK = dark;
  if (remember) localStorage.setItem('hub-theme', dark ? 'dark' : 'light');
  applyTheme();
}

/* ── sidebar ──────────────────────────────────────────────────────────────── */
function icon(name, cls) {
  return `<svg class="ico${cls ? ' ' + cls : ''}"><use href="#${name}"/></svg>`;
}

function renderProjects(filter) {
  const box = $('projects');
  const needle = (filter || '').trim().toLowerCase();
  box.textContent = '';
  const showArchived = !!STATE.config.show_archived;
  const all = STATE.projects.filter(p => showArchived || !p.archived);
  const shown = all.filter(p => !needle ||
    ((p.label || p.name).toLowerCase().includes(needle) ||
     p.name.toLowerCase().includes(needle)));
  const dirty = shown.filter(p => p.dirty).length;
  const archived = STATE.projects.filter(p => p.archived).length;
  $('projects-count').textContent =
    shown.length + (dirty ? `  ·  ${dirty} rozdělaných` : '');

  if (!shown.length) {
    box.innerHTML = '<div class="empty">(nic nenalezeno)</div>';
  }
  for (const p of shown) {
    const meta = [p.type, p.branch, p.dirty ? `${p.dirty} změn` : '']
      .filter(Boolean).join('  ·  ');
    // Karta je div, ne button: uvnitř má vlastní tlačítko „⋯" a tlačítko
    // v tlačítku je neplatné HTML, které prohlížeče rozhodí po svém.
    const el = document.createElement('div');
    el.className = 'card' + (p.dirty ? ' dirty' : '') + (p.archived ? ' archived' : '');
    el.tabIndex = 0;
    el.innerHTML = `<span class="dot"></span><span class="card-col">
        <span class="card-name"></span>
        <span class="card-meta"></span>
        <span class="card-path"></span></span>
      <button class="card-more" title="Možnosti">⋯</button>`;
    el.querySelector('.card-name').textContent = p.label || p.name;
    el.querySelector('.card-meta').textContent = meta;
    el.querySelector('.card-path').textContent = shortPath(p.path);
    if (p.brief) {
      const flag = document.createElement('span');
      flag.className = 'card-flag';
      flag.title = 'Má briefing v CLAUDE.md';
      flag.textContent = 'i';
      el.appendChild(flag);
    }
    el.title = p.path + (p.brief ? '\n\n' + p.brief.slice(0, 300) : '');
    const open = () => openTab({kind: 'project', path: p.path,
                                title: p.label || p.name});
    el.onclick = (ev) => { if (!ev.target.closest('.card-more')) open(); };
    el.onkeydown = (ev) => {
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); open(); }
    };
    el.oncontextmenu = (ev) => { ev.preventDefault(); projectMenu(ev, p); };
    el.querySelector('.card-more').onclick = (ev) => {
      ev.stopPropagation();
      const box = ev.currentTarget.getBoundingClientRect();
      projectMenu({clientX: box.left, clientY: box.bottom, preventDefault(){}}, p);
    };
    box.appendChild(el);
  }

  const foot = document.createElement('div');
  foot.className = 'projects-foot';
  const add = document.createElement('button');
  add.className = 'linkbtn';
  add.textContent = '＋ Přidat projekt';
  add.onclick = addProject;
  foot.appendChild(add);
  if (archived) {
    const t = document.createElement('button');
    t.className = 'linkbtn';
    t.textContent = showArchived ? 'skrýt archiv' : `archiv (${archived})`;
    t.onclick = async () => {
      await api('config', {show_archived: !showArchived});
      await reload();
    };
    foot.appendChild(t);
  }
  box.appendChild(foot);
}

/* Domovská složka je u každé cesty stejná a jen zabírá místo. */
function shortPath(path) {
  const home = STATE.home || '';
  const short = home && path.startsWith(home) ? '~' + path.slice(home.length) : path;
  return short.length > 42 ? '…' + short.slice(-41) : short;
}

async function addProject() {
  const picked = await pickFolder();
  if (!picked) return;
  try {
    await api('project', {action: 'add', path: picked});
    await reload();
    toast('Přidáno: ' + picked);
  } catch (err) { toast(err.message); }
}

/* Úprava projektu. Briefing je tu to hlavní: uloží se do CLAUDE.md projektu,
   takže si ho Claude Code přečte sám, jakmile ten projekt otevřeš. */
function editProject(p) {
  const box = document.createElement('div');
  box.className = 'onb';
  box.innerHTML = `
    <div class="onb-box">
      <div class="onb-head"><div>
        <div class="onb-title">Upravit projekt</div>
        <div class="onb-sub"></div>
      </div></div>
      <div class="onb-body">
        <div class="set-title">Název v panelu</div>
        <input class="ed-label" type="text" placeholder="">
        <div class="set-title" style="margin-top:16px">Briefing</div>
        <div class="set-note">Napiš vlastními slovy, o co v projektu jde — stack,
          hosting, klient, na co si dát pozor. Uloží se do
          <code>CLAUDE.md</code> projektu, takže to Claude Code přečte sám,
          jakmile projekt otevřeš.</div>
        <textarea class="ed-brief" rows="9" placeholder="Např.: E-shop na vlastním PHP, Wedos hosting, deploy přes FTP…"></textarea>
      </div>
      <div class="onb-foot">
        <button class="btn ghost ed-archive"></button>
        <span class="spacer"></span>
        <button class="btn ghost ed-cancel">Zrušit</button>
        <button class="btn primary ed-save">Uložit</button>
      </div>
    </div>`;
  box.querySelector('.onb-sub').textContent = p.path;
  const label = box.querySelector('.ed-label');
  const brief = box.querySelector('.ed-brief');
  label.placeholder = p.name;
  label.value = p.label || '';
  brief.value = p.brief || '';
  const arch = box.querySelector('.ed-archive');
  arch.textContent = p.archived ? 'Vrátit z archivu' : 'Archivovat';
  const close = () => box.remove();
  box.querySelector('.ed-cancel').onclick = close;
  box.addEventListener('click', (ev) => { if (ev.target === box) close(); });
  arch.onclick = async () => {
    try {
      await api('project', {action: 'save', path: p.path, archived: !p.archived});
      close();
      await reload();
      toast(p.archived ? 'Vráceno z archivu.' : 'Archivováno.');
    } catch (err) { toast(err.message); }
  };
  box.querySelector('.ed-save').onclick = async () => {
    const btn = box.querySelector('.ed-save');
    btn.disabled = true;
    try {
      const r = await api('project', {action: 'save', path: p.path,
                                      label: label.value, brief: brief.value});
      close();
      await reload();
      toast(r.briefing && r.briefing.written
        ? 'Uloženo — briefing je v CLAUDE.md projektu.' : 'Uloženo.');
    } catch (err) {
      toast(err.message);
      btn.disabled = false;
    }
  };
  document.body.appendChild(box);
  (p.brief ? brief : label).focus();
}

function renderMemory() {
  const mem = STATE.memory;
  $('memory-section').hidden = !mem.enabled;
  if (!mem.enabled) return;
  const c = mem.counts;
  $('memory-summary').innerHTML =
    `<span class="learnings">${icon('i-bulb')} ${c.learnings || 0}</span>
     <span class="errors">${icon('i-error')} ${c.errors || 0}</span>
     <span class="wins">${icon('i-star')} ${c.wins || 0}</span>`;
  const box = $('memory');
  box.textContent = '';
  if (!mem.recent.length) {
    box.innerHTML = '<div class="empty">(zatím prázdné)</div>';
    return;
  }
  const symbol = {learnings: 'i-bulb', errors: 'i-error', wins: 'i-star'};
  for (const note of mem.recent) {
    const el = document.createElement('button');
    el.className = `mem-card ${note.kind}`;
    el.innerHTML = icon(symbol[note.kind] || 'i-bulb') + '<span></span>';
    el.querySelector('span').textContent = note.title;
    el.title = note.file;
    el.onclick = () => openExternal('', 'note', note.file);
    box.appendChild(el);
  }
}

const ACTIONS = [
  {skill: 'save', label: 'Uložit do paměti', icon: 'i-save', cmd: '/save\r'},
  {skill: 'project', label: 'Poznámka projektu', icon: 'i-note', cmd: '/project\r'},
  {skill: 'deploy', label: 'Deploy', icon: 'i-deploy', cmd: '/deploy\r'},
  {skill: 'push', label: 'Push na GitHub', icon: 'i-push', cmd: '/push\r'},
  {skill: 'status', label: 'Přehled projektů', icon: 'i-status', cmd: '/status\r'},
  {skill: 'screenshot', label: 'Screenshot…', icon: 'i-image', cmd: '/screenshot '},
];

function renderActions() {
  const box = $('actions');
  box.textContent = '';
  for (const a of ACTIONS) {
    if (!STATE.skills.includes(a.skill)) continue;  // never offer "Unknown command"
    const el = document.createElement('button');
    el.className = 'barbtn';
    el.innerHTML = icon(a.icon) + '<span></span>';
    el.querySelector('span').textContent = a.label;
    el.onclick = () => runSlash(a.cmd);
    box.appendChild(el);
  }
}

function renderFooter() {
  const now = new Date();
  const date = `${now.getDate()}.${now.getMonth() + 1}.${now.getFullYear()}`;
  const foot = $('footer');
  foot.textContent = '';
  foot.appendChild(document.createTextNode(
    [STATE.user, date].filter(Boolean).join('  ·  ') + '  ·  '));
  // Verze je zároveň cesta do nastavení — tam se s ní stejně něco dělá.
  const ver = document.createElement('button');
  ver.className = 'footer-ver';
  ver.textContent = 'v' + STATE.version.version;
  ver.title = 'Nastavení a aktualizace';
  ver.onclick = () => HubSettings.open({...hubIO(), state: STATE});
  foot.appendChild(ver);
}

/* Uvítání. Není to jen ozdoba — je to jediná obrazovka, kterou člověk vidí,
   než něco otevře, takže nese i to, co je dobré vědět hned: kde se naposledy
   dělalo, co zůstalo rozdělané a jestli něco chybí. */
const NS = 'http://www.w3.org/2000/svg';

function dayPart(hour) {
  if (hour < 5) return 'noc';
  if (hour < 10) return 'rano';
  if (hour < 18) return 'den';
  if (hour < 22) return 'vecer';
  return 'noc';
}

/* Scéna podle denní doby: slunce nad obzorem tím výš, čím je blíž poledni.
   Kreslí se z proměnných motivu, aby seděla ve světlém i tmavém režimu. */
function dayScene(part) {
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('viewBox', '0 0 280 96');
  svg.setAttribute('width', '280');
  svg.setAttribute('height', '96');
  const add = (tag, attrs) => {
    const n = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    svg.appendChild(n);
    return n;
  };

  const noc = part === 'noc';
  const y = {rano: 60, den: 34, vecer: 62, noc: 40}[part];
  const disc = noc ? 'var(--dim)' : 'var(--amber)';

  // záře kolem tělesa
  const glow = add('radialGradient', {id: 'dayglow'});
  for (const [offset, op] of [[0, '.30'], [1, '0']]) {
    const stop = document.createElementNS(NS, 'stop');
    stop.setAttribute('offset', offset);
    stop.setAttribute('stop-color', disc);
    stop.setAttribute('stop-opacity', op);
    glow.appendChild(stop);
  }
  add('circle', {cx: 140, cy: y, r: 44, fill: 'url(#dayglow)'});

  if (noc) {
    // měsíc = kruh s odkrojeným kruhem, ne "banán" z cesty
    const mask = document.createElementNS(NS, 'mask');
    mask.setAttribute('id', 'moon');
    for (const [cx, fill] of [[140, '#fff'], [150, '#000']]) {
      const c = document.createElementNS(NS, 'circle');
      c.setAttribute('cx', cx); c.setAttribute('cy', y);
      c.setAttribute('r', 15); c.setAttribute('fill', fill);
      mask.appendChild(c);
    }
    svg.appendChild(mask);
    add('circle', {cx: 140, cy: y, r: 15, fill: disc, mask: 'url(#moon)'});
    for (const [cx, cy, r] of [[92, 26, 1.6], [196, 32, 2], [72, 52, 1.3],
                               [214, 58, 1.5], [116, 18, 1.2]]) {
      add('circle', {cx, cy, r, fill: disc, opacity: '.5'});
    }
  } else {
    add('circle', {cx: 140, cy: y, r: 15, fill: disc});
    // paprsky ubývají, jak slunce klesá k obzoru
    const rays = part === 'den' ? 8 : 5;
    for (let i = 0; i < rays; i++) {
      const a = (Math.PI * (i + 0.5)) / rays;
      const x1 = 140 - Math.cos(a) * 23, y1 = y - Math.sin(a) * 23;
      const x2 = 140 - Math.cos(a) * 31, y2 = y - Math.sin(a) * 31;
      add('line', {x1, y1, x2, y2, stroke: disc, 'stroke-width': 2.5,
                   'stroke-linecap': 'round', opacity: '.65'});
    }
  }

  // obzor
  add('line', {x1: 40, y1: 78, x2: 240, y2: 78, stroke: 'var(--border)',
               'stroke-width': 2, 'stroke-linecap': 'round'});
  add('line', {x1: 96, y1: 78, x2: 184, y2: 78, stroke: disc,
               'stroke-width': 2, 'stroke-linecap': 'round', opacity: '.55'});
  return svg;
}

function renderWelcome() {
  const hour = new Date().getHours();
  const part = dayPart(hour);
  const pozdrav = {rano: 'Dobré ráno', den: 'Dobrý den',
                   vecer: 'Dobrý večer', noc: 'Dobrou noc'}[part];
  const who = (STATE.user || '').split(/[\s.]/)[0];
  $('welcome-greet').textContent = pozdrav + (who ? ', ' + who : '') + '.';

  const scene = $('welcome-scene');
  scene.textContent = '';
  scene.appendChild(dayScene(part));

  const box = $('welcome-actions');
  box.textContent = '';
  const cfg = STATE.config.newtab || {};
  const actions = [];
  if (cfg.claude !== false) {
    actions.push(['i-terminal', 'Otevřít Claude Code', true,
      () => openTab({kind: 'project', path: STATE.home, title: 'Claude Code'})]);
  }
  if (cfg.shell !== false) {
    actions.push(['i-terminal', 'Otevřít terminál', false,
      () => openTab({kind: 'shell', path: '', title: 'terminál'})]);
  }
  actions.push(['i-gear', 'Nastavení', false,
    () => HubSettings.open({...hubIO(), state: STATE})]);
  for (const [ico, label, primary, run] of actions) {
    const b = document.createElement('button');
    b.className = 'btn ' + (primary ? 'primary' : 'ghost');
    b.innerHTML = icon(ico) + '<span></span>';
    b.querySelector('span').textContent = label;
    b.onclick = run;
    box.appendChild(b);
  }

  renderWelcomeCols();

  const facts = [];
  const mem = STATE.memory || {};
  if (mem.enabled) {
    const total = (mem.counts.learnings || 0) + (mem.counts.errors || 0) +
                  (mem.counts.wins || 0);
    facts.push(total ? total + ' poznámek v paměti' : 'paměť připravená');
  }
  facts.push('verze ' + STATE.version.version);
  $('welcome-facts').textContent = facts.join('  ·  ');
}

function kdy(ts) {
  if (!ts) return '';
  const dny = Math.floor((Date.now() / 1000 - ts) / 86400);
  if (dny <= 0) return 'dnes';
  if (dny === 1) return 'včera';
  if (dny < 7) return `před ${dny} dny`;
  if (dny < 60) return `před ${Math.floor(dny / 7)} týdny`;
  return `před ${Math.floor(dny / 30)} měsíci`;
}

/* Dva sloupce: kde se naposledy dělalo a co zůstalo rozdělané. Obojí je
   zkratka k tomu, co člověk stejně otevře jako první. */
function renderWelcomeCols() {
  const wrap = $('welcome-cols');
  wrap.textContent = '';
  const live = (STATE.projects || []).filter(p => !p.archived);
  const recent = [...live].sort((a, b) => b.mtime - a.mtime).slice(0, 4);
  const dirty = live.filter(p => p.dirty)
    .sort((a, b) => b.dirty - a.dirty).slice(0, 4);

  const column = (title, items, note) => {
    if (!items.length) return null;
    const col = document.createElement('div');
    col.className = 'wcol';
    col.appendChild(Object.assign(document.createElement('div'),
      {className: 'wcol-title', textContent: title}));
    for (const p of items) {
      const b = document.createElement('button');
      b.className = 'wcol-item';
      b.innerHTML = '<span class="wcol-name"></span><span class="wcol-note"></span>';
      b.querySelector('.wcol-name').textContent = p.label || p.name;
      b.querySelector('.wcol-note').textContent = note(p);
      b.title = p.path;
      b.onclick = () => openTab({kind: 'project', path: p.path,
                                 title: p.label || p.name});
      col.appendChild(b);
    }
    return col;
  };

  const a = column('NAPOSLEDY', recent, p => kdy(p.mtime));
  const b = column('ROZDĚLANÉ', dirty, p => p.dirty + ' změn');
  if (a) wrap.appendChild(a);
  if (b) wrap.appendChild(b);
}

function renderDoctor() {
  const d = STATE.doctor, warn = $('welcome-warn');
  const problems = [];
  if (!d.bash) {
    problems.push(d.platform === 'windows'
      ? 'Nenašel jsem <b>Git for Windows</b> — bez něj hub neumí spustit bash a taby zůstanou prázdné.<br><code>winget install Git.Git</code>'
      : 'Nenašel jsem <b>bash</b> — taby se nespustí.');
  }
  if (!d.claude) {
    problems.push('Claude Code CLI (<b>claude</b>) není v PATH — tab se otevře jako obyčejný shell.' +
      (d.platform === 'windows' ? '<br><code>winget install Anthropic.ClaudeCode</code>'
                                : '<br><code>curl -fsSL https://claude.ai/install.sh | bash</code>'));
  }
  warn.hidden = !problems.length;
  warn.innerHTML = problems.join('<hr style="border:none;border-top:1px solid var(--border);margin:8px 0">');
}

/* Které „+" tlačítko se ukazuje. Kdo jede jen v Claude Code, nechce vedle sebe
   pořád tlačítko na holý shell — a naopak. */
function renderNewTabButtons() {
  const cfg = STATE.config.newtab || {};
  $('btn-new-claude').hidden = cfg.claude === false;
  $('btn-new-shell').hidden = cfg.shell === false;
}

async function reload() {
  STATE = await api('state');
  renderProjects($('search').value);
  renderMemory();
  renderActions();
  renderFooter();
  renderDoctor();
  renderWelcome();
  renderNewTabButtons();
  applyTheme();
}

/* ── tabs ─────────────────────────────────────────────────────────────────── */
function openTab({kind, path, title}) {
  const tab = createTab({kind, path, title});
  const dims = measure(tab);
  send({t: 'open', ref: tab.ref, kind, path, title, cols: dims.cols, rows: dims.rows});
  return tab;
}

function createTab({kind, path, title, id}) {
  const ref = ++refSeq;
  const pane = document.createElement('div');
  pane.className = 'pane';
  $('panes').appendChild(pane);

  const term = new Terminal({
    fontFamily: '"Cascadia Mono","JetBrains Mono","DejaVu Sans Mono",Menlo,Consolas,monospace',
    fontSize: 13,
    scrollback: 100000,
    cursorBlink: true,
    allowProposedApi: true,
    theme: termTheme(),
  });
  const fit = new FitAddon.FitAddon();
  term.loadAddon(fit);
  // Links open in the real browser, not inside the app window.
  term.loadAddon(new WebLinksAddon.WebLinksAddon((_ev, uri) => openExternal(uri)));
  term.open(pane);

  const tab = {ref, id: id || null, title, kind, path, term, fit, pane, exited: false};
  const toPty = (d) => { if (tab.id) send({t: 'in', id: tab.id, d}); };
  term.onData(toPty);
  // Diacritics arrive from the GTK input method as composition events, which
  // xterm.js mishandles badly enough to corrupt the line — see ime.js.
  tab.releaseIME = HubIME.install(term);
  // WebKitGTK nepustí stránku ke schránce, tak se na ni sahá přes náš server.
  tab.releaseClipboard = HubClipboard.install(term, {
    read: (which) => api('clipboard?which=' + which).then(r => r.text || ''),
    write: (text, which) => api('clipboard', {text, which}),
    notice: toast,
  });
  wireFiles(tab);

  const el = document.createElement('button');
  el.className = 'tab';
  el.draggable = true;
  el.innerHTML = '<span class="tab-title"></span>' +
                 `<span class="tab-close" title="Zavřít tab">${icon('i-close')}</span>`;
  el.querySelector('.tab-title').textContent = title;
  el.onclick = (ev) => {
    if (ev.target.closest('.tab-close')) { closeTab(tab); return; }
    activate(tab);
  };
  el.ondblclick = (ev) => { if (!ev.target.closest('.tab-close')) startRename(tab); };
  wireDrag(el, tab);
  $('tabbar').insertBefore(el, $('btn-new-claude'));
  tab.el = el;

  TABS.push(tab);
  activate(tab);
  return tab;
}

function measure(tab) {
  try {
    const dims = tab.fit.proposeDimensions();
    if (dims && dims.cols > 0 && dims.rows > 0) return dims;
  } catch (_) { /* pane not laid out yet */ }
  return {cols: 100, rows: 30};
}

function activate(tab) {
  ACTIVE = tab;
  for (const t of TABS) {
    t.el.classList.toggle('active', t === tab);
    t.pane.classList.toggle('active', t === tab);
  }
  $('welcome').hidden = TABS.length > 0;
  $('actionbar').hidden = !tab;
  if (tab) {
    refit(tab);
    tab.term.focus();
  }
}

function refit(tab) {
  if (!tab || tab.pane.offsetWidth === 0) return;
  try { tab.fit.fit(); } catch (_) { return; }
  if (tab.id) send({t: 'resize', id: tab.id, cols: tab.term.cols, rows: tab.term.rows});
}

function closeTab(tab) {
  if (tab.id) send({t: 'close', id: tab.id});
  if (tab.releaseIME) tab.releaseIME();
  if (tab.releaseClipboard) tab.releaseClipboard();
  tab.term.dispose();
  tab.el.remove();
  tab.pane.remove();
  TABS = TABS.filter(t => t !== tab);
  if (ACTIVE === tab) ACTIVE = null;
  const next = TABS[TABS.length - 1];
  if (next) activate(next);
  else { $('welcome').hidden = false; $('actionbar').hidden = true; }
}

function startRename(tab) {
  const holder = tab.el.querySelector('.tab-title');
  const input = document.createElement('input');
  input.value = tab.title;
  holder.textContent = '';
  holder.appendChild(input);
  input.focus();
  input.select();
  const finish = () => {
    const name = input.value.trim() || tab.title;
    tab.title = name;
    holder.textContent = name;
    if (tab.id) send({t: 'rename', id: tab.id, title: name});
  };
  input.onblur = finish;
  input.onkeydown = (ev) => {
    if (ev.key === 'Enter') { ev.preventDefault(); finish(); }
    if (ev.key === 'Escape') { ev.preventDefault(); holder.textContent = tab.title; }
    ev.stopPropagation();
  };
  input.onclick = (ev) => ev.stopPropagation();
}

let dragged = null;
function wireDrag(el, tab) {
  el.ondragstart = () => { dragged = tab; el.classList.add('dragging'); };
  el.ondragend = () => { dragged = null; el.classList.remove('dragging'); };
  el.ondragover = (ev) => ev.preventDefault();
  el.ondrop = (ev) => {
    ev.preventDefault();
    if (!dragged || dragged === tab) return;
    $('tabbar').insertBefore(dragged.el, el);
    const order = [...$('tabbar').querySelectorAll('.tab')];
    TABS.sort((a, b) => order.indexOf(a.el) - order.indexOf(b.el));
  };
}

/* ── pasted and dropped files ─────────────────────────────────────────────── */
/* A screenshot on the clipboard has no path, and a dropped file's real path is
 * deliberately hidden from the page — but a path is the only thing a terminal
 * can be handed. So we write our own copy through the server and type that. */
function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('nejde přečíst'));
    reader.onload = () => resolve(String(reader.result).split(',', 2)[1] || '');
    reader.readAsDataURL(file);
  });
}

function shellQuote(p) {
  return /[\s"'`$\\]/.test(p) ? `'${p.replace(/'/g, `'\\''`)}'` : p;
}

async function attachFiles(tab, fileList) {
  const files = [...fileList].filter(f => f && f.size);
  if (!files.length || !tab.id) return;
  const paths = [];
  for (const file of files) {
    try {
      const data = await fileToBase64(file);
      const res = await api('upload', {name: file.name || 'obrazek.png', data});
      if (res.path) paths.push(res.path);
    } catch (err) {
      toast(`Nepodařilo se přiložit ${file.name || 'soubor'}: ${err.message}`);
    }
  }
  if (!paths.length) return;
  send({t: 'in', id: tab.id, d: paths.map(shellQuote).join(' ') + ' '});
  tab.term.focus();
  toast(paths.length === 1 ? 'Přiloženo: ' + paths[0]
                           : `Přiloženo ${paths.length} souborů`);
}

function wireFiles(tab) {
  const pane = tab.pane;
  // Capture on the pane so we get there before xterm's own textarea handler:
  // it would otherwise paste the file's *name* as text. Plain text pastes are
  // left alone — those xterm does right.
  pane.addEventListener('paste', (ev) => {
    const cd = ev.clipboardData;
    if (!cd || !cd.files || !cd.files.length) return;
    // Text má přednost: zkopírovaný soubor ze správce souborů nese vedle sebe
    // i svoje jméno jako text, a kdo kopíroval text, čeká text. Sáhneme po
    // souborech jen tehdy, když na schránce nic textového není — to je případ
    // screenshotu. (Vkládání textu už jednou padlo na tom, že to bylo obráceně.)
    if (cd.getData && cd.getData('text/plain')) return;
    ev.preventDefault();
    ev.stopPropagation();
    attachFiles(tab, cd.files);
  }, true);
  pane.addEventListener('dragover', (ev) => {
    if (!ev.dataTransfer || ![...ev.dataTransfer.types].includes('Files')) return;
    ev.preventDefault();
    pane.classList.add('dropping');
  });
  pane.addEventListener('dragleave', (ev) => {
    if (ev.target === pane) pane.classList.remove('dropping');
  });
  pane.addEventListener('drop', (ev) => {
    const files = ev.dataTransfer && ev.dataTransfer.files;
    if (!files || !files.length) return;
    ev.preventDefault();
    pane.classList.remove('dropping');
    attachFiles(tab, files);
  });
}

/* Slash commands: type the text, then send Enter as its own keystroke a moment
 * later — a \r bundled with pasted text reads as a newline, not as submit. */
function runSlash(cmd) {
  if (!ACTIVE || !ACTIVE.id) return;
  const id = ACTIVE.id;
  if (cmd.endsWith('\r')) {
    send({t: 'in', id, d: cmd.slice(0, -1)});
    setTimeout(() => send({t: 'in', id, d: '\r'}), 180);
  } else {
    send({t: 'in', id, d: cmd});
  }
  ACTIVE.term.focus();
}

/* ── context menu ─────────────────────────────────────────────────────────── */
function projectMenu(ev, p) {
  const items = [
    {icon: 'i-terminal', label: 'Otevřít v Claude',
     run: () => openTab({kind: 'project', path: p.path, title: p.label || p.name})},
    {icon: 'i-note', label: 'Upravit…', run: () => editProject(p)},
    {icon: 'i-deploy', label: p.deployable ? 'Deploy (FTP)' : 'Deploy',
     run: () => openTab({kind: 'deploy', path: p.path, title: 'deploy: ' + p.name})},
  ];
  if (STATE.skills.includes('project')) {
    items.push({icon: 'i-note', label: 'Poznámka do paměti (/project)',
      run: () => openTab({kind: 'slash:project', path: p.path, title: 'note: ' + p.name})});
  }
  items.push(
    {icon: 'i-terminal', label: 'Shell tady',
     run: () => openTab({kind: 'shell', path: p.path, title: p.name})},
    {icon: 'i-folder', label: 'Otevřít složku', run: () => openExternal(p.path)},
    {icon: 'i-save', label: p.archived ? 'Vrátit z archivu' : 'Archivovat',
     run: async () => {
       await api('project', {action: 'save', path: p.path, archived: !p.archived});
       await reload();
     }},
    {icon: 'i-close', label: 'Odebrat z Hubu', run: () => removeProject(p)},
  );
  showMenu(ev.clientX, ev.clientY, items);
}

/* Odebrání je jen o panelu — složka na disku zůstává. Kdyby to mazalo soubory,
   byla by to poslední věc, kterou by kdo od launcheru čekal. */
async function removeProject(p) {
  if (!confirm(`Odebrat „${p.label || p.name}" z Hubu?\n\n` +
               `Složka na disku zůstane, maže se jen z panelu:\n${p.path}`)) return;
  try {
    const r = await api('project', {action: 'remove', path: p.path});
    if (r.rescanned) {
      await api('project', {action: 'save', path: p.path, archived: true});
      toast('Leží v nastavené složce, tak jsem ho aspoň archivoval.');
    } else {
      toast('Odebráno z panelu.');
    }
    await reload();
  } catch (err) { toast(err.message); }
}

/* Nabídka se otevírá pod kurzorem, takže uvolnění téhož kliknutí, kterým ji
   člověk vyvolal, dopadne rovnou na první položku a spustí ji. Proto se kreslí
   kousek vedle a chvíli po otevření kliknutí ignoruje. */
const MENU_ARM_MS = 300;
let menuArmedAt = 0;

function showMenu(x, y, items) {
  const menu = $('ctxmenu');
  menu.textContent = '';
  for (const item of items) {
    const el = document.createElement('button');
    el.innerHTML = icon(item.icon) + '<span></span>';
    el.querySelector('span').textContent = item.label;
    el.onclick = (ev) => {
      if (Date.now() - menuArmedAt < MENU_ARM_MS) {
        ev.preventDefault();
        ev.stopPropagation();
        return;                       // pořád doznívá kliknutí, které ji otevřelo
      }
      hideMenu();
      item.run();
    };
    menu.appendChild(el);
  }
  menu.hidden = false;
  menuArmedAt = Date.now();
  const box = menu.getBoundingClientRect();
  // +3 px, ať kurzor nestojí přímo na první položce
  menu.style.left = Math.min(x + 3, innerWidth - box.width - 8) + 'px';
  menu.style.top = Math.min(y + 3, innerHeight - box.height - 8) + 'px';
}

function hideMenu() { $('ctxmenu').hidden = true; }

/* ── folder picker ────────────────────────────────────────────────────────── */
let pickerPath = '';
let pickerResolve = null;

/* Vybrat složku a dostat ji zpátky — průvodce i nastavení potřebují cestu,
   ne otevřený tab. */
function pickFolder(start) {
  return new Promise((resolve) => {
    if (pickerResolve) pickerResolve(null);   // předchozí výběr už nikoho nezajímá
    pickerResolve = resolve;
    openPicker(start || '');
  });
}

function settlePicker(value) {
  const resolve = pickerResolve;
  pickerResolve = null;
  if (resolve) resolve(value);
  return !!resolve;
}

async function openPicker(path) {
  const data = await api('listdir?path=' + encodeURIComponent(path || ''));
  pickerPath = data.path;
  $('modal').hidden = false;
  $('modal-path').textContent = data.path;
  const roots = $('modal-roots');
  roots.textContent = '';
  for (const r of data.roots) {
    const el = document.createElement('button');
    el.textContent = r.name;
    el.onclick = () => openPicker(r.path);
    roots.appendChild(el);
  }
  const list = $('modal-list');
  list.textContent = '';
  if (data.parent) {
    const up = document.createElement('button');
    up.innerHTML = icon('i-up') + '<span>..</span>';
    up.onclick = () => openPicker(data.parent);
    list.appendChild(up);
  }
  for (const d of data.dirs) {
    const el = document.createElement('button');
    el.innerHTML = icon('i-folder') + '<span></span>';
    el.querySelector('span').textContent = d.name;
    el.onclick = () => openPicker(d.path);
    list.appendChild(el);
  }
}

function closePicker() { $('modal').hidden = true; settlePicker(null); }

/* ── websocket ────────────────────────────────────────────────────────────── */
function connect() {
  WS = new WebSocket(`ws://${location.host}/ws?t=${encodeURIComponent(TOKEN)}`);
  WS.onopen = () => send({t: 'hello'});
  WS.onmessage = (ev) => handle(JSON.parse(ev.data));
  WS.onclose = () => setTimeout(connect, 1000);
}

function handle(msg) {
  if (msg.t === 'out') {
    const tab = TABS.find(t => t.id === msg.id);
    if (tab) tab.term.write(msg.d);
  } else if (msg.t === 'opened') {
    const tab = TABS.find(t => t.ref === msg.ref);
    if (tab) { tab.id = msg.id; refit(tab); }
  } else if (msg.t === 'exit') {
    const tab = TABS.find(t => t.id === msg.id);
    if (tab) { tab.exited = true; tab.el.classList.add('exited'); }
  } else if (msg.t === 'sessions') {
    restore(msg.list);
  } else if (msg.t === 'error') {
    const tab = TABS.find(t => t.ref === msg.ref);
    if (tab) closeTab(tab);
    toast(msg.d);
  }
}

/* Re-attach after a reload or a dropped connection: the server is the source of
 * truth about which sessions exist. */
function restore(list) {
  const live = new Set(list.map(s => s.id));
  for (const tab of [...TABS]) {
    if (tab.id && !live.has(tab.id)) closeTab(tab);
  }
  for (const info of list) {
    let tab = TABS.find(t => t.id === info.id);
    if (!tab) {
      tab = createTab({kind: info.kind, path: info.path, title: info.title, id: info.id});
    }
    tab.term.reset();          // the replay below is the full scrollback
    send({t: 'attach', id: info.id});
    refit(tab);
  }
  if (TABS.length) activate(TABS[TABS.length - 1]);
}

let toastTimer = null;
function toast(text) {
  const el = $('toast');
  el.textContent = text;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 6000);
}

/* ── průvodce a nastavení ─────────────────────────────────────────────────── */
function hubIO() {
  return {
    get state() { return STATE; },
    api,
    setTheme,
    toast,
    pickFolder,
    reload,
    refreshState: async () => { STATE = await api('state'); },
    openWizard: () => HubOnboarding.open({...hubIO(), state: STATE}),
  };
}

/* ── boot ─────────────────────────────────────────────────────────────────── */
async function main() {
  const saved = localStorage.getItem('hub-theme');
  DARK = saved ? saved === 'dark'
               : !window.matchMedia('(prefers-color-scheme: light)').matches;

  await reload();

  $('search').oninput = (ev) => renderProjects(ev.target.value);
  $('btn-refresh').onclick = () => reload();
  $('btn-theme').onclick = () => setTheme(!DARK, true);
  $('btn-settings').onclick = () => HubSettings.open({...hubIO(), state: STATE});
  $('btn-new-shell').onclick = () => openTab({kind: 'shell', path: '', title: 'terminál'});
  $('btn-new-claude').onclick = () =>
    openTab({kind: 'project', path: STATE.home, title: 'Claude Code'});
  $('btn-brain').onclick = () => openExternal('', 'brain');
  $('modal-close').onclick = closePicker;
  $('modal-cancel').onclick = closePicker;
  $('modal-open').onclick = () => {
    const path = pickerPath;
    $('modal').hidden = true;
    if (settlePicker(path)) return;          // o cestu si někdo řekl
    const name = path.replace(/[\\/]+$/, '').split(/[\\/]/).pop() || path;
    openTab({kind: 'project', path, title: name});
  };

  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', (ev) => {
    if (!localStorage.getItem('hub-theme')) setTheme(ev.matches, false);
  });

  // Stejné doznívající kliknutí by nabídku hned zase zavřelo, než ji stihne
  // člověk vidět.
  document.addEventListener('click', () => {
    if (Date.now() - menuArmedAt >= MENU_ARM_MS) hideMenu();
  });
  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape') { hideMenu(); closePicker(); }
  });

  new ResizeObserver(() => refit(ACTIVE)).observe($('panes'));
  window.addEventListener('resize', () => refit(ACTIVE));

  connect();

  // Napoprvé se hub nastavuje tady, ne v instalačce — ta běží jednou a v
  // terminálu, takže po ní nebylo kde nastavení změnit.
  if (!STATE.onboarded) HubOnboarding.open({...hubIO(), state: STATE});
}

main().catch(err => toast('Hub se nenačetl: ' + err.message));
