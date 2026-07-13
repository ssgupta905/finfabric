"""Unified LLM router across providers (openai, gemini).

Every agent module goes through `generate(task=..., prompt=...)` so we
can pick the best-priced model per task and gracefully fall back to the
other provider when the primary errors out.

Task profiles (chosen for cost/quality balance):

  explainer    — 1-sentence gloss on why a field went to review.
                 Uses a nano-tier model (cheapest, fine for short output).

  adjudicator  — pick one of two OCR candidates. Short, deterministic.
                 Nano-tier model with temperature 0.

  copilot_gen  — generate a full workflow JSON. Needs JSON mode / structure.
                 Uses mini-tier for reliable structured output.

  copilot_refine — modify an existing workflow. Same needs as copilot_gen.

  report       — audit-style compliance note, ~180 words markdown.
                 Mini-tier: cheap, formats markdown well.

  chat         — grounded, conversational. Mini-tier.

Fallback logic: try primary; if it returns *_ERROR:*, try the other provider.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from . import gemini
from . import openai_client as oai

REPO = Path(__file__).resolve().parents[1]


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


def _primary() -> str:
    """Which provider to try first. Defaults to openai if key set,
    else gemini."""
    forced = _env().get("LLM_PRIMARY", "").lower().strip()
    if forced in ("openai", "gemini"):
        return forced
    return "openai" if oai.enabled() else "gemini"


# Per-task model choices. Kept here (not in per-agent code) so cost tuning
# is a one-line change. Nano is cheapest — use it for short outputs.
_TASK_PROFILE: dict[str, dict] = {
    "explainer": {
        "openai_model": "gpt-4.1-nano",
        "gemini_thinking": 0,
        "max_tokens": 120, "temperature": 0.2,
    },
    "adjudicator": {
        "openai_model": "gpt-4.1-nano",
        "gemini_thinking": 0,
        "max_tokens": 80, "temperature": 0.0,
    },
    "copilot_gen": {
        "openai_model": "gpt-4o-mini",
        "gemini_thinking": 512,
        "max_tokens": 1400, "temperature": 0.2,
        "json_mode": True,
    },
    "copilot_refine": {
        "openai_model": "gpt-4o-mini",
        "gemini_thinking": 512,
        "max_tokens": 1400, "temperature": 0.2,
        "json_mode": True,
    },
    "report": {
        "openai_model": "gpt-4o-mini",
        "gemini_thinking": 512,
        "max_tokens": 1200, "temperature": 0.3,
    },
    "chat": {
        "openai_model": "gpt-4o-mini",
        "gemini_thinking": 256,
        "max_tokens": 500, "temperature": 0.3,
    },
    "default": {
        "openai_model": "gpt-4o-mini",
        "gemini_thinking": 128,
        "max_tokens": 512, "temperature": 0.3,
    },
}


def generate(
    prompt: str,
    *,
    task: str = "default",
    system: Optional[str] = None,
    cache_key: Optional[str] = None,
    timeout: float = 30.0,
    max_tokens_override: Optional[int] = None,
) -> str:
    """Route to the best provider for this task, with automatic fallback."""
    profile = _TASK_PROFILE.get(task, _TASK_PROFILE["default"])
    max_toks = max_tokens_override or profile.get("max_tokens", 512)
    temp = profile.get("temperature", 0.2)
    json_mode = profile.get("json_mode", False)

    order = [_primary(), "gemini" if _primary() == "openai" else "openai"]

    for provider in order:
        if provider == "openai" and oai.enabled():
            out = oai.generate(
                prompt,
                system=system,
                model=profile.get("openai_model"),
                temperature=temp,
                max_tokens=max_toks,
                timeout=timeout,
                cache_key=(f"{task}:{cache_key}" if cache_key else None),
                json_mode=json_mode,
            )
            if not oai.is_error(out):
                return out
        elif provider == "gemini" and gemini.enabled():
            # Gemini needs a big enough max_tokens including thinking budget.
            think = profile.get("gemini_thinking", 128)
            out = gemini.generate(
                prompt,
                system=system,
                temperature=temp,
                max_tokens=max_toks + think,
                timeout=timeout,
                cache_key=(f"{task}:{cache_key}" if cache_key else None),
                thinking_budget=think,
            )
            if not gemini.is_error(out):
                return out

    # Both providers failed or none available.
    return "LLM_ERROR: no provider available or all providers errored"


def is_error(text: str) -> bool:
    return text.startswith(("LLM_ERROR:", "OPENAI_ERROR:", "GEMINI_ERROR:"))


def enabled() -> bool:
    return oai.enabled() or gemini.enabled()


def describe() -> dict:
    """Snapshot of provider config, exposed to /api/health for judges."""
    return {
        "primary": _primary(),
        "openai_enabled": oai.enabled(),
        "openai_model": oai.default_model() if oai.enabled() else None,
        "gemini_enabled": gemini.enabled(),
        "gemini_model": gemini.model_name() if gemini.enabled() else None,
    }
