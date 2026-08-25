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
  const shown = STATE.projects.filter(p => !needle || p.name.toLowerCase().includes(needle));
  if (!shown.length) {
    box.innerHTML = '<div class="empty">(nic nenalezeno)</div>';
    return;
  }
  for (const p of shown) {
    const meta = [p.type, p.branch, p.dirty ? `${p.dirty} změn` : ''].filter(Boolean).join('  ·  ');
    const el = document.createElement('button');
    el.className = 'card' + (p.dirty ? ' dirty' : '');
    el.innerHTML = `<span class="dot"></span><span class="card-col">
        <span class="card-name"></span><span class="card-meta"></span></span>`;
    el.querySelector('.card-name').textContent = p.name;
    el.querySelector('.card-meta').textContent = meta;
    el.title = p.path;
    el.onclick = () => openTab({kind: 'project', path: p.path, title: p.name});
    el.oncontextmenu = (ev) => { ev.preventDefault(); projectMenu(ev, p); };
    box.appendChild(el);
  }
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
  $('footer').textContent = [STATE.user, date].filter(Boolean).join('  ·  ');
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

async function reload() {
  STATE = await api('state');
  renderProjects($('search').value);
  renderMemory();
  renderActions();
  renderFooter();
  renderDoctor();
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
  $('tabbar').insertBefore(el, $('btn-newtab'));
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
     run: () => openTab({kind: 'project', path: p.path, title: p.name})},
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
  );
  showMenu(ev.clientX, ev.clientY, items);
}

/* Nabídka se otevírá pod kurzorem, takže uvolnění téhož kliknutí, kterým ji
   člověk vyvolal, dopadne rovnou na první položku a spustí ji — pravý klik pak
   vypadá jako „vložilo se to samo a teprve pak vyskočila nabídka". Proto se
   kreslí kousek vedle a chvíli po otevření kliknutí ignoruje. */
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
  $('btn-newtab').onclick = () => openTab({kind: 'shell', path: '', title: 'shell'});
  $('btn-browse').onclick = () => openPicker('');
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
