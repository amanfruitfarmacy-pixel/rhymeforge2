"""Reusable clip library: every generated AI clip is kept and tagged so later
songs can reuse it instead of spending GPU time again."""
from __future__ import annotations

import shutil
from pathlib import Path

from .util import content_words, jaccard, read_json, write_json


class ClipLibrary:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.index_path = self.root / "index.json"
        self.items: list[dict] = read_json(self.index_path, [])

    def find(self, characters: list[str], action: str, min_sim: float = 0.5,
             exclude: set[str] | None = None) -> dict | None:
        want = content_words(action)
        best, best_sim = None, min_sim
        for it in self.items:
            if exclude and it["id"] in exclude:
                continue
            if sorted(it["characters"]) != sorted(characters):
                continue
            if not (self.root / it["file"]).exists():
                continue
            sim = jaccard(want, set(it["keywords"]))
            if sim >= best_sim:
                best, best_sim = it, sim
        return best

    def add(self, clip_path: Path, clip_id: str, characters: list[str], action: str, prompt: str) -> dict:
        self.root.mkdir(parents=True, exist_ok=True)
        dest = self.root / "clips" / f"{clip_id}{Path(clip_path).suffix}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(clip_path, dest)
        item = {"id": clip_id, "file": str(dest.relative_to(self.root)), "characters": sorted(characters),
                "action": action, "keywords": sorted(content_words(action)), "prompt": prompt}
        self.items = [i for i in self.items if i["id"] != clip_id] + [item]
        write_json(self.index_path, self.items)
        return item

    def path(self, item: dict) -> Path:
        return self.root / item["file"]
