"""Storyboard: one shot per lyric line, with a GPU budget.

Shot kinds:
  ai_clip  - generated keyframe image + image-to-video clip (the expensive kind)
  reuse    - repeat chorus line: reuse the shot from the first chorus
  library  - an existing clip from the clip library
  still    - generated image animated with zoom/pan/beat-bounce in ffmpeg
"""
from __future__ import annotations

from .library import ClipLibrary
from .llm import LLM, LLMError, SYSTEM
from .util import log

MOTIONS = ["push_in", "bounce", "pan_left", "pull_out", "pan_right", "bounce"]


def characters_in(text: str, names: list[str]) -> list[str]:
    low = text.lower()
    return [n for n in names if n.lower() in low]


def plan_shots(lines: list[dict], unique_clips: int, names: list[str],
               library: ClipLibrary | None = None) -> list[dict]:
    shots: list[dict] = []
    first_chorus_shot: dict[int, int] = {}  # line_in_section -> shot index
    for i, ln in enumerate(lines):
        shot = {"id": f"s{i:02d}", "line": ln["text"], "section": ln["section"],
                "section_type": ln["section_type"], "occurrence": ln["occurrence"],
                "characters": characters_in(ln["text"], names) or names[:1],
                "motion": MOTIONS[i % len(MOTIONS)], "kind": "still"}
        if ln["section_type"] == "chorus" and ln["occurrence"] > 0 and ln["line_in_section"] in first_chorus_shot:
            shot["kind"] = "reuse"
            shot["reuse_of"] = shots[first_chorus_shot[ln["line_in_section"]]]["id"]
        if ln["section_type"] == "chorus" and ln["occurrence"] == 0:
            first_chorus_shot[ln["line_in_section"]] = i
        shots.append(shot)

    # Priority for the AI clip budget: hook, first chorus, first line of each verse/bridge, outro.
    def priority(s: dict) -> tuple:
        idx = int(s["id"][1:])
        if idx == 0:
            return (0, idx)
        if s["section_type"] == "chorus" and s["occurrence"] == 0:
            return (1, idx)
        first_in_section = idx == 0 or shots[idx - 1]["section"] != s["section"]
        if first_in_section and s["section_type"] in ("verse", "bridge", "outro"):
            return (2, idx)
        return (3, idx)

    # Library clips first (free), then spend the GPU budget on the next-best shots,
    # so every week still adds `unique_clips` new clips to the library.
    used: set[str] = set()
    if library:
        for s in sorted([s for s in shots if s["kind"] == "still"], key=priority):
            hit = library.find(s["characters"], s["line"], exclude=used)
            if hit:
                s["kind"] = "library"
                s["library_id"] = hit["id"]
                used.add(hit["id"])
    budget = unique_clips
    for s in sorted([s for s in shots if s["kind"] == "still"], key=priority):
        if budget <= 0:
            break
        s["kind"] = "ai_clip"
        budget -= 1
    return shots


def add_prompts(llm: LLM, shots: list[dict], script: dict, cfg: dict) -> list[dict]:
    chars = {c["name"]: c["description"] for c in cfg["channel"]["characters"]}
    style = cfg["channel"]["style_bible"]
    need = [s for s in shots if s["kind"] in ("ai_clip", "still")]
    scenes: dict[str, dict] = {}
    if llm.available and need:
        prompt = f"""For each lyric line of a toddler music video, describe ONE simple visual scene and ONE
gentle motion. Topic: {script.get('topic')}. Keep the same location within a section.
Lines: {[{'id': s['id'], 'line': s['line'], 'section': s['section_type'], 'characters': s['characters']} for s in need]}
Return JSON list of {{"id", "scene" (what we see, <=30 words, no character descriptions), "motion" (<=12 words, e.g. "Pip hops and waves")}}."""
        try:
            for r in llm.json_list(prompt, system=SYSTEM):
                scenes[r["id"]] = r
        except (LLMError, TypeError, KeyError) as e:
            log(f"storyboard: LLM failed ({e}); using template prompts")
    for s in need:
        sc = scenes.get(s["id"], {})
        scene = sc.get("scene") or f"a cheerful scene illustrating: {s['line']}"
        cast = "; ".join(f"{n}, {chars[n]}" for n in s["characters"] if n in chars)
        s["image_prompt"] = f"{style}. {cast}. {scene}"
        s["motion_prompt"] = (f"{style}. {cast}. {sc.get('motion') or 'gentle happy bouncing, small head tilt, waving'}. "
                              f"smooth slow camera, consistent characters")
    return shots


def build_storyboard(llm: LLM, script: dict, timed_lines: list[dict], cfg: dict,
                     library: ClipLibrary | None = None) -> dict:
    names = [c["name"] for c in cfg["channel"]["characters"]]
    shots = plan_shots(timed_lines, cfg["video"]["unique_clips"], names, library)
    shots = add_prompts(llm, shots, script, cfg)
    counts = {k: sum(1 for s in shots if s["kind"] == k) for k in ("ai_clip", "reuse", "library", "still")}
    return {"shots": shots, "counts": counts}
