"""LLM client that returns parsed JSON.

Providers (llm.provider in config):
  gemini - Gemini API free tier (GEMINI_API_KEY), the default
  claude - Claude via the Anthropic SDK (ANTHROPIC_API_KEY; paid, `pip install 'rhymeforge[claude]'`)

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
        self.provider = cfg.get("llm", {}).get("provider", "gemini")
        if self.provider == "claude":
            self.claude_model = cfg["llm"].get("claude_model", "claude-opus-5-5")
            self.claude_effort = cfg["llm"].get("claude_effort", "medium")
            self.available = bool(cfg["keys"].get("anthropic")) and not offline
            self._client = None
            return
        self.key = cfg["keys"].get("gemini", "")
        self.model = cfg["gemini"]["model"]
        self.fallback = cfg["gemini"].get("fallback_model") or ""
        self.min_interval = float(cfg["gemini"].get("min_interval_s", 0))
        self.available = bool(self.key) and not offline
        self._last = 0.0

    def json(self, prompt: str, system: str | None = None, temperature: float = 0.9,
             retries: int = 5) -> Any:
        if not self.available:
            raise LLMError("LLM not available (offline or no API key)")
        if self.provider == "claude":
            return self._claude_json(prompt, system)
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

    def _claude_json(self, prompt: str, system: str | None) -> Any:
        try:
            import anthropic
        except ImportError as e:
            raise LLMError("llm.provider is claude but the anthropic package is missing: "
                           "pip install 'rhymeforge[claude]'") from e
        if self._client is None:
            self._client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
        try:
            r = self._client.beta.messages.create(
                model=self.claude_model, max_tokens=16000, system=system or SYSTEM,
                output_config={"effort": self.claude_effort},
                betas=["server-side-fallback-2026-07-01"], fallbacks="default",
                messages=[{"role": "user", "content": prompt + "\nAnswer with the JSON only."}])
        except anthropic.APIStatusError as e:
            raise LLMError(f"Claude error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise LLMError(f"Claude connection error: {e}") from e
        if r.stop_reason == "refusal":
            raise LLMError("Claude declined this request")
        text = "".join(b.text for b in r.content if b.type == "text")
        try:
            return parse_json(text)
        except ValueError as e:
            raise LLMError(f"Claude returned non-JSON: {text[:300]}") from e

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
    try:
        return json.loads(text)
    except ValueError:
        start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
        if start < 0:
            raise
        return json.JSONDecoder().raw_decode(text[start:])[0]


SYSTEM = (
    "You are the head writer of a preschool YouTube channel making original, educational "
    "songs for children aged 1-4. Content must be gentle, safe, age-appropriate and genuinely "
    "teach one thing. Never copy existing songs, characters or titles from other channels. "
    "Always answer with valid JSON only."
)
