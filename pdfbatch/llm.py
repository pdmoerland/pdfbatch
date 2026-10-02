"""Last-resort LLM tier. Off by default.

The LLM never supplies metadata that ends up in the RIS file. It only reads
page-1 text (hard-capped) and returns a *search key* (title, first-author
surname, year, DOI if printed), which goes back through the CrossRef/OpenAlex
title search and the normal validation. Typical cost: ~1.5k tokens in, ~60 out,
and only for PDFs where every free tier missed.

Backends:
  anthropic  — needs ANTHROPIC_API_KEY; default model is the cheapest Haiku.
  ollama     — native /api/chat with a JSON-schema `format` and think=False.
"""

from __future__ import annotations

import json
import os
import re
from typing import Optional

import requests

MAX_CHARS = 6000          # ≈1.5k tokens of page-1 text

SYSTEM = (
    "You read the first page of a scientific article and identify that article. "
    "Return ONLY a JSON object with keys: title (string), first_author_surname "
    "(string or null), year (string or null), doi (string or null; only if printed "
    "as the article's own DOI, never a reference's). No prose."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "first_author_surname": {"type": ["string", "null"]},
        "year": {"type": ["string", "null"]},
        "doi": {"type": ["string", "null"]},
    },
    "required": ["title", "first_author_surname", "year", "doi"],
}


def _parse(text: str) -> Optional[dict]:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    text = re.sub(r"```(?:json)?|```", "", text).strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return d if isinstance(d, dict) and d.get("title") else None


class LlmIdentifier:
    def __init__(self, backend: str, model: Optional[str] = None, ollama_url: Optional[str] = None):
        self.backend = backend
        self.model = model
        self.ollama_url = (ollama_url or os.environ.get("OLLAMA_URL") or "http://localhost:11434").rstrip("/")
        self.tokens_in = 0
        self.tokens_out = 0
        self.calls = 0

    def identify(self, page1_text: str) -> Optional[dict]:
        ask = {"anthropic": self._anthropic, "ollama": self._ollama}[self.backend]
        self.calls += 1
        try:
            return ask(page1_text[:MAX_CHARS])
        except requests.RequestException:
            return None

    def _anthropic(self, text: str) -> Optional[dict]:
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={
                "model": self.model or "claude-haiku-4-5",
                "max_tokens": 200,
                "system": SYSTEM,
                "messages": [{"role": "user", "content": text}],
            },
            timeout=60,
        )
        r.raise_for_status()
        body = r.json()
        usage = body.get("usage") or {}
        self.tokens_in += usage.get("input_tokens", 0)
        self.tokens_out += usage.get("output_tokens", 0)
        return _parse("".join(b.get("text", "") for b in body.get("content", [])))

    def _ollama(self, text: str) -> Optional[dict]:
        r = requests.post(
            f"{self.ollama_url}/api/chat",
            json={
                "model": self.model or "qwen3:8b",
                "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": text}],
                "format": SCHEMA,
                "think": False,
                "stream": False,
                "options": {"temperature": 0, "num_ctx": 8192},
            },
            timeout=300,
        )
        r.raise_for_status()
        body = r.json()
        self.tokens_in += body.get("prompt_eval_count", 0)
        self.tokens_out += body.get("eval_count", 0)
        return _parse((body.get("message") or {}).get("content", ""))
