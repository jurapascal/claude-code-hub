/* Čtení — konverzace místo terminálu.
 *
 * V tabu pořád běží skutečný terminál a Claude Code v něm kreslí svoje okno;
 * tahle vrstva ho překrývá a ukazuje totéž jako text: co jsem napsal, co
 * Claude odepsal a co za tím spustil. Nástroj je jeden řádek („✎ upravil
 * hub.css"), který se klepnutím rozbalí — číst se má text, ne výpisy.
 *
 * Proč překryv a ne místo terminálu: xterm potřebuje mít pořád svoji velikost,
 * jinak by Claude Code psal do jinak širokého okna, než je vidět. Terminál se
 * proto nezmenšuje, jen se přes něj položí tahle vrstva — a uhne, jakmile se
 * Claude na něco zeptá (pane.asking, viz composer.js). Jiný přepínač není.
 *
 * Bloky chodí ze serveru (hub/cteni.py) po kouscích od bajtu, na kterém se
 * minule skončilo, takže při práci přibývají jen nové.
 */
'use strict';

(function (global) {

  const POLL = 1200;          // jak často se ptát, když se něco děje
  const POLL_KLID = 4000;     // a jak, když se dlouho nic nezměnilo
  const KLID_PO = 6;          // po kolika prázdných dotazech zpomalit
  const U_DNA = 90;           // px od spodku, dokud se ještě roluje samo

  const ZNAK = {
    Read: '◉', Edit: '✎', Write: '✎', NotebookEdit: '✎',
    Bash: '⏵', Grep: '⌕', Glob: '⌕', Task: '⛭',
    WebFetch: '⇱', WebSearch: '⌕', TodoWrite: '☑', AskUserQuestion: '?',
  };

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  /* Markdown umí už prohlížeč poznámek — Claudeův text je tentýž Markdown,
     tak se kreslí stejně (a stejně se i escapuje). Bez vault.js zbyde holý
     text, což je pořád čitelné. */
  function markdown(text) {
    if (global.HubVault && global.HubVault.render) {
      try {
        return global.HubVault.render(text, {resolve: () => '', image: () => ''}).html;
      } catch (_) { /* radši holý text než prázdno */ }
    }
    return '';
  }

  /* Obrázek přes celé okno — klepnutí nebo Esc ho zavře. */
  function zvetsit(src) {
    const box = el('div', 'cteni-lupa');
    const img = el('img');
    img.src = src;
    img.alt = '';
    box.appendChild(img);
    const zavrit = () => { box.remove(); document.removeEventListener('keydown', naKlavesu, true); };
    function naKlavesu(ev) { if (ev.key === 'Escape') { ev.stopPropagation(); zavrit(); } }
    box.onclick = zavrit;
    document.addEventListener('keydown', naKlavesu, true);
    document.body.appendChild(box);
  }

  /* Proud bloků. Stejný pro živý tab i pro okno se starou konverzací.
     `imageUrl` převede cestu k obrázku z hub-images na adresu, ze které ho
     stránka smí načíst (/api/image). */
  function flow(mount, imageUrl) {
    const tools = new Map();          // id nástroje → jeho řádek
    let prace = null;                 // řádek „Claude pracuje…"

    function udelejNastroj(b) {
      const box = el('div', 'cteni-tool');
      const head = el('button', 'cteni-head');
      head.appendChild(el('span', 'cteni-caret', '▸'));
      head.appendChild(el('span', 'cteni-ico', ZNAK[b.name] || '•'));
      head.appendChild(el('span', 'cteni-title', b.title || b.name || 'nástroj'));
      const meta = el('span', 'cteni-meta', b.meta || '');
      head.appendChild(meta);
      const detail = el('pre', 'cteni-detail');
      detail.hidden = true;
      detail.textContent = b.detail || '';
      head.onclick = () => {
        detail.hidden = !detail.hidden;
        box.classList.toggle('open', !detail.hidden);
      };
      box.append(head, detail);
      box.dataset.id = b.id || '';
      box._meta = meta;
      box._detail = detail;
      return box;
    }

    function pridej(b) {
      if (b.kind === 'me') {
        const row = el('div', 'cteni-me');
        const bubble = el('div', 'cteni-bubble');
        /* Přiložený obrázek se ukáže jako obrázek, ne jako cesta k souboru —
           tu hub Claudovi napsal jen proto, aby si ho uměl otevřít. */
        const srcs = (b.images || []).map((p) => (imageUrl ? imageUrl(p) : ''))
          .filter(Boolean).concat(b.inline || []);
        if (srcs.length) {
          const imgs = el('div', 'cteni-imgs');
          for (const src of srcs) {
            const img = el('img', 'cteni-img');
            img.src = src;
            img.alt = 'obrázek';
            img.loading = 'lazy';
            img.onclick = () => zvetsit(src);
            imgs.appendChild(img);
          }
          bubble.appendChild(imgs);
        }
        if (b.text) bubble.appendChild(el('div', 'cteni-text', b.text));
        row.appendChild(bubble);
        mount.appendChild(row);
        return;
      }
      if (b.kind === 'say') {
        const box = el('div', 'cteni-say vault-md');
        const html = markdown(b.text);
        if (html) box.innerHTML = html;
        else box.textContent = b.text;
        mount.appendChild(box);
        return;
      }
      if (b.kind === 'tool') {
        const box = udelejNastroj(b);
        if (b.id) tools.set(b.id, box);
        mount.appendChild(box);
        return;
      }
      if (b.kind === 'res') {
        // Výsledek patří k nástroji, u kterého se schovává. Když jeho řádek
        // není (načetl se jen konec přepisu), nekreslí se nic — samotný
        // výpis bez toho, co ho vyvolalo, neříká nic.
        const box = b.id && tools.get(b.id);
        if (!box) return;
        box.classList.toggle('chyba', !b.ok);
        const kus = [];
        if (box._meta.textContent) kus.push(box._meta.textContent);
        if (!b.ok) kus.push('chyba');
        else if (b.radku) kus.push(b.radku + ' ř.');
        box._meta.textContent = kus.join(' · ');
        if (b.detail) {
          box._detail.textContent = (box._detail.textContent
            ? box._detail.textContent + '\n\n' : '') + b.detail;
        }
      }
    }

    /* Řádek „Claude pracuje": točící se hvězdička, co zrovna dělá (slovo si
       vybírá Claude Code sám), jak dlouho a kolik napsal. Stav čte bublina
       z terminálu (composer.js → io.prace); čas běží i mezi překresleními,
       ať se neposouvá po skocích. */
    let odKdy = 0, tikani = null;
    function cas(ms) {
      const s = Math.max(0, Math.round(ms / 1000));
      return s < 60 ? s + ' s' : Math.floor(s / 60) + ' min ' + (s % 60) + ' s';
    }
    function stav(st) {
      if (!st || !st.on) {
        if (prace) { prace.remove(); prace = null; }
        clearInterval(tikani);
        tikani = null;
        odKdy = 0;
        return;
      }
      if (!prace) {
        prace = el('div', 'cteni-prace');
        prace.append(el('span', 'cteni-spin', '✻'), el('span', 'cteni-slovo'),
                     el('span', 'cteni-cas'), el('span', 'cteni-tok'));
      }
      mount.appendChild(prace);            // vždycky na konci konverzace
      prace.querySelector('.cteni-slovo').textContent = (st.sloveso || 'Pracuje') + '…';
      if (st.sekund !== null && st.sekund !== undefined) odKdy = Date.now() - st.sekund * 1000;
      else if (!odKdy) odKdy = Date.now();
      prace.querySelector('.cteni-tok').textContent = st.tokeny ? '↓ ' + st.tokeny + ' tokenů' : '';
      const napis = () => {
        if (prace) prace.querySelector('.cteni-cas').textContent = cas(Date.now() - odKdy);
      };
      napis();
      if (!tikani) tikani = setInterval(napis, 1000);
    }

    return {
      add(bloky) {
        for (const b of bloky) pridej(b);
        if (prace) mount.appendChild(prace);   // nové bloky nad řádek práce
      },
      prazdny() { return !mount.querySelector(':scope > :not(.cteni-prace)'); },
      stav,
    };
  }

  /* Připojení na server: od kterého bajtu dál a co z toho přišlo. */
  function zdroj(io, dotaz) {
    let odkud = 0;
    let hotovo = false;
    return {
      async dalsi() {
        const url = 'cteni?' + (typeof dotaz === 'function' ? dotaz() : dotaz) +
                    '&from=' + odkud;
        const res = await io.api(url);
        if (!res || !res.ready) return {blocks: [], konec: true};
        const posun = res.next !== odkud;
        odkud = res.next;
        hotovo = !posun;
        return {blocks: res.blocks || [], konec: !posun};
      },
      get odkud() { return odkud; },
      get hotovo() { return hotovo; },
    };
  }

  /* ── vrstva v tabu ──────────────────────────────────────────────────────── */
  function install(tab, io) {
    const root = el('div', 'cteni');
    const scroll = el('div', 'cteni-scroll');
    const mount = el('div', 'cteni-flow');
    const prazdno = el('div', 'cteni-empty', 'Zatím nic — napiš Claudovi dole.');
    // Přepínač na terminál tu není: čtení je výchozí a terminál se ukáže sám,
    // když se Claude na něco zeptá (karta dotazu má i vlastní „Terminál").
    scroll.append(mount, prazdno);
    root.append(scroll);
    tab.pane.appendChild(root);
    tab.pane.classList.add('cteni-on');

    const proud = flow(mount, io.imageUrl);
    const data = zdroj(io, () => 'id=' + encodeURIComponent(tab.id || ''));
    let timer = null, prazdnych = 0, zivy = true, ceka = false;

    // Roluje se samo, dokud je člověk u dna. Jak si odroluje nahoru číst,
    // nic mu pod rukama neuteče.
    let uDna = true;
    scroll.addEventListener('scroll', () => {
      uDna = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < U_DNA;
    });

    async function tik() {
      if (ceka || !zivy) return;
      ceka = true;
      try {
        const {blocks, konec} = await data.dalsi();
        if (!zivy) return;
        if (blocks.length) {
          proud.add(blocks);
          prazdno.hidden = true;
          prazdnych = 0;
          if (uDna) scroll.scrollTop = scroll.scrollHeight;
        } else if (konec) {
          prazdnych++;
          prazdno.hidden = !proud.prazdny();
        }
      } catch (_) {
        prazdnych++;                 // server chvilku nemohl — zkusí se dál
      } finally {
        ceka = false;
        if (zivy) {
          clearTimeout(timer);
          // Tab, na který není vidět, se doptává zvolna — přibýt v něm může
          // hodně, ale nikdo to zrovna nečte.
          const rychle = prazdnych < KLID_PO && (!io.aktivni || io.aktivni());
          timer = setTimeout(tik, rychle ? POLL : POLL_KLID);
        }
      }
    }

    // Tab bez id ještě nemá session na serveru — počká se, až přijde.
    function start() {
      if (!zivy) return;
      if (!tab.id) { setTimeout(start, 400); return; }   // session ještě nevznikla
      tik();
    }
    start();

    return {
      release() {
        zivy = false;
        clearTimeout(timer);
        proud.stav(null);
        root.remove();
        tab.pane.classList.remove('cteni-on');
      },
      // Po přepnutí na tab se doptá hned, ať čtení není o vteřinu pozadu.
      wake() { if (zivy) { clearTimeout(timer); timer = setTimeout(tik, 60); } },
      /* Claude začal nebo přestal pracovat (composer.js čte terminál). Když
         pracuje, doptává se čtení rychle, ať text přibývá, jak ho píše. */
      prace(st) {
        if (!zivy) return;
        proud.stav(st);
        if (st && st.on) {
          prazdno.hidden = true;
          prazdnych = 0;
        }
        if (uDna) scroll.scrollTop = scroll.scrollHeight;
      },
    };
  }

  /* ── okno se starou konverzací ──────────────────────────────────────────── */
  function open(io, chat) {
    const box = el('div', 'onb set-modal cteni-modal');
    box.innerHTML = `
      <div class="onb-box">
        <div class="onb-head">
          <span class="onb-mark"><svg class="ico" width="26" height="26"><use href="#i-note"/></svg></span>
          <div>
            <div class="onb-title"></div>
            <div class="onb-sub"></div>
          </div>
          <span class="spacer"></span>
          <button class="btn primary cteni-go">Pokračovat</button>
          <button class="set-x cteni-close" title="Zavřít (Esc)">×</button>
        </div>
        <div class="onb-body">
          <div class="cteni-scroll"><div class="cteni-flow"></div></div>
          <!-- Číst a nemoct odepsat je půlka věci: co se tu napíše, dostane
               Claude jako první zprávu, jakmile se konverzace otevře. -->
          <div class="cteni-write">
            <textarea class="cteni-input" rows="1" spellcheck="false"
                      placeholder="Napiš a Claude v konverzaci pokračuje… (Enter odešle)"></textarea>
            <button class="cteni-send" title="Odeslat (Enter)">↑</button>
          </div>
        </div>
      </div>`;
    document.body.appendChild(box);
    const q = (sel) => box.querySelector(sel);
    q('.onb-title').textContent = chat.title || 'Konverzace';
    q('.onb-sub').textContent = [chat.project, chat.when].filter(Boolean).join('  ·  ');

    let zivy = true;
    const zavrit = () => {
      zivy = false;
      box.remove();
      document.removeEventListener('keydown', naKlavesu, true);
    };
    function naKlavesu(ev) { if (ev.key === 'Escape') { ev.stopPropagation(); zavrit(); } }
    document.addEventListener('keydown', naKlavesu, true);
    q('.cteni-close').onclick = zavrit;
    box.addEventListener('click', (ev) => { if (ev.target === box) zavrit(); });
    q('.cteni-go').onclick = () => { zavrit(); io.resume(chat); };

    /* Napsat a pokračovat je jedno gesto: okno se zavře, konverzace se otevře
       v tabu a Claude na napsané začne dělat hned. */
    const pole = q('.cteni-input');
    // Na telefonu se dlouhá výzva zalomí do dvou řádků a spodek se ořízne.
    if (window.matchMedia('(max-width: 520px)').matches) {
      pole.placeholder = 'Napiš zprávu…';
    }
    const odeslat = () => {
      const text = pole.value.trim();
      if (!text) return pole.focus();
      zavrit();
      io.resume(chat, text);
    };
    q('.cteni-send').onclick = odeslat;
    pole.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing) {
        ev.preventDefault();
        odeslat();
      }
    });
    // Pole roste s textem, ale jen do výšky, po které je ještě vidět konverzace.
    pole.addEventListener('input', () => {
      pole.style.height = 'auto';
      pole.style.height = Math.min(pole.scrollHeight, 160) + 'px';
    });
    setTimeout(() => pole.focus(), 0);

    const scroll = q('.cteni-scroll');
    const proud = flow(q('.cteni-flow'), io.imageUrl);
    const data = zdroj(io, 'chat=' + encodeURIComponent(chat.id));

    (async () => {
      // Načte se celý zbytek přepisu, ne jen první kus — stará konverzace se
      // nedočítá sama, tak ať je po otevření celá.
      for (let i = 0; i < 40 && zivy; i++) {
        let res;
        try {
          res = await data.dalsi();
        } catch (err) {
          io.notice('Konverzaci se nepodařilo načíst: ' + err.message);
          return;
        }
        if (!zivy) return;
        proud.add(res.blocks);
        if (res.konec) break;
      }
      if (zivy) scroll.scrollTop = scroll.scrollHeight;
    })();
    return {close: zavrit};
  }

  global.HubCteni = {install, open};

})(typeof window !== 'undefined' ? window : globalThis);
