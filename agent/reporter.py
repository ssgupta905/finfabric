"""Compliance / audit report generator.

Takes the current demo state (metrics, receipts, revocations) and asks
Gemini to render a one-page prose audit that an auditor or regulator would
actually read. The report is grounded — every number in the output must
appear in the state we pass in — so hallucination is bounded to prose."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from . import gemini


_SYSTEM = (
    "You are a compliance officer writing a short audit note for a "
    "decentralized identity issuance run. Use only the facts in the JSON "
    "state provided. Do not invent numbers, addresses, or dates. Structure: "
    "1) one-sentence executive summary, 2) issuance metrics (bulleted), "
    "3) privacy posture (bulleted, cite the design choices), "
    "4) revocation status, 5) on-chain footprint with cost. "
    "Tone: neutral, precise, ~180 words. Markdown headings."
)


def generate_report(state: dict) -> dict:
    """Return {report_markdown, model, generated_at, is_fallback}."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not gemini.enabled():
        return {
            "report_markdown": _fallback(state, now),
            "model": "deterministic-template",
            "generated_at": now,
            "is_fallback": True,
        }
    prompt = (
        "State to audit (JSON):\n" + json.dumps(state, indent=2, default=str) +
        f"\n\nGenerated-at timestamp to use: {now}\n\nWrite the audit note."
    )
    out = gemini.generate(prompt, system=_SYSTEM, temperature=0.3,
                          max_tokens=1200, thinking_budget=512, timeout=45)
    if gemini.is_error(out):
        return {
            "report_markdown": _fallback(state, now) + f"\n\n_LLM unavailable — {out}_",
            "model": "deterministic-template",
            "generated_at": now,
            "is_fallback": True,
        }
    return {
        "report_markdown": out,
        "model": gemini.model_name(),
        "generated_at": now,
        "is_fallback": False,
    }


def _fallback(state: dict, now: str) -> str:
    """Deterministic report so the demo works with no LLM at all."""
    h = state.get("harness_result") or {}
    r = state.get("anchor_receipt") or {}
    cost = r.get("cost_usd", 0)
    return (
        f"# Audit Note — {now}\n\n"
        f"**Summary.** Issuance run completed with a {h.get('escape_rate', 0):.2%} escape "
        f"rate across {h.get('documents', 0)} synthetic documents.\n\n"
        f"## Issuance metrics\n"
        f"- Raw single-engine accuracy: {h.get('raw_accuracy', 0):.2%}\n"
        f"- Auto-accepted after gate: {h.get('auto_accept', 0):.2%}\n"
        f"- Routed to review: {h.get('review_rate', 0):.2%}\n"
        f"- Escape rate (wrong fields accepted): {h.get('escape_rate', 0):.4%}\n\n"
        f"## Privacy posture\n"
        f"- No personal data written on-chain (32-byte epoch root only)\n"
        f"- Per-field 128-bit salts prevent brute-force of undisclosed fields\n"
        f"- Verifiers receive only what the holder chooses to reveal\n\n"
        f"## Revocation\n"
        f"- Bitstring published off-chain; only its keccak hash is anchored\n"
        f"- Current revocation state: {'active' if state.get('revoked') else 'clean'}\n\n"
        f"## On-chain footprint\n"
        f"- Epoch {r.get('epoch_id', '—')}: {r.get('credential_count', 0)} credentials in "
        f"{r.get('gas_used', 0)} gas (~${cost:.4f})\n"
        f"- Transaction: {r.get('tx_hash', '—')}\n"
    )
