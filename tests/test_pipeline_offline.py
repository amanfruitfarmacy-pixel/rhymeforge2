"""End-to-end: the whole week runs offline (sample data, templates, placeholder media)."""
import shutil

import pytest

from rhymeforge.config import load_config
from rhymeforge.pipeline import make_shorts, week
from rhymeforge.util import read_json

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")


def test_week_offline(tmp_path):
    cfg = load_config(tmp_path / "none.yaml", overrides={
        "video": {"duration_s": 30, "width": 320, "height": 180, "unique_clips": 3},
        "paths": {k: str(tmp_path / k) for k in ("runs", "data", "library", "secrets")},
    })
    run_dir = week(cfg, count=1, offline=True, run_name="t")
    vdirs = [d for d in run_dir.iterdir() if (d / "final.mp4").exists()]
    assert len(vdirs) == 1
    v = vdirs[0]
    qa = read_json(v / "qa.json")
    assert qa["checks"]["loudness_ok"] and qa["checks"]["duration_ok"]
    assert (v / "thumbnail.jpg").exists() and (v / "captions.ass").exists()
    assert read_json(tmp_path / "library" / "index.json")  # clips saved for reuse
    picks = make_shorts(v, cut=False, top=1)
    assert picks and picks[0]["duration"] >= 15
