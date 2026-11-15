"""Gemini API (free tier) client that returns parsed JSON.

When no key is configured, or in --offline mode, `available` is False and
callers use their deterministic offline fallbacks instead.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

import requests

from .util import log

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self, cfg: dict, offline: bool = False):
        self.key = cfg["keys"].get("gemini", "")
        self.model = cfg["gemini"]["model"]
        self.min_interval = float(cfg["gemini"].get("min_interval_s", 0))
        self.available = bool(self.key) and not offline
        self._last = 0.0

    def json(self, prompt: str, system: str | None = None, temperature: float = 0.9,
             retries: int = 5) -> Any:
        if not self.available:
            raise LLMError("LLM not available (offline or no GEMINI_API_KEY)")
        body: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature, "responseMimeType": "application/json"},
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        delay = 10.0
        for attempt in range(retries):
            wait = self.min_interval - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            r = requests.post(API.format(model=self.model), params={"key": self.key},
                              json=body, timeout=120)
            if r.status_code in (429, 500, 503) and attempt < retries - 1:
                log(f"Gemini {r.status_code}, retrying in {delay:.0f}s")
                time.sleep(delay)
                delay *= 2
                continue
            if r.status_code != 200:
                raise LLMError(f"Gemini error {r.status_code}: {r.text[:500]}")
            try:
                text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            except (KeyError, IndexError) as e:
                raise LLMError(f"Unexpected Gemini response: {r.text[:500]}") from e
            return parse_json(text)
        raise LLMError("Gemini retries exhausted")


def parse_json(text: str) -> Any:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    return json.loads(text)


SYSTEM = (
    "You are the head writer of a preschool YouTube channel making original, educational "
    "songs for children aged 1-4. Content must be gentle, safe, age-appropriate and genuinely "
    "teach one thing. Never copy existing songs, characters or titles from other channels. "
    "Always answer with valid JSON only."
)
