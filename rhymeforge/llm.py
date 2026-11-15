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

# Gemini's OpenAI-compatible endpoint: the API key goes in a standard Bearer header.
API = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self, cfg: dict, offline: bool = False):
        self.key = cfg["keys"].get("gemini", "")
        self.model = cfg["gemini"]["model"]
        self.fallback = cfg["gemini"].get("fallback_model") or ""
        self.min_interval = float(cfg["gemini"].get("min_interval_s", 0))
        self.available = bool(self.key) and not offline
        self._last = 0.0

    def json(self, prompt: str, system: str | None = None, temperature: float = 0.9,
             retries: int = 5) -> Any:
        if not self.available:
            raise LLMError("LLM not available (offline or no GEMINI_API_KEY)")
        messages = [{"role": "system", "content": system}] if system else []
        messages.append({"role": "user", "content": prompt})
        body: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": temperature,
                                "response_format": {"type": "json_object"}}
        delay = 10.0
        for attempt in range(retries):
            wait = self.min_interval - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            body["model"] = self.model
            try:
                r = requests.post(API, headers={"Authorization": f"Bearer {self.key}"}, json=body, timeout=120)
                timed_out = r.status_code in (502, 504)
            except requests.Timeout:
                timed_out = True
            if timed_out and self.fallback and self.model != self.fallback:
                # Slow "thinking" models can exceed proxy/gateway time limits; the lite model answers in ~1s.
                log(f"Gemini {self.model} timed out; switching to {self.fallback} for this run")
                self.model = self.fallback
                continue
            if timed_out:
                raise LLMError(f"Gemini {self.model} timed out")
            if r.status_code in (429, 500, 503) and attempt < retries - 1:
                log(f"Gemini {r.status_code}, retrying in {delay:.0f}s")
                time.sleep(delay)
                delay *= 2
                continue
            if r.status_code != 200:
                raise LLMError(f"Gemini error {r.status_code}: {r.text[:500]}")
            try:
                text = r.json()["choices"][0]["message"]["content"]
            except (KeyError, IndexError) as e:
                raise LLMError(f"Unexpected Gemini response: {r.text[:500]}") from e
            return parse_json(text)
        raise LLMError("Gemini retries exhausted")

    def json_list(self, prompt: str, **kw) -> list:
        """Like json(), but always returns a list (JSON mode may wrap lists in an object)."""
        r = self.json(prompt + "\nReturn a JSON object of the form {\"items\": [...]}.", **kw)
        if isinstance(r, list):
            return r
        if isinstance(r, dict):
            for v in r.values():
                if isinstance(v, list):
                    return v
        raise LLMError(f"expected a list, got: {str(r)[:200]}")


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
