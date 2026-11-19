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
from .assemble import assemble, render_vertical
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
from .util import fmt_ts, log, media_duration, read_json, slugify, write_json


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
    log("2/8 ranking niches (outliers x YouTube search demand)")

    def _niches():
        from .demand import boost_niches, topic_demand
        try:
            demand = topic_demand(cfg, p["data"], offline)
        except Exception as e:  # noqa: BLE001 - demand is a bonus signal
            log(f"search demand skipped: {e}")
            demand = []
        return boost_niches(rank_niches(scored, history), demand)
    niches = step(run_dir / "niches.json", _niches, force)
    log("3/8 generating ideas")
    ideas = step(run_dir / "ideas.json", lambda: generate_ideas(llm, niches, count, cfg, history), force)
    learnings = ret.summarize_learnings(p["data"] / "learnings.json")

    # 4-5. Script, hook, title/thumbnail pair, storyboard per video.
    videos = [prepare_video(llm, idea, run_dir, cfg, learnings, library, force, n, len(ideas))
              for n, idea in enumerate(ideas, 1)]

    # 6. Generation for all videos in one batch.
    gpu_out = generate_assets(videos, cfg, run_dir, backend)

    # 7. Assemble, thumbnail, QA, Shorts.
    results = [finish_video(v, gpu_out, cfg, library, force) for v in videos]

    # 8. Upload (private + scheduled, review window before going public).
    if upload and not offline and cfg["upload"]["enabled"]:
        log("8/8 uploading")
        upload_results(results, cfg, allow_qa_fail)
    else:
        log("8/8 upload skipped")

    summary = write_summary(run_dir, results, niches)
    log(f"done. Summary: {summary}")
    return run_dir


def prepare_video(llm: LLM, idea: dict, run_dir: Path, cfg: dict, learnings: str = "",
                  library: ClipLibrary | None = None, force: bool = False, n: int = 1, total: int = 1) -> dict:
    """Steps 2-3 for one video: lyrics (+hook), title/thumbnail, storyboard."""
    slug = slugify(idea["working_title"])
    vdir = run_dir / slug
    log(f"4/8 [{n}/{total}] lyrics + hook: {idea['working_title']}")
    if idea.get("rhyme"):
        script = step(vdir / "script.json", lambda: public_domain_script(llm, idea, cfg), force)
    else:
        script = step(vdir / "script.json", lambda: write_script(llm, idea, cfg, learnings), force)
    lines = lyric_lines(script)
    bpm = script.get("bpm", cfg["video"]["bpm"])
    est = estimate_timing(lines, max(cfg["video"]["duration_s"] if not idea.get("rhyme") else 0,
                                     (len(lines) * 8 + 16) * 60 / bpm))
    log(f"    hook '{script['hook']['text']}' scored {script['hook']['score']}")
    def _package():
        pkg = make_package(llm, script, cfg, est)
        if script.get("public_domain") and script["title"].split(",")[0].lower() not in pkg["title"].lower():
            lead = cfg["channel"]["characters"][0]["name"]
            pkg["title"] = f"{script['title']} | Nursery Rhyme with {lead}"[:70]
        return pkg
    package = step(vdir / "package.json", _package, force)
    log(f"    title: {package['title']!r} | thumbnail: {package['thumbnail_text']!r}")
    sb = step(vdir / "storyboard.json", lambda: build_storyboard(llm, script, est, cfg, library), force)
    write_storyboard_md(vdir, script, package, sb, est)
    write_handoffs(vdir, script, sb)
    log(f"5/8 storyboard: {sb['counts']} -> {vdir / 'storyboard.md'}")
    return {"slug": slug, "dir": vdir, "script": script, "package": package, "storyboard": sb, "lines": lines}


def public_domain_script(llm: LLM, idea: dict, cfg: dict) -> dict:
    from .hooks import best_hook
    from .rhymes import get_rhyme, intro_hooks, rhyme_script
    from .script import safety_review
    r = get_rhyme(idea["rhyme"])
    names = [c["name"] for c in cfg["channel"]["characters"]]
    hook = best_hook(llm, idea, cfg, candidates=intro_hooks(r, names))
    bpm = int(cfg.get("rhymes", {}).get("bpm", 100))
    script = rhyme_script(r, hook["text"], names, int(cfg.get("rhymes", {}).get("repeats", 3)), bpm)
    script["bpm"] = bpm
    script["hook"] = {k: hook[k] for k in ("text", "formula", "score", "heuristic", "llm", "passed", "feedback")}
    script["hook_candidates"] = [{k: h[k] for k in ("text", "formula", "score")} for h in hook["all"]]
    script["topic"] = idea["topic"]
    script["idea"] = idea
    script["safety"] = safety_review(llm, script)
    return script


