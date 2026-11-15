"""Configuration loading: defaults <- config.yaml <- environment / .env."""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

DEFAULTS: dict[str, Any] = {
    "channel": {
        "name": "Pip & Coco Songs",
        "characters": [
            {"name": "Pip", "description": "a small round yellow chick with big friendly blue eyes, tiny orange feet and a red knitted scarf"},
            {"name": "Coco", "description": "a little lavender baby elephant with pink cheeks, big floppy ears and a yellow bow"},
        ],
        "style_bible": "cute 3D cartoon render, soft pastel colors, rounded shapes, bright even lighting, preschool TV show style, simple uncluttered background, high detail",
        "language": "en",
        "category_id": "27",
        "default_tags": ["nursery rhymes", "kids songs", "toddler learning", "songs for kids"],
    },
    "schedule": {
        "publish_days": ["mon", "wed", "fri", "sun"],
        "publish_time": "16:00",
        "timezone": "America/New_York",
        "min_lead_hours": 24,
    },
    "scout": {
        "seed_channels": [],
        "search_queries": ["nursery rhymes for toddlers", "toddler learning songs", "kids songs new"],
        "videos_per_channel": 30,
        "min_age_days": 3,
        "cache_hours": 24,
    },
    "video": {
        "duration_s": 180,
        "bpm": 110,
        "width": 1920,
        "height": 1080,
        "fps": 24,
        "unique_clips": 12,
        "clip_seconds": 5,
    },
    "hooks": {"min_score": 75, "max_rewrites": 3},
    "gemini": {"model": "gemini-2.5-flash", "min_interval_s": 6},
    "generation": {
        "backend": "kaggle",
        "image_model": "stabilityai/stable-diffusion-xl-base-1.0",
        "video_model": "Lightricks/LTX-Video",
        "music_model": "ace-step",
        "kaggle_kernel_slug": "rhymeforge-gen",
        "kaggle_timeout_hours": 11,
    },
    "upload": {"enabled": True, "privacy": "private", "playlist_id": ""},
    "paths": {"runs": "runs", "data": "data", "library": "library", "secrets": ".secrets"},
    "keys": {"gemini": "", "youtube": ""},
}


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_dotenv(path: Path) -> None:
    """Minimal .env reader so we don't need python-dotenv."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip().strip('"').strip("'")
        if v and k.strip() not in os.environ:
            os.environ[k.strip()] = v


def load_config(path: str | Path | None = None, overrides: dict | None = None) -> dict:
    load_dotenv(Path(".env"))
    cfg = copy.deepcopy(DEFAULTS)
    p = Path(path) if path else Path("config.yaml")
    if p.exists():
        cfg = deep_merge(cfg, yaml.safe_load(p.read_text()) or {})
    if overrides:
        cfg = deep_merge(cfg, overrides)
    cfg["keys"]["gemini"] = cfg["keys"].get("gemini") or os.environ.get("GEMINI_API_KEY", "")
    cfg["keys"]["youtube"] = cfg["keys"].get("youtube") or os.environ.get("YOUTUBE_API_KEY", "")
    return cfg
