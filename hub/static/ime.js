/* Composed input (diacritics), handled here instead of by xterm.js.
 *
 * WebKitGTK — the window host on Linux — routes every accented character
 * through the GTK input method, so "á" arrives as keydown(229) plus a
 * composition cycle rather than as a keypress. xterm.js answers that with two
 * competing senders:
 *
 *   1. CompositionHelper.keydown() → _handleAnyTextareaChanges(), which diffs
 *      the hidden textarea and sends whatever is new;
 *   2. compositionend → _finalizeComposition(), which sends
 *      textarea.value.substring(start) — to the END of the value.
 *
 * Neither of them ever empties that textarea, so it grows for the whole
 * session, and the two setTimeout(0) chains only stay in step while you type
 * slowly. Type at normal speed and they overlap: the offsets drift and sender
 * 2 ships the entire accumulated tail, which is why "rychlá" used to arrive as
 * "rychlljak máme ten hub tak chci rychl".
 *
 * So the composition events never reach xterm at all. We capture them on the
 * terminal element — an ancestor of the textarea, which is what makes capture
 * genuinely run first (listeners on the target itself fire in registration
 * order, capture flag or not) — send exactly ev.data once, and keep the
 * textarea empty so nothing can accumulate.
 */
'use strict';

(function (global) {

  /* Take over composed input for one terminal. Returns a teardown function.
     sendData(text) gets each committed string exactly once. */
  function install(term, sendData) {
    const root = term.element;
    const ta = term.textarea;
    if (!root || !ta) return () => {};

    const screen = root.querySelector('.xterm-screen') || root;
    const preview = document.createElement('div');
    preview.className = 'ime-preview';
    preview.hidden = true;
    screen.appendChild(preview);

    let composing = false;
    let settling = 0;   // timer id while a commit is still settling

    const swallow = (ev) => ev.stopImmediatePropagation();

    /* xterm draws its own composition overlay, but it only knows where the
       cursor is through internals we would rather not reach into. Cell size
       from the screen box and the cursor from the public buffer API gets the
       preview to the same place without any of that. */
    function showPreview(text) {
      if (!text) { preview.hidden = true; return; }
      const box = screen.getBoundingClientRect();
      const cw = box.width / Math.max(term.cols, 1);
      const ch = box.height / Math.max(term.rows, 1);
      const cur = term.buffer.active;
      preview.style.left = Math.round(cur.cursorX * cw) + 'px';
      preview.style.top = Math.round(cur.cursorY * ch) + 'px';
      preview.style.height = Math.round(ch) + 'px';
      preview.style.lineHeight = Math.round(ch) + 'px';
      preview.textContent = text;
      preview.hidden = false;
    }

    function onStart(ev) {
      swallow(ev);
      composing = true;
      ta.value = '';
      showPreview('');
    }

    function onUpdate(ev) {
      swallow(ev);
      showPreview(ev.data || '');
    }

    function onEnd(ev) {
      swallow(ev);
      composing = false;
      showPreview('');
      // ev.data is what the input method committed; the textarea is only a
      // fallback for the IMEs that leave data null.
      const text = ev.data != null ? ev.data : ta.value;
      ta.value = '';
      // WebKit likes to follow compositionend with an input event carrying the
      // same text as plain "insertText", which xterm would send a second time.
      clearTimeout(settling);
      settling = setTimeout(() => { settling = 0; ta.value = ''; }, 0);
      if (text) sendData(text);
    }

    // keyCode 229 means "the input method owns this key" — that is the keydown
    // xterm answers by diffing the textarea, i.e. the other half of the double
    // send. While composing, the same applies to every key: Enter and the
    // arrows are commands for the IM, not for the shell.
    function onKeyDown(ev) {
      if (ev.keyCode === 229 || composing) swallow(ev);
    }

    function onInput(ev) {
      if (composing || settling || ev.isComposing ||
          (ev.inputType || '').startsWith('insertComposition') ||
          (ev.inputType || '').startsWith('insertFromComposition')) {
        swallow(ev);
        ta.value = '';
      }
    }

    root.addEventListener('compositionstart', onStart, true);
    root.addEventListener('compositionupdate', onUpdate, true);
    root.addEventListener('compositionend', onEnd, true);
    root.addEventListener('keydown', onKeyDown, true);
    root.addEventListener('input', onInput, true);

    return function teardown() {
      root.removeEventListener('compositionstart', onStart, true);
      root.removeEventListener('compositionupdate', onUpdate, true);
      root.removeEventListener('compositionend', onEnd, true);
      root.removeEventListener('keydown', onKeyDown, true);
      root.removeEventListener('input', onInput, true);
      clearTimeout(settling);
      preview.remove();
    };
  }

  global.HubIME = { install };

})(window);
