"""Runs the real gate + Merkle pipeline on N synthetic documents and keeps
every intermediate decision. This is what feeds the drill-down views —
per-doc, per-field, per-signal state that would otherwise be discarded by
the aggregate harness."""

from __future__ import annotations

import hashlib
import random
import string
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterator, Optional

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import merkle
from gate import gate_document
from records import make_record
from schema import FIELDS, FIELD_BY_NAME
from validators import VALIDATORS


@dataclass
class FieldDecision:
    field: str
    label: str
    value: Optional[str]        # what the pipeline decided (post-repair)
    accepted: bool
    confidence: float
    signals: dict               # {schema, agreement, confidence, mrz, adjudicator}
    reason: str
    ocr_a: str                  # engine A raw
    ocr_a_conf: float
    ocr_b: str                  # engine B raw
    mrz_value: Optional[str]    # what the MRZ said, if it parsed
    truth: str                  # ground truth
    correct: bool               # matches truth after canonicalization
    in_mrz: bool
    validator: str


@dataclass
class DocResult:
    doc_id: str
    subject_did: str
    action: str                 # issue | review | recapture
    ok: bool
    mrz_ok: bool
    mrz_errors: list
    cross_field_errors: list
    decisions: list             # list[FieldDecision]
    truth: dict                 # canonical truth per field
    accepted_values: dict       # canonical accepted values (only when ok)
    reviewed_fields: list       # names of fields in review
    latency_ms: int
    created_at: float           # unix ts


@dataclass
class IssuedCredential:
    index: int
    doc_id: str
    subject_did: str
    epoch_id: Optional[int]
    root_hex: str
    values: dict
    field_order: list
    salts_hex: dict             # for demo transparency, real wallets keep private
    revoked: bool = False
    # Runtime object retained for disclosures; not serialized directly.
    _merkle_cred: Optional[object] = field(default=None, repr=False)


def _corrupt(s: str, cer: float, rng: random.Random) -> tuple[str, bool]:
    out, hit = [], False
    for ch in s:
        if rng.random() < cer:
            hit = True
            if ch.isdigit():
                out.append(rng.choice("0123456789"))
            elif ch.isalpha():
                out.append(rng.choice(string.ascii_uppercase))
            else:
                out.append(ch)
        else:
            out.append(ch)
    return "".join(out), hit


_DEFAULT_CER = {"name": .015, "date_of_birth": .018, "sex": .022, "nationality": .030,
                "address": .079, "status": .033, "date_of_expiry": .037, "id_no": .026,
                "date_of_issue": .024, "period_of_stay": .029}


@dataclass
class Scenario:
    """Knobs that shape the simulated OCR errors and MRZ behaviour.
    Every scenario in SCENARIOS is a preset over these knobs."""
    cer_multiplier: float = 1.0
    cer_overrides: dict = field(default_factory=dict)   # per-field CER overrides
    mrz_readable_rate: float = 0.94
    date_swap_rate: float = 0.02                        # expiry ↔ issue swap
    engine_b_correlation: float = 0.35                  # of B errors shared with A


def _effective_cer(scn: Scenario) -> dict:
    return {name: scn.cer_overrides.get(name, _DEFAULT_CER[name]) * scn.cer_multiplier
            for name in _DEFAULT_CER}


def _fake_engines(rec, rng, cer: dict, scn: Scenario):
    a, b = {}, {}
    ocr_debug = {}
    for f in FIELDS:
        truth = rec[f.name]
        ta, hit_a = _corrupt(truth, cer[f.name], rng)
        tb = ta if (hit_a and rng.random() < scn.engine_b_correlation) \
             else _corrupt(truth, cer[f.name] * 0.8, rng)[0]
        if f.name == "date_of_expiry" and rng.random() < scn.date_swap_rate:
            ta = tb = rec["date_of_issue"]
        conf = 0.995 if ta == truth else rng.uniform(0.62, 0.97)
        a[f.name] = (ta, conf)
        b[f.name] = tb
        ocr_debug[f.name] = (ta, conf, tb)
    mrz = rec["_mrz"] if (rng.random() < scn.mrz_readable_rate) else None
    return a, b, mrz, ocr_debug


SCENARIOS = {
    "baseline": Scenario(
        # As reported in BDIMS: address ~7.9% CER dominates the review rate.
    ),
    "fine_tuned": Scenario(
        # Sensitivity table target: address CER ≤ 0.8% — in-domain fine-tune.
        cer_overrides={"address": 0.008},
    ),
    "date_swap_adversarial": Scenario(
        # Attacker triggers the adjacent-date confusion at 20% rate. MRZ must
        # catch it; without MRZ, an escape is a real risk.
        date_swap_rate=0.20,
    ),
    "mrz_blown_out": Scenario(
        # Glare on the MRZ zone means most docs cannot self-check. Docs are
        # routed to recapture rather than manually transcribed.
        mrz_readable_rate=0.40,
    ),
    "correlated_engines": Scenario(
        # Two engines share more failure modes (same photo, same glare).
        # Naive dual-engine voting starts to fail here — the gate holds.
        engine_b_correlation=0.85,
    ),
    "high_volume": Scenario(
        # Realistic quality but a larger batch — shows constant on-chain cost.
        cer_overrides={"address": 0.04},
    ),
}


