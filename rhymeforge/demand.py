"""Topic demand from YouTube search autocomplete (free, no API key, no quota).

YouTube only suggests searches people actually type a lot, so for each seed topic we
ask for completions of a few phrasings ("brushing teeth song", "brushing teeth song for")
and score how many kid-song suggestions come back and how high they rank.
This is a rough signal of search demand, not a search volume. Use it to pick between
topics, and combine it with the outlier scouting (`niches`) when you have a YouTube key.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import requests

from .util import log, read_json, write_json

# Seed topics -> the phrase people search for. Keys match niches.TOPICS where possible.
SEED_TOPICS: dict[str, str] = {
    "colors": "colors song",
    "animals": "animal sounds song",
    "bath_time": "bath song",
    "hygiene": "brushing teeth song",
    "counting": "counting song",
    "alphabet": "abc song",
    "bedtime": "bedtime song",
    "potty": "potty song",
    "shapes": "shapes song",
    "feelings": "feelings song",
    "vehicles": "wheels on the bus",
    "food": "fruit song",
    "dinosaurs": "dinosaur song",
    "weather": "rain song",
    "body": "body parts song",
    "manners": "sharing song",
    "opposites": "opposites song",
    "peekaboo": "peekaboo song",
}

SUGGEST_URL = "https://suggestqueries.google.com/complete/search"
KID_WORDS = ("kid", "toddler", "baby", "nursery", "preschool", "children", "song", "rhyme", "learn")

# Used offline/in tests: a plausible shape, NOT real data.
SAMPLE = {
    "hygiene": ["brushing teeth song", "brushing teeth song for kids", "brushing teeth song for toddlers",
                "brushing teeth song 2 minutes", "brushing teeth song cocomelon"],
    "bath_time": ["bath song", "bath song for kids", "bath song for toddlers", "bath song nursery rhyme"],
    "colors": ["colors song", "colors song for kids", "colors song for toddlers", "colors song rainbow",
               "colors song learn colours", "colors song for babies"],
    "animals": ["animal sounds song", "animal sounds song for kids", "animal sounds song farm"],
}


def suggestions(query: str, timeout: float = 15) -> list[str]:
    r = requests.get(SUGGEST_URL, params={"client": "firefox", "ds": "yt", "q": query, "hl": "en"},
                     timeout=timeout, headers={"User-Agent": "Mozilla/5.0 (rhymeforge)"})
    r.raise_for_status()
    data = json.loads(r.content.decode("utf-8", "replace"))
    return [s for s in data[1] if isinstance(s, str)]


def score_suggestions(phrase: str, sugg: list[str]) -> float:
    """More completions, and kid-specific ones near the top, mean more demand."""
    score = 0.0
    for rank, s in enumerate(sugg):
        weight = 1.0 / (1 + 0.15 * rank)
        kid = any(w in s.lower() for w in KID_WORDS)
        score += weight * (1.5 if kid else 1.0)
    return round(score, 2)


def topic_demand(cfg: dict, data_dir: Path, offline: bool = False, topics: dict[str, str] | None = None,
                 cache_hours: float = 24, pause_s: float = 0.4) -> list[dict]:
    """[{topic, phrase, demand, suggestions}] sorted by demand, cached for a day."""
    topics = topics or cfg.get("ideas", {}).get("seed_topics") or SEED_TOPICS
    cache = Path(data_dir) / "demand_cache.json"
    cached = read_json(cache, {}) or {}
    out = []
    for topic, phrase in topics.items():
        hit = cached.get(phrase)
        if hit and time.time() - hit["at"] < cache_hours * 3600:
            sugg = hit["s"]
        elif offline:
            sugg = SAMPLE.get(topic, [phrase, phrase + " for kids"])
        else:
            sugg = []
            for q in (phrase, phrase + " for"):
                try:
                    sugg += [s for s in suggestions(q) if s not in sugg]
                except (requests.RequestException, ValueError, IndexError) as e:
                    log(f"autocomplete failed for {q!r}: {e}")
                time.sleep(pause_s)
            cached[phrase] = {"at": time.time(), "s": sugg}
        out.append({"topic": topic, "phrase": phrase, "demand": score_suggestions(phrase, sugg),
                    "suggestions": sugg[:10]})
    if not offline:
        write_json(cache, cached)
    return sorted(out, key=lambda d: -d["demand"])


def pick_topic(demand: list[dict], history: list[dict] | None = None) -> dict:
    """Highest demand, halved for each of the last 8 videos on the same topic."""
    recent = [h.get("topic") for h in (history or [])[-8:]]
    return max(demand, key=lambda d: d["demand"] * 0.5 ** recent.count(d["topic"]))


def boost_niches(niches: list[dict], demand: list[dict]) -> list[dict]:
    """Blend autocomplete demand into the outlier gap scores (x0.5 .. x1.5)."""
    if not demand:
        return niches
    top = max(d["demand"] for d in demand) or 1
    by_topic = {d["topic"]: d["demand"] / top for d in demand}
    for n in niches:
        n["search_demand"] = round(by_topic.get(n["topic"], 0.0), 2)
        n["gap_score"] = round(n["gap_score"] * (0.5 + by_topic.get(n["topic"], 0.0)), 2)
    return sorted(niches, key=lambda n: -n["gap_score"])
