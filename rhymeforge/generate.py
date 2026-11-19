"""Build the GPU job for the week and run it on a backend.

Backends:
  kaggle  - push the job to a private Kaggle notebook on a free GPU, wait, download results
  local   - run the same job script on this machine (needs an NVIDIA GPU)
  manual  - write job.json + generate.py for you to run in Colab, then re-run the command
  offline - make placeholder images/clips/songs locally (no GPU, for testing the pipeline)
  api     - no GPU: free image API + local or ElevenLabs music (see freegen.py)
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
import zlib
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .script import ace_step_lyrics, lyric_lines
from .util import log, run, write_json

KERNEL_SRC = Path(__file__).parent / "kaggle_kernel" / "generate.py"
NEGATIVE = ("realistic photo, scary, creepy, dark, horror, distorted face, extra limbs, deformed, text, "
            "watermark, logo, blurry, low quality")


def seed_for(s: str) -> int:
    return zlib.crc32(s.encode()) % 2_000_000_000


def build_job(videos: list[dict], cfg: dict) -> dict:
    """videos: [{slug, script, package, storyboard}]"""
    g, v = cfg["generation"], cfg["video"]
    fps = 24
    frames = int(v["clip_seconds"] * fps) // 8 * 8 + 1  # LTX needs 8k+1 frames
    job = {
        "settings": {"image_model": g["image_model"], "video_model": g["video_model"],
                     "image_w": 1344, "image_h": 768, "clip_w": 832, "clip_h": 480,
                     "clip_frames": frames, "fps": fps, "clip_steps": 40,
                     "negative": NEGATIVE, "negative_video": NEGATIVE + ", jittery, flicker, morphing"},
        "images": [], "clips": [], "songs": [],
    }
    for vid in videos:
        slug = vid["slug"]
        for s in vid["storyboard"]["shots"]:
            if s["kind"] in ("ai_clip", "still"):
                iid = f"{slug}__{s['id']}"
                job["images"].append({"id": iid, "prompt": s["image_prompt"], "seed": seed_for(iid)})
                if s["kind"] == "ai_clip":
                    job["clips"].append({"id": iid, "image_id": iid, "prompt": s["motion_prompt"],
                                         "seed": seed_for(iid + "v")})
        tid = f"{slug}__thumb"
        job["images"].append({"id": tid, "prompt": f"{cfg['channel']['style_bible']}. {vid['package']['thumbnail_scene']}",
                              "seed": seed_for(tid)})
        job["songs"].append({"id": slug, "lyrics": ace_step_lyrics(vid["script"]),
                             "tags": vid["script"]["music_tags"], "duration": v["duration_s"],
                             "seed": seed_for(slug), "bpm": vid["script"].get("bpm", v["bpm"]),
                             "lines": [l["text"] for l in lyric_lines(vid["script"])],
                             "tunes": vid["script"].get("tunes"), "sections": vid["script"]["sections"]})
    return job


def run_job(job: dict, cfg: dict, work: Path, backend: str) -> Path:
    """Run the job and return the output directory."""
    out = work / "gpu_output"
    write_json(work / "job.json", job)
    if backend == "offline":
        offline_generate(job, out)
    elif backend == "api":
        from .freegen import api_generate
        api_generate(job, cfg, out)
    elif backend == "local":
        subprocess.run([sys.executable, str(KERNEL_SRC), str(work / "job.json"), str(out)], check=True)
    elif backend == "kaggle":
        run_on_kaggle(job, cfg, work, out)
    elif backend == "manual":
        shutil.copy2(KERNEL_SRC, work / "generate.py")
        if not (out / "errors.json").exists():
            raise SystemExit(
                f"Manual GPU step:\n  1. Upload {work/'generate.py'} and {work/'job.json'} to Colab (GPU runtime)\n"
                f"  2. Run: !RHYMEFORGE_INSTALL=1 python generate.py job.json out\n"
                f"  3. Zip 'out', download, and unzip it to {out}\n"
                f"  4. Re-run the same rhymeforge command; it resumes from here.")
    else:
        raise ValueError(f"unknown backend {backend}")
    return out


# ------------------------------------------------------------------- kaggle
def kaggle_user() -> str:
    import os
    if os.environ.get("KAGGLE_USERNAME"):
        return os.environ["KAGGLE_USERNAME"]
    p = Path.home() / ".kaggle" / "kaggle.json"
    if p.exists():
        return json.loads(p.read_text())["username"]
    raise RuntimeError("Kaggle credentials not found: set KAGGLE_USERNAME/KAGGLE_KEY or ~/.kaggle/kaggle.json")


def run_on_kaggle(job: dict, cfg: dict, work: Path, out: Path) -> None:
    if (out / "errors.json").exists():
        log("kaggle: output already downloaded, skipping GPU run")
        return
    if not shutil.which("kaggle"):
        raise RuntimeError("kaggle CLI not found: pip install 'rhymeforge[kaggle]'")
    user = kaggle_user()
    slug = cfg["generation"]["kaggle_kernel_slug"]
    kdir = work / "kaggle_kernel"
    kdir.mkdir(parents=True, exist_ok=True)
    src = KERNEL_SRC.read_text().replace("JOB = None  # replaced", f"JOB = json.loads({json.dumps(json.dumps(job))})  #", 1)
    (kdir / "generate.py").write_text(src)
    write_json(kdir / "kernel-metadata.json", {
        "id": f"{user}/{slug}", "title": slug, "code_file": "generate.py", "language": "python",
        "kernel_type": "script", "is_private": True, "enable_gpu": True, "enable_internet": True,
        "dataset_sources": [], "competition_sources": [], "kernel_sources": [], "model_sources": [],
    })
    log(f"kaggle: pushing job ({len(job['images'])} images, {len(job['clips'])} clips, {len(job['songs'])} songs)")
    run(["kaggle", "kernels", "push", "-p", str(kdir)])
    deadline = time.time() + cfg["generation"]["kaggle_timeout_hours"] * 3600
    status = ""
    while time.time() < deadline:
        time.sleep(60)
        res = subprocess.run(["kaggle", "kernels", "status", f"{user}/{slug}"], capture_output=True, text=True)
        status = res.stdout.lower()
        log(f"kaggle: {res.stdout.strip()}")
        if "complete" in status or "error" in status or "cancel" in status:
            break
    if "complete" not in status:
        log("kaggle: run did not complete cleanly; downloading whatever it produced")
    dl = work / "kaggle_download"
    run(["kaggle", "kernels", "output", f"{user}/{slug}", "-p", str(dl)])
    produced = dl / "out" if (dl / "out").exists() else dl
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(produced, out)
    if not (out / "errors.json").exists():
        write_json(out / "errors.json", [{"stage": "kaggle", "id": "*", "error": f"status: {status.strip()}"}])


# ------------------------------------------------------------------ offline
def _font(size: int):
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "DejaVuSans-Bold.ttf", "Arial Bold.ttf"):
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


def placeholder_image(path: Path, text: str, w: int, h: int, seed: int) -> None:
    hue = seed % 360
    img = Image.new("RGB", (w, h), _hsv(hue, 0.35, 0.98))
    d = ImageDraw.Draw(img)
    for k in range(6):
        r = 60 + k * 40
        cx, cy = (seed * (k + 3)) % w, (seed * (k + 7)) % h
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=_hsv((hue + 40 * k) % 360, 0.5, 0.95))
    d.text((40, h - 120), "PLACEHOLDER", font=_font(36), fill=(60, 60, 60))
    d.text((40, h - 70), text[:70], font=_font(28), fill=(30, 30, 30))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


def _hsv(h: float, s: float, v: float) -> tuple:
    import colorsys
    r, g, b = colorsys.hsv_to_rgb(h / 360, s, v)
    return int(r * 255), int(g * 255), int(b * 255)


def offline_generate(job: dict, out: Path) -> None:
    s = job["settings"]
    log("offline: generating PLACEHOLDER assets (no GPU)")
    for it in job["images"]:
        p = out / "images" / f"{it['id']}.png"
        if not p.exists():
            placeholder_image(p, it["prompt"].split(". ")[-1], s["image_w"] // 2, s["image_h"] // 2, it["seed"])
    for c in job["clips"]:
        p = out / "clips" / f"{c['id']}.mp4"
        if p.exists():
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        dur = s["clip_frames"] / s["fps"]
        run(["ffmpeg", "-y", "-loop", "1", "-i", str(out / "images" / f"{c['image_id']}.png"), "-t", f"{dur:.2f}",
             "-vf", f"scale={s['clip_w']}:{s['clip_h']},rotate=0.05*sin(2*PI*t):fillcolor=white,fps={s['fps']}",
             "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", str(p)])
    for song in job["songs"]:
        p = out / "songs" / f"{song['id']}.wav"
        if p.exists():
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        # A simple repeating melody so loudness/QA steps have something real to measure.
        notes = [262, 294, 330, 349, 392, 349, 330, 294]
        beat = 60 / 110
        expr = "+".join(
            f"0.3*sin(2*PI*{f}*t)*between(mod(t,{beat * len(notes):.4f}),{i * beat:.4f},{(i + 1) * beat - 0.05:.4f})"
            for i, f in enumerate(notes))
        run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"aevalsrc='{expr}':s=44100:d={song['duration']}",
             "-ac", "2", str(p)])
        # Fake transcript: lyric words spread evenly (real runs use faster-whisper).
        lyric_words = [w for line in song["lyrics"].splitlines() if not line.startswith("[") for w in line.split()]
        start, end = 2.0, song["duration"] - 3.0
        step = (end - start) / max(len(lyric_words), 1)
        words = [{"word": w, "start": round(start + i * step, 3), "end": round(start + (i + 0.8) * step, 3)}
                 for i, w in enumerate(lyric_words)]
        write_json(out / "transcripts" / f"{song['id']}.json", {"words": words})
    write_json(out / "errors.json", [])
