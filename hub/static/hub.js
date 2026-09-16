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
  if (!res.ok) {
    // Server chybu posílá jako {"error": "…"} — do hlášky patří věta, ne JSON.
    const body = await res.text();
    let message = body;
    try {
      const parsed = JSON.parse(body);
      message = parsed.error || parsed.detail || body;
    } catch (_) { /* není JSON */ }
    throw new Error(message);
  }
  return res.json();
}

function send(msg) {
  if (WS && WS.readyState === WebSocket.OPEN) WS.send(JSON.stringify(msg));
}

function openExternal(path, kind, file) {
  api('open-path', {path, kind, file}).catch(() => toast('Nepodařilo se otevřít: ' + path));
}

/* Odkaz z terminálu. Na serveru (brána) by open-path pustil xdg-open na
 * serveru, kde se nic neukáže — odkaz se proto otevře tam, kde člověk sedí.
 * Na počítači jde do výchozího prohlížeče jako dřív. */
function openLink(url) {
  if (!/^https?:\/\//i.test(url)) return;
  if (!STATE.config.gateway_user) { openExternal(url); return; }
  // Okno appky (WebKitGTK) nové okno předá prohlížeči a vrátí null — podle
  // návratové hodnoty se tedy neúspěch poznat nedá, hláška by lhala.
  const win = window.open(url, '_blank');
  if (win) win.opener = null;
}

/* Hub běží na serveru (brána)? Pak schránka i prohlížeč serveru nejsou toho,
 * kdo se dívá — co sahá ven, musí jít přes jeho prohlížeč. */
function onServer() {
  return !!STATE.config.gateway_user;
}

/* Do schránky toho, kdo se dívá. Na serveru přes prohlížeč: /api/clipboard by
 * psal do schránky serveru, kde žádná není. Na počítači je naopak spolehlivější
 * server, protože WebKitGTK stránku ke schránce nepustí (viz clipboard.js). */
function copyText(text) {
  if (!onServer()) return api('clipboard', {text, which: 'clipboard'});
  if (navigator.clipboard && navigator.clipboard.writeText) {
    return navigator.clipboard.writeText(text);
  }
  return Promise.reject(new Error('prohlížeč stránku ke schránce nepustil'));
}

/* Schránka terminálu (clipboard.js). Na serveru jen to, co dá prohlížeč:
 * PRIMARY stránce nedá vůbec a čtení jen na kliknutí (pravé tlačítko). Ctrl+V
 * tam clipboard.js nechytá — nativní vložení přinese text i obrázek. */
async function clipRead(which) {
  if (!onServer()) return api('clipboard?which=' + which);
  if (which !== 'clipboard' || !navigator.clipboard || !navigator.clipboard.readText) return {};
  try {
    return {text: await navigator.clipboard.readText()};
  } catch (_) {
    throw new Error('prohlížeč stránku ke schránce nepustil — vlož přes Ctrl+V');
  }
}

function clipWrite(text, which) {
  if (!onServer()) return api('clipboard', {text, which});
  return which === 'clipboard' ? copyText(text) : Promise.resolve();
}

/* ── theme ────────────────────────────────────────────────────────────────── */
const CSS_VARS = {
  AMBER: '--amber', BG: '--bg', BG_SIDEBAR: '--bg-sidebar', BG_CARD: '--bg-card',
  FG: '--fg', FG_BRIGHT: '--fg-bright', DIM: '--dim', GREEN: '--green',
  RED: '--red', CARD_HOVER: '--card-hover', BORDER: '--border', SECTION: '--section',
  CHART: '--chart',
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
  // Skupiny drží pohromadě, co k sobě patří; bez skupiny se nic nenadpisuje.
  let lastGroup = null;
  for (const p of shown) {
    const group = p.group || '';
    if (group !== lastGroup) {
      lastGroup = group;
      if (group) {
        const h = document.createElement('div');
        h.className = 'group-head';
        h.textContent = group;
        box.appendChild(h);
      }
    }
    const meta = [p.type, p.branch, p.dirty ? `${p.dirty} změn` : '']
      .filter(Boolean).join('  ·  ');
    // Karta je div, ne button: uvnitř má vlastní tlačítka a tlačítko
    // v tlačítku je neplatné HTML, které prohlížeče rozhodí po svém.
    const el = document.createElement('div');
    el.className = 'card' + (p.dirty ? ' dirty' : '') + (p.archived ? ' archived' : '')
                 + (p.image ? ' has-photo' : '');
    el.tabIndex = 0;
    el.innerHTML = `<span class="dot"></span>
      <span class="card-thumb"></span>
      <span class="card-col">
        <span class="card-name"></span>
        <span class="card-meta"></span>
        <span class="card-path"></span></span>
      <button class="card-more" title="Možnosti">⋯</button>`;
    el.querySelector('.card-name').textContent = p.label || p.name;
    el.querySelector('.card-meta').textContent = meta;
    el.querySelector('.card-path').textContent = shortPath(p.path);
    const thumb = el.querySelector('.card-thumb');
    if (p.image) {
      const img = document.createElement('img');
      img.src = imageUrl(p.image);
      img.alt = '';
      thumb.appendChild(img);
    } else {
      thumb.remove();
    }
    if (p.brief) {
      const flag = document.createElement('span');
      flag.className = 'card-flag';
      flag.title = 'Má briefing v CLAUDE.md';
      flag.textContent = 'i';
      el.appendChild(flag);
    }
    el.title = [p.path, p.repo ? 'github: ' + p.repo : '',
                p.brief ? '\n' + p.brief.slice(0, 300) : '']
      .filter(Boolean).join('\n');
    const open = () => openTab({kind: 'project', path: p.path,
                                title: p.label || p.name,
                                agent: agentFor(p.path)});
    el.onclick = (ev) => { if (!ev.target.closest('.card-more')) open(); };
    el.onkeydown = (ev) => {
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); open(); }
    };
    el.oncontextmenu = (ev) => { ev.preventDefault(); projectMenu(ev, p); };
    el.querySelector('.card-more').onclick = (ev) => {
      ev.stopPropagation();
      const b = ev.currentTarget.getBoundingClientRect();
      projectMenu({clientX: b.left, clientY: b.bottom, preventDefault(){}}, p);
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
        <div class="ed-grid">
          <label><span>Název v panelu</span>
            <input class="ed-label" type="text"></label>
          <label><span>Skupina</span>
            <input class="ed-group" type="text" list="ed-groups"
                   placeholder="např. Klienti, Vlastní, Archiv"></label>
        </div>
        <datalist id="ed-groups"></datalist>
        <label class="ed-full"><span>GitHub repo</span>
          <input class="ed-repo" type="text" placeholder="owner/repo"></label>
        <div class="set-title" style="margin-top:16px">Fotka projektu</div>
        <div class="ed-photo">
          <div class="ed-thumb"></div>
          <div class="ed-photo-btns">
            <button class="btn ghost ed-pick">Vybrat obrázek…</button>
            <button class="btn ghost ed-drop">Odebrat</button>
          </div>
          <input class="ed-file" type="file" accept="image/*" hidden>
        </div>
        <div class="set-title" style="margin-top:16px">Briefing</div>
        <div class="set-note">Napiš vlastními slovy, o co v projektu jde — stack,
          hosting, klient, na co si dát pozor. Uloží se do
          <code>CLAUDE.md</code> projektu, takže to Claude Code přečte sám,
          jakmile projekt otevřeš.</div>
        <textarea class="ed-brief" rows="8"
          placeholder="Např.: E-shop na vlastním PHP, Wedos hosting, deploy přes FTP…"></textarea>
      </div>
      <div class="onb-foot">
        <button class="btn ghost ed-archive"></button>
        <span class="spacer"></span>
        <button class="btn ghost ed-cancel">Zrušit</button>
        <button class="btn primary ed-save">Uložit</button>
      </div>
    </div>`;
  box.querySelector('.onb-sub').textContent = p.path;
  const q = (sel) => box.querySelector(sel);
  q('.ed-label').placeholder = p.name;
  q('.ed-label').value = p.label || '';
  q('.ed-group').value = p.group || '';
  q('.ed-repo').value = p.repo || '';
  q('.ed-brief').value = p.brief || '';

  // našeptávač skupin z toho, co už existuje
  const groups = [...new Set(STATE.projects.map(x => x.group).filter(Boolean))];
  for (const g of groups) {
    const o = document.createElement('option');
    o.value = g;
    q('#ed-groups').appendChild(o);
  }

  let image = p.image || '';
  const paintThumb = () => {
    const t = q('.ed-thumb');
    t.textContent = '';
    if (image) {
      const img = document.createElement('img');
      img.src = imageUrl(image);
      t.appendChild(img);
    } else {
      t.textContent = 'bez fotky';
    }
    q('.ed-drop').hidden = !image;
  };
  paintThumb();
  q('.ed-pick').onclick = () => q('.ed-file').click();
  q('.ed-drop').onclick = () => { image = ''; paintThumb(); };
  q('.ed-file').onchange = async (ev) => {
    const file = ev.target.files && ev.target.files[0];
    if (!file) return;
    q('.ed-pick').disabled = true;
    try {
      const data = await fileToBase64(file);
      const r = await api('upload', {name: file.name || 'projekt.png', data});
      image = r.path;
      paintThumb();
    } catch (err) {
      toast('Obrázek se nepodařilo nahrát: ' + err.message);
    } finally {
      q('.ed-pick').disabled = false;
    }
  };

  const arch = q('.ed-archive');
  arch.textContent = p.archived ? 'Vrátit z archivu' : 'Archivovat';
  const close = () => box.remove();
  q('.ed-cancel').onclick = close;
  // Jen klik, který na pozadí i začal: výběr textu v poli tažením ven z okna
  // končí na pozadí, prohlížeč to hlásí jako klik — a rozepsané úpravy by zmizely.
  let downOutside = false;
  box.addEventListener('pointerdown', (ev) => { downOutside = ev.target === box; });
  box.addEventListener('click', (ev) => { if (ev.target === box && downOutside) close(); });
  arch.onclick = async () => {
    try {
      await api('project', {action: 'save', path: p.path, archived: !p.archived});
      close();
      await reload();
      toast(p.archived ? 'Vráceno z archivu.' : 'Archivováno.');
    } catch (err) { toast(err.message); }
  };
  q('.ed-save').onclick = async () => {
    const btn = q('.ed-save');
    btn.disabled = true;
    try {
      const r = await api('project', {
        action: 'save', path: p.path,
        label: q('.ed-label').value, brief: q('.ed-brief').value,
        group: q('.ed-group').value, repo: q('.ed-repo').value.trim(),
        image,
      });
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
  q('.ed-label').focus();
}

function imageUrl(path) {
  return `/api/image?t=${encodeURIComponent(TOKEN)}&path=${encodeURIComponent(path)}`;
}

/* „ukládá se sama · naposledy 14:05" — ať je vidět, že se na nic klikat nemusí. */
function autosaveLine() {
  if (!STATE.memory_autosave) return 'automatické ukládání vypnuté';
  const last = (STATE.autosave_recent || []).find((e) => e.status === 'saved');
  if (!last) return 'ukládá se sama';
  const d = new Date(last.at);
  const today = d.toDateString() === new Date().toDateString();
  const when = today ? d.toLocaleTimeString('cs-CZ', {hour: '2-digit', minute: '2-digit'})
                     : d.toLocaleDateString('cs-CZ', {day: 'numeric', month: 'numeric'});
  return `ukládá se sama · naposledy ${when}`;
}

/* Trezor Obsidian: na serveru a bez aplikace Obsidian ho hub ukáže sám
   (vault.js) — open-path by tam spustil něco na serveru, kde se nic neukáže.
   Na počítači s Obsidianem se otevírá aplikace jako dřív. */
function vaultPreview() {
  return !!window.HubVault && (onServer() || !STATE.obsidian);
}

function openVault(path, vault, title) {
  const firma = vault === 'firma';
  const shared = /^sdilene:/.test(vault || '');
  HubVault.open({
    api,
    path: path || '',
    vault: firma || shared ? vault : '',
    title: firma ? 'Firemní Obsidian' : shared ? 'Sdílený Obsidian — ' + (title || vault.slice(8)) : '',
    fileUrl: (p) => `/api/vault-file?path=${encodeURIComponent(p)}` +
      (firma || shared ? '&vault=' + encodeURIComponent(vault) : '') +
      `&t=${encodeURIComponent(TOKEN)}`,
    openLink,
    toast,
    obsidian: !firma && !shared && !onServer() && !!STATE.obsidian,
    openInObsidian: (p) => api('open-path', {kind: 'vault-note', file: p})
      .catch(() => toast('Obsidian se nepodařilo otevřít.')),
  });
}

/* ── firemní Obsidian ─────────────────────────────────────────────────────────
   Společný trezor pro všechny účty na bráně. Claude do něj sám nezapíše:
   poznámku připraví (tools/firma.py), hub ji tady ukáže s náhledem a nahraje
   ji až brána po kliknutí na Nahrát. */
let firmaTimer = null;
let firmaCard = null;
const firmaLater = new Set();          // „Později": do obnovení stránky se neukáže
const firmaWarned = new Set();         // o zadrženém návrhu stačí říct jednou
let firmaBusy = false;                 // nahrávání z firemního tabu běží

function renderFirma() {
  const on = !!(STATE.firma && STATE.firma.vault);
  $('firma-section').hidden = !on;
  // Tlačítka v liště („osobní" a „firemní") řeší renderNewTabButtons.
  if (!on) return;
  $('btn-firma').onclick = () => openVault('', 'firma');
  renderShared();
  if (!firmaTimer) {
    firmaTimer = setInterval(checkFirma, 3000);
    checkFirma();
  }
}

/* ── sdílené Obsidiany ─────────────────────────────────────────────────────────
   Trezory jen pro vybrané lidi (gateway/shared.py). Seznam je živý z brány —
   co je nové, se do prostoru sváže až po restartu, a panel ho nabídne. */
let sharedTimer = null;

async function renderShared() {
  const box = $('shared-section');
  if (!onServer()) { box.hidden = true; return; }
  if (!sharedTimer) {
    sharedTimer = setInterval(() => { if (!document.hidden) renderShared(); }, 60000);
  }
  let data;
  try {
    const r = await fetch('/gw/sdilene', {credentials: 'same-origin'});
    if (!r.ok) throw new Error('HTTP ' + r.status);
    data = await r.json();
  } catch (err) {
    box.hidden = true;                 // starší brána — sekce přijde s aktualizací
    return;
  }
  box.hidden = false;
  const list = $('shared-list');
  list.textContent = '';
  const vaults = data.vaults || [];
  if (!vaults.length) {
    const hint = document.createElement('div');
    hint.className = 'empty shared-hint';
    hint.textContent = 'Zatím žádný. Řekni Claudovi třeba „udělej sdílený Obsidian Marketing pro mě a Petra“.';
    list.appendChild(hint);
  }
  for (const v of vaults) {
    const item = document.createElement('button');
    item.className = 'shared-item' + (v.needs_restart ? ' pending' : '');
    item.dataset.slug = v.slug;
    item.innerHTML = '<span class="shared-name"></span><span class="shared-meta"></span>';
    item.querySelector('.shared-name').textContent = v.name;
    const who = (v.members || []).map((m) => m.name || m.email).join(', ');
    item.querySelector('.shared-meta').textContent = v.needs_restart ? 'načte se po restartu prostoru' : who;
    item.title = 'Členové: ' + who + (v.owner ? '\nZaložil: ' + v.owner : '');
    item.onclick = () => (v.needs_restart
      ? restartSpace(`Sdílený Obsidian „${v.name}“ se do prostoru načte po restartu.`)
      : openVault('', 'sdilene:' + v.slug, v.name));
    list.appendChild(item);
  }
}

async function restartSpace(why) {
  if (!confirm(why + '\n\nRestartovat prostor teď? Otevřené taby se zavřou, ' +
               'konverzace zůstanou v seznamu konverzací.')) return;
  try {
    const r = await fetch('/gw/restart', {
      method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', 'X-Hub-Account': '1'}, body: '{}',
    });
    if (!r.ok) throw new Error('HTTP ' + r.status);
  } catch (err) {
    toast('Restart se nepovedl: ' + err.message);
    return;
  }
  toast('Prostor se restartuje…');
  setTimeout(() => location.reload(), 2500);
}

async function checkFirma() {
  if (document.hidden || firmaCard || !window.HubVault) return;
  let res;
  try {
    res = await api('firma');
  } catch (err) {
    return;                            // starší hub v prostoru — po restartu to umí
  }
  const waiting = (res.pending || []).filter((p) => !firmaLater.has(p.id));
  // Z firemního tabu se nahrává rovnou. Co vypadá jako přihlašovací údaj, hub
  // tiše nenahraje — ukáže kartu a jednou to i napíše.
  const rovnou = waiting.find((p) => p.auto);
  if (rovnou) return publishFirma(rovnou);
  const citlive = waiting.find((p) => p.citlive && !firmaWarned.has(p.id));
  if (citlive) {
    firmaWarned.add(citlive.id);
    toast('Vypadá to na přihlašovací údaje — do firemního se to nahraje až po potvrzení.');
  }
  if (waiting.length && !firmaCard) showFirmaCard(waiting[0], waiting.length);
}

/* Nahrání bez karty (firemní tab). Zapisuje pořád brána a jen s cookie
   z tohohle prohlížeče — hub v prostoru do firemního trezoru nemůže. */
async function publishFirma(p) {
  if (firmaBusy) return;
  firmaBusy = true;
  let res = {};
  try {
    const r = await fetch('/gw/firma/publish', {
      method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', 'X-Hub-Firma': '1'},
      body: JSON.stringify({id: p.id, prepsat: !!p.prepise}),
    });
    res = await r.json().catch(() => ({error: `HTTP ${r.status}`}));
  } catch (err) {
    res = {error: err.message};
  }
  firmaBusy = false;
  if (res.ok) {
    return toast((res.message || `Nahráno do firemního Obsidianu: ${res.path}`) +
                 (p.prepise ? ' (přepsáno)' : ''));
  }
  // Když to neprojde, ať to nezmizí potichu — ukáže se karta jako jindy.
  toast('Nahrát do firemního se nepodařilo: ' + (res.error || 'neznámá chyba'));
}

function showFirmaCard(p, total) {
  const root = document.createElement('div');
  root.className = 'onb firma-modal';
  root.innerHTML = `
    <div class="onb-box">
      <div class="onb-head">
        <span class="onb-mark">${icon('i-book')}</span>
        <div>
          <div class="onb-title">Nahrát do firemního Obsidianu?</div>
          <div class="onb-sub"></div>
        </div>
      </div>
      <div class="onb-body">
        <div class="firma-target"><span>Kam</span><code></code></div>
        <div class="firma-warn" hidden>Na téhle cestě už poznámka je — nahráním se přepíše.</div>
        <div class="firma-people" hidden></div>
        <article class="vault-md firma-preview"></article>
      </div>
      <div class="onb-foot">
        <button class="btn ghost firma-later">Později</button>
        <button class="btn ghost firma-discard">Zahodit</button>
        <span class="spacer"></span>
        <button class="btn primary firma-ok"></button>
      </div>
    </div>`;
  const $$ = (sel) => root.querySelector(sel);
  // Druh návrhu: firemní Obsidian, nebo sdílený (zápis, založení, členové…).
  const kind = p.druh || 'firma';
  const writes = kind === 'firma' || kind === 'sdilene-zapis';
  const where = p.nazev ? `„${p.nazev}“` : '';
  const titles = {
    'firma': 'Nahrát do firemního Obsidianu?',
    'sdilene-zapis': `Nahrát do sdíleného Obsidianu ${where}?`,
    'sdilene-zalozit': `Založit sdílený Obsidian ${where}?`,
    'sdilene-clenove': `Změnit členy sdíleného Obsidianu ${where}?`,
    'sdilene-odejit': `Odejít ze sdíleného Obsidianu ${where}?`,
    'sdilene-smazat': `Smazat sdílený Obsidian ${where}?`,
  };
  const okLabels = {'sdilene-zalozit': 'Založit', 'sdilene-clenove': 'Změnit',
                    'sdilene-odejit': 'Odejít', 'sdilene-smazat': 'Smazat'};
  const names = (list) => (list || []).join(', ');
  const details = {
    'sdilene-zapis': p.lide && p.lide.length ? 'Uvidí: ' + names(p.lide) : '',
    'sdilene-zalozit': 'Pro: ' + names(p.lide),
    'sdilene-clenove': [p.pridat && p.pridat.length ? 'Přidat: ' + names(p.pridat) : '',
                        p.odebrat && p.odebrat.length ? 'Odebrat: ' + names(p.odebrat) : '']
      .filter(Boolean).join('\n'),
    'sdilene-odejit': 'Přestaneš ho vidět; vrátit tě může jen ten, kdo ho založil.',
    'sdilene-smazat': 'Zmizí všem členům' + (p.lide && p.lide.length ? ' (' + names(p.lide) + ')' : '') +
      '. Soubory zůstanou na serveru stranou.',
  };
  $$('.onb-title').textContent = titles[kind] || 'Potvrdit návrh?';
  $$('.onb-sub').textContent = 'Claude to připravil v tomhle prostoru' +
    (total > 1 ? ` · čeká ${total} návrhů` : '') + '. Provede se až po potvrzení.';
  $$('.firma-target').hidden = !writes;
  $$('.firma-target code').textContent = p.cil || '';
  const people = $$('.firma-people');
  people.textContent = details[kind] || '';
  people.hidden = !people.textContent;
  people.classList.toggle('danger', kind === 'sdilene-smazat' || kind === 'sdilene-odejit');
  const paint = () => {
    $$('.firma-warn').hidden = !(writes && p.prepise);
    $$('.firma-ok').textContent = writes ? (p.prepise ? 'Přepsat' : 'Nahrát') : (okLabels[kind] || 'Potvrdit');
  };
  paint();
  $$('.firma-preview').hidden = !writes;
  if (writes) {
    $$('.firma-preview').innerHTML = HubVault.render(p.text || '',
      {resolve: () => '', image: () => '', current: ''}).html;
  }
  const buttons = [...root.querySelectorAll('button')];
  const busy = (on) => buttons.forEach((b) => { b.disabled = on; });
  const close = () => {
    root.remove();
    document.removeEventListener('keydown', onKey, true);
    firmaCard = null;
    setTimeout(checkFirma, 300);       // další návrh v řadě
  };
  const later = () => { firmaLater.add(p.id); close(); };
  function onKey(ev) {
    if (ev.key === 'Escape') { ev.stopPropagation(); later(); }
  }
  $$('.firma-later').onclick = later;
  // Jen klik, který na pozadí i začal — výběr textu z náhledu tažením ven by
  // jinak kartu schoval.
  let downOutside = false;
  root.addEventListener('pointerdown', (ev) => { downOutside = ev.target === root; });
  root.addEventListener('click', (ev) => { if (ev.target === root && downOutside) later(); });
  $$('.firma-discard').onclick = async () => {
    busy(true);
    try {
      await api('firma', {action: 'discard', id: p.id});
      toast('Návrh do firemního Obsidianu zahozen.');
      close();
    } catch (err) {
      toast('Zahodit se nepodařilo: ' + err.message);
      busy(false);
    }
  };
  $$('.firma-ok').onclick = async () => {
    busy(true);
    let res = {};
    try {
      // Nahrává brána, ne hub v prostoru — a jen s cookie z tohohle prohlížeče.
      const r = await fetch('/gw/firma/publish', {
        method: 'POST', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json', 'X-Hub-Firma': '1'},
        body: JSON.stringify({id: p.id, prepsat: !!p.prepise}),
      });
      res = await r.json().catch(() => ({error: `HTTP ${r.status}`}));
    } catch (err) {
      res = {error: err.message};
    }
    if (res.ok) {
      toast(res.message || `Nahráno do firemního Obsidianu: ${res.path}`);
      close();
      if (kind !== 'firma') renderShared();
    } else if (res.exists) {
      // Mezitím tam poznámku nahrál někdo jiný — ať je vidět, že se přepíše.
      p.prepise = true;
      paint();
      busy(false);
    } else {
      toast('Nahrát se nepodařilo: ' + (res.error || 'neznámá chyba'));
      busy(false);
    }
  };
  document.addEventListener('keydown', onKey, true);
  document.body.appendChild(root);
  firmaCard = root;
}

/* ── konverzace ───────────────────────────────────────────────────────────────
   Seznam minulých konverzací s Claudem jako v oficiální appce: podle dní,
   s hledáním. Klik konverzaci otevře (claude --resume) — nebo přepne na tab,
   ve kterém už běží. Data: /api/chats (hub/chats.py). */
let CHATS = [];
let chatsTimer = null;
const CHATS_PAGE = 60;
let chatsShown = CHATS_PAGE;

const foldText = (s) => String(s || '').normalize('NFD').replace(/\p{M}/gu, '').toLowerCase();

function sidebarView(view) {
  const onChats = view === 'chats';
  $('projects-view').hidden = onChats;
  $('chats-view').hidden = !onChats;
  $('projects-count').hidden = onChats;
  $('chats-count').hidden = !onChats;
  for (const b of document.querySelectorAll('.side-tab')) {
    b.classList.toggle('on', b.dataset.view === (onChats ? 'chats' : 'projects'));
  }
  try { localStorage.setItem('hub-side-view', onChats ? 'chats' : 'projects'); } catch (_) {}
  clearInterval(chatsTimer);
  chatsTimer = null;
  if (onChats) {
    loadChats();
    chatsTimer = setInterval(() => { if (!document.hidden) loadChats(); }, 20000);
  }
}

async function loadChats() {
  let res;
  try {
    res = await api('chats');
  } catch (err) {
    const box = $('chats');
    box.textContent = '';
    const note = document.createElement('div');
    note.className = 'empty';
    note.textContent = 'Konverzace se nenačetly: ' + err.message;
    box.appendChild(note);
    return;
  }
  CHATS = res.chats || [];
  renderChats();
}

function renderChats() {
  const box = $('chats');
  const words = foldText($('chats-search').value.trim()).split(/\s+/).filter(Boolean);
  const list = words.length
    ? CHATS.filter((c) => {
      const hay = foldText([c.title, c.prompt, c.project].join(' '));
      return words.every((w) => hay.includes(w));
    })
    : CHATS;
  $('chats-count').textContent = CHATS.length ? String(CHATS.length) : '';
  box.textContent = '';
  if (!list.length) {
    const note = document.createElement('div');
    note.className = 'empty';
    note.textContent = words.length ? '(nic nenalezeno)' : '(zatím žádné konverzace)';
    box.appendChild(note);
    return;
  }
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() / 1000;
  const groupOf = (t) => (t >= today ? 'Dnes'
    : t >= today - 86400 ? 'Včera'
      : t >= today - 6 * 86400 ? 'Posledních 7 dní'
        : t >= today - 29 * 86400 ? 'Posledních 30 dní'
          : new Date(t * 1000).toLocaleDateString('cs-CZ', {month: 'long', year: 'numeric'}));
  let lastGroup = null;
  for (const c of list.slice(0, chatsShown)) {
    const group = groupOf(c.updated);
    if (group !== lastGroup) {
      lastGroup = group;
      const head = document.createElement('div');
      head.className = 'group-head';
      head.textContent = group;
      box.appendChild(head);
    }
    const item = document.createElement('button');
    item.className = 'chat-item' + (c.tab ? ' running' : '') + (c.exists ? '' : ' gone');
    item.dataset.id = c.id;
    item.innerHTML = '<span class="chat-title"></span><span class="chat-meta"></span>';
    item.querySelector('.chat-title').textContent = c.title;
    const at = new Date(c.updated * 1000);
    const when = c.updated >= today
      ? at.toLocaleTimeString('cs-CZ', {hour: '2-digit', minute: '2-digit'})
      : at.toLocaleDateString('cs-CZ', {day: 'numeric', month: 'numeric'});
    item.querySelector('.chat-meta').textContent =
      [c.project, when, c.tab ? 'otevřená' : ''].filter(Boolean).join('  ·  ');
    item.title = [c.prompt && c.prompt !== c.title ? c.prompt : '', c.cwd].filter(Boolean).join('\n\n');
    item.onclick = () => openChat(c);
    box.appendChild(item);
  }
  if (list.length > chatsShown) {
    const more = document.createElement('button');
    more.className = 'linkbtn chats-more';
    more.textContent = `Zobrazit další (${list.length - chatsShown})`;
    more.onclick = () => { chatsShown += CHATS_PAGE; renderChats(); };
    box.appendChild(more);
  }
}

function openChat(c) {
  const running = c.tab && TABS.find((t) => t.id === c.tab);
  if (running) {
    activate(running);
    document.body.classList.remove('drawer-open');
    return;
  }
  if (!c.exists) {
    toast('Složka téhle konverzace už není: ' + (c.cwd || '?'));
    return;
  }
  // Změněná před chvílí a v žádném tabu hubu neběží: nejspíš je otevřená
  // jinde (terminál, jiné okno). Dva Claudy v jedné konverzaci by si
  // přepisovaly historii — otevře se proto její kopie.
  let fork = false;
  if (Date.now() / 1000 - c.updated < 180) {
    if (!confirm(`Konverzace „${c.title}" se změnila před chvílí — nejspíš ještě běží jinde.\n\n` +
                 'Otevřít její kopii? Původní konverzace zůstane, jak je.')) return;
    fork = true;
  }
  openTab({kind: 'project', path: c.cwd, title: c.title.slice(0, 40), agent: 'claude',
           resume: c.id, fork});
  document.body.classList.remove('drawer-open');
  setTimeout(loadChats, 4000);
}

