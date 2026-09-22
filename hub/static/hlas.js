/* Hlas — diktování do pole a předčítání odpovědí, česky (hub/hlas.py).
 *
 * Nahrává se přes Web Audio rovnou do WAV (16 kHz, mono): žádné kodeky, takže
 * to jde stejně v okně appky (WebKitGTK), v Chrome i v Safari na iPhonu.
 * Přepis i hlas dělá hub na svém stroji — na počítači, nebo na serveru —
 * a nic neodchází ven.
 *
 * Tlačítka se ukážou, jen když je hlas na stroji nainstalovaný (/api/hlas).
 */
'use strict';

(function (global) {
  const KLIC_AUTO = 'hub-hlas-predcitat';
  const MAX_NAHRAVKA_S = 5 * 60;
  const KUS = 500;                     // znaků na jedno předčítání (věty se nedělí)

  let io = null;                       // {api(name, body), url(name)}
  let stav = null;
  let stavP = null;

  function init(opts) { io = opts; }

  function ready() {
    if (!io) return Promise.resolve(false);
    if (!stavP) {
      stavP = io.api('hlas').then((s) => { stav = s; return !!s.ready; })
        .catch(() => { stav = {ready: false}; return false; });
    }
    return stavP;
  }

  function refresh() { stav = null; stavP = null; return ready(); }

  const umiMikrofon = () => !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);

  /* ── nahrávání ────────────────────────────────────────────────────────── */
  /* Zvukový kontext musí vzniknout a rozběhnout se přímo v kliknutí —
     prohlížeče (Safari, Chrome i WebKitGTK) ho jinak nechají uspaný a z
     mikrofonu nepřijde ani vzorek. Proto ho zakládá obsluha kliknutí dřív,
     než se čeká na povolení mikrofonu, a sem ho jen předá. */
  function kontext() {
    const Ctx = global.AudioContext || global.webkitAudioContext;
    const ctx = new Ctx();
    if (ctx.state === 'suspended') ctx.resume().catch(() => {});
    return ctx;
  }

  async function nahravat(ctx, naUroven) {
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        // Potlačení ozvěny je pro hovor, ne pro diktování — hlas jím zbytečně
        // trpí. Šum a hlasitost prohlížeč srovnat smí.
        audio: {channelCount: 1, echoCancellation: false, noiseSuppression: true,
                autoGainControl: true}});
    } catch (err) {
      ctx.close().catch(() => {});
      throw err;
    }
    if (ctx.state === 'suspended') await ctx.resume().catch(() => {});
    const src = ctx.createMediaStreamSource(stream);
    const proc = ctx.createScriptProcessor(4096, 1, 1);
    const kusy = [];
    proc.onaudioprocess = (ev) => {
      const data = ev.inputBuffer.getChannelData(0);
      kusy.push(new Float32Array(data));
      if (naUroven) {
        let sum = 0;
        for (let i = 0; i < data.length; i += 16) sum += data[i] * data[i];
        naUroven(Math.min(1, Math.sqrt(sum / (data.length / 16)) * 10));
      }
    };
    src.connect(proc);
    proc.connect(ctx.destination);
    const konec = () => {
      try { proc.disconnect(); src.disconnect(); } catch (_) { /* už je */ }
      stream.getTracks().forEach((t) => t.stop());
      ctx.close().catch(() => {});
    };
    return {
      stop() {
        konec();
        return wav16(kusy, ctx.sampleRate);
      },
      zrusit: konec,
      // Kolik vzorků už přišlo — podle toho se pozná mikrofon, který nic nedává.
      get vzorku() { let n = 0; for (const k of kusy) n += k.length; return n; },
    };
  }

  /* Nahrávka jako WAV 16 kHz mono pro Whisper. Prohlížeč nahrává na 44,1
     nebo 48 kHz a převzorkovat se musí s filtrem: prosté „každý třetí
     vzorek“ zvuk zkreslí (aliasing) a přepis pak plete slova. */
  async function wav16(kusy, rate) {
    let delka = 0;
    for (const k of kusy) delka += k.length;
    const vse = new Float32Array(delka);
    let o = 0;
    for (const k of kusy) { vse.set(k, o); o += k.length; }
    const cil = 16000;
    let pcm = vse;
    if (rate !== cil && vse.length) {
      pcm = null;
      const Off = global.OfflineAudioContext || global.webkitOfflineAudioContext;
      if (Off) {
        try {
          const n = Math.ceil(vse.length * cil / rate);
          const off = new Off(1, n, cil);
          const buf = off.createBuffer(1, vse.length, rate);
          buf.getChannelData(0).set(vse);
          const src = off.createBufferSource();
          src.buffer = buf;
          src.connect(off.destination);
          src.start(0);
          const out = await new Promise((hotovo, chyba) => {
            off.oncomplete = (ev) => hotovo(ev.renderedBuffer);
            const p = off.startRendering();
            if (p && p.then) p.then(hotovo, chyba);
          });
          pcm = out.getChannelData(0);
        } catch (_) { pcm = null; }
      }
      if (!pcm) {
        // Záloha: průměr přes okno — hrubý, ale pořád filtr.
        const krok = rate / cil;
        const n = Math.floor(vse.length / krok);
        pcm = new Float32Array(n);
        for (let i = 0; i < n; i++) {
          const a = Math.floor(i * krok), b = Math.min(vse.length, Math.floor((i + 1) * krok));
          let sum = 0;
          for (let j = a; j < b; j++) sum += vse[j];
          pcm[i] = sum / Math.max(1, b - a);
        }
      }
    }
    // Tichou nahrávku zesílit (nanejvýš 8×): Whisper slabý hlas snadno přeslechne.
    let spicka = 0;
    for (let i = 0; i < pcm.length; i++) { const a = Math.abs(pcm[i]); if (a > spicka) spicka = a; }
    const zisk = spicka > 0 ? Math.min(8, 0.9 / spicka) : 1;
    const n = pcm.length;
    const buf = new ArrayBuffer(44 + n * 2);
    const v = new DataView(buf);
    const str = (at, t) => { for (let i = 0; i < t.length; i++) v.setUint8(at + i, t.charCodeAt(i)); };
    str(0, 'RIFF'); v.setUint32(4, 36 + n * 2, true); str(8, 'WAVE');
    str(12, 'fmt '); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
    v.setUint32(24, cil, true); v.setUint32(28, cil * 2, true); v.setUint16(32, 2, true);
    v.setUint16(34, 16, true); str(36, 'data'); v.setUint32(40, n * 2, true);
    for (let i = 0; i < n; i++) {
      const x = Math.max(-1, Math.min(1, pcm[i] * zisk));
      v.setInt16(44 + i * 2, x < 0 ? x * 0x8000 : x * 0x7fff, true);
    }
    return new Uint8Array(buf);
  }

  function base64(bytes) {
    let s = '';
    for (let i = 0; i < bytes.length; i += 0x8000) {
      s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    }
    return btoa(s);
  }

  /* Tlačítko mikrofonu v poli: klik = začít, klik = hotovo a přepsat do pole.
     Esc během nahrávání ji zahodí. */
  function mic(btn, input, opts = {}) {
    if (!btn || !input) return;
    btn.hidden = true;
    if (!umiMikrofon()) return;
    // Přepis jde i bez hlasu na tomhle stroji — počítač ho pošle na server.
    ready().then((ok) => { btn.hidden = !(ok || (stav && stav.prepis)); });
    let rec = null, timer = null, start = 0, busy = false;
    const puvodni = input.placeholder;

    function uklid() {
      clearInterval(timer);
      timer = null;
      rec = null;
      btn.classList.remove('rec');
      btn.style.removeProperty('--uroven');
      input.placeholder = puvodni;
    }

    async function hotovo() {
      const r = rec;
      uklid();
      if (!r) return;
      const data = await r.stop();
      if (data.length < 44 + 16000) {           // pod půl vteřiny = omyl
        if (opts.notice) opts.notice('Nahrávka byla moc krátká.');
        return;
      }
      busy = true;
      btn.classList.add('busy');
      input.placeholder = 'Přepisuju…';
      try {
        const out = await io.api('hlas-prepis', {wav: base64(data)});
        const text = (out.text || '').trim();
        if (!text) {
          if (opts.notice) opts.notice('Nic jsem nezachytil — zkus to znovu a blíž k mikrofonu.');
        } else {
          const pred = input.value;
          const mezera = pred && !/\s$/.test(pred) ? ' ' : '';
          input.value = pred + mezera + text;
          input.dispatchEvent(new Event('input', {bubbles: true}));
          input.focus();
          input.setSelectionRange(input.value.length, input.value.length);
        }
      } catch (err) {
        if (opts.notice) opts.notice(err.message || 'Přepis se nepovedl.');
      }
      busy = false;
      btn.classList.remove('busy');
      input.placeholder = puvodni;
    }

    btn.addEventListener('click', async (ev) => {
      ev.preventDefault();
      if (busy) return;
      if (rec) return hotovo();
      const ctx = kontext();                  // hned v kliknutí, ne až po await
      try {
        rec = await nahravat(ctx, (u) => btn.style.setProperty('--uroven', u.toFixed(2)));
      } catch (err) {
        rec = null;
        if (opts.notice) {
          opts.notice(err && err.name === 'NotAllowedError'
            ? 'Mikrofon není povolený — povol ho prohlížeči pro tuhle stránku.'
            : 'Mikrofon se nepodařilo zapnout: ' + (err.message || err));
        }
        return;
      }
      stopVse();                          // při diktování nemá nic mluvit
      start = Date.now();
      btn.classList.add('rec');
      input.placeholder = 'Poslouchám… klikni na mikrofon, až domluvíš (Esc zruší)';
      timer = setInterval(() => {
        const s = Math.round((Date.now() - start) / 1000);
        input.placeholder = `Poslouchám… ${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}` +
                            ' — klikni na mikrofon, až domluvíš (Esc zruší)';
        if (s >= MAX_NAHRAVKA_S) hotovo();
        // Po dvou vteřinách bez jediného vzorku mikrofon nic nedává —
        // lepší to říct hned, než nechat člověka mluvit do prázdna.
        if (s >= 2 && rec && rec.vzorku === 0) {
          rec.zrusit();
          uklid();
          if (opts.notice) opts.notice('Z mikrofonu nejde zvuk — zkontroluj, jestli ho prohlížeč smí používat.');
        }
      }, 500);
    });

    document.addEventListener('keydown', (ev) => {
      if (ev.key === 'Escape' && rec) {
        ev.preventDefault();
        ev.stopPropagation();
        rec.zrusit();
        uklid();
      }
    }, true);
  }

  /* ── předčítání ───────────────────────────────────────────────────────── */
  /* Z Markdownu jen to, co se dá říct: kód, adresy a značky pryč. */
  function plain(md) {
    return String(md || '')
      .replace(/```[\s\S]*?```/g, ' ')
      .replace(/`([^`\n]+)`/g, '$1')
      .replace(/!\[[^\]]*\]\([^)]*\)/g, ' ')
      .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
      .replace(/https?:\/\/\S+/g, 'odkaz')
      .replace(/^\s*\|?\s*:?-{2,}.*$/gm, ' ')
      .replace(/\|/g, ', ')
      .replace(/^\s{0,3}#{1,6}\s+/gm, '')
      .replace(/^\s*(?:[-*+]|\d+[.)])\s+/gm, '')
      .replace(/^\s*>\s?/gm, '')
      .replace(/(\*\*|__|\*|_|~~|==)(?=\S)([^\n]*?\S)\1/g, '$2')
      .replace(/[ \t]+/g, ' ')
      .replace(/\n{2,}/g, '\n')
      .trim();
  }

  function kusy(text) {
    const vety = text.match(/[^.!?…\n]+[.!?…]*\s*|\n/g) || [text];
    const out = [];
    let cur = '';
    for (const v of vety) {
      if ((cur + v).length > KUS && cur.trim()) { out.push(cur.trim()); cur = ''; }
      cur += v;
    }
    if (cur.trim()) out.push(cur.trim());
    return out;
  }

  async function zvuk(text) {
    const res = await fetch(io.url('hlas-rec'), {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({text})});
    if (!res.ok) {
      let msg = 'Předčítání se nepovedlo.';
      try { msg = (await res.json()).error || msg; } catch (_) { /* není JSON */ }
      throw new Error(msg);
    }
    return URL.createObjectURL(await res.blob());
  }

  let fronta = [];                     // [{text, btn}]
  let hraje = null;                    // {audio, btn, zruseno}

  function stopVse() {
    fronta = [];
    if (hraje) {
      hraje.zruseno = true;
      try { hraje.audio && hraje.audio.pause(); } catch (_) { /* nic */ }
      if (hraje.btn) hraje.btn.classList.remove('hraje');
      hraje = null;
    }
  }

  async function dalsi(notice) {
    if (hraje || !fronta.length) return;
    const {text, btn} = fronta.shift();
    const casti = kusy(plain(text));
    const ja = {audio: null, btn, zruseno: false};
    hraje = ja;
    if (btn) btn.classList.add('hraje');
    try {
      let pristi = casti.length ? zvuk(casti[0]) : null;
      for (let i = 0; i < casti.length && !ja.zruseno; i++) {
        const url = await pristi;
        pristi = i + 1 < casti.length ? zvuk(casti[i + 1]) : null;   // další se chystá, zatímco tahle hraje
        if (ja.zruseno) break;
        await new Promise((hotovo) => {
          const a = new Audio(url);
          ja.audio = a;
          a.onended = a.onerror = () => { URL.revokeObjectURL(url); hotovo(); };
          a.play().catch(() => hotovo());
        });
      }
    } catch (err) {
      if (notice) notice(err.message);
    }
    if (btn) btn.classList.remove('hraje');
    if (hraje === ja) hraje = null;
    dalsi(notice);
  }

  /* Přečíst text; `btn` se během čtení rozsvítí a druhým klikem čtení zastaví. */
  function speak(text, btn, notice) {
    if (hraje && btn && hraje.btn === btn) { stopVse(); return; }
    if (btn) stopVse();                 // klik na jiné tlačítko = tohle hned
    fronta.push({text, btn});
    dalsi(notice);
  }

  /* Tlačítko 🔊 k bublině s odpovědí. */
  function tlacitko(box, text, notice) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'hlas-cti';
    b.title = 'Přečíst nahlas';
    b.setAttribute('aria-label', 'Přečíst nahlas');
    b.innerHTML = '<svg class="ico hlas-i-cti"><use href="#i-speak"/></svg>' +
                  '<svg class="ico hlas-i-stop"><use href="#i-stop"/></svg>';
    b.hidden = true;
    ready().then((ok) => { b.hidden = !ok; });
    b.onclick = (ev) => { ev.stopPropagation(); speak(text, b, notice); };
    box.appendChild(b);
    return b;
  }

  const auto = {
    get() { try { return localStorage.getItem(KLIC_AUTO) === '1'; } catch (_) { return false; } },
    set(on) { try { localStorage.setItem(KLIC_AUTO, on ? '1' : '0'); } catch (_) { /* nic */ } },
  };

  global.HubHlas = {init, ready, refresh, mic, speak, stop: stopVse, tlacitko, auto, plain,
                    stav: () => stav};
})(window);
