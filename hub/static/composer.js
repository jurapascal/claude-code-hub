/* Bublina místo vstupního řádku.
 *
 * Terminál zůstává terminálem — jen jeho spodek, kde Claude Code kreslí svoje
 * vstupní pole, překryje HTML bublina: textové pole, přepínač modelu, slash
 * příkazy, příloha a režimy. Píše se do bubliny, odesláním se text pošle do
 * PTY, takže Claude Code dostane přesně to, co by dostal z klávesnice.
 *
 * Proč překryv a ne obyčejný blok pod terminálem: kdyby terminál zůstal celý,
 * bylo by vstupní pole vidět dvakrát — jednou Claudeovo, jednou naše.
 *
 * Bublina se sama uklidí, kdykoli dole v terminálu není klidný prompt:
 * u výběru modelu, dotazu na oprávnění nebo při listování historií by pod ní
 * zmizelo právě to, na co se má člověk podívat. Zbyde po ní úzký proužek,
 * kterým se dá vrátit zpátky.
 */
'use strict';

(function (global) {

  // Kolik spodních řádků terminálu se čte při rozhodování, jestli je dole
  // klidný prompt. Vstupní pole i s nápovědou pod ním má do šesti řádků.
  const PROBE_ROWS = 8;

  /* Jak vypadá spodek Claude Code, když jen čeká na zadání (naměřeno, ne
     odhadnuto):

         ────────────────────────────────
         ❯
         ────────────────────────────────
           ⏵⏵ bypass permissions on (shift+tab to cycle)

     Šipka ❯ sama o sobě nestačí — stejnou kreslí i vybraná položka v dialogu
     („❯ No, exit"). Rozhoduje se proto podle dvojice: řádek se šipkou a pod
     ním nápověda k režimu nebo zkratkám. */
  const PROMPT = /^\s*[│|]?\s*[❯>›]\s*$|^\s*[│|]?\s*[❯>›]\s+\S/;
  const HINT = /(shift\+tab to cycle|for shortcuts|bypass permissions on|accept edits on|plan mode on|auto-accept edits)/i;

  // Dotazy, které Claude Code kreslí místo vstupního pole. Bublina jim musí
  // uhnout, jinak se odpovídá naslepo.
  const DIALOG = /(Esc to cancel|Do you want|Would you like|Proceed\?|\(y\/n\)|\[y\/N\]|to confirm|^\s*❯?\s*\d+\.\s|↑\/↓)/i;

  // Jména, kterým rozumí `/model <jméno>` — přepne rovnou, bez procházení
  // výběru v terminálu.
  const MODELS = [
    ['Výchozí', 'default'],
    ['Opus 5', 'opus'],
    ['Sonnet 5', 'sonnet'],
    ['Haiku 4.5', 'haiku'],
    ['Fable 5', 'fable'],
  ];

  // Vestavěné příkazy Claude Code, které se nedají vyčíst ze složky skillů.
  const BUILTIN = ['/clear', '/compact', '/context', '/model', '/status',
                   '/resume', '/cost', '/help'];

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function icon(id) {
    return `<svg class="ico"><use href="#${id}"/></svg>`;
  }

  /* Text z posledních řádků toho, co je vidět. Ne z konce bufferu: když člověk
     odroluje nahoru, zajímá nás, co má před očima on, ne kde stojí Claude. */
  function visibleBottom(term, count) {
    const buf = term.buffer.active;
    const lines = [];
    for (let i = term.rows - count; i < term.rows; i++) {
      const row = buf.getLine(buf.viewportY + i);
      if (row) lines.push(row.translateToString(true));
    }
    return lines;
  }

  function install(tab, io) {
    const term = tab.term;
    const root = el('div', 'composer');
    root.innerHTML = `
      <button class="composer-peek" title="Psát v bublině">
        ${icon('i-up')}<span>Psát v bublině</span>
      </button>
      <div class="composer-box">
        <textarea class="composer-input" rows="1" spellcheck="false"
                  placeholder="Napiš, co má Claude udělat… (Enter odešle, Shift+Enter nový řádek)"></textarea>
        <div class="composer-bar">
          <button class="composer-chip" data-act="model"
                  title="Přepne model. Claude Code si volbu uloží i jako výchozí pro nové sessions."><span>Model</span> ▾</button>
          <button class="composer-chip" data-act="slash">/ příkazy</button>
          <button class="composer-chip" data-act="file">${icon('i-image')} Příloha</button>
          <button class="composer-chip" data-act="mode" title="Shift+Tab — plán / auto-accept / normální">Režim</button>
          <span class="spacer"></span>
          <button class="composer-chip ghost" data-act="esc" title="Přeruší, co Claude právě dělá (Esc)">Esc</button>
          <button class="composer-send" title="Odeslat (Enter)">${icon('i-up')}</button>
        </div>
      </div>
      <input type="file" multiple hidden>`;
    tab.pane.appendChild(root);

    const input = root.querySelector('.composer-input');
    const picker = root.querySelector('input[type=file]');
    const modelChip = root.querySelector('[data-act=model] span');
    const history = [];
    let histAt = -1;         // -1 = píše se nový text, ne historie
    let draft = '';
    let hiddenByUser = false;
    let shown = false;

    /* ── odesílání ────────────────────────────────────────────────────────── */

    function toPty(data) {
      if (tab.id) io.send({t: 'in', id: tab.id, d: data});
    }

    /* Enter se posílá zvlášť a s odstupem: slepený s textem ho Claude Code
       přečte jako nový řádek, ne jako odeslání. (Stejný důvod jako u tlačítek
       rychlých akcí.) */
    function submit(text) {
      const body = text.replace(/\r/g, '');
      if (!body.trim() || !tab.id) return;
      // Víceřádkový text musí dorazit jako vložení, jinak by se každý řádek
      // odeslal zvlášť. Jednořádkový jde rovnou — bez uvozovacích sekvencí.
      toPty(body.includes('\n') ? '\x1b[200~' + body + '\x1b[201~' : body);
      setTimeout(() => toPty('\r'), 180);
      history.push(body);
      histAt = -1;
      draft = '';
    }

    function send() {
      const text = input.value;
      if (!text.trim()) return;
      submit(text);
      input.value = '';
      autogrow();
      input.focus();
    }

    function insert(text, {focus = true} = {}) {
      const at = input.selectionStart ?? input.value.length;
      const before = input.value.slice(0, at);
      const after = input.value.slice(input.selectionEnd ?? at);
      const glue = before && !/\s$/.test(before) ? ' ' : '';
      input.value = before + glue + text + after;
      const pos = (before + glue + text).length;
      input.setSelectionRange(pos, pos);
      autogrow();
      if (focus) input.focus();
    }

    function autogrow() {
      input.style.height = 'auto';
      // Strop je pět řádků: víc už by bublina ukrajovala z výpisu nad sebou.
      input.style.height = Math.min(input.scrollHeight, 5 * 20 + 12) + 'px';
    }

    /* ── nabídky ──────────────────────────────────────────────────────────── */

    /* Nabídky se otevírají nad bublinu — pod ní je konec okna. Kotví se na
       horní hranu celé bubliny, ne na tlačítko, ať nepřekrývají text. */
    function anchor(ev) {
      const chip = ev.currentTarget.getBoundingClientRect();
      const bubble = root.querySelector('.composer-box').getBoundingClientRect();
      return [chip.left, bubble.top - 6];
    }

    function modelMenu(ev) {
      const [x, y] = anchor(ev);
      io.menu(x, y, MODELS.map(([label, key]) => ({
        icon: 'i-star',
        label,
        run: () => {
          // /model <jméno> přepne rovnou, bez procházení výběru v terminálu.
          submit('/model ' + key);
          modelChip.textContent = label;
        },
      })), {above: true});
    }

    function slashMenu(ev) {
      const [x, y] = anchor(ev);
      const own = (io.skills() || []).map(s => '/' + s).sort();
      // Vlastní skill může mít stejné jméno jako vestavěný příkaz (/status),
      // a dvakrát v nabídce by jen mátl.
      const items = [...new Set([...own, ...BUILTIN])].map(cmd => ({
        icon: cmd === '/clear' || cmd === '/compact' ? 'i-refresh' : 'i-terminal',
        label: cmd,
        run: () => insert(cmd + ' '),
      }));
      io.menu(x, y, items, {above: true});
    }

    async function attach(files) {
      const paths = await io.upload(files);
      if (paths.length) insertPaths(paths);
    }

    /* Cesta k souboru je jediné, co si Claude Code z obrázku vezme. Sem chodí
       i to, co člověk pustí nebo vloží kdekoli v tabu — hub.js se ptá bubliny
       dřív, než by cestu napsal do terminálu. */
    function insertPaths(paths) {
      if (!shown || !paths.length) return false;
      insert(paths.map(io.quote).join(' '));
      return true;
    }

    /* ── viditelnost ──────────────────────────────────────────────────────── */

    function looksIdle() {
      if (tab.exited) return false;
      const buf = term.buffer.active;
      // Odrolováno nahoru: tam bublina jen zakrývá historii.
      if (buf.viewportY < buf.baseY - 1) return false;
      const lines = visibleBottom(term, PROBE_ROWS);
      if (lines.some(l => DIALOG.test(l))) return false;
      return lines.some(l => PROMPT.test(l)) && lines.some(l => HINT.test(l));
    }

    function apply() {
      const want = !hiddenByUser && looksIdle();
      if (want === shown) return;
      const hadTerm = tab.pane.contains(document.activeElement) &&
                      document.activeElement !== input;
      shown = want;
      root.classList.toggle('on', shown);
      // Fokus musí jít za tím, do čeho se píše. Jinak by psaní s diakritikou
      // (skládané klávesy) skončilo v poli schovaném pod bublinou.
      if (shown && hadTerm) input.focus();
      if (!shown && document.activeElement === input) term.focus();
    }

    let pending = null;
    function schedule() {
      if (pending) return;
      pending = setTimeout(() => { pending = null; apply(); }, 90);
    }

    const offRender = term.onRender(schedule);
    const offScroll = term.onScroll(schedule);

    /* ── klávesy ──────────────────────────────────────────────────────────── */

    input.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter' && !ev.shiftKey) {
        ev.preventDefault();
        send();
        return;
      }
      if (ev.key === 'Escape') {
        // Esc patří Claudeovi (přeruší práci), text v bublině zůstává.
        ev.preventDefault();
        toPty('\x1b');
        return;
      }
      if (ev.key === 'Tab' && ev.shiftKey) {
        ev.preventDefault();
        toPty('\x1b[Z');
        return;
      }
      // Historie je vlastní: Claudeovu bychom listovali v poli, které není
      // vidět, a člověk by netušil, co vlastně odesílá.
      if ((ev.key === 'ArrowUp' || ev.key === 'ArrowDown') && history.length) {
        const oneLine = !input.value.includes('\n');
        if (!oneLine) return;
        ev.preventDefault();
        if (ev.key === 'ArrowUp') {
          if (histAt === -1) { draft = input.value; histAt = history.length; }
          histAt = Math.max(0, histAt - 1);
          input.value = history[histAt];
        } else {
          histAt = histAt === -1 ? -1 : histAt + 1;
          input.value = histAt >= history.length ? (histAt = -1, draft)
                                                 : history[histAt];
        }
        autogrow();
        input.setSelectionRange(input.value.length, input.value.length);
      }
    });

    input.addEventListener('input', autogrow);
    input.addEventListener('paste', (ev) => {
      const files = ev.clipboardData && ev.clipboardData.files;
      if (!files || !files.length) return;
      if (ev.clipboardData.getData && ev.clipboardData.getData('text/plain')) return;
      ev.preventDefault();
      attach(files);
    });

    /* Když je bublina vidět, psaní do terminálu by končilo v poli schovaném pod
       ní. Písmeno tedy přesměrujeme do bubliny; ovládací klávesy si terminál
       nechává, ať Ctrl+C, výběr textu a dialogy fungují dál. */
    term.attachCustomKeyEventHandler((ev) => {
      if (!shown || ev.type !== 'keydown') return true;
      if (ev.ctrlKey || ev.altKey || ev.metaKey) return true;
      if (ev.key.length !== 1) return true;
      // preventDefault je tu nutný: po přesunu fokusu by prohlížeč to samé
      // písmeno do pole zapsal ještě jednou (naměřeno — psalo se „aa").
      ev.preventDefault();
      input.focus();
      insert(ev.key, {focus: true});
      return false;
    });

    root.querySelector('.composer-send').onclick = send;
    root.querySelector('.composer-peek').onclick = () => {
      hiddenByUser = false;
      apply();
      if (shown) input.focus();
    };
    root.querySelector('[data-act=model]').onclick = modelMenu;
    root.querySelector('[data-act=slash]').onclick = slashMenu;
    root.querySelector('[data-act=file]').onclick = () => picker.click();
    root.querySelector('[data-act=mode]').onclick = () => { toPty('\x1b[Z'); input.focus(); };
    root.querySelector('[data-act=esc]').onclick = () => { toPty('\x1b'); input.focus(); };
    picker.onchange = () => {
      if (picker.files && picker.files.length) attach(picker.files);
      picker.value = '';
    };

    schedule();

    return {
      insertPaths,
      /* Rychlé akce z pravého panelu. Příkaz bez koncového \r se má jen
         napsat — třeba /screenshot čeká, až doplníš adresu. */
      run: (cmd) => {
        if (!shown) return false;
        if (cmd.endsWith('\r')) submit(cmd.slice(0, -1));
        else insert(cmd);
        return true;
      },
      focus: () => { if (shown) input.focus(); },
      visible: () => shown,
      hide: () => { hiddenByUser = true; apply(); },
      release: () => {
        offRender.dispose();
        offScroll.dispose();
        if (pending) clearTimeout(pending);
        root.remove();
      },
    };
  }

  global.HubComposer = {install};

})(window);
