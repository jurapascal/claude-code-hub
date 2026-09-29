/* Claudíci — hřiště nad polem na psaní.
 *
 * Hlavní Claudík (jantarový) je Claude v tomhle tabu: když pracuje, chodí,
 * když čeká na odpověď, zvedne otazník a poskakuje, když je hotový, stojí.
 * Pomocníci (agenti, cteni.js) z něj vyskočí, pobíhají a občas si předají
 * papír; po práci doběhnou zpátky a papír s výsledkem mu donesou. Zpráva
 * z jiného chatu (nebo do něj) přiletí / odletí jako obálka.
 *
 * Je to jen ozdoba: nic se tu nerozhoduje, kreslí se podle událostí z čtení
 * a z bubliny. Hřiště se ukáže, když se něco děje, a po chvíli klidu zmizí.
 */
'use strict';

(function (global) {

  const DOMOV = 5;             // kde hlavní Claudík stojí (% šířky)
  const OD = 16, DO = 94;      // kudy pobíhají pomocníci
  const SCHOVAT_PO = 2500;     // ms klidu, než hřiště zmizí
  const PREDAVKA = [3500, 7000];  // jak často si pomocníci něco předají

  const nahoda = (a, b) => a + Math.random() * (b - a);

  function el(cls, text) {
    const node = document.createElement('div');
    node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  /* `svg(typ)` kreslí postavičku (cteni.js claudik) — tady se nekreslí znovu,
     ať Claudík vypadá na kartě agenta i na hřišti stejně. */
  function hriste(root, svg) {
    const box = el('hriste');
    box.hidden = true;
    box.setAttribute('aria-hidden', 'true');
    root.appendChild(box);

    let zivy = true;
    let praceOn = false, cekaOn = false;
    let letu = 0;                        // papíry a obálky ve vzduchu
    let schovatTimer = null, predavkaTimer = null;
    const pomocnici = new Map();         // id → figurka

    function figurka(typ, barva, cls) {
      const f = {el: el('cl-fig ' + (cls || '')), x: DOMOV, t: null, t2: null, konci: false};
      f.el.style.setProperty('--ag', barva || 'var(--accent)');
      f.bublina = el('cl-bublina');
      const telo = el('cl-postava');
      telo.innerHTML = svg(typ);
      f.el.append(f.bublina, telo);
      f.el.style.left = f.x + '%';
      box.appendChild(f.el);
      return f;
    }

    const hlavni = figurka('', 'var(--accent)', 'hlavni');

    function jdi(f, x, pak) {
      clearTimeout(f.t);
      const ms = Math.round(350 + Math.abs(x - f.x) * 26);
      f.el.style.transitionDuration = ms + 'ms';
      if (Math.abs(x - f.x) > 0.5) f.el.classList.toggle('vlevo', x < f.x);
      f.el.classList.add('chodi');
      f.x = x;
      f.el.style.left = x + '%';
      f.t = setTimeout(() => {
        f.el.classList.remove('chodi');
        if (pak && zivy) pak();
      }, ms);
    }

    function stuj(f) {
      clearTimeout(f.t);
      clearTimeout(f.t2);
      f.el.classList.remove('chodi');
    }

    // Pomocník se toulá, dokud nemá hotovo (a nepředává zrovna papír).
    function toulej(f) {
      if (!zivy || f.konci || f.predava) return;
      jdi(f, nahoda(OD, DO), () => {
        f.t2 = setTimeout(() => toulej(f), nahoda(250, 1400));
      });
    }

    // Hlavní Claudík při práci přechází kolem svého místa.
    function prechazej() {
      if (!zivy || !praceOn || cekaOn) return;
      jdi(hlavni, nahoda(DOMOV, 30), () => {
        hlavni.t2 = setTimeout(prechazej, nahoda(400, 1600));
      });
    }

    /* Papír letí od jedné postavičky ke druhé (nebo za okraj a zpoza něj). */
    function papir(odX, kamX, znak, pak) {
      const p = el('cl-papir');
      const vnitrek = el('cl-papir-v', znak || '');
      if (!znak) vnitrek.classList.add('list');
      p.appendChild(vnitrek);
      p.style.left = odX + '%';
      box.appendChild(p);
      letu++;
      ukaz();
      const ms = Math.round(450 + Math.abs(kamX - odX) * 9);
      p.style.transitionDuration = ms + 'ms';
      vnitrek.style.animationDuration = ms + 'ms';
      requestAnimationFrame(() => requestAnimationFrame(() => { p.style.left = kamX + '%'; }));
      setTimeout(() => {
        p.remove();
        letu--;
        if (zivy && pak) pak();
        moznaSchovat();
      }, ms + 30);
    }

    function radost(f) {
      f.el.classList.remove('radost');
      void f.el.offsetWidth;            // animace znovu od začátku
      f.el.classList.add('radost');
      setTimeout(() => f.el.classList.remove('radost'), 700);
    }

    /* Dva pomocníci si občas něco předají — ten první doběhne k druhému. */
    function naplanujPredavku() {
      clearTimeout(predavkaTimer);
      predavkaTimer = setTimeout(() => {
        if (!zivy) return;
        const volni = [...pomocnici.values()].filter((f) => !f.konci && !f.predava);
        if (volni.length >= 2) {
          const a = volni[Math.floor(Math.random() * volni.length)];
          const b = volni.filter((f) => f !== a)[Math.floor(Math.random() * (volni.length - 1))];
          a.predava = true;
          stuj(a);
          jdi(a, Math.max(OD, Math.min(DO, b.x + (a.x < b.x ? -5 : 5))), () => {
            papir(a.x, b.x, '', () => {
              radost(b);
              a.predava = false;
              toulej(a);
            });
          });
        }
        if (pomocnici.size >= 2) naplanujPredavku();
      }, nahoda(...PREDAVKA));
    }

    function nekdoJe() {
      return praceOn || cekaOn || pomocnici.size > 0 || letu > 0;
    }

    function ukaz() {
      clearTimeout(schovatTimer);
      schovatTimer = null;
      if (box.hidden) {
        box.hidden = false;
        root.classList.add('hriste-on');
      }
    }

    function moznaSchovat() {
      if (nekdoJe() || schovatTimer) return;
      schovatTimer = setTimeout(() => {
        schovatTimer = null;
        if (nekdoJe() || !zivy) return;
        box.hidden = true;
        root.classList.remove('hriste-on');
      }, SCHOVAT_PO);
    }

    return {
      /* Claude pracuje / přestal. */
      prace(on) {
        if (!zivy || praceOn === !!on) return;
        praceOn = !!on;
        if (praceOn) {
          ukaz();
          hlavni.el.classList.remove('hotovo');
          prechazej();
        } else {
          stuj(hlavni);
          jdi(hlavni, DOMOV, () => {
            hlavni.el.classList.remove('vlevo');
            if (!cekaOn) { hlavni.el.classList.add('hotovo'); radost(hlavni); }
          });
          moznaSchovat();
        }
      },
      /* Claude čeká na odpověď — otazník nad hlavou, poskakuje. */
      ceka(on) {
        if (!zivy || cekaOn === !!on) return;
        cekaOn = !!on;
        hlavni.el.classList.toggle('ceka', cekaOn);
        hlavni.bublina.textContent = cekaOn ? '?' : '';
        if (cekaOn) {
          ukaz();
          stuj(hlavni);
          hlavni.el.classList.remove('hotovo');
        } else {
          if (praceOn) prechazej();
          moznaSchovat();
        }
      },
      /* Nový pomocník. `tise` = byl tu už před otevřením tabu, bez výskoku. */
      pridej(id, typ, barva, tise) {
        if (!zivy || pomocnici.has(id)) return;
        ukaz();
        const f = figurka(typ, barva, 'pomocnik' + (tise ? '' : ' vznik'));
        f.x = tise ? nahoda(OD, DO) : hlavni.x;
        f.el.style.left = f.x + '%';
        pomocnici.set(id, f);
        if (!tise) radost(hlavni);
        setTimeout(() => toulej(f), tise ? 0 : 450);
        if (pomocnici.size === 2) naplanujPredavku();
      },
      /* Pomocník doběhl: s výsledkem k hlavnímu, s chybou jen odejde. */
      hotovo(id, stav, tise) {
        const f = pomocnici.get(id);
        if (!f || f.konci) return;
        f.konci = true;
        stuj(f);
        const pryc = () => {
          f.el.classList.add('odchazi');
          setTimeout(() => {
            f.el.remove();
            pomocnici.delete(id);
            moznaSchovat();
          }, 500);
        };
        if (tise || !zivy) { f.el.remove(); pomocnici.delete(id); moznaSchovat(); return; }
        if (stav !== 'hotovo') {
          f.el.classList.add(stav === 'chyba' ? 'chyba' : 'stop');
          setTimeout(pryc, 900);
          return;
        }
        f.el.classList.add('hotovo');
        jdi(f, Math.min(DO, hlavni.x + 7), () => {
          f.el.classList.add('vlevo');
          papir(f.x, hlavni.x, '', () => { radost(hlavni); setTimeout(pryc, 250); });
        });
      },
      /* Zpráva mezi chaty: `ven` odletí za okraj, `dovnitr` přiletí. */
      dopis(smer) {
        if (!zivy) return;
        if (smer === 'ven') papir(hlavni.x, 104, '✉');
        else papir(104, hlavni.x, '✉', () => radost(hlavni));
      },
      release() {
        zivy = false;
        clearTimeout(schovatTimer);
        clearTimeout(predavkaTimer);
        for (const f of [hlavni, ...pomocnici.values()]) stuj(f);
        box.remove();
        root.classList.remove('hriste-on');
      },
    };
  }

  global.HubClaudici = {hriste};
})(window);
