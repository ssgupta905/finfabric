"""Thin Gemini client. One call site, one shape, no SDK dependency.

The key lives in .env and is read at call time (never logged, never sent in
the URL — header auth only). If the key is missing, `enabled()` returns
False and every caller can fall back to a deterministic stub."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parents[1]
_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
_CACHE: dict[tuple, tuple[float, str]] = {}
_CACHE_TTL = 300  # 5 minutes


def _env() -> dict[str, str]:
    env = dict(os.environ)
    dotenv = REPO / ".env"
    if dotenv.exists():
        for line in dotenv.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env.setdefault(k.strip(), v.strip())
    return env


def enabled() -> bool:
    return bool(_env().get("GEMINI_API_KEY"))


def model_name() -> str:
    return _env().get("GEMINI_MODEL", "gemini-2.5-flash")


def generate(
    prompt: str,
    *,
    system: Optional[str] = None,
    temperature: float = 0.2,
    max_tokens: int = 512,
    timeout: float = 20.0,
    cache_key: Optional[str] = None,
    thinking_budget: int = 0,
) -> str:
    """One-shot text generation. Returns the model's text or a marker string
    beginning with 'GEMINI_ERROR:' — callers should treat that as a fallback
    trigger, never surface it as if it were content."""
    env = _env()
    key = env.get("GEMINI_API_KEY")
    if not key:
        return "GEMINI_ERROR: no API key configured"

    if cache_key:
        hit = _CACHE.get((cache_key, model_name()))
        if hit and time.time() - hit[0] < _CACHE_TTL:
            return hit[1]

    body: dict = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
            # Gemini 2.5 uses hidden "thinking" tokens which count against
            # maxOutputTokens — a small budget is enough for short answers,
            # and reporter/chat callers explicitly raise it.
            "thinkingConfig": {"thinkingBudget": thinking_budget},
        },
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}

    url = f"{_BASE}/{model_name()}:generateContent"
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:200]
        return f"GEMINI_ERROR: HTTP {e.code}: {detail}"
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return f"GEMINI_ERROR: {type(e).__name__}: {e}"

    try:
        text = payload["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        return f"GEMINI_ERROR: unexpected shape: {json.dumps(payload)[:200]}"

    text = text.strip()
    if cache_key:
        _CACHE[(cache_key, model_name())] = (time.time(), text)
    return text


def is_error(text: str) -> bool:
    return text.startswith("GEMINI_ERROR:")
