"""21 hook formulas adapted for toddlers, plus a hook scorer.

The score blends a deterministic heuristic (short, participatory, sound words,
simple vocabulary, repetition, character name) with an LLM rubric score when
available. Hooks under the threshold are rewritten before anything is generated.
"""
from __future__ import annotations

import re

from .llm import LLM, LLMError, SYSTEM
from .util import log, words

FORMULAS: list[dict] = [
    {"id": "name_call", "name": "Name call", "pattern": "Greet the child directly with the character", "example": "Hi friend! It's me, Pip!"},
    {"id": "sound_question", "name": "Sound effect + question", "pattern": "Sound effect, then a who/what question", "example": "Choo choo! Who's on the train?"},
    {"id": "peekaboo", "name": "Peekaboo reveal", "pattern": "Hide something, then reveal it", "example": "Peekaboo! Where is Coco?"},
    {"id": "count_in", "name": "Count-in", "pattern": "Count up into the song", "example": "One, two, three, let's go!"},
    {"id": "animal_guess", "name": "Animal sound guess", "pattern": "Make a sound, ask who makes it", "example": "Moo! Who says moo?"},
    {"id": "i_spy", "name": "I spy", "pattern": "I see something + attribute", "example": "I see something red!"},
    {"id": "tiny_problem", "name": "Tiny problem", "pattern": "Uh-oh + a small, safe problem", "example": "Uh-oh! Pip lost a sock!"},
    {"id": "move_invite", "name": "Movement invitation", "pattern": "Invite a body movement", "example": "Clap your hands with me!"},
    {"id": "routine_chant", "name": "Routine chant", "pattern": "Name a daily routine with repetition", "example": "Bath time, bath time, splash splash splash!"},
    {"id": "mystery_box", "name": "Mystery box", "pattern": "What's inside?", "example": "What's inside the box?"},
    {"id": "call_response", "name": "Call and response", "pattern": "When I say X, you say Y", "example": "When I say Pip, you say hooray!"},
    {"id": "silly_mistake", "name": "Silly mistake", "pattern": "Deliberately wrong fact the child corrects", "example": "A cow says... quack? No!"},
    {"id": "emotion_mirror", "name": "Emotion mirror", "pattern": "Character feels something, can we help?", "example": "Coco feels sad. Can we help?"},
    {"id": "countdown", "name": "Countdown", "pattern": "Count down to an event", "example": "Five little ducks... ready?"},
    {"id": "whisper", "name": "Whisper secret", "pattern": "Quiet voice, a secret", "example": "Shh... baby bear is sleeping."},
    {"id": "big_little", "name": "Big/little contrast", "pattern": "Contrast loud/quiet or big/small", "example": "Big drum BOOM! Little drum tap!"},
    {"id": "whats_this", "name": "What's this?", "pattern": "Show an object and name it", "example": "What's this? A banana!"},
    {"id": "rhyme_tease", "name": "Rhyme tease", "pattern": "Two rhymes, ask for the next", "example": "Cat, hat... what rhymes with cat?"},
    {"id": "signature", "name": "Signature jingle", "pattern": "Channel catchphrase", "example": "It's Pip and Coco time!"},
    {"id": "challenge", "name": "Can you...?", "pattern": "Small physical challenge", "example": "Can you touch your nose?"},
    {"id": "world_change", "name": "World change", "pattern": "Look! Something in the world changed", "example": "Look! It's snowing!"},
]

SOUND_WORDS = {
    "moo", "quack", "baa", "oink", "neigh", "woof", "meow", "roar", "choo", "beep", "honk", "vroom",
    "splash", "pop", "boom", "tap", "clap", "stomp", "whoosh", "ding", "dong", "la", "yay", "hooray",
    "peekaboo", "shh", "uh-oh", "oh", "wow", "boing", "tick", "tock", "zoom", "buzz", "tweet",
}
INVITE_STARTS = ("clap", "touch", "let's", "lets", "can you", "look", "where", "who", "what",
                 "jump", "wave", "stomp", "say", "sing", "count", "shake", "find", "when i say")


def syllables(word: str) -> int:
    w = word.lower().strip("'")
    groups = re.findall(r"[aeiouy]+", w)
    n = len(groups)
    if w.endswith("e") and n > 1 and not w.endswith(("le", "ee")):
        n -= 1
    return max(n, 1)


