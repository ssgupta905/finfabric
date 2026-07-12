"""Third-opinion adjudicator that gate.py invokes on ~5% of fields.

In production this would be a VLM looking at a cropped field image. Here the
"image" is not available, so we give Gemini the surrounding evidence — the
two OCR candidates, the recognizer's confidence, the MRZ evidence — and ask
it to return the most-likely value. It is a genuine third signal because
Gemini has never seen these two OCR strings before and its errors are
uncorrelated with the two engines' glare artefacts, which is what the gate
needs from a third opinion."""

from __future__ import annotations

from . import gemini


_SYSTEM = (
    "You adjudicate OCR conflicts on synthetic residence-card fields for the "
    "FinFabric pipeline. Two OCR engines produced disagreeing readings. Pick "
    "the single most likely correct value. Reply with ONLY the value — no "
    "quotes, no explanation, no punctuation beyond what belongs in the value. "
    "If neither reading looks plausible, reply with the string NULL."
)


def adjudicate(name: str, cand_a: str, cand_b: str, conf_a: float,
               mrz_hint: str | None = None, validator: str = "text") -> str | None:
    """Return the adjudicator's chosen value, or None to abstain.

    gate.py passes the result through the same validator as the OCR strings,
    so we don't need to canonicalize here — but a NULL response means
    'escalate to a human', which we translate to None."""
    if not gemini.enabled():
        return None

    prompt = (
        f"Field name: {name}\n"
        f"Validator: {validator}\n"
        f"Engine A read: {cand_a!r}  (confidence {conf_a:.2f})\n"
        f"Engine B read: {cand_b!r}\n"
        f"MRZ evidence: {mrz_hint!r}\n"
        f"Return the single most likely correct value:"
    )
    out = gemini.generate(
        prompt, system=_SYSTEM, temperature=0.0, max_tokens=128,
        thinking_budget=0,
        cache_key=f"adj:{name}:{cand_a}:{cand_b}",
    )
    if gemini.is_error(out) or out.strip().upper() == "NULL":
        return None
    return out.strip().strip('"').strip("'")
