from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from rhymeforge import edl, retention as ret
from rhymeforge.align import align_lines
from rhymeforge.config import DEFAULTS
from rhymeforge.hooks import FORMULAS, heuristic_score
from rhymeforge.niches import classify, is_unique, rank_niches
from rhymeforge.packaging import overlap, validate_pair
from rhymeforge.scout import score_outliers
from rhymeforge.script import estimate_timing, lines_needed, lyric_lines, offline_script
from rhymeforge.shorts import find_shorts
from rhymeforge.storyboard import plan_shots
from rhymeforge.youtube import next_slots

NOW = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)


def vid(ch, views, days=10, title="Colors Song"):
    return {"video_id": f"{ch}{views}", "channel_id": ch, "channel_title": ch, "title": title, "views": views,
            "published_at": (NOW - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")}


def test_21_formulas():
    assert len(FORMULAS) == 21
    assert len({f["id"] for f in FORMULAS}) == 21


def test_outlier_beats_big_channel():
    small = [vid("small", 5000) for _ in range(4)] + [vid("small", 50_000)]
    big = [vid("big", 2_000_000) for _ in range(5)]
    scored = score_outliers(small + big, now=NOW)
    assert scored[0]["channel_id"] == "small" and scored[0]["outlier"] == 10.0
    assert all(v["outlier"] == 1.0 for v in scored if v["channel_id"] == "big")


def test_outlier_ignores_too_new_videos():
    vids = [vid("c", 1000) for _ in range(3)] + [vid("c", 900_000, days=1)]
    assert all(v["views"] != 900_000 for v in score_outliers(vids, min_age_days=3, now=NOW))


def test_niche_ranking_and_classify():
    assert "colors" in classify("Rainbow Colors Song")
    scored = [dict(vid("a", 1), title="Colors Song", outlier=10, channel_title="a"),
              dict(vid("a", 2), title="Colors Song", outlier=1, channel_title="a"),
              dict(vid("b", 3), title="Bath time splash", outlier=6, channel_title="b")]
    ranked = rank_niches(scored)
    assert ranked[0]["topic"] == "colors"
    penalised = rank_niches(scored, history=[{"topic": "colors"}, {"topic": "colors"}])
    assert penalised[0]["topic"] == "bath_time"


def test_uniqueness():
    assert not is_unique("Colors Song with Balloons", ["Balloon Colors Song"])
    assert is_unique("Bath Time Counting", ["Balloon Colors Song"])


def test_hook_heuristic_prefers_toddler_hooks():
    good = heuristic_score("Peekaboo! Where is Pip?", ["Pip"])["score"]
    bad = heuristic_score("Today we will be investigating the fascinating characteristics of mammals", ["Pip"])["score"]
    assert good >= 75 > bad


def test_title_thumbnail_pairing_rejects_repeats():
    assert overlap("Colors Song for Toddlers", "Colors Song!") > 0.25
    assert validate_pair({"title": "Colors Song for Toddlers", "thumbnail_text": "Colors!"}, "colors")
    assert not validate_pair({"title": "Learn Colors | Toddler Song", "thumbnail_text": "Uh-oh!"}, "colors")


def test_script_structure_and_timing():
    assert 35 <= lines_needed(180, 110) <= 45
    s = offline_script({"topic": "colors", "items": ["red", "blue"]}, ["Pip", "Coco"], "Peekaboo!")
    lines = lyric_lines(s)
    timed = estimate_timing(lines, 180)
    assert timed[0]["start"] >= 0 and timed[-1]["end"] <= 180
    chorus = [l for l in lines if l["section_type"] == "chorus"]
    assert len({l["occurrence"] for l in chorus}) == 3


def test_storyboard_budget_and_chorus_reuse():
    s = offline_script({"topic": "colors", "items": ["red"]}, ["Pip", "Coco"], "Peekaboo! Where is Pip?")
    shots = plan_shots(lyric_lines(s), 12, ["Pip", "Coco"])
    assert sum(1 for x in shots if x["kind"] == "ai_clip") == 12
    assert shots[0]["kind"] == "ai_clip"  # the hook always gets real motion
    reuse = [x for x in shots if x["kind"] == "reuse"]
    assert reuse and all(x["reuse_of"] for x in reuse)


def test_alignment_uses_whisper_times():
    lines = [{"text": "Hello Pip", "section": 0, "section_type": "intro", "occurrence": 0, "line_in_section": 0},
             {"text": "Red red red", "section": 1, "section_type": "verse", "occurrence": 0, "line_in_section": 0}]
    heard = [{"word": "Hello", "start": 1.0, "end": 1.4}, {"word": "Pip!", "start": 1.5, "end": 2.0},
             {"word": "red", "start": 5.0, "end": 5.3}, {"word": "red", "start": 5.4, "end": 5.7},
             {"word": "red", "start": 5.8, "end": 6.2}]
    out = align_lines(lines, heard, 10)
    assert out[0]["start"] == 1.0 and out[1]["start"] == 5.0 and out[1]["end"] == 6.2


def test_retention_csv_percent_positions(tmp_path):
    p = tmp_path / "r.csv"
    p.write_text("Video position (%),Audience retention (%)\n0,100\n10,98\n20,80\n30,79\n")
    pts = ret.load_csv(p, 200)
    assert pts[2]["t"] == 40 and pts[2]["retention"] == 0.8
    drops = ret.find_drops(pts)
    assert drops[0]["t"] == 40 and drops[0]["drop_pp"] == 18


def test_retention_csv_timestamps(tmp_path):
    p = tmp_path / "r.csv"
    p.write_text("Time,Retention\n0:00,1.0\n0:42,0.9\n1:10,0.5\n")
    pts = ret.load_csv(p, 180)
    assert [x["t"] for x in pts] == [0, 42, 70]


def test_annotate_finds_lyric_and_shot():
    lines = [{"text": "A", "start": 0, "end": 5, "section_type": "intro"},
             {"text": "B", "start": 40, "end": 45, "section_type": "verse"}]
    shots = [{"id": "s01", "kind": "still", "start": 38, "end": 46, "characters": ["Pip"]}]
    a = ret.annotate([{"t": 42, "from": 80, "to": 70, "drop_pp": 10}], lines, shots)[0]
    assert a["lyric"] == "B" and a["shot_kind"] == "still" and "Static" in a["diagnosis"]


def test_edl_removes_dead_air_and_writes_cmx():
    segs = [{"start": 0, "end": 2, "text": "hi"}, {"start": 2.5, "end": 4, "text": "there"},
            {"start": 10, "end": 12, "text": "later"}]
    keeps = edl.plan_edits(segs, max_gap=1.2, pad=0.1)
    assert len(keeps) == 2
    cmx = edl.to_cmx3600(keeps, "t", "src.mp4", fps=24)
    assert "001  AX" in cmx and "002  AX" in cmx and "FCM: NON-DROP FRAME" in cmx
    assert edl.tc(61.5, 24) == "00:01:01:12"


def test_edl_cut_drops():
    segs = [{"start": 0, "end": 2, "text": "keep"}, {"start": 2.2, "end": 4, "text": "boring"}]
    keeps = edl.plan_edits(segs, [{"t": 3, "drop_pp": 9}], cut_drops=True)
    assert "boring" not in " ".join(k["text"] for k in keeps)


def test_srt_parsing(tmp_path):
    p = tmp_path / "a.srt"
    p.write_text("1\n00:00:01,000 --> 00:00:02,500\nHello\n\n2\n00:00:03,000 --> 00:00:04,000\nWorld\n")
    assert edl.load_transcript(p) == [{"start": 1.0, "end": 2.5, "text": "Hello"},
                                      {"start": 3.0, "end": 4.0, "text": "World"}]


def test_shorts_prefers_repeated_chorus():
    segs = []
    t = 0.0
    for i in range(30):
        text = "Sing the colors song!" if 10 <= i < 16 else f"verse line {i}"
        segs.append({"start": t, "end": t + 4, "text": text})
        t += 4.5
    segs += [{"start": t, "end": t + 4, "text": "Sing the colors song!"}]
    picks = find_shorts(segs, top=1, min_s=15, max_s=30)
    assert picks and 40 <= picks[0]["start"] <= 50 and picks[0]["hook_density"] > 0.5


def test_publish_slots():
    now = datetime(2026, 11, 16, 9, tzinfo=ZoneInfo("America/New_York"))  # a Monday
    slots = next_slots(["mon", "wed", "fri", "sun"], "16:00", "America/New_York", 4, 24, now=now)
    assert [s.strftime("%a") for s in slots] == ["Wed", "Fri", "Sun", "Mon"]
    assert all(s.hour == 16 for s in slots)


def test_defaults_are_sane():
    assert DEFAULTS["video"]["duration_s"] == 180
    assert DEFAULTS["generation"]["backend"] == "kaggle"