def heuristic_score(hook: str, character_names: list[str] | None = None) -> dict:
    ws = words(hook)
    names = [n.lower() for n in (character_names or [])]
    parts: dict[str, int] = {}
    n = len(ws)
    parts["short"] = 25 if n <= 8 else 20 if n <= 12 else 10 if n <= 16 else 0
    parts["address"] = 15 if (any(w in names for w in ws) or "you" in ws or "friend" in ws) else 0
    has_sound = any(w in SOUND_WORDS for w in ws) or any(ws[i] == ws[i + 1] for i in range(n - 1))
    parts["sound"] = 15 if has_sound else 0
    low = hook.lower().strip()
    parts["participation"] = 20 if (hook.strip().endswith("?") or low.startswith(INVITE_STARTS)
                                    or "with me" in low) else 0
    avg_syl = sum(syllables(w) for w in ws) / n if n else 9
    parts["simple"] = 15 if avg_syl <= 1.5 else 8 if avg_syl <= 1.8 else 0
    parts["repetition"] = 10 if len(set(ws)) < n else 0
    return {"score": sum(parts.values()), "parts": parts}


def score_hook(llm: LLM, hook: str, idea: dict, character_names: list[str]) -> dict:
    h = heuristic_score(hook, character_names)
    result = {"text": hook, "heuristic": h["score"], "parts": h["parts"], "llm": None, "score": h["score"],
              "feedback": ""}
    if llm.available:
        prompt = f"""Score this opening hook for a toddler song (ages 1-4) about "{idea.get('topic')}".
Hook: "{hook}"
Rubric (0-100 total): instantly clear to a 2-year-old (25), triggers curiosity or anticipation (25),
invites participation - sing/move/answer (20), easy to repeat and remember (15), fresh vs typical
nursery content (15). Return JSON: {{"score": int, "feedback": "one sentence on how to improve"}}"""
        try:
            r = llm.json(prompt, system=SYSTEM, temperature=0.2)
            result["llm"] = int(r["score"])
            result["feedback"] = r.get("feedback", "")
            result["score"] = round(0.6 * result["llm"] + 0.4 * h["score"])
        except (LLMError, KeyError, ValueError, TypeError) as e:
            log(f"hook scoring: LLM failed ({e}); heuristic only")
    return result


def best_hook(llm: LLM, idea: dict, cfg: dict, candidates: list[dict] | None = None) -> dict:
    """Generate hooks from several formulas, score them, rewrite until one passes."""
    names = [c["name"] for c in cfg["channel"]["characters"]]
    min_score = cfg["hooks"]["min_score"]
    candidates = candidates or draft_hooks(llm, idea, names)
    scored = [dict(score_hook(llm, c["text"], idea, names), formula=c["formula"]) for c in candidates]
    best = max(scored, key=lambda s: s["score"])
    rounds = 0
    while best["score"] < min_score and rounds < cfg["hooks"]["max_rewrites"] and llm.available:
        rounds += 1
        log(f"hook '{best['text']}' scored {best['score']} < {min_score}; rewrite {rounds}")
        try:
            r = llm.json(f"""Rewrite this toddler-song hook to score higher. Topic: {idea.get('topic')}.
Hook: "{best['text']}". Feedback: {best['feedback']}. Keep it under 10 words, use one of the
characters {names}, include a sound word or a question. Return JSON {{"text": "..."}}""", system=SYSTEM)
            cand = dict(score_hook(llm, r["text"], idea, names), formula=best["formula"])
            scored.append(cand)
            best = max(scored, key=lambda s: s["score"])
        except (LLMError, KeyError, TypeError) as e:
            log(f"hook rewrite failed: {e}")
            break
    best["passed"] = best["score"] >= min_score
    best["all"] = sorted(scored, key=lambda s: -s["score"])
    return best


def draft_hooks(llm: LLM, idea: dict, names: list[str], n: int = 6) -> list[dict]:
    if llm.available:
        try:
            r = llm.json_list(f"""Write {n} opening hooks (first sung/spoken line) for a toddler song.
Idea: {idea}. Characters: {names}.
Use a DIFFERENT formula for each, chosen from: {[(f['id'], f['pattern'], f['example']) for f in FORMULAS]}.
Return JSON list of {{"formula": id, "text": hook}}.""", system=SYSTEM)
            return [c for c in r if c.get("text")]
        except (LLMError, TypeError, AttributeError) as e:
            log(f"hook drafting: LLM failed ({e}); using templates")
    return offline_hooks(idea, names)


def offline_hooks(idea: dict, names: list[str]) -> list[dict]:
    lead = names[0] if names else "Pip"
    friend = names[1] if len(names) > 1 else "friend"
    item = (idea.get("items") or ["something"])[0]
    topic = idea.get("topic", "fun").replace("_", " ")
    return [
        {"formula": "sound_question", "text": f"Peekaboo! What did {lead} find?"},
        {"formula": "i_spy", "text": f"I see {item}! Do you see {item} too?"},
        {"formula": "move_invite", "text": f"Clap clap with {lead} and me!"},
        {"formula": "routine_chant", "text": f"{topic.title()} time, {topic} time, here we go!"},
        {"formula": "tiny_problem", "text": f"Uh-oh! {friend} needs help. Can you help?"},
        {"formula": "count_in", "text": "One, two, three, sing with me!"},
    ]
