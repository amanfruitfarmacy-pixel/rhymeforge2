"""Outlier scout: find videos that beat their OWN channel's typical performance.

outlier = video views / median views of that channel's recent uploads.
A 50k-view video on a channel whose median is 5k scores 10x and outranks a
2M-view video on a channel whose median is 2M (1x).
"""
from __future__ import annotations

import random
import statistics
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from .util import log, read_json, write_json

API = "https://www.googleapis.com/youtube/v3/"


class YouTubeData:
    """YouTube Data API v3, read-only. Authenticates with an API key (sent in the
    X-Goog-Api-Key header) or, if there is none, with your OAuth login from `rhymeforge auth`."""

    def __init__(self, key: str = "", creds=None):
        if not key and creds is None:
            raise RuntimeError("No YouTube access for scouting: set YOUTUBE_API_KEY or run `rhymeforge auth`. "
                               "Use --offline to try sample data.")
        self.key = key
        self.creds = creds
        self.units = 0

    def _headers(self) -> dict:
        if self.key:
            return {"X-Goog-Api-Key": self.key}
        if not self.creds.valid:
            from google.auth.transport.requests import Request
            self.creds.refresh(Request())
        return {"Authorization": f"Bearer {self.creds.token}"}

    def get(self, endpoint: str, cost: int = 1, **params) -> dict:
        r = requests.get(API + endpoint, params=params, headers=self._headers(), timeout=30)
        self.units += cost
        if r.status_code != 200:
            raise RuntimeError(f"YouTube API {endpoint} {r.status_code}: {r.text[:300]}")
        return r.json()

    def search_channels(self, query: str, published_after_days: int = 120, max_results: int = 50) -> list[str]:
        after = (datetime.now(timezone.utc) - timedelta(days=published_after_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        data = self.get("search", cost=100, part="snippet", q=query, type="video",
                        maxResults=max_results, publishedAfter=after, order="viewCount",
                        safeSearch="strict")
        return list(dict.fromkeys(i["snippet"]["channelId"] for i in data.get("items", [])))

    def channel_videos(self, channel_id: str, limit: int = 30) -> list[dict]:
        ch = self.get("channels", part="contentDetails,snippet,statistics", id=channel_id)
        if not ch.get("items"):
            return []
        item = ch["items"][0]
        uploads = item["contentDetails"]["relatedPlaylists"]["uploads"]
        pl = self.get("playlistItems", part="contentDetails", playlistId=uploads, maxResults=min(limit, 50))
        ids = [i["contentDetails"]["videoId"] for i in pl.get("items", [])]
        if not ids:
            return []
        vids = self.get("videos", part="snippet,statistics,contentDetails", id=",".join(ids))
        out = []
        for v in vids.get("items", []):
            out.append({
                "video_id": v["id"],
                "channel_id": channel_id,
                "channel_title": item["snippet"]["title"],
                "subscribers": int(item["statistics"].get("subscriberCount", 0) or 0),
                "title": v["snippet"]["title"],
                "published_at": v["snippet"]["publishedAt"],
                "views": int(v["statistics"].get("viewCount", 0) or 0),
                "duration": v["contentDetails"].get("duration", ""),
                "thumbnail": v["snippet"]["thumbnails"].get("high", {}).get("url", ""),
            })
        return out


def age_days(published_at: str, now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    t = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    return max((now - t).total_seconds() / 86400, 0.01)


def score_outliers(videos: list[dict], min_age_days: float = 3, now: datetime | None = None) -> list[dict]:
    """Attach outlier score (views / channel median) and views/day; sort best first."""
    by_channel: dict[str, list[dict]] = {}
    for v in videos:
        by_channel.setdefault(v["channel_id"], []).append(v)
    scored = []
    for ch_videos in by_channel.values():
        mature = [v for v in ch_videos if age_days(v["published_at"], now) >= min_age_days]
        if len(mature) < 3:
            continue
        median = statistics.median(v["views"] for v in mature) or 1
        for v in mature:
            s = dict(v)
            s["channel_median"] = median
            s["outlier"] = round(v["views"] / median, 2)
            s["views_per_day"] = round(v["views"] / age_days(v["published_at"], now), 1)
            scored.append(s)
    scored.sort(key=lambda x: (x["outlier"], x["views_per_day"]), reverse=True)
    return scored


def run_scout(cfg: dict, data_dir: Path, offline: bool = False) -> list[dict]:
    sc = cfg["scout"]
    if offline:
        log("scout: offline mode, using generated SAMPLE data (not real YouTube data)")
        videos = sample_videos()
    else:
        cache = data_dir / "scout_cache.json"
        cached = read_json(cache)
        if cached and time.time() - cached["fetched_at"] < sc["cache_hours"] * 3600:
            log("scout: using cached YouTube data")
            videos = cached["videos"]
        else:
            key = cfg["keys"]["youtube"]
            creds = None
            if not key:
                from .youtube import credentials
                creds = credentials(Path(cfg["paths"]["secrets"]), interactive=False)
            yt = YouTubeData(key, creds)
            channels = list(sc["seed_channels"])
            for q in sc["search_queries"]:
                channels += yt.search_channels(q)
            channels = list(dict.fromkeys(channels))
            log(f"scout: scanning {len(channels)} channels")
            videos = []
            for cid in channels:
                try:
                    videos += yt.channel_videos(cid, sc["videos_per_channel"])
                except RuntimeError as e:
                    log(f"scout: skipped {cid}: {e}")
            log(f"scout: used ~{yt.units} API quota units")
            write_json(cache, {"fetched_at": time.time(), "videos": videos})
    return score_outliers(videos, sc["min_age_days"])


# ---------------------------------------------------------------- sample data
_SAMPLE_TITLES = [
    "Colors Song with Balloons", "Wheels on the Tractor", "Bath Time Splash Song", "Count to 10 with Ducks",
    "Brush Your Teeth Song", "Dinosaur Stomp Dance", "Potty Training Song", "Feelings Song for Toddlers",
    "Shapes in the Kitchen", "Rainy Day Puddle Song", "Fruit Colors Song", "Bedtime Lullaby Moon",
    "Animal Sounds on the Farm", "Fire Truck Rescue Song", "Wash Your Hands Song", "ABC Animal Parade",
    "Opposites Song Big and Small", "Body Parts Song Head Shoulders", "Snow Day Counting", "Sharing is Caring Song",
    "Garbage Truck Song", "Peekaboo Song", "Yes Yes Vegetables", "Five Little Bunnies",
]


def sample_videos(seed: int = 7) -> list[dict]:
    """Deterministic fake data so the whole pipeline can run without keys."""
    rng = random.Random(seed)
    now = datetime.now(timezone.utc)
    videos = []
    for c in range(10):
        median = rng.choice([3_000, 20_000, 150_000, 2_000_000])
        for i in range(24):
            title = rng.choice(_SAMPLE_TITLES)
            mult = rng.lognormvariate(0, 0.6) * (rng.choice([1] * 12 + [6, 12]))
            videos.append({
                "video_id": f"sample{c}_{i}",
                "channel_id": f"UCsample{c}",
                "channel_title": f"Sample Channel {c}",
                "subscribers": median * 10,
                "title": f"{title} | Sample {c}-{i}",
                "published_at": (now - timedelta(days=rng.randint(4, 120))).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "views": int(median * mult),
                "duration": "PT3M",
                "thumbnail": "",
            })
    return videos
