"""Niche finder + idea generator.

Groups outlier videos into toddler topics, scores each topic by demand
(how hard its videos beat their channels) versus supply (how many uploads
there are), then asks the LLM for an original angle in the best gaps.
"""
from __future__ import annotations

import math

from .llm import LLM, LLMError, SYSTEM
from .util import content_words, jaccard, log

TOPICS: dict[str, list[str]] = {
    "colors": ["color", "colour", "red", "blue", "green", "yellow", "rainbow"],
    "counting": ["count", "number", "numbers", "five little", "ten", "1 2 3"],
    "alphabet": ["abc", "alphabet", "letter", "phonics"],
    "animals": ["animal", "farm", "zoo", "cow", "duck", "bunny", "bunnies", "dog", "cat"],
    "vehicles": ["truck", "car", "bus", "train", "tractor", "wheels", "fire truck", "garbage"],
    "bath_time": ["bath", "splash", "bubbles"],
    "bedtime": ["bedtime", "lullaby", "sleep", "goodnight", "moon"],
    "potty": ["potty", "toilet"],
    "hygiene": ["brush", "teeth", "wash", "hands"],
    "feelings": ["feeling", "feelings", "emotion", "happy", "sad", "angry"],
    "shapes": ["shape", "circle", "square", "triangle"],
    "food": ["fruit", "vegetable", "vegetables", "food", "eat", "banana", "apple"],
    "dinosaurs": ["dinosaur", "dino", "t-rex"],
    "weather": ["rain", "rainy", "snow", "sun", "weather", "puddle"],
    "body": ["body", "head", "shoulders", "nose", "eyes"],
    "manners": ["sharing", "please", "thank you", "manners", "sorry"],
    "opposites": ["opposite", "opposites", "big and small"],
    "peekaboo": ["peekaboo", "hide and seek"],
}

# What a toddler song in each topic can teach, used for offline ideas.
TOPIC_ITEMS: dict[str, list[str]] = {
    "colors": ["red", "blue", "yellow", "green", "orange", "purple"],
    "counting": ["one", "two", "three", "four", "five"],
    "alphabet": ["A for apple", "B for ball", "C for cat", "D for duck", "E for egg"],
    "animals": ["cow", "duck", "sheep", "pig", "horse"],
    "vehicles": ["bus", "train", "boat", "plane", "tractor"],
    "bath_time": ["bubbles", "rubber duck", "soap", "towel", "splash"],
    "bedtime": ["pajamas", "teeth", "story", "teddy", "moon"],
    "potty": ["potty", "flush", "wash", "dry", "hooray"],
    "hygiene": ["toothbrush", "toothpaste", "scrub", "rinse", "smile"],
    "feelings": ["happy", "sad", "angry", "scared", "calm"],
    "shapes": ["circle", "square", "triangle", "star", "heart"],
    "food": ["apple", "banana", "carrot", "peas", "bread"],
    "dinosaurs": ["stomp", "roar", "long neck", "tiny arms", "egg"],
    "weather": ["sun", "rain", "wind", "snow", "rainbow"],
    "body": ["head", "nose", "hands", "knees", "toes"],
    "manners": ["please", "thank you", "share", "sorry", "wait"],
    "opposites": ["big and small", "up and down", "fast and slow", "loud and quiet", "in and out"],
    "peekaboo": ["behind the tree", "under the bed", "in the box", "behind the door", "under the hat"],
}


def classify(title: str) -> list[str]:
    t = title.lower()
    return [topic for topic, kws in TOPICS.items() if any(k in t for k in kws)]


def rank_niches(scored_videos: list[dict], history: list[dict] | None = None,
                outlier_threshold: float = 3.0) -> list[dict]:
    """gap = demand / sqrt(supply + 1), minus a penalty for topics you did recently."""
    stats: dict[str, dict] = {}
    for v in scored_videos:
        for topic in classify(v["title"]):
            s = stats.setdefault(topic, {"topic": topic, "supply": 0, "demand": 0.0, "examples": []})
            s["supply"] += 1
            if v["outlier"] >= outlier_threshold:
                s["demand"] += v["outlier"]
                s["examples"].append({"title": v["title"], "outlier": v["outlier"],
                                      "channel": v["channel_title"], "video_id": v["video_id"]})
    recent = [h.get("topic") for h in (history or [])[-8:]]
    out = []
    for s in stats.values():
        if s["demand"] <= 0:
            continue
        gap = s["demand"] / math.sqrt(s["supply"] + 1)
        gap *= 0.5 ** recent.count(s["topic"])  # don't do the same topic every week
        s["gap_score"] = round(gap, 2)
        s["examples"] = sorted(s["examples"], key=lambda e: -e["outlier"])[:5]
        out.append(s)
    return sorted(out, key=lambda s: -s["gap_score"])


def is_unique(title: str, others: list[str], threshold: float = 0.6) -> bool:
    cw = content_words(title)
    return all(jaccard(cw, content_words(o)) < threshold for o in others)


def generate_ideas(llm: LLM, niches: list[dict], count: int, cfg: dict,
                   history: list[dict] | None = None) -> list[dict]:
    history = history or []
    avoid = [h.get("title", "") for h in history] + [e["title"] for n in niches for e in n["examples"]]
    chars = cfg["channel"]["characters"]
    ideas: list[dict] = []
    if llm.available:
        prompt = f"""Our cast: {chars}
Top under-served topics (gap_score = demand/supply, higher is better), with videos that beat their channel average:
{[{k: n[k] for k in ('topic', 'gap_score', 'examples')} for n in niches[:8]]}

Propose {count + 3} ORIGINAL song ideas, each on a different topic from the list above.
Each needs a fresh angle that is clearly different from the example titles (combine a topic with a
routine, a twist or a second skill, e.g. "counting + bath time"). Teach exactly one thing.
Return JSON list of objects with keys:
topic, working_title, angle, learning_goal, items (4-6 concrete things the song names), why_it_can_win."""
        try:
            raw = llm.json(prompt, system=SYSTEM)
            for idea in raw:
                if idea.get("topic") and is_unique(idea["working_title"], avoid + [i["working_title"] for i in ideas]):
                    ideas.append(idea)
        except (LLMError, ValueError, TypeError, KeyError) as e:
            log(f"ideas: LLM failed ({e}); using offline ideas")
    if len(ideas) < count:
        ideas += offline_ideas(niches, count - len(ideas), chars,
                               exclude={i["topic"] for i in ideas}, avoid=avoid)
    return ideas[:count]


def offline_ideas(niches: list[dict], count: int, chars: list[dict], exclude: set | None = None,
                  avoid: list[str] | None = None) -> list[dict]:
    exclude = exclude or set()
    topics = [n["topic"] for n in niches if n["topic"] not in exclude] or list(TOPIC_ITEMS)
    lead = chars[0]["name"] if chars else "Pip"
    out = []
    for topic in topics:
        if len(out) >= count:
            break
        nice = topic.replace("_", " ")
        idea = {
            "topic": topic,
            "working_title": f"{lead}'s {nice.title()} Adventure",
            "angle": f"{lead} and friends discover {nice} through a playful everyday moment",
            "learning_goal": f"name and recognise {nice}",
            "items": TOPIC_ITEMS.get(topic, ["one", "two", "three", "four"]),
            "why_it_can_win": "high demand relative to supply in scouting data",
        }
        if is_unique(idea["working_title"], avoid or []):
            out.append(idea)
    return out
