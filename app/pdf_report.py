"""Generate an audit-ready PDF for a workflow run (single or batch).

Each flagged item gets the specific failure reason plus the SOP citation
and recommended action from app/sops.py. The output is compliance-grade
(citations are hand-curated, not LLM-inferred) so a KYC officer or an RBI
reviewer can act directly from the document."""

from __future__ import annotations

import io
from datetime import datetime, timezone

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (BaseDocTemplate, Frame, PageTemplate,
                                Paragraph, Spacer, Table, TableStyle, KeepTogether)

from app.sops import sop_for, sort_by_severity


# ---------- styles -------------------------------------------------------

_BASE = getSampleStyleSheet()
INK      = colors.HexColor("#1d1d1f")
INK2     = colors.HexColor("#424245")
INK3     = colors.HexColor("#6e6e73")
ACCENT   = colors.HexColor("#0071e3")
GOOD     = colors.HexColor("#007a4d")
WARN     = colors.HexColor("#a55300")
BAD      = colors.HexColor("#b3261e")
CRITICAL = colors.HexColor("#7a0f0a")
LINE     = colors.HexColor("#d2d2d7")
BG2      = colors.HexColor("#f5f5f7")

STY = {
    "title": ParagraphStyle("Title", parent=_BASE["Title"], fontName="Helvetica-Bold",
                             fontSize=22, leading=26, textColor=INK, spaceAfter=6,
                             alignment=TA_LEFT),
    "eyebrow": ParagraphStyle("Eyebrow", parent=_BASE["BodyText"],
                               fontName="Helvetica-Bold", fontSize=9, leading=11,
                               textColor=ACCENT, spaceAfter=4,
                               letterSpacing=0.6, alignment=TA_LEFT),
    "sub": ParagraphStyle("Sub", parent=_BASE["BodyText"], fontName="Helvetica",
                           fontSize=11, leading=15, textColor=INK2, spaceAfter=14),
    "h2": ParagraphStyle("H2", parent=_BASE["Heading2"], fontName="Helvetica-Bold",
                          fontSize=14, leading=18, textColor=INK, spaceBefore=14,
                          spaceAfter=8),
    "h3": ParagraphStyle("H3", parent=_BASE["Heading3"], fontName="Helvetica-Bold",
                          fontSize=11.5, leading=14, textColor=INK, spaceBefore=8,
                          spaceAfter=4),
    "body": ParagraphStyle("Body", parent=_BASE["BodyText"], fontName="Helvetica",
                            fontSize=10, leading=14, textColor=INK2, spaceAfter=6),
    "mono": ParagraphStyle("Mono", parent=_BASE["BodyText"], fontName="Courier",
                            fontSize=9, leading=12, textColor=INK2, spaceAfter=4),
    "small": ParagraphStyle("Small", parent=_BASE["BodyText"], fontName="Helvetica",
                             fontSize=8.5, leading=11, textColor=INK3),
    "badge_ok":       ParagraphStyle("BOK", fontName="Helvetica-Bold", fontSize=8, leading=10, textColor=GOOD),
    "badge_bad":      ParagraphStyle("BBad", fontName="Helvetica-Bold", fontSize=8, leading=10, textColor=BAD),
    "badge_critical": ParagraphStyle("BCrit", fontName="Helvetica-Bold", fontSize=8, leading=10, textColor=CRITICAL),
}


# ---------- doc frame with page numbers ----------------------------------

class _Doc(BaseDocTemplate):
    def __init__(self, buffer, **kw):
        super().__init__(buffer, pagesize=A4, leftMargin=18*mm, rightMargin=18*mm,
                          topMargin=18*mm, bottomMargin=18*mm, **kw)
        frame = Frame(self.leftMargin, self.bottomMargin,
                      self.width, self.height, id="body")
        self.addPageTemplates([PageTemplate(id="all", frames=[frame], onPage=self._draw_footer)])

    def _draw_footer(self, canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(INK3)
        canvas.drawString(18*mm, 10*mm,
                          f"FinFabric · compliance audit · generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%MZ')}")
        canvas.drawRightString(A4[0] - 18*mm, 10*mm, f"page {doc.page}")
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.4)
        canvas.line(18*mm, 12*mm, A4[0] - 18*mm, 12*mm)
        canvas.restoreState()


# ---------- helpers ------------------------------------------------------

def _severity_style(sev: str) -> ParagraphStyle:
    if sev == "CRITICAL": return STY["badge_critical"]
    if sev in ("HIGH", "MEDIUM"): return STY["badge_bad"]
    return STY["badge_ok"]


def _severity_bg(sev: str):
    return {"CRITICAL": colors.HexColor("#f4d1cf"),
            "HIGH":     colors.HexColor("#fadcda"),
            "MEDIUM":   colors.HexColor("#fbe6c8"),
            "LOW":      colors.HexColor("#eef2f7")}.get(sev, BG2)


