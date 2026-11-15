"""Title + thumbnail written as ONE pairing.

The title and thumbnail text must do different jobs: the title says what it is
(topic + who it's for) and the thumbnail text gives the feeling or the moment.
Pairs where the two repeat each other are rejected.
"""
from __future__ import annotations

from .llm import LLM, LLMError, SYSTEM
from .util import content_words, fmt_ts, jaccard, log

MAX_TITLE = 70
MAX_THUMB_WORDS = 4
MAX_OVERLAP = 0.25


def overlap(title: str, thumb_text: str) -> float:
    """Share of thumbnail words already in the title (0 = fully complementary)."""
    t, th = content_words(title), content_words(thumb_text)
    if not th:
        return 0.0
    return max(len(t & th) / len(th), jaccard(t, th))


def validate_pair(pair: dict, topic: str) -> list[str]:
    problems = []
    title, thumb = pair.get("title", ""), pair.get("thumbnail_text", "")
    if not title or len(title) > MAX_TITLE:
        problems.append(f"title must be 1-{MAX_TITLE} chars")
    if len(thumb.split()) > MAX_THUMB_WORDS or not thumb:
        problems.append(f"thumbnail text must be 1-{MAX_THUMB_WORDS} words")
    if overlap(title, thumb) > MAX_OVERLAP:
        problems.append("thumbnail text repeats the title")
    topic_words = content_words(topic.replace("_", " "))
    if topic_words and not (topic_words & content_words(title)) and topic not in title.lower():
        problems.append("title doesn't name the topic")
    return problems


def heuristic_ctr(pair: dict) -> int:
    score = 50
    title, thumb = pair["title"], pair["thumbnail_text"]
    if 30 <= len(title) <= 60:
        score += 10
    if any(w in title.lower() for w in ("song", "kids", "toddler", "learn")):
        score += 10
    if thumb.endswith(("!", "?")):
        score += 10
    if len(thumb.split()) <= 2:
        score += 10
    score -= int(40 * overlap(title, thumb))
    return max(0, min(100, score))


def make_package(llm: LLM, script: dict, cfg: dict, timed_lines: list[dict] | None = None) -> dict:
    topic = script.get("topic") or ""
    chars = cfg["channel"]["characters"]
    candidates: list[dict] = []
    if llm.available:
        prompt = f"""Create 6 title + thumbnail PAIRS for this toddler song. Parents choose what toddlers watch,
so the title tells a parent what the child learns; the thumbnail text (max {MAX_THUMB_WORDS} words) adds
emotion or the key moment and must NOT repeat words from the title. The thumbnail_scene describes one
bold, simple image (1-2 characters, big face, one object, high contrast) for an image generator.
Song: title={script.get('title')!r}, learning_goal={script.get('learning_goal')!r}, hook={script['hook']['text']!r}
Characters: {chars}
Return JSON list of {{"title", "thumbnail_text", "thumbnail_scene", "ctr_score" (0-100 your estimate)}}."""
        try:
            candidates = llm.json_list(prompt, system=SYSTEM)
        except (LLMError, TypeError) as e:
            log(f"packaging: LLM failed ({e}); using templates")
    candidates = [c for c in candidates if isinstance(c, dict)] + offline_pairs(script, chars)
    valid = []
    for c in candidates:
        c["problems"] = validate_pair(c, topic)
        c["ctr_score"] = int(c.get("ctr_score") or heuristic_ctr(c))
        c["overlap"] = round(overlap(c.get("title", ""), c.get("thumbnail_text", "")), 2)
        if not c["problems"]:
            valid.append(c)
    pool = valid or candidates
    best = max(pool, key=lambda c: (c["ctr_score"] + heuristic_ctr(c)) / 2)
    return {
        "title": best["title"],
        "thumbnail_text": best["thumbnail_text"],
        "thumbnail_scene": best.get("thumbnail_scene") or default_scene(script, chars),
        "ctr_score": best["ctr_score"],
        "overlap": best["overlap"],
        "rejected": [{"title": c.get("title"), "thumbnail_text": c.get("thumbnail_text"), "problems": c["problems"]}
                     for c in candidates if c["problems"]],
        "description": description(script, best["title"], timed_lines, cfg),
        "tags": tags(script, cfg),
    }


def default_scene(script: dict, chars: list[dict]) -> str:
    c = chars[0] if chars else {"description": "a cute cartoon chick"}
    item = (script.get("idea", {}).get("items") or ["a toy"])[0]
    return f"close-up of {c['description']}, excited open-mouth smile, holding {item}, bright solid background"


def offline_pairs(script: dict, chars: list[dict]) -> list[dict]:
    topic = (script.get("topic") or "fun").replace("_", " ")
    lead = chars[0]["name"] if chars else "Pip"
    return [
        {"title": f"{topic.title()} Song for Toddlers | Learn with {lead}", "thumbnail_text": "Uh-oh!",
         "thumbnail_scene": default_scene(script, chars)},
        {"title": f"Learn {topic.title()} | Kids Songs & Nursery Rhymes", "thumbnail_text": "Peekaboo!",
         "thumbnail_scene": default_scene(script, chars)},
        {"title": f"{lead}'s {topic.title()} Song | Toddler Learning", "thumbnail_text": "Sing along!",
         "thumbnail_scene": default_scene(script, chars)},
    ]


def description(script: dict, title: str, timed_lines: list[dict] | None, cfg: dict) -> str:
    chars = " & ".join(c["name"] for c in cfg["channel"]["characters"])
    parts = [f"{title}", "",
             f"Sing along with {chars}! In this song your little one learns: {script.get('learning_goal')}.", ""]
    if timed_lines:
        parts.append("Chapters:")
        seen = set()
        n = 0
        for ln in timed_lines:
            key = (ln["section"])
            if key in seen:
                continue
            seen.add(key)
            n += 1
            start = 0 if n == 1 else ln["start"]
            label = ln["section_type"].title()
            parts.append(f"{fmt_ts(start)} {label}")
        parts.append("")
    parts += ["Lyrics:"] + [l for s in script["sections"] for l in s["lines"]] + ["",
              "Made with the help of AI tools and reviewed by a human before publishing.",
              "#nurseryrhymes #kidssongs #toddlerlearning"]
    return "\n".join(parts)


def tags(script: dict, cfg: dict) -> list[str]:
    topic = (script.get("topic") or "").replace("_", " ")
    t = list(cfg["channel"]["default_tags"]) + [f"{topic} song", f"learn {topic}"]
    t += [c["name"] for c in cfg["channel"]["characters"]]
    out, total = [], 0
    for tag in dict.fromkeys(t):
        if total + len(tag) > 450:
            break
        out.append(tag)
        total += len(tag) + 1
    return out
