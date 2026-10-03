/* Ukázkový plugin appky — zkopíruj složku, přejmenuj id a uprav.
 * API popisuje hub/static/pluginy.js (HubPluginy.registruj). */
HubPluginy.registruj('ukazka', (api) => {
  const vychozi = 'Shrň krátce v bodech, co jsme v tomhle chatu zatím udělali.';

  // 1) Tlačítko v Rychlých akcích: pošle Claudovi zprávu do aktivního chatu.
  api.akce({
    label: 'Shrň chat',
    title: 'Ukázkový plugin: pošle Claudovi prosbu o shrnutí',
    run: () => {
      if (!api.aktivniTab()) return api.toast('Nejdřív otevři chat.');
      api.posli(api.uloziste.get('prompt') || vychozi);
    },
  });

  // 2) Reakce na události: kolikrát Claude spustil nástroj (odznak vpravo dole).
  const odznak = document.createElement('div');
  odznak.className = 'ukazka-odznak';
  odznak.hidden = true;
  document.body.appendChild(odznak);
  let nastroju = 0;
  api.on('nastroj', (e) => {
    nastroju += 1;
    odznak.hidden = false;
    odznak.textContent = '🔧 ' + nastroju + ' · ' + (e.name || '').replace(/^mcp__/, '');
  });
  api.on('tab', () => { nastroju = 0; odznak.hidden = true; });

  // 3) Vlastní nastavení (Nastavení → Pluginy → Pro appku → Nastavení).
  api.nastaveni((box) => {
    const pole = document.createElement('input');
    pole.className = 'set-input';
    pole.value = api.uloziste.get('prompt') || vychozi;
    const ulozit = document.createElement('button');
    ulozit.className = 'btn primary';
    ulozit.textContent = 'Uložit';
    ulozit.onclick = () => { api.uloziste.set('prompt', pole.value.trim() || vychozi); api.toast('Uloženo.'); };
    box.append('Text, který pošle tlačítko „Shrň chat“:', pole, ulozit);
  });
});
