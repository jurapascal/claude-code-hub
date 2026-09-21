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
 * Claude na něco zeptá (pane.asking, viz composer.js).
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

  /* Proud bloků. Stejný pro živý tab i pro okno se starou konverzací. */
  function flow(mount) {
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
        row.appendChild(el('div', 'cteni-bubble', b.text));
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

    return {
      add(bloky) { for (const b of bloky) pridej(b); },
      prazdny() { return !mount.firstChild; },
      /* „Claude pracuje…" — poslední nástroj ještě nemá výsledek, takže se
         zrovna něco děje. Řádek je pořád tentýž, jen se přesouvá na konec. */
      cinnost(on) {
        if (!on) {
          if (prace) { prace.remove(); prace = null; }
          return;
        }
        if (!prace) prace = el('div', 'cteni-prace', 'Claude pracuje…');
        mount.appendChild(prace);
      },
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
    const term = el('button', 'cteni-term', 'Terminál');
    term.title = 'Ukázat terminál tak, jak ho kreslí Claude Code';
    const zpet = el('button', 'cteni-back', 'Čtení');
    zpet.title = 'Zpátky ke čtení';
    scroll.append(mount, prazdno);
    root.append(term, scroll);
    tab.pane.appendChild(root);
    tab.pane.appendChild(zpet);
    tab.pane.classList.add('cteni-on');

    const proud = flow(mount);
    const data = zdroj(io, () => 'id=' + encodeURIComponent(tab.id || ''));
    let timer = null, prazdnych = 0, zivy = true, ceka = false;

    // Roluje se samo, dokud je člověk u dna. Jak si odroluje nahoru číst,
    // nic mu pod rukama neuteče.
    let uDna = true;
    scroll.addEventListener('scroll', () => {
      uDna = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < U_DNA;
    });

    term.onclick = () => {
      tab.pane.classList.add('cteni-off');
      tab.term.focus();
    };
    zpet.onclick = () => tab.pane.classList.remove('cteni-off');

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
          const posledni = blocks[blocks.length - 1];
          proud.cinnost(posledni.kind === 'tool');
          if (uDna) scroll.scrollTop = scroll.scrollHeight;
        } else if (konec) {
          prazdnych++;
          if (prazdnych > 2) proud.cinnost(false);
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
        root.remove();
        zpet.remove();
        tab.pane.classList.remove('cteni-on', 'cteni-off');
      },
      // Po přepnutí na tab se doptá hned, ať čtení není o vteřinu pozadu.
      wake() { if (zivy) { clearTimeout(timer); timer = setTimeout(tik, 60); } },
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

    const scroll = q('.cteni-scroll');
    const proud = flow(q('.cteni-flow'));
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
