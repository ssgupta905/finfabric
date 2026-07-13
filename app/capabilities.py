"""Banking-native capabilities the workflow engine can compose.

Each capability is a pure function of (record, params, context) → result dict.
The result carries:
  ok        — bool, whether the step passed
  value     — canonical value or lookup result, if any
  detail    — one-line human summary shown in the UI
  signals   — dict of sub-signals for the audit trail
  duration_ms — for the timeline

Adding a capability is a matter of registering a callable in CAPABILITIES.
"""

from __future__ import annotations

import hashlib
import random
import re
import time
from dataclasses import dataclass


# ---------- primitive validators (fast, deterministic) --------------------

_PAN_RE      = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
_IFSC_RE     = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
_GSTIN_RE    = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
_AADH_MASKED = re.compile(r"^X{4}[-\s]?X{4}[-\s]?[0-9]{4}$")
_PIN_RE      = re.compile(r"^[1-9][0-9]{5}$")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _timed(fn):
    """Wrap a capability implementation with a timing decorator."""
    def wrapped(record, params, context):
        t0 = time.time()
        r = fn(record, params, context)
        r["duration_ms"] = int((time.time() - t0) * 1000)
        return r
    return wrapped


# ---------- individual capabilities ---------------------------------------

@_timed
def cap_pan_validate(record, params, ctx):
    value = str(record.get("pan") or record.get("id_no") or "").upper().replace("-", "")
    ok = bool(_PAN_RE.match(value))
    return {
        "ok": ok, "value": value if ok else None,
        "detail": "PAN format valid" if ok else f"PAN pattern failed for {value!r}",
        "signals": {"format": ok, "length": len(value) == 10},
    }


@_timed
def cap_aadhaar_masked_validate(record, params, ctx):
    v = str(record.get("aadhaar_masked") or record.get("aadhaar") or "").upper()
    ok = bool(_AADH_MASKED.match(v))
    return {
        "ok": ok, "value": v if ok else None,
        "detail": "Masked Aadhaar (last 4 exposed) OK" if ok else "Aadhaar mask pattern invalid — never accept full 12-digit",
        "signals": {"pattern": ok, "no_full_aadhaar": not re.match(r"^[0-9]{12}$", v)},
    }


@_timed
def cap_gstin_validate(record, params, ctx):
    """GSTIN: 15 chars, 2-digit state + 10-char PAN + 1 entity + Z + 1 check.
    Full checksum: base-36 weighted algorithm."""
    v = str(record.get("gstin") or "").upper()
    format_ok = bool(_GSTIN_RE.match(v))
    checksum_ok = format_ok and _gstin_checksum_ok(v)
    ok = format_ok and checksum_ok
    return {
        "ok": ok, "value": v if ok else None,
        "detail": "GSTIN valid" if ok else ("GSTIN checksum failed" if format_ok else "GSTIN format invalid"),
        "signals": {"format": format_ok, "checksum": checksum_ok,
                    "state_code": v[:2] if format_ok else None,
                    "pan_embedded": v[2:12] if format_ok else None},
    }


def _gstin_checksum_ok(g: str) -> bool:
    if len(g) != 15:
        return False
    alpha = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    factor = 1
    total = 0
    for c in g[:14]:
        val = alpha.index(c) * factor
        total += val // 36 + val % 36
        factor = 2 if factor == 1 else 1
    check = (36 - total % 36) % 36
    return alpha[check] == g[14]


@_timed
def cap_ifsc_validate(record, params, ctx):
    v = str(record.get("ifsc") or "").upper()
    ok = bool(_IFSC_RE.match(v))
    return {
        "ok": ok, "value": v if ok else None,
        "detail": "IFSC format valid" if ok else "IFSC format invalid",
        "signals": {"format": ok, "bank_code": v[:4] if ok else None},
    }


@_timed
def cap_pincode_validate(record, params, ctx):
    v = str(record.get("pincode") or "")
    if not v and record.get("address"):
        # last 6-digit chunk in address
        m = re.search(r"\b([1-9][0-9]{5})\b", record["address"])
        if m: v = m.group(1)
    ok = bool(_PIN_RE.match(v))
    return {
        "ok": ok, "value": v if ok else None,
        "detail": f"PIN {v} looks valid" if ok else "PIN code missing or invalid",
        "signals": {"format": ok, "state_hint": _pin_to_region(v) if ok else None},
    }


def _pin_to_region(pin: str) -> str:
    """First digit → RBI region cluster (rough)."""
    d = int(pin[0])
    return ["NORTH", "NORTH", "WEST", "WEST", "SOUTH", "SOUTH", "EAST", "EAST"][d - 1]


# ---------- lookups / screening (mocked; deterministic pseudo-random) -----

_SANCTIONS_MOCK = {
    "ABCDE1234F", "ZZZZZ9999Z",  # PAN-based hits
    "SHARMA VILAS", "IVANOV ILYA",
}
_PEP_MOCK = {"MEHTA MEERA", "REDDY NEHA"}


