"""Tests for the no-GPU path: demand, public-domain rhymes, local music, images API, captions, make()."""
import shutil
import wave

import pytest

from rhymeforge import demand, freegen
from rhymeforge.assemble import bounce_text, write_ass
from rhymeforge.config import load_config
from rhymeforge.llm import parse_json
from rhymeforge.rhymes import RHYMES, get_rhyme, rhyme_script
from rhymeforge.script import lyric_lines
from rhymeforge.util import read_json

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")


def test_every_tune_line_is_8_beats_and_matches_lyrics():
    for key, r in RHYMES.items():
        if r.get("tune"):
            assert len(r["tune"]) == len(r["lines"]), key
            for t in r["tune"]:
                assert abs(sum(b for _, b in freegen.parse_tune(t)) - 8) < 0.01, (key, t)


def test_rhyme_script_repeats_and_aligns_tunes():
    r = get_rhyme("Twinkle Twinkle")
    s = rhyme_script(r, "Ready, Pip? Let's sing Twinkle!", ["Pip", "Coco"], repeats=2)
    lines = lyric_lines(s)
    assert lines[0]["text"].startswith("Ready")
    assert sum(1 for l in lines if l["text"] == "Twinkle, twinkle, little star,") == 4
    assert len(s["tunes"]) == len(lines)
    assert all(t is None for t, l in zip(s["tunes"], lines) if l["section_type"] != "chorus")
    with pytest.raises(RuntimeError):
        get_rhyme("not-a-rhyme")


def test_demand_scoring_and_pick():
    assert demand.score_suggestions("x", ["x song for kids", "x"]) > demand.score_suggestions("x", ["x"])
    d = [{"topic": "colors", "demand": 6.0}, {"topic": "bath_time", "demand": 5.0}]
    assert demand.pick_topic(d)["topic"] == "colors"
    assert demand.pick_topic(d, [{"topic": "colors"}])["topic"] == "bath_time"  # avoid repeats
    niches = [{"topic": "colors", "gap_score": 1.0}, {"topic": "potty", "gap_score": 1.2}]
    out = demand.boost_niches(niches, d)
    assert out[0]["topic"] == "colors" and out[0]["search_demand"] == 1.0


def test_demand_uses_autocomplete(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(demand, "suggestions", lambda q, **k: calls.append(q) or [q + " for kids", q + " 1 hour"])
    cfg = load_config(tmp_path / "none.yaml")
    out = demand.topic_demand(cfg, tmp_path, topics={"bath_time": "bath song"}, pause_s=0)
    assert calls == ["bath song", "bath song for"] and out[0]["demand"] > 0
    assert read_json(tmp_path / "demand_cache.json")["bath song"]["s"]


def test_local_song_timing_is_on_the_beat(tmp_path):
    cfg = load_config(tmp_path / "none.yaml")
    song = {"id": "t", "seed": 3, "bpm": 120, "lines": ["Twinkle, twinkle, little star,", "Brush brush brush!"],
            "tunes": [RHYMES["twinkle-twinkle"]["tune"][0], None]}
    plan = freegen.plan_song(song)
    assert plan["lines"][0]["start"] == pytest.approx(4.0)  # 8-beat intro at 120 bpm
    assert plan["lines"][1]["start"] == pytest.approx(8.0)
    assert abs(sum(d for _, d in plan["lines"][1]["notes"]) - 8) < 1e-6
    assert plan["lines"][0]["bars"][0] == "C"
    words = freegen.local_song(song, tmp_path / "t.wav", cfg)
    assert [w["word"] for w in words][:3] == ["twinkle", "twinkle", "little"]
    assert all(a["start"] <= b["start"] for a, b in zip(words, words[1:]))
    with wave.open(str(tmp_path / "t.wav")) as w:
        assert w.getnchannels() == 2 and abs(w.getnframes() / w.getframerate() - plan["duration"]) < 0.05


def test_pollinations_request_and_fallback(monkeypatch, tmp_path):
    import io

    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(buf, "PNG")

    class R:
        status_code, headers, content, text = 200, {"content-type": "image/png"}, buf.getvalue(), ""
    seen = {}
    monkeypatch.setattr(freegen.requests, "get", lambda url, params, timeout: seen.update(url=url, **params) or R())
    g = {"image_min_interval_s": 0}
    freegen.pollinations_image("cute chick, red scarf", tmp_path / "a.png", 64, 32, 7, g)
    assert (tmp_path / "a.png").exists() and seen["seed"] == 7 and "cute%20chick" in seen["url"]

    def fail(*a, **k):
        raise freegen.requests.ConnectionError("blocked")
    monkeypatch.setattr(freegen.requests, "get", fail)
    monkeypatch.setattr(freegen.time, "sleep", lambda s: None)
    cfg = load_config(tmp_path / "none.yaml", overrides={"generation": {"image_min_interval_s": 0}})
    job = {"settings": {"image_w": 64, "image_h": 32}, "images": [{"id": "x", "prompt": "a. b", "seed": 1}],
           "clips": [], "songs": []}
    freegen.api_generate(job, cfg, tmp_path / "out")
    assert (tmp_path / "out" / "images" / "x.png").exists()  # placeholder fallback
    assert read_json(tmp_path / "out" / "errors.json")[0]["stage"] == "image"


def test_bounce_captions(tmp_path):
    ln = {"text": "Brush your teeth!", "start": 1.0, "end": 2.5,
          "words": [{"word": "brush", "start": 1.0, "end": 1.4}, {"word": "your", "start": 1.5, "end": 1.8},
                    {"word": "teeth", "start": 2.0, "end": 2.5}]}
    txt = bounce_text(ln, 1.0)
    assert "\\t(500,590," in txt and "\\fscy145" in txt and txt.endswith("teeth!")
    write_ass([ln], tmp_path / "c.ass", 1080, 1920, "bounce", offset=0.5)
    ass = (tmp_path / "c.ass").read_text()
    assert "Dialogue: 0,0:00:00.50" in ass and "PlayResY: 1920" in ass


def test_parse_json_with_preamble():
    assert parse_json('Here you go: {"a": 1} hope that helps') == {"a": 1}


@needs_ffmpeg
def test_make_rhyme_end_to_end(tmp_path):
    from rhymeforge.pipeline import make
    cfg = load_config(tmp_path / "none.yaml", overrides={
        "video": {"width": 320, "height": 180},
        "rhymes": {"repeats": 1, "bpm": 140},
        "generation": {"image_api": "placeholder", "music_api": "local"},
        "paths": {k: str(tmp_path / k) for k in ("runs", "data", "library", "secrets")},
    })
    vdir = make(cfg, rhyme="baa-baa-black-sheep", run_name="t")
    assert read_json(vdir / "qa.json")["passed"]
    assert "Baa, Baa, Black Sheep" in read_json(vdir / "package.json")["title"]
    shorts = list((vdir / "shorts").glob("*.mp4"))
    assert shorts
    from rhymeforge.util import run
    probe = run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=width,height",
                 "-of", "csv=p=0", str(shorts[0])]).stdout.strip()
    assert probe == "1080,1920"
    for f in ("storyboard.md", "canva_scenes.csv", "suno.txt", "captions.ass"):
        assert (vdir / f).exists()
    timing = read_json(vdir / "timing.json")
    assert timing[1]["text"].startswith("Baa, baa") and timing[1]["words"]
