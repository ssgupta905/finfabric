"""Workflow engine + persistence.

A workflow is a directed acyclic graph of capability nodes. The engine
executes them in topological order, streaming a per-node event so the UI
can pulse the graph in real time.

Persistence is via SQLAlchemy Core, which switches transparently between
SQLite (local dev) and Postgres (Render / any host with DATABASE_URL)."""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from sqlalchemy import select, update, delete, insert, func

from app.capabilities import CAPABILITIES
from app.db import engine, init_schema, workflows as wf_table

REPO = Path(__file__).resolve().parents[1]


# ---------- persistence --------------------------------------------------

def init_db():
    init_schema()
    _seed_default_workflows()


def _seed_default_workflows():
    """Ship 3 banking-native starter workflows so the UI has something to
    show on the first cold-start."""
    with engine().connect() as c:
        count = c.execute(select(func.count()).select_from(wf_table)).scalar_one()
        if count > 0: return

    starters = [
        {
            "name": "Retail — New savings account",
            "description": "Standard onboarding: extract, PAN + PIN + address checks, PEP + sanctions screen, AML score, then commit and anchor.",
            "config": {
                "nodes": [
                    {"id": "extract",  "cap": "extract_fields", "params": {}},
                    {"id": "pan",      "cap": "pan_validate",   "params": {}},
                    {"id": "pin",      "cap": "pincode_validate","params": {}},
                    {"id": "addr",     "cap": "address_verify", "params": {}},
                    {"id": "pep",      "cap": "pep_check",      "params": {}},
                    {"id": "sanct",    "cap": "sanctions_screen","params": {"list": "PMLA/OFAC"}},
                    {"id": "aml",      "cap": "aml_risk_score", "params": {}},
                    {"id": "commit",   "cap": "commit_merkle",  "params": {}},
                    {"id": "anchor",   "cap": "anchor_epoch",   "params": {}},
                ],
                "edges": [["extract","pan"],["pan","pin"],["pin","addr"],
                          ["addr","pep"],["pep","sanct"],["sanct","aml"],
                          ["aml","commit"],["commit","anchor"]],
            },
        },
        {
            "name": "Corporate — GSTIN onboarding",
            "description": "Corporate KYC: extract, GSTIN checksum, PAN, IFSC, sanctions, then anchor. Skips PEP (used for individuals).",
            "config": {
                "nodes": [
                    {"id": "extract",  "cap": "extract_fields", "params": {}},
                    {"id": "gstin",    "cap": "gstin_validate", "params": {}},
                    {"id": "pan",      "cap": "pan_validate",   "params": {}},
                    {"id": "ifsc",     "cap": "ifsc_validate",  "params": {}},
                    {"id": "sanct",    "cap": "sanctions_screen","params": {"list": "PMLA/OFAC/RBI-Wilful-Defaulters"}},
                    {"id": "aml",      "cap": "aml_risk_score", "params": {}},
                    {"id": "commit",   "cap": "commit_merkle",  "params": {}},
                    {"id": "anchor",   "cap": "anchor_epoch",   "params": {}},
                ],
                "edges": [["extract","gstin"],["gstin","pan"],["pan","ifsc"],
                          ["ifsc","sanct"],["sanct","aml"],["aml","commit"],
                          ["commit","anchor"]],
            },
        },
        {
            "name": "Re-KYC — periodic update",
            "description": "Existing customer: just re-verify address + PIN + sanctions, then re-anchor. Fast path.",
            "config": {
                "nodes": [
                    {"id": "extract",  "cap": "extract_fields", "params": {}},
                    {"id": "addr",     "cap": "address_verify", "params": {}},
                    {"id": "pin",      "cap": "pincode_validate","params": {}},
                    {"id": "sanct",    "cap": "sanctions_screen","params": {}},
                    {"id": "commit",   "cap": "commit_merkle",  "params": {}},
                    {"id": "anchor",   "cap": "anchor_epoch",   "params": {}},
                ],
                "edges": [["extract","addr"],["addr","pin"],["pin","sanct"],
                          ["sanct","commit"],["commit","anchor"]],
            },
        },
    ]
    for w in starters:
        save_workflow(w["name"], w["description"], w["config"])


def _row_to_dict(r) -> dict:
    return {
        "id": r.id, "name": r.name, "description": r.description,
        "config": json.loads(r.config_json),
        "created_at": r.created_at, "run_count": r.run_count or 0,
    }


def save_workflow(name: str, description: str, config: dict) -> int:
    with engine().begin() as c:
        result = c.execute(insert(wf_table).values(
            name=name, description=description,
            config_json=json.dumps(config), created_at=int(time.time()),
            run_count=0,
        ))
        pk = result.inserted_primary_key
        return pk[0] if pk else None


def list_workflows() -> list[dict]:
    with engine().connect() as c:
        rows = c.execute(select(wf_table).order_by(wf_table.c.id.desc())).all()
    return [_row_to_dict(r) for r in rows]


def get_workflow(wid: int) -> Optional[dict]:
    with engine().connect() as c:
        r = c.execute(select(wf_table).where(wf_table.c.id == wid)).first()
    return _row_to_dict(r) if r else None


def delete_workflow(wid: int) -> bool:
    with engine().begin() as c:
        c.execute(delete(wf_table).where(wf_table.c.id == wid))
    return True


def bump_run_count(wid: int):
    with engine().begin() as c:
        c.execute(update(wf_table).where(wf_table.c.id == wid).values(
            run_count=wf_table.c.run_count + 1))


# ---------- execution engine ---------------------------------------------

