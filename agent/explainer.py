"""One-line human explanations for reviewed fields. Called on demand from
the UI, so cost stays bounded — never in the harness inner loop."""

from __future__ import annotations

from . import gemini

_SYSTEM = (
    "You explain in ONE sentence why a residence-card field was flagged for "
    "review by the FinFabric confidence gate. Be specific about which "
    "signal failed. No preamble, no restating the input, just the sentence."
)


def explain(name: str, cand_a: str, cand_b: str, conf: float,
            signals: dict, reason: str) -> str:
    if not gemini.enabled():
        return _fallback(name, cand_a, cand_b, signals, reason)
    prompt = (
        f"Field: {name}\n"
        f"Engine A: {cand_a!r}  (confidence {conf:.2f})\n"
        f"Engine B: {cand_b!r}\n"
        f"Signals passed: {signals}\n"
        f"Technical reason: {reason}\n"
    )
    out = gemini.generate(
        prompt, system=_SYSTEM, temperature=0.2, max_tokens=150,
        thinking_budget=0,
        cache_key=f"exp:{name}:{cand_a}:{cand_b}:{reason}",
    )
    if gemini.is_error(out):
        return _fallback(name, cand_a, cand_b, signals, reason)
    return out


def _fallback(name, cand_a, cand_b, signals, reason):
    if not signals.get("agreement"):
        return f"The two OCR engines disagreed on '{name}' ({cand_a!r} vs {cand_b!r})."
    if signals.get("mrz") is False:
        return f"The MRZ check digit contradicts OCR for '{name}'."
    if not signals.get("confidence"):
        return f"Recognizer confidence for '{name}' fell below the no-oracle threshold."
    return f"'{name}' failed gate: {reason}."
