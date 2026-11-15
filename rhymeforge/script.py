"""Song script: lyrics in sections, a scored hook, timing and a safety review."""
from __future__ import annotations

from .hooks import best_hook
from .llm import LLM, LLMError, SYSTEM
from .util import log

# Section plan for ~3 minutes at ~4.4 s per line (110 bpm, 2 bars per line).
STRUCTURE = [("intro", 2), ("verse", 6), ("chorus", 4), ("verse", 6), ("chorus", 4),
             ("bridge", 4), ("verse", 6), ("chorus", 4), ("outro", 4)]

BANNED = {"kill", "die", "dead", "blood", "gun", "knife", "hate", "stupid", "shut up", "monster under",
          "alone in the bath", "candy from", "stranger"}


def lines_needed(duration_s: float, bpm: float, bars_per_line: int = 2) -> int:
    line_s = bars_per_line * 4 * 60 / bpm
    return max(8, round((duration_s - 6) / line_s))


def write_script(llm: LLM, idea: dict, cfg: dict, learnings: str = "") -> dict:
    hook = best_hook(llm, idea, cfg)
    names = [c["name"] for c in cfg["channel"]["characters"]]
    vcfg = cfg["video"]
    n_lines = lines_needed(vcfg["duration_s"], vcfg["bpm"])
    script = None
    if llm.available:
        prompt = f"""Write an ORIGINAL toddler song. Idea: {idea}
Characters: {cfg['channel']['characters']}
The very first line must be this hook: "{hook['text']}"
Target about {n_lines} sung lines total (~{vcfg['duration_s']}s at {vcfg['bpm']} bpm).
Use this section order: {[s for s, _ in STRUCTURE]}. The chorus is identical every time and
carries the learning goal. Lines of 4-8 simple words. Lots of repetition, sound words and
actions the child can copy. Each verse introduces new items from: {idea.get('items')}.
{('Lessons from our audience retention data: ' + learnings) if learnings else ''}
Return JSON: {{"title": str, "learning_goal": str, "music_tags": "comma separated genre/instrument/mood tags",
"sections": [{{"type": "intro|verse|chorus|bridge|outro", "lines": [str]}}]}}"""
        try:
            script = llm.json(prompt, system=SYSTEM)
            assert script["sections"] and all(s["lines"] for s in script["sections"])
        except (LLMError, AssertionError, KeyError, TypeError) as e:
            log(f"script: LLM failed ({e}); using offline template")
            script = None
    if script is None:
        script = offline_script(idea, names, hook["text"])
    # Make sure the hook really opens the song.
    first = script["sections"][0]["lines"]
    if first and first[0].strip().lower() != hook["text"].strip().lower():
        first.insert(0, hook["text"])
    script["hook"] = {k: hook[k] for k in ("text", "formula", "score", "heuristic", "llm", "passed", "feedback")}
    script["hook_candidates"] = [{k: h[k] for k in ("text", "formula", "score")} for h in hook["all"]]
    script["topic"] = idea.get("topic")
    script["idea"] = idea
    script["music_tags"] = script.get("music_tags") or default_music_tags(vcfg["bpm"])
    script["safety"] = safety_review(llm, script)
    return script


def default_music_tags(bpm: int) -> str:
    return (f"children's song, nursery rhyme, happy, playful, ukulele, glockenspiel, light percussion, "
            f"clear female vocal, simple melody, {bpm} bpm")


def offline_script(idea: dict, names: list[str], hook: str) -> dict:
    lead = names[0] if names else "Pip"
    friend = names[1] if len(names) > 1 else "Friend"
    items = list(idea.get("items") or ["one", "two", "three", "four", "five"])
    topic = idea.get("topic", "fun").replace("_", " ")
    chorus = [f"{topic.title()}, {topic}, sing along!", "Clap your hands and sing the song!",
              f"{lead} and {friend} sing with you,", f"Learning {topic} is fun for me and you!"]
    sections = []
    vi = 0
    for kind, n in STRUCTURE:
        if kind == "intro":
            lines = [hook, f"Hello! It's {lead} and {friend}!"]
        elif kind == "chorus":
            lines = list(chorus)
        elif kind == "verse":
            lines = []
            for k in range(n // 2):
                it = items[(vi + k) % len(items)]
                lines += [f"{lead} sees {it}, {it}, {it}!", f"Can you say {it}? Yay, yay, yay!"]
            vi += n // 2
        elif kind == "bridge":
            lines = ["Stomp stomp, clap clap,", "Wiggle wiggle, tap tap tap,", "Jump up high and touch the sky,",
                     "Now sit down and wave goodbye... not yet!"]
        else:
            lines = [f"Bye bye {topic}, see you soon!", f"{lead} says thank you, la la la!",
                     f"{friend} says thank you, la la la!", "Bye bye, friends! Bye bye!"]
        sections.append({"type": kind, "lines": lines[:n] if kind != "intro" else lines})
    return {"title": idea.get("working_title", f"{topic.title()} Song"),
            "learning_goal": idea.get("learning_goal", topic), "sections": sections}


def safety_review(llm: LLM, script: dict) -> dict:
    text = " ".join(l for s in script["sections"] for l in s["lines"]).lower()
    flags = sorted(w for w in BANNED if w in text)
    review = {"banned_words": flags, "llm": None, "ok": not flags}
    if llm.available:
        try:
            r = llm.json(f"""Review these toddler song lyrics for a 1-4 year old audience.
Flag anything unsafe to imitate (e.g. child alone in water, small objects in mouth), scary,
mean, or confusing. Lyrics: {script['sections']}
Return JSON {{"ok": bool, "issues": [str]}}""", system=SYSTEM, temperature=0.1)
            review["llm"] = r
            review["ok"] = review["ok"] and bool(r.get("ok", True))
        except (LLMError, TypeError) as e:
            log(f"safety review: LLM failed ({e})")
    return review


def lyric_lines(script: dict) -> list[dict]:
    """Flatten sections into lines with section info (index, type, occurrence)."""
    out = []
    seen: dict[str, int] = {}
    for si, sec in enumerate(script["sections"]):
        occ = seen.get(sec["type"], 0)
        seen[sec["type"]] = occ + 1
        for li, line in enumerate(sec["lines"]):
            out.append({"text": line, "section": si, "section_type": sec["type"],
                        "occurrence": occ, "line_in_section": li})
    return out


def estimate_timing(lines: list[dict], duration_s: float, intro_pad: float = 2.0,
                    outro_pad: float = 3.0) -> list[dict]:
    """Spread lines evenly when no word-level alignment is available."""
    usable = duration_s - intro_pad - outro_pad
    step = usable / max(len(lines), 1)
    out = []
    for i, ln in enumerate(lines):
        d = dict(ln)
        d["start"] = round(intro_pad + i * step, 3)
        d["end"] = round(intro_pad + (i + 1) * step, 3)
        out.append(d)
    return out


def ace_step_lyrics(script: dict) -> str:
    """Lyrics in ACE-Step's [section] tag format."""
    blocks = []
    for sec in script["sections"]:
        tag = {"intro": "intro", "outro": "outro", "bridge": "bridge", "chorus": "chorus"}.get(sec["type"], "verse")
        blocks.append(f"[{tag}]\n" + "\n".join(sec["lines"]))
    return "\n\n".join(blocks)
