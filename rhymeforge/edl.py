"""Transcript -> edit decision list (CMX3600 + JSON).

Keeps everything that is spoken/sung, removes dead air longer than `max_gap`,
and either cuts or flags (as REVIEW comments) segments where retention dropped.
Works on our own songs and on any long video's transcript (.srt, .vtt, .json).
"""
from __future__ import annotations

import json
import re
from pathlib import Path


def _ts(s: str) -> float:
    s = s.strip().replace(",", ".")
    parts = s.split(":")
    secs = 0.0
    for p in parts:
        secs = secs * 60 + float(p)
    return secs


def load_transcript(path: Path) -> list[dict]:
    """Return segments [{start, end, text}] from SRT, VTT or our JSON formats."""
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix == ".json":
        data = json.loads(text)
        if isinstance(data, dict) and "words" in data:  # whisper words -> ~sentence segments
            return words_to_segments(data["words"])
        if isinstance(data, dict) and "segments" in data:
            data = data["segments"]
        return [{"start": float(d["start"]), "end": float(d["end"]), "text": d["text"]} for d in data]
    segs = []
    for block in re.split(r"\n\s*\n", text):
        m = re.search(r"([\d:.,]+)\s*-->\s*([\d:.,]+)[^\n]*\n(.+)", block, re.S)
        if m:
            body = re.sub(r"<[^>]+>", "", m.group(3)).strip().replace("\n", " ")
            segs.append({"start": _ts(m.group(1)), "end": _ts(m.group(2)), "text": body})
    return segs


def words_to_segments(words: list[dict], max_gap: float = 0.6, max_len: float = 6.0) -> list[dict]:
    segs: list[dict] = []
    for w in words:
        if segs and w["start"] - segs[-1]["end"] <= max_gap and w["end"] - segs[-1]["start"] <= max_len:
            segs[-1]["end"] = w["end"]
            segs[-1]["text"] += " " + w["word"]
        else:
            segs.append({"start": w["start"], "end": w["end"], "text": w["word"]})
    return segs


def plan_edits(segments: list[dict], drops: list[dict] | None = None, max_gap: float = 1.2,
               pad: float = 0.25, cut_drops: bool = False, protect: set[str] | None = None) -> list[dict]:
    """Return keep-ranges [{start, end, text, note}] in source time."""
    drops = drops or []
    protect = {p.lower() for p in (protect or set())}
    kept: list[dict] = []
    for seg in segments:
        dropped_here = [d for d in drops if seg["start"] - 0.5 <= d["t"] <= seg["end"] + 1.0]
        note = ""
        if dropped_here:
            worst = max(dropped_here, key=lambda d: d["drop_pp"])
            note = f"retention -{worst['drop_pp']}pp at {worst['t']:.1f}s"
            if cut_drops and seg["text"].strip().lower() not in protect:
                continue
        start, end = max(0.0, seg["start"] - pad), seg["end"] + pad
        if kept and start - kept[-1]["end"] <= max_gap:
            kept[-1]["end"] = max(kept[-1]["end"], end)
            kept[-1]["text"] += " / " + seg["text"]
            if note and note not in kept[-1]["note"]:
                kept[-1]["note"] = (kept[-1]["note"] + "; " + note).strip("; ")
        else:
            kept.append({"start": start, "end": end, "text": seg["text"], "note": note})
    return kept


def tc(seconds: float, fps: int) -> str:
    frames = int(round(seconds * fps))
    h, rem = divmod(frames, 3600 * fps)
    m, rem = divmod(rem, 60 * fps)
    s, f = divmod(rem, fps)
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"


def to_cmx3600(keeps: list[dict], title: str, clip_name: str, fps: int = 24, reel: str = "AX") -> str:
    out = [f"TITLE: {title}", "FCM: NON-DROP FRAME", ""]
    rec = 0.0
    for i, k in enumerate(keeps, 1):
        dur = k["end"] - k["start"]
        out.append(f"{i:03d}  {reel:<8} AA/V  C        {tc(k['start'], fps)} {tc(k['end'], fps)} "
                   f"{tc(rec, fps)} {tc(rec + dur, fps)}")
        out.append(f"* FROM CLIP NAME: {clip_name}")
        out.append(f"* TEXT: {k['text'][:120]}")
        if k.get("note"):
            out.append(f"* REVIEW: {k['note']}")
        out.append("")
        rec += dur
    return "\n".join(out)


def summary(segments: list[dict], keeps: list[dict]) -> dict:
    src = segments[-1]["end"] if segments else 0
    new = sum(k["end"] - k["start"] for k in keeps)
    return {"source_s": round(src, 1), "edited_s": round(new, 1), "removed_s": round(max(0, src - new), 1),
            "events": len(keeps), "review_flags": sum(1 for k in keeps if k.get("note"))}
