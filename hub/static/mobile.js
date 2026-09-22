/* Co hub potřebuje navíc, když ho někdo otevře v telefonu.
 *
 * Rozvržení řeší hub.css: na úzké obrazovce se z postranního panelu stane
 * šuplík. Tady je jen to, co musí umět kód — otevírání šuplíku, dlouhý stisk
 * místo pravého tlačítka (iOS žádné contextmenu neposílá), přepočet terminálu
 * při vyjetí měkké klávesnice a registrace service workeru, bez kterého
 * Android nenabídne instalaci na plochu.
 *
 * Se stránkou se domlouvá přes DOM a `resize`, ne přes vnitřek hub.js —
 * v prohlížeči na počítači se tenhle soubor jen tiše nezapne.
 */
'use strict';

(function () {

  const NARROW = '(max-width: 820px)';
  const LONG_PRESS_MS = 500;
  const MOVE_TOLERANCE = 10;   // px, nad to je to scroll a ne podržení

  const $ = (id) => document.getElementById(id);

  /* ── třídy na <body> ──────────────────────────────────────────────────── */
  // Dotyk a úzká obrazovka jsou dvě různé věci: tablet je dotykový a široký,
  // okno hubu zmenšené na půlku obrazovky je úzké a myší.
  function applyClasses() {
    const narrow = window.matchMedia(NARROW).matches;
    document.body.classList.toggle('is-narrow', narrow);
    if (!narrow) closeDrawer();
    moveActionbar(narrow);
  }

  const touch = matchMedia('(pointer: coarse)').matches;
  document.body.classList.toggle('is-touch', touch);
  // Standalone = spuštěno z ikony na ploše, ne z prohlížeče.
  if (matchMedia('(display-mode: standalone)').matches ||
      window.navigator.standalone) {
    document.body.classList.add('is-app');
  }

  /* ── šuplík s projekty ────────────────────────────────────────────────── */
  const scrim = $('drawer-scrim');
  const toggle = $('btn-drawer');

  function openDrawer() {
    document.body.classList.add('drawer-open');
    if (scrim) scrim.hidden = false;
  }

  function closeDrawer() {
    document.body.classList.remove('drawer-open');
    if (scrim) scrim.hidden = true;
  }

  if (toggle) {
    toggle.onclick = () => {
      if (document.body.classList.contains('drawer-open')) closeDrawer();
      else openDrawer();
    };
  }
  if (scrim) scrim.onclick = closeDrawer;
  // Šuplík otevírá i nabídka v liště (hub.js) — odsud na ni sahá.
  window.HubMobile = {openDrawer, closeDrawer};
  // Křížek v hlavičce šuplíku: klika, která ho otevřela, je pod ním.
  const closeBtn = $('btn-drawer-close');
  if (closeBtn) closeBtn.onclick = closeDrawer;

  // Klik na projekt otevře tab — šuplík už jen překáží.
  for (const id of ['projects', 'chats', 'memory', 'actions']) {
    const box = $(id);
    if (box) box.addEventListener('click', () => setTimeout(closeDrawer, 60));
  }
  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape') closeDrawer();
  });

  /* Rychlé akce mají na telefonu vlastní sloupec vedle terminálu kde vzít —
     tak se stěhují do šuplíku pod projekty. Přesouvá se celý prvek, takže
     tlačítka v něm si dál plní hub.js a o ničem neví. */
  const layout = document.querySelector('.layout');
  const sidebar = document.querySelector('.sidebar');
  const actionbar = $('actionbar');
  const spacer = sidebar ? sidebar.querySelector('.spacer') : null;

  function moveActionbar(narrow) {
    if (!actionbar || !layout || !sidebar) return;
    if (narrow && actionbar.parentElement !== sidebar) {
      sidebar.insertBefore(actionbar, spacer);
    } else if (!narrow && actionbar.parentElement !== layout) {
      layout.appendChild(actionbar);
    }
  }

  /* ── jako appka Claude: taby v šuplíku, jméno tabu v liště ────────────── */
  /* Lišta s taby se na telefon nevejde (a v appce Claude žádná není): otevřené
     taby jsou v šuplíku nad projekty a jméno toho, na který se kouká, stojí
     uprostřed horní lišty. Seznam je zrcadlo #tabbar — klepnutí posílá dál
     na původní tlačítko tabu, takže o přepínání i zavírání dál rozhoduje
     hub.js a o tomhle nic neví. */
  const tabbar = $('tabbar');
  const openBox = $('open-tabs');
  const openSection = $('open-section');
  const tabTitle = $('topbar-tab');

  function syncTabs() {
    if (!tabbar || !openBox) return;
    const tabs = [...tabbar.querySelectorAll('.tab')];
    const active = tabs.find((t) => t.classList.contains('active'));
    const name = (t) => (t.querySelector('.tab-title') || t).textContent.trim();
    if (tabTitle) tabTitle.textContent = active ? name(active) : '';
    document.body.classList.toggle('has-tab', !!active);
    openSection.hidden = !tabs.length;
    openBox.textContent = '';
    for (const t of tabs) {
      const row = document.createElement('div');
      row.className = 'open-tab' + (t === active ? ' on' : '') +
                      (t.classList.contains('exited') ? ' exited' : '');
      const dot = t.querySelector('.tab-agent');
      const d = document.createElement('span');
      d.className = 'open-dot';
      if (dot && dot.style.getPropertyValue('--agent-color')) {
        d.style.background = dot.style.getPropertyValue('--agent-color');
      }
      if (t.classList.contains('firma')) d.style.background = 'var(--firma)';
      const go = document.createElement('button');
      go.className = 'open-name';
      go.textContent = name(t);
      go.onclick = () => { t.click(); closeDrawer(); };
      const x = document.createElement('button');
      x.className = 'open-x';
      x.title = 'Zavřít tab';
      x.innerHTML = '<svg class="ico"><use href="#i-close"/></svg>';
      x.onclick = () => { const c = t.querySelector('.tab-close'); if (c) c.click(); };
      row.append(d, go, x);
      openBox.appendChild(row);
    }
  }
  if (tabbar) {
    let pending = 0;
    new MutationObserver(() => {
      cancelAnimationFrame(pending);
      pending = requestAnimationFrame(syncTabs);
    }).observe(tabbar, {childList: true, subtree: true, characterData: true,
                        attributes: true, attributeFilter: ['class', 'style']});
    syncTabs();
  }

  /* Nový tab: stejná nabídka jako „+" v liště tabů (hub.js), jen zakotvená
     u tlačítka, na které se kleplo. */
  function newTab(anchor) {
    const plus = $('btn-new-menu');
    if (plus && plus.onclick) plus.onclick({currentTarget: anchor});
  }
  const newTop = $('btn-new-top');
  if (newTop) newTop.onclick = () => newTab(newTop);
  const drawerNew = $('btn-drawer-new');
  if (drawerNew) drawerNew.onclick = () => { closeDrawer(); if (newTop) newTab(newTop); };
  const drawerX = $('btn-drawer-x');
  if (drawerX) drawerX.onclick = closeDrawer;

  matchMedia(NARROW).addEventListener('change', applyClasses);
  applyClasses();

  /* ── dlouhý stisk místo pravého tlačítka ──────────────────────────────── */
  /* Chrome na Androidu pošle při podržení `contextmenu` sám, Safari na iOS ne
     — tam by se k přejmenování, archivaci ani deploy projektu nikdo nedostal.
     Podržení proto vyrobí tutéž událost ručně; prohlížeč, který ji pošle sám,
     se pozná podle toho, že přijde dřív, a druhá se už nekoná. */
  if (touch) {
    let timer = null, start = null, fired = false;

    const cancel = () => {
      clearTimeout(timer);
      timer = null;
      start = null;
    };

    document.addEventListener('touchstart', (ev) => {
      if (ev.touches.length !== 1) return cancel();
      const t = ev.touches[0];
      const target = t.target.closest('.card, .tab, .barbtn, .chat-item');
      if (!target) return;
      start = {x: t.clientX, y: t.clientY};
      fired = false;
      timer = setTimeout(() => {
        fired = true;
        target.dispatchEvent(new MouseEvent('contextmenu', {
          bubbles: true, cancelable: true,
          clientX: start.x, clientY: start.y,
        }));
        if (navigator.vibrate) navigator.vibrate(12);
      }, LONG_PRESS_MS);
    }, {passive: true});

    document.addEventListener('touchmove', (ev) => {
      if (!start || !timer) return;
      const t = ev.touches[0];
      if (Math.abs(t.clientX - start.x) > MOVE_TOLERANCE ||
          Math.abs(t.clientY - start.y) > MOVE_TOLERANCE) cancel();
    }, {passive: true});

    document.addEventListener('touchend', (ev) => {
      // Po vyvolané nabídce nesmí projít i klepnutí, jinak se rovnou otevře tab.
      if (fired) { ev.preventDefault(); fired = false; }
      cancel();
    });
    document.addEventListener('touchcancel', cancel, {passive: true});
  }

  /* ── měkká klávesnice ─────────────────────────────────────────────────── */
  /* Když vyjede klávesnice, okno se na Androidu zmenší (interactive-widget
     v <meta viewport>), kdežto na iOS se jen posune vizuální výřez a `resize`
     nepřijde. Terminál by pak počítal se starou výškou. hub.js na `resize`
     přepočítává sám, takže stačí ho poslat. */
  // Jen na dotykovém zařízení. Na počítači měkká klávesnice není, zato tam
  // visualViewport umí přijít i jindy — a každé takové „přepočítej" jde přes
  // refit až do pty, kde ConPTY na Windows překreslí celou obrazovku.
  if (touch && window.visualViewport) {
    let pending = 0;
    const nudge = () => {
      clearTimeout(pending);
      pending = setTimeout(() => window.dispatchEvent(new Event('resize')), 120);
    };
    visualViewport.addEventListener('resize', nudge);
    visualViewport.addEventListener('scroll', nudge);
  }

  /* ── service worker ───────────────────────────────────────────────────── */
  // Registruje se jen v bezpečném kontextu; přes http na tailnet adrese ho
  // prohlížeč stejně odmítne a hub funguje i bez něj, jen se nedá nainstalovat.
  if ('serviceWorker' in navigator && window.isSecureContext) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('/sw.js').catch(() => {
        /* bez něj se jen nenabídne instalace na plochu */
      });
    });
  }

})();