def _record_summary_row(record: dict) -> list:
    """Compact single-line summary for a batch row."""
    def g(k): return str(record.get(k) or "—")
    return [g("name"), g("pan") or g("gstin"), g("nationality"), g("address")[:40]]


def _flatten_flags(run: dict) -> list[dict]:
    """From a single-record run, collect each failed node with SOP info."""
    flags = []
    for step in run.get("steps", []):
        if not step.get("ok"):
            sop = sop_for(step["cap"])
            flags.append({
                "cap": step["cap"],
                "detail": step.get("detail"),
                "signals": step.get("signals"),
                **sop,
            })
    return sort_by_severity(flags)


# ---------- report builders ----------------------------------------------

def build_pdf(run_bundle: dict) -> bytes:
    """`run_bundle` shape:
      {
        "kind": "single" | "batch",
        "workflow": {"name": str, "description": str, "config": {...}},
        "runs": [
          {"record": {...}, "steps": [{"cap", "ok", "detail", "signals", "duration_ms"}], "ok": bool,
           "anchor_receipt": {...}?}
        ],
        "started_at": unix_ts,
        "finished_at": unix_ts,
      }
    """
    buf = io.BytesIO()
    doc = _Doc(buf, title=f"FinFabric audit — {run_bundle.get('workflow', {}).get('name', 'Run')}")
    story = []

    wf = run_bundle.get("workflow", {})
    runs = run_bundle.get("runs", [])
    kind = run_bundle.get("kind", "single")
    now_z = datetime.now(timezone.utc)

    # ---- header ----
    story.append(Paragraph("COMPLIANCE AUDIT NOTE · IDBI KYC PIPELINE", STY["eyebrow"]))
    story.append(Paragraph(wf.get("name", "Workflow run"), STY["title"]))
    story.append(Paragraph(wf.get("description", "") or "", STY["sub"]))

    # ---- summary card ----
    total = len(runs)
    passed = sum(1 for r in runs if r.get("ok"))
    flagged = total - passed
    total_flags = sum(len(_flatten_flags(r)) for r in runs)
    critical = sum(1 for r in runs for f in _flatten_flags(r) if f.get("severity") == "CRITICAL")

    summary_data = [
        ["Records processed", f"{total}"],
        ["Auto-passed", f"{passed}"],
        ["Flagged for officer review", f"{flagged}"],
        ["Total flags raised", f"{total_flags}"],
        ["Critical (immediate action)", f"{critical}"],
        ["Report time", now_z.strftime("%Y-%m-%d %H:%M UTC")],
    ]
    t = Table(summary_data, colWidths=[70*mm, 60*mm], hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), BG2),
        ("BOX", (0, 0), (-1, -1), 0.5, LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, LINE),
        ("FONT", (0, 0), (0, -1), "Helvetica-Bold", 10),
        ("FONT", (1, 0), (1, -1), "Helvetica", 10),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        # highlight critical row
        ("TEXTCOLOR", (1, 4), (1, 4), CRITICAL if critical else INK),
        ("FONT", (1, 4), (1, 4), "Helvetica-Bold", 10),
    ]))
    story.append(t)

    # ---- workflow topology ----
    story.append(Paragraph("Workflow topology", STY["h2"]))
    story.append(Paragraph(
        "The workflow below was executed on each record. Every accepted "
        "credential passed all nodes; any node marked 'flag' below routed "
        "the record to an officer for review before it could be anchored.",
        STY["body"]))
    nodes = wf.get("config", {}).get("nodes", [])
    if nodes:
        node_rows = [["Node id", "Capability", "Category"]]
        for n in nodes:
            sop = sop_for(n.get("cap"))
            node_rows.append([n.get("id", "—"), n.get("cap", "—"), sop.get("severity", "—")])
        nt = Table(node_rows, colWidths=[40*mm, 70*mm, 30*mm], hAlign="LEFT")
        nt.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e5eefc")),
            ("BOX", (0, 0), (-1, -1), 0.4, LINE),
            ("INNERGRID", (0, 0), (-1, -1), 0.2, LINE),
            ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
            ("FONT", (0, 1), (-1, -1), "Courier", 9),
            ("TEXTCOLOR", (0, 0), (-1, -1), INK2),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(nt)

    # ---- per-record flagged sections ----
    story.append(Paragraph("Flagged items · required officer action", STY["h2"]))
    if flagged == 0:
        story.append(Paragraph("No records were flagged in this run. All customers auto-issued.", STY["body"]))
    else:
        story.append(Paragraph(
            "Each flagged customer is listed below with the specific signal that failed, "
            "the applicable RBI / DPDP / PMLA citation, and the SOP-recommended next action. "
            "Items marked <b>CRITICAL</b> require immediate freeze plus STR filing with FIU-IND.",
            STY["body"]))
        for i, r in enumerate(runs, start=1):
            if r.get("ok"): continue
            flags = _flatten_flags(r)
            if not flags: continue
            rec = r.get("record", {})
            name = rec.get("name") or rec.get("pan") or f"Record {i}"
            block = [
                Paragraph(f"#{i}  ·  {name}", STY["h3"]),
                _record_meta_table(rec),
            ]
            for f in flags:
                block.append(_flag_row(f))
            story.append(KeepTogether(block))
            story.append(Spacer(1, 5))

    # ---- successful records (short summary) ----
    if passed:
        story.append(Paragraph("Auto-passed records", STY["h2"]))
        rows = [["#", "Customer", "PAN / GSTIN", "Nationality", "Address"]]
        for i, r in enumerate(runs, start=1):
            if not r.get("ok"): continue
            rec = r.get("record", {})
            rows.append([str(i), *_record_summary_row(rec)])
        pt = Table(rows, colWidths=[10*mm, 48*mm, 32*mm, 22*mm, 60*mm], hAlign="LEFT")
        pt.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dcf3e8")),
            ("BOX", (0, 0), (-1, -1), 0.4, LINE),
            ("INNERGRID", (0, 0), (-1, -1), 0.2, LINE),
            ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
            ("FONT", (0, 1), (-1, -1), "Helvetica", 8.5),
            ("TEXTCOLOR", (0, 0), (-1, -1), INK2),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(pt)

    # ---- anchor receipts ----
    anchored = [r for r in runs if r.get("anchor_receipt")]
    if anchored:
        story.append(Paragraph("On-chain anchors", STY["h2"]))
        arows = [["#", "Epoch", "Tx hash", "Gas", "Cost (USD)"]]
        for i, r in enumerate(anchored, start=1):
            ar = r["anchor_receipt"]
            arows.append([str(i), str(ar.get("epoch_id", "—")),
                          (ar.get("tx_hash", "") or "")[:26] + "…",
                          f"{ar.get('gas_used', 0):,}",
                          f"${ar.get('cost_usd', 0):.6f}"])
        at = Table(arows, colWidths=[10*mm, 30*mm, 82*mm, 20*mm, 25*mm], hAlign="LEFT")
        at.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e5eefc")),
            ("BOX", (0, 0), (-1, -1), 0.4, LINE),
            ("INNERGRID", (0, 0), (-1, -1), 0.2, LINE),
            ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
            ("FONT", (0, 1), (-1, -1), "Courier", 8),
            ("TEXTCOLOR", (0, 0), (-1, -1), INK2),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(at)

    # ---- footer / statement ----
    story.append(Spacer(1, 12))
    story.append(Paragraph(
        "<i>This report was generated automatically by the FinFabric console. "
        "The SOP citations are hand-curated against publicly published RBI, "
        "DPDP Act, PMLA and FATF guidance. It is not a substitute for a "
        "compliance officer's judgement.</i>", STY["small"]))

    doc.build(story)
    return buf.getvalue()


