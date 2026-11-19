"""Public-domain nursery rhymes: traditional lyrics, plus the traditional tune for some.

All lyrics here are traditional (18th-19th century or older) and out of copyright. The
tunes are traditional too ("Twinkle Twinkle" and "Baa Baa Black Sheep" share the French
melody "Ah! vous dirai-je, maman"). Modern recordings and arrangements of these songs ARE
copyrighted, so we only ever generate our own audio.

Tune format: one string per lyric line, "NOTE:BEATS" separated by spaces, 8 beats per line.
"""
from __future__ import annotations

from .script import default_music_tags

_TWINKLE = ["C4:1 C4:1 G4:1 G4:1 A4:1 A4:1 G4:2", "F4:1 F4:1 E4:1 E4:1 D4:1 D4:1 C4:2",
            "G4:1 G4:1 F4:1 F4:1 E4:1 E4:1 D4:2", "G4:1 G4:1 F4:1 F4:1 E4:1 E4:1 D4:2"]

RHYMES: dict[str, dict] = {
    "twinkle-twinkle": {
        "title": "Twinkle, Twinkle, Little Star",
        "topic": "bedtime",
        "lines": ["Twinkle, twinkle, little star,", "How I wonder what you are!",
                  "Up above the world so high,", "Like a diamond in the sky.",
                  "Twinkle, twinkle, little star,", "How I wonder what you are!"],
        "tune": _TWINKLE + _TWINKLE[:2],
        "scene_hint": "a calm night sky full of soft glowing stars over a cosy village",
    },
    "baa-baa-black-sheep": {
        "title": "Baa, Baa, Black Sheep",
        "topic": "animals",
        "lines": ["Baa, baa, black sheep, have you any wool?", "Yes sir, yes sir, three bags full;",
                  "One for the master, and one for the dame,",
                  "And one for the little boy who lives down the lane."],
        "tune": ["C4:1 C4:1 G4:1 G4:1 A4:0.5 A4:0.5 A4:0.5 A4:0.5 G4:2",
                 "F4:1 F4:1 E4:1 E4:1 D4:1 D4:1 C4:2",
                 "G4:1 G4:0.5 G4:0.5 F4:1 F4:1 E4:1 E4:0.5 E4:0.5 D4:2",
                 "G4:0.5 G4:0.5 G4:0.5 G4:0.5 F4:0.5 F4:0.5 F4:1 E4:0.5 E4:0.5 E4:1 D4:2"],
        "scene_hint": "a sunny green farm with a fluffy black sheep and three wool bags",
    },
    "mary-had-a-little-lamb": {
        "title": "Mary Had a Little Lamb",
        "topic": "animals",
        "lines": ["Mary had a little lamb,", "Little lamb, little lamb,", "Mary had a little lamb,",
                  "Its fleece was white as snow."],
        "tune": ["E4:1 D4:1 C4:1 D4:1 E4:1 E4:1 E4:2", "D4:1 D4:1 D4:2 E4:1 G4:1 G4:2",
                 "E4:1 D4:1 C4:1 D4:1 E4:1 E4:1 E4:2", "E4:1 D4:1 D4:1 E4:1 D4:1 C4:3"],
        "scene_hint": "a meadow with a little girl and a fluffy white lamb",
    },
    "row-row-row-your-boat": {
        "title": "Row, Row, Row Your Boat",
        "topic": "vehicles",
        "lines": ["Row, row, row your boat,", "Gently down the stream.",
                  "Merrily, merrily, merrily, merrily,", "Life is but a dream."],
        "tune": ["C4:1 C4:1 C4:1 D4:1 E4:4", "E4:1 D4:1 E4:1 F4:1 G4:4",
                 " ".join(f"{n}:0.6667" for n in ["C5"] * 3 + ["G4"] * 3 + ["E4"] * 3 + ["C4"] * 3),
                 "G4:1 F4:1 E4:1 D4:1 C4:4"],
        "scene_hint": "a little rowing boat on a gentle sparkling river with ducks",
    },
    "humpty-dumpty": {
        "title": "Humpty Dumpty",
        "topic": "opposites",
        "lines": ["Humpty Dumpty sat on a wall,", "Humpty Dumpty had a great fall.",
                  "All the king's horses and all the king's men",
                  "Couldn't put Humpty together again."],
        "scene_hint": "a friendly cartoon egg on a low garden wall, soft cushions below",
    },
    "hickory-dickory-dock": {
        "title": "Hickory Dickory Dock",
        "topic": "counting",
        "lines": ["Hickory, dickory, dock,", "The mouse ran up the clock.", "The clock struck one,",
                  "The mouse ran down,", "Hickory, dickory, dock."],
        "scene_hint": "a tall friendly grandfather clock with a tiny mouse",
    },
    "hey-diddle-diddle": {
        "title": "Hey Diddle Diddle",
        "topic": "animals",
        "lines": ["Hey diddle diddle,", "The cat and the fiddle,", "The cow jumped over the moon;",
                  "The little dog laughed to see such fun,", "And the dish ran away with the spoon."],
        "scene_hint": "a starry night with a cow leaping over a smiling moon",
    },
    "itsy-bitsy-spider": {
        "title": "The Itsy Bitsy Spider",
        "topic": "weather",
        "lines": ["The itsy bitsy spider climbed up the water spout.",
                  "Down came the rain and washed the spider out.",
                  "Out came the sun and dried up all the rain,",
                  "And the itsy bitsy spider climbed up the spout again."],
        "scene_hint": "a cute smiling spider on a garden water spout, rain then sunshine",
    },
    "little-bo-peep": {
        "title": "Little Bo-Peep",
        "topic": "animals",
        "lines": ["Little Bo-Peep has lost her sheep,", "And doesn't know where to find them;",
                  "Leave them alone, and they'll come home,", "Wagging their tails behind them."],
        "scene_hint": "a shepherd girl looking around green hills for her sheep",
    },
}