function initChats() {
  for (const b of document.querySelectorAll('.side-tab')) b.onclick = () => sidebarView(b.dataset.view);
  $('chats-search').addEventListener('input', () => { chatsShown = CHATS_PAGE; renderChats(); });
  let saved = 'projects';
  try { saved = localStorage.getItem('hub-side-view') || 'projects'; } catch (_) {}
  sidebarView(saved);
}

function renderMemory() {
  const mem = STATE.memory;
  $('memory-section').hidden = !mem.enabled;
  if (!mem.enabled) return;
  const c = mem.counts;
  $('memory-summary').innerHTML =
    `<span class="learnings">${icon('i-bulb')} ${c.learnings || 0}</span>
     <span class="errors">${icon('i-error')} ${c.errors || 0}</span>
     <span class="wins">${icon('i-star')} ${c.wins || 0}</span>
     <span class="mem-auto"></span>`;
  $('memory-summary').querySelector('.mem-auto').textContent = autosaveLine();
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
    el.onclick = () => (vaultPreview() ? openVault('memory/' + note.file)
                                       : openExternal('', 'note', note.file));
    box.appendChild(el);
  }
}

// `dev: true` = nasazení a GitHub. Ukáže se až ve vývojářském režimu; komu
// hub slouží na psaní s agentem, tomu deploy ani push nemá co nabízet.
// Uložit do paměti a poznámka k projektu tu nejsou schválně: dělá je hook
// memory-autosave.py sám, když session ztichne nebo se tab zavře.
const ACTIONS = [
  {skill: 'deploy', label: 'Deploy', icon: 'i-deploy', cmd: '/deploy\r', dev: true},
  {skill: 'push', label: 'Push na GitHub', icon: 'i-push', cmd: '/push\r', dev: true},
  {skill: 'status', label: 'Přehled projektů', icon: 'i-status', cmd: '/status\r'},
  {skill: 'screenshot', label: 'Screenshot…', icon: 'i-image', cmd: '/screenshot '},
];