def generate_assets(videos: list[dict], cfg: dict, run_dir: Path, backend: str) -> Path:
    log(f"6/8 generating images + music on backend '{backend}'")
    job = build_job(videos, cfg)
    gpu_out = run_job(job, cfg, run_dir, backend)
    errors = read_json(gpu_out / "errors.json", [])
    if errors:
        log(f"generation reported {len(errors)} errors (see {gpu_out/'errors.json'}); continuing with fallbacks")
    return gpu_out


def find_song(vdir: Path, gpu_out: Path, slug: str) -> Path | None:
    """Your own song (song.wav/.mp3, e.g. made on Suno) beats the generated one."""
    for name in ("song.wav", "song.mp3", "song.m4a"):
        if (vdir / name).exists():
            return vdir / name
    return next(iter(sorted((gpu_out / "songs").glob(f"{slug}.*"))), None)


def finish_video(v: dict, gpu_out: Path, cfg: dict, library: ClipLibrary | None = None,
                 force: bool = False) -> dict:
    """Step 6 for one video: timing, 16:9 assembly, Shorts, thumbnail, QA."""
    vdir, slug = v["dir"], v["slug"]
    log(f"7/8 assembling {slug}")
    try:
        song = find_song(vdir, gpu_out, slug)
        if song is None:
            raise RuntimeError(f"no song generated for {slug}; add {vdir / 'song.wav'} and re-run")
        own_song = song.parent == vdir
        dur = media_duration(song)
        heard = [] if own_song else read_json(gpu_out / "transcripts" / f"{slug}.json", {}).get("words", [])
        timed = align_lines(v["lines"], heard, dur) if heard else estimate_timing(v["lines"], dur)
        write_json(vdir / "timing.json", timed)
        final = vdir / "final.mp4"
        if force or not final.exists() or not (vdir / "render.json").exists():
            assemble(vdir, v["storyboard"], timed, gpu_out, slug, cfg, library, song if own_song else None)
        shorts = make_shorts(vdir, top=int(cfg.get("shorts", {}).get("per_video", 1)), cut=True, cfg=cfg)
        scene = gpu_out / "images" / f"{slug}__thumb.png"
        if not scene.exists():
            scene = next(iter(sorted((gpu_out / "images").glob(f"{slug}__*.png"))), None) or \
                next(iter(sorted((vdir / "art").glob("*.png"))))
        thumb = make_thumbnail(scene, v["package"]["thumbnail_text"], vdir / "thumbnail.jpg")
        v["package"]["description"] = description(v["script"], v["package"]["title"], timed, cfg)
        write_json(vdir / "package.json", v["package"])
        qa = run_qa(final, thumb, v["package"], v["script"], cfg, expected_s=dur)
        write_json(vdir / "qa.json", qa)
        return {"slug": slug, "dir": str(vdir), "final": str(final), "thumb": str(thumb), "qa": qa,
                "shorts": [s.get("file") for s in shorts], "title": v["package"]["title"],
                "topic": v["script"].get("topic")}
    except Exception as e:  # noqa: BLE001 - one bad video shouldn't sink the week
        log(f"FAILED {slug}: {e}")
        return {"slug": slug, "dir": str(vdir), "error": str(e), "title": v["package"]["title"]}


def make(cfg: dict, topic: str | None = None, rhyme: str | None = None, offline: bool = False,
         backend: str | None = None, force: bool = False, run_name: str | None = None) -> Path:
    """One video, start to finish, no accounts needed: topic (or the best one by YouTube search
    demand) or a public-domain rhyme -> lyrics -> storyboard -> visuals -> music -> 16:9 + Short."""
    import copy
    from .demand import pick_topic, topic_demand
    from .niches import TOPIC_ITEMS, classify, generate_ideas
    from .rhymes import rhyme_idea
    cfg = copy.deepcopy(cfg)
    p = paths(cfg)
    backend = "offline" if offline else (backend or cfg["generation"].get("make_backend", "api"))
    if backend in ("api", "offline"):
        cfg["video"]["unique_clips"] = 0  # no AI video clips without a GPU: animated stills only
    llm = LLM(cfg, offline=offline)
    history = read_json(p["data"] / "history.json", [])
    log("1/6 idea")
    if rhyme:
        idea = rhyme_idea(rhyme, cfg)
    else:
        if not topic:
            demand = topic_demand(cfg, p["data"], offline)
            best = pick_topic(demand, history)
            log("    search demand (YouTube autocomplete): " +
                ", ".join(f"{d['topic']} {d['demand']}" for d in demand[:6]))
            topic = best["topic"]
        key = topic if topic in TOPIC_ITEMS else (classify(topic) or [slugify(topic).replace("-", "_")])[0]
        niche = [{"topic": key, "gap_score": 1.0, "examples": [], "brief": topic}]
        idea = generate_ideas(llm, niche, 1, cfg, history)[0]
        idea["topic"] = key
        if not llm.available and topic != key:  # templates: keep the user's own words in the title
            idea["working_title"] = f"{cfg['channel']['characters'][0]['name']}'s {topic.title()} Song"
        if key not in TOPIC_ITEMS and not idea.get("items"):
            idea["items"] = [w for w in topic.split()][:5]
    run_dir = p["runs"] / (run_name or f"make-{datetime.now():%Y%m%d-%H%M%S}-{slugify(idea['working_title'], 24)}")
    run_dir.mkdir(parents=True, exist_ok=True)
    write_json(run_dir / "idea.json", idea)
    v = prepare_video(llm, idea, run_dir, cfg, "", None, force)
    gpu_out = generate_assets([v], cfg, run_dir, backend)
    result = finish_video(v, gpu_out, cfg, None, force)
    summary = write_summary(run_dir, [result], [])
    if "error" in result:
        raise RuntimeError(f"{result['error']} (see {summary})")
    log(f"done: {result['final']} + {len(result['shorts'])} Short(s). Summary: {summary}")
    return Path(result["dir"])


