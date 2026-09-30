/* Prohlížeč v appce — plovoucí okno s prohlížečem, který používá Claude.
 *
 * Claude v něm pracuje přes Playwright (vidíš, co dělá) a můžeš mu do toho
 * sáhnout: kliknout, přihlásit se, vyřešit captchu. Okno jde přesunout za
 * záhlaví, změnit mu velikost za pravý dolní roh, minimalizovat do lišty
 * „Prohlížeč" dole a zavřít. Když Claude prohlížeč zase potřebuje (spustí
 * nástroj Playwright), okno samo vyskočí — i po minimalizaci nebo zavření —
 * a stráž, že jsi ho právě posunul nebo zmenšil, se neuplatní.
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
  let kb = null;                    // skrytá textarea: bere psaní a diakritiku
  let root = null, canvas = null, ctx = null, urlInput = null, tabsBox = null, stavEl = null;
  let pill = null;
  let frame = {w: 1280, h: 800};
  let stav = 'zavreno';             // zavreno | okno | mini
  let geo = null;
  let otevrene = false;              // poslali jsme serveru {a:'open'}
  let posledniNastroj = 0;
  let info = {pages: [], active: ''};

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
  function ulozStav() { ulozGeo(); }

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
        '<button class="br-btn br-zavri" title="Zavřít">' + ico('i-close') + '</button>' +
      '</div>' +
      '<div class="br-taby"></div>' +
      '<div class="br-lista">' +
        '<button class="br-btn br-zpet" title="Zpět">' + ico('i-chevron') + '</button>' +
        '<button class="br-btn br-vpred" title="Vpřed">' + ico('i-chevron') + '</button>' +
        '<button class="br-btn br-znovu" title="Načíst znovu">' + ico('i-refresh') + '</button>' +
        '<input class="br-url" type="text" spellcheck="false" placeholder="Adresa nebo hledání">' +
      '</div>' +
      '<div class="br-scena"><canvas class="br-platno"></canvas>' +
        '<textarea class="br-kb" autocapitalize="off" autocomplete="off" autocorrect="off" spellcheck="false" tabindex="0"></textarea>' +
        '<div class="br-stav"></div></div>' +
      '<div class="br-roh" title="Změnit velikost"></div>';
    document.body.appendChild(root);
    canvas = $('.br-platno');
    ctx = canvas.getContext('2d');
    urlInput = $('.br-url');
    tabsBox = $('.br-taby');
    stavEl = $('.br-stav');
    stavEl.textContent = 'Připojuji prohlížeč…';
    // Prohlížeč je na svých stránkách „zpět" a „vpřed" — šipka se jen otáčí.
    $('.br-zpet').classList.add('br-otoc');
    $('.br-mini').onclick = () => nastav('mini');
    $('.br-zavri').onclick = () => nastav('zavreno');
    $('.br-zpet').onclick = () => send({t: 'br', a: 'back'});
    $('.br-vpred').onclick = () => send({t: 'br', a: 'forward'});
    $('.br-znovu').onclick = () => send({t: 'br', a: 'reload'});
    urlInput.addEventListener('keydown', (ev) => {
      ev.stopPropagation();
      if (ev.key === 'Enter') { send({t: 'br', a: 'go', url: urlInput.value}); kb.focus(); }
    });
    tahni($('.br-hlava'));
    velikost($('.br-roh'));
    kb = $('.br-kb');
    platno();
    root.addEventListener('pointerdown', () => { root.classList.add('br-nahore'); });
  }

  /* ── přesun a velikost ───────────────────────────────────────────────────── */
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

  /* ── plátno: myš, kolečko, klávesy ───────────────────────────────────────── */
  function platno() {
    // Plátno je `object-fit: contain` — obraz nemusí vyplnit celý prvek, takže
    // se bod počítá od skutečného rohu obrazu, ne od rohu plátna.
    const bod = (ev) => {
      const r = canvas.getBoundingClientRect();
      const k = Math.min(r.width / frame.w, r.height / frame.h) || 1;
      const ox = (r.width - frame.w * k) / 2, oy = (r.height - frame.h * k) / 2;
      return {x: (ev.clientX - r.left - ox) / k, y: (ev.clientY - r.top - oy) / k};
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
    /* Psaní bere skrytá textarea pod plátnem, ne plátno samo: na plátno
       se psát nedá a diakritiku (ě, š, č…) z metody zadávání by zahodilo.
       Obyčejná písmena ASCII jdou jako klávesy (stránky je vidí jako
       skutečné stisky), cokoli dalšího — diakritika, složené znaky, vložení —
       jde jako text. Enter, šipky, Backspace a zkratky jdou vždycky jako klávesy. */
    const foc = () => { try { kb.focus({preventScroll: true}); } catch (_) { kb.focus(); } };
    canvas.addEventListener('pointerdown', foc);
    root.querySelector('.br-scena').addEventListener('pointerup', () => { if (document.activeElement !== urlInput) foc(); });
    const ascii = (ev) => ev.key.length === 1 && ev.key.charCodeAt(0) < 128;
    kb.addEventListener('keydown', (ev) => {
      ev.stopPropagation();
      if (ev.isComposing || ev.key === 'Process' || ev.key === 'Dead') return;
      const zkratka = ev.ctrlKey || ev.metaKey || ev.altKey;
      if (ev.key.length === 1 && !ascii(ev) && !zkratka) return;      // diakritika → textarea → input
      if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === 'v') return;   // vložení → paste
      ev.preventDefault();
      send({t: 'br', a: 'key', type: 'down', key: ev.key, code: ev.code, vk: ev.keyCode, mod: mod(ev)});
    });
    kb.addEventListener('keyup', (ev) => {
      ev.stopPropagation();
      if (ev.isComposing || ev.key === 'Process' || ev.key === 'Dead') return;
      if (ev.key.length === 1 && !ascii(ev) && !(ev.ctrlKey || ev.metaKey || ev.altKey)) return;
      if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === 'v') return;
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
    send({t: 'br', a: 'open'});
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
      if (!potichu) root.classList.remove('br-zavolano');
    }
    ulozStav();
  }

  /* ── zprávy ze serveru ───────────────────────────────────────────────────── */
  const obraz = new Image();
  obraz.onload = () => {
    if (!canvas) return;
    if (canvas.width !== frame.w || canvas.height !== frame.h) {
      canvas.width = frame.w;
      canvas.height = frame.h;
    }
    ctx.drawImage(obraz, 0, 0, canvas.width, canvas.height);
  };

  function naZpravu(msg) {
    if (!root) return;
    if (msg.t === 'br-frame') {
      if (msg.w && msg.h) frame = {w: msg.w, h: msg.h};
      if (!stavEl.textContent.startsWith('Prázdná')) stavEl.hidden = true;
      obraz.src = 'data:image/jpeg;base64,' + msg.d;
    } else if (msg.t === 'br-info') {
      info = msg;
      if (document.activeElement !== urlInput) urlInput.value = msg.url === 'about:blank' ? '' : msg.url || '';
      // Prázdná karta je v Chromiu černá — vypadá to jako porucha, tak se řekne, co to je.
      const prazdna = !msg.url || msg.url === 'about:blank';
      if (prazdna) { stavEl.textContent = 'Prázdná karta — napiš nahoře adresu, nebo počkej, až tam Claude něco otevře.'; stavEl.hidden = false; }
      else if (stavEl.textContent.startsWith('Prázdná')) stavEl.hidden = true;
      tabsBox.textContent = '';
      tabsBox.hidden = (msg.pages || []).length < 2;
      for (const p of msg.pages || []) {
        const b = el('button', 'br-tab' + (p.id === msg.active ? ' on' : ''), p.title || p.url || 'Nová karta');
        b.title = p.url;
        b.onclick = () => send({t: 'br', a: 'tab', id: p.id});
        tabsBox.appendChild(b);
      }
      const plus = el('button', 'br-tab br-tab-plus', '+');
      plus.title = 'Nová karta';
      plus.onclick = () => send({t: 'br', a: 'newtab'});
      tabsBox.appendChild(plus);
      tabsBox.hidden = false;
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
