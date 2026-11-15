"""GPU generation job. Runs on a free Kaggle GPU (pushed automatically by
`rhymeforge`), on Colab, or on any local NVIDIA GPU:

    python generate.py job.json out_dir

Produces:
    out/images/<id>.png        keyframes / stills / thumbnails   (SDXL or FLUX)
    out/clips/<id>.mp4         image-to-video clips              (LTX-Video)
    out/songs/<id>.wav         songs with vocals                 (ACE-Step)
    out/transcripts/<id>.json  word timestamps of the vocals     (faster-whisper)
    out/errors.json            per-item failures (partial results are still returned)

Every item is wrapped in try/except so one failure doesn't lose the batch.
This file must stay self-contained: it is uploaded to Kaggle on its own.
"""
import gc
import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path

JOB = None  # replaced with the job dict when pushed to Kaggle

ERRORS = []


def sh(cmd):
    print("+", cmd, flush=True)
    subprocess.run(cmd, shell=True, check=False)


def install():
    sh("pip install -q -U diffusers transformers accelerate sentencepiece imageio imageio-ffmpeg faster-whisper")


def install_ace_step():
    sh("pip install -q git+https://github.com/ace-step/ACE-Step.git")


def free():
    import torch
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def dtype():
    import torch
    if torch.cuda.is_available() and torch.cuda.is_bf16_supported():
        return torch.bfloat16
    return torch.float16


def record(stage, item_id, exc):
    ERRORS.append({"stage": stage, "id": item_id, "error": repr(exc)})
    traceback.print_exc()


def gen_images(job, out):
    items = [i for i in job.get("images", []) if not (out / "images" / f"{i['id']}.png").exists()]
    if not items:
        return
    import torch
    from diffusers import AutoPipelineForText2Image
    s = job["settings"]
    model = s["image_model"]
    kw = {"torch_dtype": torch.float16}
    if "xl" in model.lower():
        kw["variant"] = "fp16"
        kw["use_safetensors"] = True
    if "flux" in model.lower():
        kw["torch_dtype"] = dtype()
    pipe = AutoPipelineForText2Image.from_pretrained(model, **kw)
    pipe.enable_model_cpu_offload()
    flux = "flux" in model.lower()
    for it in items:
        t = time.time()
        try:
            g = torch.Generator("cpu").manual_seed(int(it.get("seed", 0)))
            args = dict(prompt=it["prompt"], width=s["image_w"], height=s["image_h"], generator=g)
            if flux:
                args.update(num_inference_steps=4, guidance_scale=0.0)
            else:
                args.update(num_inference_steps=30, guidance_scale=6.0, negative_prompt=s["negative"])
            img = pipe(**args).images[0]
            p = out / "images" / f"{it['id']}.png"
            p.parent.mkdir(parents=True, exist_ok=True)
            img.save(p)
            print(f"image {it['id']} {time.time() - t:.0f}s", flush=True)
        except Exception as e:  # noqa: BLE001
            record("image", it["id"], e)
    del pipe
    free()


def gen_clips(job, out):
    items = [c for c in job.get("clips", []) if not (out / "clips" / f"{c['id']}.mp4").exists()]
    if not items:
        return
    import torch
    from diffusers import LTXImageToVideoPipeline
    from diffusers.utils import export_to_video, load_image
    s = job["settings"]
    pipe = LTXImageToVideoPipeline.from_pretrained(s["video_model"], torch_dtype=dtype())
    pipe.enable_model_cpu_offload()
    pipe.vae.enable_tiling()
    for c in items:
        t = time.time()
        try:
            src = out / "images" / f"{c['image_id']}.png"
            if not src.exists():
                raise FileNotFoundError(f"keyframe {src} missing")
            image = load_image(str(src)).resize((s["clip_w"], s["clip_h"]))
            g = torch.Generator("cpu").manual_seed(int(c.get("seed", 0)))
            frames = pipe(image=image, prompt=c["prompt"], negative_prompt=s["negative_video"],
                          width=s["clip_w"], height=s["clip_h"], num_frames=s["clip_frames"],
                          num_inference_steps=s.get("clip_steps", 40), generator=g).frames[0]
            p = out / "clips" / f"{c['id']}.mp4"
            p.parent.mkdir(parents=True, exist_ok=True)
            export_to_video(frames, str(p), fps=s["fps"])
            print(f"clip {c['id']} {time.time() - t:.0f}s", flush=True)
        except Exception as e:  # noqa: BLE001
            record("clip", c["id"], e)
            free()
    del pipe
    free()