function devMode() {
  return !!(STATE.config && STATE.config.dev_mode);
}

/* Rychlé akce jsou slash příkazy — dávají smysl jen v tabu s agentem, který
   naše skilly čte. V holém shellu by /status skončil v bashi. */
function showsActions(tab) {
  if (!tab || !(tab.kind === 'project' || tab.kind.startsWith('slash:'))) return false;
  const a = agentById(tab.agent || STATE.default_agent);
  return !!(a && a.skills) && $('actions').childElementCount > 0;
}

function renderActions() {
  const box = $('actions');
  box.textContent = '';
  for (const a of ACTIONS) {
    if (a.dev && !devMode()) continue;
    if (!STATE.skills.includes(a.skill)) continue;  // never offer "Unknown command"
    const el = document.createElement('button');
    el.className = 'barbtn';
    el.innerHTML = icon(a.icon) + '<span></span>';
    el.querySelector('span').textContent = a.label;
    el.onclick = () => runSlash(a.cmd);
    box.appendChild(el);
  }
  $('actionbar').hidden = !showsActions(ACTIVE);
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

/* Kde se pracuje. Okno vypadá na počítači i na serveru stejně, tak to říká
   štítek v hlavičce — a klik na něj vede do Účtu, odkud se přepíná. */
function renderPlace() {
  const badge = document.querySelector('.topbar-badge');
  if (!badge) return;
  const u = STATE.config.gateway_user;
  badge.textContent = u ? 'SERVER' : 'HUB';
  badge.classList.toggle('on-server', !!u);
  badge.title = u ? `Prostor na serveru · ${u.name || u.email}` : '';
  badge.onclick = u
    ? () => HubSettings.open({...hubIO(), state: STATE, tab: 'ucet'}) : null;
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
  if (cfg.agent !== false && cfg.claude !== false) {
    const a = agentById(STATE.default_agent) || agentList(true)[0];
    if (a) {
      actions.push(['i-terminal', 'Otevřít ' + a.label, true,
        () => openTab({kind: 'project', path: STATE.home, title: a.label,
                       agent: a.id})]);
    }
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
  renderWelcomeStats();

  const facts = [];
  const mem = STATE.memory || {};
  if (mem.enabled) {
    const total = (mem.counts.learnings || 0) + (mem.counts.errors || 0) +
                  (mem.counts.wins || 0);
    facts.push(total ? total + ' poznámek v paměti' : 'paměť připravená');
    if (STATE.memory_autosave) facts.push('ukládá se sama');
  }
  facts.push('verze ' + STATE.version.version);
  $('welcome-facts').textContent = facts.join('  ·  ');
}

/* Statistiky na úvodu: jen pár čísel a poslední měsíc, zbytek je pod ⧉.
   Načítá se až po vykreslení a mimo hlavní tok — první výpočet čte skoro
   gigabajt přepisů a úvodní obrazovka na něj čekat nesmí. */
let welcomeStatsLoaded = false;

async function renderWelcomeStats(force) {
  if (welcomeStatsLoaded && !force) return;
  const box = $('welcome-stats');
  let data;
  for (let i = 0; i < 30; i++) {
    try {
      data = await api('stats');
    } catch (_) { return; }
    if (!data.running) break;
    await new Promise(r => setTimeout(r, 1200));
  }
  if (!data || data.running || !data.tokens) return;
  welcomeStatsLoaded = true;

  box.textContent = '';
  const t = data.tokens;
  const gh = data.github || {};
  const row = document.createElement('div');
  row.className = 'wst-row';
  const add = (value, label) => {
    const b = document.createElement('button');
    b.className = 'wst-tile';
    b.innerHTML = '<span class="wst-value"></span><span class="wst-label"></span>';
    b.querySelector('.wst-value').textContent = value;
    b.querySelector('.wst-label').textContent = label;
    b.onclick = () => HubStats.open(hubIO());
    row.appendChild(b);
  };
  add(statNum(t.out), 'napsaných tokenů');
  add(statNum(data.prompts), 'zpráv');
  add(String(data.sessions), 'sezení');
  if (gh.ok) add(String(gh.commits_year), 'commitů za rok');
  box.appendChild(row);

  // Posledních 30 dnů — jedna série, měří velikost, proto bez legendy.
  const days = (data.days || []).slice(-30);
  if (days.length > 3) {
    const max = Math.max(1, ...days.map(d => d.prompts));
    const chart = document.createElement('div');
    chart.className = 'wst-spark';
    chart.title = 'Odeslané zprávy za posledních ' + days.length + ' dnů';
    for (const d of days) {
      const col = document.createElement('span');
      col.className = 'wst-col';
      const bar = document.createElement('span');
      bar.className = 'wst-bar';
      bar.style.height = Math.max(d.prompts ? 8 : 0, d.prompts / max * 100) + '%';
      col.title = `${d.day}: ${d.prompts} zpráv`;
      col.appendChild(bar);
      chart.appendChild(col);
    }
    box.appendChild(chart);
    const cap = document.createElement('div');
    cap.className = 'wst-cap';
    cap.textContent = 'posledních ' + days.length + ' dnů  ·  klikni pro celé statistiky';
    box.appendChild(cap);
  }
  box.hidden = false;
}

function statNum(n) {
  if (n >= 1e9) return (n / 1e9).toFixed(1).replace('.', ',') + ' mld';
  if (n >= 1e6) return (n / 1e6).toFixed(1).replace('.', ',') + ' M';
  if (n >= 1e3) return Math.round(n / 1e3) + ' tis.';
  return String(n || 0);
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
  // Chybí-li úplně všechno, je to problém. Chybí-li jen ten vybraný, taky —
  // ale ostatní se nabídnou, ať se dá pracovat hned.
  const ready = agentList(true);
  const def = agentById(STATE.default_agent);
  if (!ready.length) {
    const first = agentList()[0];
    problems.push('Není nainstalovaný <b>žádný AI agent</b> — tab se otevře jako obyčejný shell.' +
      (first && first.install ? '<br><code>' + first.install + '</code>' : '') +
      '<br>Nebo v nastavení: ⚙ → AI agenti.');
  } else if (def && !def.path) {
    problems.push('Vybraný agent <b>' + def.label + '</b> není v PATH — ' +
      'tab se otevře jako obyčejný shell.' +
      (def.install ? '<br><code>' + def.install + '</code>' : '') +
      '<br>K dispozici je: ' + ready.map((a) => a.label).join(', ') + '.');
  }
  warn.hidden = !problems.length;
  warn.innerHTML = problems.join('<hr style="border:none;border-top:1px solid var(--border);margin:8px 0">');
}

/* Které „+" tlačítko se ukazuje. Kdo jede jen v agentovi, nechce vedle sebe
   pořád tlačítko na holý shell — a naopak. Klíč `claude` je tu z verzí do 1.6,
   kde se tlačítko tak jmenovalo; starý konfig se tím pádem nemusí přepisovat. */
function renderNewTabButtons() {
  const cfg = STATE.config.newtab || {};
  const btn = $('btn-new-agent');
  btn.hidden = cfg.agent === false || cfg.claude === false;
  $('btn-new-shell').hidden = cfg.shell === false;
  // Tlačítko se jmenuje po tom, koho doopravdy spustí. Kde je firemní Obsidian,
  // jsou tlačítka dvě — osobní a firemní — ať je vidět, nad čím Claude pojede.
  const firma = !!(STATE.firma && STATE.firma.vault);
  $('btn-new-firma').hidden = !firma;
  const a = agentById(STATE.default_agent) || agentList(true)[0];
  const label = btn.querySelector('span');
  if (a && label) {
    label.textContent = firma ? a.label + ' osobní' : a.label;
    btn.title = firma ? 'Otevřít ' + a.label + ' nad osobním Obsidianem'
      : (agentList(true).length > 1
        ? 'Otevřít agenta (šipka dolů = výběr)' : 'Otevřít ' + a.label);
  }
}

async function reload() {
  STATE = await api('state');
  renderProjects($('search').value);
  renderMemory();
  renderFirma();
  renderActions();
  renderFooter();
  renderPlace();
  renderDoctor();
  renderWelcome();
  renderNewTabButtons();
  for (const t of TABS) paintAgent(t);
  applyTheme();
}

/* ── agenti ───────────────────────────────────────────────────────────────── */
/* Katalog i to, co je na stroji doopravdy k dispozici, přichází ze serveru
   (/api/state → agents). Frontend si nic o agentech nedomýšlí — jen kreslí. */
function agentList(onlyReady) {
  const all = STATE.agents || [];
  return onlyReady ? all.filter((a) => a.path) : all;
}

function agentById(id) {
  return (STATE.agents || []).find((a) => a.id === id) || null;
}

/* Kterým agentem se otevře tenhle projekt: co si u něj člověk naposled vybral,
   jinak výchozí z nastavení. */
function agentFor(path) {
  const saved = (STATE.config.project_agents || {})[path];
  if (saved && agentById(saved)) return saved;
  return STATE.default_agent || 'claude';
}

/* Volba u projektu přežije zavření hubu — proto do konfigu, ne do localStorage:
   projekt otevírá i doctor a instalačka, a ty do prohlížeče nevidí. */
async function rememberAgent(path, id) {
  if (!path) return;
  const map = {...(STATE.config.project_agents || {})};
  if (map[path] === id) return;
  map[path] = id;
  STATE.config.project_agents = map;      // ať menu hned ukazuje novou volbu
  try { await api('config', {project_agents: map}); } catch (_) { /* nevadí */ }
}

/* Nabídka „čím otevřít nový tab". Nedostupní agenti v ní zůstávají — jinak by
   se z ní nedalo dostat k jejich instalaci. */
function newAgentMenu(ev) {
  const items = agentList().map((a) => ({
    icon: 'i-terminal',
    label: a.path ? a.label : a.label + ' — nainstalovat',
    on: a.path && a.id === STATE.default_agent,
    run: () => (a.path
      ? openTab({kind: 'project', path: STATE.home, title: a.label, agent: a.id})
      : openTab({kind: 'install:' + a.id, path: STATE.home,
                 title: 'instalace: ' + a.label})),
  }));
  items.push({icon: 'i-gear', label: 'Nastavení agentů…',
              run: () => HubSettings.open({...hubIO(), state: STATE, tab: 'agenti'})});
  const b = ev.currentTarget ? ev.currentTarget.getBoundingClientRect() : null;
  showMenu(b ? b.left : ev.clientX, b ? b.bottom : ev.clientY, items);
}

/* ── tabs ─────────────────────────────────────────────────────────────────── */
function openTab({kind, path, title, agent, model, mode, resume, fork, vault}) {
  const tab = createTab({kind, path, title, agent, model, mode, resume});
  const dims = measure(tab);
  send({t: 'open', ref: tab.ref, kind, path, title, agent: agent || '',
        model: model || '', resume: resume || '', fork: !!fork,
        vault: vault || '', cols: dims.cols, rows: dims.rows});
  return tab;
}

/* Otevřít projekt konkrétním agentem a zapamatovat si tu volbu. */
function openWith(agentId, {path, title}) {
  rememberAgent(path, agentId);
  return openTab({kind: 'project', path, title, agent: agentId});
}

function createTab({kind, path, title, id, agent, model, background, bypass, mode, resume}) {
  const ref = ++refSeq;
  const pane = document.createElement('div');
  pane.className = 'pane';
  $('panes').appendChild(pane);
  // Terminál bydlí ve vlastním obalu — z jeho výšky se počítá počet řádků,
  // takže je to jediné místo, kde se dá terminálu ubrat, aniž by přetekl.
  const termbox = document.createElement('div');
  termbox.className = 'termbox';
  pane.appendChild(termbox);

  const term = new Terminal({
    fontFamily: '"Cascadia Mono","JetBrains Mono","DejaVu Sans Mono",Menlo,Consolas,monospace',
    // Na telefonu je 13 px na 80 sloupců moc — Claude Code pak láme rámečky.
    fontSize: window.matchMedia('(max-width: 820px)').matches ? 11 : 13,
    scrollback: 100000,
    cursorBlink: true,
    allowProposedApi: true,
    theme: termTheme(),
  });
  const fit = new FitAddon.FitAddon();
  term.loadAddon(fit);
  // Links open in the real browser, not inside the app window.
  term.loadAddon(new WebLinksAddon.WebLinksAddon((_ev, uri) => openLink(uri)));
  term.open(termbox);

  const tab = {ref, id: id || null, title, kind, path, term, fit, pane, termbox,
               // Agent musí být na tabu hned: bublina se podle něj rozhoduje,
               // co umí, a instaluje se o pár řádků níž.
               agent: agent || '', model: model || '', exited: false,
               // Jde v tabu zapnout bypass (agent se spustil s tou možností)?
               // A režim, do kterého se má tab po startu přepnout sám.
               bypass: !!bypass, wantMode: mode || '',
               // Ve které uložené konverzaci tab pokračuje (seznam konverzací).
               resume: resume || ''};
  const toPty = (d) => {
    tab.lastInput = Date.now();          // ozvěna psaní není „agent pracuje"
    if (tab.id) send({t: 'in', id: tab.id, d});
  };
  term.onData(toPty);
  // Diacritics arrive from the GTK input method as composition events, which
  // xterm.js mishandles badly enough to corrupt the line — see ime.js.
  tab.releaseIME = HubIME.install(term);
  // WebKitGTK nepustí stránku ke schránce, tak se na ni sahá přes náš server.
  tab.releaseClipboard = HubClipboard.install(term, {
    read: clipRead,
    write: clipWrite,
    attach: (path) => typePaths(tab, [path]),
    notice: toast,
    native: onServer,
  });
  wireFiles(tab);
  // Odkazy z výpisu jako tlačítka — rozlámanou adresu nejde kliknout (links.js).
  // Bez links.js (stará stránka z cache, napůl nahraný server) tab jede dál.
  tab.links = window.HubLinks
    ? HubLinks.install(tab, {open: openLink, copy: copyText, notice: toast}) : null;
  // Bublina jen tam, kde běží agent. V holém shellu, při deployi ani během
  // instalace není co překrývat — a odeslaný text by skončil v bashi.
  if (kind === 'project' || kind.startsWith('slash:')) {
    tab.composer = HubComposer.install(tab, {
      send,
      menu: showMenu,
      upload: uploadFiles,
      quote: shellQuote,
      skills: () => STATE.skills || [],
      // Čím Claude v tabu doopravdy odpověděl (z přepisu konverzace na serveru).
      tabModel: (id) => api('tab-model?id=' + encodeURIComponent(id)),
      /* Model ze settings.json — s ním Claude Code v tomhle tabu nastartoval.
         Přepnutí si Claude Code do settings.json uloží taky, ale my ho víme
         hned, tak si ho tu rovnou přepíšeme: další tab pak ukáže to samé. */
      // Kdo běží v tomhle tabu — bublina si podle toho vybere, co umí.
      agent: () => agentById(tab.agent || STATE.default_agent),
      agents: (ready) => agentList(!!ready),
      /* Přepnout agenta ani model v běžícím tabu nejde: je to jiný program,
         případně jiný startovací argument. Otevře se proto nový tab nad tímtéž
         projektem — a ten starý zůstane, dokud ho člověk sám nezavře. */
      openWith: (agentId, model, opts = {}) => {
        rememberAgent(tab.path, agentId);
        const a = agentById(agentId);
        openTab({kind: 'project', path: tab.path,
                 title: (a && a.label) || agentId, agent: agentId, model,
                 mode: opts.mode});
      },
      // Potvrzení varování k bypassu — pak ho nabízí každý nový tab.
      acceptBypass: () => api('bypass', {accept: true}),
      model: (key) => {
        if (key) STATE.model = key;
        return STATE.model || '';
      },
      // Na schránku bublina sama nedosáhne — obrázek si vyžádá přes server,
      // stejnou cestou jako terminál. Na serveru brány žádná schránka není:
      // obrázek tam přijde nativním vložením (paste se souborem).
      read: (which) => (onServer() ? Promise.resolve({}) : api('clipboard?which=' + which)),
      // Náhled přílohy: soubor podá server, prohlížeč na disk nevidí.
      imageUrl,
      notice: toast,
      /* Kolik místa dole si bublina ukusuje z terminálu navíc k tomu, co
         zabírá Claudeovo vlastní vstupní pole. Terminál se o to zkrátí, takže
         poslední řádky výpisu zůstanou nad bublinou, ne pod ní. */
      reserve: (px) => {
        const now = parseFloat(tab.termbox.style.bottom) || 0;
        if (Math.abs(now - px) < 1) return;
        tab.termbox.style.bottom = px > 0 ? px + 'px' : '';
        refit(tab);
      },
    });
  } else {
    // Bublina se v holém shellu neinstaluje, ale řádek kláves ano — na
    // telefonu je Tab i Ctrl+C jinak nedosažitelný. Na počítači ho CSS skryje.
    tab.keys = HubComposer.installKeys(tab, send);
  }

  const el = document.createElement('button');
  el.className = 'tab';
  el.draggable = true;
  el.innerHTML = '<span class="tab-agent" hidden></span>' +
                 '<span class="tab-title"></span>' +
                 `<span class="tab-close" title="Zavřít tab">${icon('i-close')}</span>`;
  el.querySelector('.tab-title').textContent = title;
  el.onclick = (ev) => {
    if (ev.target.closest('.tab-close')) { requestCloseTab(tab); return; }
    activate(tab);
  };
  el.ondblclick = (ev) => { if (!ev.target.closest('.tab-close')) startRename(tab); };
  wireDrag(el, tab);
  $('tabbar').insertBefore(el, $('btn-new-agent'));
  tab.el = el;
  paintAgent(tab);   // až teď — dřív tab své tlačítko ještě nemá

  TABS.push(tab);
  if (!background || !ACTIVE) activate(tab);
  return tab;
}

/* Odznak agenta na tabu: barevná tečka vždy, jméno navíc u toho, kdo není
   výchozí — jinak by u každého tabu svítilo totéž slovo. */
function paintAgent(tab) {
  const badge = tab.el && tab.el.querySelector('.tab-agent');
  if (!badge) return;
  const a = agentById(tab.agent || (tab.kind === 'shell' ? '' : STATE.default_agent));
  if (!a || tab.kind === 'shell' || tab.kind === 'deploy') {
    badge.hidden = true;
    return;
  }
  badge.hidden = false;
  badge.style.setProperty('--agent-color', a.color);
  badge.textContent = a.id === (STATE.default_agent || 'claude') ? '' : a.short;
  badge.classList.toggle('named', !!badge.textContent);
  tab.el.title = a.label + (tab.model ? ' · ' + tab.model : '');
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
  $('actionbar').hidden = !showsActions(tab);
  if (tab) {
    refit(tab);
    claimSize(tab);
    if (tab.links) tab.links.update();      // skrytý tab odkazy nečetl
    tab.term.focus();
    // Když je vidět bublina, píše se do ní — fokus patří jí.
    if (tab.composer) tab.composer.focus();
  }
}

function refit(tab) {
  if (!tab || tab.pane.offsetWidth === 0) return;
  try { tab.fit.fit(); } catch (_) { return; }
  if (!tab.id) return;
  /* Stejný rozměr se posílat nemusí — a hlavně nemá: ConPTY na Windows
     překreslí při resize celou obrazovku i tehdy, když se nic nezměnilo.
     Přepočet se přitom spouští z několika stran (ResizeObserver, změna okna,
     rezervace místa pro bublinu), takže se sem chodí i bez změny velikosti. */
  if (tab.sentCols === tab.term.cols && tab.sentRows === tab.term.rows) return;
  tab.sentCols = tab.term.cols;
  tab.sentRows = tab.term.rows;
  send({t: 'resize', id: tab.id, cols: tab.sentCols, rows: tab.sentRows});
}

/* Tohle okno se teď používá — pty ať má jeho rozměr. Okno na pozadí si taby
 * aktivuje taky (po znovupřipojení), a to rozměr brát nesmí: přetahovalo by ho
 * tomu, kdo zrovna pracuje jinde. Psaní si rozměr bere samo na serveru. */
function claimSize(tab) {
  if (tab && tab.id && document.hasFocus()) send({t: 'focus', id: tab.id});
}

/* Potvrzení „Ano / Ne" jako slib. Vrací true, když člověk klikl na Ano.
 * Otevřený dialog nikdy nezdvojujeme — druhé volání počká na to první. */
let confirmPending = null;
function askConfirm({title, html, yes = 'Ano', no = 'Ne'}) {
  if (confirmPending) return confirmPending;
  const box = $('confirm');
  $('confirm-title').textContent = title;
  $('confirm-text').innerHTML = html;
  $('confirm-yes').textContent = yes;
  $('confirm-no').textContent = no;
  box.hidden = false;
  $('confirm-yes').focus();
  confirmPending = new Promise((resolve) => {
    const finish = (answer) => {
      box.hidden = true;
      document.removeEventListener('keydown', onKey, true);
      confirmPending = null;
      resolve(answer);
    };
    const onKey = (ev) => {
      if (ev.key === 'Escape') { ev.stopPropagation(); finish(false); }
      if (ev.key === 'Enter') { ev.stopPropagation(); finish(true); }
    };
    $('confirm-yes').onclick = () => finish(true);
    $('confirm-no').onclick = () => finish(false);
    document.addEventListener('keydown', onKey, true);
  });
  return confirmPending;
}

/* Pracuje v tabu zrovna něco? Claude Code při práci točí ukazatel a posílá
 * výpis několikrát za sekundu; v klidu mlčí. Jeden dva kusy výpisu (třeba
 * překreslení po ztrátě fokusu kliknutím na křížek) za práci nepovažujeme. */
function isBusy(tab) {
  const now = Date.now();
  return !!tab.outTimes && tab.outTimes.filter((t) => now - t < 2000).length >= 6;
}

/* Zavření tabu se neptá: do paměti se session uloží sama (memory-autosave.py)
 * a přerušená konverzace jde vrátit přes `claude --resume`. Ptá se jen, když
 * by se zavřením utnula rozdělaná práce. */
async function requestCloseTab(tab) {
  if (tab.exited || !tab.id || !isBusy(tab)) { closeTab(tab); return; }
  const ok = await askConfirm({
    title: 'Agent ještě pracuje',
    html: `V <b>${escapeHtml(tab.title)}</b> se pořád něco děje.` +
          `<span class="hint">Zavřením to přerušíš. Co je hotové, se do paměti uloží samo.</span>`,
    yes: 'Přerušit a zavřít',
    no: 'Nechat běžet',
  });
  if (ok) closeTab(tab);
}

function escapeHtml(text) {
  const d = document.createElement('div');
  d.textContent = text;
  return d.innerHTML;
}

/* `remote` = zavřelo ho jiné okno: server už o tom ví, tady se jen uklidí. */
function closeTab(tab, {remote = false} = {}) {
  if (tab.id && !remote) send({t: 'close', id: tab.id});
  if (tab.releaseIME) tab.releaseIME();
  if (tab.releaseClipboard) tab.releaseClipboard();
  if (tab.composer) tab.composer.release();
  if (tab.keys) tab.keys.release();
  if (tab.links) tab.links.release();
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

/* Název změněný v jiném okně. Kdo ho tu zrovna přepisuje, tomu se nepřepíše. */
function setTitle(tab, name) {
  tab.title = name;
  const holder = tab.el.querySelector('.tab-title');
  if (!holder.querySelector('input')) holder.textContent = name;
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

/* Soubory na server a zpátky jejich cesty. Odděleně od vkládání do promptu,
   protože totéž potřebuje i bublina composeru. */
async function uploadFiles(fileList) {
  const files = [...fileList].filter(f => f && f.size);
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
  return paths;
}

async function attachFiles(tab, fileList) {
  if (!tab.id) return;
  typePaths(tab, await uploadFiles(fileList));
}

/* Cesta k souboru napsaná na prompt — to je jediné, co terminál od obrázku
 * vezme. Chodí sem vložené i přetažené soubory a screenshot ze schránky. */
function typePaths(tab, paths) {
  if (!paths.length || !tab.id) return;
  // Když je vidět bublina, patří cesta do ní — do terminálu by se napsala
  // pod ni, do pole, které není vidět.
  // V bublině je po přiložení vidět náhled, ten mluví za sebe. Do terminálu
  // se píše holá cesta, a tam se hodí říct, co se vlastně stalo.
  if (tab.composer && tab.composer.insertPaths(paths)) return;
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
  // Na počítači sem Ctrl+V nedojde: clipboard.js ho chytá na keydown a ruší mu
  // výchozí chování — obrázek řeší cestou přes server. Tenhle posluchač je tam
  // pro Shift+Insert a všechno ostatní, co paste doopravdy vyvolá. Na serveru
  // (brána) jde Ctrl+V právě sem: schránku tam má jen prohlížeč.
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
    // Bublina si o obrázek umí říct serveru sama (pod WebKitGTK jinak nemá
    // jak). Když ho ale podá prohlížeč, musí o tom vědět — jinak by se
    // tentýž screenshot přiložil dvakrát, každou cestou jednou.
    if (tab.composer) tab.composer.browserPaste();
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
  if (ACTIVE.composer && ACTIVE.composer.run(cmd)) return;
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
  const title = p.label || p.name;
  const ready = agentList(true);
  const preferred = agentFor(p.path);
  // Nejdřív ten, kterým se projekt otevírá teď — ať je klik na kartu a první
  // položka v nabídce totéž.
  ready.sort((a, b) => (b.id === preferred) - (a.id === preferred));
  const items = ready.map((a) => ({
    icon: 'i-terminal', label: 'Otevřít v ' + a.label,
    on: a.id === preferred && ready.length > 1,
    run: () => openWith(a.id, {path: p.path, title}),
  }));
  if (!ready.length) {
    items.push({icon: 'i-terminal', label: 'Žádný agent není nainstalovaný…',
                run: () => HubSettings.open({...hubIO(), state: STATE, tab: 'agenti'})});
  }
  items.push({icon: 'i-note', label: 'Upravit…', run: () => editProject(p)});
  if (devMode()) {
    items.push({icon: 'i-deploy', label: p.deployable ? 'Deploy (FTP)' : 'Deploy',
      run: () => openTab({kind: 'deploy', path: p.path, title: 'deploy: ' + p.name})});
    if (p.repo) {
      items.push({icon: 'i-push', label: 'Otevřít na GitHubu',
        run: () => openExternal('https://github.com/' + p.repo)});
    }
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

function showMenu(x, y, items, opts) {
  const menu = $('ctxmenu');
  menu.textContent = '';
  for (const item of items) {
    const el = document.createElement('button');
    // Vybraná položka (model, režim) se pozná fajfkou místo vlastní ikony.
    el.innerHTML = icon(item.on ? 'i-check' : item.icon) + '<span></span>';
    if (item.on) el.classList.add('on');
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
  // Nabídka z bubliny musí růst nahoru — dole už není kam.
  const top = (opts && opts.above) ? y - box.height : y + 3;
  menu.style.top = Math.max(8, Math.min(top, innerHeight - box.height - 8)) + 'px';
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
  const wsProto = location.protocol === 'https:' ? 'wss://' : 'ws://';
  WS = new WebSocket(`${wsProto}${location.host}/ws?t=${encodeURIComponent(TOKEN)}`);
  WS.onopen = () => send({t: 'hello'});
  WS.onmessage = (ev) => handle(JSON.parse(ev.data));
  WS.onclose = () => setTimeout(connect, 1000);
}

// Přepnutí do tohohle okna = pracuje se tady, rozměr terminálu patří jemu.
window.addEventListener('focus', () => claimSize(ACTIVE));

function handle(msg) {
  if (msg.t === 'out') {
    const tab = TABS.find(t => t.id === msg.id);
    if (tab) {
      tab.term.write(msg.d);
      const now = Date.now();
      if (now - (tab.lastInput || 0) > 400) {
        (tab.outTimes = tab.outTimes || []).push(now);
        if (tab.outTimes.length > 12) tab.outTimes.shift();
      }
    }
  } else if (msg.t === 'opened') {
    const tab = TABS.find(t => t.ref === msg.ref);
    if (tab) {
      tab.id = msg.id;
      tab.bypass = !!msg.bypass;
      // Server mohl model doplnit sám (Ollama bez modelu nespustíš), tak se
      // tím, co doopravdy běží, přepíše i to, s čím se tab zakládal.
      if (msg.model && msg.model !== tab.model) {
        tab.model = msg.model;
        if (tab.composer && tab.composer.setModel) tab.composer.setModel(msg.model);
        paintAgent(tab);
      }
      refit(tab);
    }
  } else if (msg.t === 'exit') {
    const tab = TABS.find(t => t.id === msg.id);
    if (tab) { tab.exited = true; tab.el.classList.add('exited'); }
  } else if (msg.t === 'tab-opened') {
    // Tab z jiného okna téhož hubu (appka i prohlížeč naráz). Přidá se na
    // pozadí — přepnout na něj by tomu, kdo tu zrovna pracuje, sebralo tab.
    if (!TABS.some(t => t.id === msg.id)) {
      attachTab(createTab({kind: msg.kind, path: msg.path, title: msg.title,
                           id: msg.id, agent: msg.agent, model: msg.model,
                           bypass: msg.bypass, resume: msg.resume, background: true}));
    }
  } else if (msg.t === 'tab-closed') {
    const tab = TABS.find(t => t.id === msg.id);
    if (tab) closeTab(tab, {remote: true});
  } else if (msg.t === 'tab-renamed') {
    const tab = TABS.find(t => t.id === msg.id);
    if (tab) setTitle(tab, msg.title);
  } else if (msg.t === 'sessions') {
    if (staleVersion(msg.version)) return;
    restore(msg.list);
  } else if (msg.t === 'memory-saved') {
    memorySaved(msg);
  } else if (msg.t === 'error') {
    const tab = TABS.find(t => t.ref === msg.ref);
    if (tab) closeTab(tab);
    toast(msg.d);
  }
}

/* Po aktualizaci serveru (brána, noční aktualizace) se stránka jen znovu
   připojí a jela by dál se starým JavaScriptem, který o nových věcech neví.
   Rozepsaný text v bublině se ale nezahazuje — pak jen hláška. */
function staleVersion(version) {
  const mine = STATE && STATE.version && STATE.version.version;
  if (!version || !mine || version === mine) return false;
  const typing = [...document.querySelectorAll('.composer-input')].some(i => i.value.trim());
  if (typing) {
    toast('Hub je aktualizovaný — obnov stránku (F5), až dopíšeš.');
    return false;
  }
  location.reload();
  return true;
}

/* Re-attach after a reload or a dropped connection: the server is the source of
 * truth about which sessions exist. */
function restore(list) {
  /* Tab, na kterém člověk je, zůstává. Dřív se po každém znovupřipojení
     (výpadek sítě, uspaný počítač, proxy brány zavře nečinné spojení) přepnulo
     na poslední tab a každý tab, který mezitím přibyl, se aktivoval — uprostřed
     práce to „skočilo" jinam. Na poslední se přepne jen při prvním načtení. */
  const before = ACTIVE;
  const live = new Set(list.map(s => s.id));
  for (const tab of [...TABS]) {
    if (tab.id && !live.has(tab.id)) closeTab(tab);
  }
  for (const info of list) {
    let tab = TABS.find(t => t.id === info.id);
    if (!tab) {
      tab = createTab({kind: info.kind, path: info.path, title: info.title,
                       id: info.id, agent: info.agent, model: info.model,
                       bypass: info.bypass, resume: info.resume, background: true});
    }
    tab.bypass = !!info.bypass;
    attachTab(tab);
  }
  const keep = before && TABS.includes(before) ? before : TABS[TABS.length - 1];
  if (keep && keep !== ACTIVE) activate(keep);
}

/* Připojit tab k session na serveru. Server pošle celý výpis znovu, takže se
 * terminál nejdřív vyčistí. Rozměr se pošle i beze změny: server si ho drží
 * pro každé spojení zvlášť a nové spojení ho ještě nezná. */
function attachTab(tab) {
  tab.term.reset();          // the replay below is the full scrollback
  send({t: 'attach', id: tab.id});
  tab.sentCols = tab.sentRows = null;
  refit(tab);
}

let toastTimer = null;
/* Hook memory-autosave.py doplnil paměť. Stačí o tom vědět — nic se nepotvrzuje. */
async function memorySaved({files, projects}) {
  const notes = (files || []).filter((f) => f !== 'MEMORY.md');
  if (!notes.length) return;
  const where = (projects || []).length ? ` (${projects.join(', ')})` : '';
  toast(`Paměť doplněna${where}: ${notes.join(', ')}`);
  try {
    STATE = await api('state');
    renderMemory();
    renderWelcome();
  } catch (_) { /* hláška stačí, seznam se obnoví příště */ }
}

function toast(text) {
  const el = $('toast');
  el.textContent = text;
  // Bublina se často zvětší v témže tiknutí, ve kterém hláška vzniká — vložená
  // cesta se zalomí na další řádek. ResizeObserver, který --composer-h píše, se
  // ozve až další snímek, takže by se hláška stihla posadit na starou, nižší
  // hranu, tedy přes bublinu. Změří se proto tady, těsně před zobrazením.
  const box = document.querySelector('.pane.active .composer');
  document.documentElement.style.setProperty(
    '--composer-h', (box ? box.offsetHeight : 0) + 'px');
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
    // Obě jména schválně: části UI vznikaly postupně a půlka volá toast(),
    // půlka notice(). Jedno chybějící jméno zabilo hlášku o zkopírovaném logu.
    toast,
    notice: toast,
    copy: copyText,
    // Odkaz ven — na serveru v prohlížeči toho, kdo se dívá (openLink).
    open: openLink,
    pickFolder,
    reload,
    refreshState: async () => { STATE = await api('state'); },
    openWizard: () => HubOnboarding.open({...hubIO(), state: STATE}),
    /* Přihlášení jiným účtem. `/login` žije uvnitř Claude Code, ne v shellu,
       tak se otevře tab, který ho dostane rovnou jako první příkaz — stejnou
       cestou, jakou jde „Upravit poznámku" na /project. Jiný účet znamená
       i jiné konektory (třeba druhou gmailovou schránku). */
    login: () => openTab({kind: 'slash:login', path: STATE.home,
                          title: 'přihlášení'}),
    // Nastavení agentů otevírá taby: instalaci i přihlášení je lepší vidět
    // běžet, než je pustit skrytě na pozadí.
    openTab: (opts) => openTab(opts),
  };
}

/* ── boot ─────────────────────────────────────────────────────────────────── */
/* Chyby ze stránky posíláme do stejného logu jako ty ze serveru — jinak by se
   problém hledal na dvou místech a jedno z nich by nikdo neotevřel. */
function reportToLog(text) {
  try {
    fetch(`/api/log?t=${encodeURIComponent(TOKEN)}`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({text: String(text).slice(0, 500), level: 'error'}),
    }).catch(() => {});
  } catch (_) { /* logování nesmí být důvod další chyby */ }
}

window.addEventListener('error', (ev) => {
  reportToLog(`${ev.message} @ ${ev.filename}:${ev.lineno}`);
});
window.addEventListener('unhandledrejection', (ev) => {
  reportToLog('neodchycené odmítnutí: ' + (ev.reason && ev.reason.message || ev.reason));
});

async function main() {
  // Hub na serveru: appka sem přišla s `#local=`, cestou zpátky na počítač.
  // Čte se hned, ať adresa s tokenem nezůstane viset v řádku ani v historii.
  HubServer.captureLocal();
  // Návrat ze serveru se zapíše dřív, než se načte zbytek hubu: kdo okno
  // zavře hned po návratu, má příště naskočit tady, ne zase na serveru.
  const returned = await applyReturn();

  const saved = localStorage.getItem('hub-theme');
  DARK = saved ? saved === 'dark'
               : !window.matchMedia('(prefers-color-scheme: light)').matches;

  await reload();

  $('search').oninput = (ev) => renderProjects(ev.target.value);
  $('btn-refresh').onclick = () => reload();
  $('btn-theme').onclick = () => setTheme(!DARK, true);
  $('btn-settings').onclick = () => HubSettings.open({...hubIO(), state: STATE});
  $('btn-stats').onclick = () => HubStats.open(hubIO());
  initChats();
  $('btn-new-shell').onclick = () => openTab({kind: 'shell', path: '', title: 'terminál'});
  // Levý klik = výchozí agent, pravý klik nebo delší podržení = výběr.
  // Nabídka se sama otevře i tehdy, když výchozí agent není nainstalovaný.
  $('btn-new-agent').onclick = (ev) => {
    const ready = agentList(true);
    const def = agentById(STATE.default_agent);
    if (ready.length > 1 && (ev.altKey || !def || !def.path)) return newAgentMenu(ev);
    const a = (def && def.path) ? def : ready[0];
    if (!a) return HubSettings.open({...hubIO(), state: STATE, tab: 'agenti'});
    openTab({kind: 'project', path: STATE.home, title: a.label, agent: a.id});
  };
  $('btn-new-agent').oncontextmenu = (ev) => { ev.preventDefault(); newAgentMenu(ev); };
  // Firemní tab: Claude v něm pracuje nad firemním Obsidianem a zapisuje do něj
  // rovnou — souhlas dal uživatel tím, že tab otevřel.
  $('btn-new-firma').onclick = () =>
    openTab({kind: 'project', path: STATE.home, title: 'Claude Code firemní',
             agent: 'claude', vault: 'firma'});
  $('btn-brain').onclick = () => (vaultPreview() ? openVault() : openExternal('', 'brain'));
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

  // Zavření celého okna. Vlastní dialog sem nedosáhne — křížek okna řídí
  // prohlížeč, takže se ptá on. A jen když by se utnula rozdělaná práce:
  // do paměti se session uloží samy, jakmile hub taby zavře.
  window.addEventListener('beforeunload', (ev) => {
    // Přechod mezi počítačem a serverem není zavírání: taby na počítači běží
    // dál a stránka se k nim po návratu připojí.
    if (window.HUB_LEAVING) return;
    if (!TABS.some(t => t.id && !t.exited && isBusy(t))) return;
    ev.preventDefault();
    ev.returnValue = '';             // vyžadují starší prohlížeče
  });

  connect();
  checkForUpdate();

  await startScreen(returned);
}

/* Okno se vrátilo ze serveru: `?mode=local` (pracovat tady) nebo
 * `?mode=logout` (odhlásit appku). Parametr se z adresy hned odstraní, ať
 * reload stránky akci nezopakuje. */
async function applyReturn() {
  if (!HubServer.isAppWindow()) return '';
  const params = new URLSearchParams(location.search);
  const mode = params.get('mode');
  if (!mode) return '';
  params.delete('mode');
  history.replaceState(null, '', location.pathname + '?' + params.toString());
  if (mode === 'local') await api('account', {action: 'local'}).catch(() => {});
  else if (mode === 'logout') await api('account', {action: 'logout'}).catch(() => {});
  else return '';
  return mode;
}

/* Co ukázat po startu: prostor na serveru, přihlášení, nebo průvodce.
 *
 * Když je appka v serverovém režimu, launcher okno pošle na server rovnou.
 * Sem se tedy dostane, jen když to nevyšlo (server neodpovídá, přihlášení
 * vypršelo) — nebo když se okno ze serveru vrátilo. */
async function startScreen(returned) {
  const onLocal = () => {
    // Napoprvé se hub nastavuje tady, ne v instalačce — ta běží jednou a v
    // terminálu, takže po ní nebylo kde nastavení změnit.
    if (!STATE.onboarded) HubOnboarding.open({...hubIO(), state: STATE});
  };
  if (STATE.config.gateway_user || !HubServer.isAppWindow()) return onLocal();

  if (returned === 'local') {
    toast('Pracuješ na tomhle počítači. Na server se vrátíš v Nastavení → Účet.');
    return onLocal();
  }
  if (returned === 'logout') {
    HubServer.gate(hubIO(), {loggedOut: true, onLocal,
                             note: 'Odhlášeno. Přihlas se znovu, nebo pracuj na počítači.'});
    return;
  }
  if (STATE.config.server_mode) {
    HubServer.gate(hubIO(), {onLocal});
    return;
  }
  onLocal();
}

/* ── Je venku nová verze? ────────────────────────────────────────────────────
 * Běží při startu na pozadí — načtení okna na to nikdy nečeká a když GitHub
 * neodpoví, mlčí se. Odpověď se drží půl dne, aby se na každé spuštění
 * neťukalo na síť; jakmile je něco venku, ukazuje se z paměti hned. */
const UPDATE_CACHE_KEY = 'hubUpdateCheck';
const UPDATE_CACHE_MS = 12 * 60 * 60 * 1000;

// Značka na GitHubu je „v1.6.0", verze v aplikaci „1.6.0" — bez tohohle by se
// po aktualizaci nikdy nerovnaly a pilulka by svítila napořád.
const bare = (v) => String(v || '').replace(/^v/, '');

function readUpdateCache() {
  try {
    const raw = JSON.parse(localStorage.getItem(UPDATE_CACHE_KEY) || 'null');
    // Po vlastní aktualizaci sedí uložené „latest" na běžící verzi — pak už
    // není co hlásit a záznam se zahodí, ať pilulka nesvítí zbytečně.
    if (raw && raw.latest && bare(raw.latest) !== bare(STATE.version.version)) return raw;
    if (raw) localStorage.removeItem(UPDATE_CACHE_KEY);
  } catch (_) { /* rozbitý nebo nedostupný localStorage nesmí zabít start */ }
  return null;
}

function showUpdate(latest) {
  const btn = $('btn-update');
  $('btn-update-text').textContent = `Nová verze ${latest}`;
  btn.title = `Je venku verze ${latest}, máš ${STATE.version.version}. ` +
              `Klikni pro aktualizaci v nastavení.`;
  btn.hidden = false;
  btn.onclick = () => HubSettings.open({...hubIO(), state: STATE});
}

async function checkForUpdate() {
  // Na serveru aktualizuje server sám (gateway/update.sh) — není co nabízet.
  if (onServer()) return;
  const cached = readUpdateCache();
  if (cached) showUpdate(cached.latest);
  if (cached && Date.now() - cached.at < UPDATE_CACHE_MS) return;

  let info;
  try {
    info = await api('update-check');
  } catch (_) {
    return;                       // bez sítě se prostě nic neřekne
  }
  if (!info || !info.update_available || !info.latest) {
    try { localStorage.removeItem(UPDATE_CACHE_KEY); } catch (_) { /* nevadí */ }
    return;
  }
  try {
    localStorage.setItem(UPDATE_CACHE_KEY,
      JSON.stringify({at: Date.now(), latest: info.latest}));
  } catch (_) { /* nevadí, jen se příště zeptáme znovu */ }
  showUpdate(info.latest);
  // Toast až po síti: kdyby se ukazoval i z paměti, otravoval by při každém
  // spuštění, dokud člověk neaktualizuje.
  if (!cached || bare(cached.latest) !== bare(info.latest)) {
    toast(`Je venku nová verze Hubu ${info.latest} (máš ${info.version}). ` +
          `Aktualizovat můžeš v nastavení.`);
  }
}

main().catch(err => toast('Hub se nenačetl: ' + err.message));
