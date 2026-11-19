"""Command line entry point: `rhymeforge <command>`."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import load_config


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="rhymeforge", description="Toddler song pipeline for YouTube, on free tools.")
    ap.add_argument("--config", default=None, help="path to config.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("week", help="the whole week in one command: scout -> ideas -> songs -> videos -> upload")
    w.add_argument("--count", type=int, default=4)
    w.add_argument("--offline", action="store_true", help="no keys/GPU: sample data, templates, placeholder assets")
    w.add_argument("--backend", choices=["kaggle", "local", "manual", "api", "offline"])
    w.add_argument("--force", action="store_true", help="redo stages that already have results")
    w.add_argument("--no-upload", action="store_true")
    w.add_argument("--allow-qa-fail", action="store_true", help="upload even if automated QA flagged something")
    w.add_argument("--run-name", help="folder name under runs/ (default: ISO week)")

    mk = sub.add_parser("make", help="one video, no accounts: idea -> lyrics -> storyboard -> visuals -> "
                                     "music -> 16:9 video + Short")
    what = mk.add_mutually_exclusive_group()
    what.add_argument("--topic", help='e.g. "brushing teeth", "colours", bath_time (default: best by search demand)')
    what.add_argument("--rhyme", help="a public-domain rhyme, e.g. twinkle-twinkle (see `rhymeforge rhymes`)")
    mk.add_argument("--images", choices=["pollinations", "placeholder", "none"], help="image source (api backend)")
    mk.add_argument("--music", choices=["local", "elevenlabs"], help="music source (api backend)")
    mk.add_argument("--backend", choices=["api", "kaggle", "local", "manual", "offline"],
                    help="api (default) = no GPU; kaggle = free GPU with AI clips and sung vocals")
    mk.add_argument("--llm", choices=["gemini", "claude"], help="who writes lyrics and storyboards")
    mk.add_argument("--offline", action="store_true", help="no network at all: templates + placeholders")
    mk.add_argument("--force", action="store_true")
    mk.add_argument("--run-name")

    t = sub.add_parser("topics", help="rank toddler topics by YouTube search demand (autocomplete, no key)")
    t.add_argument("--offline", action="store_true")
    sub.add_parser("rhymes", help="list the built-in public-domain rhymes")

    s = sub.add_parser("scout", help="rank videos by how far they beat their own channel")
    s.add_argument("--offline", action="store_true")
    s.add_argument("--top", type=int, default=20)

    n = sub.add_parser("niches", help="rank under-served toddler topics")
    n.add_argument("--offline", action="store_true")

    h = sub.add_parser("hook", help="score a hook line")
    h.add_argument("text")
    h.add_argument("--topic", default="")
    h.add_argument("--offline", action="store_true")

    sub.add_parser("formulas", help="list the 21 hook formulas")

    a = sub.add_parser("autopsy", help="where viewers left and what was on screen then")
    a.add_argument("--video-dir", required=True, help="a runs/.../<video> folder")
    src = a.add_mutually_exclusive_group(required=True)
    src.add_argument("--csv", help="retention CSV exported from YouTube Studio")
    src.add_argument("--video-id", help="fetch the curve from the YouTube Analytics API")
    a.add_argument("--drop", type=float, default=3.0, help="minimum drop in percentage points")

    e = sub.add_parser("edl", help="turn a transcript into an edit decision list")
    e.add_argument("transcript", help=".srt, .vtt or .json")
    e.add_argument("--retention", help="retention CSV to flag/cut drop-off segments")
    e.add_argument("--duration", type=float, help="video length in seconds (needed if the CSV uses ratios)")
    e.add_argument("--cut-drops", action="store_true", help="cut segments with drops instead of flagging them")
    e.add_argument("--max-gap", type=float, default=1.2)
    e.add_argument("--fps", type=int, default=24)
    e.add_argument("--clip-name", default="source.mp4")
    e.add_argument("-o", "--out", default="edit")

    sh = sub.add_parser("shorts", help="find Shorts inside a long video")
    sh.add_argument("--video", required=True)
    sh.add_argument("--transcript", required=True)
    sh.add_argument("--retention", help="retention CSV")
    sh.add_argument("--top", type=int, default=3)
    sh.add_argument("--cut", action="store_true", help="also export vertical 1080x1920 files")
    sh.add_argument("-o", "--out", default="shorts")

    sub.add_parser("review", help="autopsy + Shorts for uploads public for 48h+")

    au = sub.add_parser("auth", help="one-time YouTube sign-in; saves .secrets/token.json and shows your channel")
    au.add_argument("--no-browser", action="store_true", help="print the sign-in link instead of opening a browser")

    args = ap.parse_args(argv)
    cfg = load_config(args.config)

    if args.cmd == "week":
        from .pipeline import week
        week(cfg, count=args.count, offline=args.offline, backend=args.backend, force=args.force,
             upload=not args.no_upload, run_name=args.run_name, allow_qa_fail=args.allow_qa_fail)

    elif args.cmd == "make":
        from .pipeline import make
        g = cfg["generation"]
        if args.images:
            g["image_api"] = args.images
        if args.music:
            g["music_api"] = args.music
        if args.llm:
            cfg["llm"]["provider"] = args.llm
        vdir = make(cfg, topic=args.topic, rhyme=args.rhyme, offline=args.offline, backend=args.backend,
                    force=args.force, run_name=args.run_name)
        print(f"16:9 video: {vdir / 'final.mp4'}")
        for f in sorted((vdir / "shorts").glob("*.mp4")):
            print(f"Short:      {f}")
        print(f"Storyboard: {vdir / 'storyboard.md'}")

    elif args.cmd == "topics":
        from .demand import topic_demand
        for d in topic_demand(cfg, Path(cfg["paths"]["data"]), args.offline):
            print(f"{d['demand']:>6.1f}  {d['topic']:<10} {d['phrase']:<22} e.g. {'; '.join(d['suggestions'][:3])}")

    elif args.cmd == "rhymes":
        from .rhymes import RHYMES
        for k, r in RHYMES.items():
            print(f"{k:<24} {r['title']:<34} {'traditional tune' if r.get('tune') else 'generated tune'}")

    elif args.cmd == "scout":
        from .scout import run_scout
        for v in run_scout(cfg, Path(cfg["paths"]["data"]), args.offline)[: args.top]:
            print(f"{v['outlier']:>7.1f}x  {v['views']:>10,} views  median {int(v['channel_median']):>9,}  "
                  f"{v['channel_title'][:22]:<22}  {v['title'][:60]}")

    elif args.cmd == "niches":
        from .niches import rank_niches
        from .scout import run_scout
        for n_ in rank_niches(run_scout(cfg, Path(cfg["paths"]["data"]), args.offline)):
            print(f"{n_['gap_score']:>7.2f}  {n_['topic']:<12} demand {n_['demand']:>7.1f}  supply {n_['supply']:>4}")

    elif args.cmd == "hook":
        from .hooks import score_hook
        from .llm import LLM
        names = [c["name"] for c in cfg["channel"]["characters"]]
        r = score_hook(LLM(cfg, args.offline), args.text, {"topic": args.topic}, names)
        print(json.dumps(r, indent=2))

    elif args.cmd == "formulas":
        from .hooks import FORMULAS
        for i, f in enumerate(FORMULAS, 1):
            print(f"{i:>2}. {f['name']:<24} {f['pattern']:<48} e.g. {f['example']}")

    elif args.cmd == "autopsy":
        from . import retention as ret
        from .pipeline import autopsy
        from .util import media_duration, read_json
        vdir = Path(args.video_dir)
        dur = media_duration(vdir / "final.mp4")
        if args.csv:
            points = ret.load_csv(Path(args.csv), dur)
        else:
            from .youtube import credentials, fetch_retention
            points = ret.from_api(fetch_retention(credentials(Path(cfg["paths"]["secrets"])), args.video_id), dur)
        title = (read_json(vdir / "package.json") or {}).get("title", vdir.name)
        autopsy(vdir, points, cfg, title, args.video_id or vdir.name, args.drop)
        print((vdir / "autopsy.md").read_text())

    elif args.cmd == "edl":
        from . import edl, retention as ret
        segs = edl.load_transcript(Path(args.transcript))
        drops = []
        if args.retention:
            dur = args.duration or (segs[-1]["end"] if segs else 0)
            drops = ret.find_drops(ret.load_csv(Path(args.retention), dur))
        keeps = edl.plan_edits(segs, drops, max_gap=args.max_gap, cut_drops=args.cut_drops)
        Path(args.out + ".edl").write_text(edl.to_cmx3600(keeps, Path(args.transcript).stem, args.clip_name, args.fps))
        Path(args.out + ".json").write_text(json.dumps({"summary": edl.summary(segs, keeps), "events": keeps}, indent=2))
        print(json.dumps(edl.summary(segs, keeps), indent=2))
        print(f"wrote {args.out}.edl and {args.out}.json")

    elif args.cmd == "shorts":
        from . import edl, retention as ret
        from .shorts import cut_vertical, find_shorts
        from .util import media_duration
        segs = edl.load_transcript(Path(args.transcript))
        points = ret.load_csv(Path(args.retention), media_duration(Path(args.video))) if args.retention else None
        picks = find_shorts(segs, points, top=args.top)
        for i, p in enumerate(picks, 1):
            print(f"#{i} {p['start']:.1f}-{p['end']:.1f}s ({p['duration']}s) score {p['score']}: {p['first_line']}")
            if args.cut:
                p["file"] = str(cut_vertical(Path(args.video), p["start"], p["end"], Path(args.out) / f"short_{i}.mp4"))
        Path(args.out).mkdir(parents=True, exist_ok=True)
        (Path(args.out) / "shorts.json").write_text(json.dumps(picks, indent=2))

    elif args.cmd == "auth":
        from .youtube import credentials, whoami
        secrets = Path(cfg["paths"]["secrets"])
        creds = credentials(secrets, open_browser=not args.no_browser)
        channels = whoami(creds)
        if not channels:
            print("Signed in, but this Google account has no YouTube channel. Create one at youtube.com first.")
            return 1
        for c in channels:
            print(f"Signed in. Channel: {c['title']} ({c['id']}), subscribers: {c['subscribers']}")
        print(f"Saved login to {secrets / 'token.json'} (keep this file private).")

    elif args.cmd == "review":
        from .pipeline import review_due
        done = review_due(cfg)
        print(f"reviewed {len(done)} videos")
    return 0


if __name__ == "__main__":
    sys.exit(main())
