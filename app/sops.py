"""Banking-SOP knowledge base for FinFabric.

For every capability + failure mode we ship the regulatory citation and the
recommended action from Indian banking guidelines. This is the file to
update when RBI issues new master directions. Deliberately hand-curated —
not LLM-inferred — so the PDF report is legally defensible."""

from __future__ import annotations


# Each entry answers: WHY did this fail (per the specific signal), WHICH
# regulation governs the response, and WHAT the KYC officer / compliance
# team should do next. Kept intentionally concise; the PDF report renders
# them as bullet points next to the failing customer record.

SOP_GUIDANCE: dict[str, dict] = {
    "extract_fields": {
        "title": "KYC field extraction",
        "when_fails": "Fewer than six canonical fields could be extracted from the submitted document.",
        "citation": "RBI Master Direction on KYC (2016, as amended 2024), para 10 — Officially Valid Documents (OVD) list.",
        "action": "Return document to customer with checklist of missing fields. Request one OVD (passport / driving licence / voter ID / Aadhaar / NREGA job card) that carries name + address + photo.",
        "severity": "MEDIUM",
    },
    "pan_validate": {
        "title": "PAN format check",
        "when_fails": "PAN does not match the 5-letter + 4-digit + 1-letter canonical pattern issued by CBDT.",
        "citation": "Income Tax Act 1961, sec 139A read with RBI KYC MD para 16.",
        "action": "Reject the application. If customer has no PAN, accept Form 60 as declaration in lieu — mandatory for accounts >₹50,000 or aggregate credits >₹5,00,000 in a year.",
        "severity": "HIGH",
    },
    "aadhaar_masked_validate": {
        "title": "Masked Aadhaar check",
        "when_fails": "Aadhaar was submitted in full 12-digit form OR the masked pattern is invalid.",
        "citation": "Aadhaar Act 2016 sec 8; DPDP Act 2023; UIDAI circular K-11020/125/2017-UIDAI.",
        "action": "DO NOT store the full Aadhaar number. Ask customer to redact / mask (only last 4 digits visible), or switch to offline eKYC via DigiLocker / mAadhaar. Purge any inadvertently captured full-number copy per DPDP Act.",
        "severity": "HIGH",
    },
    "gstin_validate": {
        "title": "GSTIN validity",
        "when_fails": "GSTIN failed the base-36 checksum or does not match the 15-character CBIC format.",
        "citation": "CGST Act 2017 sec 25; CBIC GSTIN structure guidelines.",
        "action": "Request corrected GSTIN certificate. Cross-verify on gst.gov.in — GSTIN, legal name, and constitution of business must match KYC record.",
        "severity": "MEDIUM",
    },
    "ifsc_validate": {
        "title": "IFSC code check",
        "when_fails": "IFSC does not match the 4-letter + 0 + 6-alphanumeric RBI pattern.",
        "citation": "RBI directive DPSS.CO.CHD.No/133/03.06.01/2019-20.",
        "action": "Verify the correct IFSC at https://ifsc.rbi.org.in. Request customer to provide a corrected cancelled cheque or bank statement showing the code.",
        "severity": "LOW",
    },
    "pincode_validate": {
        "title": "Address PIN code",
        "when_fails": "PIN code missing or does not match Department of Posts pattern (6 digits, first digit 1–8).",
        "citation": "RBI KYC MD para 16 (Address Proof); Department of Posts PIN directory.",
        "action": "Request a valid address-proof OVD showing a resolvable PIN. If customer's stated PIN and OVD PIN differ, capture both as permanent + current address per RBI MD.",
        "severity": "LOW",
    },
    "address_verify": {
        "title": "Address structure",
        "when_fails": "Address could not be parsed into street + city + PIN.",
        "citation": "RBI KYC MD para 16(c) — Deemed OVD list; DPDP Act 2023 for accuracy obligation.",
        "action": "Ask customer to resubmit address in structured form. If OVD address is out-of-date, capture current address separately with a self-declaration + deemed OVD (utility bill ≤2 months old, rent agreement, employer certificate).",
        "severity": "LOW",
    },
    "document_classify": {
        "title": "Document classification",
        "when_fails": "The document class returned by the classifier is not on the accepted OVD list.",
        "citation": "RBI KYC MD para 16 — Officially Valid Documents.",
        "action": "Reject the upload. Ask customer to submit one of: passport, driving licence, voter ID, Aadhaar (masked), NREGA job card, or Letter from National Population Register.",
        "severity": "MEDIUM",
    },
    "sanctions_screen": {
        "title": "Sanctions / PMLA screening",
        "when_fails": "Customer name or PAN matches an entry on PMLA / OFAC / UNSC consolidated list.",
        "citation": "PMLA 2002 sec 12A + PMLR 2005; RBI KYC MD Ch V; UNSC Res 1267/1373/1988 as adopted by MEA notifications.",
        "action": "IMMEDIATE FREEZE of customer's funds. Do NOT tip off the customer. File Suspicious Transaction Report (STR) with FIU-IND within 7 working days. Report to designated Nodal Officer. Retain evidence for 5 years per PMLA.",
        "severity": "CRITICAL",
    },
    "pep_check": {
        "title": "Politically Exposed Person",
        "when_fails": "Customer matches a Politically Exposed Person entry (Indian or foreign).",
        "citation": "RBI KYC MD para 40; FATF Recommendation 12 (PEPs).",
        "action": "Do NOT auto-onboard. Escalate to senior management (branch head + compliance officer) for approval. Apply Enhanced Due Diligence: source-of-funds declaration, cross-check with independent public records, ongoing enhanced monitoring, annual (not periodic) review.",
        "severity": "HIGH",
    },
    "aml_risk_score": {
        "title": "AML risk categorisation",
        "when_fails": "Combined risk score falls in the HIGH band (>= 60/100).",
        "citation": "RBI KYC MD para 8 (Risk categorisation); PMLA Rules 2005 rule 9(1A).",
        "action": "Categorise customer as High Risk. Apply Enhanced Due Diligence per RBI MD Ch VI. Reduce periodic-review interval from 8 years → 2 years. Senior-management sign-off required before onboarding. Ongoing transaction monitoring against the customer's declared source of funds.",
        "severity": "HIGH",
    },
    "commit_merkle": {
        "title": "Credential commitment",
        "when_fails": "Merkle commitment could not be built because upstream extraction produced no fields.",
        "citation": "Internal control — data-quality gate before on-chain anchoring.",
        "action": "Fix upstream extraction; do not attempt to anchor a credential with zero disclosed fields. Anchoring an empty root is technically valid but audit-useless.",
        "severity": "MEDIUM",
    },
    "anchor_epoch": {
        "title": "On-chain anchor",
        "when_fails": "Anchor transaction failed (network / gas / permission).",
        "citation": "Internal SOP — chain-connectivity failure handling.",
        "action": "Retry with fresh nonce. If sustained failure, hold credential in local queue and alert DevOps; do not report success to customer until anchored.",
        "severity": "MEDIUM",
    },
}


# Severity ranking for sorting flagged items in the PDF.
_SEVERITY_RANK = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


def sop_for(capability_key: str) -> dict:
    """Look up SOP guidance for a capability. Returns a safe default if
    we haven't curated one yet (rather than crashing the PDF)."""
    return SOP_GUIDANCE.get(capability_key, {
        "title": capability_key,
        "when_fails": "Capability reported failure.",
        "citation": "General bank internal controls.",
        "action": "Escalate to KYC officer for manual review.",
        "severity": "MEDIUM",
    })


def sort_by_severity(flagged_items: list) -> list:
    """Sort flagged items so CRITICAL appears first in the report."""
    return sorted(flagged_items, key=lambda x: _SEVERITY_RANK.get(x.get("severity"), 99))
