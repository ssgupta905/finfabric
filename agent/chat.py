"""Grounded chat assistant. Answers questions about the CURRENT demo state
plus the design context from the README. Refuses to speculate outside
that scope — the point of FinFabric is that most decisions are
deterministic, and the assistant should reinforce that framing."""

from __future__ import annotations

import json
from pathlib import Path

from . import gemini

REPO = Path(__file__).resolve().parents[1]


def _readme_excerpt() -> str:
    """Trim README to the sections a user is likely to ask about."""
    txt = (REPO / "README.md").read_text() if (REPO / "README.md").exists() else ""
    # Keep it under ~4k chars to bound token use.
    return txt[:4000]


_SYSTEM = (
    "You are a helpful assistant embedded in the FinFabric demo UI. "
    "FinFabric is a decentralized identity pipeline that anchors salted "
    "Merkle roots on Base and uses a deterministic confidence gate to avoid "
    "paid human verifiers. Answer using ONLY the design context and current "
    "demo state provided. If asked about something outside that scope, say "
    "so briefly and suggest a related question that IS grounded. Keep "
    "answers under 120 words unless the user explicitly asks for detail. "
    "When you cite a number, cite it exactly as it appears in the state."
)


def answer(user_message: str, state: dict, history: list[dict] | None = None) -> dict:
    """Return {answer, model, is_fallback}. `history` is a list of
    {"role": "user"|"assistant", "content": str} turns."""
    if not gemini.enabled():
        return {
            "answer": "The Gemini API key is not configured, so the chat "
                      "assistant is disabled. Set GEMINI_API_KEY in .env and "
                      "restart the server to enable it.",
            "model": "fallback",
            "is_fallback": True,
        }

    context = (
        "## Design context (excerpt from README)\n" + _readme_excerpt() +
        "\n\n## Current demo state (JSON)\n" +
        json.dumps({k: v for k, v in state.items() if k != "raw_output"},
                   indent=2, default=str)[:3000]
    )
    turns = history or []
    convo = "\n".join(f"{t['role'].upper()}: {t['content']}" for t in turns[-6:])
    prompt = (
        context + "\n\n## Conversation so far\n" + convo +
        f"\n\nUSER: {user_message}\nASSISTANT:"
    )
    out = gemini.generate(prompt, system=_SYSTEM, temperature=0.3,
                          max_tokens=700, thinking_budget=256, timeout=30)
    if gemini.is_error(out):
        return {"answer": f"Assistant is temporarily unavailable ({out}).",
                "model": "fallback", "is_fallback": True}
    return {"answer": out, "model": gemini.model_name(), "is_fallback": False}
