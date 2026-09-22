"""
Hlas — pracovní proces. Běží v prostředí s faster-whisper a Piperem
(hub/hlas.py ho pouští tamním Pythonem), ne v hubu: hub sám zůstává na
holém Pythonu bez závislostí.

    python hlas_worker.py prepis <složka modelu>   < WAV  > text
    python hlas_worker.py rec <hlas.onnx>          < text > WAV

Proces na jedno použití: model se načte, udělá se jedna věc a proces
skončí — paměť za přepisem (kolem 1 GB) se vrátí hned, ne až při uspání
prostoru. Načtení z disku trvá vteřinu, dvě; u krátké zprávy to nevadí.
"""
import io
import sys
import wave


def prepis(model_dir):
    import numpy as np
    from faster_whisper import WhisperModel

    raw = sys.stdin.buffer.read()
    with wave.open(io.BytesIO(raw)) as wav:
        rate = wav.getframerate()
        channels = wav.getnchannels()
        width = wav.getsampwidth()
        frames = wav.readframes(wav.getnframes())
    if width != 2:
        raise SystemExit("Čekám 16bitové WAV.")
    audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    if rate != 16000:
        # Prohlížeč nahrává na 16 kHz sám; kdyby ne, převzorkuje se tady.
        n = int(len(audio) * 16000 / rate)
        audio = np.interp(np.linspace(0, len(audio) - 1, n),
                          np.arange(len(audio)), audio).astype(np.float32)

    model = WhisperModel(model_dir, device="cpu", compute_type="int8",
                         local_files_only=True)
    segments, _info = model.transcribe(
        audio, language="cs", beam_size=5, vad_filter=True,
        # Každá věta sama za sebe: navazování na předchozí text u krátkého
        # diktování nepomáhá a Whisper se jím umí zacyklit.
        condition_on_previous_text=False,
        # Slova, která Whisper jinak komolí. Hotwords jen napovídají — na
        # rozdíl od úvodního textu (initial_prompt) je nedopisuje do přepisu.
        hotwords="Claude, Claude Code, hub, commit, push, deploy, Obsidian, server")
    text = " ".join(s.text.strip() for s in segments).strip()
    sys.stdout.write(text)


def rec(voice_path):
    from piper import PiperVoice

    text = sys.stdin.buffer.read().decode("utf-8", "replace").strip()
    voice = PiperVoice.load(voice_path)
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        voice.synthesize_wav(text, wav)
    sys.stdout.buffer.write(out.getvalue())


if __name__ == "__main__":
    what, arg = sys.argv[1], sys.argv[2]
    {"prepis": prepis, "rec": rec}[what](arg)