@_timed
def cap_sanctions_screen(record, params, ctx):
    name = str(record.get("name", "")).upper()
    pan = str(record.get("pan") or record.get("id_no") or "").upper().replace("-", "")
    hit = name in _SANCTIONS_MOCK or pan in _SANCTIONS_MOCK
    return {
        "ok": not hit, "value": "clear" if not hit else "HIT",
        "detail": f"Sanctions clear on {params.get('list', 'PMLA/OFAC')}" if not hit
                  else f"⚠ Sanctions HIT on {params.get('list', 'PMLA/OFAC')} — escalate to compliance",
        "signals": {"list": params.get("list", "PMLA/OFAC"), "hit": hit, "checked_at": _now_ms()},
    }


@_timed
def cap_pep_check(record, params, ctx):
    name = str(record.get("name", "")).upper()
    hit = name in _PEP_MOCK
    return {
        "ok": not hit, "value": "not PEP" if not hit else "PEP MATCH",
        "detail": "Not a Politically Exposed Person" if not hit
                  else "⚠ PEP match — enhanced due diligence required per RBI",
        "signals": {"hit": hit},
    }


@_timed
def cap_aml_risk_score(record, params, ctx):
    """Deterministic rule-based AML risk score (0-100).
    Combines: high-risk country nationality, corporate category, PEP hit, address inconsistency."""
    score = 10  # baseline
    reasons = []
    high_risk_countries = {"UAE", "ARE", "IRN"}
    if str(record.get("nationality", "")).upper() in high_risk_countries:
        score += 30; reasons.append("high-risk jurisdiction")
    if "CORPORATE" in str(record.get("status", "")).upper():
        score += 15; reasons.append("corporate entity")
    # Prior signals set by upstream nodes in the workflow context.
    # signals_bus stores the .ok of each capability; PEP/sanctions .ok=True
    # means "no hit". A HIT is what raises AML risk.
    prior = ctx.get("signals_bus", {})
    if prior.get("pep_check") is False:
        score += 40; reasons.append("PEP match")
    if prior.get("sanctions_screen") is False:
        score += 30; reasons.append("sanctions hit")
    tier = "LOW" if score < 25 else "MEDIUM" if score < 60 else "HIGH"
    return {
        "ok": tier != "HIGH", "value": score,
        "detail": f"AML risk: {tier} ({score}/100) — " + (", ".join(reasons) if reasons else "no risk factors"),
        "signals": {"tier": tier, "score": score, "reasons": reasons},
    }


@_timed
def cap_address_verify(record, params, ctx):
    """Mock address verification: check the address parses into street/city/PIN."""
    addr = str(record.get("address", ""))
    lines = [l.strip() for l in addr.splitlines() if l.strip()]
    pin_ok = bool(re.search(r"\b[1-9][0-9]{5}\b", addr))
    ok = len(lines) >= 2 and pin_ok
    return {
        "ok": ok, "value": addr if ok else None,
        "detail": "Address structure parses" if ok else "Address missing PIN or too short",
        "signals": {"lines": len(lines), "pin_present": pin_ok},
    }


@_timed
def cap_document_classify(record, params, ctx):
    """Given a record, guess the document class. In production this is a
    vision-model call on the uploaded image."""
    cats = params.get("categories", ["passport", "aadhaar", "pan_card", "driving_license"])
    # Deterministic pseudo-pick based on record hash
    h = hashlib.md5(str(record).encode()).hexdigest()
    idx = int(h[:2], 16) % len(cats)
    return {
        "ok": True, "value": cats[idx],
        "detail": f"Classifier: {cats[idx]} (mock)",
        "signals": {"class": cats[idx], "candidates": cats},
    }


@_timed
def cap_extract_fields(record, params, ctx):
    """Extract & canonicalize the standard KYC fields. Present-and-valid
    counts as extracted; genuinely missing fields don't fail the node —
    a downstream validator will complain if it needs one."""
    from schema import FIELDS
    from validators import VALIDATORS
    out = {}
    for f in FIELDS:
        raw = record.get(f.name, "")
        if not raw:
            continue
        ok, v, _ = VALIDATORS[f.validator](raw)
        if ok: out[f.name] = v
    return {
        "ok": len(out) >= 6, "value": out,
        "detail": f"Extracted {len(out)} of {len(FIELDS)} canonical fields",
        "signals": {"fields_extracted": len(out), "field_names": list(out.keys())},
    }


