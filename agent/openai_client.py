"""Thin OpenAI client — mirrors gemini.py's shape so the router treats
both providers identically. Uses only stdlib (urllib), no SDK dependency."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parents[1]
_BASE = "https://api.openai.com/v1/chat/completions"
_CACHE: dict[tuple, tuple[float, str]] = {}
_CACHE_TTL = 300


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
    return bool(_env().get("OPENAI_API_KEY"))


def default_model() -> str:
    # gpt-4o-mini is the sweet spot for our workload: fast, cheap
    # ($0.15/M input, $0.60/M output), JSON mode support.
    return _env().get("OPENAI_MODEL", "gpt-4o-mini")


def generate(
    prompt: str,
    *,
    system: Optional[str] = None,
    model: Optional[str] = None,
    temperature: float = 0.2,
    max_tokens: int = 512,
    timeout: float = 30.0,
    cache_key: Optional[str] = None,
    json_mode: bool = False,
) -> str:
    """One-shot chat completion. Returns model text or a marker string
    beginning with 'OPENAI_ERROR:'."""
    env = _env()
    key = env.get("OPENAI_API_KEY")
    if not key:
        return "OPENAI_ERROR: no API key configured"

    if cache_key:
        hit = _CACHE.get((cache_key, model or default_model()))
        if hit and time.time() - hit[0] < _CACHE_TTL:
            return hit[1]

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    body: dict = {
        "model": model or default_model(),
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}

    req = urllib.request.Request(
        _BASE,
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        return f"OPENAI_ERROR: HTTP {e.code}: {detail}"
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return f"OPENAI_ERROR: {type(e).__name__}: {e}"

    try:
        text = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return f"OPENAI_ERROR: unexpected shape: {json.dumps(payload)[:200]}"

    text = (text or "").strip()
    if cache_key:
        _CACHE[(cache_key, model or default_model())] = (time.time(), text)
    return text


def is_error(text: str) -> bool:
    return text.startswith("OPENAI_ERROR:")
