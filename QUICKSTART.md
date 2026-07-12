# Run the demo

## One command

```bash
pip install fastapi uvicorn pydantic python-dotenv web3
python -m uvicorn app.server:app --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000** in a browser.

## What you'll see

Five scenes, in order. Each has a button that triggers the real pipeline.

1. **Escape rate.** Click *Run 2,000-document simulation*. Four animated bars
   fill in: raw single-engine accuracy (~77%), auto-accepted (~86%), routed to
   review (~14%), and the escape rate (0.0000%).
2. **Anchor.** Click *Issue 1,000 & anchor*. An epoch tree animates from 1,000
   leaves up into a single root, then a receipt appears (tx hash, gas, cost).
   The sample credential card populates on the right.
3. **Reveal.** Check one field (e.g. *date of birth*), click *Present to
   verifier*. The verifier panel shows only the revealed field; the rest read
   *withheld*. Merkle verification against the anchored root shows green.
4. **Attack.** *Change DOB to 1990-01-01 & verify* — the panel goes red with
   `Merkle proof failed`. *Revoke this credential* publishes a new StatusList
   hash and the verifier re-check goes red with `credential is revoked`.
5. **Agent.** *Ask the VLM* runs Gemini as the third-opinion adjudicator on
   two disagreeing OCR strings. *Generate* writes a compliance audit report
   from live state. The chat assistant answers questions grounded in the
   current state and the README.

## Modes

Two switches in `.env` (copy from `.env.example`):

- `USE_LIVE_CHAIN=0` → fixture mode. Uses deterministic tx hashes and gas
  numbers; no wallet needed. The Merkle math is still real; only the on-chain
  bytes are simulated.
- `USE_LIVE_CHAIN=1` → live Base Sepolia. Requires a funded wallet, deployed
  contracts (see [DEPLOY.md](DEPLOY.md)), and roughly 0.05 ETH from the faucet.

- `GEMINI_API_KEY=…` → enables the four agent features (adjudicator, audit
  report, chat, per-review explainer). Unset it and the UI falls back to
  deterministic templates for report/adjudicator; chat hides itself gracefully.

## Verifying the pipeline without the UI

```bash
python harness.py
```

Prints the escape-rate table and a self-narrating crypto demo (epoch anchor,
selective disclosure, tamper test, revocation test).

## Structure

```
schema.py, records.py, validators.py   # field definitions, synthetic data, deterministic validators
gate.py                                 # 5-signal confidence gate (unchanged)
merkle.py                               # salted Merkle trees, epoch batching (unchanged)
harness.py                              # end-to-end escape-rate simulator (unchanged)
issuer/anchor_client.py                 # web3.py bridge (live) + fixture-mode fallback
agent/gemini.py                         # thin Gemini 2.5 Flash client, header-auth
agent/adjudicator.py                    # third-opinion VLM (gate.py's adjudicate slot)
agent/reporter.py                       # audit report generator
agent/chat.py                           # grounded chat assistant
agent/explainer.py                      # one-line human explanations for review reasons
app/server.py                           # FastAPI backend, 9 endpoints
app/static/                             # Apple-themed HTML/CSS/JS UI
contracts/                              # Foundry project: src/, script/Deploy.s.sol, foundry.toml
```
