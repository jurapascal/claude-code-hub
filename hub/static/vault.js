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
    lastText = note.text || '';
    noteMtime = note.mtime || 0;
    dirty = false;
    clearTimeout(saveTimer);
    q('.vault-modes').hidden = !canEdit();
    // Přejmenovat a smazat umí hub jen v osobním trezoru — do společných
    // zapisuje brána až po potvrzení karty.
    q('.vault-rename').hidden = q('.vault-del').hidden = !canEdit() || proposes();
    setState(mode === 'edit' ? (proposes() ? 'úpravy se potvrzují kartou' : 'ukládá se samo') : '');
    if (mode === 'edit' && editor) editor.setDoc(lastText);
    if (graph) graph.setFocus(current);

    q('.vault-panel').scrollTop = 0;
    if (heading) scrollToHeading(heading);
    markCurrent();
  }

  /* ── úpravy poznámky ──────────────────────────────────────────────────────
     Osobní trezor hub zapíše rovnou (ukládá se samo). Firemní a sdílený jsou
     jen ke čtení: z Uložit vznikne návrh a zapíše ho brána, až se potvrdí
     karta v hubu — stejně jako když poznámku připraví Claude. */

  let lastText = '';      // text otevřené poznámky, jak přišel ze serveru
  let mode = 'read';
  let editor = null;
  let saveTimer = null;
  let dirty = false;
  let noteMtime = 0;
  let graph = null;

  const canEdit = () => !!(root.HubEditor && root.HubEditor.available);
  const proposes = () => !!(io && io.vault);

  function setState(text, kind) {
    const el = q('.vault-state');
    el.textContent = text || '';
    el.className = 'vault-state' + (kind ? ' ' + kind : '');
  }

  function setMode(next) {
    if (!box || mode === next) return;
    if (next === 'edit' && !canEdit()) return io.toast('Editor se nenačetl — zkus obnovit stránku.');
    mode = next;
    for (const b of box.querySelectorAll('.vault-mode')) b.classList.toggle('on', b.dataset.mode === mode);
    q('.vault-md').hidden = mode === 'edit';
    q('.vault-edit').hidden = mode !== 'edit';
    q('.vault-backlinks').hidden = mode === 'edit';
    if (mode !== 'edit') {
      // Ve čtení má být vidět to, co se právě napsalo, ne text z načtení.
      if (editor) showRendered(editor.getDoc());
      if (dirty && !proposes()) saveNow();
      return;
    }
    mountEditor();
  }

  function showRendered(text) {
    const out = render(text, {resolve: resolveNote, image: resolveImage, current});
    const props = q('.vault-props');
    props.textContent = '';
    for (const [key, value] of out.props) {
      const chip = node('span', 'vault-prop');
      chip.appendChild(node('b', '', key + ': '));
      chip.appendChild(document.createTextNode(value));
      props.appendChild(chip);
    }
    q('.vault-md').innerHTML = out.html || '<p class="vault-empty">(prázdná poznámka)</p>';
  }

  function mountEditor() {
    const host = q('.vault-edit');
    if (editor) {
      editor.setDoc(lastText);
      return;
    }
    host.textContent = '';
    editor = root.HubEditor.create({
      parent: host,
      doc: lastText,
      readOnly: false,
      notes: () => notes.map((n) => n.path),
      image: (name) => resolveImage(name),
      onChange: () => {
        dirty = true;
        if (proposes()) setState('neuložené změny', 'warn');
        else { setState('ukládá se…'); scheduleSave(); }
      },
      onSave: () => saveNow(),
      onOpenNote: (target) => {
        const path = resolveNote(target);
        if (path) { setMode('read'); load(path); }
        else io.toast('Poznámka „' + target + '" v trezoru není.');
      },
    });
    // Editor vzniká ve chvíli, kdy je jeho místo teprve odkryté — bez přeměření
    // by první náhled počítal s nulovou výškou a nevykreslil nic.
    requestAnimationFrame(() => editor && editor.view.requestMeasure());
    setState(proposes() ? 'úpravy se potvrzují kartou' : 'ukládá se samo');
  }

  function scheduleSave() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(saveNow, 900);
  }

  async function saveNow() {
    clearTimeout(saveTimer);
    if (!editor || !dirty || !current) return;
    const text = editor.getDoc();
    const path = current;
    setState('ukládám…');
    let res;
    try {
      res = await io.api('vault-save' + (io.vault ? '?vault=' + encodeURIComponent(io.vault) : ''),
                         {path, text, mtime: noteMtime});
    } catch (err) {
      setState('neuloženo', 'bad');
      io.toast('Uložit se nepodařilo: ' + err.message);
      return;
    }
    if (!box) return;
    if (res.conflict) {
      setState('změnila se jinde', 'bad');
      io.toast('Poznámka se mezitím změnila jinde — otevři ji znovu a změny přenes ručně.');
      return;
    }
    if (!res.ok) {
      setState('neuloženo', 'bad');
      io.toast(res.error || 'Uložit se nepodařilo.');
      return;
    }
    dirty = false;
    lastText = text;
    if (res.proposal) {
      setState('čeká na potvrzení', 'warn');
      io.toast('Návrh je připravený — potvrď ho kartou „Nahrát".');
      return;
    }
    noteMtime = res.mtime || noteMtime;
    setState('uloženo');
    q('.vault-date').textContent = 'upraveno ' + new Date(noteMtime * 1000).toLocaleString('cs-CZ');
    if (res.created && !notes.some((n) => n.path === res.path)) {
      notes.push({path: res.path, mtime: noteMtime});
      resolveNote = indexOf(notes.map((n) => n.path), true);
      renderList();
      markCurrent();
    }
  }

  async function newNote() {
    const name = (prompt('Jméno nové poznámky (může být i se složkou):', '') || '').trim();
    if (!name) return;
    const path = name.replace(/\.md$/i, '') + '.md';
    if (notes.some((n) => n.path.toLowerCase() === path.toLowerCase())) {
      io.toast('Taková poznámka už tu je.');
      return load(path);
    }
    lastText = '# ' + baseName(path) + '\n\n';
    current = path;
    noteMtime = 0;
    dirty = true;
    q('.vault-path').textContent = path;
    q('.vault-date').textContent = '';
    q('.vault-props').textContent = '';
    q('.vault-backlinks').textContent = '';
    q('.vault-modes').hidden = false;
    setMode('edit');
    if (editor) editor.setDoc(lastText);
    if (!proposes()) saveNow();
  }

  async function renameNote() {
    if (!current || proposes() || !canEdit()) return;
    const was = current;
    const name = (prompt('Nové jméno poznámky (může být i se složkou):',
                         was.replace(/\.md$/i, '')) || '').trim();
    if (!name || name === was.replace(/\.md$/i, '')) return;
    if (dirty) await saveNow();
    let res;
    try {
      res = await io.api('vault-rename', {path: was, to: name});
    } catch (err) {
      return io.toast('Přejmenovat se nepodařilo: ' + err.message);
    }
    if (!box) return;
    if (!res.ok) return io.toast(res.error || 'Přejmenovat se nepodařilo.');
    notes = notes.filter((n) => n.path !== was).concat([{path: res.path, mtime: noteMtime}]);
    resolveNote = indexOf(notes.map((n) => n.path), true);
    back = back.filter((p) => p !== was);
    current = res.path;
    renderList();
    await load(res.path, {remember: false});
    io.toast(res.relinked
      ? `Přejmenováno · odkazy opraveny v ${res.relinked} poznámkách`
      : 'Přejmenováno.');
  }

  async function deleteNote() {
    if (!current || proposes() || !canEdit()) return;
    const was = current;
    if (!confirm(`Smazat poznámku „${baseName(was)}“? Přesune se do koše trezoru (.trash).`)) return;
    clearTimeout(saveTimer);
    dirty = false;
    let res;
    try {
      res = await io.api('vault-delete', {path: was});
    } catch (err) {
      return io.toast('Smazat se nepodařilo: ' + err.message);
    }
    if (!box) return;
    if (!res.ok) return io.toast(res.error || 'Smazat se nepodařilo.');
    notes = notes.filter((n) => n.path !== was);
    resolveNote = indexOf(notes.map((n) => n.path), true);
    back = back.filter((p) => p !== was);
    current = '';
    renderList();
    io.toast('Poznámka je v koši trezoru (.trash).');
    const next = back.pop() || (notes[0] && notes[0].path);
    if (next) return load(next, {remember: false});
    setMode('read');
    q('.vault-path').textContent = '';
    q('.vault-md').textContent = '';
    q('.vault-rename').hidden = q('.vault-del').hidden = true;
  }

  /* ── graf trezoru ─────────────────────────────────────────────────────── */

  async function toggleGraph() {
    if (graph) return closeGraph();
    if (!root.HubGraph) return io.toast('Graf se nenačetl — zkus obnovit stránku.');
    const wrap = q('.vault-graph');
    wrap.hidden = false;
    q('.vault-graph-btn').classList.add('on');
    let data;
    try {
      data = await io.api('vault-graph' + (io.vault ? '?vault=' + encodeURIComponent(io.vault) : ''));
    } catch (err) {
      wrap.hidden = true;
      q('.vault-graph-btn').classList.remove('on');
      io.toast('Graf se nepodařilo načíst: ' + err.message);
      return;
    }
    if (!box || wrap.hidden) return;
    graph = root.HubGraph.render(q('.graph-mount'), data, {
      onOpenNote: (path) => { closeGraph(); load(path); },
    });
    graph.setFocus(current);
    graphPanel(data);
  }

  function closeGraph() {
    if (graph) graph.destroy();
    graph = null;
    if (!box) return;
    q('.vault-graph').hidden = true;
    q('.graph-side').textContent = '';
    q('.vault-graph-btn').classList.remove('on');
  }

  function graphPanel(data) {
    const side = q('.graph-side');
    side.textContent = '';
    const opts = graph.options();
    let counts = () => {};          // přepíše se, až bude kam počty psát
    const section = (title) => {
      const el = node('div', 'graph-section');
      el.appendChild(node('div', 'graph-title', title));
      side.appendChild(el);
      return el;
    };
    const slider = (parent, label, key, min, max, step) => {
      const row = node('label', 'graph-row');
      row.appendChild(node('span', '', label));
      const input = document.createElement('input');
      input.type = 'range';
      input.min = min; input.max = max; input.step = step;
      input.value = opts[key];
      input.addEventListener('input', () => {
        graph.setOption(key, parseFloat(input.value));
        counts();
      });
      row.appendChild(input);
      parent.appendChild(row);
    };
    const toggle = (parent, label, key, invert) => {
      const row = node('label', 'graph-row graph-check');
      const input = document.createElement('input');
      input.type = 'checkbox';
      input.checked = invert ? !opts[key] : !!opts[key];
      input.addEventListener('change', () => {
        graph.setOption(key, invert ? !input.checked : input.checked);
        counts();
      });
      row.appendChild(input);
      row.appendChild(node('span', '', label));
      parent.appendChild(row);
    };

    const filters = section('Filtry');
    const find = document.createElement('input');
    find.type = 'search';
    find.className = 'vault-search graph-find';
    find.placeholder = 'Hledat v grafu…';
    find.addEventListener('input', () => { graph.setFilter(find.value); counts(); });
    filters.appendChild(find);
    toggle(filters, 'Poznámky bez odkazů', 'showOrphans');
    toggle(filters, 'Nenalezené odkazy', 'hideUnresolved', true);
    const local = node('label', 'graph-row');
    local.appendChild(node('span', '', 'Okolí otevřené'));
    const depth = document.createElement('select');
    for (const [v, t] of [[0, 'celý trezor'], [1, '1 krok'], [2, '2 kroky'], [3, '3 kroky']]) {
      const o = document.createElement('option');
      o.value = String(v); o.textContent = t;
      depth.appendChild(o);
    }
    depth.value = String(opts.localDepth || 0);
    depth.addEventListener('change', () => {
      graph.setFocus(current);
      graph.setOption('localDepth', parseInt(depth.value, 10));
      counts();
    });
    local.appendChild(depth);
    filters.appendChild(local);

    const display = section('Zobrazení');
    slider(display, 'Velikost uzlů', 'nodeSizeMultiplier', 0.3, 3, 0.1);
    slider(display, 'Tloušťka čar', 'lineSizeMultiplier', 0.2, 4, 0.1);
    slider(display, 'Mizení textu', 'textFadeMultiplier', -1, 3, 0.1);
    toggle(display, 'Šipky', 'showArrow');

    const forces = section('Síly');
    slider(forces, 'Střed', 'centerStrength', 0, 3, 0.1);
    slider(forces, 'Odpuzování', 'repelStrength', 1, 40, 1);
    slider(forces, 'Síla odkazů', 'linkStrength', 0, 3, 0.1);
    slider(forces, 'Délka odkazů', 'linkDistance', 40, 600, 10);

    const groups = (data.settings && data.settings.groups) || [];
    const legend = section(groups.length ? 'Skupiny' : 'Barvy podle složek');
    const folders = [...new Set(data.nodes.filter((n) => !n.missing && n.path.includes('/'))
                                  .map((n) => n.path.split('/')[0]))].slice(0, 10);
    const items = groups.length ? groups.map((g) => [g.color, g.query])
                                : folders.map((f, i) => [null, f]);
    for (const [color, label] of items) {
      const row = node('div', 'graph-legend');
      const dot = node('span', 'graph-dot');
      dot.style.background = color || '';
      if (!color) dot.dataset.folder = label;
      row.appendChild(dot);
      row.appendChild(node('span', '', label));
      legend.appendChild(row);
    }

    const foot = node('div', 'graph-foot');
    const info = node('span', 'graph-count', '');
    const center = node('button', 'btn ghost', 'Vycentrovat');
    center.onclick = () => graph.center();
    foot.appendChild(info);
    foot.appendChild(center);
    side.appendChild(foot);
    counts = () => {
      const c = graph.counts();
      info.textContent = c.nodes + ' poznámek · ' + c.links + ' odkazů';
    };
    counts();
    // Barvy podle složek přiřazuje graf sám — legenda si je vezme z plátna.
    for (const dot of side.querySelectorAll('.graph-dot[data-folder]')) {
      const found = data.nodes.find((n) => n.path.split('/')[0] === dot.dataset.folder);
      dot.style.background = found ? graphColor(data, found) : '';
    }
  }

  function graphColor(data, node) {
    const folders = [...new Set(data.nodes.filter((n) => !n.missing && n.path.includes('/'))
                                 .map((n) => n.path.split('/')[0]))];
    const PALETTE = ['#e0843c', '#5aa9e6', '#44cf6e', '#c678dd', '#e5c07b',
                     '#e06c75', '#56b6c2', '#a3be8c', '#d19a66', '#7aa2f7'];
    const i = folders.indexOf(node.path.split('/')[0]);
    return i >= 0 ? PALETTE[i % PALETTE.length] : '#9fa3ad';
  }

  function onKey(ev) {
    if (!box || ev.key !== 'Escape') return;
    ev.stopPropagation();
    if (graph) return closeGraph();
    // Při psaní patří Escape editoru (zavře našeptávání nebo hledání), ne oknu.
    if (mode === 'edit' && editor && editor.view.hasFocus) return;
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
    // Rozepsanou změnu v osobním trezoru ještě uložit — okno se zavírá i Escapem.
    if (dirty && editor && !proposes()) saveNow();
    if (graph) { graph.destroy(); graph = null; }
    if (editor) { editor.destroy(); editor = null; }
    document.removeEventListener('keydown', onKey, true);
    clearTimeout(searchTimer);
    clearTimeout(saveTimer);
    box.remove();
    box = null;
    current = '';
    back = [];
    mode = 'read';
    lastText = '';
    dirty = false;
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
          <button class="btn ghost vault-new" title="Nová poznámka" hidden>+ Nová</button>
          <button class="btn ghost vault-rename" title="Přejmenovat poznámku" hidden>Přejmenovat</button>
          <button class="btn ghost vault-del" title="Smazat poznámku" hidden>Smazat</button>
          <button class="btn ghost vault-graph-btn" title="Graf poznámek">Graf</button>
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
              <span class="vault-state"></span>
              <span class="vault-date"></span>
              <div class="vault-modes" hidden>
                <button class="vault-mode on" data-mode="read">Čtení</button>
                <button class="vault-mode" data-mode="edit">Úpravy</button>
              </div>
            </div>
            <div class="vault-props"></div>
            <article class="vault-md"></article>
            <div class="vault-edit" hidden></div>
            <div class="vault-backlinks"></div>
          </div>
          <div class="vault-graph" hidden>
            <div class="graph-mount"></div>
            <aside class="graph-side"></aside>
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
    q('.vault-new').hidden = !canEdit();
    q('.vault-new').onclick = newNote;
    q('.vault-rename').onclick = renameNote;
    q('.vault-del').onclick = deleteNote;
    q('.vault-graph-btn').onclick = toggleGraph;
    for (const b of box.querySelectorAll('.vault-mode')) {
      b.onclick = () => setMode(b.dataset.mode);
    }
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
