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
    Bash: '⏵', Grep: '⌕', Glob: '⌕', Task: '⛭', Agent: '⛭',
    WebFetch: '⇱', WebSearch: '⌕', TodoWrite: '☑', AskUserQuestion: '?',
    peer: '✉',
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

  /* ── agenti ──────────────────────────────────────────────────────────────
     Claude pustí agenta a ten pracuje sám, často na pozadí a často několik
     najednou. V terminálu je z toho jeden řádek — tady karta: kdo to je, co
     dostal za úkol, jestli ještě běží, co zrovna dělá a co odevzdal. */

  // Barva podle typu agenta, ať se paralelní agenti dají od sebe poznat.
  const AG_BARVA = {
    'general-purpose': 'var(--accent)', Explore: '#4c97f0', Plan: '#a371f7',
    'claude-code-guide': '#3fb950',
  };
  const AG_PALETA = ['#4c97f0', '#a371f7', '#3fb950', '#e0843c', '#2bb3a8', '#e05d8a'];
  const AG_STAV = {bezi: 'Běží', hotovo: 'Hotovo', chyba: 'Selhal', stop: 'Zastaven'};
  const AG_KROKU = 3;          // kolik posledních kroků je na kartě vidět

  function barvaAgenta(typ) {
    if (AG_BARVA[typ]) return AG_BARVA[typ];
    let h = 0;
    for (const c of String(typ || '')) h = (h * 31 + c.charCodeAt(0)) >>> 0;
    return AG_PALETA[h % AG_PALETA.length];
  }

  function jmenoAgenta(typ) {
    if (!typ || typ === 'general-purpose') return 'Agent';
    return typ.charAt(0).toUpperCase() + typ.slice(1);
  }

  // „claude-haiku-4-5-20251001" → „Haiku 4.5", „opus" → „Opus".
  function jmenoModelu(m) {
    const x = /(opus|sonnet|haiku|fable)(?:-(\d+)(?:-(\d{1,2})(?!\d))?)?/i.exec(m || '');
    if (!x) return '';
    return x[1].charAt(0).toUpperCase() + x[1].slice(1).toLowerCase() +
      (x[2] ? ' ' + x[2] + (x[3] ? '.' + x[3] : '') : '');
  }

  function cas(ms) {
    const s = Math.max(0, Math.round(ms / 1000));
    if (s < 60) return s + ' s';
    const m = Math.floor(s / 60);
    return m < 60 ? m + ' min ' + (s % 60) + ' s' : Math.floor(m / 60) + ' h ' + (m % 60) + ' min';
  }

  function pocet(n, jedna, dve, pet) {
    return n + ' ' + (n === 1 ? jedna : n >= 2 && n <= 4 ? dve : pet);
  }

  function tokeny(n) {
    if (!n) return '';
    const t = n >= 1000 ? (n / 1000).toFixed(n >= 100000 ? 0 : 1).replace('.', ',') + 'k' : String(n);
    return t + ' tokenů';
  }

  /* Proud bloků. Stejný pro živý tab i pro okno se starou konverzací.
     `imageUrl` převede cestu k obrázku z hub-images na adresu, ze které ho
     stránka smí načíst (/api/image). */
  function flow(mount, imageUrl, {historie = false, openLink = null} = {}) {
    /* Odkaz v Markdownu je <a> bez href (vault.js) — sám nikam nevede,
       otevřít ho musí hub. V okně appky by holý odkaz stejně nic neudělal. */
    mount.addEventListener('click', (ev) => {
      const a = ev.target.closest('a');
      if (!a || !mount.contains(a)) return;
      ev.preventDefault();
      const url = a.dataset.href || a.getAttribute('href') || '';
      if (/^(https?:|mailto:)/i.test(url) && openLink) openLink(url);
    });

    const tools = new Map();          // id nástroje → jeho řádek
    const agenti = new Map();         // id volání Agent → karta
    let prace = null;                 // řádek „Claude pracuje…"
    let skupina = null;               // karty agentů puštěných najednou
    let posun = 0;                    // o kolik jdou hodiny stránky napřed před serverem

    /* Spodek konverzace: řádek práce, agenti na pozadí a pod tím zprávy,
       které ještě nedošly — napsané, ale Claude je zatím nevidí. Drží se
       vždycky na konci, i když nad ně přibývá. */
    const fronta = el('div', 'cteni-fronta');
    const pozadi = el('button', 'cteni-pozadi');
    pozadi.hidden = true;
    const cekajici = [];              // ze serveru: {key, row}
    const mistni = [];                // odeslané odsud, v přepisu ještě nejsou

    function naKonec() {
      if (prace) mount.appendChild(prace);
      mount.appendChild(pozadi);
      mount.appendChild(fronta);
    }

    function udelejNastroj(b) {
      const box = el('div', 'cteni-tool');
      const head = el('button', 'cteni-head');
      head.appendChild(el('span', 'cteni-caret', '▸'));
      head.appendChild(el('span', 'cteni-ico', ZNAK[b.name] || '•'));
      head.appendChild(el('span', 'cteni-title', b.title || b.name || 'nástroj'));
      head.appendChild(el('span', 'cteni-bezi'));
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

    /* Bublina se zadáním. `stitek` = řádek pod ní: jestli ji Claude už vidí. */
    function bublina(b, cls, stitek) {
      const row = el('div', 'cteni-me' + (cls ? ' ' + cls : ''));
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
      if (stitek) row.appendChild(el('div', 'cteni-stitek', stitek));
      return row;
    }

    /* ── zprávy, které ještě nedošly ────────────────────────────────────────
       Co se odešle z bubliny, je vidět hned — i když Claude zrovna pracuje
       a zprávu si vezme až za chvíli. Stav pod ní říká, kde je:
         odesílá se → ve frontě (Claude ji zatím nevidí) → v konverzaci. */
    const NEDOSLO_MS = 15000;
    function norm(t) {
      return String(t || '').replace(/<\/?pasted_content\b[^>]*>/g, '')
        .replace(/\s+/g, ' ').trim().toLowerCase();
    }
    function sedi(m, b) {
      if (m.norm) return norm(b.text).includes(m.norm.slice(0, 48));
      return !!((b.images || []).length || (b.inline || []).length);
    }
    const STITEK = {
      start: 'Čeká, až Claude Code naběhne…',
      odesila: 'Odesílá se…',
      nedoslo: 'Claude Code zprávu zatím nepřevzal — mrkni do terminálu',
    };
    function nastavMistni(m, stav) {
      m.stav = stav;
      m.row.className = 'cteni-me cteni-ceka mistni ' + stav;
      m.row.querySelector('.cteni-stitek').textContent = STITEK[stav];
      clearTimeout(m.timer);
      if (stav === 'odesila') m.timer = setTimeout(() => nastavMistni(m, 'nedoslo'), NEDOSLO_MS);
    }
    function odeslano(z, stav) {
      if (!z) {                                  // zpráva ze startu právě odešla
        for (const m of mistni) if (m.stav === 'start') nastavMistni(m, 'odesila');
        return;
      }
      const m = {norm: norm(z.text), row: bublina(z, '', ' ')};
      mistni.push(m);
      fronta.appendChild(m.row);
      nastavMistni(m, stav === 'start' ? 'start' : 'odesila');
      naKonec();
    }
    function potvrd(b) {
      for (const m of mistni.slice()) {
        if (!sedi(m, b)) continue;
        clearTimeout(m.timer);
        m.row.remove();
        mistni.splice(mistni.indexOf(m), 1);
      }
    }
    function zFronty(key) {
      const i = cekajici.findIndex((c) => c.key === key);
      if (i < 0) return;
      cekajici[i].row.remove();
      cekajici.splice(i, 1);
    }

    /* ── karty agentů ───────────────────────────────────────────────────── */
    function udelejAgenta(b) {
      const k = {id: b.id, typ: b.type, stav: 'bezi', bg: !!b.bg, model: b.model || '',
                 start: Date.parse(b.ts || '') || 0, ms: 0, tools: 0, tokens: 0,
                 kroky: [], prompt: b.prompt || '', vysledek: '', souhrn: '',
                 konecOd: 0, otevreno: ''};
      const box = el('div', 'ag bezi');
      box.style.setProperty('--ag', barvaAgenta(b.type));
      box.innerHTML = `
        <div class="ag-top">
          <span class="ag-avatar"><span class="ag-pismeno"></span></span>
          <span class="ag-jmeno">
            <span class="ag-radek"><b class="ag-typ"></b><span class="ag-model"></span><span class="ag-bg">na pozadí</span></span>
            <span class="ag-ukol"></span>
          </span>
          <span class="ag-stav"><i></i><span class="ag-stav-t"></span><span class="ag-cas"></span></span>
        </div>
        <ol class="ag-kroky"></ol>
        <div class="ag-nahled vault-md" hidden></div>
        <div class="ag-pata">
          <span class="ag-cisla"></span>
          <button class="ag-btn" data-v="prompt">Zadání</button>
          <button class="ag-btn" data-v="vysledek" hidden>Výsledek</button>
        </div>
        <div class="ag-telo" hidden></div>`;
      const q = (sel) => box.querySelector(sel);
      q('.ag-pismeno').textContent = jmenoAgenta(b.type).charAt(0);
      q('.ag-typ').textContent = jmenoAgenta(b.type);
      q('.ag-ukol').textContent = b.title || 'bez popisu';
      const telo = q('.ag-telo');
      for (const btn of box.querySelectorAll('.ag-btn')) {
        btn.onclick = () => {
          const co = btn.dataset.v;
          k.otevreno = k.otevreno === co ? '' : co;
          for (const x of box.querySelectorAll('.ag-btn')) x.classList.toggle('on', x.dataset.v === k.otevreno);
          telo.hidden = !k.otevreno;
          telo.className = 'ag-telo' + (co === 'vysledek' ? ' vault-md' : '');
          if (co === 'prompt') telo.textContent = k.prompt || '(bez zadání)';
          else {
            const html = markdown(k.vysledek);
            if (html) telo.innerHTML = html;
            else telo.textContent = k.vysledek;
          }
          // Náhled výsledku je jen upoutávka — s rozbaleným celým by byl dvakrát.
          q('.ag-nahled').hidden = k.otevreno === 'vysledek' || !k.vysledek;
        };
      }
      k.box = box;
      k.q = q;
      return k;
    }

    function kresliAgenta(k) {
      const {box, q} = k;
      for (const s of Object.keys(AG_STAV)) box.classList.toggle(s, k.stav === s);
      q('.ag-stav-t').textContent = AG_STAV[k.stav];
      q('.ag-model').textContent = jmenoModelu(k.model);
      q('.ag-bg').hidden = !k.bg || k.stav !== 'bezi';
      const kroky = q('.ag-kroky');
      kroky.hidden = k.stav !== 'bezi';
      if (k.stav === 'bezi') {
        const sig = k.kroky.map((x) => x.title).join('\n');
        if (sig !== kroky.dataset.sig) {
          const stare = kroky.dataset.sig ? kroky.dataset.sig.split('\n') : [];
          kroky.dataset.sig = sig;
          kroky.textContent = '';
          const ukazat = k.kroky.slice(-AG_KROKU);
          if (!ukazat.length) kroky.appendChild(el('li', 'ag-krok prazdny', 'Rozjíždí se…'));
          ukazat.forEach((x, i) => {
            const li = el('li', 'ag-krok');
            li.append(el('span', 'ag-krok-ico', ZNAK[x.name] || '•'), el('span', 'ag-krok-t', x.title));
            if (i === ukazat.length - 1) li.classList.add('ted');
            if (!stare.includes(x.title)) li.classList.add('nove');
            kroky.appendChild(li);
          });
        }
      }
      const nahled = q('.ag-nahled');
      if (k.vysledek && nahled.dataset.src !== k.vysledek) {
        nahled.dataset.src = k.vysledek;
        const html = markdown(k.vysledek);
        if (html) nahled.innerHTML = html;
        else nahled.textContent = k.vysledek;
      }
      nahled.hidden = !k.vysledek || k.otevreno === 'vysledek';
      q('[data-v=vysledek]').hidden = !k.vysledek;
      const cisla = [];
      if (k.tools) cisla.push(pocet(k.tools, 'nástroj', 'nástroje', 'nástrojů'));
      if (k.tokens) cisla.push(tokeny(k.tokens));
      if (k.stav !== 'bezi' && k.ms) cisla.push(cas(k.ms));
      if (k.stav === 'chyba' && k.souhrn && !k.vysledek) cisla.push(k.souhrn);
      q('.ag-cisla').textContent = cisla.join(' · ');
      tikAgenta(k);
      if (k.skupina) hlavaSkupiny(k.skupina);
    }

    function tikAgenta(k) {
      const t = k.q('.ag-cas');
      if (k.stav === 'bezi') t.textContent = k.start ? cas(Date.now() - posun - k.start) : '';
      else t.textContent = '';
    }

    function hlavaSkupiny(g) {
      const karty = g.karty;
      g.box.classList.toggle('vic', karty.length > 1);
      if (karty.length < 2) return;
      const bezi = karty.filter((k) => k.stav === 'bezi').length;
      g.pocet.textContent = pocet(karty.length, 'agent', 'agenti', 'agentů') + ' najednou';
      const kus = [];
      if (bezi) kus.push(bezi + ' běží');
      if (karty.length - bezi) kus.push((karty.length - bezi) + ' hotovo');
      g.stav.textContent = kus.join(' · ');
    }

    function pridejAgenta(b) {
      if (agenti.has(b.id)) return;
      const k = udelejAgenta(b);
      // Agenti puštění jednou zprávou stojí pohromadě, jako strom.
      if (!skupina || mount.lastElementChild !== skupina.box) {
        const box = el('div', 'ag-skupina');
        const hlava = el('div', 'ag-hlava');
        const pocetEl = el('b', 'ag-hlava-n');
        const stavEl = el('span', 'ag-hlava-stav');
        hlava.append(el('span', 'ag-hlava-ico', '⛬'), pocetEl, stavEl);
        const strom = el('div', 'ag-strom');
        box.append(hlava, strom);
        skupina = {box, strom, karty: [], pocet: pocetEl, stav: stavEl};
        mount.appendChild(box);
      }
      skupina.karty.push(k);
      skupina.strom.appendChild(k.box);
      k.skupina = skupina;
      agenti.set(b.id, k);
      kresliAgenta(k);
      hlidej();
    }

    function doplnAgenta(k, info) {
      if (!info) return;
      if (info.model) k.model = info.model;
      if (typeof info.tools === 'number' && info.tools > k.tools) k.tools = info.tools;
      if (info.tokens) k.tokens = info.tokens;
      if (info.ms) k.ms = info.ms;
    }

    /* Hotovo podle hlášky o doběhlé úloze (`task`) nebo podle výsledku. */
    function dobehl(k, stav, vysledek) {
      k.stav = stav;
      if (vysledek) k.vysledek = vysledek;
      if (!k.ms && k.start && k.posledni) k.ms = Math.max(0, k.posledni - k.start);
    }

    /* Průběh z přepisů agentů (/api/cteni-agenti). */
    function prubeh(res) {
      if (!res) return;
      if (res.now) posun = Date.now() - res.now;
      for (const a of res.agents || []) {
        const k = agenti.get(a.id);
        if (!k) continue;
        if (a.start) k.start = a.start;
        if (a.update) k.posledni = a.update;
        if (a.tools > k.tools) k.tools = a.tools;
        if (a.tokens && (k.stav === 'bezi' || !k.tokens)) k.tokens = a.tokens;
        if (a.model) k.model = a.model;       // čím agent doopravdy jede
        if (Array.isArray(a.last)) k.kroky = a.last;
        /* Agent skončil, ale hláška o tom ještě nedorazila (nebo v přepisu
           staré konverzace chybí): po chvíli se bere jako hotový. */
        if (k.stav === 'bezi' && a.konec) {
          k.konecOd = k.konecOd || Date.now();
          if (historie || Date.now() - k.konecOd > 8000) dobehl(k, 'hotovo', '');
        } else if (!a.konec) {
          k.konecOd = 0;
        }
        kresliAgenta(k);
      }
      hlidej();
    }

    /* Hodiny u běžících agentů a proužek „pracují na pozadí". */
    let tikani = null;
    function bezici() {
      return [...agenti.values()].filter((k) => k.stav === 'bezi');
    }
    function hlidej() {
      const bezi = bezici();
      pozadi.hidden = !bezi.length || historie;
      if (bezi.length) {
        const jmena = bezi.map((k) => jmenoAgenta(k.typ));
        const prvni = jmena[0] + (jmena.length > 1 ? ' a ' + pocet(jmena.length - 1, 'další', 'další', 'dalších') : '');
        pozadi.textContent = '';
        pozadi.append(el('span', 'cteni-pozadi-tecka'),
                      el('span', '', prvni + (jmena.length > 1 ? ' pracují' : ' pracuje') + ' — ukázat'));
        pozadi.onclick = () => bezi[0].box.scrollIntoView({behavior: 'smooth', block: 'center'});
      }
      if (bezi.length && !tikani) {
        tikani = setInterval(() => {
          const ted = bezici();
          for (const k of ted) tikAgenta(k);
          if (!ted.length) { clearInterval(tikani); tikani = null; }
        }, 1000);
      }
    }

    function pridej(b) {
      if (b.kind === 'me') {
        if (b.key) zFronty(b.key);           // z fronty rovnou do konverzace
        potvrd(b);
        mount.appendChild(bublina(b, b.mid ? 'mid' : '',
          b.mid ? '✓ Claude si to přečetl během práce' : ''));
        return;
      }
      if (b.kind === 'queued') {
        potvrd(b);
        const row = bublina(b, 'cteni-ceka fronta', 'Ve frontě — Claude to uvidí, až dokončí krok');
        cekajici.push({key: b.key, row});
        fronta.insertBefore(row, fronta.querySelector('.mistni'));
        return;
      }
      if (b.kind === 'unqueue') {
        if (b.all) {
          for (const c of cekajici) c.row.remove();
          cekajici.length = 0;
        } else {
          zFronty(b.key);
        }
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
        box.classList.add('ceka');
        if (b.id) tools.set(b.id, box);
        mount.appendChild(box);
        return;
      }
      if (b.kind === 'peer') {
        const box = udelejNastroj({name: 'peer', title: 'zpráva od ' + (b.from || 'jiné session'),
                                   detail: b.text});
        box.classList.add('peer');
        mount.appendChild(box);
        return;
      }
      if (b.kind === 'agent') {
        pridejAgenta(b);
        return;
      }
      if (b.kind === 'task') {
        const k = b.id && agenti.get(b.id);
        if (k) {
          doplnAgenta(k, b);
          const stav = b.status === 'completed' ? 'hotovo'
            : b.status === 'failed' ? 'chyba'
            : /kill|stop|cancel/i.test(b.status || '') ? 'stop' : k.stav;
          k.souhrn = b.summary || '';
          dobehl(k, stav, b.result || '');
          kresliAgenta(k);
          hlidej();
          return;
        }
        // Příkaz na pozadí (Bash) — k jeho řádku se připíše, jak dopadl.
        const box = b.id && tools.get(b.id);
        if (box && !box.classList.contains('dobehl')) {
          box.classList.add('dobehl');
          box.classList.toggle('chyba', b.status === 'failed');
          box._meta.textContent = [box._meta.textContent,
            b.status === 'completed' ? 'doběhlo' : b.status === 'failed' ? 'selhalo' : b.status]
            .filter(Boolean).join(' · ');
          if (b.summary) box._detail.textContent += '\n\n' + b.summary;
        }
        return;
      }
      if (b.kind === 'res') {
        const k = b.id && agenti.get(b.id);
        if (k) {
          doplnAgenta(k, b.agent);
          if (b.agent && b.agent.async) {
            k.bg = true;                      // běží dál na pozadí
          } else if (k.stav === 'bezi') {
            dobehl(k, b.ok ? 'hotovo' : 'chyba', b.detail || '');
          }
          kresliAgenta(k);
          hlidej();
          return;
        }
        // Výsledek patří k nástroji, u kterého se schovává. Když jeho řádek
        // není (načetl se jen konec přepisu), nekreslí se nic — samotný
        // výpis bez toho, co ho vyvolalo, neříká nic.
        const box = b.id && tools.get(b.id);
        if (!box) return;
        box.classList.remove('ceka');
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
    let odKdy = 0, tikaniPrace = null;
    function stav(st) {
      mount.classList.toggle('pracuje', !!(st && st.on));
      if (!st || !st.on) {
        if (prace) { prace.remove(); prace = null; }
        clearInterval(tikaniPrace);
        tikaniPrace = null;
        odKdy = 0;
        return;
      }
      if (!prace) {
        prace = el('div', 'cteni-prace');
        prace.append(el('span', 'cteni-spin', '✻'), el('span', 'cteni-slovo'),
                     el('span', 'cteni-cas'), el('span', 'cteni-tok'));
      }
      naKonec();                            // vždycky na konci konverzace
      prace.querySelector('.cteni-slovo').textContent = (st.sloveso || 'Pracuje') + '…';
      if (st.sekund !== null && st.sekund !== undefined) odKdy = Date.now() - st.sekund * 1000;
      else if (!odKdy) odKdy = Date.now();
      prace.querySelector('.cteni-tok').textContent = st.tokeny ? '↓ ' + st.tokeny + ' tokenů' : '';
      const napis = () => {
        if (prace) prace.querySelector('.cteni-cas').textContent = cas(Date.now() - odKdy);
      };
      napis();
      if (!tikaniPrace) tikaniPrace = setInterval(napis, 1000);
    }

    /* Hláška od hubu, ne z přepisu: Claude v tabu spadl nebo skončil. */
    function upozorni(text) {
      const box = el('div', 'cteni-pozor');
      box.append(el('span', 'cteni-pozor-ico', '⚠'), el('span', '', text));
      mount.appendChild(box);
      for (const m of mistni) nastavMistni(m, 'nedoslo');
      naKonec();
    }

    naKonec();
    return {
      upozorni,
      add(bloky) {
        for (const b of bloky) pridej(b);
        naKonec();                          // nové bloky nad řádek práce a frontu
      },
      prazdny() {
        return !mount.querySelector(':scope > :not(.cteni-prace):not(.cteni-pozadi):not(.cteni-fronta):not(.cteni-pozor)') &&
               !fronta.firstChild;
      },
      stav,
      odeslano,
      prubeh,
      agentu: () => agenti.size,
      bezi: () => bezici().length,
      zavri() { clearInterval(tikani); clearInterval(tikaniPrace); for (const m of mistni) clearTimeout(m.timer); },
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

    const proud = flow(mount, io.imageUrl, {openLink: io.openLink});
    const data = zdroj(io, () => 'id=' + encodeURIComponent(tab.id || ''));
    let timer = null, prazdnych = 0, zivy = true, ceka = false;

    /* Agenti si píšou vlastní přepisy — dokud některý běží, doptává se
       čtení i na ně (co zrovna dělá, kolik nástrojů, kolik tokenů). */
    let agTimer = null, agCeka = false;
    function hlidejAgenty(hned) {
      if (!zivy || agCeka || (!hned && agTimer)) return;
      if (!proud.bezi() && !hned) return;
      clearTimeout(agTimer);
      agTimer = setTimeout(async () => {
        agTimer = null;
        if (!zivy || !tab.id) return;
        agCeka = true;
        try {
          proud.prubeh(await io.api('cteni-agenti?id=' + encodeURIComponent(tab.id)));
        } catch (_) { /* příště */ }
        agCeka = false;
        if (proud.bezi()) hlidejAgenty();
      }, hned ? 50 : (!io.aktivni || io.aktivni()) ? 2000 : 6000);
    }

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
          const agentu = proud.agentu();
          proud.add(blocks);
          prazdno.hidden = true;
          prazdnych = 0;
          if (uDna) scroll.scrollTop = scroll.scrollHeight;
          // Nový agent: hned zjistit, co dělá, ne až za dvě vteřiny.
          if (proud.agentu() !== agentu) hlidejAgenty(true);
          else hlidejAgenty();
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
        clearTimeout(agTimer);
        proud.stav(null);
        proud.zavri();
        root.remove();
        tab.pane.classList.remove('cteni-on');
      },
      // Po přepnutí na tab se doptá hned, ať čtení není o vteřinu pozadu.
      wake() { if (zivy) { clearTimeout(timer); timer = setTimeout(tik, 60); } },
      /* Zpráva odešla z bubliny (composer.js). Ve čtení je vidět hned, ještě
         než ji Claude Code zapíše — a pod ní, jestli už ji Claude vidí.
         `z` = null: zprávy čekající na start Claude Code právě odešly. */
      odeslano(z, stav) {
        if (!zivy) return;
        proud.odeslano(z, stav);
        prazdno.hidden = true;
        prazdnych = 0;
        scroll.scrollTop = scroll.scrollHeight;
        uDna = true;
        clearTimeout(timer);
        timer = setTimeout(tik, 60);
      },
      upozorni(text) {
        if (!zivy) return;
        proud.upozorni(text);
        prazdno.hidden = true;
        scroll.scrollTop = scroll.scrollHeight;
      },
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
                  autocomplete="off" autocorrect="on" autocapitalize="sentences" aria-autocomplete="none" data-form-type="other" data-1p-ignore data-lpignore="true"
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
      if (proud) proud.zavri();
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
    const proud = flow(q('.cteni-flow'), io.imageUrl, {historie: true, openLink: io.openLink});
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
      // Karty agentů doplní, kolik toho udělali (z jejich vlastních přepisů).
      if (zivy && proud.agentu()) {
        try {
          proud.prubeh(await io.api('cteni-agenti?chat=' + encodeURIComponent(chat.id)));
        } catch (_) { /* karty zůstanou bez čísel */ }
      }
    })();
    return {close: zavrit};
  }

  global.HubCteni = {install, open};

})(typeof window !== 'undefined' ? window : globalThis);
