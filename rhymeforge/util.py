"""Small shared helpers."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "with", "is", "are", "be",
    "it", "its", "this", "that", "my", "your", "our", "we", "you", "i", "at", "by", "from",
    "song", "songs", "kids", "kid", "toddlers", "toddler", "nursery", "rhyme", "rhymes",
    "baby", "babies", "children", "for", "fun", "let's", "lets",
}


def log(msg: str) -> None:
    print(f"[rhymeforge] {msg}", file=sys.stderr, flush=True)


def slugify(text: str, max_len: int = 48) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:max_len].rstrip("-") or "video"


def read_json(path: Path, default: Any = None) -> Any:
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text())


def write_json(path: Path, data: Any) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    return path


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def stem(w: str) -> str:
    for suf in ("ing", "es", "s"):
        if len(w) > len(suf) + 2 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def content_words(text: str) -> set[str]:
    return {stem(w) for w in words(text) if w not in STOPWORDS and len(w) > 1}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def require_ffmpeg() -> None:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("ffmpeg/ffprobe not found on PATH. Install ffmpeg first.")


def run(cmd: list[str], quiet: bool = True) -> subprocess.CompletedProcess:
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"command failed ({res.returncode}): {' '.join(cmd)}\n{res.stderr[-2000:]}")
    if not quiet:
        print(res.stdout)
    return res


def media_duration(path: Path) -> float:
    res = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "default=nw=1:nk=1", str(path)])
    return float(res.stdout.strip())


def fmt_ts(seconds: float) -> str:
    seconds = max(0.0, seconds)
    m, s = divmod(int(round(seconds)), 60)
    return f"{m}:{s:02d}"