def list_rhymes() -> list[tuple[str, str]]:
    return [(k, v["title"]) for k, v in RHYMES.items()]


def get_rhyme(key: str) -> dict:
    k = key.lower().strip().replace(" ", "-").replace(",", "")
    if k not in RHYMES:
        raise RuntimeError(f"unknown rhyme {key!r}. Choose from: {', '.join(RHYMES)}")
    return dict(RHYMES[k], key=k)


def rhyme_idea(key: str, cfg: dict) -> dict:
    r = get_rhyme(key)
    return {"topic": r["topic"], "working_title": f"{r['title']} | Sing Along", "rhyme": r["key"],
            "angle": f"the traditional rhyme sung with {cfg['channel']['characters'][0]['name']} and friends",
            "learning_goal": "sing along and join in with a classic rhyme",
            "items": [], "scene_hint": r["scene_hint"], "why_it_can_win": "evergreen public-domain search demand"}


def intro_hooks(r: dict, names: list[str]) -> list[dict]:
    lead = names[0] if names else "Pip"
    friend = names[1] if len(names) > 1 else "everyone"
    short = r["title"].split(",")[0]
    return [{"text": f"Ready, {lead}? Let's sing {short}!", "formula": "invitation"},
            {"text": f"Shh... {lead} hears a song! Can you hear it?", "formula": "curiosity"},
            {"text": f"Clap clap! {lead} and {friend} love {short}!", "formula": "action"}]


def rhyme_script(r: dict, hook_text: str, names: list[str], repeats: int = 3, bpm: int = 100) -> dict:
    """The traditional rhyme sung `repeats` times, with a short original intro and outro.

    The repeats are marked as `chorus` sections, so the storyboard reuses their visuals and
    the Shorts finder treats them as the catchy part.
    """
    lead = names[0] if names else "Pip"
    sections = [{"type": "intro", "lines": [hook_text]}]
    tunes: list[str | None] = [None]
    cues = ["", "Let's sing it again, clap along!", "One more time, nice and loud!"]
    for i in range(repeats):
        if i and cues[i % len(cues)]:
            sections.append({"type": "bridge", "lines": [cues[i % len(cues)]]})
            tunes.append(None)
        sections.append({"type": "chorus", "lines": list(r["lines"])})
        tunes += list(r.get("tune") or [None] * len(r["lines"]))
    sections.append({"type": "outro", "lines": [f"Yay! Bye bye from {lead}!"]})
    tunes.append(None)
    return {"title": r["title"], "learning_goal": "sing along with a classic rhyme", "sections": sections,
            "tunes": tunes, "public_domain": True, "rhyme": r["key"],
            "music_tags": default_music_tags(bpm).replace("children's song", "traditional nursery rhyme")}
