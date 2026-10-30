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
import threading

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


def _parse(text: str) -> dict | None:
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
    def __init__(self, backend: str, model: str | None = None, ollama_url: str | None = None):
        if backend == "anthropic" and not os.environ.get("ANTHROPIC_API_KEY"):
            raise ValueError("--llm anthropic needs ANTHROPIC_API_KEY (in the environment or .env)")
        self.backend = backend
        self.model = model
        self.ollama_url = (ollama_url or os.environ.get("OLLAMA_URL") or "http://localhost:11434").rstrip("/")
        self.tokens_in = 0
        self.tokens_out = 0
        self.calls = 0
        self.failures = 0
        self._lock = threading.Lock()         # worker threads share one identifier and its counters

    def identify(self, page1_text: str) -> dict | None:
        """A search key, or None. A failed call (network, a reply that is not the expected
        JSON) costs this PDF its last tier, never the PDF-derived fields it already has."""
        ask = {"anthropic": self._anthropic, "ollama": self._ollama}[self.backend]
        with self._lock:
            self.calls += 1
        try:
            return ask(page1_text[:MAX_CHARS])
        except (requests.RequestException, ValueError, KeyError):
            with self._lock:
                self.failures += 1
            return None

    def _count(self, tokens_in: int, tokens_out: int) -> None:
        with self._lock:
            self.tokens_in += tokens_in
            self.tokens_out += tokens_out

    def _anthropic(self, text: str) -> dict | None:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
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
        self._count(usage.get("input_tokens", 0), usage.get("output_tokens", 0))
        return _parse("".join(b.get("text", "") for b in body.get("content", [])))

    def _ollama(self, text: str) -> dict | None:
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
        self._count(body.get("prompt_eval_count", 0), body.get("eval_count", 0))
        return _parse((body.get("message") or {}).get("content", ""))
