"""Retention autopsy: the second viewers left, and what was on screen and sung then.

Input is either the YouTube Analytics API curve (100 points) or a CSV export
from YouTube Studio (any CSV with a time/position column and a retention column).
"""
from __future__ import annotations

import csv
import re
from collections import Counter
from pathlib import Path

from .util import fmt_ts, read_json, write_json


def parse_time(value: str) -> float:
    value = value.strip()
    if re.fullmatch(r"\d+(:\d{1,2}){1,2}", value):
        secs = 0.0
        for part in value.split(":"):
            secs = secs * 60 + float(part)
        return secs
    return float(value.rstrip("%"))


def load_csv(path: Path, duration: float) -> list[dict]:
    """Return points as {"t": seconds, "retention": 0-1+}."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return []
    cols = list(rows[0].keys())
    tcol = next((c for c in cols if re.search(r"time|position|elapsed|moment|second", c, re.I)), cols[0])
    rcol = next((c for c in cols if c != tcol and re.search(r"retention|watch ratio|audience|viewers", c, re.I)),
                cols[1] if len(cols) > 1 else None)
    if rcol is None:
        raise ValueError(f"no retention column found in {cols}")
    pts = []
    for row in rows:
        try:
            t = parse_time(row[tcol])
            r = float(str(row[rcol]).strip().rstrip("%"))
        except (ValueError, KeyError):
            continue
        pts.append({"t": t, "retention": r})
    if not pts:
        return []
    if re.search(r"%|percent", tcol, re.I):  # position given as % of the video
        for p in pts:
            p["t"] = p["t"] / 100 * duration
    elif max(p["t"] for p in pts) <= 1.0001:  # ratios, not seconds
        for p in pts:
            p["t"] *= duration
    if max(p["retention"] for p in pts) > 3:  # percentages
        for p in pts:
            p["retention"] /= 100
    return sorted(pts, key=lambda p: p["t"])


def from_api(points: list[dict], duration: float) -> list[dict]:
    return [{"t": p["ratio"] * duration, "retention": p["retention"]} for p in points]


def find_drops(points: list[dict], drop_pp: float = 3.0, skip_first_s: float = 0.0) -> list[dict]:
    """Steps where retention fell by >= drop_pp percentage points between neighbours."""
    drops = []
    for a, b in zip(points, points[1:]):
        if b["t"] < skip_first_s:
            continue
        delta = (a["retention"] - b["retention"]) * 100
        if delta >= drop_pp:
            drops.append({"t": round(b["t"], 1), "from": round(a["retention"] * 100, 1),
                          "to": round(b["retention"] * 100, 1), "drop_pp": round(delta, 1)})
    return sorted(drops, key=lambda d: -d["drop_pp"])


def annotate(drops: list[dict], lines: list[dict] | None, shots: list[dict] | None) -> list[dict]:
    out = []
    for d in drops:
        t = d["t"]
        a = dict(d, timestamp=fmt_ts(t))
        if lines:
            ln = next((l for l in lines if l["start"] - 0.5 <= t <= l["end"] + 1.5), None)
            ln = ln or min(lines, key=lambda l: abs(l["start"] - t))
            a.update(lyric=ln["text"], section=ln.get("section_type"))
        if shots:
            sh = next((s for s in shots if s["start"] <= t < s["end"]), None)
            if sh:
                a.update(shot=sh["id"], shot_kind=sh["kind"], on_screen=sh.get("characters"))
        a["diagnosis"] = diagnose(a)
        out.append(a)
    return out


def diagnose(a: dict) -> str:
    if a["t"] <= 10:
        return "Intro drop: the hook didn't land. Get to the character and the song faster."
    if a.get("shot_kind") == "still":
        return "Static shot: viewers left on an animated still. Use a real clip or more motion here."
    if a.get("shot_kind") == "reuse":
        return "Repeated chorus footage: vary the visuals on repeat choruses."
    if a.get("section") == "verse":
        return "Verse dragged: shorten the verse or add an action/sound to copy."
    if a.get("section") == "bridge":
        return "Bridge lost them: keep the bridge shorter or more physical."
    if a.get("section") == "outro":
        return "Outro exit (normal). Add an end-screen to the next video."
    return "Check this moment: the visual or the lyric stopped holding attention."


def report(video_title: str, annotated: list[dict], points: list[dict]) -> str:
    avg = sum(p["retention"] for p in points) / len(points) * 100 if points else 0
    md = [f"# Retention autopsy: {video_title}", "",
          f"Average retention across the video: **{avg:.0f}%**", "",
          "| When | Drop | Lyric being sung | Shot | Likely cause |", "|---|---|---|---|---|"]
    for a in annotated:
        md.append(f"| {a['timestamp']} | -{a['drop_pp']}pp ({a['from']}% → {a['to']}%) | "
                  f"{a.get('lyric', '-')} | {a.get('shot', '-')} ({a.get('shot_kind', '-')}) | {a['diagnosis']} |")
    if not annotated:
        md.append("| - | no drop above threshold | | | |")
    return "\n".join(md) + "\n"


def update_learnings(learnings_path: Path, video: str, annotated: list[dict]) -> None:
    data = read_json(learnings_path, {"drops": []})
    data["drops"] = [d for d in data["drops"] if d.get("video") != video]
    for a in annotated:
        data["drops"].append({"video": video, **{k: a.get(k) for k in
                                                  ("t", "drop_pp", "lyric", "section", "shot_kind", "diagnosis")}})
    write_json(learnings_path, data)


def summarize_learnings(learnings_path: Path, top: int = 4) -> str:
    """Short text fed back into the songwriting prompt."""
    data = read_json(learnings_path, {"drops": []})
    if not data["drops"]:
        return ""
    c = Counter(d["diagnosis"] for d in data["drops"])
    return " ".join(f"({n}x) {diag}" for diag, n in c.most_common(top))