@_timed
def cap_commit_merkle(record, params, ctx):
    """Build a salted Merkle credential from context.extracted."""
    import merkle
    from schema import FIELDS
    values = ctx.get("extracted", {})
    if not values:
        return {"ok": False, "value": None,
                "detail": "No extracted fields to commit — upstream extract must run first",
                "signals": {"missing_upstream": True}}
    order = [f.name for f in FIELDS if f.name in values]
    cred = merkle.issue("did:pkh:eip155:84532:0x" + hashlib.sha256(str(record).encode()).hexdigest()[:40],
                        "did:web:issuer.finfabric.demo", values, order)
    ctx["credential"] = cred
    return {
        "ok": True, "value": cred.root.hex()[:24] + "…",
        "detail": f"Built {len(order)} salted leaves → credential root",
        "signals": {"root_hex": cred.root.hex(), "leaves": len(order)},
    }


@_timed
def cap_anchor_epoch(record, params, ctx):
    """Anchor the committed credential in a fresh single-cred epoch."""
    import merkle
    from issuer.anchor_client import anchor_epoch_onchain
    cred = ctx.get("credential")
    if cred is None:
        return {"ok": False, "value": None,
                "detail": "No credential in context — commit must run first",
                "signals": {"missing_upstream": True}}
    epoch_id = int(time.time())
    root = merkle.anchor_epoch([cred], epoch_id=epoch_id)
    receipt = anchor_epoch_onchain(epoch_id, root, 1)
    ctx["anchor_receipt"] = receipt
    return {
        "ok": True, "value": receipt["tx_hash"][:20] + "…",
        "detail": f"Anchored to Base · {receipt['gas_used']:,} gas · ${receipt.get('cost_usd', 0):.6f}",
        "signals": {"tx_hash": receipt["tx_hash"], "gas": receipt["gas_used"],
                    "epoch_id": epoch_id, "mode": receipt.get("mode")},
    }


# ---------- registry -----------------------------------------------------

@dataclass
class Capability:
    key: str
    label: str
    category: str          # extract | validate | screen | commit | anchor
    fn: callable
    inputs: list           # what fields it needs on the record
    outputs: list          # what it puts in the context bus
    default_params: dict


CAPABILITIES: dict[str, Capability] = {
    "extract_fields": Capability(
        "extract_fields", "Extract & canonicalise fields", "extract",
        cap_extract_fields, ["name", "date_of_birth", "..."], ["extracted"], {},
    ),
    "pan_validate": Capability(
        "pan_validate", "PAN format validator", "validate",
        cap_pan_validate, ["pan"], ["pan_valid"], {},
    ),
    "aadhaar_masked_validate": Capability(
        "aadhaar_masked_validate", "Masked-Aadhaar validator", "validate",
        cap_aadhaar_masked_validate, ["aadhaar_masked"], ["aadhaar_valid"], {},
    ),
    "gstin_validate": Capability(
        "gstin_validate", "GSTIN validator (with checksum)", "validate",
        cap_gstin_validate, ["gstin"], ["gstin_valid"], {},
    ),
    "ifsc_validate": Capability(
        "ifsc_validate", "IFSC format validator", "validate",
        cap_ifsc_validate, ["ifsc"], ["ifsc_valid"], {},
    ),
    "pincode_validate": Capability(
        "pincode_validate", "PIN code validator", "validate",
        cap_pincode_validate, ["pincode", "address"], ["pincode_valid"], {},
    ),
    "address_verify": Capability(
        "address_verify", "Address structure check", "validate",
        cap_address_verify, ["address"], ["address_valid"], {},
    ),
    "document_classify": Capability(
        "document_classify", "Document classifier (VLM)", "extract",
        cap_document_classify, ["_image"], ["doc_class"],
        {"categories": ["passport", "aadhaar", "pan_card", "driving_license"]},
    ),
    "sanctions_screen": Capability(
        "sanctions_screen", "Sanctions / PMLA screen", "screen",
        cap_sanctions_screen, ["name", "pan"], ["sanctions_screen"],
        {"list": "PMLA/OFAC"},
    ),
    "pep_check": Capability(
        "pep_check", "PEP (Politically Exposed Person) check", "screen",
        cap_pep_check, ["name"], ["pep_check"], {},
    ),
    "aml_risk_score": Capability(
        "aml_risk_score", "AML risk score (rule-based)", "screen",
        cap_aml_risk_score, ["nationality", "status"], ["aml_score"], {},
    ),
    "commit_merkle": Capability(
        "commit_merkle", "Commit salted Merkle credential", "commit",
        cap_commit_merkle, ["extracted"], ["credential"], {},
    ),
    "anchor_epoch": Capability(
        "anchor_epoch", "Anchor to Base (or fixture)", "anchor",
        cap_anchor_epoch, ["credential"], ["anchor_receipt"], {},
    ),
}


def capability_descriptions() -> list[dict]:
    """Serialisable form for the frontend palette + copilot prompt."""
    return [{
        "key": c.key, "label": c.label, "category": c.category,
        "inputs": c.inputs, "outputs": c.outputs, "default_params": c.default_params,
    } for c in CAPABILITIES.values()]
