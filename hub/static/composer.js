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
  const HINT = /(shift\+tab to cycle|for shortcuts|bypass permissions on|accept edits on|plan mode on|auto-accept edits|manual mode on)/i;

  // Dotazy, které Claude Code kreslí místo vstupního pole. Bublina jim musí
  // uhnout, jinak se odpovídá naslepo.
  const DIALOG = /(Esc to cancel|Do you want|Would you like|Proceed\?|\(y\/n\)|\[y\/N\]|to confirm|^\s*❯?\s*\d+\.\s|↑\/↓)/i;

  /* Číslovaná volba v dialogu: „❯ 1. Yes" — i s okrajem rámečku po stranách.
     Podle ní se dialog přenese do tlačítek, aby se dalo odpovídat myší a ne
     hledáním v terminálu. */
  const OPTION = /^[\s│|]*(❯|>)?\s*(\d+)[.)]\s+(\S.*?)[\s│|]*$/;

  // Výběr ze seznamu (historie promptů, volba modelu): ovládá se šipkami.
  const PICKER = /(↑\/↓|to navigate)/i;
  const YESNO = /\(y\/n\)|\[y\/N\]/i;

  /* Dialog, který volby nečísluje — stojí prostě pod sebou a vybranou označuje
     jedině šipka („Yes, I trust this folder" hned po startu). Že jde o nabídku
     a ne o výpis, řekne až nápověda s Enterem: samotná šipka na začátku řádku
     patří i Claudeovu vstupnímu poli. */
  const CONFIRM = /Enter to confirm/i;
  const MARKED = /^[\s│|]*[❯>]\s+\S/;
  const CHOICE = /^[\s│|]*(❯|>)?\s*(\S.*?)[\s│|]*$/;
  // Delší seznam patří šipkám: dvacet tlačítek přes celou šířku už nikdo nečte.
  const PLAIN_MAX = 8;

  // Přípony, u kterých má smysl kreslit náhled přílohy.
  const IMG_EXT = /\.(png|jpe?g|gif|webp|bmp|avif|svg)$/i;

  // Jak dlouho po doptání na schránku se další žádost bere jako tentýž stisk.
  // Ctrl+V dorazí dvakrát (keydown i paste) během jednotek milisekund.
  const PASTE_GAP = 400;   // ms

  // Kolik řádků nahoru se dialog čte. Nabídka oprávnění má i s rámečkem
  // a textem příkazu k dvaceti řádkům.
  const DIALOG_ROWS = 24;

  // Jména, kterým rozumí `/model <jméno>` — přepne rovnou, bez procházení
  // výběru v terminálu. Bez „výchozího": vybraný model má být vidět jménem,
  // a „výchozí" je jenom jiné jméno pro jeden z nich.
  const MODELS = [
    ['Opus 5', 'opus'],
    ['Sonnet 5', 'sonnet'],
    ['Haiku 4.5', 'haiku'],
    ['Fable 5', 'fable'],
  ];

  /* Režimy, jak je Claude Code hlásí pod vstupním polem. Přepíná se jedině
     Shift+Tab, které cykluje dokola — proto se sem tiskne i pořadí: kliknutí
     na režim znamená „mačkej Shift+Tab, dokud dole nesvítí tenhle".

     Bypass je v cyklu jen tehdy, když se s ním session spustila; do nabídky
     se proto dostane, až když ho terminál sám ukáže. */
  const MODES = [
    ['normal', 'Normální', /manual mode on/i],
    ['accept', 'Auto-accept', /(auto-)?accept edits on/i],
    ['plan', 'Plán', /plan mode on/i],
    ['bypass', 'Bypass', /bypass permissions on/i],
  ];

  // Kolikrát se zkusí Shift+Tab, než to vzdáme. Cyklus má nejvýš čtyři kroky,
  // pátý je pojistka proti tomu, že klávesu nikdo nečte.
  const MODE_TRIES = 5;
  const MODE_STEP = 220;   // ms — než Claude Code překreslí nápovědu

  function modeOf(lines) {
    const text = (lines || []).join('\n');
    for (const [key, , re] of MODES) if (re && re.test(text)) return key;
    return 'normal';
  }

  function modeLabel(key) {
    const found = MODES.find(m => m[0] === key);
    return found ? found[1] : 'Normální';
  }

  /* Číslované volby z dialogu, odshora dolů. Bere se jen souvislá řada od
     jedničky — čísla ve výpisu (seznam kroků, řádky souboru) se tak do
     tlačítek nedostanou. */
  function scanOptions(lines) {
    const out = [];
    for (let i = 0; i < lines.length; i++) {
      const m = OPTION.exec(lines[i]);
      if (!m) continue;
      const num = Number(m[2]);
      if (num === 1) out.length = 0;                    // začíná nová nabídka
      else if (!out.length || num !== Number(out[out.length - 1].key) + 1) continue;
      out.push({
        key: m[2],
        label: m[3].replace(/\s+/g, ' ').trim(),
        sel: !!m[1],
        row: i,
      });
    }
    return out;
  }

  /* Volby dialogu, který nečísluje. Bere se blok řádků slepený kolem toho se
     šipkou; hranicí je prázdný řádek, takže text nad nabídkou ani nápověda pod
     ní se do tlačítek nedostanou. */
  function scanPlain(lines) {
    if (!lines.some(l => CONFIRM.test(l))) return [];
    let at = -1;
    for (let i = lines.length - 1; i >= 0; i--) {
      if (MARKED.test(lines[i]) && !CONFIRM.test(lines[i])) { at = i; break; }
    }
    if (at < 0) return [];
    let from = at, to = at;
    while (from > 0 && lines[from - 1].trim()) from--;
    while (to < lines.length - 1 && lines[to + 1].trim()) to++;
    const out = [];
    for (let i = from; i <= to; i++) {
      const m = CHOICE.exec(lines[i]);
      // Cokoli, co nevypadá jako volba, znamená, že to nabídka není.
      if (!m || CONFIRM.test(lines[i])) return [];
      out.push({label: m[2].replace(/\s+/g, ' ').trim().slice(0, 60),
                sel: !!m[1], at: i - from, row: i});
    }
    return out.length > 1 && out.length <= PLAIN_MAX && out.some(o => o.sel)
      ? out : [];
  }

  /* ── dotaz přenesený do karty ─────────────────────────────────────────────
     Claude Code kreslí dotaz do terminálu: rámeček z čar, šipka u vybrané
     volby, nápověda pod tím. Přečíst se to dá, odpovědět myší ne — a hlavně
     to vypadá jako výpis, ne jako otázka. Rozebere se proto na text, volby
     a nápovědu, ze kterých se poskládá karta. */

  // Čáry rámečku a vodorovná pravítka. V terminálu drží dotaz pohromadě,
  // v kartě by z nich bylo jen rozsypané písmo navíc.
  const RULE = /^[\s─━═╌╍┄┅╭╮╰╯┌┐└┘├┤┬┴┼│┃|╔╗╚╝║]+$/;
  // Jen vnější rámeček: ten vnořený (příkaz, diff) patří do textu dotazu.
  const BOX_TOP = /^\s*[╭┌╔]/;

  /* Řádek zbavený svislého okraje rámečku — i vnořeného, protože příkaz nebo
     diff uvnitř dotazu mívá vlastní. Odsazení uvnitř zůstává: podle něj se
     v kartě pozná věta od ukázky. */
  function unbox(line) {
    let s = line.replace(/[\s│┃|]+$/, '');
    for (let i = 0; i < 2; i++) s = s.replace(/^\s*[│┃|] ?/, '');
    return s;
  }

  // Kolik řádků nad volbami se ještě počítá za text dotazu.
  const BODY_MAX = 30;

  /* Text dotazu: všechno nad volbami. Dotaz v rámečku se čte po jeho horní
     hranu; ten bez rámečku (důvěra ke složce hned po startu) nahoru tak
     dlouho, dokud text drží pohromadě — dva prázdné řádky za sebou znamenají,
     že výš už je obyčejný výpis, ne otázka. */
  function dialogBody(lines, upto) {
    let from = 0, blanks = 0;
    for (let i = upto - 1; i >= 0; i--) {
      if (!lines[i].trim()) {
        if (++blanks >= 2) { from = i + 1; break; }
        continue;
      }
      blanks = 0;
      if (BOX_TOP.test(lines[i])) { from = i + 1; break; }
      if (upto - i >= BODY_MAX) { from = i; break; }
    }
    const out = [];
    for (let i = from; i < upto; i++) {
      const s = unbox(lines[i]);
      // Čára rámečku se nekreslí, ale odděluje — bez ní by se nadpis slepil
      // s cestou k souboru pod ním do jednoho odstavce.
      if (!s.trim() || RULE.test(s)) { if (out.length) out.push(''); continue; }
      out.push(s);
    }
    while (out.length && !out[out.length - 1].trim()) out.pop();
    /* Odsazení se měří proti nejlevějšímu řádku, ne proti nule. Rámeček bývá
       odsazený celý (Claude Code do něj sype „│  text") a bez tohohle kroku
       by věty vypadaly jako ukázka kódu — naměřeno na vlastním dotazu, kde
       tak skončil úplně celý text. */
    let base = Infinity;
    for (const line of out) {
      if (line.trim()) base = Math.min(base, line.length - line.trimStart().length);
    }
    return base > 0 && base < Infinity ? out.map(l => l.slice(base)) : out;
  }

  /* Souvislé kusy textu. Odsazený řádek je ukázka (příkaz, cesta, diff) a
     patří do neproporcionálního bloku, zbytek je věta. Prázdný řádek kusy
     odděluje, do karty se sám nedostane. */
  function bodyBlocks(body) {
    const out = [];
    for (const line of body) {
      if (!line.trim()) { out.push(null); continue; }
      const code = /^\s/.test(line);
      const last = out[out.length - 1];
      if (last && last.code === code) last.lines.push(line);
      else out.push({code, lines: [line]});
    }
    return out.filter(Boolean);
  }

  /* Nápověda pod volbami. Claude Code tam píše „Enter to confirm · Esc to
     cancel" — v kartě, která má vlastní tlačítko Zrušit, by to bylo totéž
     dvakrát a ještě anglicky. Píše se proto, co v kartě opravdu platí. */
  const HINT_NUM = 'Odpovědět jde i číslem · Enter potvrdí vybranou volbu';
  const HINT_PLAIN = 'Enter potvrdí vybranou volbu';

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
     odroluje nahoru, zajímá nás, co má před očima on, ne kde stojí Claude.

     `from` je číslo prvního vráceného řádku v terminálu — podle něj se pozná,
     kde Claudeovo vstupní pole začíná. */
  function visibleBottom(term, count) {
    const buf = term.buffer.active;
    const from = Math.max(0, term.rows - count);
    const lines = [];
    for (let i = from; i < term.rows; i++) {
      const row = buf.getLine(buf.viewportY + i);
      if (row) lines.push(row.translateToString(true));
    }
    lines.from = from;
    return lines;
  }

  /* Spodek výpisu pro čtení dialogu — prázdné řádky pod ním se přeskakují.
     Dotaz na důvěru ke složce přijde hned po startu, kdy je obrazovka ještě
     prázdná: nabídka stojí nahoře a pod ní zbývá zbytek okna. Kdyby se četlo
     prostě N posledních řádků terminálu, nespadla by do nich. */
  function dialogLines(term, count) {
    const buf = term.buffer.active;
    const text = (i) => {
      const row = buf.getLine(buf.viewportY + i);
      return row ? row.translateToString(true) : '';
    };
    let last = term.rows - 1;
    while (last >= 0 && !text(last).trim()) last--;
    if (last < 0) return [];
    const lines = [];
    for (let i = Math.max(0, last - count + 1); i <= last; i++) lines.push(text(i));
    return lines;
  }

  /* Kam až nahoru sahá bublina. Hlášky (toast) se podle toho posadí nad ni —
     dřív ležely přes ni a schovaly zrovna to pole, do kterého se píše.
     Měří se bublina aktivního tabu; ostatní panely jsou display:none, takže
     mají nulovou výšku a na pořadí volání nezáleží. */
  function syncHeight() {
    const on = document.querySelector('.pane.active .composer');
    document.documentElement.style.setProperty(
      '--composer-h', (on ? on.offsetHeight : 0) + 'px');
  }

  function install(tab, io) {
    const term = tab.term;
    const root = el('div', 'composer');
    root.innerHTML = `
      <div class="composer-answer" hidden>
        <span class="composer-answer-q"></span>
        <div class="composer-answer-opts"></div>
      </div>
      <button class="composer-peek" title="Psát v bublině">
        ${icon('i-up')}<span>Psát v bublině</span>
      </button>
      <div class="composer-box">
        <div class="composer-atts" hidden></div>
        <textarea class="composer-input" rows="1" spellcheck="false"
                  placeholder="Napiš, co má Claude udělat… (Enter odešle, Shift+Enter nový řádek)"></textarea>
        <div class="composer-bar">
          <button class="composer-chip" data-act="model"
                  title="Přepne model. Claude Code si volbu uloží i jako výchozí pro nové sessions.">Model: <span class="val"></span> ▾</button>
          <button class="composer-chip" data-act="slash">/ příkazy</button>
          <button class="composer-chip" data-act="history"
                  title="Dřívější zadání. Co je napsané v poli, tím se seznam rovnou filtruje.">${icon('i-refresh')} Historie</button>
          <button class="composer-chip" data-act="file">${icon('i-image')} Příloha</button>
          <button class="composer-chip" data-act="mode"
                  title="Režim oprávnění (Shift+Tab) — plán / auto-accept / normální">Režim: <span class="val"></span> ▾</button>
          <span class="spacer"></span>
          <button class="composer-chip ghost" data-act="esc" title="Přeruší, co Claude právě dělá (Esc)">Esc</button>
          <button class="composer-send" title="Odeslat (Enter)">${icon('i-up')}</button>
        </div>
      </div>
      <input type="file" multiple hidden>`;
    tab.pane.appendChild(root);

    /* Karta s dotazem leží přes terminál. Když se Claude Code ptá, nemá být
       na co koukat do výpisu: otázka i volby patří do okna, ne mezi čáry
       rámečku. Terminál si jde kdykoli vyvolat zpátky proužkem. */
    const askRoot = el('div', 'ask');
    askRoot.hidden = true;
    askRoot.innerHTML = `
      <div class="ask-card">
        <div class="ask-tag">${icon('i-bulb')}<span>Claude se ptá</span></div>
        <div class="ask-title"></div>
        <div class="ask-body"></div>
        <div class="ask-opts"></div>
        <div class="ask-foot">
          <span class="ask-hint"></span>
          <span class="spacer"></span>
          <button class="ask-ghost" data-act="term" title="Schová kartu a ukáže dotaz tak, jak ho kreslí Claude Code">${icon('i-terminal')} Terminál</button>
          <button class="ask-ghost" data-act="esc" title="Zavře dotaz bez odpovědi (Esc)">Zrušit</button>
        </div>
      </div>
      <button class="ask-back" title="Zpátky ke kartě s dotazem">${icon('i-bulb')} Zpět k otázce</button>`;
    tab.pane.insertBefore(askRoot, root);

    const input = root.querySelector('.composer-input');
    const attBox = root.querySelector('.composer-atts');
    const picker = root.querySelector('input[type=file]');
    const modelBtn = root.querySelector('[data-act=model]');
    const modelChip = modelBtn.querySelector('.val');
    const modeBtn = root.querySelector('[data-act=mode]');
    const modeChip = modeBtn.querySelector('.val');
    // Historie zadání. Ukládá se po projektech, ať přežije zavření okna —
    // jinak by pro ni člověk musel do Claudeova vlastního hledání v terminálu.
    const HIST_KEY = 'hub.history:' + (tab.path || 'home');
    const HIST_MAX = 200;
    const history = loadHistory();

    function loadHistory() {
      try {
        const raw = JSON.parse(localStorage.getItem(HIST_KEY) || '[]');
        return Array.isArray(raw) ? raw.filter(t => typeof t === 'string') : [];
      } catch (err) {
        return [];
      }
    }

    function saveHistory() {
      try {
        localStorage.setItem(HIST_KEY, JSON.stringify(history.slice(-HIST_MAX)));
      } catch (err) { /* plná nebo zakázaná úložiště nejsou důvod spadnout */ }
    }

    let histAt = -1;         // -1 = píše se nový text, ne historie
    let draft = '';
    let hiddenByUser = false;
    let shown = false;
    let lastInsert = 0;      // kdy naposledy do pole přistála cesta k souboru
    // Klíč pro `/model <jméno>`. Ze settings.json (co si Claude Code uložil),
    // a když tam nic není, aspoň to, co se naposledy vybralo tady.
    let model = (io.model && io.model()) || localStorage.getItem('hub.model') || '';
    let mode = 'normal';     // co dole hlásí Claude Code
    let seenBypass = false;  // bypass je v cyklu jen u takhle spuštěné session
    let switching = false;   // běží přepínání režimu, nemačkat další

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
      // Dvakrát po sobě to samé je v seznamu jen k horšímu.
      if (history[history.length - 1] !== body) history.push(body);
      if (history.length > HIST_MAX) history.splice(0, history.length - HIST_MAX);
      saveHistory();
      histAt = -1;
      draft = '';
    }

    function send() {
      const files = atts.map(io.quote).join(' ');
      const text = files ? input.value.trim() : input.value;
      const body = files ? (text ? files + ' ' + text : files) : text;
      if (!body.trim()) return;
      submit(body);
      input.value = '';
      atts.length = 0;
      renderAtts();
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

    // Odesílací tlačítko se rozsvítí, až když je co odeslat — text nebo příloha.
    function ready() {
      root.classList.toggle('ready', !!input.value.trim() || atts.length > 0);
    }

    function autogrow() {
      input.style.height = 'auto';
      // Strop je pět řádků: dál už by bublina ukrajovala z výpisu příliš.
      input.style.height = Math.min(input.scrollHeight, 5 * 20 + 12) + 'px';
      ready();
      // Vyšší bublina = o řádek kratší terminál, ať pod ní nic nezmizí.
      if (shown) { dirty = true; fitOver(); }
    }

    /* ── nabídky ──────────────────────────────────────────────────────────── */

    /* Nabídky se otevírají nad bublinu — pod ní je konec okna. Kotví se na
       horní hranu celé bubliny, ne na tlačítko, ať nepřekrývají text. */
    function anchor(ev) {
      const chip = ev.currentTarget.getBoundingClientRect();
      const bubble = root.querySelector('.composer-box').getBoundingClientRect();
      return [chip.left, bubble.top - 6];
    }

    /* Jméno modelu pro člověka. V settings.json nemusí být zrovna to krátké
       jméno, co bere `/model` — bývá tam i celé id (claude-opus-5), tak se
       hledá i podle kusu jména. */
    function modelName(key) {
      if (!key) return 'výchozí';
      const exact = MODELS.find(m => m[1] === key);
      if (exact) return exact[0];
      const near = MODELS.find(m => key.toLowerCase().includes(m[1]));
      return near ? near[0] : key;
    }

    function syncModel() {
      modelChip.textContent = modelName(model);
    }

    function modelMenu(ev) {
      const [x, y] = anchor(ev);
      io.menu(x, y, MODELS.map(([label, key]) => ({
        icon: 'i-star',
        label,
        on: modelName(model) === label,
        run: () => {
          // /model <jméno> přepne rovnou, bez procházení výběru v terminálu.
          submit('/model ' + key);
          model = key;
          if (io.model) io.model(key);
          try { localStorage.setItem('hub.model', key); } catch (err) { /* soukromé okno */ }
          syncModel();
        },
      })), {above: true});
    }

    /* ── režim oprávnění ──────────────────────────────────────────────────── */

    /* Přepnout se dá jedině Shift+Tab, a to cykluje: na vybraný režim se tedy
       mačká tak dlouho, dokud ho Claude Code dole nenahlásí. Slepě poslat
       jedno Shift+Tab nestačilo — nápověda s režimem leží pod bublinou, takže
       po tlačítku nebylo nic vidět a působilo mrtvě. */
    function syncMode(lines) {
      const now = modeOf(lines);
      if (now === 'bypass') seenBypass = true;
      if (now === mode) return now;
      mode = now;
      modeChip.textContent = modeLabel(mode);
      modeBtn.classList.toggle('normal', mode === 'normal');
      return now;
    }

    function readMode() {
      return syncMode(visibleBottom(term, PROBE_ROWS));
    }

    async function setMode(target) {
      if (switching) return;
      switching = true;
      try {
        for (let i = 0; i < MODE_TRIES; i++) {
          if (readMode() === target) return;
          toPty('\x1b[Z');
          await new Promise(done => setTimeout(done, MODE_STEP));
        }
        if (readMode() !== target) {
          io.notice(target === 'bypass'
            ? 'Bypass jde jen u session spuštěné s --dangerously-skip-permissions.'
            : 'Režim se přepnout nepodařilo — zkus Shift+Tab v terminálu.');
        }
      } finally {
        switching = false;
        input.focus();
      }
    }

    function modeMenu(ev) {
      const [x, y] = anchor(ev);
      readMode();
      const items = MODES
        .filter(([key]) => key !== 'bypass' || seenBypass)
        .map(([key, label]) => ({
          icon: key === 'plan' ? 'i-note' : 'i-dot',
          label,
          on: key === mode,
          run: () => setMode(key),
        }));
      io.menu(x, y, items, {above: true});
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

    /* Dřívější zadání v nabídce. Claude Code má svoje hledání (Ctrl+R), jenže
       to je celoobrazovkový terminálový výběr — tady stačí kliknout. Místo
       filtračního políčka slouží to, co je zrovna napsané v poli. */
    function historyMenu(ev) {
      const [x, y] = anchor(ev);
      const needle = input.value.trim().toLowerCase();
      const seen = new Set();
      const items = [];
      for (let i = history.length - 1; i >= 0 && items.length < 40; i--) {
        const text = history[i];
        if (seen.has(text)) continue;
        seen.add(text);
        if (needle && !text.toLowerCase().includes(needle)) continue;
        items.push({
          icon: text.startsWith('/') ? 'i-terminal' : 'i-note',
          // Víceřádkové zadání by nabídku roztáhlo; v poli se pak ukáže celé.
          label: text.replace(/\s+/g, ' ').slice(0, 80),
          run: () => {
            input.value = text;
            autogrow();
            input.focus();
            input.setSelectionRange(text.length, text.length);
          },
        });
      }
      if (!items.length) {
        items.push({icon: 'i-bulb',
                    label: needle ? 'Nic takového tu není' : 'Zatím prázdno',
                    run: () => input.focus()});
      }
      io.menu(x, y, items, {above: true});
    }

    /* ── odpovídání na dialogy myší ───────────────────────────────────────── */

    /* Když se Claude Code na něco ptá, bublina uhne (jinak by dotaz překryla)
       a zbyl by jen terminál. Otázka se proto přenese do tlačítek: volby
       z rámečku, ano/ne i šipky u výběru ze seznamu. */
    const answer = root.querySelector('.composer-answer');
    const answerQ = root.querySelector('.composer-answer-q');
    const answerOpts = root.querySelector('.composer-answer-opts');
    const askCard = askRoot.querySelector('.ask-card');
    const askTitle = askRoot.querySelector('.ask-title');
    const askBody = askRoot.querySelector('.ask-body');
    const askOpts = askRoot.querySelector('.ask-opts');
    const askHint = askRoot.querySelector('.ask-hint');
    let answerSig = '';

    function press(data) {
      toPty(data);
      // Psaní (filtr v hledání, vlastní odpověď) má po kliknutí pokračovat
      // v terminálu, ne na tlačítku.
      term.focus();
    }

    /* Nabídka bez čísel se ovládá jedině šipkami, takže kliknutí znamená
       „dojeď na tu volbu a potvrď". Klávesy jdou po jedné s odstupem: slepené
       v jedné dávce si je Claude Code přebere jako jediný stisk. */
    function pickPlain(steps) {
      const key = steps < 0 ? '\x1b[A' : '\x1b[B';
      let wait = 0;
      for (let i = Math.abs(steps); i > 0; i--, wait += 50) {
        setTimeout(() => toPty(key), wait);
      }
      setTimeout(() => toPty('\r'), wait + 120);
      term.focus();
    }

    function renderAnswer() {
      const buf = term.buffer.active;
      // Odrolováno nahoru: dole je historie, ne živý dotaz.
      const live = !shown && !tab.exited && buf.viewportY >= buf.baseY - 1;
      const lines = live ? dialogLines(term, Math.min(term.rows, DIALOG_ROWS)) : [];
      const text = lines.join('\n');
      /* Číslovaný seznam se ve výpisu objeví i jen tak (kroky, poznámky).
         Že jde o dotaz, prozradí až šipka ❯ u jedné z voleb — tu Claude Code
         kreslí jedině u toho, co se dá vybrat. */
      const found = lines.length ? scanOptions(lines) : [];
      const opts = found.some(o => o.sel) ? found : [];
      const plain = opts.length || !lines.length ? [] : scanPlain(lines);
      const picker = !opts.length && !plain.length && PICKER.test(text);
      const yesno = !opts.length && !plain.length && !picker && YESNO.test(text);

      /* Volby jako řádky karty. Číslované se odpovídají číslem, nečíslované
         dojezdem šipek — pro člověka je to v obou případech jedno kliknutí. */
      const rows = [];
      if (opts.length) {
        for (const o of opts) {
          rows.push({key: o.key, label: o.label, sel: o.sel, row: o.row,
                     run: () => press(o.key)});
        }
      } else if (plain.length) {
        const now = plain.findIndex(o => o.sel);
        for (const o of plain) {
          rows.push({label: o.label, sel: o.sel, row: o.row,
                     run: () => pickPlain(o.at - now)});
        }
      } else if (yesno) {
        rows.push({label: 'Ano', run: () => press('y')},
                  {label: 'Ne', run: () => press('n')});
      }

      // Text nad volbami a nápověda pod nimi. Ano/ne se nekreslí jako seznam,
      // takže tam žádné „nad" a „pod" není — bere se celý dotaz.
      const numbered = rows.length && rows[0].row != null;
      const body = rows.length
        ? dialogBody(lines, numbered ? rows[0].row : lines.length) : [];
      const hint = !rows.length ? ''
                 : opts.length ? HINT_NUM : plain.length ? HINT_PLAIN : '';
      let blocks = bodyBlocks(body);
      // První krátká věta je nadpis („Bash command", „Accessing workspace:").
      let title = '';
      if (blocks.length && !blocks[0].code && blocks[0].lines.length === 1 &&
          blocks[0].lines[0].length <= 70) {
        title = blocks[0].lines[0];
        blocks = blocks.slice(1);
      }

      /* Výběr ze seznamu (historie, volba modelu) do karty nepatří: seznam je
         v terminálu a karta by ho překryla. Tomu zbývá lišta se šipkami. */
      const buttons = [];
      if (picker) {
        buttons.push({label: '↑', title: 'O položku výš', run: () => press('\x1b[A')},
                     {label: '↓', title: 'O položku níž', run: () => press('\x1b[B')},
                     {label: 'Vybrat (Enter)', run: () => press('\r')},
                     {label: 'Zrušit (Esc)', ghost: true, run: () => press('\x1b')});
      }

      // Překreslovat se má jen při změně: jinak by tlačítko zmizelo pod prstem
      // uprostřed kliknutí, protože terminál překresluje i sám od sebe.
      const sig = [
        rows.map(r => (r.sel ? '*' : '') + (r.key || '') + r.label).join('|'),
        title,
        blocks.map(b => b.lines.join('\n')).join('\n\n'),
        hint,
        buttons.map(b => b.label).join('|'),
      ].join(' ');
      if (sig === answerSig) return;
      answerSig = sig;

      // Nový dotaz = nová otázka, na kterou se má člověk podívat. Odsunutí
      // karty platilo pro ten předchozí.
      askRoot.classList.remove('peek');
      askRoot.hidden = !rows.length;
      tab.pane.classList.toggle('asking', !!rows.length);
      if (rows.length) {
        askTitle.textContent = title;
        askTitle.hidden = !title;
        askBody.textContent = '';
        for (const b of blocks) {
          const node = el(b.code ? 'pre' : 'p', b.code ? 'ask-code' : 'ask-p',
                          b.lines.join('\n'));
          askBody.appendChild(node);
        }
        askBody.hidden = !blocks.length;
        askOpts.textContent = '';
        for (const r of rows) {
          const btn = el('button', 'ask-opt' + (r.sel ? ' sel' : ''));
          if (r.key) btn.appendChild(el('span', 'ask-num', r.key));
          btn.appendChild(el('span', 'ask-opt-label', r.label));
          // Na kterou volbu ukazuje ❯ v terminálu — tam padne Enter.
          if (r.sel) btn.insertAdjacentHTML('beforeend', icon('i-check'));
          btn.onclick = r.run;
          askOpts.appendChild(btn);
        }
        askHint.textContent = hint;
        askCard.scrollTop = 0;
      }

      answer.hidden = !buttons.length;
      answerOpts.textContent = '';
      if (buttons.length) {
        answerQ.textContent = 'Výběr:';
        for (const b of buttons) {
          const btn = el('button', 'composer-answer-btn' +
                                   (b.sel ? ' sel' : '') + (b.ghost ? ' ghost' : ''),
                         b.label);
          if (b.title) btn.title = b.title;
          btn.onclick = b.run;
          answerOpts.appendChild(btn);
        }
      }
      // I zmizení lišty musí terminálu vrátit řádky, které si na ni vzala.
      holdForAnswer();
      syncHeight();
    }

    /* Karta se dá odsunout — třeba když se dotaz nepovedlo přečíst tak, jak
       ho Claude Code nakreslil. Zbyde po ní tlačítko, kterým se vrátí. */
    function askShow(on) {
      askRoot.classList.toggle('peek', !on);
      if (on) askCard.scrollTop = 0;
      term.focus();
    }
    askRoot.querySelector('[data-act=term]').onclick = () => askShow(false);
    askRoot.querySelector('.ask-back').onclick = () => askShow(true);
    askRoot.querySelector('[data-act=esc]').onclick = () => press('\x1b');

    /* Lišta s odpovědí leží přes spodek terminálu — tedy přes poslední řádky
       dialogu, který popisuje. Terminál se o ni proto na tu chvíli zkrátí,
       ať zůstane vidět celá otázka. */
    let answerHold = -1;
    function holdForAnswer() {
      if (shown || answer.hidden) {
        if (answerHold >= 0) {
          answerHold = -1;
          dirty = true;
          io.reserve(reserved > 0 ? reserved : 0);
        }
        return;
      }
      const px = Math.round(root.offsetHeight);
      if (Math.abs(answerHold - px) < 1) return;
      answerHold = px;
      dirty = true;
      io.reserve(px);
    }

    async function attach(files) {
      const paths = await io.upload(files);
      if (paths.length) insertPaths(paths);
    }

    /* Cesta k souboru je jediné, co si Claude Code z obrázku vezme — jenže
       v poli je z ní jen dlouhý řetěz, ze kterého nikdo nepozná, co vlastně
       přiložil. Cesty se proto drží stranou, v bublině je vidět náhled a
       k textu se připojí až při odeslání.

       Sem chodí i to, co člověk pustí nebo vloží kdekoli v tabu — hub.js se
       ptá bubliny dřív, než by cestu napsal do terminálu. */
    const atts = [];

    function insertPaths(paths) {
      if (!shown || !paths.length) return false;
      for (const path of paths) {
        if (!atts.includes(path)) atts.push(path);
      }
      renderAtts();
      lastInsert = Date.now();
      input.focus();
      return true;
    }

    function dropAtt(path) {
      const at = atts.indexOf(path);
      if (at >= 0) atts.splice(at, 1);
      renderAtts();
      input.focus();
    }

    function renderAtts() {
      attBox.textContent = '';
      attBox.hidden = !atts.length;
      for (const path of atts) {
        const name = path.split(/[\\/]/).pop();
        const chip = el('div', 'composer-att');
        // Celá cesta patří pod myš, ne do bubliny — tam mluví sám náhled.
        chip.title = path;
        const x = el('button', 'composer-att-x');
        x.title = 'Odebrat přílohu';
        x.innerHTML = icon('i-close');
        x.onclick = () => dropAtt(path);
        // Jméno pasteovaného screenshotu je jen časové razítko, to nikomu nic
        // neřekne. U obrázku proto stojí za sebe náhled, jméno až u ostatních.
        const named = () => {
          chip.classList.remove('img');
          chip.classList.add('bare');
          chip.insertBefore(el('span', 'composer-att-name', name), x);
        };
        if (IMG_EXT.test(name) && io.imageUrl) {
          chip.classList.add('img');
          const img = el('img');
          img.src = io.imageUrl(path);
          img.alt = name;
          // Náhled se nemusí povést (soubor mimo složku hubu) — pak zbyde jméno.
          img.onerror = () => { img.remove(); named(); };
          chip.appendChild(img);
          chip.appendChild(x);
        } else {
          chip.appendChild(x);
          named();
        }
        attBox.appendChild(chip);
      }
      ready();
      // Pruh s náhledy je o kus vyšší bublina — terminál se o něj musí zkrátit.
      if (shown) { dirty = true; fitOver(); }
      syncHeight();
    }

    /* Ctrl+V s obrázkem v bublině. Vkládání do terminálu si řeší clipboard.js
       přes server, jenže když je bublina vidět, fokus drží její textové pole —
       a tam Ctrl+V obslouží prohlížeč. Text zvládne, ale obrázek pod WebKitGTK
       do `clipboardData` nedá vůbec nic, takže vkládání screenshotu do bubliny
       vyznělo naprázdno. Doptáme se tedy serveru: ten na systémovou schránku
       dosáhne (xclip / wl-paste), odloží si kopii na disk a vrátí cestu.

       Text schválně neřešíme — ten prohlížeč vloží sám a podruhé ho nechceme.

       Jedno Ctrl+V přitom projde dvěma cestami: nejdřív jako `keydown`, pak
       jako `paste`, kterému nebereme výchozí chování kvůli textu. Bez pojistky
       se server zeptá dvakrát — a protože si obrázek pokaždé odloží pod novým
       jménem, přilepily se k promptu dvě kopie téhož screenshotu. */
    let pasteRun = null;     // běžící doptání serveru
    let pasteAt = 0;         // kdy skončilo to poslední
    let byBrowser = 0;       // kdy si obrázek vzal prohlížeč sám

    function pasteImage() {
      // Druhá cesta téhož stisku mlčí. Okno je krátké, takže dva screenshoty
      // po sobě si člověk pořád vloží oba.
      if (pasteRun) return pasteRun;
      if (Date.now() - pasteAt < PASTE_GAP) return Promise.resolve();
      pasteRun = (async () => {
        const at = Date.now();
        // Kdyby prohlížeč obrázek přece jen podal, dorazí za okamžik jako paste
        // se souborem a cestu vloží ta cesta. Pauza jí dá přednost, ať tentýž
        // screenshot neskončí v poli dvakrát.
        await new Promise(done => setTimeout(done, 140));
        if (lastInsert >= at || byBrowser >= at) return;
        let res;
        try {
          res = await io.read('clipboard');
        } catch (err) {
          return;
        }
        // Nahrávání souboru trvá dýl než tohle doptání, takže se ptáme znovu:
        // mezitím mohla cesta dorazit tou druhou cestou.
        if (lastInsert >= at || byBrowser >= at) return;
        // Bez hlášky s cestou: co se přiložilo, je vidět na náhledu v bublině.
        if (res && res.image) insertPaths([res.image]);
      })();
      return pasteRun.finally(() => { pasteRun = null; pasteAt = Date.now(); });
    }

    /* ── kolik spodku terminálu si bublina bere ───────────────────────────── */

    /* Bublina leží přes spodek terminálu, aby překryla vstupní pole, které si
       Claude Code kreslí sám. Jenže její výška se s tím polem nepotká: je-li
       bublina vyšší, spolkne navíc i poslední řádky výpisu — a to bývá zrovna
       to, co má člověk číst (zařazená zpráva, poslední odpověď). Naměří se
       proto, kolik řádků Claudeovo pole zabírá, a terminál se o ten rozdíl
       zkrátí. Bublina pak končí přesně na horní hraně Claudeova pole. */

    // Výška jednoho řádku. Terminál kreslí DOM renderer, takže řádek je prvek;
    // kdyby nebyl, spočítá se z plátna.
    function cellHeight() {
      const rows = tab.pane.querySelector('.xterm-rows');
      const first = rows && rows.firstElementChild;
      if (first) {
        const h = first.getBoundingClientRect().height;
        if (h > 4) return h;
      }
      const screen = tab.pane.querySelector('.xterm-screen');
      if (screen && term.rows) {
        const h = screen.getBoundingClientRect().height / term.rows;
        if (h > 4) return h;
      }
      return 0;
    }

    /* Kolik spodních řádků patří Claudeovu vstupnímu poli. Šipka ❯ je jeho
       prostředek, nad ní je horní hrana rámečku, pod ní dolní hrana a nápověda
       k režimu. Zařazené zprávy leží nad rámečkem, ty se počítat nesmí — právě
       o ně tady jde. */
    function ownRows(lines) {
      for (let k = lines.length - 1; k >= 0; k--) {
        if (!PROMPT.test(lines[k])) continue;
        const row = (lines.from || 0) + k;
        return term.rows - Math.max(0, row - 1);
      }
      return 0;
    }

    // Výška bubliny samotné, bez dorovnání na celé řádky.
    function naturalHeight() {
      const keep = root.style.minHeight;
      root.style.minHeight = '0px';
      const h = root.offsetHeight;
      root.style.minHeight = keep;
      return h;
    }

    let reserved = -1;       // kolik pixelů dole si bublina drží
    let lastLines = null;    // poslední naměřený spodek terminálu
    let lastOwn = 0;
    let dirty = true;        // je co přeměřit (jinak se sahá jen na regexy)

    function fitOver() {
      const own = lastLines ? ownRows(lastLines) : 0;
      if (!own) return;
      // Přeměřovat při každém překreslení by znamenalo vynutit si přepočet
      // rozvržení stránky uprostřed výpisu. Sáhne se na to, jen když se něco
      // změnilo — jinak stačí porovnat čísla řádků.
      if (!dirty && own === lastOwn) return;
      const cell = cellHeight();
      const box = tab.termbox;
      if (!cell || !box) return;
      dirty = false;
      lastOwn = own;
      // Místo od horní hrany terminálu po spodek panelu — o tohle se terminál
      // s bublinou dělí.
      const space = tab.pane.getBoundingClientRect().bottom -
                    box.getBoundingClientRect().top;
      const natural = naturalHeight();
      /* Terminál dostane tolik celých řádků, aby na bublinu zbylo aspoň
         tolik, kolik potřebuje. Bublina pak sahá přesně na horní hranu
         Claudeova vstupního pole — ani o řádek výš, kde už je výpis.

         Počítá se přes počet řádků schválně: terminál kreslí jen celé řádky
         a zbytek pod nimi nechává prázdný, takže z pixelů by vyšla mezera. */
      let rows = Math.floor((space + own * cell - natural) / cell);
      rows = Math.max(1, Math.min(rows, Math.floor(space / cell)));
      root.style.minHeight =
        Math.min(space, space - (rows - own) * cell) + 'px';
      const keep = Math.round(space - rows * cell);
      // Místo se drží i ve složeném stavu: uvolnit ho při každém dialogu by
      // znamenalo terminál pořád zvětšovat a zmenšovat, a překreslování v něm
      // je vidět víc než prázdný proužek dole.
      if (Math.abs(reserved - keep) < 1) return;
      reserved = keep;
      io.reserve(keep);
    }

    /* ── viditelnost ──────────────────────────────────────────────────────── */

    function looksIdle() {
      if (tab.exited) return false;
      const buf = term.buffer.active;
      // Odrolováno nahoru: tam bublina jen zakrývá historii.
      if (buf.viewportY < buf.baseY - 1) return false;
      const lines = visibleBottom(term, PROBE_ROWS);
      if (lines.some(l => DIALOG.test(l))) return false;
      if (!lines.some(l => PROMPT.test(l)) || !lines.some(l => HINT.test(l))) {
        return false;
      }
      lastLines = lines;
      // Režim se dá poznat jen z nápovědy pod vstupním polem — a tu bublina
      // vzápětí překryje. Tady je naposledy vidět, tak se z ní opíše na chip.
      syncMode(lines);
      return true;
    }

    function apply() {
      const want = !hiddenByUser && looksIdle();
      if (want !== shown) {
        const hadTerm = tab.pane.contains(document.activeElement) &&
                        document.activeElement !== input;
        shown = want;
        root.classList.toggle('on', shown);
        if (!shown) root.style.minHeight = '';
        dirty = true;
        syncHeight();
        // Fokus musí jít za tím, do čeho se píše. Jinak by psaní s diakritikou
        // (skládané klávesy) skončilo v poli schovaném pod bublinou.
        if (shown && hadTerm) input.focus();
        if (!shown && document.activeElement === input) term.focus();
      }
      // Měřit jde až s nasazenou třídou: složená bublina je jenom proužek
      // a vyšla by z ní čtvrtinová výška.
      if (shown) fitOver();
      renderAnswer();
    }

    let pending = null;
    function schedule() {
      if (pending) return;
      pending = setTimeout(() => { pending = null; apply(); }, 90);
    }

    const offRender = term.onRender(schedule);
    const offScroll = term.onScroll(schedule);
    // Jiná velikost okna = jiná výška řádku i jiný počet řádků, přeměřit.
    const offResize = term.onResize(() => { dirty = true; schedule(); });
    // Roste s textem a mizí s přepnutím tabu — obojí musí hlášky poznat.
    const sizes = new ResizeObserver(syncHeight);
    sizes.observe(root);

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
        // Nápověda s režimem je pod bublinou, tak ať se změna projeví na chipu.
        setTimeout(readMode, MODE_STEP);
        return;
      }
      // Bez preventDefault: text si vloží prohlížeč sám, my jen doplníme to,
      // co nám z obrázku nedá.
      if ((ev.ctrlKey || ev.metaKey) && !ev.altKey &&
          (ev.key || '').toLowerCase() === 'v') {
        pasteImage();
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
      const cd = ev.clipboardData;
      const files = cd && cd.files;
      // Text má přednost: zkopírovaný soubor ze správce nese vedle sebe i svoje
      // jméno jako text, a kdo kopíroval text, čeká text.
      const text = cd && cd.getData && cd.getData('text/plain');
      if (files && files.length && !text) {
        ev.preventDefault();
        attach(files);
        return;
      }
      // Ani soubor, ani text — přesně tak vypadá screenshot pod WebKitGTK.
      // Ctrl+V si hlídá keydown, tohle je pro Shift+Insert a nabídku.
      if (!text) pasteImage();
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
    modelBtn.onclick = modelMenu;
    root.querySelector('[data-act=slash]').onclick = slashMenu;
    root.querySelector('[data-act=history]').onclick = historyMenu;
    root.querySelector('[data-act=file]').onclick = () => picker.click();
    modeBtn.onclick = modeMenu;
    root.querySelector('[data-act=esc]').onclick = () => { toPty('\x1b'); input.focus(); };
    picker.onchange = () => {
      if (picker.files && picker.files.length) attach(picker.files);
      picker.value = '';
    };

    syncModel();
    modeChip.textContent = modeLabel(mode);
    modeBtn.classList.add('normal');
    schedule();

    return {
      insertPaths,
      /* Prohlížeč si obrázek ze schránky vzal sám (dostal ho v `clipboardData`
         jako soubor) a nahrává ho. Doptávat se ještě serveru by znamenalo
         přiložit tentýž screenshot dvakrát — jednou jako nahraný soubor,
         podruhé jako kopii odloženou serverem. */
      browserPaste: () => { byBrowser = Date.now(); },
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
        offResize.dispose();
        sizes.disconnect();
        if (pending) clearTimeout(pending);
        root.remove();
        askRoot.remove();
        tab.pane.classList.remove('asking');
        if (reserved > 0) io.reserve(0);
        syncHeight();
      },
    };
  }

  global.HubComposer = {install};

})(window);
