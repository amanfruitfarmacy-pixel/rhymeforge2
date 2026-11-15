"""`rhymeforge week`: the whole weekly pipeline in one command.

Every stage writes its result to runs/<year>-W<week>/..., and re-running the
command resumes: finished stages are loaded instead of redone (use --force to redo).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from . import retention as ret
from .align import align_lines
from .assemble import assemble
from .edl import load_transcript
from .generate import build_job, run_job
from .library import ClipLibrary
from .llm import LLM
from .niches import generate_ideas, rank_niches
from .packaging import description, make_package
from .qa import run_qa
from .scout import run_scout
from .script import estimate_timing, lyric_lines, write_script
from .shorts import cut_vertical, find_shorts
from .storyboard import build_storyboard
from .thumbnail import make_thumbnail
from .util import log, media_duration, read_json, slugify, write_json


def step(path: Path, fn: Callable[[], Any], force: bool = False) -> Any:
    if path.exists() and not force:
        return read_json(path)
    result = fn()
    write_json(path, result)
    return result


def paths(cfg: dict) -> dict[str, Path]:
    return {k: Path(v) for k, v in cfg["paths"].items()}


def week(cfg: dict, count: int = 4, offline: bool = False, backend: str | None = None, force: bool = False,
         upload: bool = True, run_name: str | None = None, allow_qa_fail: bool = False) -> Path:
    p = paths(cfg)
    backend = "offline" if offline else (backend or cfg["generation"]["backend"])
    iso = datetime.now().isocalendar()
    run_dir = p["runs"] / (run_name or f"{iso[0]}-W{iso[1]:02d}")
    run_dir.mkdir(parents=True, exist_ok=True)
    llm = LLM(cfg, offline=offline)
    if not llm.available:
        log("LLM: no Gemini key or offline mode, so using built-in templates (lower quality)")
    history = read_json(p["data"] / "history.json", [])
    library = ClipLibrary(p["library"])

    # 0. Learn from last week's videos first.
    if upload and not offline and cfg["upload"]["enabled"]:
        try:
            review_due(cfg)
        except Exception as e:  # noqa: BLE001 - reviewing must never block producing
            log(f"review of previous uploads skipped: {e}")

    # 1-3. Research and ideas.
    log("1/8 scouting outliers")
    scored = step(run_dir / "scout.json", lambda: run_scout(cfg, p["data"], offline), force)
    log("2/8 ranking niches")
    niches = step(run_dir / "niches.json", lambda: rank_niches(scored, history), force)
    log("3/8 generating ideas")
    ideas = step(run_dir / "ideas.json", lambda: generate_ideas(llm, niches, count, cfg, history), force)
    learnings = ret.summarize_learnings(p["data"] / "learnings.json")

    # 4-5. Script, hook, title/thumbnail pair, storyboard per video.
    videos = []
    for n, idea in enumerate(ideas, 1):
        slug = slugify(idea["working_title"])
        vdir = run_dir / slug
        log(f"4/8 [{n}/{len(ideas)}] script + hook: {idea['working_title']}")
        script = step(vdir / "script.json", lambda: write_script(llm, idea, cfg, learnings), force)
        lines = lyric_lines(script)
        est = estimate_timing(lines, cfg["video"]["duration_s"])
        log(f"    hook '{script['hook']['text']}' scored {script['hook']['score']}")
        package = step(vdir / "package.json", lambda: make_package(llm, script, cfg, est), force)
        log(f"    title: {package['title']!r} | thumbnail: {package['thumbnail_text']!r}")
        sb = step(vdir / "storyboard.json", lambda: build_storyboard(llm, script, est, cfg, library), force)
        log(f"5/8 storyboard: {sb['counts']}")
        videos.append({"slug": slug, "dir": vdir, "script": script, "package": package,
                       "storyboard": sb, "lines": lines})

    # 6. GPU generation for all videos in one batch.
    log(f"6/8 generating assets on backend '{backend}'")
    job = build_job(videos, cfg)
    gpu_out = run_job(job, cfg, run_dir, backend)
    errors = read_json(gpu_out / "errors.json", [])
    if errors:
        log(f"GPU job reported {len(errors)} errors (see {gpu_out/'errors.json'}); continuing with fallbacks")

    # 7. Assemble, thumbnail, QA.
    results = []
    for v in videos:
        vdir, slug = v["dir"], v["slug"]
        log(f"7/8 assembling {slug}")
        try:
            user_song = vdir / "song.wav"
            song = user_song if user_song.exists() else next(iter(sorted((gpu_out / "songs").glob(f"{slug}.*"))), None)
            if song is None:
                raise RuntimeError(f"no song generated for {slug}; add {user_song} and re-run")
            dur = media_duration(song)
            heard = read_json(gpu_out / "transcripts" / f"{slug}.json", {}).get("words", [])
            timed = align_lines(v["lines"], heard, dur) if heard else estimate_timing(v["lines"], dur)
            write_json(vdir / "timing.json", timed)
            final = vdir / "final.mp4"
            if force or not final.exists():
                assemble(vdir, v["storyboard"], timed, gpu_out, slug, cfg, library, user_song)
            scene = gpu_out / "images" / f"{slug}__thumb.png"
            if not scene.exists():
                scene = next(iter(sorted((gpu_out / "images").glob(f"{slug}__*.png"))))
            thumb = make_thumbnail(scene, v["package"]["thumbnail_text"], vdir / "thumbnail.jpg")
            v["package"]["description"] = description(v["script"], v["package"]["title"], timed, cfg)
            write_json(vdir / "package.json", v["package"])
            qa = run_qa(final, thumb, v["package"], v["script"], cfg)
            write_json(vdir / "qa.json", qa)
            results.append({"slug": slug, "dir": str(vdir), "final": str(final), "thumb": str(thumb), "qa": qa,
                            "title": v["package"]["title"], "topic": v["script"].get("topic")})
        except Exception as e:  # noqa: BLE001 - one bad video shouldn't sink the week
            log(f"FAILED {slug}: {e}")
            results.append({"slug": slug, "dir": str(vdir), "error": str(e), "title": v["package"]["title"]})

    # 8. Upload (private + scheduled, review window before going public).
    if upload and not offline and cfg["upload"]["enabled"]:
        log("8/8 uploading")
        upload_results(results, cfg, allow_qa_fail)
    else:
        log("8/8 upload skipped")

    summary = write_summary(run_dir, results, niches)
    log(f"done. Summary: {summary}")
    return run_dir


def upload_results(results: list[dict], cfg: dict, allow_qa_fail: bool = False) -> None:
    from .youtube import credentials, next_slots, upload_video
    p = paths(cfg)
    history = read_json(p["data"] / "history.json", [])
    ready = [r for r in results if "error" not in r and (r["qa"]["passed"] or allow_qa_fail)
             and not any(h.get("dir") == r["dir"] for h in history)]
    if not ready:
        log("nothing to upload (QA failures or already uploaded)")
        return
    s = cfg["schedule"]
    slots = next_slots(s["publish_days"], s["publish_time"], s["timezone"], len(ready), s["min_lead_hours"],
                       taken=[h.get("publish_at") for h in history])
    creds = credentials(p["secrets"])
    for r, slot in zip(ready, slots):
        pkg = read_json(Path(r["dir"]) / "package.json")
        info = upload_video(creds, Path(r["final"]), Path(r["thumb"]), pkg, cfg, slot,
                            Path(r["dir"]) / "captions.srt")
        r["upload"] = info
        write_json(Path(r["dir"]) / "upload.json", info)
        history.append({"video_id": info["video_id"], "publish_at": info["publish_at"], "dir": r["dir"],
                        "title": r["title"], "topic": r["topic"], "reviewed": False})
        write_json(p["data"] / "history.json", history)
        log(f"uploaded {r['slug']} -> {info['studio_url']} (goes public {slot:%a %d %b %H:%M})")


def review_due(cfg: dict, min_hours: float = 48) -> list[dict]:
    """Retention autopsy + Shorts for uploads that have been public for 48h."""
    from .youtube import credentials, fetch_retention
    p = paths(cfg)
    history = read_json(p["data"] / "history.json", [])
    now = datetime.now(timezone.utc)
    due = [h for h in history if not h.get("reviewed") and h.get("video_id")
           and datetime.fromisoformat(h["publish_at"]) + timedelta(hours=min_hours) <= now]
    if not due:
        return []
    creds = credentials(p["secrets"])
    done = []
    for h in due:
        vdir = Path(h["dir"])
        curve = fetch_retention(creds, h["video_id"])
        if not curve:
            log(f"no retention data yet for {h['video_id']}")
            continue
        out = autopsy(vdir, ret.from_api(curve, media_duration(vdir / "final.mp4")), cfg, h["title"], h["video_id"])
        make_shorts(vdir, out["points"], cut=True)
        h["reviewed"] = True
        done.append(h)
    write_json(p["data"] / "history.json", history)
    return done


def autopsy(vdir: Path, points: list[dict], cfg: dict, title: str, video_key: str, drop_pp: float = 3.0) -> dict:
    drops = ret.find_drops(points, drop_pp)
    annotated = ret.annotate(drops, read_json(vdir / "timing.json"), read_json(vdir / "shots_timed.json"))
    (vdir / "autopsy.md").write_text(ret.report(title, annotated, points))
    write_json(vdir / "autopsy.json", {"points": points, "drops": annotated})
    ret.update_learnings(paths(cfg)["data"] / "learnings.json", video_key, annotated)
    return {"points": points, "drops": annotated}


def make_shorts(vdir: Path, points: list[dict] | None = None, cut: bool = True, top: int = 3) -> list[dict]:
    timing = read_json(vdir / "timing.json")
    if timing:
        segs = [{"start": l["start"], "end": l["end"], "text": l["text"]} for l in timing]
        starts = {l["start"] for i, l in enumerate(timing) if i == 0 or timing[i - 1]["section"] != l["section"]}
    else:
        segs, starts = load_transcript(vdir / "captions.srt"), set()
    picks = find_shorts(segs, points, top=top, section_starts=starts)
    for i, s in enumerate(picks, 1):
        if cut:
            s["file"] = str(cut_vertical(vdir / "final.mp4", s["start"], s["end"], vdir / "shorts" / f"short_{i}.mp4"))
    write_json(vdir / "shorts.json", picks)
    return picks


def write_summary(run_dir: Path, results: list[dict], niches: list[dict]) -> Path:
    md = [f"# RhymeForge run {run_dir.name}", "", "## Top niches this week", "",
          "| Topic | Gap score | Best outlier example |", "|---|---|---|"]
    for n in niches[:6]:
        ex = n["examples"][0] if n["examples"] else {"title": "-", "outlier": "-"}
        md.append(f"| {n['topic']} | {n['gap_score']} | {ex['title']} ({ex['outlier']}x) |")
    md += ["", "## Videos", "", "| Video | QA | Upload |", "|---|---|---|"]
    for r in results:
        if "error" in r:
            md.append(f"| {r['title']} | FAILED: {r['error'][:80]} | - |")
            continue
        failed = [k for k, ok in r["qa"]["checks"].items() if not ok]
        qa = "passed" if not failed else "check: " + ", ".join(failed)
        up = r.get("upload", {}).get("studio_url", "not uploaded")
        md.append(f"| {r['title']} | {qa} | {up} |")
    md += ["", "Every video still needs a human watch-through before its publish time."]
    path = run_dir / "summary.md"
    path.write_text("\n".join(md) + "\n")
    return path
