"""FastAPI backend for the FinFabric console.

Serves a multi-view web app plus JSON + SSE endpoints exposing the real
per-doc, per-field, per-signal state of the issuance pipeline. Every drill-
down view reads from this in-memory model; the on-chain calls are made
through issuer/anchor_client.py (which switches between fixture and live)."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import queue
import random
import sys
import threading
import time
from contextlib import redirect_stdout
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "issuer"))

import harness           # noqa: E402
import merkle            # noqa: E402
from schema import FIELDS, FIELD_BY_NAME   # noqa: E402
from records import make_record            # noqa: E402
from issuer.anchor_client import (         # noqa: E402
    anchor_epoch_onchain, publish_status, read_root_onchain, _live,
)
from agent import gemini as agent_gemini   # noqa: E402
from agent import llm as agent_llm         # noqa: E402
from agent import chat as agent_chat       # noqa: E402
from agent import reporter as agent_report # noqa: E402
from agent import explainer as agent_explain # noqa: E402
from agent import adjudicator as agent_adj # noqa: E402
from agent import copilot as agent_copilot # noqa: E402
from app import issuance                   # noqa: E402
from app import workflow as wfx            # noqa: E402
from app import db as app_db               # noqa: E402
from app import pdf_report                 # noqa: E402
from app.capabilities import capability_descriptions  # noqa: E402
import csv as csv_mod                      # noqa: E402
import io as io_mod                        # noqa: E402
import uuid                                # noqa: E402

app = FastAPI(title="FinFabric console")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

STATIC = REPO / "app" / "static"
FIXTURES = REPO / "fixtures"

# ---- in-memory model -----------------------------------------------------

class Epoch:
    def __init__(self, epoch_id, docs, creds, stats, receipt, anchored_at, seed):
        self.epoch_id = epoch_id
        self.docs = docs            # list[DocResult]
        self.creds = creds          # list[IssuedCredential]
        self.stats = stats
        self.receipt = receipt
        self.anchored_at = anchored_at
        self.seed = seed
        self.status_version = 1
        self.status_history = []    # list[receipt]
        self.reviewed_docs = [d for d in docs if d.action != "issue"]


_state = {
    "epochs": {},                # epoch_id -> Epoch
    "epoch_order": [],           # newest first
    "chain_txs": [],             # list[receipt] across all types, timeline
    "revocations": [],           # list[{epoch_id, cred_indices, receipt}]
    "field_order": [f.name for f in FIELDS],
    "startup_at": time.time(),
    "harness_result": None,
    "workflow_runs": {},         # run_id -> bundle (for PDF download)
}

_lock = threading.Lock()


def _next_epoch_id() -> int:
    return int(time.time())


def _bootstrap():
    """Seed 2 historical epochs so the console has real data on first load."""
    for offset_secs, seed, count in [(3600 * 6, 21, 60), (1800, 33, 80)]:
        report = issuance.run_batch(count, seed=seed)
        creds = report["credentials"]
        docs = report["docs"]
        stats = report["stats"]
        if not creds:
            continue
        epoch_id = int(time.time()) - offset_secs
        root = merkle.anchor_epoch([c._merkle_cred for c in creds], epoch_id=epoch_id)
        for c in creds:
            c.epoch_id = epoch_id
        receipt = anchor_epoch_onchain(epoch_id, root, len(creds))
        receipt["anchored_at"] = int(time.time()) - offset_secs
        ep = Epoch(epoch_id, docs, creds, stats, receipt,
                   anchored_at=receipt["anchored_at"], seed=seed)
        _state["epochs"][epoch_id] = ep
        _state["epoch_order"].insert(0, epoch_id)
        _state["chain_txs"].insert(0, {"kind": "anchor", **receipt})

    # Revoke one credential in the older epoch to seed the timeline.
    if _state["epoch_order"]:
        old = _state["epoch_order"][-1]
        ep = _state["epochs"][old]
        if ep.creds:
            ep.creds[0].revoked = True
            list_hash, uri, receipt = _publish_status_for_epoch(ep)
            _state["revocations"].append({
                "epoch_id": old, "cred_indices": [0],
                "list_hash_hex": "0x" + list_hash.hex(), "uri": uri,
                "receipt": receipt, "version": ep.status_version,
            })
            _state["chain_txs"].insert(0, {"kind": "status", **receipt})


def _publish_status_for_epoch(ep: Epoch):
    n = len(ep.creds)
    bs = bytearray((n + 7) // 8)
    for i, c in enumerate(ep.creds):
        if c.revoked:
            bs[i // 8] |= 1 << (7 - (i % 8))
    list_hash = hashlib.sha256(bytes(bs)).digest()
    ep.status_version += 1
    uri = f"ipfs://demo/finfabric/epoch-{ep.epoch_id}-v{ep.status_version}.gz"
    receipt = publish_status(uri, list_hash, ep.status_version)
    receipt["anchored_at"] = int(time.time())
    ep.status_history.append(receipt)
    return list_hash, uri, receipt


# ---- request models ------------------------------------------------------

class IssueRunRequest(BaseModel):
    n: int = 60
    seed: Optional[int] = None
    anchor: bool = True
    scenario: Optional[str] = "baseline"
    # Optional overrides (any of these override the preset)
    cer_multiplier: Optional[float] = None
    address_cer: Optional[float] = None
    mrz_readable_rate: Optional[float] = None
    date_swap_rate: Optional[float] = None
    engine_b_correlation: Optional[float] = None




def _resolve_scenario(name: Optional[str], **overrides) -> issuance.Scenario:
    base = issuance.SCENARIOS.get(name or "baseline", issuance.Scenario())
    # Copy so we don't mutate the preset in memory.
    scn = issuance.Scenario(
        cer_multiplier=base.cer_multiplier,
        cer_overrides=dict(base.cer_overrides),
        mrz_readable_rate=base.mrz_readable_rate,
        date_swap_rate=base.date_swap_rate,
        engine_b_correlation=base.engine_b_correlation,
    )
    if overrides.get("cer_multiplier") is not None:
        scn.cer_multiplier = overrides["cer_multiplier"]
    if overrides.get("address_cer") is not None:
        scn.cer_overrides["address"] = overrides["address_cer"]
    if overrides.get("mrz_readable_rate") is not None:
        scn.mrz_readable_rate = overrides["mrz_readable_rate"]
    if overrides.get("date_swap_rate") is not None:
        scn.date_swap_rate = overrides["date_swap_rate"]
    if overrides.get("engine_b_correlation") is not None:
        scn.engine_b_correlation = overrides["engine_b_correlation"]
    return scn


class DiscloseRequest(BaseModel):
    epoch_id: int
    credential_index: int
    reveal: list[str]
    tamper_field: Optional[str] = None
    tamper_value: Optional[str] = None


class RevokeRequest(BaseModel):
    epoch_id: int
    credential_index: int


class UnrevokeRequest(BaseModel):
    epoch_id: int
    credential_index: int


class ChatRequest(BaseModel):
    message: str
    history: list[dict] = []


class ExplainRequest(BaseModel):
    field: str
    cand_a: str
    cand_b: str
    conf: float = 0.75
    signals: dict = {}
    reason: str = ""


class AdjudicateRequest(BaseModel):
    field: str
    cand_a: str
    cand_b: str
    conf_a: float = 0.75
    mrz_hint: Optional[str] = None
    validator: str = "text"


class CopilotRequest(BaseModel):
    description: str


class CopilotRefineRequest(BaseModel):
    workflow: dict
    change_request: str


class WorkflowSaveRequest(BaseModel):
    name: str
    description: str = ""
    config: dict


class WorkflowRunRequest(BaseModel):
    workflow_id: Optional[int] = None
    config: Optional[dict] = None
    record: Optional[dict] = None
    kind: str = "retail"  # for synth record when `record` is omitted
    pace_ms: int = 220


# ---- health & meta -------------------------------------------------------

@app.get("/api/health")
def health():
    llm_info = agent_llm.describe()
    return {
        "ok": True,
        "mode": "live" if _live() else "fixture",
        "llm": llm_info,
        # Backward-compat: some old UI reads gemini_enabled/gemini_model.
        "gemini_enabled": llm_info["gemini_enabled"] or llm_info["openai_enabled"],
        "gemini_model": llm_info.get("openai_model") or llm_info.get("gemini_model"),
        "db": app_db.describe(),
        "fields": [{"name": f.name, "label": f.label, "in_mrz": f.in_mrz,
                    "validator": f.validator} for f in FIELDS],
        "startup_at": _state["startup_at"],
    }


@app.get("/api/dashboard")
def dashboard():
    epochs = [_epoch_summary(_state["epochs"][eid]) for eid in _state["epoch_order"]]
    total_creds = sum(len(e.creds) for e in _state["epochs"].values())
    total_reviewed = sum(len(e.reviewed_docs) for e in _state["epochs"].values())
    total_revoked = sum(1 for e in _state["epochs"].values() for c in e.creds if c.revoked)
    # Weighted escape rate
    total_escapes = sum(e.stats.get("escapes", 0) for e in _state["epochs"].values())
    total_fields = sum(e.stats.get("field_total", 0) for e in _state["epochs"].values())
    total_cost = sum((e.receipt.get("cost_usd") or 0) for e in _state["epochs"].values())
    return {
        "epochs": epochs,
        "totals": {
            "epochs": len(epochs),
            "credentials": total_creds,
            "reviewed": total_reviewed,
            "revoked": total_revoked,
            "escape_rate": (total_escapes / total_fields) if total_fields else 0,
            "cost_usd": total_cost,
            "field_total": total_fields,
        },
        "chain_txs": _state["chain_txs"][:20],
        "revocations": _state["revocations"][-10:],
    }


def _epoch_summary(ep: Epoch) -> dict:
    return {
        "epoch_id": ep.epoch_id,
        "anchored_at": ep.anchored_at,
        "seed": ep.seed,
        "credential_count": len(ep.creds),
        "reviewed_count": len(ep.reviewed_docs),
        "recapture_count": sum(1 for d in ep.docs if d.action == "recapture"),
        "docs_total": len(ep.docs),
        "revoked_count": sum(1 for c in ep.creds if c.revoked),
        "escape_rate": (ep.stats["escapes"] / ep.stats["field_total"]) if ep.stats["field_total"] else 0,
        "auto_accept_rate": (ep.stats["field_accepted"] / ep.stats["field_total"]) if ep.stats["field_total"] else 0,
        "review_rate": (ep.stats["field_reviewed"] / ep.stats["field_total"]) if ep.stats["field_total"] else 0,
        "raw_accuracy": (ep.stats["field_ok_raw"] / ep.stats["field_total"]) if ep.stats["field_total"] else 0,
        "root_hex": ep.receipt.get("epoch_root_hex"),
        "tx_hash": ep.receipt.get("tx_hash"),
        "basescan_url": ep.receipt.get("basescan_url"),
        "gas_used": ep.receipt.get("gas_used"),
        "cost_usd": ep.receipt.get("cost_usd"),
        "status_version": ep.status_version,
    }


# ---- issuance run (streaming) --------------------------------------------

@app.get("/api/scenarios")
def list_scenarios():
    """Preset scenarios judges can pick from."""
    presets = {
        "baseline": {
            "name": "New account opening — baseline",
            "desc": "Typical branch onboarding with off-the-shelf OCR on scanned KYC docs. Address is the dominant reviewer cost (~7.9% CER) — the RBI's usual acceptable band.",
            "expected": "~14% of applications route to a KYC officer; 0% mis-issued KYC (escape rate).",
        },
        "fine_tuned": {
            "name": "Fine-tuned OCR — in-branch model",
            "desc": "Bank-trained OCR on Indian KYC documents brings address CER down to 0.8% — the sensitivity-table target. Throughput multiplies without weakening any downstream check.",
            "expected": "~4% to a KYC officer; 10× the throughput of baseline; 0% escape.",
        },
        "date_swap_adversarial": {
            "name": "Adversarial — expiry-date forgery",
            "desc": "Attacker submits KYC docs that trigger the adjacent-date confusion at 20% rate, attempting to extend validity past expiry. MRZ check digits are the only oracle that can catch this.",
            "expected": "MRZ rescues every attempted forgery; escape rate stays at 0%.",
        },
        "mrz_blown_out": {
            "name": "Poor photo quality — MRZ unreadable",
            "desc": "Customer's phone photo has glare on the MRZ zone; 60% of docs cannot self-check. The gate correctly routes them to re-capture (customer's device) instead of a manual reviewer.",
            "expected": "Higher recapture rate; low reviewer load. Cost lands on the app, not the branch.",
        },
        "correlated_engines": {
            "name": "Correlated OCR failures",
            "desc": "Both OCR engines share 85% of failure modes — same phone image, same glare. Naive dual-engine voting fails; the gate holds because it also stacks schema, MRZ and cross-field consistency.",
            "expected": "Review rate rises as agreement loses power. Escape rate stays at 0%.",
        },
        "high_volume": {
            "name": "Batch onboarding — 200 customers",
            "desc": "A fintech partner submits an overnight batch. Demonstrates that on-chain cost is constant in customer count — one anchor tx settles the whole batch.",
            "expected": "One Base tx (~$0.01) anchors 200 credentials. Marginal cost per customer approaches zero.",
        },
    }
    return [{"key": k, **v,
             "params": {
                 "cer_multiplier": issuance.SCENARIOS[k].cer_multiplier,
                 "address_cer": issuance.SCENARIOS[k].cer_overrides.get("address", issuance._DEFAULT_CER["address"]),
                 "mrz_readable_rate": issuance.SCENARIOS[k].mrz_readable_rate,
                 "date_swap_rate": issuance.SCENARIOS[k].date_swap_rate,
                 "engine_b_correlation": issuance.SCENARIOS[k].engine_b_correlation,
             },
             "suggested_n": 200 if k == "high_volume" else 60}
            for k, v in presets.items()]


@app.post("/api/issue/run")
def issue_run(req: IssueRunRequest):
    """Non-streaming issuance run for programmatic callers; the UI uses
    the SSE version below for live feedback."""
    seed = req.seed if req.seed is not None else int(time.time()) & 0xFFFF
    scn = _resolve_scenario(req.scenario,
                            cer_multiplier=req.cer_multiplier,
                            address_cer=req.address_cer,
                            mrz_readable_rate=req.mrz_readable_rate,
                            date_swap_rate=req.date_swap_rate,
                            engine_b_correlation=req.engine_b_correlation)
    report = issuance.run_batch(req.n, seed=seed, scenario=scn)
    epoch_id, receipt = _anchor_batch(report) if req.anchor and report["credentials"] else (None, None)
    return {
        "seed": seed,
        "epoch_id": epoch_id,
        "receipt": receipt,
        "stats": report["stats"],
        "issued": len(report["credentials"]),
        "reviewed": sum(1 for d in report["docs"] if d.action != "issue"),
    }


@app.get("/api/issue/stream")
async def issue_stream(
    n: int = 30, seed: Optional[int] = None, anchor: bool = True,
    scenario: Optional[str] = "baseline",
    cer_multiplier: Optional[float] = None,
    address_cer: Optional[float] = None,
    mrz_readable_rate: Optional[float] = None,
    date_swap_rate: Optional[float] = None,
    engine_b_correlation: Optional[float] = None,
    pace_ms: int = 120,
):
    """Server-Sent Events: emits stage + doc events per document, then a
    final 'anchor' event with the receipt (if anchor=true). `pace_ms`
    inserts a delay between stages so a human can follow the pipeline."""
    seed = seed if seed is not None else int(time.time()) & 0xFFFF
    scn = _resolve_scenario(scenario,
                            cer_multiplier=cer_multiplier,
                            address_cer=address_cer,
                            mrz_readable_rate=mrz_readable_rate,
                            date_swap_rate=date_swap_rate,
                            engine_b_correlation=engine_b_correlation)

    q: queue.Queue = queue.Queue()

    def worker():
        try:
            report = issuance.run_batch(n, seed=seed, on_step=q.put,
                                        scenario=scn, pace_ms=pace_ms)
            if anchor and report["credentials"]:
                epoch_id, receipt = _anchor_batch(report)
                q.put({"type": "anchor", "epoch_id": epoch_id, "receipt": receipt})
            q.put({"type": "done", "stats": report["stats"]})
        except Exception as e:
            q.put({"type": "error", "message": str(e)})
        finally:
            q.put(None)

    threading.Thread(target=worker, daemon=True).start()

    async def gen():
        loop = asyncio.get_event_loop()
        while True:
            evt = await loop.run_in_executor(None, q.get)
            if evt is None:
                break
            yield f"data: {json.dumps(evt)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


def _anchor_batch(report) -> tuple[int, dict]:
    creds = report["credentials"]
    docs = report["docs"]
    stats = report["stats"]
    epoch_id = _next_epoch_id()
    root = merkle.anchor_epoch([c._merkle_cred for c in creds], epoch_id=epoch_id)
    for c in creds:
        c.epoch_id = epoch_id
    receipt = anchor_epoch_onchain(epoch_id, root, len(creds))
    receipt["anchored_at"] = int(time.time())
    with _lock:
        ep = Epoch(epoch_id, docs, creds, stats, receipt,
                   anchored_at=receipt["anchored_at"], seed=0)
        _state["epochs"][epoch_id] = ep
        _state["epoch_order"].insert(0, epoch_id)
        _state["chain_txs"].insert(0, {"kind": "anchor", **receipt})
    return epoch_id, receipt


# ---- epoch & credentials --------------------------------------------------

@app.get("/api/epochs")
def list_epochs():
    return [_epoch_summary(_state["epochs"][eid]) for eid in _state["epoch_order"]]


@app.get("/api/epochs/{epoch_id}")
def get_epoch(epoch_id: int):
    ep = _state["epochs"].get(epoch_id)
    if not ep:
        raise HTTPException(404, "unknown epoch")
    return {
        "summary": _epoch_summary(ep),
        "field_escapes": ep.stats.get("per_field_escapes", {}),
        "review_reasons": _review_reason_histogram(ep),
        "status_history": ep.status_history,
    }


def _review_reason_histogram(ep: Epoch) -> dict:
    hist: dict[str, int] = {}
    for d in ep.docs:
        for dec in d.decisions:
            if not dec.accepted:
                hist[dec.reason or "unknown"] = hist.get(dec.reason or "unknown", 0) + 1
    return dict(sorted(hist.items(), key=lambda kv: -kv[1])[:10])


@app.get("/api/epochs/{epoch_id}/credentials")
def epoch_credentials(epoch_id: int, offset: int = 0, limit: int = 50,
                      status: str = "all", q: str = ""):
    ep = _state["epochs"].get(epoch_id)
    if not ep:
        raise HTTPException(404, "unknown epoch")
    items = ep.creds
    if status == "active":
        items = [c for c in items if not c.revoked]
    elif status == "revoked":
        items = [c for c in items if c.revoked]
    if q:
        ql = q.lower()
        items = [c for c in items if ql in c.doc_id.lower()
                 or ql in c.subject_did.lower()
                 or any(ql in str(v).lower() for v in c.values.values())]
    total = len(items)
    slice_ = items[offset:offset + limit]
    return {"total": total, "offset": offset, "limit": limit,
            "items": [issuance.cred_to_json(c) for c in slice_]}


@app.get("/api/epochs/{epoch_id}/reviews")
def epoch_reviews(epoch_id: int, offset: int = 0, limit: int = 50):
    ep = _state["epochs"].get(epoch_id)
    if not ep:
        raise HTTPException(404, "unknown epoch")
    items = ep.reviewed_docs
    return {
        "total": len(items),
        "offset": offset, "limit": limit,
        "items": [{
            "doc_id": d.doc_id,
            "subject_did": d.subject_did,
            "action": d.action,
            "reviewed_fields": d.reviewed_fields,
            "mrz_ok": d.mrz_ok,
            "cross_field_errors": d.cross_field_errors,
            "latency_ms": d.latency_ms,
            "reasons": {dec.field: dec.reason for dec in d.decisions if not dec.accepted},
        } for d in items[offset:offset + limit]],
    }


@app.get("/api/credentials/{epoch_id}/{index}")
def credential_detail(epoch_id: int, index: int):
    ep = _state["epochs"].get(epoch_id)
    if not ep:
        raise HTTPException(404, "unknown epoch")
    if index < 0 or index >= len(ep.creds):
        raise HTTPException(404, "credential out of range")
    cred = ep.creds[index]
    doc = next(d for d in ep.docs if d.doc_id == cred.doc_id)
    return {
        "epoch": _epoch_summary(ep),
        "credential": issuance.cred_to_json(cred),
        "doc": issuance.doc_to_json(doc),
        "salts_hex": cred.salts_hex,
    }


@app.get("/api/credentials/{epoch_id}/{index}/proof/{field}")
def credential_proof(epoch_id: int, index: int, field: str):
    """Return the full Merkle proof steps for one field: the leaf pre-image,
    the leaf hash, each sibling on the way up to the credential root, and
    the epoch proof up to the anchored epoch root."""
    ep = _state["epochs"].get(epoch_id)
    if not ep:
        raise HTTPException(404, "unknown epoch")
    if index < 0 or index >= len(ep.creds):
        raise HTTPException(404, "credential out of range")
    ic = ep.creds[index]
    mcred = ic._merkle_cred
    if field not in mcred.order:
        raise HTTPException(404, "unknown field")

    leaves = [merkle.leaf_hash(n, mcred.values[n], mcred.salts[n]) for n in mcred.order]
    _, levels = merkle.build_tree(leaves)
    i = mcred.order.index(field)
    field_path = merkle.make_proof(levels, i)

    # Reconstruct the walk up so we can show intermediate hashes.
    walk = [{"level": 0, "index": i, "hash": leaves[i].hex(), "role": "leaf"}]
    cur_hash = leaves[i]
    idx = i
    for lvl_no, (sib_hash, sib_is_left) in enumerate(field_path, start=1):
        parent = merkle.node_hash(sib_hash, cur_hash) if sib_is_left else merkle.node_hash(cur_hash, sib_hash)
        walk.append({
            "level": lvl_no,
            "sibling": sib_hash.hex(),
            "sibling_is_left": bool(sib_is_left),
            "hash": parent.hex(),
        })
        cur_hash = parent

    epoch_root_hex = ep.receipt.get("epoch_root_hex", "0x")
    return {
        "epoch_id": ep.epoch_id,
        "field": field,
        "leaf_preimage": {
            "field_name": field,
            "value": mcred.values[field],
            "salt_hex": mcred.salts[field].hex(),
        },
        "credential_root": mcred.root.hex(),
        "epoch_root": epoch_root_hex.removeprefix("0x"),
        "credential_walk": walk,
        "epoch_walk": [{"sibling": h.hex(), "sibling_is_left": bool(l)}
                       for h, l in mcred.epoch_proof],
    }


# ---- disclose / tamper / revoke ------------------------------------------

@app.post("/api/disclose")
def disclose(req: DiscloseRequest):
    ep = _state["epochs"].get(req.epoch_id)
    if not ep:
        raise HTTPException(404, "unknown epoch")
    if req.credential_index < 0 or req.credential_index >= len(ep.creds):
        raise HTTPException(404, "credential out of range")
    if not req.reveal:
        raise HTTPException(400, "reveal must be non-empty")

    ic = ep.creds[req.credential_index]
    mcred = ic._merkle_cred
    pres = mcred.disclose(req.reveal)

    if req.tamper_field and req.tamper_value is not None:
        for d in pres["disclosures"]:
            if d["name"] == req.tamper_field:
                d["value"] = req.tamper_value

    epoch_root = bytes.fromhex(ep.receipt["epoch_root_hex"].removeprefix("0x"))
    ok, revealed, why = merkle.verify_presentation(pres, epoch_root, revoked=ic.revoked)

    return {
        "verified": ok,
        "reason": why,
        "presentation": pres,
        "presentation_bytes": len(json.dumps(pres)),
        "revealed": revealed,
        "on_chain_root": ep.receipt["epoch_root_hex"],
        "revoked": ic.revoked,
        "withheld_field_count": len(mcred.order) - len(req.reveal),
        "epoch_id": ep.epoch_id,
        "credential_index": req.credential_index,
    }


@app.post("/api/revoke")
def revoke(req: RevokeRequest):
    ep = _state["epochs"].get(req.epoch_id)
    if not ep:
        raise HTTPException(404, "unknown epoch")
    if req.credential_index < 0 or req.credential_index >= len(ep.creds):
        raise HTTPException(404, "credential out of range")
    ep.creds[req.credential_index].revoked = True
    lh, uri, receipt = _publish_status_for_epoch(ep)
    _state["revocations"].append({
        "epoch_id": ep.epoch_id,
        "cred_indices": [i for i, c in enumerate(ep.creds) if c.revoked],
        "list_hash_hex": "0x" + lh.hex(),
        "uri": uri, "receipt": receipt, "version": ep.status_version,
        "anchored_at": receipt.get("anchored_at"),
    })
    _state["chain_txs"].insert(0, {"kind": "status", **receipt})
    return {
        "receipt": receipt, "list_hash_hex": "0x" + lh.hex(),
        "revoked_count": sum(1 for c in ep.creds if c.revoked),
        "total_credentials": len(ep.creds),
        "epoch_id": ep.epoch_id, "credential_index": req.credential_index,
    }


# ---- agents (unchanged surface) ------------------------------------------

@app.post("/api/report")
def audit_report():
    return agent_report.generate_report(_dashboard_state_for_agent())


def _dashboard_state_for_agent() -> dict:
    """Compact snapshot the LLM can reason about — pass rolled-up numbers,
    NOT raw per-doc arrays, or the report goes off the rails."""
    d = dashboard()
    return {
        "mode": "live" if _live() else "fixture",
        "totals": d["totals"],
        "epochs": d["epochs"][:10],
        "recent_revocations": d["revocations"][-5:],
        "recent_txs": d["chain_txs"][:10],
    }


@app.post("/api/chat")
def chat(req: ChatRequest):
    return agent_chat.answer(req.message, _dashboard_state_for_agent(), req.history)


@app.post("/api/explain")
def explain(req: ExplainRequest):
    return {"explanation": agent_explain.explain(
        req.field, req.cand_a, req.cand_b, req.conf, req.signals, req.reason)}


@app.post("/api/adjudicate")
def adjudicate(req: AdjudicateRequest):
    result = agent_adj.adjudicate(
        req.field, req.cand_a, req.cand_b, req.conf_a,
        mrz_hint=req.mrz_hint, validator=req.validator,
    )
    llm_info = agent_llm.describe()
    model_label = (llm_info.get("openai_model") if llm_info["primary"] == "openai"
                   else llm_info.get("gemini_model")) or "fallback"
    return {
        "field": req.field, "chosen": result, "abstained": result is None,
        "model": model_label,
    }


# ---- workflow builder + copilot ------------------------------------------

@app.get("/api/capabilities")
def list_capabilities():
    return capability_descriptions()


@app.get("/api/workflows")
def list_workflows():
    return wfx.list_workflows()


@app.get("/api/workflows/{wid}")
def get_workflow(wid: int):
    w = wfx.get_workflow(wid)
    if not w: raise HTTPException(404, "workflow not found")
    return w


@app.post("/api/workflows")
def save_workflow(req: WorkflowSaveRequest):
    wid = wfx.save_workflow(req.name, req.description, req.config)
    return wfx.get_workflow(wid)


@app.delete("/api/workflows/{wid}")
def delete_workflow(wid: int):
    wfx.delete_workflow(wid)
    return {"ok": True}


@app.post("/api/copilot/generate")
def copilot_generate(req: CopilotRequest):
    return agent_copilot.generate_workflow(req.description)


@app.post("/api/copilot/refine")
def copilot_refine(req: CopilotRefineRequest):
    return agent_copilot.refine_workflow(req.workflow, req.change_request)


def _register_run(bundle: dict, workflow: dict) -> str:
    """Cache a completed run bundle so the PDF endpoint can render it later."""
    bundle["workflow"] = {
        "name": workflow.get("name"),
        "description": workflow.get("description"),
        "config": workflow.get("config"),
    }
    run_id = uuid.uuid4().hex[:12]
    _state["workflow_runs"][run_id] = bundle
    # Keep only last 32 runs to bound memory.
    if len(_state["workflow_runs"]) > 32:
        oldest = list(_state["workflow_runs"].keys())[0]
        _state["workflow_runs"].pop(oldest, None)
    return run_id


@app.post("/api/workflow/upload_run")
async def workflow_upload_run(workflow_id: int, file: UploadFile = File(...)):
    """Accept a CSV of customer records, run each through the workflow,
    cache the bundle, and return a run_id the UI can use to download PDF."""
    w = wfx.get_workflow(workflow_id)
    if not w:
        raise HTTPException(404, "workflow not found")
    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(400, "file must be UTF-8 CSV")

    reader = csv_mod.DictReader(io_mod.StringIO(text))
    records = [{k.strip(): (v or "").strip() for k, v in row.items()} for row in reader]
    if not records:
        raise HTTPException(400, "CSV had no rows")
    if len(records) > 200:
        raise HTTPException(400, "CSV limit is 200 rows for the demo")

    bundle = wfx.run_batch(w["config"], records, pace_ms=0)
    wfx.bump_run_count(workflow_id)
    run_id = _register_run(bundle, w)

    total = len(bundle["runs"])
    passed = sum(1 for r in bundle["runs"] if r.get("ok"))
    return {
        "run_id": run_id,
        "workflow_id": workflow_id,
        "total_records": total,
        "passed": passed,
        "flagged": total - passed,
        "critical": sum(1 for r in bundle["runs"] for s in r["steps"]
                        if not s["ok"] and s["cap"] == "sanctions_screen"),
        "runs": bundle["runs"],
    }


@app.get("/api/workflow/sample_csv")
def sample_csv():
    """Emit a well-formed sample CSV so users know the expected columns."""
    from app.workflow import sample_record
    rows = [sample_record("retail") for _ in range(3)] + [sample_record("corporate") for _ in range(2)]
    # Trim to relevant columns
    cols = ["name", "date_of_birth", "sex", "nationality", "address",
            "status", "pan", "aadhaar_masked", "gstin", "ifsc", "pincode",
            "id_no", "period_of_stay", "date_of_issue", "date_of_expiry"]
    buf = io_mod.StringIO()
    w = csv_mod.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        # newlines in address break CSV parsers; flatten
        r2 = dict(r)
        if "address" in r2:
            r2["address"] = r2["address"].replace("\n", ", ")
        w.writerow(r2)
    return Response(content=buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="finfabric-sample.csv"'})


@app.get("/api/document/schema")
def document_schema():
    """Card layout for the extraction studio overlays."""
    from schema import CARD_W, CARD_H, MRZ_BOX
    return {
        "image_url": "/sample_card.jpg",
        "width": CARD_W, "height": CARD_H,
        "mrz_box": {"x": MRZ_BOX[0], "y": MRZ_BOX[1], "w": MRZ_BOX[2], "h": MRZ_BOX[3]},
        "fields": [{
            "name": f.name, "label": f.label,
            "box": {"x": f.box[0], "y": f.box[1], "w": f.box[2], "h": f.box[3]},
            "in_mrz": f.in_mrz, "validator": f.validator,
        } for f in FIELDS],
    }


@app.get("/api/document/analyze")
def document_analyze(seed: Optional[int] = None):
    """Fresh simulated VLM + dual-OCR + MRZ analysis on the sample card.
    Returns per-field extraction with box, values, confidences and status.
    The UI paints these on top of sample_card.jpg with staged animation."""
    import random as _rnd
    from schema import CARD_W, CARD_H, MRZ_BOX
    seed = seed if seed is not None else int(time.time()) & 0xFFFF
    rng = _rnd.Random(seed)
    doc, _ocr_debug = issuance.run_one(rng, f"extract-{seed}")

    # Look up each decision by field
    dec_by = {d.field: d for d in doc.decisions}

    field_analyses = []
    for f in FIELDS:
        d = dec_by[f.name]
        # Category for coloring:
        if d.accepted and d.correct: status = "issued"
        elif d.accepted and not d.correct: status = "escape"
        elif not d.accepted and "repaired" in (d.reason or "").lower(): status = "repaired"
        elif not d.accepted and d.signals.get("mrz") is False: status = "mrz_conflict"
        elif not d.accepted and d.signals.get("agreement") is False: status = "disagree"
        else: status = "review"
        field_analyses.append({
            "name": f.name, "label": f.label,
            "box": {"x": f.box[0], "y": f.box[1], "w": f.box[2], "h": f.box[3]},
            "in_mrz": f.in_mrz,
            "truth": d.truth, "canonical": d.value,
            "ocr_a": d.ocr_a, "ocr_a_conf": round(d.ocr_a_conf, 3),
            "ocr_b": d.ocr_b, "mrz_value": d.mrz_value,
            "accepted": d.accepted, "correct": d.correct,
            "signals": d.signals, "reason": d.reason,
            "status": status,
        })

    # VLM document classification (mocked deterministically)
    vlm_classes = ["Passport-style ID", "National residence card", "Aadhaar card",
                   "PAN card", "Driving licence"]
    vlm_class = "National residence card"  # matches our sample
    vlm_conf = 0.94 + rng.random() * 0.05

    return {
        "seed": seed,
        "image_url": "/sample_card.jpg",
        "width": CARD_W, "height": CARD_H,
        "mrz_box": {"x": MRZ_BOX[0], "y": MRZ_BOX[1], "w": MRZ_BOX[2], "h": MRZ_BOX[3]},
        "mrz_ok": doc.mrz_ok,
        "mrz_lines": ([str(doc.truth.get(k, "")) for k in ["name"]] +
                      # we don't have mrz lines on DocResult; reconstruct short summary
                      []) or [],
        "vlm": {"class": vlm_class, "confidence": round(vlm_conf, 3),
                "alternatives": [{"class": c, "confidence": round(0.05 + rng.random() * 0.4, 3)}
                                 for c in vlm_classes if c != vlm_class][:3]},
        "fields": field_analyses,
        "summary": {
            "total_fields": len(field_analyses),
            "auto_issued": sum(1 for a in field_analyses if a["status"] == "issued"),
            "repaired_from_mrz": sum(1 for a in field_analyses if a["status"] == "repaired"),
            "to_review": sum(1 for a in field_analyses if a["status"] in ("review", "disagree", "mrz_conflict")),
            "escapes": sum(1 for a in field_analyses if a["status"] == "escape"),
        },
    }


@app.get("/api/report/pdf/{run_id}")
def report_pdf(run_id: str):
    bundle = _state["workflow_runs"].get(run_id)
    if not bundle:
        raise HTTPException(404, "run not found — regenerate the workflow run")
    pdf_bytes = pdf_report.build_pdf(bundle)
    wf_name = bundle.get("workflow", {}).get("name", "run")
    safe = "".join(c if c.isalnum() else "_" for c in wf_name)[:40]
    return Response(
        content=pdf_bytes, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="finfabric-audit-{safe}.pdf"'},
    )


@app.get("/api/workflow/run_stream")
async def workflow_run_stream(
    workflow_id: Optional[int] = None,
    kind: str = "retail",
    pace_ms: int = 220,
):
    """SSE stream of a workflow execution against a synthetic record."""
    if workflow_id is None:
        raise HTTPException(400, "workflow_id required")
    w = wfx.get_workflow(workflow_id)
    if not w: raise HTTPException(404, "workflow not found")
    record = wfx.sample_record(kind)

    q: queue.Queue = queue.Queue()

    def worker():
        try:
            events = []
            def relay(evt):
                events.append(evt)
                q.put(evt)
            result = wfx.run_workflow(w["config"], record, on_step=relay, pace_ms=pace_ms)
            wfx.bump_run_count(workflow_id)
            # Cache bundle for PDF export
            steps = [{"cap": e["cap"], "ok": e.get("ok"), "detail": e.get("detail"),
                      "duration_ms": e.get("duration_ms")}
                     for e in events if e.get("type") == "node_end"]
            bundle = {
                "kind": "single",
                "runs": [{"record": record, "steps": steps,
                          "ok": result.get("ok"), "anchor_receipt": result.get("anchor_receipt")}],
                "started_at": int(time.time()),
                "finished_at": int(time.time()),
            }
            run_id = _register_run(bundle, w)
            q.put({"type": "final", "ok": result["ok"], "run_id": run_id})
        except Exception as e:
            q.put({"type": "error", "message": f"{type(e).__name__}: {e}"})
        finally:
            q.put(None)

    threading.Thread(target=worker, daemon=True).start()

    async def gen():
        loop = asyncio.get_event_loop()
        # Send the record first so the UI can display what we're running on
        yield f"data: {json.dumps({'type': 'record', 'record': record})}\n\n"
        while True:
            evt = await loop.run_in_executor(None, q.get)
            if evt is None: break
            yield f"data: {json.dumps(evt, default=str)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


# ---- legacy: aggregate harness (kept for /api/harness callers) -----------

@app.post("/api/harness")
def run_harness_aggregate(n: int = 2000, seed: int = 7):
    buf = io.StringIO()
    t0 = time.time()
    with redirect_stdout(buf):
        harness.run(n=n, seed=seed)
    text = buf.getvalue()
    metrics = {}
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("documents "): metrics["documents"] = int(s.split()[-1])
        elif "raw field accuracy" in s: metrics["raw_accuracy"] = float(s.split()[3].rstrip("%")) / 100
        elif s.startswith("auto-accepted"): metrics["auto_accept"] = float(s.split()[1].rstrip("%")) / 100
        elif s.startswith("sent to review"): metrics["review_rate"] = float(s.split()[3].rstrip("%")) / 100
        elif s.startswith("ESCAPE RATE"): metrics["escape_rate"] = float(s.split()[2].rstrip("%")) / 100
    metrics["elapsed_ms"] = int((time.time() - t0) * 1000)
    metrics["raw_output"] = text
    _state["harness_result"] = metrics
    return metrics


# ---- static + boot -------------------------------------------------------

app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


@app.get("/sample_card.jpg")
def sample_card():
    return FileResponse(REPO / "sample_card.jpg")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.on_event("startup")
def startup():
    wfx.init_db()
    _bootstrap()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
