"""End-to-end harness.

The detector and recognizer are not trained yet, so this harness drives the
pipeline with a *simulated* OCR pair whose error model is taken from the BDIMS
results table: ~97-98% character accuracy on most fields, 92% on the address,
plus the date-field confusion their confusion matrix reports. When the real
ONNX engines land, `fake_engines` is swapped for the real ones and every metric
below keeps its meaning.

The headline number is not accuracy. It is the ESCAPE RATE: fields that were
wrong AND were auto-accepted anyway. Those are the ones that reach the Merkle
root and get anchored — an escaped error is permanently attested nonsense.
A field that is wrong and correctly flagged for review costs a few seconds of
human time; a field that is wrong and accepted costs the credential's integrity.
"""

import random, string, sys, os

sys.path[:0] = [os.path.join(os.path.dirname(__file__), "..", d)
                for d in ("data", "extraction", "extraction/validators", "issuer")]

from schema import FIELDS, FIELD_BY_NAME             # noqa: E402
from records import make_record                      # noqa: E402
from gate import gate_document                       # noqa: E402
from validators import VALIDATORS                    # noqa: E402
import merkle                                        # noqa: E402

# Per-field character error rates, mirroring the BDIMS Table 2 profile.
CER = {"name": .015, "date_of_birth": .018, "sex": .022, "nationality": .030,
       "address": .079, "status": .033, "date_of_expiry": .037, "id_no": .026,
       "date_of_issue": .024, "period_of_stay": .029}


def corrupt(s, cer, rng):
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


def fake_engines(rec, rng, mrz_readable=True):
    """Two engines with partially shared failure modes — the realistic case.
    Engine B sees the same glare, so agreement alone is not sufficient."""
    a, b = {}, {}
    for f in FIELDS:
        truth = rec[f.name]
        ta, hit_a = corrupt(truth, CER[f.name], rng)
        # 35% of engine B's errors are correlated with engine A's (same glare,
        # same blur) — this is what makes naive dual-engine voting insufficient.
        tb = ta if (hit_a and rng.random() < 0.35) else corrupt(truth, CER[f.name] * 0.8, rng)[0]

        # The date confusion BDIMS reports: adjacent date fields swap.
        if f.name == "date_of_expiry" and rng.random() < 0.02:
            ta = tb = rec["date_of_issue"]

        conf = 0.995 if ta == truth else rng.uniform(0.62, 0.97)
        a[f.name] = (ta, conf)
        b[f.name] = tb
    mrz = rec["_mrz"] if (mrz_readable and rng.random() < 0.94) else None
    return a, b, mrz


def truth_canonical(rec):
    out = {}
    for f in FIELDS:
        ok, v, _ = VALIDATORS[f.validator](rec[f.name])
        out[f.name] = v
    return out


def run(n=2000, seed=7):
    rng = random.Random(seed)
    tot = acc = esc = rev = raw_ok = 0
    docs_clean = docs_review = 0
    per_field_escape = {f.name: 0 for f in FIELDS}
    first_cred = None

    for _ in range(n):
        rec = make_record(rng)
        truth = truth_canonical(rec)
        a, b, mrz = fake_engines(rec, rng)

        # raw accuracy, before any gating
        for f in FIELDS:
            ok, v, _ = VALIDATORS[f.validator](a[f.name][0])
            raw_ok += int(ok and v == truth[f.name])

        # A VLM adjudicator on a single cropped field: ~97% field-accurate,
        # and its errors are uncorrelated with the OCR engines' glare artefacts.
        def adjudicate(name, _rec=rec, _rng=rng):
            if _rng.random() < 0.97:
                return _rec[name]
            return corrupt(_rec[name], 0.05, _rng)[0]

        res = gate_document(a, b, mrz, adjudicate=adjudicate)
        for name, r in res["fields"].items():
            tot += 1
            correct = r.value == truth[name]
            if r.accepted:
                acc += 1
                if not correct:
                    esc += 1
                    per_field_escape[name] += 1
            else:
                rev += 1
        docs_clean += int(res["ok"])
        docs_review += int(not res["ok"])

        if first_cred is None and res["ok"]:
            first_cred = (rec, {k: v.value for k, v in res["fields"].items()})

    print(f"documents          {n}")
    print(f"raw field accuracy {raw_ok / tot:6.2%}   (single engine, no gate — what BDIMS anchors)")
    print(f"auto-accepted      {acc / tot:6.2%}")
    print(f"sent to review     {rev / tot:6.2%}   ({rev} of {tot} fields)")
    print(f"ESCAPE RATE        {esc / tot:6.4%}   ({esc} wrong fields accepted)")
    print(f"post-gate accuracy {(acc - esc) / acc:6.4%}   of what was accepted")
    print(f"docs clean         {docs_clean / n:6.2%}  |  docs needing review {docs_review / n:6.2%}")
    if esc:
        worst = sorted(per_field_escape.items(), key=lambda kv: -kv[1])[:3]
        print("escapes by field  ", ", ".join(f"{k}={v}" for k, v in worst if v))
    return first_cred


def crypto_demo(rec, values):
    order = [f.name for f in FIELDS]
    creds = [merkle.issue("did:pkh:eip155:8453:0xA11ce", "did:web:issuer.example",
                          values, order)]
    for _ in range(999):  # 999 other credentials issued in the same epoch
        r = make_record(random.Random())
        creds.append(merkle.issue("did:pkh:...", "did:web:issuer.example",
                                  truth_canonical(r), order))

    epoch_root = merkle.anchor_epoch(creds, epoch_id=42)
    print(f"\nepoch anchored     1 transaction for {len(creds)} credentials")
    print(f"epoch root         0x{epoch_root.hex()[:32]}…  (32 bytes on-chain, nothing else)")

    pres = creds[0].disclose(["date_of_birth", "nationality"])
    ok, revealed, why = merkle.verify_presentation(pres, epoch_root)
    size = len(str(pres))
    print(f"presentation       reveals {list(revealed)} — {size} bytes, verifier says {ok} ({why})")
    hidden = [f.name for f in FIELDS if f.name not in revealed]
    print(f"withheld           {len(hidden)} fields incl. {hidden[:3]} — no hash, no ciphertext, nothing")

    tampered = creds[0].disclose(["date_of_birth"])
    tampered["disclosures"][0]["value"] = "1990-01-01"
    ok2, _, why2 = merkle.verify_presentation(tampered, epoch_root)
    print(f"tamper test        holder edits date_of_birth → verifier says {ok2} ({why2})")

    ok3, _, why3 = merkle.verify_presentation(pres, epoch_root, revoked=True)
    print(f"revocation test    status bit set → verifier says {ok3} ({why3})")


if __name__ == "__main__":
    rec, values = run()
    crypto_demo(rec, values)
