/* Kopírování a vkládání v tabu.
 *
 * Naměřeno na WebKitGTK (hostitel okna na Linuxu): `navigator.clipboard`
 * existuje, ale bez uživatelského gesta hlásí NotAllowedError, a
 * `document.execCommand('copy')` vrací rovnou false. Prohlížeč tady tedy
 * spolehlivá cesta ke schránce není.
 *
 * Hub ale běží jako místní proces, takže na systémovou schránku dosáhne přímo
 * přes vlastní server (xclip / wl-clipboard / pbcopy / clip). Odtud se dá
 * obsloužit i PRIMARY.
 *
 * Myš se chová tak, jak je člověk na Linuxu zvyklý — označím a mám
 * zkopírováno, pravým vložím. Nabídka po pravém kliknutí tu byla, ale jen
 * překážela: vyskočila přesně tam, kam mířil kurzor, a stála mezi zvykem
 * a výsledkem.
 *
 *   Ctrl+C        výběr → schránka; bez výběru projde jako ^C (přerušení)
 *   Ctrl+Shift+C  vždycky kopírovat (konvence terminálu)
 *   Ctrl+V        vložit ze schránky
 *   označení myší → rovnou do schránky i do PRIMARY
 *   pravé         vložit ze schránky
 *   prostřední    vložit z PRIMARY
 */
'use strict';

(function (global) {

  /* install(term, io) — io: {read, write, notice}. Vrací teardown. */
  function install(term, io) {
    const root = term.element;
    if (!root) return () => {};

    async function copySelection(quiet) {
      const text = term.getSelection();
      if (!text) return false;
      await io.write(text, 'clipboard');
      // Výběr zrušíme schválně: dokud drží, bralo by Ctrl+C pořád jako
      // kopírování a nešlo by přerušit běžící příkaz.
      term.clearSelection();
      if (!quiet) io.notice('Zkopírováno (' + text.length + ' znaků)');
      return true;
    }

    async function pasteFrom(which) {
      let text;
      try {
        text = await io.read(which);
      } catch (err) {
        io.notice('Ze schránky se nepodařilo číst: ' + err.message);
        return;
      }
      // term.paste() obalí text bracketed-paste značkami, když je aplikace
      // v tabu čeká — bez toho by víceřádkový text odešel jako řada Enterů.
      if (text) term.paste(text);
    }

    function onKeyDown(ev) {
      const mod = ev.ctrlKey || ev.metaKey;
      if (!mod || ev.altKey) return;
      const key = (ev.key || '').toLowerCase();

      if (key === 'c') {
        if (!ev.shiftKey && !term.hasSelection()) return;   // ^C musí projít
        ev.preventDefault();
        ev.stopImmediatePropagation();
        copySelection(false);
      } else if (key === 'v') {
        ev.preventDefault();
        ev.stopImmediatePropagation();
        pasteFrom('clipboard');
      }
    }

    // Co označím, to mám zkopírované — bez sahání po klávesnici. Do PRIMARY
    // kvůli prostřednímu tlačítku, do schránky kvůli všemu ostatnímu.
    // Doběh po tažení: onSelectionChange se během tahu sype.
    let selTimer = null;
    const offSelection = term.onSelectionChange(() => {
      clearTimeout(selTimer);
      selTimer = setTimeout(() => {
        const text = term.getSelection();
        if (!text) return;
        io.write(text, 'primary').catch(() => {});
        io.write(text, 'clipboard').catch(() => {});
      }, 150);
    });

    function onMouseDown(ev) {
      if (ev.button === 1) {                 // prostřední = vložit z PRIMARY
        ev.preventDefault();
        ev.stopImmediatePropagation();
        pasteFrom('primary');
      } else if (ev.button === 2) {          // pravé = vložit ze schránky
        // Chytáme už mousedown: kdyby se čekalo na contextmenu, stihl by
        // xterm začít výběr a kliknutí by zrušilo, co bylo označené.
        ev.preventDefault();
        ev.stopImmediatePropagation();
        pasteFrom('clipboard');
      }
    }

    // Nabídku prohlížeče nechceme, pravé tlačítko už svou práci odvedlo.
    function onContextMenu(ev) {
      ev.preventDefault();
      ev.stopImmediatePropagation();
    }

    root.addEventListener('keydown', onKeyDown, true);   // dřív než xterm
    root.addEventListener('mousedown', onMouseDown, true);
    root.addEventListener('contextmenu', onContextMenu, true);

    return function teardown() {
      root.removeEventListener('keydown', onKeyDown, true);
      root.removeEventListener('mousedown', onMouseDown, true);
      root.removeEventListener('contextmenu', onContextMenu, true);
      clearTimeout(selTimer);
      if (offSelection && offSelection.dispose) offSelection.dispose();
    };
  }

  global.HubClipboard = { install };

})(window);
