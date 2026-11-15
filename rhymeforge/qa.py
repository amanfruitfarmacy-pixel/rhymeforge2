"""Automated checks before a human approves a video."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .util import media_duration


def _ffmpeg_log(args: list[str]) -> str:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", *args, "-f", "null", "-"],
                       capture_output=True, text=True)
    return r.stderr


def loudness(path: Path) -> float | None:
    log = _ffmpeg_log(["-i", str(path), "-map", "0:a", "-af", "ebur128"])
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", log)
    return float(m[-1]) if m else None


def black_segments(path: Path, min_s: float = 0.5) -> list[tuple[float, float]]:
    log = _ffmpeg_log(["-i", str(path), "-map", "0:v", "-vf", f"blackdetect=d={min_s}:pix_th=0.1"])
    return [(float(a), float(b)) for a, b in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", log)]


def rapid_cut_windows(path: Path, max_cuts_per_s: int = 3) -> list[float]:
    """Seconds with too many hard cuts (a rough flashing/strobe check)."""
    log = _ffmpeg_log(["-i", str(path), "-map", "0:v", "-vf", "select='gt(scene,0.5)',showinfo"])
    times = [float(t) for t in re.findall(r"pts_time:([\d.]+)", log)]
    bad = []
    for t in times:
        n = sum(1 for u in times if t <= u < t + 1)
        if n > max_cuts_per_s and (not bad or t - bad[-1] > 1):
            bad.append(round(t, 2))
    return bad


def run_qa(final: Path, thumb: Path, package: dict, script: dict, cfg: dict) -> dict:
    target = cfg["video"]["duration_s"]
    dur = media_duration(final)
    lufs = loudness(final)
    checks = {
        "duration_ok": abs(dur - target) <= target * 0.15,
        "loudness_ok": lufs is not None and -16.5 <= lufs <= -11.5,
        "no_black": not black_segments(final),
        "no_strobe": not rapid_cut_windows(final),
        "thumbnail_ok": thumb.exists() and thumb.stat().st_size < 2_000_000,
        "title_ok": 0 < len(package["title"]) <= 100,
        "hook_ok": bool(script["hook"].get("passed")),
        "lyrics_safe": bool(script["safety"]["ok"]),
    }
    return {"duration_s": round(dur, 1), "lufs": lufs, "checks": checks, "passed": all(checks.values()),
            "needs_human_review": True}
