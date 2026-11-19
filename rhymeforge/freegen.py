"""`api` backend: generate a job's assets without a GPU.

Images (generation.image_api):
  pollinations - free text-to-image API, no key (https://pollinations.ai)
  placeholder  - local coloured cards, for testing
  none         - generate nothing; put your own art (e.g. made in Canva) in <video>/art/<shot>.png

Music (generation.music_api):
  local        - free, on this CPU: a backing track (drums, bass, chords, glockenspiel + lead
                 playing the melody; the traditional tune for public-domain rhymes). Adds spoken-sung
                 vocals with Piper TTS if `piper` and a voice model (PIPER_VOICE) are installed;
                 otherwise the result is an instrumental sing-along with on-screen lyrics.
  elevenlabs   - sung vocals + backing track from the ElevenLabs Music API (paid; ELEVENLABS_API_KEY),
                 word timings from ElevenLabs speech-to-text.

The local music path places every lyric line on the beat itself, so its word timings are exact.
AI-video clips are not made here; storyboard clip shots fall back to animated stills.
"""
from __future__ import annotations

import math
import os
import random
import shutil
import subprocess
import tempfile
import time
import urllib.parse
import wave
from pathlib import Path

import numpy as np
import requests

from .generate import placeholder_image
from .hooks import syllables
from .util import log, run, words, write_json

SR = 44100
NOTE_IDX = {"C": 0, "C#": 1, "DB": 1, "D": 2, "D#": 3, "EB": 3, "E": 4, "F": 5, "F#": 6, "GB": 6,
            "G": 7, "G#": 8, "AB": 8, "A": 9, "A#": 10, "BB": 10, "B": 11}
CHORDS = {"C": (0, 4, 7), "F": (5, 9, 0), "G": (7, 11, 2), "Am": (9, 0, 4), "Dm": (2, 5, 9), "Em": (4, 7, 11)}
CHORD_PREF = {"C": 0.3, "F": 0.2, "G": 0.2, "Am": 0.1, "Dm": 0.0, "Em": 0.0}
PROGRESSION = ["C", "G", "Am", "F", "C", "F", "G", "C"]
PENTA = [0, 2, 4, 7, 9]  # C major pentatonic


