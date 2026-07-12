"""The confidence gate.

A field is auto-accepted only if every independent signal agrees. The signals
are cheap and uncorrelated, which is the whole point — two OCR engines can share
a failure mode, but neither of them can talk a check digit into being right.

  1. schema      the value passes its deterministic validator
  2. agreement   engine A and engine B produce the same canonical value
  3. confidence  the recognizer's own score clears the threshold
  4. mrz         where the field is in the MRZ, the MRZ (which check-digits
                 itself) agrees with the visual field
  5. consistency the cross-field date/ordering rules hold

MRZ evidence outranks recognizer confidence: a check digit is arithmetic, a
softmax is a hope. Fields that fail any signal go to a review queue rather than
to a crowd of paid strangers.
"""

from dataclasses import dataclass
from typing import Optional

from validators import VALIDATORS, parse_mrz, cross_field, canon, loose_eq
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "data"))
from schema import FIELDS, FIELD_BY_NAME  # noqa: E402

CONF_MIN = 0.90
# Fields with no independent oracle (no check digit, no closed vocabulary) get a
# higher bar and a third opinion. Two OCR engines can share a glare artefact;
# they cannot share a check digit that does not exist.
CONF_MIN_NO_ORACLE = 0.98
NO_ORACLE = {"address", "date_of_issue"}

# The MRZ can CONFIRM any field it carries, but it can only REPAIR fields whose
# alphabet it fully represents. It substitutes '<' for hyphens and apostrophes,
# so repairing a name from the MRZ turns AL-RASHID into AL RASHID — a wrong
# value with a valid check digit behind it, which is the worst possible outcome.
# Structured fields (dates, sex, id, country code) survive the round trip; free
# text does not.
MRZ_REPAIRABLE = {"date_of_birth", "sex", "date_of_expiry", "id_no", "nationality"}


@dataclass
class FieldResult:
    name: str
    value: Optional[str]
    accepted: bool
    confidence: float
    signals: dict
    reason: str = ""


def gate_field(name, cand_a, conf_a, cand_b, mrz_fields, mrz_ok, adjudicate=None):
    fld = FIELD_BY_NAME[name]
    ok_schema, value, why = VALIDATORS[fld.validator](cand_a or "")
    _, value_b, _ = VALIDATORS[fld.validator](cand_b or "")

    agree = bool(value) and value == value_b
    conf_ok = conf_a >= CONF_MIN

    mrz_sig = None
    if fld.in_mrz and mrz_ok and mrz_fields.get(name):
        m = mrz_fields[name]
        if loose_eq(m, value):
            # Same string modulo punctuation the MRZ cannot carry. The visual
            # field is the richer of the two — keep it, and count the MRZ as
            # confirming it.
            mrz_sig = True
        elif name in MRZ_REPAIRABLE:
            # The MRZ check-digits itself. When it disagrees, it wins.
            value, mrz_sig, why = str(m), True, "repaired from MRZ"
        else:
            # Confirmable but not repairable: the MRZ proves OCR is wrong
            # without being able to say what is right. Escalate.
            mrz_sig, why = False, "MRZ contradicts OCR but cannot restore punctuation"

    signals = {"schema": ok_schema, "agreement": agree, "confidence": conf_ok, "mrz": mrz_sig}

    if not ok_schema:
        return FieldResult(name, value, False, conf_a, signals, why or "failed schema")
    if mrz_sig is True:
        return FieldResult(name, value, True, conf_a, signals, why)

    # A field's tier is decided at runtime, not statically. `date_of_birth` has
    # an oracle only when the MRZ actually parsed — on a glare-blown MRZ it is
    # just as unverifiable as the address, and must be treated that way.
    has_oracle = (mrz_sig is True) or fld.validator.startswith("enum")
    thresh = CONF_MIN if (has_oracle and name not in NO_ORACLE) else CONF_MIN_NO_ORACLE
    if agree and conf_a >= thresh:
        return FieldResult(name, value, True, conf_a, signals, why)

    # Third opinion, on the ~5% of fields that reach here. This is the only
    # place a VLM is called, and it sees one cropped field — not the document.
    if adjudicate is not None:
        v = adjudicate(name)
        if v is not None:
            ok_v, cv, _ = VALIDATORS[fld.validator](v)
            if ok_v and (cv == value or cv == value_b):
                signals["adjudicator"] = True
                return FieldResult(name, cv, True, conf_a, signals, "adjudicated")

    return FieldResult(name, value, False, conf_a, signals,
                       "engines disagree" if not agree else "low confidence")


def gate_document(engine_a, engine_b, mrz_lines, adjudicate=None):
    """engine_a: {field: (text, confidence)}   engine_b: {field: text}"""
    mrz_ok, mrz_fields, mrz_errs = parse_mrz(mrz_lines) if mrz_lines else (False, {}, ["no MRZ"])

    results = {}
    for fld in FIELDS:
        ta, ca = engine_a.get(fld.name, ("", 0.0))
        tb = engine_b.get(fld.name, "")
        results[fld.name] = gate_field(fld.name, ta, ca, tb, mrz_fields, mrz_ok, adjudicate)

    accepted = {n: r.value for n, r in results.items() if r.accepted}
    xf_errs = cross_field(accepted) if len(accepted) == len(FIELDS) else []
    doc_ok = all(r.accepted for r in results.values()) and not xf_errs

    return {
        "ok": doc_ok,
        # Doc-level policy: a document whose MRZ will not parse should be
        # recaptured, not manually transcribed field by field. Recapture is
        # free; a reviewer's attention is not.
        "action": "issue" if doc_ok else ("recapture" if not mrz_ok else "review"),
        "fields": results,
        "mrz_ok": mrz_ok,
        "mrz_errors": mrz_errs,
        "cross_field_errors": xf_errs,
        "review": [n for n, r in results.items() if not r.accepted] or (
            ["_document"] if xf_errs else []),
    }