def _truth_canonical(rec):
    out = {}
    for f in FIELDS:
        _, v, _ = VALIDATORS[f.validator](rec[f.name])
        out[f.name] = v
    return out


def _adjudicate_stub(name, rec, rng):
    """Match harness.py's cheap 97%-accurate stand-in. Real production would
    swap this for agent.adjudicator.adjudicate."""
    if rng.random() < 0.97:
        return rec[name]
    return _corrupt(rec[name], 0.05, rng)[0]


def run_one(rng: random.Random, doc_id: str, scn: Scenario = None) -> tuple[DocResult, dict]:
    """Runs one doc through the pipeline. Returns (result, ocr_debug_map)."""
    scn = scn or Scenario()
    cer = _effective_cer(scn)
    t0 = time.time()
    rec = make_record(rng)
    truth = _truth_canonical(rec)
    a, b, mrz, ocr_debug = _fake_engines(rec, rng, cer, scn)

    adj = lambda name, _r=rec, _rng=rng: _adjudicate_stub(name, _r, _rng)
    gate_res = gate_document(a, b, mrz, adjudicate=adj)

    # Reconstruct MRZ values per field (what the MRZ parser saw) if any.
    from validators import parse_mrz
    mrz_vals = {}
    if mrz:
        _, mrz_fields, _ = parse_mrz(mrz)
        mrz_vals = mrz_fields

    decisions: list[FieldDecision] = []
    for f in FIELDS:
        r = gate_res["fields"][f.name]
        ocr_a, ocr_a_conf, ocr_b = ocr_debug[f.name]
        correct = (r.value == truth[f.name])
        decisions.append(FieldDecision(
            field=f.name, label=f.label,
            value=r.value, accepted=r.accepted, confidence=r.confidence,
            signals=dict(r.signals or {}),
            reason=r.reason,
            ocr_a=ocr_a, ocr_a_conf=ocr_a_conf, ocr_b=ocr_b,
            mrz_value=mrz_vals.get(f.name),
            truth=truth[f.name], correct=correct,
            in_mrz=f.in_mrz, validator=f.validator,
        ))

    accepted = {d.field: d.value for d in decisions if d.accepted}
    reviewed = [d.field for d in decisions if not d.accepted]

    result = DocResult(
        doc_id=doc_id,
        subject_did=f"did:pkh:eip155:84532:0x{hashlib.sha256(doc_id.encode()).hexdigest()[:40]}",
        action=gate_res["action"],
        ok=gate_res["ok"],
        mrz_ok=gate_res["mrz_ok"],
        mrz_errors=list(gate_res["mrz_errors"] or []),
        cross_field_errors=list(gate_res["cross_field_errors"] or []),
        decisions=decisions,
        truth=truth,
        accepted_values=accepted if gate_res["ok"] else {},
        reviewed_fields=reviewed,
        latency_ms=int((time.time() - t0) * 1000),
        created_at=time.time(),
    )
    return result, ocr_debug


def issue_credential(doc: DocResult, field_order: list, issuer_did: str) -> IssuedCredential:
    cred = merkle.issue(doc.subject_did, issuer_did, doc.accepted_values, field_order)
    return IssuedCredential(
        index=-1,  # set by caller
        doc_id=doc.doc_id,
        subject_did=doc.subject_did,
        epoch_id=None,  # set at anchor time
        root_hex=cred.root.hex(),
        values=dict(doc.accepted_values),
        field_order=list(field_order),
        salts_hex={k: v.hex() for k, v in cred.salts.items()},
        _merkle_cred=cred,
    )