def api_generate(job: dict, cfg: dict, out: Path) -> None:
    g = cfg["generation"]
    errors: list[dict] = []
    out.mkdir(parents=True, exist_ok=True)
    s = job["settings"]
    image_api = g.get("image_api", "pollinations")
    for it in job["images"]:
        p = out / "images" / f"{it['id']}.png"
        if p.exists() or image_api == "none":
            continue
        try:
            if image_api == "pollinations":
                pollinations_image(it["prompt"], p, s["image_w"], s["image_h"], it["seed"], g)
            else:
                placeholder_image(p, it["prompt"].split(". ")[-1], s["image_w"] // 2, s["image_h"] // 2, it["seed"])
        except Exception as e:  # noqa: BLE001 - one bad image must not stop the rest
            log(f"image {it['id']} failed ({e}); using a placeholder")
            errors.append({"stage": "image", "id": it["id"], "error": str(e)[:300]})
            placeholder_image(p, it["prompt"].split(". ")[-1], s["image_w"] // 2, s["image_h"] // 2, it["seed"])
    if job["clips"]:
        log(f"api backend makes no AI video clips; {len(job['clips'])} clip shots become animated stills")
    music_api = g.get("music_api", "local")
    for song in job["songs"]:
        p = out / "songs" / f"{song['id']}.wav"
        if p.exists():
            continue
        try:
            if music_api == "elevenlabs":
                elevenlabs_song(song, out, cfg)
            else:
                words_ = local_song(song, p, cfg)
                write_json(out / "transcripts" / f"{song['id']}.json", {"words": words_, "source": "planned"})
        except Exception as e:  # noqa: BLE001
            log(f"song {song['id']} failed: {e}")
            errors.append({"stage": "song", "id": song["id"], "error": str(e)[:500]})
    write_json(out / "errors.json", errors)


# ------------------------------------------------------------------ images
_last_call = [0.0]


def pollinations_image(prompt: str, dest: Path, w: int, h: int, seed: int, g: dict) -> Path:
    """Free text-to-image. Same seed + same style text keeps a series looking consistent."""
    url = (g.get("pollinations_url", "https://image.pollinations.ai/prompt/") + urllib.parse.quote(prompt[:1500]))
    params = {"width": w, "height": h, "seed": seed, "nologo": "true", "model": g.get("pollinations_model", "flux")}
    delay = 10.0
    for attempt in range(4):
        wait = float(g.get("image_min_interval_s", 6)) - (time.time() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()
        try:
            r = requests.get(url, params=params, timeout=180)
        except requests.RequestException as e:
            err = str(e)
        else:
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image/"):
                dest.parent.mkdir(parents=True, exist_ok=True)
                tmp = dest.with_suffix(".download")
                tmp.write_bytes(r.content)
                from PIL import Image
                Image.open(tmp).convert("RGB").save(dest)
                tmp.unlink()
                return dest
            err = f"HTTP {r.status_code}: {r.text[:200]}"
        log(f"pollinations attempt {attempt + 1} failed ({err}); retrying in {delay:.0f}s")
        time.sleep(delay)
        delay *= 2
    raise RuntimeError(f"pollinations failed: {err}")


# ------------------------------------------------------------- local music
def note_freq(name: str) -> float:
    name = name.strip().upper()
    pitch, octave = name[:-1], int(name[-1])
    midi = 12 * (octave + 1) + NOTE_IDX[pitch]
    return 440.0 * 2 ** ((midi - 69) / 12)


def parse_tune(tune: str) -> list[tuple[float, float]]:
    """'C4:1 G4:2' -> [(freq, beats)]"""
    out = []
    for tok in tune.split():
        n, b = tok.split(":")
        out.append((note_freq(n), float(b)))
    return out


def make_melody(text: str, seed: int, bars: list[str]) -> list[tuple[float, float]]:
    """A simple singable 8-beat phrase: one note per syllable (up to 15), last note held."""
    rng = random.Random(seed)
    n = max(3, min(15, sum(syllables(w) for w in words(text)) or 4))
    if n <= 7:
        durs = [1.0] * (n - 1) + [8.0 - (n - 1)]
    else:
        durs = [0.5, 0.5] * (n - 8) + [1.0] * (8 - (n - 8))
        durs = durs[: n - 1] + [8.0 - sum(durs[: n - 1])]
    deg = rng.choice([0, 2, 4])  # index into PENTA, around middle C..A
    notes = []
    for i, d in enumerate(durs):
        beat = sum(durs[:i])
        chord = CHORDS[bars[0] if beat < 4 else bars[1]]
        if i == len(durs) - 1:  # land on the chord root
            pc = chord[0]
        else:
            deg = max(0, min(len(PENTA) * 2 - 1, deg + rng.choice([-1, 0, 1, 1, -2, 2])))
            pc = PENTA[deg % len(PENTA)] + 12 * (deg // len(PENTA))
            if i % 2 == 0 and pc % 12 not in chord:  # strong beats on chord tones
                pc = min(chord, key=lambda c: abs(c - pc % 12)) + 12 * (pc // 12)
        notes.append((261.63 * 2 ** (pc / 12), d))
    return notes


def harmonize(notes: list[tuple[float, float]]) -> list[str]:
    """Pick a chord for each 4-beat bar that best fits the melody notes in it."""
    bars: list[str] = []
    for bar in (0, 1):
        weight = {c: CHORD_PREF[c] for c in CHORDS}
        t = 0.0
        for f, d in notes:
            if bar * 4 <= t < bar * 4 + 4:
                pc = round(12 * math.log2(f / 261.63)) % 12
                for c, tones in CHORDS.items():
                    if pc in tones:
                        weight[c] += d * (1.5 if pc == tones[0] else 1.0)
            t += d
        bars.append(max(weight, key=weight.get))
    return bars


def _env(n: int, attack: float, decay: float) -> np.ndarray:
    t = np.arange(n) / SR
    return np.minimum(1.0, t / max(attack, 1e-4)) * np.exp(-t / max(decay, 1e-4))


def _add(buf: np.ndarray, start_s: float, sig: np.ndarray) -> None:
    i = int(start_s * SR)
    if i >= len(buf):
        return
    j = min(len(buf), i + len(sig))
    buf[i:j] += sig[: j - i]


def _tone(f: float, dur: float, kind: str) -> np.ndarray:
    n = int(dur * SR)
    t = np.arange(n) / SR
    if kind == "glock":
        sig = np.sin(2 * np.pi * f * 2 * t) + 0.35 * np.sin(2 * np.pi * f * 2 * 2.76 * t) * np.exp(-t / 0.08)
        return sig * _env(n, 0.002, 0.45)
    if kind == "lead":  # soft whistle/flute with a little vibrato, holds for the note
        vib = 1 + 0.004 * np.sin(2 * np.pi * 5.5 * t)
        sig = np.sin(2 * np.pi * f * np.cumsum(vib) / SR) + 0.15 * np.sin(4 * np.pi * f * t)
        rel = np.clip((dur - t) / 0.06, 0, 1)
        return sig * np.minimum(1.0, t / 0.03) * rel
    if kind == "bass":
        sig = np.sin(2 * np.pi * f * t) + 0.3 * np.sin(4 * np.pi * f * t)
        return sig * _env(n, 0.005, 0.35)
    if kind == "pad":
        sig = sum(np.sin(2 * np.pi * f * k * t) / k for k in (1, 2, 3))
        att = np.minimum(1.0, t / 0.25)
        return sig * att * np.clip((dur - t) / 0.2, 0, 1)
    raise ValueError(kind)


def _kick() -> np.ndarray:
    n = int(0.25 * SR)
    t = np.arange(n) / SR
    freq = 50 + 110 * np.exp(-t / 0.04)
    return np.sin(2 * np.pi * np.cumsum(freq) / SR) * np.exp(-t / 0.09)


def _noise_hit(dur: float, decay: float, seed: int) -> np.ndarray:
    n = int(dur * SR)
    noise = np.random.default_rng(seed).uniform(-1, 1, n)
    noise = np.diff(noise, prepend=0)  # brighter
    return noise * _env(n, 0.001, decay)


def plan_song(song: dict) -> dict:
    """Line start times, melody notes and chords for the local backend."""
    bpm = float(song.get("bpm", 110))
    beat = 60.0 / bpm
    lines = song["lines"]
    tunes = song.get("tunes") or [None] * len(lines)
    intro_beats = 8
    plan = {"bpm": bpm, "beat": beat, "intro_bars": ["C", "G"], "outro_bars": ["F", "C"], "lines": []}
    for i, text in enumerate(lines):
        start = (intro_beats + 8 * i) * beat
        tune = tunes[i] if i < len(tunes) else None
        if tune:
            notes = parse_tune(tune)
            bars = harmonize(notes)
        else:
            bars = [PROGRESSION[(2 * i) % len(PROGRESSION)], PROGRESSION[(2 * i + 1) % len(PROGRESSION)]]
            notes = make_melody(text, song["seed"] + i, bars)
        plan["lines"].append({"text": text, "start": start, "notes": notes, "bars": bars})
    plan["duration"] = (intro_beats + 8 * len(lines) + 8) * beat + 1.5
    return plan


def word_times(line: dict, beat: float) -> list[dict]:
    """Spread a line's words over its notes by syllable count."""
    ws = words(line["text"])
    if not ws:
        return []
    syl = [syllables(w) for w in ws]
    total = sum(syl)
    onsets, t = [], line["start"]
    for _, d in line["notes"]:
        onsets.append((t, d * beat))
        t += d * beat
    out, cum = [], 0
    for w, s in zip(ws, syl):
        k0 = min(len(onsets) - 1, int(cum / total * len(onsets)))
        k1 = min(len(onsets) - 1, max(k0, int((cum + s) / total * len(onsets)) - 1))
        start = onsets[k0][0]
        end = onsets[k1][0] + min(onsets[k1][1], 2 * beat) * 0.9
        out.append({"word": w, "start": round(start, 3), "end": round(end, 3)})
        cum += s
    return out


def local_song(song: dict, dest: Path, cfg: dict) -> list[dict]:
    """Render the backing track (+ Piper vocals if available). Returns planned word timings."""
    plan = plan_song(song)
    beat = plan["beat"]
    n = int(plan["duration"] * SR)
    drums, bass, pad, glock, lead = (np.zeros(n) for _ in range(5))
    bars: list[str] = list(plan["intro_bars"]) + [b for ln in plan["lines"] for b in ln["bars"]] + plan["outro_bars"]
    kick, clap = _kick(), _noise_hit(0.2, 0.05, 1)
    for bi, chord in enumerate(bars):
        t0 = bi * 4 * beat
        tones = CHORDS[chord]
        root = 65.41 * 2 ** (tones[0] / 12)  # C2-based
        for b in range(4):
            tb = t0 + b * beat
            if b in (0, 2):
                _add(drums, tb, kick)
                _add(bass, tb, _tone(root, beat * 1.5, "bass"))
            else:
                _add(drums, tb, 0.5 * clap)
            for half in (0, 0.5):
                _add(drums, tb + half * beat, 0.07 * _noise_hit(0.05, 0.015, bi * 8 + b))
        for pc in tones:
            _add(pad, t0, _tone(261.63 * 2 ** (pc / 12) / 2, 4 * beat, "pad") / 3)
    for ln in plan["lines"]:
        t = ln["start"]
        for f, d in ln["notes"]:
            _add(glock, t, _tone(f, min(d * beat, 1.5), "glock"))
            _add(lead, t, _tone(f, d * beat * 0.95, "lead"))
            t += d * beat
    mix = 0.9 * drums + 0.5 * bass + 0.12 * pad + 0.22 * glock + 0.18 * lead
    vocals = piper_vocals(plan, cfg)
    if vocals is not None:
        mix = mix * 0.7 + vocals[:n] * 0.9
        mix -= 0.08 * lead  # the voice carries the line; keep the lead quieter
    fade = int(1.5 * SR)
    mix[-fade:] *= np.linspace(1, 0, fade)
    mix /= max(1e-6, np.max(np.abs(mix))) / 0.8
    write_wav(dest, mix)
    return [w for ln in plan["lines"] for w in word_times(ln, beat)]


def write_wav(path: Path, mono: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(mono, -1, 1) * 32767).astype("<i2")
    stereo = np.repeat(pcm[:, None], 2, axis=1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(stereo.tobytes())


def read_wav_mono(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float64) / 32768
        ch = w.getnchannels()
    return data.reshape(-1, ch).mean(axis=1) if ch > 1 else data


def piper_vocals(plan: dict, cfg: dict) -> np.ndarray | None:
    """Each line spoken by Piper TTS (free, offline), squeezed to fit its 2 bars and placed on the beat."""
    voice = cfg["generation"].get("piper_voice") or os.environ.get("PIPER_VOICE", "")
    exe = shutil.which("piper")
    if not exe or not voice or not Path(voice).exists():
        if voice or exe:
            log("piper vocals skipped: need both the `piper` command and a voice .onnx (PIPER_VOICE)")
        return None
    n = int(plan["duration"] * SR)
    out = np.zeros(n)
    slot = 7.5 * plan["beat"]
    with tempfile.TemporaryDirectory() as td:
        for i, ln in enumerate(plan["lines"]):
            raw, fit = Path(td) / f"{i}.wav", Path(td) / f"{i}_fit.wav"
            subprocess.run([exe, "--model", voice, "--output_file", str(raw)], input=ln["text"],
                           text=True, capture_output=True, check=True)
            dur = float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                             "-of", "default=nw=1:nk=1", str(raw)]).stdout)
            tempo = min(2.0, max(1.0, dur / slot))
            run(["ffmpeg", "-y", "-i", str(raw), "-af", f"atempo={tempo:.3f}", "-ar", str(SR), "-ac", "1",
                 "-c:a", "pcm_s16le", str(fit)])
            _add(out, ln["start"], read_wav_mono(fit))
    return out


# --------------------------------------------------------------- elevenlabs
EL_API = "https://api.elevenlabs.io/v1"


def elevenlabs_song(song: dict, out: Path, cfg: dict) -> None:
    key = cfg["keys"].get("elevenlabs") or os.environ.get("ELEVENLABS_API_KEY", "")
    if not key:
        raise RuntimeError("music_api is elevenlabs but ELEVENLABS_API_KEY is not set")
    headers = {"xi-api-key": key}
    beat = 60.0 / float(song.get("bpm", 110))
    sections = []
    for i, sec in enumerate(song.get("sections") or [{"type": "verse", "lines": song["lines"]}]):
        ms = int(min(120_000, max(3_000, len(sec["lines"]) * 8 * beat * 1000)))
        sections.append({"section_name": f"{sec['type'].title()} {i + 1}", "positive_local_styles": [],
                         "negative_local_styles": [], "duration_ms": ms, "lines": sec["lines"]})
    plan = {"positive_global_styles": [t.strip() for t in song["tags"].split(",") if t.strip()],
            "negative_global_styles": ["explicit", "dark", "aggressive", "heavy distortion"],
            "sections": sections}
    r = requests.post(f"{EL_API}/music", headers=headers, params={"output_format": "mp3_44100_128"},
                      json={"composition_plan": plan}, timeout=600)
    if r.status_code in (400, 422):
        log(f"ElevenLabs rejected the composition plan ({r.text[:200]}); retrying with a text prompt")
        prompt = f"{song['tags']}. A song for toddlers with these exact lyrics:\n{song['lyrics']}"
        r = requests.post(f"{EL_API}/music", headers=headers, params={"output_format": "mp3_44100_128"},
                          json={"prompt": prompt[:4000], "music_length_ms": int(song["duration"] * 1000)}, timeout=600)
    if r.status_code != 200:
        raise RuntimeError(f"ElevenLabs music error {r.status_code}: {r.text[:300]}")
    mp3 = out / "songs" / f"{song['id']}.mp3"
    mp3.parent.mkdir(parents=True, exist_ok=True)
    mp3.write_bytes(r.content)
    wav = out / "songs" / f"{song['id']}.wav"
    run(["ffmpeg", "-y", "-i", str(mp3), "-ar", str(SR), "-ac", "2", str(wav)])
    mp3.unlink()
    with wav.open("rb") as f:
        t = requests.post(f"{EL_API}/speech-to-text", headers=headers, timeout=600,
                          files={"file": (wav.name, f, "audio/wav")},
                          data={"model_id": "scribe_v1", "timestamps_granularity": "word"})
    if t.status_code == 200:
        ws = [{"word": w["text"], "start": w["start"], "end": w["end"]}
              for w in t.json().get("words", []) if w.get("type", "word") == "word"]
        write_json(out / "transcripts" / f"{song['id']}.json", {"words": ws, "source": "elevenlabs-stt"})
    else:
        log(f"ElevenLabs speech-to-text failed ({t.status_code}); lyrics will be spread evenly")
