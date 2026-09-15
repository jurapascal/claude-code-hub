/* Odkazy z terminálu jako tlačítka.
 *
 * Adresa v terminálu je text: dlouhou — typicky přihlášení („Browser didn't
 * open? Use the url below to sign in") — program rozláme přes několik řádků,
 * takže se nečte, nedá se kliknout (web-links addon slepí jen řádky, které
 * zalomil terminál, ne ty, které zalomil program) a myší se vybírá špatně.
 * Na serveru navíc klik vedl na `xdg-open` na serveru, kde se nic neukáže.
 *
 * Každou adresu, která je vidět, proto v okně zakryje výplň barvou terminálu
 * a na jejím začátku stojí tlačítko Otevřít odkaz s kopírováním vedle.
 * Tlačítko je pro všechny odkazy stejné — celá adresa je v popisku pod myší.
 * Terminál ani výpis se nemění, jen se na adresu nekouká. Co se s odkazem
 * stane, rozhoduje hub.js (`io.open`, `io.copy`) — na počítači a na serveru
 * se otevírá jinak.
 */
'use strict';

(function (global) {

  // Adresa končí mezerou, uvozovkou nebo čárou rámečku, kterou kreslí TUI.
  const URL_RE = /https?:\/\/[^\s"'<>`│┃║]+/g;
  // Začátek řádku, kterým adresa pokračuje: znaky adresy.
  const URL_HEAD = /^[A-Za-z0-9\-._~:/?#[\]@!$&'()*+,;=%]+/;
  // Mezera nebo čára rámečku — okraj řádku, ne jeho obsah.
  const EDGE = /[\s│┃║]/;
  /* Kolik sloupců před pravým okrajem už je řádek „plný". Program láme dřív
     než terminál (odsazení a okraj TUI), takže přesně na okraj nedosáhne. */
  const FULL_SLACK = 12;
  // Přihlašovací odkaz dostane výrazné tlačítko — kvůli němu tu tohle je.
  const LOGIN = /oauth|authorize|\/login|\/signin|\/device|\/activate/i;
  // Tlačítko má pro každý odkaz stejný rozměr, ať je adresa jakkoli dlouhá.
  const BTN_W = 150;
  const BTN_H = 24;

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function icon(id) {
    return `<svg class="ico"><use href="#${id}"/></svg>`;
  }

  function coreStart(text) {
    let i = 0;
    while (i < text.length && EDGE.test(text[i])) i++;
    return i;
  }

  function coreEnd(text) {
    let i = text.length;
    while (i > 0 && EDGE.test(text[i - 1])) i--;
    return i;
  }

  // Tečka nebo závorka za adresou patří větě, ne adrese — ledaže v ní začala.
  function trimUrl(url) {
    let u = url;
    for (;;) {
      const last = u.slice(-1);
      const count = (ch) => u.split(ch).length - 1;
      if ('.,;:!?\'"'.includes(last) ||
          (last === ')' && count('(') < count(')')) ||
          (last === ']' && count('[') < count(']'))) {
        u = u.slice(0, -1);
      } else {
        return u;
      }
    }
  }

  /* Viditelné adresy: {url, parts: [{row, from, to}], alone} — kde na obrazovce
     leží (řádek a sloupce) a jestli jsou na svých řádcích samy.

     Na další řádek adresa pokračuje, když ho zalomil terminál (isWrapped), nebo
     když ho zalomil program: ten slovo láme uprostřed jen tehdy, když se na
     řádek nevejde celé — adresa pak začíná na začátku plného řádku a sahá až
     na jeho konec. Jinak by se k adrese na konci řádku přilepilo první slovo
     další věty. */
  function findLinks(term) {
    const buf = term.buffer.active;
    const rows = [];
    for (let i = 0; i < term.rows; i++) {
      const line = buf.getLine(buf.viewportY + i);
      if (!line) break;
      rows.push({text: line.translateToString(true), wrapped: line.isWrapped});
    }
    const full = (text) => text.length >= term.cols - FULL_SLACK;
    const links = [];
    let skip = null;          // kousek adresy, který už patří té z řádku výš

    rows.forEach((row, i) => {
      URL_RE.lastIndex = 0;
      let m;
      while ((m = URL_RE.exec(row.text))) {
        if (skip && skip.row === i && m.index < skip.to) continue;
        let url = m[0];
        const parts = [{row: i, from: m.index, to: m.index + url.length}];
        let r = i;
        while (r + 1 < rows.length) {
          const last = parts[parts.length - 1];
          const here = rows[r].text;
          const next = rows[r + 1];
          const endsRow = last.to === coreEnd(here);
          const byTerminal = next.wrapped && endsRow;
          const byProgram = !next.wrapped && endsRow && full(here) &&
                            last.from === coreStart(here);
          if (!byTerminal && !byProgram) break;
          const start = byTerminal ? 0 : coreStart(next.text);
          const head = URL_HEAD.exec(next.text.slice(start));
          if (!head) break;
          url += head[0];
          parts.push({row: r + 1, from: start, to: start + head[0].length});
          r++;
        }
        if (parts.length > 1) skip = parts[parts.length - 1];

        // Interpunkce za adresou zůstane vidět jako součást věty.
        const clean = trimUrl(url);
        parts[parts.length - 1].to -= url.length - clean.length;
        try {
          if (!new URL(clean).host) continue;
        } catch (_) {
          continue;
        }
        const alone = parts.every((p) => p.from === coreStart(rows[p.row].text) &&
                                         p.to >= coreEnd(rows[p.row].text) - 1);
        links.push({url: clean, parts, alone});
      }
    });
    return links;
  }

  /* install(tab, io) — io: {open(url), copy(text) → Promise, notice(text)}. */
  function install(tab, io) {
    const {term} = tab;
    const layer = el('div', 'links');
    tab.termbox.appendChild(layer);
    let drawn = '';
    let frame = 0;

    function place(node, box) {
      Object.assign(node.style, {
        left: box.left + 'px', top: box.top + 'px',
        width: box.width + 'px', height: box.height + 'px',
      });
      return node;
    }

    function buttons(url) {
      const login = LOGIN.test(url);
      const open = el('button', 'link-open' + (login ? ' primary' : ''));
      open.type = 'button';
      open.title = url;
      open.innerHTML = icon('i-open');
      open.append(el('span', 'link-label', login ? 'Přihlásit se' : 'Otevřít odkaz'));
      open.onclick = () => io.open(url);

      const copy = el('button', 'link-copy');
      copy.type = 'button';
      copy.title = 'Zkopírovat odkaz';
      copy.innerHTML = icon('i-copy');
      copy.onclick = async () => {
        try {
          await io.copy(url);
          io.notice('Odkaz je ve schránce');
        } catch (_) {
          io.notice('Odkaz se nepodařilo zkopírovat');
        }
      };
      return [open, copy];
    }

    function update() {
      frame = 0;
      const screen = tab.termbox.querySelector('.xterm-screen');
      // Skrytý tab nemá rozměry; přečte se, až se na něj přepne (activate).
      if (!screen || tab.pane.offsetWidth === 0) return;
      const outer = tab.termbox.getBoundingClientRect();
      const scr = screen.getBoundingClientRect();
      const x0 = scr.left - outer.left;
      const y0 = scr.top - outer.top;
      const cellW = scr.width / term.cols;
      const cellH = scr.height / term.rows;
      const links = findLinks(term);
      // Překreslit jen při změně — onRender chodí s každým kouskem výpisu.
      const key = JSON.stringify([links, x0, y0, cellW, cellH, outer.width]);
      if (key === drawn) return;
      drawn = key;
      layer.textContent = '';

      for (const link of links) {
        const open = () => io.open(link.url);
        // Adresa sama na řádcích: jeden obdélník přes všechny. Ve větě: každý
        // kousek zvlášť, ať zbytek věty zůstane vidět.
        const rects = link.alone
          ? [{
            left: x0 + Math.min(...link.parts.map((p) => p.from)) * cellW,
            top: y0 + link.parts[0].row * cellH,
            width: (Math.max(...link.parts.map((p) => p.to)) -
                    Math.min(...link.parts.map((p) => p.from))) * cellW,
            height: (link.parts[link.parts.length - 1].row - link.parts[0].row + 1) * cellH,
          }]
          : link.parts.map((p) => ({
            left: x0 + p.from * cellW, top: y0 + p.row * cellH,
            width: (p.to - p.from) * cellW, height: cellH,
          }));
        for (const rect of rects) {
          const cover = place(el('div', 'link'), rect);
          cover.onclick = open;
          layer.appendChild(cover);
        }
        /* Tlačítko na začátek adresy. Kratší adresu přesáhne doprava (je pro
           všechny stejně široké); u pravého okraje couvne, ať nevyjede z okna. */
        const first = rects[0];
        const height = Math.min(BTN_H, first.height);
        const group = place(el('div', 'link-btn'), {
          left: Math.max(0, Math.min(first.left, outer.width - BTN_W - 2)),
          top: first.top + (first.height - height) / 2,
          width: BTN_W, height,
        });
        group.append(...buttons(link.url));
        layer.appendChild(group);
      }
    }

    // Jednou za snímek: s výpisem se adresa posouvá a tlačítko musí jet s ní.
    function schedule() {
      if (!frame) frame = requestAnimationFrame(update);
    }

    const subs = [term.onRender(schedule), term.onScroll(schedule), term.onResize(schedule)];
    const sizes = new ResizeObserver(schedule);
    sizes.observe(tab.termbox);
    schedule();

    return {
      update: schedule,
      release() {
        cancelAnimationFrame(frame);
        subs.forEach((sub) => sub.dispose());
        sizes.disconnect();
        layer.remove();
      },
    };
  }

  global.HubLinks = {install, findLinks};
})(window);
