"""Align lyric lines to whisper word timestamps of the generated song."""
from __future__ import annotations

import difflib
import re

from .util import words as tokenize


def norm(w: str) -> str:
    return re.sub(r"[^a-z0-9']", "", w.lower())


def align_lines(lines: list[dict], heard: list[dict], duration: float) -> list[dict]:
    """Give each lyric line start/end (and per-word times) from recognised words.

    Matched lyric words take whisper's times; unmatched ones are interpolated
    between neighbours. Falls back to even spacing if almost nothing matched.
    """
    lyric_tokens: list[tuple[int, str]] = []
    for li, ln in enumerate(lines):
        for w in tokenize(ln["text"]):
            lyric_tokens.append((li, w))
    heard_norm = [norm(h["word"]) for h in heard]
    sm = difflib.SequenceMatcher(a=[t for _, t in lyric_tokens], b=heard_norm, autojunk=False)
    times: list[tuple[float, float] | None] = [None] * len(lyric_tokens)
    matched = 0
    for a, b, size in sm.get_matching_blocks():
        for k in range(size):
            h = heard[b + k]
            times[a + k] = (float(h["start"]), float(h["end"]))
            matched += 1

    n = len(lyric_tokens)
    if n == 0:
        return []
    if matched < max(3, n * 0.15):
        from .script import estimate_timing
        return estimate_timing(lines, duration)

    # Interpolate gaps.
    known = [i for i, t in enumerate(times) if t]
    for i in range(n):
        if times[i]:
            continue
        prev = max((k for k in known if k < i), default=None)
        nxt = min((k for k in known if k > i), default=None)
        t0 = times[prev][1] if prev is not None else 0.5
        t1 = times[nxt][0] if nxt is not None else duration - 1.0
        lo = prev if prev is not None else -1
        hi = nxt if nxt is not None else n
        frac = (i - lo) / (hi - lo)
        start = t0 + (t1 - t0) * frac
        times[i] = (start, min(start + 0.35, duration))

    out = []
    for li, ln in enumerate(lines):
        idx = [i for i, (l, _) in enumerate(lyric_tokens) if l == li]
        d = dict(ln)
        if idx:
            d["start"] = round(times[idx[0]][0], 3)
            d["end"] = round(times[idx[-1]][1], 3)
            d["words"] = [{"word": lyric_tokens[i][1], "start": round(times[i][0], 3), "end": round(times[i][1], 3)}
                          for i in idx]
        out.append(d)
    # Enforce monotonic, non-overlapping lines.
    for i in range(1, len(out)):
        if out[i]["start"] < out[i - 1]["start"]:
            out[i]["start"] = out[i - 1]["end"]
        out[i]["end"] = max(out[i]["end"], out[i]["start"] + 0.3)
    return out
