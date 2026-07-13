"""Workflow copilot. Given a natural-language description ("Build a workflow
for onboarding NRI customers with sanctions check"), produce a workflow
config JSON that references the platform's capabilities. Grounded — the
prompt lists exactly which capabilities exist and how they compose."""

from __future__ import annotations

import json
import re

from . import gemini
from app.capabilities import capability_descriptions

_SYSTEM = (
    "You are a workflow copilot for a decentralized-KYC platform used by "
    "Indian banks. Given a plain-language description of what the bank "
    "wants to onboard, produce a workflow JSON that composes ONLY the "
    "provided capabilities in a valid topological order. The output must "
    "be a single JSON object with:\n"
    "  - name (string)\n"
    "  - description (one sentence)\n"
    "  - config: {nodes: [{id, cap, params}], edges: [[from_id, to_id], ...]}\n"
    "Every node id must be unique. Every 'cap' must be one of the "
    "capabilities listed. Do not invent capabilities. The workflow must "
    "start with an extract-style node and end with commit_merkle → "
    "anchor_epoch, so the customer's KYC lands on-chain.\n"
    "Return ONLY the JSON, no prose, no markdown fence."
)


def _capabilities_prompt() -> str:
    return "\n".join(
        f"- {c['key']} ({c['category']}): {c['label']}"
        for c in capability_descriptions()
    )


def generate_workflow(description: str) -> dict:
    """Returns {ok, workflow?, raw?, error?}."""
    if not gemini.enabled():
        return {"ok": False, "error": "Copilot needs GEMINI_API_KEY set."}

    prompt = (
        "Available capabilities (use these keys exactly):\n" +
        _capabilities_prompt() + "\n\n"
        "User request:\n" + description.strip() + "\n\n"
        "Reply with the workflow JSON only."
    )
    out = gemini.generate(prompt, system=_SYSTEM, temperature=0.2,
                          max_tokens=1200, thinking_budget=512, timeout=45)
    if gemini.is_error(out):
        return {"ok": False, "error": out}

    # Strip possible fences and pull the first JSON object.
    text = out.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {"ok": False, "error": "No JSON in response", "raw": out}
    try:
        wf = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"JSON parse failed: {e}", "raw": out}

    # Validate: capabilities exist, node ids unique, edges reference known nodes
    valid_caps = {c["key"] for c in capability_descriptions()}
    config = wf.get("config", {})
    nodes = config.get("nodes", [])
    edges = config.get("edges", [])
    ids = set()
    for n in nodes:
        nid = n.get("id"); cap = n.get("cap")
        if nid in ids: return {"ok": False, "error": f"duplicate node id {nid!r}"}
        ids.add(nid)
        if cap not in valid_caps: return {"ok": False, "error": f"unknown capability {cap!r}"}
        n.setdefault("params", {})
    for a, b in edges:
        if a not in ids or b not in ids:
            return {"ok": False, "error": f"edge references unknown node: {a} → {b}"}

    return {"ok": True, "workflow": wf, "model": gemini.model_name()}


_REFINE_SYSTEM = (
    "You are refining an existing FinFabric workflow. The user will give "
    "you the current workflow JSON and a change request in plain English. "
    "Output the FULL updated workflow JSON (same shape as generate: "
    "{name, description, config: {nodes, edges}}). Every node id must stay "
    "unique. Every cap must be in the list of available capabilities. "
    "Preserve existing node ids when possible so downstream references "
    "(edges, etc.) don't break. Do NOT explain — output JSON only."
)


def refine_workflow(current: dict, change_request: str) -> dict:
    """Modify an existing workflow based on a natural-language edit."""
    if not gemini.enabled():
        return {"ok": False, "error": "Copilot needs GEMINI_API_KEY set."}

    prompt = (
        "Available capabilities (use these keys exactly):\n" +
        _capabilities_prompt() + "\n\n"
        "Current workflow:\n" + json.dumps(current, indent=2) + "\n\n"
        "Change request:\n" + change_request.strip() + "\n\n"
        "Reply with the updated workflow JSON only."
    )
    out = gemini.generate(prompt, system=_REFINE_SYSTEM, temperature=0.2,
                          max_tokens=1400, thinking_budget=512, timeout=45)
    if gemini.is_error(out):
        return {"ok": False, "error": out}

    text = out.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {"ok": False, "error": "No JSON in response", "raw": out}
    try:
        wf = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"JSON parse failed: {e}", "raw": out}

    # Same validation
    valid_caps = {c["key"] for c in capability_descriptions()}
    config = wf.get("config", {})
    nodes = config.get("nodes", [])
    edges = config.get("edges", [])
    ids = set()
    for n in nodes:
        nid = n.get("id"); cap = n.get("cap")
        if nid in ids: return {"ok": False, "error": f"duplicate node id {nid!r}"}
        ids.add(nid)
        if cap not in valid_caps: return {"ok": False, "error": f"unknown capability {cap!r}"}
        n.setdefault("params", {})
    for a, b in edges:
        if a not in ids or b not in ids:
            return {"ok": False, "error": f"edge references unknown node: {a} → {b}"}
    return {"ok": True, "workflow": wf, "model": gemini.model_name()}
