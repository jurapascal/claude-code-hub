/* Okna na potvrzení a zadání textu v appce — místo výchozích oken prohlížeče
 * („test.alba-rosa.cz říká…"), která vypadají cize, neumí styl appky a na
 * telefonu a v nainstalované appce se chovají jinak.
 *
 *   if (!(await HubDialog.confirm('Smazat poznámku?', {title: 'Smazat', ok: 'Smazat', danger: true}))) return;
 *   const jmeno = await HubDialog.prompt('Jak se má jmenovat?', 'Telefon', {title: 'Název'});   // null = zrušeno
 *
 * Enter potvrdí, Esc zruší, klik mimo okno zruší (jen když i začal mimo — výběr
 * textu tažením ven okno nezavře). Nebezpečné akce (smazat, odebrat…) mají
 * červené tlačítko a výchozí je „Zrušit".
 */
'use strict';

(function (global) {
  const NEBEZPECNE = /^(smazat|odebrat|odinstalovat|vymazat|odpojit|zahodit|přestat|opravdu smazat)/i;

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function otevri({title, text, ok, zrusit, danger, pole, vychozi}) {
    return new Promise((resolve) => {
      const predtim = document.activeElement;
      const wrap = el('div', 'hd-wrap');
      wrap.setAttribute('role', 'dialog');
      wrap.setAttribute('aria-modal', 'true');
      const box = el('div', 'hd-box');
      box.appendChild(el('div', 'hd-title', title));
      const telo = el('div', 'hd-text');
      // Odstavce podle prázdných řádků, jednoduché zalomení zůstává.
      String(text || '').split(/\n\s*\n/).forEach((p) => telo.appendChild(el('p', '', p.trim())));
      box.appendChild(telo);
      let input = null;
      if (pole) {
        input = el('input', 'hd-pole');
        input.type = 'text';
        input.value = vychozi || '';
        input.spellcheck = false;
        box.appendChild(input);
      }
      const btns = el('div', 'hd-btns');
      const no = el('button', 'btn ghost', zrusit);
      const yes = el('button', 'btn ' + (danger ? 'danger' : 'primary'), ok);
      btns.append(no, yes);
      box.appendChild(btns);
      wrap.appendChild(box);

      let hotovo = false;
      const konec = (val) => {
        if (hotovo) return;
        hotovo = true;
        document.removeEventListener('keydown', klavesy, true);
        wrap.classList.add('zavira');
        setTimeout(() => wrap.remove(), 120);
        try { if (predtim && predtim.focus) predtim.focus({preventScroll: true}); } catch (_) { /* nic */ }
        resolve(val);
      };
      const potvrd = () => konec(pole ? input.value : true);
      const zrus = () => konec(pole ? null : false);
      const klavesy = (ev) => {
        if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); zrus(); }
        else if (ev.key === 'Enter' && !(ev.target === no)) { ev.preventDefault(); ev.stopPropagation(); potvrd(); }
        else if (ev.key === 'Tab') {                       // fokus zůstává v okně
          const f = [...box.querySelectorAll('input, button')];
          const i = f.indexOf(document.activeElement);
          ev.preventDefault();
          f[(i + (ev.shiftKey ? -1 : 1) + f.length) % f.length].focus();
        }
      };
      document.addEventListener('keydown', klavesy, true);
      yes.onclick = potvrd;
      no.onclick = zrus;
      let zacalMimo = false;
      wrap.addEventListener('pointerdown', (ev) => { zacalMimo = ev.target === wrap; });
      wrap.addEventListener('click', (ev) => { if (ev.target === wrap && zacalMimo) zrus(); });
      document.body.appendChild(wrap);
      requestAnimationFrame(() => {
        const cil = input || (danger ? no : yes);
        cil.focus();
        if (input) input.select();
      });
    });
  }

  global.HubDialog = {
    /* Potvrzení. Vrací true / false. `text` může mít odstavce (prázdný řádek). */
    confirm(text, o = {}) {
      const ok = o.ok || 'OK';
      return otevri({title: o.title || 'Potvrď', text, ok, zrusit: o.zrusit || 'Zrušit',
                     danger: o.danger !== undefined ? o.danger : NEBEZPECNE.test(ok)});
    },
    /* Zadání textu. Vrací řetězec, nebo null při zrušení. */
    prompt(text, vychozi = '', o = {}) {
      return otevri({title: o.title || 'Zadej', text, ok: o.ok || 'OK', zrusit: o.zrusit || 'Zrušit',
                     pole: true, vychozi});
    },
  };
})(window);