def gen_songs(job, out):
    items = [s for s in job.get("songs", []) if not (out / "songs" / f"{s['id']}.wav").exists()]
    if not items:
        return
    install_ace_step()
    import torch
    from acestep.pipeline_ace_step import ACEStepPipeline
    bf16 = torch.cuda.is_available() and torch.cuda.is_bf16_supported()
    pipe = ACEStepPipeline(checkpoint_dir=None, dtype="bfloat16" if bf16 else "float32",
                           torch_compile=False, cpu_offload=True)
    for s in items:
        t = time.time()
        try:
            p = out / "songs" / f"{s['id']}.wav"
            p.parent.mkdir(parents=True, exist_ok=True)
            pipe(audio_duration=float(s["duration"]), prompt=s["tags"], lyrics=s["lyrics"],
                 infer_step=60, guidance_scale=15.0, manual_seeds=[int(s.get("seed", 42))],
                 save_path=str(p))
            if not p.exists():  # some versions append a suffix
                cands = sorted(p.parent.glob(f"{s['id']}*.wav")) + sorted(p.parent.glob(f"{s['id']}*.mp3"))
                if cands:
                    cands[0].rename(p.with_suffix(cands[0].suffix))
            print(f"song {s['id']} {time.time() - t:.0f}s", flush=True)
        except Exception as e:  # noqa: BLE001
            record("song", s["id"], e)
    del pipe
    free()


def transcribe(job, out):
    songs = [s for s in job.get("songs", [])]
    if not songs:
        return
    from faster_whisper import WhisperModel
    import torch
    cuda = torch.cuda.is_available()
    model = WhisperModel("small", device="cuda" if cuda else "cpu", compute_type="float16" if cuda else "int8")
    for s in songs:
        audio = next((p for p in (out / "songs").glob(f"{s['id']}.*")), None) if (out / "songs").exists() else None
        if not audio:
            continue
        try:
            segs, _ = model.transcribe(str(audio), word_timestamps=True, language="en",
                                       initial_prompt=s["lyrics"].replace("\n", " ")[:800])
            wordlist = [{"word": w.word.strip(), "start": round(w.start, 3), "end": round(w.end, 3)}
                        for seg in segs for w in (seg.words or [])]
            p = out / "transcripts" / f"{s['id']}.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({"words": wordlist}))
        except Exception as e:  # noqa: BLE001
            record("transcribe", s["id"], e)
    del model
    free()


def main(job, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    if os.environ.get("KAGGLE_KERNEL_RUN_TYPE") or os.environ.get("RHYMEFORGE_INSTALL"):
        install()
    stages = job.get("stages", ["images", "clips", "songs", "transcribe"])
    for name, fn in (("images", gen_images), ("clips", gen_clips), ("songs", gen_songs), ("transcribe", transcribe)):
        if name in stages:
            t = time.time()
            try:
                fn(job, out)
            except Exception as e:  # noqa: BLE001
                record(name, "*", e)
            print(f"== stage {name} done in {time.time() - t:.0f}s", flush=True)
    (out / "errors.json").write_text(json.dumps(ERRORS, indent=2))
    print(f"finished with {len(ERRORS)} errors", flush=True)


if __name__ == "__main__":
    if JOB is not None:
        main(JOB, "/kaggle/working/out")
    else:
        main(json.loads(Path(sys.argv[1]).read_text()), sys.argv[2])
