# did-stack

A decentralized identity pipeline that deploys for approximately nothing.

Structurally it follows BDIMS (Le et al., *Computers* 2025), but with three of its
choices replaced — the three that make BDIMS expensive, fragile, and privacy-leaky.

## Run it

```bash
pip install pillow numpy opencv-python-headless

cd data && python generator.py --out ../datasets/train --n 20000 --seed 1
cd data && python generator.py --out ../datasets/eval  --n 1000 --seed 99 --holdout
cd eval && python harness.py
```

The eval split renders with fonts and background tints the training split never
sees, so the numbers measure generalisation, not template memorisation.

## What's here

| Path | What it does |
|---|---|
| `data/schema.py` | Field schema. Single source of truth for detector classes, validators, and Merkle leaf order. |
| `data/records.py` | Synthetic records + a real ICAO 9303 MRZ with genuine check digits. |
| `data/generator.py` | Renders the card, emits YOLO labels and ground-truth JSON. |
| `data/degrade.py` | Print-scan degradation: perspective, glare, shadow, blur, moire, sensor noise. |
| `extraction/validators/` | Deterministic validators: check digits, enum snapping, cross-field ordering. |
| `extraction/gate.py` | The confidence gate. Replaces the paper's paid human verifiers. |
| `issuer/merkle.py` | Salted commitments, selective disclosure, epoch batching. |
| `contracts/` | `RootRegistry` (one anchor per epoch), `StatusRegistry` (revocation bitstring). |
| `eval/harness.py` | End-to-end metrics, escape rate, crypto self-tests. |

## Three departures from BDIMS

**1. No paid human verifiers.** BDIMS routes every document segment to freelance
verifiers who sign it on-chain. That spends money on exactly the fields that
don't need it: a check digit is arithmetic, and arithmetic cannot be bribed,
cannot be phished, and does not need to be shown the holder's date of birth.
The gate stacks five uncorrelated signals — schema, dual-engine agreement,
recognizer confidence, MRZ check digits, cross-field consistency — and escalates
only what none of them can settle. On the harness that is ~12% of fields at the
paper's own reported OCR quality, and under 1% at a realistic trained one.

**2. Leaves commit to field values, not image segments.** An image hash proves
bytes, not identity. Re-encode the JPEG and every proof dies; keep the bytes and
swap the meaning and no proof notices. Every leaf is
`H(field_name ‖ canonical_value ‖ salt)` with a fresh 128-bit salt per field per
credential — without the salt, `sha256("sex=M")` is a two-guess brute force, so
an "undisclosed" field is not actually hidden, and the same field hash shown to
two verifiers lets them link the holder behind his back.

**3. Cost scales with time, not users.** Credential roots are folded into an
epoch tree; only the epoch root is anchored. One transaction per epoch serves any
number of credentials. Revocation is a StatusList bitstring published off-chain
with only its hash anchored — so withdrawing consent, the thing a user should be
able to do freely and instantly, costs the user nothing.

## Results

From `eval/harness.py`, driven by an OCR error model taken from the BDIMS
results table (97–98% character accuracy, 92% on the address, plus their reported
date-field confusion):

```
raw field accuracy 79.62%   single engine, no gate — what BDIMS anchors
auto-accepted      87.71%
sent to review     12.29%
ESCAPE RATE         0.0000%  wrong fields that were accepted anyway
```

**Escape rate is the number that matters.** A field that is wrong and correctly
flagged costs a reviewer ten seconds. A field that is wrong and *accepted* gets
Merkle-committed and anchored — permanently attested nonsense, with a valid
cryptographic proof standing behind it. Character accuracy of 97% sounds fine and
means that on a ten-field document, the odds all ten are simultaneously right are
poor; this is what the paper's headline metrics conceal.

### Sensitivity — this sets the training target

| Address CER | Auto-accept | Field review | Docs needing review | Escapes |
|---|---|---|---|---|
| 7.9% (BDIMS as reported) | 87.8% | 12.2% | 85.8% | 0 |
| 4.0% | 93.2% | 6.9% | 57.9% | 0 |
| 2.0% | 96.5% | 3.5% | 31.6% | 0 |
| 0.8% | 98.6% | 1.4% | 13.7% | 0 |
| 0.4% | 99.3% | 0.8% | 7.3% | 0 |

Escapes stay at zero at every quality level. **Model quality buys throughput, not
correctness** — which is the correct place for the safety property to live. It
also means the training target is unambiguous: address CER ≤ 0.8%, roughly 10×
better than off-the-shelf Tesseract, which is what an in-domain fine-tune on the
synthetic set is for.

## Privacy posture

No real identity documents, no real personal data, and no scraped images are used
anywhere in this repository. The card is fictional; the names, addresses and
countries are invented; the portrait is two grey ellipses. This is not a
compliance gesture — it is what makes the dataset shareable, the results
reproducible, and the training run legal to publish.

Nothing personal is ever written on-chain, encrypted or otherwise. An encrypted
blob on an immutable ledger is a breach with a delay fuse.

## Not done yet

- Detector + recognizer training (`models/`) — the harness drives a simulated
  OCR pair until the real ONNX engines land; every metric above keeps its meaning
  when they do.
- Presentation-attack detection (screen / print recapture).
- SD-JWT-VC wrapper over the Merkle commitment, for OpenID4VP interop.
- Optional Groth16 predicate circuit (`age ≥ 18`) and its verifier contract.
- Foundry deployment + gas report on Base Sepolia.
