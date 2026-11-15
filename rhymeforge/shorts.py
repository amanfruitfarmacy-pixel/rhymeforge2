"""Find the Shorts hiding inside a long video and cut them vertically.

A good Short is self-contained (starts and ends on segment boundaries, ideally a
whole section), is dense with the repeated/hook lines, held retention well in
the long video, and is 20-40 s long.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from .util import run


def _norm(t: str) -> str:
    return " ".join(t.lower().split())


def find_shorts(segments: list[dict], retention: list[dict] | None = None, min_s: float = 15,
                max_s: float = 58, top: int = 3, section_starts: set[float] | None = None) -> list[dict]:
    if not segments:
        return []
    counts = Counter(_norm(s["text"]) for s in segments)
    avg_ret = (sum(p["retention"] for p in retention) / len(retention)) if retention else None
    section_starts = section_starts or set()
    cands = []
    for i, first in enumerate(segments):
        for j in range(i, len(segments)):
            start, end = first["start"], segments[j]["end"]
            dur = end - start
            if dur > max_s:
                break
            if dur < min_s:
                continue
            window = segments[i:j + 1]
            hook_density = sum(1 for s in window if counts[_norm(s["text"])] >= 2) / len(window)
            gap_before = first["start"] - segments[i - 1]["end"] if i > 0 else 1.0
            gap_after = segments[j + 1]["start"] - end if j + 1 < len(segments) else 1.0
            clean = 0.5 * min(gap_before / 0.5, 1) + 0.5 * min(gap_after / 0.5, 1)
            if any(abs(start - s) < 0.75 for s in section_starts):
                clean = max(clean, 1.0)
            length = 1.0 if 20 <= dur <= 40 else 0.6
            ret = 0.5
            if retention and avg_ret:
                inside = [p["retention"] for p in retention if start <= p["t"] <= end]
                if inside:
                    ret = min(1.0, (sum(inside) / len(inside)) / avg_ret / 2)
            score = 0.35 * hook_density + 0.25 * clean + 0.25 * ret + 0.15 * length
            cands.append({"start": round(start, 2), "end": round(end, 2), "duration": round(dur, 1),
                          "score": round(score, 3), "hook_density": round(hook_density, 2),
                          "first_line": first["text"], "text": " / ".join(s["text"] for s in window)})
    cands.sort(key=lambda c: -c["score"])
    chosen: list[dict] = []
    for c in cands:
        if all(c["end"] <= o["start"] or c["start"] >= o["end"] for o in chosen):
            chosen.append(c)
        if len(chosen) == top:
            break
    return sorted(chosen, key=lambda c: c["start"])


def cut_vertical(src: Path, start: float, end: float, dest: Path) -> Path:
    """1080x1920 with the 16:9 frame centred over a blurred fill."""
    vf = ("split[a][b];[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=20:2[bg];"
          "[b]scale=1080:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1")
    dest.parent.mkdir(parents=True, exist_ok=True)
    run(["ffmpeg", "-y", "-ss", f"{start:.2f}", "-to", f"{end:.2f}", "-i", str(src), "-filter_complex", vf,
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(dest)])
    return dest
