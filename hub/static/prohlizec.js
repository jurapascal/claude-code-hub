/* Prohlížeč v appce — plovoucí okno s prohlížečem, který používá Claude.
 *
 * Chová se jako skutečný prohlížeč: karty se zavíráním a ikonami, adresní
 * řádek se zámkem, zpět / vpřed / načíst znovu / zastavit, klávesové zkratky
 * (Ctrl+L, T, W, R, Alt+šipky, Ctrl+Tab, Ctrl+C), dialogy stránky (alert,
 * confirm, prompt) a stránka, která vidí přesně tu velikost, kterou vidíš ty.
 * Claude v něm pracuje přes Playwright a ty mu můžeš do toho sáhnout: kliknout,
 * přihlásit se, vyřešit captchu.
 *
 * Okno jde přesunout za záhlaví, změnit mu velikost za pravý dolní roh,
 * minimalizovat do lišty „Prohlížeč“ a zavřít. Když Claude prohlížeč zase
 * potřebuje (spustí nástroj Playwright), okno samo vyskočí.
 *
 * Obraz a vstup jdou přes websocket hubu (hub/prohlizec.py): server posílá
 * snímky stránky, tady se kreslí a zpátky jde myš, kolečko a klávesy.
 */
'use strict';

(function (global) {
  const KEY = 'hub-prohlizec';
  const MIN_W = 360, MIN_H = 260;
  const POP_PAUZA = 25000;          // po jak dlouhé pauze mezi nástroji vyskočí znovu

  let send = () => {};
  let copyText = null;
  let root = null, canvas = null, ctx = null, urlInput = null, tabsBox = null, stavEl = null;
  let kb = null, scena = null, zamek = null, titulek = null, dialogEl = null;
  let tlZpet = null, tlVpred = null, tlZnovu = null;
  let pill = null;
  let frame = {w: 1280, h: 800};
  let stav = 'zavreno';             // zavreno | okno | mini
  let geo = null;
  let otevrene = false;              // poslali jsme serveru {a:'open'}
  let posledniNastroj = 0;
  let info = {pages: [], active: '', url: '', back: false, fwd: false, loading: false};
  let sizeTimer = null, odeslanaVelikost = '';
  const taby = new Map();            // id karty → její prvek

  const $ = (s, r) => (r || root).querySelector(s);
  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }
  const ico = (id) => `<svg class="ico"><use href="#${id}"/></svg>`;

  function nactiGeo() {
    let g = null;
    try { g = JSON.parse(localStorage.getItem(KEY) || 'null'); } catch (_) { /* nic */ }
    const vw = window.innerWidth, vh = window.innerHeight;
    const w = Math.min(g && g.w || Math.round(vw * 0.62), vw - 16), h = Math.min(g && g.h || Math.round(vh * 0.72), vh - 16);
    return {
      w: Math.max(MIN_W, w), h: Math.max(MIN_H, h),
      x: Math.max(0, Math.min(g ? g.x : vw - w - 24, vw - 120)),
      y: Math.max(0, Math.min(g ? g.y : vh - h - 90, vh - 60)),
      stav: g && g.stav,
    };
  }
  function ulozGeo() {
    try { localStorage.setItem(KEY, JSON.stringify({...geo, stav})); } catch (_) { /* soukromé okno */ }
  }

  function usad() {
    if (!root) return;
    root.style.left = geo.x + 'px';
    root.style.top = geo.y + 'px';
    root.style.width = geo.w + 'px';
    root.style.height = geo.h + 'px';
  }

  function sestav() {
    root = el('div', 'br-okno');
    root.innerHTML =
      '<div class="br-hlava">' +
        '<span class="br-nazev">' + ico('i-globe') + '<b>Prohlížeč</b></span>' +
        '<span class="br-spacer"></span>' +
        '<button class="br-btn br-mini" title="Minimalizovat">' + ico('i-minus') + '</button>' +
        '<button class="br-btn br-zavri" title="Zavřít okno">' + ico('i-close') + '</button>' +
      '</div>' +
      '<div class="br-taby"></div>' +
      '<div class="br-lista">' +
        '<button class="br-btn br-zpet br-otoc" title="Zpět (Alt+←)">' + ico('i-chevron') + '</button>' +
        '<button class="br-btn br-vpred" title="Vpřed (Alt+→)">' + ico('i-chevron') + '</button>' +
        '<button class="br-btn br-znovu" title="Načíst znovu (Ctrl+R)">' + ico('i-refresh') + '</button>' +
        '<div class="br-urlbox"><span class="br-zamek"></span>' +
          '<input class="br-url" type="text" spellcheck="false" autocomplete="off" placeholder="Adresa nebo hledání"></div>' +
      '</div>' +
      '<div class="br-scena"><canvas class="br-platno"></canvas>' +
        '<textarea class="br-kb" autocapitalize="off" autocomplete="off" autocorrect="off" spellcheck="false" tabindex="0"></textarea>' +
        '<div class="br-stav"></div><div class="br-dialog" hidden></div></div>' +
      '<div class="br-roh" title="Změnit velikost"></div>';
    document.body.appendChild(root);
    canvas = $('.br-platno');
    ctx = canvas.getContext('2d');
    urlInput = $('.br-url');
    tabsBox = $('.br-taby');
    stavEl = $('.br-stav');
    stavEl.textContent = 'Připojuji prohlížeč…';
    scena = $('.br-scena');
    zamek = $('.br-zamek');
    titulek = $('.br-nazev b');
    dialogEl = $('.br-dialog');
    kb = $('.br-kb');
    tlZpet = $('.br-zpet'); tlVpred = $('.br-vpred'); tlZnovu = $('.br-znovu');
    $('.br-mini').onclick = () => nastav('mini');
    $('.br-zavri').onclick = () => nastav('zavreno');
    tlZpet.onclick = () => send({t: 'br', a: 'back'});
    tlVpred.onclick = () => send({t: 'br', a: 'forward'});
    tlZnovu.onclick = () => send({t: 'br', a: info.loading ? 'stop' : 'reload'});
    urlInput.addEventListener('focus', () => { urlInput.value = info.url === 'about:blank' ? '' : (info.url || ''); urlInput.select(); });
    urlInput.addEventListener('blur', ukazUrl);
    urlInput.addEventListener('keydown', (ev) => {
      ev.stopPropagation();
      if (ev.key === 'Enter') { send({t: 'br', a: 'go', url: urlInput.value}); kb.focus(); }
      else if (ev.key === 'Escape') { kb.focus(); }
      else zkratka(ev);
    });
    tahni($('.br-hlava'));
    velikost($('.br-roh'));
    platno();
    // Změna plochy okna = stránka dostane novou velikost (jako když tažíš za okraj v Chromu).
    new ResizeObserver(() => { rozloz(); posliVelikost(); }).observe(scena);
    root.addEventListener('pointerdown', (ev) => {
      root.classList.add('br-nahore');
      // Zkratky (Ctrl+T, Ctrl+Tab…) mají fungovat i po kliknutí na kartu nebo lištu.
      if (!ev.target.closest('input, textarea')) setTimeout(() => { try { kb.focus({preventScroll: true}); } catch (_) { /* nic */ } }, 0);
    });
    // Zkratky fungují kdekoli v okně (i na liště), ne jen v poli pro psaní.
    root.addEventListener('keydown', (ev) => { if (ev.target !== kb && ev.target !== urlInput) zkratka(ev); });
  }

  /* ── plátno: velikost, přesun ─────────────────────────────────────────────
     Obraz se na plátno nezmenšuje přes CSS (`object-fit` na canvasu některé
     jádra neumí), velikost i střed se počítají tady. Stránka má velikost
     plochy okna, takže měřítko je většinou přesně 1:1. */
  function rozloz() {
    if (!canvas || !scena) return;
    const sw = scena.clientWidth, sh = scena.clientHeight;
    if (!sw || !sh) return;
    const k = Math.min(sw / frame.w, sh / frame.h);
    const w = Math.round(frame.w * k), h = Math.round(frame.h * k);
    canvas.style.width = w + 'px';
    canvas.style.height = h + 'px';
    canvas.style.left = Math.round((sw - w) / 2) + 'px';
    canvas.style.top = Math.round((sh - h) / 2) + 'px';
  }

  function posliVelikost() {
    clearTimeout(sizeTimer);
    sizeTimer = setTimeout(() => {
      if (!otevrene || !scena) return;
      const w = scena.clientWidth, h = scena.clientHeight;
      const sig = w + 'x' + h;
      if (!w || !h || sig === odeslanaVelikost) return;
      odeslanaVelikost = sig;
      send({t: 'br', a: 'size', w, h});
    }, 250);
  }

  function tahni(hlava) {
    hlava.addEventListener('pointerdown', (ev) => {
      if (ev.target.closest('.br-btn') || root.classList.contains('br-cela')) return;
      ev.preventDefault();
      const x0 = ev.clientX - geo.x, y0 = ev.clientY - geo.y;
      hlava.setPointerCapture(ev.pointerId);
      const move = (e) => {
        geo.x = Math.max(-geo.w + 100, Math.min(e.clientX - x0, window.innerWidth - 100));
        geo.y = Math.max(0, Math.min(e.clientY - y0, window.innerHeight - 40));
        usad();
      };
      const up = () => {
        hlava.removeEventListener('pointermove', move);
        hlava.removeEventListener('pointerup', up);
        ulozGeo();
      };
      hlava.addEventListener('pointermove', move);
      hlava.addEventListener('pointerup', up);
    });
    hlava.addEventListener('dblclick', (ev) => {
      if (ev.target.closest('.br-btn')) return;
      root.classList.toggle('br-cela');
    });
  }

  function velikost(roh) {
    roh.addEventListener('pointerdown', (ev) => {
      ev.preventDefault();
      const x0 = ev.clientX, y0 = ev.clientY, w0 = geo.w, h0 = geo.h;
      roh.setPointerCapture(ev.pointerId);
      const move = (e) => {
        geo.w = Math.max(MIN_W, Math.min(w0 + e.clientX - x0, window.innerWidth - geo.x));
        geo.h = Math.max(MIN_H, Math.min(h0 + e.clientY - y0, window.innerHeight - geo.y));
        usad();
      };
      const up = () => {
        roh.removeEventListener('pointermove', move);
        roh.removeEventListener('pointerup', up);
        ulozGeo();
      };
      roh.addEventListener('pointermove', move);
      roh.addEventListener('pointerup', up);
    });
  }

  /* ── zkratky jako v prohlížeči ───────────────────────────────────────────── */
  function dalsiKarta(posun) {
    const ids = (info.pages || []).map((p) => p.id);
    if (ids.length < 2) return;
    const i = ids.indexOf(info.active);
    send({t: 'br', a: 'tab', id: ids[(i + posun + ids.length) % ids.length]});
  }

  /* Vrací true, když zkratku vzalo okno (a nemá jít do stránky). */
  function zkratka(ev) {
    const ctrl = ev.ctrlKey || ev.metaKey;
    const k = ev.key.toLowerCase();
    let hotovo = true;
    if (ctrl && k === 'l') { urlInput.focus(); }
    else if (ctrl && k === 't') { send({t: 'br', a: 'newtab'}); }
    else if (ctrl && k === 'w') { if (info.active) send({t: 'br', a: 'closetab', id: info.active}); }
    else if ((ctrl && k === 'r') || ev.key === 'F5') { send({t: 'br', a: 'reload'}); }
    else if (ev.altKey && ev.key === 'ArrowLeft') { send({t: 'br', a: 'back'}); }
    else if (ev.altKey && ev.key === 'ArrowRight') { send({t: 'br', a: 'forward'}); }
    else if (ctrl && ev.key === 'Tab') { dalsiKarta(ev.shiftKey ? -1 : 1); }
    else if (ctrl && ev.key === 'PageDown') { dalsiKarta(1); }
    else if (ctrl && ev.key === 'PageUp') { dalsiKarta(-1); }
    else if (ctrl && k === 'c' && document.activeElement !== urlInput) { send({t: 'br', a: 'copy'}); }
    else if (ev.key === 'Escape' && info.loading && document.activeElement !== urlInput) { send({t: 'br', a: 'stop'}); }
    else hotovo = false;
    if (hotovo) { ev.preventDefault(); ev.stopPropagation(); }
    return hotovo;
  }

  /* ── plátno: myš, kolečko, klávesy ───────────────────────────────────────── */
  function platno() {
    // Plátno má přesně velikost obrazu, takže stačí poměr jeho rozměrů.
    const bod = (ev) => {
      const r = canvas.getBoundingClientRect();
      return {x: (ev.clientX - r.left) * frame.w / (r.width || 1), y: (ev.clientY - r.top) * frame.h / (r.height || 1)};
    };
    const mod = (ev) => (ev.altKey ? 1 : 0) | (ev.ctrlKey ? 2 : 0) | (ev.metaKey ? 4 : 0) | (ev.shiftKey ? 8 : 0);
    let posl = 0;
    canvas.addEventListener('pointerdown', (ev) => {
      kb.focus();
      canvas.setPointerCapture(ev.pointerId);
      send({t: 'br', a: 'mouse', type: 'down', ...bod(ev), button: ev.button, clicks: ev.detail || 1, mod: mod(ev)});
    });
    canvas.addEventListener('pointerup', (ev) => {
      send({t: 'br', a: 'mouse', type: 'up', ...bod(ev), button: ev.button, clicks: ev.detail || 1, mod: mod(ev)});
    });
    canvas.addEventListener('pointermove', (ev) => {
      const now = Date.now();
      if (now - posl < 40) return;           // ~25× za vteřinu stačí
      posl = now;
      send({t: 'br', a: 'mouse', type: 'move', ...bod(ev), button: 0, clicks: 0, mod: mod(ev)});
    });
    canvas.addEventListener('wheel', (ev) => {
      ev.preventDefault();
      send({t: 'br', a: 'wheel', ...bod(ev), dx: ev.deltaX, dy: ev.deltaY});
    }, {passive: false});
    canvas.addEventListener('contextmenu', (ev) => ev.preventDefault());

    /* Psaní bere skrytá textarea pod plátnem: na plátno se psát nedá a diakritiku
       (ě, š, č…) z metody zadávání by zahodilo. Obyčejná písmena ASCII jdou jako
       klávesy (stránky je vidí jako skutečné stisky), cokoli dalšího — diakritika,
       složené znaky, vložení — jde jako text. Enter, šipky, Backspace a zkratky
       prohlížeče jdou vždycky jako klávesy / příkazy. */
    const foc = () => { try { kb.focus({preventScroll: true}); } catch (_) { kb.focus(); } };
    canvas.addEventListener('pointerdown', foc);
    scena.addEventListener('pointerup', () => { if (document.activeElement !== urlInput) foc(); });
    const ascii = (ev) => ev.key.length === 1 && ev.key.charCodeAt(0) < 128;
    kb.addEventListener('keydown', (ev) => {
      ev.stopPropagation();
      if (zkratka(ev)) return;
      if (ev.isComposing || ev.key === 'Process' || ev.key === 'Dead') return;
      const zk = ev.ctrlKey || ev.metaKey || ev.altKey;
      if (ev.key.length === 1 && !ascii(ev) && !zk) return;            // diakritika → textarea → input
      if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === 'v') return;   // vložení → paste
      ev.preventDefault();
      send({t: 'br', a: 'key', type: 'down', key: ev.key, code: ev.code, vk: ev.keyCode, mod: mod(ev)});
    });
    kb.addEventListener('keyup', (ev) => {
      ev.stopPropagation();
      if (ev.isComposing || ev.key === 'Process' || ev.key === 'Dead') return;
      if (ev.key.length === 1 && !ascii(ev) && !(ev.ctrlKey || ev.metaKey || ev.altKey)) return;
      if ((ev.ctrlKey || ev.metaKey) && ['v', 'c', 'l', 't', 'w', 'r'].includes(ev.key.toLowerCase())) return;
      if (ev.key === 'F5' || (ev.altKey && (ev.key === 'ArrowLeft' || ev.key === 'ArrowRight'))) return;
      ev.preventDefault();
      send({t: 'br', a: 'key', type: 'up', key: ev.key, code: ev.code, vk: ev.keyCode, mod: mod(ev)});
    });
    const vypis = () => {
      const text = kb.value;
      kb.value = '';
      if (text) send({t: 'br', a: 'text', text});
    };
    kb.addEventListener('input', (ev) => { if (!ev.isComposing) vypis(); });
    kb.addEventListener('compositionend', () => setTimeout(vypis, 0));
    kb.addEventListener('paste', (ev) => {
      ev.preventDefault();
      const text = ev.clipboardData && ev.clipboardData.getData('text');
      if (text) send({t: 'br', a: 'text', text});
    });
  }

  /* ── stavy okna ──────────────────────────────────────────────────────────── */
  function otevriSpojeni() {
    if (otevrene) return;
    otevrene = true;
    odeslanaVelikost = '';
    send({t: 'br', a: 'open'});
    posliVelikost();
  }
  function zavriSpojeni() {
    if (!otevrene) return;
    otevrene = false;
    send({t: 'br', a: 'close'});
  }

  function pilulka() {
    if (pill) return pill;
    pill = el('button', 'br-pilulka');
    pill.innerHTML = ico('i-globe') + '<span>Prohlížeč</span>';
    pill.title = 'Znovu otevřít prohlížeč';
    pill.onclick = () => nastav('okno');
    document.body.appendChild(pill);
    return pill;
  }

  /* `zavreno` = pryč a server neposílá nic; `mini` = jen lišta (obraz se dál
     stahuje, ať je při rozbalení hned vidět, co se děje); `okno` = plovoucí. */
  function nastav(novy, potichu) {
    if (!root) { geo = nactiGeo(); sestav(); usad(); }
    stav = novy;
    root.hidden = novy !== 'okno';
    pilulka().hidden = novy !== 'mini';
    document.body.classList.toggle('br-otevren', novy === 'okno');
    if (novy === 'zavreno') zavriSpojeni(); else otevriSpojeni();
    if (novy === 'okno') {
      usad();
      rozloz();
      posliVelikost();
      if (!potichu) root.classList.remove('br-zavolano');
    }
    ulozGeo();
  }

  /* ── vykreslení stavu ze serveru ─────────────────────────────────────────── */
  const obraz = new Image();
  obraz.onload = () => {
    if (!canvas) return;
    if (canvas.width !== frame.w || canvas.height !== frame.h) {
      canvas.width = frame.w;
      canvas.height = frame.h;
      rozloz();
    }
    ctx.drawImage(obraz, 0, 0, canvas.width, canvas.height);
  };

  function hostitel(url) {
    try { return new URL(url).host.replace(/^www\./, ''); } catch (_) { return ''; }
  }

  function ukazUrl() {
    if (!urlInput || document.activeElement === urlInput) return;
    const u = info.url || '';
    urlInput.value = !u || u === 'about:blank' ? '' : u.replace(/^https?:\/\/(www\.)?/, '').replace(/\/$/, '');
    urlInput.title = u;
    const https = /^https:/i.test(u);
    zamek.className = 'br-zamek' + (https ? ' ok' : u && !/^about:/i.test(u) ? ' nejiste' : '');
    zamek.innerHTML = https ? ico('i-lock') : (u && !/^about:/i.test(u) ? '<span class="br-nezab">Nezabezpečeno</span>' : '');
  }

  /* Karty se jen aktualizují (nepřestavují), ať neblikají a klik nezmizí pod
     myší. Pořadí drží server podle vzniku karty — kliknutá karta zůstane na
     svém místě. */
  function kresliKarty(msg) {
    const pages = msg.pages || [];
    const ids = new Set(pages.map((p) => p.id));
    for (const [id, node] of taby) if (!ids.has(id)) { node.remove(); taby.delete(id); }
    let plus = tabsBox.querySelector('.br-tab-plus');
    if (!plus) {
      plus = el('button', 'br-tab-plus', '+');
      plus.title = 'Nová karta (Ctrl+T)';
      plus.onclick = () => send({t: 'br', a: 'newtab'});
      tabsBox.appendChild(plus);
    }
    let pred = null;
    for (const p of pages) {
      let node = taby.get(p.id);
      if (!node) {
        node = el('div', 'br-tab');
        node.innerHTML = '<span class="br-fav"></span><span class="br-tab-t"></span>' +
          '<button class="br-tab-x" title="Zavřít kartu (Ctrl+W)">' + ico('i-close') + '</button>';
        node.addEventListener('pointerdown', (ev) => {
          if (ev.button === 1) { ev.preventDefault(); send({t: 'br', a: 'closetab', id: p.id}); }
        });
        node.addEventListener('click', (ev) => {
          if (ev.target.closest('.br-tab-x')) { ev.stopPropagation(); send({t: 'br', a: 'closetab', id: p.id}); return; }
          send({t: 'br', a: 'tab', id: p.id});
        });
        taby.set(p.id, node);
        tabsBox.insertBefore(node, plus);
      }
      // Pořadí podle serveru; prvky se přesouvají jen když nesedí.
      if (pred ? node.previousElementSibling !== pred : node !== tabsBox.firstElementChild) {
        tabsBox.insertBefore(node, pred ? pred.nextSibling : tabsBox.firstChild);
      }
      pred = node;
      const aktivni = p.id === msg.active;
      node.classList.toggle('on', aktivni);
      node.classList.toggle('nacita', aktivni && !!msg.loading);
      const t = p.title && p.title !== p.url ? p.title : (hostitel(p.url) || (p.url === 'about:blank' ? 'Nová karta' : p.url) || 'Nová karta');
      node.querySelector('.br-tab-t').textContent = t;
      node.title = (p.title || t) + (p.url ? '\n' + p.url : '');
      const fav = node.querySelector('.br-fav');
      if (p.icon && fav.dataset.src !== p.icon) {
        fav.dataset.src = p.icon;
        fav.textContent = '';
        const img = el('img');
        img.src = p.icon;
        img.alt = '';
        img.onerror = () => { fav.textContent = ''; };
        fav.appendChild(img);
      } else if (!p.icon && fav.dataset.src) {
        fav.dataset.src = '';
        fav.textContent = '';
      }
    }
  }

  function dialog(msg) {
    if (msg.zavrit) { dialogEl.hidden = true; dialogEl.textContent = ''; return; }
    dialogEl.textContent = '';
    dialogEl.hidden = false;
    const card = el('div', 'br-dialog-karta');
    card.appendChild(el('div', 'br-dialog-text', msg.zprava || ''));
    let pole = null;
    if (msg.typ === 'prompt') {
      pole = el('input', 'br-dialog-pole');
      pole.type = 'text';
      pole.value = msg.vychozi || '';
      card.appendChild(pole);
    }
    const btns = el('div', 'br-dialog-btns');
    const ok = el('button', 'btn primary', 'OK');
    ok.onclick = () => send({t: 'br', a: 'dialog', accept: true, text: pole ? pole.value : ''});
    btns.appendChild(ok);
    if (msg.typ !== 'alert') {
      const no = el('button', 'btn ghost', 'Zrušit');
      no.onclick = () => send({t: 'br', a: 'dialog', accept: false});
      btns.appendChild(no);
    }
    card.appendChild(btns);
    dialogEl.appendChild(card);
    (pole || ok).focus();
    if (pole) pole.onkeydown = (ev) => { ev.stopPropagation(); if (ev.key === 'Enter') ok.click(); if (ev.key === 'Escape') btns.lastChild.click(); };
  }

  function naZpravu(msg) {
    if (!root) return;
    if (msg.t === 'br-frame') {
      if (msg.w && msg.h) frame = {w: msg.w, h: msg.h};
      if (!stavEl.textContent.startsWith('Prázdná')) stavEl.hidden = true;
      obraz.src = 'data:image/jpeg;base64,' + msg.d;
    } else if (msg.t === 'br-info') {
      info = msg;
      ukazUrl();
      kresliKarty(msg);
      tlZpet.disabled = !msg.back;
      tlVpred.disabled = !msg.fwd;
      tlZnovu.innerHTML = msg.loading ? ico('i-close') : ico('i-refresh');
      tlZnovu.title = msg.loading ? 'Zastavit načítání (Esc)' : 'Načíst znovu (Ctrl+R)';
      titulek.textContent = msg.title && msg.title !== msg.url ? msg.title : 'Prohlížeč';
      // Prázdná karta je v Chromiu černá — vypadá to jako porucha, tak se řekne, co to je.
      const prazdna = !msg.url || msg.url === 'about:blank';
      if (prazdna) { stavEl.textContent = 'Prázdná karta — napiš nahoře adresu, nebo počkej, až tam Claude něco otevře.'; stavEl.hidden = false; }
      else if (stavEl.textContent.startsWith('Prázdná')) stavEl.hidden = true;
    } else if (msg.t === 'br-dialog') {
      dialog(msg);
    } else if (msg.t === 'br-copy') {
      if (msg.text && copyText) copyText(msg.text);
    } else if (msg.t === 'br-stav') {
      if (!msg.ok) {
        stavEl.hidden = false;
        stavEl.textContent = msg.zprava || 'Prohlížeč není k dispozici.';
      }
    }
  }

  /* ── Claude prohlížeč potřebuje ──────────────────────────────────────────── */
  /* Zavolá ho čtení, když Claude spustí nástroj Playwright. Okno vyskočí, i
     když je minimalizované nebo zavřené — jednou na sérii nástrojů, ať
     nevyskakuje při každém kliknutí, které Claude udělá. */
  function potreba() {
    const ted = Date.now();
    const dlouho = ted - posledniNastroj > POP_PAUZA;
    posledniNastroj = ted;
    if (stav === 'okno') return;
    if (stav === 'mini') {
      // Minimalizované: rozbalí se až po pauze, jinak jen zamrká lišta.
      if (dlouho) nastav('okno', true); else pill.classList.add('br-mrk');
      return;
    }
    if (dlouho || stav === 'zavreno') nastav('okno', true);
  }

  function install(io) {
    send = io.send;
    copyText = io.copy || null;
    // Obnovení po reloadu: zůstává tak, jak jsi ho nechal (okno / lišta).
    const g = nactiGeo();
    if (g.stav === 'okno' || g.stav === 'mini') {
      geo = g;
      nastav(g.stav, true);
    }
    document.addEventListener('hub-tool', (ev) => {
      if (ev.detail && /^mcp__playwright__/.test(ev.detail.name || '')) potreba();
    });
    window.addEventListener('resize', () => {
      if (!geo || !root) return;
      geo.w = Math.min(geo.w, window.innerWidth - 8);
      geo.h = Math.min(geo.h, window.innerHeight - 8);
      geo.x = Math.max(0, Math.min(geo.x, window.innerWidth - 100));
      geo.y = Math.max(0, Math.min(geo.y, window.innerHeight - 40));
      usad();
    });
    // Nové spojení hubu (reconnect) — okno si o obraz řekne znovu.
    document.addEventListener('hub-ws-open', () => { otevrene = false; if (stav !== 'zavreno') otevriSpojeni(); });
  }

  global.HubProhlizec = {
    install, naZpravu, potreba,
    otevri: () => nastav('okno'),
    get stav() { return stav; },
  };
})(window);
