/* Náhled trezoru Obsidian přímo v hubu.
 *
 * Na serveru (brána) ani na počítači bez aplikace Obsidian se trezor nemá čím
 * otevřít — tlačítko „Otevřít Obsidian Brain" by nic neudělalo. Tady je trezor
 * vidět v okně hubu: poznámky po složkách, hledání v názvech i v textu,
 * vykreslená poznámka s odkazy [[…]] a co na ni odkazuje. Jen ke čtení.
 *
 * Markdown se převádí tady, ne knihovnou: hub běží i bez sítě a poznámky píše
 * i Claude. Cokoli z poznámky se proto nejdřív escapuje a HTML vzniká jen
 * z toho, co převod sám vyrobí — syrové HTML z poznámky se nikdy nevloží.
 */
(function (root) {
  'use strict';

  const ESC = {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'};
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ESC[c]);
  const IMAGE_EXT = /\.(png|jpe?g|gif|webp|svg|bmp|avif)$/i;
  const LIST = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;

  /* ── Markdown → HTML ──────────────────────────────────────────────────── */

  /* Vlastnosti z frontmatteru: `klíč: hodnota`, vnořené klíče i seznamy
     (`- a`) se srovnají do jedné řady — na čtení stačí. */
  function frontmatter(md) {
    const m = /^---\n([\s\S]*?)\n---[ \t]*(?:\n|$)/.exec(md);
    if (!m) return [[], md];
    const props = [];
    for (const line of m[1].split('\n')) {
      const item = /^\s*-\s+(.*)$/.exec(line);
      if (item) {
        if (props.length) {
          const last = props[props.length - 1];
          last[1] = last[1] ? last[1] + ', ' + item[1] : item[1];
        }
        continue;
      }
      const kv = /^\s*([\w.-]+):\s*(.*)$/.exec(line);
      if (kv) props.push([kv[1], kv[2].replace(/^["']|["']$/g, '')]);
    }
    return [props.filter(([, v]) => v), md.slice(m[0].length)];
  }

  function safeDecode(text) {
    try { return decodeURIComponent(text); } catch (err) { return text; }
  }

  /* Řádkový text. Odkazy a kód se vyrobí napřed a schovají za zástupné
     značky, aby se jim dovnitř nepletlo tučné písmo ani hvězdičky z adres. */
  function inline(text, ctx) {
    const keep = [];
    const put = (html) => '\u0000' + (keep.push(html) - 1) + '\u0000';
    const link = (path, label, heading) =>
      put('<a class="vault-link' + (path || heading ? '' : ' missing') + '" data-note="' +
          esc(path || '') + '" data-heading="' + esc(heading || '') + '">' + esc(label) + '</a>');
    const ext = (url, label) =>
      put('<a class="vault-ext" data-href="' + esc(url) + '">' + esc(label || url) + '</a>');

    let s = String(text).replace(/\u0000/g, '');
    s = s.replace(/`([^`\n]+)`/g, (_m, code) => put('<code>' + esc(code) + '</code>'));
    // ![[obrázek.png]] a [[poznámka#nadpis|popisek]]
    s = s.replace(/(!?)\[\[([^\]\n]+?)\]\]/g, (_m, bang, inner) => {
      const [target, ...alias] = inner.split('|');
      const name = target.trim();
      const [note, ...hash] = name.split('#');
      if (bang && IMAGE_EXT.test(note.trim())) {
        const url = ctx.image(note.trim());
        return url ? put('<img class="vault-img" alt="' + esc(note.trim()) + '" src="' + esc(url) + '">')
                   : put('<span class="vault-link missing">' + esc(name) + '</span>');
      }
      const path = note.trim() ? ctx.resolve(note.trim()) : ctx.current;
      return link(path, alias.join('|').trim() || name, hash.join('#').trim());
    });
    // [popisek](adresa) a ![popisek](obrázek)
    s = s.replace(/(!?)\[([^\]\n]*)\]\(<?([^)\s>]+)>?(?:\s+"[^"]*")?\)/g, (_m, bang, label, href) => {
      if (/^(https?:|mailto:)/i.test(href)) return ext(href, label);
      // javascript:, data: a jiná schémata: jen text, nic klikacího.
      if (/^[a-z][a-z0-9+.-]*:/i.test(href)) return put(esc(label || href));
      const [file, ...hash] = safeDecode(href).split('#');
      if (bang && IMAGE_EXT.test(file)) {
        const url = ctx.image(file);
        return url ? put('<img class="vault-img" alt="' + esc(label) + '" src="' + esc(url) + '">')
                   : put(esc(label || file));
      }
      const path = file ? ctx.resolve(file) : ctx.current;
      return link(path, label || file, hash.join('#'));
    });
    s = s.replace(/\bhttps?:\/\/[^\s<>()\u0000]*[^\s<>().,;:!?'"\u0000]/g, (url) => ext(url));

    s = esc(s)
      .replace(/\*\*(?=\S)([^\n]*?\S)\*\*/g, '<strong>$1</strong>')
      .replace(/(^|[^\w])__(?=\S)([^\n]*?\S)__(?!\w)/g, '$1<strong>$2</strong>')
      .replace(/(^|[^*\w])\*(?=[^\s*])([^*\n]*?[^\s*])\*(?!\*)/g, '$1<em>$2</em>')
      .replace(/(^|[^_\w])_(?=[^\s_])([^_\n]*?[^\s_])_(?![_\w])/g, '$1<em>$2</em>')
      .replace(/~~(?=\S)([^\n]*?\S)~~/g, '<del>$1</del>')
      .replace(/==(?=\S)([^\n]*?\S)==/g, '<mark>$1</mark>')
      .replace(/(^|\s)#([\p{L}\p{N}_/-]*\p{L}[\p{L}\p{N}_/-]*)/gu, '$1<span class="vault-tag">#$2</span>');
    return s.replace(/\u0000(\d+)\u0000/g, (_m, i) => keep[+i]);
  }

  /* Řádky odstavce. Poznámky od Clauda jsou v souboru zalomené po osmdesáti
     znacích, a kreslit každý konec řádku by text roztrhalo — spojují se proto
     mezerou jako v běžném Markdownu. Tvrdý zlom (dvě mezery nebo \ na konci)
     zůstane. */
  function joinLines(lines, ctx) {
    return lines.map((line, i) => {
      const last = i === lines.length - 1;
      const hard = !last && /( {2,}|\\)$/.test(line);
      const text = inline(line.replace(/( {2,}|\\)$/, '').trim(), ctx);
      return text + (last ? '' : hard ? '<br>' : ' ');
    }).join('');
  }

  // Řádek, kterým začíná jiný blok — ten už nepokračuje předchozí položku seznamu.
  function blockStart(line) {
    return /^\s*(`{3,}|~{3,}|#{1,6}\s|>)/.test(line) || /^\s*([-*_])(\s*\1){2,}\s*$/.test(line) ||
           LIST.test(line);
  }

  function listHtml(lines, ctx) {
    const items = [];
    for (const raw of lines) {
      const line = raw.replace(/\t/g, '    ');
      const m = LIST.exec(line);
      if (m) items.push({indent: m[1].length, ordered: /\d/.test(m[2]), text: [m[3]]});
      else if (items.length) items[items.length - 1].text.push(line.trim());
    }
    let pos = 0;
    function build(indent) {
      const tag = items[pos].ordered ? 'ol' : 'ul';
      const parts = [];
      while (pos < items.length && items[pos].indent >= indent) {
        const it = items[pos++];
        const task = /^\[([ xX])\]\s+/.exec(it.text[0]);
        if (task) it.text[0] = it.text[0].slice(task[0].length);
        let body = joinLines(it.text, ctx);
        if (task) {
          body = '<input type="checkbox" disabled' + (task[1] === ' ' ? '' : ' checked') + '>' + body;
        }
        if (pos < items.length && items[pos].indent > it.indent) body += build(items[pos].indent);
        const cls = task ? ' class="task' + (task[1] === ' ' ? '' : ' done') + '"' : '';
        parts.push('<li' + cls + '>' + body + '</li>');
      }
      return '<' + tag + '>' + parts.join('') + '</' + tag + '>';
    }
    return items.length ? build(items[0].indent) : '';
  }

  function tableHtml(lines, ctx) {
    const cells = (l) => l.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim());
    const head = cells(lines[0]);
    let html = '<table><thead><tr>' + head.map((c) => '<th>' + inline(c, ctx) + '</th>').join('') +
               '</tr></thead><tbody>';
    for (const row of lines.slice(2)) {
      html += '<tr>' + cells(row).map((c) => '<td>' + inline(c, ctx) + '</td>').join('') + '</tr>';
    }
    return html + '</tbody></table>';
  }

  function blocks(lines, ctx) {
    const out = [];
    let para = [];
    const flush = () => {
      if (para.length) out.push('<p>' + joinLines(para, ctx) + '</p>');
      para = [];
    };
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      let m;
      if ((m = /^\s*(`{3,}|~{3,})/.exec(line))) {
        flush();
        const fence = m[1];
        const code = [];
        for (i++; i < lines.length && !lines[i].trim().startsWith(fence); i++) code.push(lines[i]);
        out.push('<pre class="vault-code"><code>' + esc(code.join('\n')) + '</code></pre>');
        continue;
      }
      if (!line.trim()) { flush(); continue; }
      if ((m = /^(#{1,6})\s+(.*?)\s*#*\s*$/.exec(line))) {
        flush();
        const n = m[1].length;
        out.push('<h' + n + ' data-heading="' + esc(m[2]) + '">' + inline(m[2], ctx) + '</h' + n + '>');
        continue;
      }
      if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) { flush(); out.push('<hr>'); continue; }
      if (/^\s*>/.test(line)) {
        flush();
        const quote = [];
        for (; i < lines.length && /^\s*>/.test(lines[i]); i++) quote.push(lines[i].replace(/^\s*>\s?/, ''));
        i--;
        const call = /^\[!([\w-]+)\][+-]?\s*(.*)$/.exec(quote[0]);
        if (call) {
          const kind = call[1].toLowerCase();
          const title = call[2] || kind.charAt(0).toUpperCase() + kind.slice(1);
          out.push('<div class="vault-callout ' + esc(kind) + '"><div class="vault-callout-title">' +
                   inline(title, ctx) + '</div>' + blocks(quote.slice(1), ctx) + '</div>');
        } else {
          out.push('<blockquote>' + blocks(quote, ctx) + '</blockquote>');
        }
        continue;
      }
      const next = lines[i + 1] || '';
      if (line.includes('|') && next.includes('|') && /-/.test(next) && /^[\s|:-]+$/.test(next)) {
        flush();
        const rows = [line, next];
        for (i += 2; i < lines.length && lines[i].includes('|') && lines[i].trim(); i++) rows.push(lines[i]);
        i--;
        out.push(tableHtml(rows, ctx));
        continue;
      }
      if (LIST.test(line)) {
        flush();
        const start = i;
        // Odsazený i neodsazený řádek bez prázdného řádku před ním pokračuje
        // položku (i v Obsidianu); konec je až prázdný řádek nebo jiný blok.
        for (i++; i < lines.length && lines[i].trim() &&
                  (LIST.test(lines[i]) || /^\s{2,}\S/.test(lines[i]) || !blockStart(lines[i])); i++);
        out.push(listHtml(lines.slice(start, i), ctx));
        i--;
        continue;
      }
      para.push(line);
    }
    flush();
    return out.join('');
  }

  /* Poznámka → {props, html}. `ctx.resolve(jméno)` vrací cestu poznámky v trezoru
     (nebo ''), `ctx.image(jméno)` adresu obrázku, `ctx.current` cestu té, co se kreslí. */
  function render(md, ctx) {
    const [props, body] = frontmatter(String(md || '').replace(/\r\n?/g, '\n'));
    return {props, html: blocks(body.split('\n'), ctx)};
  }

  /* Hledání odkazů jako v Obsidianu: celá cesta, jinak jméno souboru — při
     shodě jmen vyhrává kratší cesta. */
  function indexOf(paths, strip) {
    const byPath = new Map();
    const byName = new Map();
    for (const path of paths) {
      const key = (strip ? path.replace(/\.md$/i, '') : path).toLowerCase();
      byPath.set(key, path);
      const name = key.split('/').pop();
      const prev = byName.get(name);
      if (!prev || path.length < prev.length) byName.set(name, path);
    }
    return (target) => {
      let key = String(target || '').trim().replace(/\\/g, '/').replace(/^(\.\.?\/)+/, '');
      if (strip) key = key.replace(/\.md$/i, '');
      key = key.toLowerCase();
      return byPath.get(key) || byName.get(key.split('/').pop()) || '';
    };
  }

  /* ── okno ─────────────────────────────────────────────────────────────── */

  let io = null;
  let box = null;
  let notes = [];
  let resolveNote = () => '';
  let resolveImage = () => '';
  let current = '';
  let back = [];
  let searchSeq = 0;
  let searchTimer = null;
  const openDirs = new Set(['memory']);

  // Který trezor: osobní (''), nebo firemní ('firma') — posílá se s každým dotazem.
  const vq = () => (io && io.vault ? '&vault=' + encodeURIComponent(io.vault) : '');
  const fold = (s) => String(s).normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
  const baseName = (path) => path.split('/').pop().replace(/\.md$/i, '');
  const q = (sel) => box.querySelector(sel);

  function plural(n) {
    if (n === 1) return '1 poznámka';
    if (n >= 2 && n <= 4) return n + ' poznámky';
    return n + ' poznámek';
  }

  function node(tag, cls, text) {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    if (text != null) el.textContent = text;
    return el;
  }

  function itemButton(path, snippet) {
    const b = node('button', 'vault-item' + (path === current ? ' on' : ''), baseName(path));
    b.dataset.path = path;
    b.title = path;
    if (snippet) b.appendChild(node('small', '', snippet));
    return b;
  }

  function treeOf(list) {
    const top = {path: '', dirs: new Map(), notes: []};
    for (const n of list) {
      let at = top;
      for (const part of n.path.split('/').slice(0, -1)) {
        if (!at.dirs.has(part)) {
          at.dirs.set(part, {name: part, path: (at.path ? at.path + '/' : '') + part,
                             dirs: new Map(), notes: []});
        }
        at = at.dirs.get(part);
      }
      at.notes.push(n.path);
    }
    return top;
  }

  function count(dir) {
    let n = dir.notes.length;
    for (const d of dir.dirs.values()) n += count(d);
    return n;
  }

  function drawDir(dir, parent) {
    const dirs = [...dir.dirs.values()].sort((a, b) => a.name.localeCompare(b.name, 'cs'));
    for (const d of dirs) {
      const det = node('details');
      det.dataset.dir = d.path;
      const sum = node('summary');
      sum.appendChild(node('span', '', d.name));
      sum.appendChild(node('span', 'vault-count', String(count(d))));
      det.appendChild(sum);
      const sub = node('div', 'vault-sub');
      det.appendChild(sub);
      // Obsah složky se kreslí až při otevření — skilly mají stovky poznámek.
      let drawn = false;
      const fill = () => {
        if (drawn || !det.open) return;
        drawn = true;
        drawDir(d, sub);
      };
      det.addEventListener('toggle', () => {
        if (det.open) openDirs.add(d.path); else openDirs.delete(d.path);
        fill();
      });
      det.open = openDirs.has(d.path);
      fill();
      parent.appendChild(det);
    }
    for (const path of dir.notes.sort((a, b) => a.localeCompare(b, 'cs'))) {
      parent.appendChild(itemButton(path));
    }
  }

  function renderList() {
    const list = q('.vault-list');
    const text = q('.vault-search').value.trim();
    list.textContent = '';
    clearTimeout(searchTimer);
    const seq = ++searchSeq;
    if (!text) {
      drawDir(treeOf(notes), list);
      return;
    }
    const words = fold(text).split(/\s+/).filter(Boolean);
    const named = notes.filter((n) => words.every((w) => fold(n.path).includes(w))).slice(0, 200);
    list.appendChild(node('div', 'vault-group', 'Názvy (' + named.length + ')'));
    for (const n of named) list.appendChild(itemButton(n.path));
    if (!named.length) list.appendChild(node('div', 'vault-empty', 'Žádný název nesedí.'));
    const inText = node('div');
    inText.appendChild(node('div', 'vault-group', 'V textu…'));
    list.appendChild(inText);
    searchTimer = setTimeout(async () => {
      let res;
      try {
        res = await io.api('vault-search?q=' + encodeURIComponent(text) + vq());
      } catch (err) {
        if (seq === searchSeq) inText.textContent = '';
        return;
      }
      if (seq !== searchSeq || !box) return;
      const seen = new Set(named.map((n) => n.path));
      const hits = (res.results || []).filter((r) => !seen.has(r.path));
      inText.textContent = '';
      inText.appendChild(node('div', 'vault-group', 'V textu (' + hits.length + ')'));
      for (const r of hits) inText.appendChild(itemButton(r.path, r.snippet));
    }, 250);
  }

  function markCurrent() {
    for (const b of box.querySelectorAll('.vault-item')) {
      b.classList.toggle('on', b.dataset.path === current);
    }
    if (q('.vault-search').value.trim()) return;
    // Otevřít složky až k poznámce, ať je v seznamu vidět, kde člověk je.
    const parts = current.split('/').slice(0, -1);
    let missing = false;
    for (let i = 1; i <= parts.length; i++) {
      const dir = parts.slice(0, i).join('/');
      if (!openDirs.has(dir)) { openDirs.add(dir); missing = true; }
    }
    if (missing) renderList();
    const item = [...box.querySelectorAll('.vault-item')].find((b) => b.dataset.path === current);
    if (item) item.scrollIntoView({block: 'nearest'});
  }

  function scrollToHeading(heading) {
    const want = fold(heading).trim();
    const h = [...q('.vault-md').querySelectorAll('[data-heading]')]
      .find((el) => fold(el.dataset.heading).trim() === want);
    if (h) h.scrollIntoView({block: 'start'});
  }

  async function load(path, {remember = true, heading = ''} = {}) {
    if (!path || !box) return;
    if (path === current && heading) return scrollToHeading(heading);
    let note;
    try {
      note = await io.api('vault-note?path=' + encodeURIComponent(path) + vq());
    } catch (err) {
      io.toast('Poznámku se nepodařilo otevřít: ' + err.message);
      return;
    }
    if (!box) return;
    if (remember && current && current !== note.path) back.push(current);
    current = note.path;
    const out = render(note.text, {resolve: resolveNote, image: resolveImage, current});

    q('.vault-path').textContent = note.path;
    q('.vault-date').textContent = note.mtime
      ? 'upraveno ' + new Date(note.mtime * 1000).toLocaleString('cs-CZ') : '';
    q('.vault-back').hidden = !back.length;
    const props = q('.vault-props');
    props.textContent = '';
    for (const [key, value] of out.props) {
      const chip = node('span', 'vault-prop');
      chip.appendChild(node('b', '', key + ': '));
      chip.appendChild(document.createTextNode(value));
      props.appendChild(chip);
    }
    const md = q('.vault-md');
    md.innerHTML = out.html || '<p class="vault-empty">(prázdná poznámka)</p>';
    if (note.truncated) md.appendChild(node('p', 'vault-empty', '… poznámka je dlouhá, zbytek je jen v souboru.'));

    const links = q('.vault-backlinks');
    links.textContent = '';
    if (note.backlinks && note.backlinks.length) {
      links.appendChild(node('div', 'set-title', 'Odkazuje sem (' + note.backlinks.length + ')'));
      for (const p of note.backlinks) links.appendChild(itemButton(p));
    }
    q('.vault-panel').scrollTop = 0;
    if (heading) scrollToHeading(heading);
    markCurrent();
  }

  function onKey(ev) {
    if (!box || ev.key !== 'Escape') return;
    ev.stopPropagation();
    const search = q('.vault-search');
    if (document.activeElement === search && search.value) {
      search.value = '';
      renderList();
      return;
    }
    close();
  }

  function close() {
    if (!box) return;
    document.removeEventListener('keydown', onKey, true);
    clearTimeout(searchTimer);
    box.remove();
    box = null;
    current = '';
    back = [];
  }

  function failure(text) {
    q('.vault-md').textContent = '';
    q('.vault-md').appendChild(node('p', 'vault-empty', text));
  }

  async function open(opts) {
    close();
    io = opts;
    box = node('div', 'onb set-modal vault-modal');
    box.innerHTML = `
      <div class="onb-box">
        <div class="onb-head">
          <span class="onb-mark"><svg class="ico" width="30" height="30"><use href="#i-book"/></svg></span>
          <div>
            <div class="onb-title">Obsidian</div>
            <div class="onb-sub">načítám…</div>
          </div>
          <span class="spacer"></span>
          <button class="btn ghost vault-app" hidden>Otevřít v Obsidianu</button>
          <button class="set-x vault-close" title="Zavřít (Esc)">×</button>
        </div>
        <div class="onb-body set-body">
          <nav class="set-nav vault-nav">
            <input class="vault-search" type="search" placeholder="Hledat v poznámkách…" autocomplete="off">
            <div class="vault-list"></div>
          </nav>
          <div class="set-panel vault-panel">
            <div class="vault-bar">
              <button class="btn ghost vault-back" title="Zpět" hidden>←</button>
              <span class="vault-path"></span>
              <span class="vault-date"></span>
            </div>
            <div class="vault-props"></div>
            <article class="vault-md"></article>
            <div class="vault-backlinks"></div>
          </div>
        </div>
      </div>`;
    document.body.appendChild(box);
    document.addEventListener('keydown', onKey, true);
    // Zavřít jen klikem, který na pozadí i začal: výběr textu tažením ven
    // z okna končí puštěním tlačítka na pozadí a prohlížeč to hlásí jako klik.
    let downOutside = false;
    box.addEventListener('pointerdown', (ev) => { downOutside = ev.target === box; });
    box.addEventListener('click', (ev) => { if (ev.target === box && downOutside) close(); });
    q('.vault-close').onclick = close;
    q('.vault-back').onclick = () => { if (back.length) load(back.pop(), {remember: false}); };
    q('.vault-search').addEventListener('input', renderList);
    const pick = (ev) => {
      const b = ev.target.closest('.vault-item');
      if (b) load(b.dataset.path);
    };
    q('.vault-list').addEventListener('click', pick);
    q('.vault-backlinks').addEventListener('click', pick);
    q('.vault-md').addEventListener('click', (ev) => {
      const a = ev.target.closest('a');
      if (!a) return;
      ev.preventDefault();
      if (a.classList.contains('vault-ext')) return io.openLink(a.dataset.href);
      if (a.dataset.note) return load(a.dataset.note, {heading: a.dataset.heading});
      if (a.dataset.heading) return scrollToHeading(a.dataset.heading);
      io.toast('Poznámka „' + a.textContent + '" v trezoru není.');
    });
    if (io.obsidian) {
      const app = q('.vault-app');
      app.hidden = false;
      app.onclick = () => io.openInObsidian(current);
    }

    let tree;
    try {
      tree = await io.api('vault-tree' + (io.vault ? '?vault=' + encodeURIComponent(io.vault) : ''));
    } catch (err) {
      if (!box) return;
      q('.onb-sub').textContent = '';
      failure('Náhled trezoru se nepodařilo načíst (' + err.message + '). ' +
              'Po aktualizaci ho hub umí až po restartu — na serveru nejpozději ráno.');
      return;
    }
    if (!box) return;
    notes = tree.notes || [];
    resolveNote = indexOf(notes.map((n) => n.path), true);
    const imageOf = indexOf(tree.images || [], false);
    resolveImage = (name) => {
      const path = imageOf(name);
      return path ? io.fileUrl(path) : '';
    };
    q('.onb-title').textContent = io.title || ('Osobní Obsidian — ' + (tree.name || 'trezor'));
    q('.onb-sub').textContent = tree.exists === false ? 'trezor nenalezen' : plural(notes.length);
    renderList();
    if (!notes.length) {
      failure(tree.exists === false ? 'Složka trezoru neexistuje.' : 'V trezoru zatím nejsou žádné poznámky.');
      return;
    }
    const has = (p) => notes.some((n) => n.path === p);
    const start = (opts.path && has(opts.path)) ? opts.path
      : ['memory/MEMORY.md', 'README.md'].find(has)
        || notes.slice().sort((a, b) => b.mtime - a.mtime)[0].path;
    load(start, {remember: false});
  }

  const api = {open, close, render, indexOf};
  root.HubVault = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
