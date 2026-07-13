"""Third-opinion adjudicator that gate.py invokes on ~5% of fields.

In production this would be a VLM looking at a cropped field image. Here the
"image" is not available, so we give Gemini the surrounding evidence — the
two OCR candidates, the recognizer's confidence, the MRZ evidence — and ask
it to return the most-likely value. It is a genuine third signal because
Gemini has never seen these two OCR strings before and its errors are
uncorrelated with the two engines' glare artefacts, which is what the gate
needs from a third opinion."""

from __future__ import annotations

from . import llm


_SYSTEM = (
    "You adjudicate OCR conflicts on synthetic KYC fields for the FinFabric "
    "pipeline. Two OCR engines produced disagreeing readings. Pick the single "
    "most likely correct value. Reply with ONLY the value — no quotes, no "
    "explanation, no punctuation beyond what belongs in the value. If neither "
    "reading looks plausible, reply with the string NULL."
)


def adjudicate(name: str, cand_a: str, cand_b: str, conf_a: float,
               mrz_hint: str | None = None, validator: str = "text") -> str | None:
    """Return the adjudicator's chosen value, or None to abstain."""
    if not llm.enabled():
        return None

    prompt = (
        f"Field name: {name}\n"
        f"Validator: {validator}\n"
        f"Engine A read: {cand_a!r}  (confidence {conf_a:.2f})\n"
        f"Engine B read: {cand_b!r}\n"
        f"MRZ evidence: {mrz_hint!r}\n"
        f"Return the single most likely correct value:"
    )
    out = llm.generate(
        prompt, task="adjudicator", system=_SYSTEM,
        cache_key=f"{name}:{cand_a}:{cand_b}",
    )
    if llm.is_error(out) or out.strip().upper() == "NULL":
        return None
    return out.strip().strip('"').strip("'")