def run_batch(n: int, seed: int, on_step: Optional[Callable[[dict], None]] = None,
              scenario: Scenario = None, pace_ms: int = 0) -> dict:
    """Run N docs and return a rich batch report. `on_step(evt)` is called
    per-stage for streaming callers. `pace_ms` inserts a delay between docs
    so a human can follow the pipeline live."""
    scn = scenario or Scenario()
    rng = random.Random(seed)
    docs: list[DocResult] = []
    creds: list[IssuedCredential] = []
    stats = {
        "documents": 0, "auto_accepted": 0, "reviewed": 0, "recapture": 0,
        "escapes": 0, "field_total": 0, "field_ok_raw": 0, "field_accepted": 0,
        "field_reviewed": 0, "per_field_escapes": {},
    }

    field_order = [f.name for f in FIELDS]
    for i in range(n):
        doc_id = f"doc-{seed:03d}-{i:04d}"

        # Emit stage-level narrative BEFORE running so the UI can animate the
        # pipeline node transitions in sync with actual work.
        if on_step:
            on_step({"type": "stage", "index": i, "total": n, "doc_id": doc_id,
                     "stage": "capture", "detail": "customer submits KYC document"})
            if pace_ms: time.sleep(pace_ms / 1000)
            on_step({"type": "stage", "index": i, "total": n, "doc_id": doc_id,
                     "stage": "extract", "detail": "OCR engine A + engine B + MRZ parse"})
            if pace_ms: time.sleep(pace_ms / 1000)

        d, _ = run_one(rng, doc_id, scn=scn)
        docs.append(d)
        stats["documents"] += 1

        if on_step:
            on_step({"type": "stage", "index": i, "total": n, "doc_id": doc_id,
                     "stage": "gate", "detail": f"schema + agreement + confidence + MRZ + cross-field on {len(d.decisions)} fields"})
            if pace_ms: time.sleep(pace_ms / 1000)
            reviewed = len(d.reviewed_fields)
            if reviewed:
                on_step({"type": "stage", "index": i, "total": n, "doc_id": doc_id,
                         "stage": "adjudicate", "detail": f"{reviewed} field{'s' if reviewed != 1 else ''} escalated: {', '.join(d.reviewed_fields[:3])}"})
                if pace_ms: time.sleep(pace_ms / 1000)
            if d.action == "issue":
                on_step({"type": "stage", "index": i, "total": n, "doc_id": doc_id,
                         "stage": "commit", "detail": f"build 10 salted Merkle leaves → credential root"})
                if pace_ms: time.sleep(pace_ms / 1000)

        # Raw single-engine accuracy (what BDIMS anchors)
        for dec in d.decisions:
            stats["field_total"] += 1
            _, canon_a, _ = VALIDATORS[dec.validator](dec.ocr_a or "")
            if canon_a == dec.truth:
                stats["field_ok_raw"] += 1
            if dec.accepted:
                stats["field_accepted"] += 1
                if not dec.correct:
                    stats["escapes"] += 1
                    stats["per_field_escapes"][dec.field] = \
                        stats["per_field_escapes"].get(dec.field, 0) + 1
            else:
                stats["field_reviewed"] += 1

        if d.action == "issue":
            stats["auto_accepted"] += 1
            ic = issue_credential(d, field_order, "did:web:issuer.finfabric.demo")
            ic.index = len(creds)
            creds.append(ic)
        elif d.action == "recapture":
            stats["recapture"] += 1
        else:
            stats["reviewed"] += 1

        if on_step:
            # Compact per-field payload so the UI can render decisions/truth/OCR
            # side-by-side without a follow-up fetch.
            compact_decisions = [{
                "field": dec.field, "label": dec.label,
                "value": dec.value, "truth": dec.truth, "correct": dec.correct,
                "accepted": dec.accepted, "confidence": round(dec.confidence, 3),
                "signals": dec.signals, "reason": dec.reason,
                "ocr_a": dec.ocr_a, "ocr_b": dec.ocr_b,
                "mrz_value": dec.mrz_value,
            } for dec in d.decisions]
            on_step({
                "type": "doc",
                "index": i,
                "total": n,
                "doc_id": doc_id,
                "subject_did": d.subject_did[:52] + "…" if len(d.subject_did) > 52 else d.subject_did,
                "action": d.action,
                "ok": d.ok,
                "mrz_ok": d.mrz_ok,
                "reviewed_fields": d.reviewed_fields,
                "latency_ms": d.latency_ms,
                "truth": d.truth,
                "decisions": compact_decisions,
                "running_stats": {
                    "auto_accepted": stats["auto_accepted"],
                    "reviewed": stats["reviewed"],
                    "recapture": stats["recapture"],
                    "escapes": stats["escapes"],
                    "field_total": stats["field_total"],
                    "field_ok_raw": stats["field_ok_raw"],
                    "field_accepted": stats["field_accepted"],
                    "field_reviewed": stats["field_reviewed"],
                },
            })
            if pace_ms: time.sleep(pace_ms / 1000)
    return {"docs": docs, "credentials": creds, "stats": stats,
            "field_order": field_order}


def doc_to_json(d: DocResult, include_decisions: bool = True) -> dict:
    j = asdict(d)
    if not include_decisions:
        j["decisions"] = []
    return j


def cred_to_json(c: IssuedCredential) -> dict:
    return {
        "index": c.index,
        "doc_id": c.doc_id,
        "subject_did": c.subject_did,
        "epoch_id": c.epoch_id,
        "root_hex": "0x" + c.root_hex,
        "values": c.values,
        "field_order": c.field_order,
        "revoked": c.revoked,
    }
