"""
OSINT PDF Export Service — ShamrockLeads
========================================
Generates professional PDF summary reports for OSINT scans.
Uses fpdf2 for PDF generation (no external dependencies).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List


def generate_osint_pdf(doc: Dict[str, Any]) -> bytes:
    """Generate a professional OSINT PDF report from a scan document."""
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    # ── Header ────────────────────────────────────────────────────────────────
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(0, 100, 60)  # Shamrock green
    pdf.cell(0, 12, "OSINT Intelligence Report", ln=True, align="C")
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 5, "ShamrockLeads Intelligence Module | CONFIDENTIAL", ln=True, align="C")
    pdf.ln(8)

    # ── Subject Info ──────────────────────────────────────────────────────────
    pdf.set_draw_color(0, 100, 60)
    pdf.set_line_width(0.5)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(4)

    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 7, "Subject Information", ln=True)
    pdf.set_font("Helvetica", "", 10)

    _add_field(pdf, "Name", doc.get("full_name") or "Unknown")
    _add_field(pdf, "Type", (doc.get("subject_type") or "").capitalize())
    _add_field(pdf, "Subject ID", doc.get("subject_id", ""))
    _add_field(pdf, "Scan ID", doc.get("_id") or doc.get("scan_id", ""))
    pdf.ln(4)

    # ── Scan Summary ──────────────────────────────────────────────────────────
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 7, "Scan Summary", ln=True)
    pdf.set_font("Helvetica", "", 10)

    engines = doc.get("engines_requested") or []
    _add_field(pdf, "Engines", ", ".join(engines) if engines else "N/A")
    _add_field(pdf, "Status", (doc.get("status") or "").upper())
    _add_field(pdf, "Accounts Found", str(doc.get("total_accounts", 0)))
    _add_field(pdf, "Entities Found", str(doc.get("total_entities", 0)))
    _add_field(pdf, "Risk Score", f"{doc.get('osint_risk_score', 0)} / 100 (advisory)")
    _add_field(pdf, "Platforms", ", ".join(doc.get("platforms_found", [])[:20]) or "None")

    created = doc.get("created_at")
    if isinstance(created, str):
        _add_field(pdf, "Scan Date", created[:19])
    elif isinstance(created, datetime):
        _add_field(pdf, "Scan Date", created.strftime("%Y-%m-%d %H:%M UTC"))
    else:
        _add_field(pdf, "Scan Date", "Unknown")

    pdf.ln(4)

    # ── Engine Progress ───────────────────────────────────────────────────────
    progress = doc.get("progress") or {}
    if progress:
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 7, "Engine Results", ln=True)
        pdf.set_font("Helvetica", "", 9)

        for engine, info in progress.items():
            if not isinstance(info, dict):
                continue
            status = info.get("status", "unknown")
            accts = info.get("accounts_found", 0)
            ents = info.get("entities_found", 0)
            err = info.get("error")
            icon = "OK" if status == "completed" else ("FAIL" if status == "failed" else status.upper())
            line = f"  [{icon}] {engine.capitalize()}: {accts} accounts"
            if ents:
                line += f", {ents} entities"
            if err:
                line += f" | Error: {err[:60]}"
            pdf.cell(0, 5, line, ln=True)
        pdf.ln(4)

    # ── Accounts Table ────────────────────────────────────────────────────────
    accounts = doc.get("accounts") or []
    if accounts:
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 7, f"Discovered Accounts ({len(accounts)})", ln=True)
        pdf.ln(2)

        # Table header
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_fill_color(240, 240, 240)
        col_widths = [35, 30, 75, 25, 25]
        headers = ["Platform", "Username", "URL", "Source", "Confidence"]
        for i, h in enumerate(headers):
            pdf.cell(col_widths[i], 6, h, border=1, fill=True)
        pdf.ln()

        # Table rows (cap at 60 for readability)
        pdf.set_font("Helvetica", "", 7)
        for acct in accounts[:60]:
            platform = str(acct.get("platform", ""))[:20]
            username = str(acct.get("username", ""))[:18]
            url = str(acct.get("url", ""))[:45]
            source = str(acct.get("source", ""))[:14]
            conf = str(acct.get("confidence", ""))[:12]

            pdf.cell(col_widths[0], 5, platform, border=1)
            pdf.cell(col_widths[1], 5, username, border=1)
            pdf.cell(col_widths[2], 5, url, border=1)
            pdf.cell(col_widths[3], 5, source, border=1)
            pdf.cell(col_widths[4], 5, conf, border=1)
            pdf.ln()

        if len(accounts) > 60:
            pdf.set_font("Helvetica", "I", 8)
            pdf.cell(0, 5, f"... and {len(accounts) - 60} more accounts (see JSON export for full list)", ln=True)
        pdf.ln(4)

    # ── Entities Table ────────────────────────────────────────────────────────
    entities = doc.get("entities") or []
    if entities:
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 7, f"Discovered Entities ({len(entities)})", ln=True)
        pdf.ln(2)

        pdf.set_font("Helvetica", "B", 8)
        pdf.set_fill_color(240, 240, 240)
        col_widths_e = [25, 80, 30, 30, 25]
        headers_e = ["Type", "Value", "Source", "Module", "Confidence"]
        for i, h in enumerate(headers_e):
            pdf.cell(col_widths_e[i], 6, h, border=1, fill=True)
        pdf.ln()

        pdf.set_font("Helvetica", "", 7)
        for ent in entities[:40]:
            etype = str(ent.get("type", ""))[:14]
            value = str(ent.get("value", ""))[:48]
            source = str(ent.get("source", ""))[:16]
            module = str(ent.get("module", ""))[:18]
            conf = str(ent.get("confidence", ""))[:12]

            pdf.cell(col_widths_e[0], 5, etype, border=1)
            pdf.cell(col_widths_e[1], 5, value, border=1)
            pdf.cell(col_widths_e[2], 5, source, border=1)
            pdf.cell(col_widths_e[3], 5, module, border=1)
            pdf.cell(col_widths_e[4], 5, conf, border=1)
            pdf.ln()

        if len(entities) > 40:
            pdf.set_font("Helvetica", "I", 8)
            pdf.cell(0, 5, f"... and {len(entities) - 40} more entities", ln=True)
        pdf.ln(4)

    # ── Risk Signals ──────────────────────────────────────────────────────────
    signals = doc.get("risk_signals") or []
    if signals:
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 7, "Risk Signals", ln=True)
        pdf.set_font("Helvetica", "", 9)

        for sig in signals:
            severity = str(sig.get("severity", "")).upper()
            stype = sig.get("signal_type", "")
            detail = sig.get("detail", "")[:120]
            pdf.cell(0, 5, f"  [{severity}] {stype}: {detail}", ln=True)
        pdf.ln(4)

    # ── AI Summary ────────────────────────────────────────────────────────────
    ai_summary = doc.get("ai_summary")
    if ai_summary:
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 7, "AI Analysis Summary", ln=True)
        pdf.set_font("Helvetica", "", 9)
        pdf.multi_cell(0, 5, str(ai_summary)[:1000])
        pdf.ln(4)

    # ── Footer ────────────────────────────────────────────────────────────────
    pdf.ln(8)
    pdf.set_draw_color(0, 100, 60)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(4)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 4, "Generated by ShamrockLeads OSINT Intelligence Module", ln=True)
    pdf.cell(0, 4, "This report is for internal use only. Risk scores are ADVISORY and not auto-applied.", ln=True)
    pdf.cell(0, 4, f"Report generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}", ln=True)

    return pdf.output()


def _add_field(pdf, label: str, value: str) -> None:
    """Add a label: value line to the PDF."""
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(35, 5, f"{label}:")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 5, value[:100], ln=True)