def write_storyboard_md(vdir: Path, script: dict, package: dict, sb: dict, timed: list[dict]) -> Path:
    """Human-readable storyboard: one scene per lyric line with its visual."""
    md = [f"# Storyboard: {package['title']}", "", f"Learning goal: {script.get('learning_goal', '-')}  ",
          f"Thumbnail: \"{package['thumbnail_text']}\" - {package.get('thumbnail_scene', '')}", "",
          "| # | Time (planned) | Section | Lyric | Visual | Shot |", "|---|---|---|---|---|---|"]
    for s, ln in zip(sb["shots"], timed):
        visual = s.get("image_prompt", "").split(". ")[-1] if s["kind"] != "reuse" else f"same as {s['reuse_of']}"
        md.append(f"| {s['id']} | {fmt_ts(ln['start'])} | {s['section_type']} | {s['line']} | "
                  f"{visual.replace('|', '/')} | {s['kind']} |")
    path = vdir / "storyboard.md"
    path.write_text("\n".join(md) + "\n")
    return path


def write_handoffs(vdir: Path, script: dict, sb: dict) -> None:
    """Files for doing a step by hand in another tool:
    canva_scenes.csv (one row per image to make in Canva; save as art/<shot>.png),
    suno.txt (lyrics + style to paste into Suno; save the song as song.wav or song.mp3)."""
    import csv
    vdir.mkdir(parents=True, exist_ok=True)
    with (vdir / "canva_scenes.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["shot", "save_as", "lyric", "prompt"])
        for s in sb["shots"]:
            if s["kind"] in ("ai_clip", "still"):
                w.writerow([s["id"], f"art/{s['id']}.png", s["line"], s.get("image_prompt", "")])
    from .script import ace_step_lyrics
    (vdir / "suno.txt").write_text(f"STYLE:\n{script['music_tags']}\n\nLYRICS:\n{ace_step_lyrics(script)}\n")


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
        make_shorts(vdir, out["points"], cut=True, cfg=cfg)
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


def make_shorts(vdir: Path, points: list[dict] | None = None, cut: bool = True, top: int = 3,
                cfg: dict | None = None) -> list[dict]:
    timing = read_json(vdir / "timing.json")
    if timing:
        segs = [{"start": l["start"], "end": l["end"], "text": l["text"]} for l in timing]
        starts = {l["start"] for i, l in enumerate(timing) if i == 0 or timing[i - 1]["section"] != l["section"]}
    else:
        segs, starts = load_transcript(vdir / "captions.srt"), set()
    sc = (cfg or {}).get("shorts", {})
    picks = find_shorts(segs, points, min_s=sc.get("min_s", 20), max_s=sc.get("max_s", 58), top=top,
                        section_starts=starts)
    if not picks and segs and segs[-1]["end"] - segs[0]["start"] <= sc.get("max_s", 58):
        # The whole song is Short-length (e.g. one verse of a rhyme): use all of it.
        picks = [{"start": segs[0]["start"], "end": segs[-1]["end"], "first_line": segs[0]["text"],
                  "duration": round(segs[-1]["end"] - segs[0]["start"], 1), "score": 1.0, "text": "whole song"}]
    for i, s in enumerate(picks, 1):
        if not cut:
            continue
        dest = vdir / "shorts" / f"short_{i}.mp4"
        if timing and (vdir / "render.json").exists():  # native vertical render with phone-sized captions
            s["file"] = str(render_vertical(vdir, timing, max(0.0, s["start"] - 0.3), s["end"] + 0.6, dest,
                                            cfg or {}))
        else:
            s["file"] = str(cut_vertical(vdir / "final.mp4", s["start"], s["end"], dest))
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