def _topo_order(nodes, edges):
    """Kahn's algorithm — return the order and detect cycles."""
    id_to_node = {n["id"]: n for n in nodes}
    incoming = {n["id"]: set() for n in nodes}
    outgoing = {n["id"]: set() for n in nodes}
    for a, b in edges:
        outgoing.setdefault(a, set()).add(b)
        incoming.setdefault(b, set()).add(a)
    order = []
    frontier = [n["id"] for n in nodes if not incoming[n["id"]]]
    while frontier:
        nid = frontier.pop(0)
        order.append(nid)
        for succ in list(outgoing[nid]):
            incoming[succ].discard(nid)
            if not incoming[succ]:
                frontier.append(succ)
    if len(order) != len(nodes):
        raise ValueError("workflow has a cycle")
    return [id_to_node[nid] for nid in order]


def run_workflow(config: dict, record: dict, on_step: Optional[Callable] = None,
                 pace_ms: int = 220) -> dict:
    """Execute the workflow on a single record, streaming events for each
    node's start + finish. Returns a rolled-up summary + per-node results."""
    nodes = config.get("nodes", [])
    edges = config.get("edges", [])
    try:
        ordered = _topo_order(nodes, edges)
    except ValueError as e:
        if on_step:
            on_step({"type": "error", "message": str(e)})
        return {"ok": False, "error": str(e), "results": {}}

    ctx = {"record": record, "signals_bus": {}}
    results = {}
    all_ok = True

    if on_step:
        on_step({"type": "run_start",
                 "record_summary": {k: record.get(k) for k in ("name", "pan", "gstin", "nationality")
                                    if record.get(k)}})

    for node in ordered:
        cap_key = node["cap"]
        cap = CAPABILITIES.get(cap_key)
        if not cap:
            r = {"ok": False, "detail": f"unknown capability '{cap_key}'",
                 "signals": {}, "duration_ms": 0}
        else:
            if on_step:
                on_step({"type": "node_start", "node_id": node["id"], "cap": cap_key,
                         "label": cap.label, "category": cap.category})
                if pace_ms: time.sleep(pace_ms / 1000)
            try:
                r = cap.fn(record, {**cap.default_params, **node.get("params", {})}, ctx)
            except Exception as e:
                r = {"ok": False, "value": None, "detail": f"error: {type(e).__name__}: {e}",
                     "signals": {"exception": type(e).__name__}, "duration_ms": 0}

        results[node["id"]] = {"cap": cap_key, **r}
        # publish signal for downstream nodes (e.g., AML pulls from PEP)
        ctx["signals_bus"][cap_key] = r.get("ok")
        # Special: extract_fields exposes extracted map for downstream commit
        if cap_key == "extract_fields":
            ctx["extracted"] = r.get("value") or {}

        all_ok = all_ok and r.get("ok", False)
        if on_step:
            on_step({"type": "node_end", "node_id": node["id"], "cap": cap_key,
                     "ok": r.get("ok"), "detail": r.get("detail"),
                     "duration_ms": r.get("duration_ms"),
                     "value_preview": _preview(r.get("value"))})

    if on_step:
        on_step({"type": "run_end", "ok": all_ok,
                 "anchor_receipt": ctx.get("anchor_receipt")})
    return {"ok": all_ok, "results": results, "anchor_receipt": ctx.get("anchor_receipt")}


def _preview(v):
    if v is None: return None
    if isinstance(v, (int, float, bool)): return v
    s = str(v)
    return s if len(s) <= 80 else s[:77] + "…"


# ---------- sample records -----------------------------------------------

def sample_record(kind: str = "retail") -> dict:
    """Synthesise a realistic customer record for a given onboarding scenario."""
    rng = random.Random()
    surnames = ["SHARMA", "IYER", "PATEL", "REDDY", "SINGH", "AGARWAL", "MENON"]
    givens = ["PRIYA", "ARJUN", "AISHA", "KARAN", "MEERA", "VIKRAM"]
    name = f"{rng.choice(surnames)} {rng.choice(givens)}"
    pan = "".join(rng.choices("ABCDEFGHJKLMNPQRSTUVWXYZ", k=5)) + \
          "".join(rng.choices("0123456789", k=4)) + rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ")
    common = {
        "name": name,
        "date_of_birth": f"{rng.randint(1965, 2000)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
        "sex": rng.choice(["M", "F"]),
        "nationality": "IND",
        "address": f"{rng.randint(1, 99)} MG ROAD\n{rng.choice(['MUMBAI', 'BENGALURU', 'CHENNAI', 'DELHI'])} {rng.randint(100000, 899999)}",
        "pan": pan,
        "aadhaar_masked": f"XXXX-XXXX-{rng.randint(1000, 9999)}",
        "pincode": str(rng.randint(100001, 899999)),
        "ifsc": f"{rng.choice(['SBIN','HDFC','ICIC','IDIB'])}0" + "".join(rng.choices("0123456789", k=6)),
        "date_of_issue": "2024-01-15",
        "date_of_expiry": "2034-01-15",
        "period_of_stay": "10 YEARS",
        "id_no": pan,
    }
    if kind == "corporate":
        state = f"{rng.randint(1, 37):02d}"
        gstin = state + pan + rng.choice("ABCDEFGH") + "Z" + rng.choice("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ")
        # Recompute the checksum digit so it validates
        alpha = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        factor = 1; total = 0
        for c in gstin[:14]:
            v = alpha.index(c) * factor
            total += v // 36 + v % 36
            factor = 2 if factor == 1 else 1
        check = alpha[(36 - total % 36) % 36]
        gstin = gstin[:14] + check
        common["gstin"] = gstin
        common["status"] = "CORPORATE KYC"
    else:
        common["status"] = rng.choice(["FULL KYC", "SIMPLIFIED KYC", "SMALL ACCOUNT"])
    return common