def _record_meta_table(rec: dict):
    """Small side-table with the record's declared PAN/GSTIN/Nationality."""
    def g(k, default="—"): return str(rec.get(k) or default)
    data = [
        ["Name", g("name")],
        ["PAN", g("pan")],
        ["GSTIN", g("gstin", "—")],
        ["Nationality", g("nationality")],
        ["KYC category", g("status", "—")],
        ["Address", g("address")[:80]],
    ]
    t = Table(data, colWidths=[28*mm, 132*mm], hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), BG2),
        ("BOX", (0, 0), (-1, -1), 0.3, LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.2, LINE),
        ("FONT", (0, 0), (0, -1), "Helvetica-Bold", 8.5),
        ("FONT", (1, 0), (1, -1), "Helvetica", 8.5),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK2),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


def _flag_row(flag: dict):
    """Two-column block: severity + title on the left, citation + action on the right."""
    sev = flag.get("severity", "MEDIUM")
    bg = _severity_bg(sev)
    left = Paragraph(
        f"<b>{sev}</b><br/><br/><font size=10>{flag.get('title', flag.get('cap'))}</font>",
        _severity_style(sev),
    )
    right_parts = [
        Paragraph(f"<b>Why:</b> {flag.get('when_fails', '')}", STY["body"]),
        Paragraph(f"<b>Signal reason:</b> {flag.get('detail', '')}", STY["body"]),
        Paragraph(f"<b>Citation:</b> <i>{flag.get('citation', '')}</i>", STY["body"]),
        Paragraph(f"<b>SOP action:</b> {flag.get('action', '')}", STY["body"]),
    ]
    right_cell = []
    for p in right_parts:
        right_cell.append(p)
    t = Table([[left, right_cell]], colWidths=[36*mm, 124*mm], hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), bg),
        ("BACKGROUND", (1, 0), (1, 0), colors.white),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOX", (0, 0), (-1, -1), 0.4, LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, LINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t
