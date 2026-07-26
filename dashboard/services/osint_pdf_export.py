"""
OSINT PDF Export Service — ShamrockLeads
========================================
Generates executive-grade PDF summary reports for OSINT scans.
Uses fpdf2 for PDF generation.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List


class ShamrockOSINTPDF:
    """Custom FPDF subclass with executive headers, footers, and page numbers."""

    def __init__(self, doc: Dict[str, Any]):
        from fpdf import FPDF
        super().__init__()
        self._doc = doc

    def header(self):
        # Top Shamrock Bar
        self.set_fill_color(0, 100, 60)  # Shamrock green
        self.rect(0, 0, 210, 14, fill="F")
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(255, 255, 255)
        self.set_xy(10, 3)
        self.cell(0, 8, "SHAMROCK BAIL BONDS — OSINT INTELLIGENCE DOSSIER", ln=False)

    def footer(self):
        self.set_y(-15)
        self.set_draw_color(200, 200, 200)
        self.line(10, self.get_y(), 200, self.get_y())
        self.set_y(-12)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 6, f"CONFIDENTIAL & PROPRIETARY — Page {self.page_no()}", align="C")


def generate_osint_pdf(doc: Dict[str, Any]) -> bytes:
    """Generate a high-impact, professional OSINT PDF report from a scan document."""
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()

    # Title & Subheader
    pdf.ln(8)
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(0, 10, "OSINT Intelligence Report", ln=True)

    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(100, 116, 139)
    created = doc.get("created_at")
    created_str = created[:19] if isinstance(created, str) else (created.strftime("%Y-%m-%d %H:%M UTC") if isinstance(created, datetime) else "Unknown")
    pdf.cell(0, 5, f"Scan ID: {doc.get('_id') or doc.get('scan_id', 'N/A')}  |  Generated: {created_str}", ln=True)
    pdf.ln(4)

    # ── Executive Overview Card ────────────────────────────────────────────────
    pdf.set_fill_color(248, 250, 252)
    pdf.set_draw_color(226, 232, 240)
    pdf.rect(10, pdf.get_y(), 190, 36, style="DF")

    start_y = pdf.get_y() + 4

    # Subject details
    pdf.set_xy(14, start_y)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(32, 5, "Subject Name:")
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(51, 65, 85)
    pdf.cell(50, 5, (doc.get("full_name") or "Unknown Subject")[:30])

    # Status
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(24, 5, "Status:")
    status = (doc.get("status") or "unknown").upper()
    pdf.set_font("Helvetica", "B", 10)
    if status == "COMPLETED":
        pdf.set_text_color(16, 185, 129)
    elif status == "FAILED":
        pdf.set_text_color(239, 68, 68)
    else:
        pdf.set_text_color(245, 158, 11)
    pdf.cell(40, 5, status, ln=True)

    # Row 2
    pdf.set_xy(14, start_y + 7)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(32, 5, "Subject Type:")
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(51, 65, 85)
    pdf.cell(50, 5, (doc.get("subject_type") or "defendant").capitalize())

    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(24, 5, "Engines:")
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(51, 65, 85)
    engines_str = ", ".join(doc.get("engines_requested") or []) or "None"
    pdf.cell(60, 5, engines_str[:35], ln=True)

    # Row 3
    pdf.set_xy(14, start_y + 14)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(32, 5, "Accounts Found:")
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(0, 100, 60)
    pdf.cell(50, 5, str(doc.get("total_accounts", 0)))

    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(24, 5, "Entities:")
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(0, 100, 60)
    pdf.cell(40, 5, str(doc.get("total_entities", 0)), ln=True)

    # Row 4
    pdf.set_xy(14, start_y + 21)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(32, 5, "Risk Score:")
    risk = int(doc.get("osint_risk_score", 0))
    pdf.set_font("Helvetica", "B", 10)
    if risk >= 35:
        pdf.set_text_color(220, 38, 38)
    elif risk >= 15:
        pdf.set_text_color(217, 119, 6)
    else:
        pdf.set_text_color(16, 185, 129)
    pdf.cell(50, 5, f"+{risk} (Advisory)")

    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(24, 5, "Subject ID:")
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(71, 85, 105)
    pdf.cell(60, 5, str(doc.get("subject_id", ""))[:30], ln=True)

    pdf.set_y(start_y + 34)
    pdf.ln(6)

    # ── AI Summary Box ────────────────────────────────────────────────────────
    ai_summary = doc.get("ai_summary")
    if ai_summary:
        pdf.set_fill_color(243, 232, 255)  # Soft purple
        pdf.set_draw_color(216, 180, 254)
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(126, 34, 206)
        pdf.cell(0, 7, "  AI Intelligence Summary", ln=True, fill=True)

        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(51, 65, 85)
        pdf.set_fill_color(250, 245, 255)
        clean_ai = str(ai_summary).replace("\n", " ").strip()
        pdf.multi_cell(0, 5, f"  {clean_ai[:950]}", fill=True, border=1)
        pdf.ln(6)

    # ── Discovered Accounts Table ─────────────────────────────────────────────
    accounts = doc.get("accounts") or []
    if accounts:
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(15, 23, 42)
        pdf.cell(0, 7, f"Discovered Social & Web Accounts ({len(accounts)})", ln=True)
        pdf.ln(2)

        # Table Header
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_fill_color(0, 100, 60)
        pdf.set_text_color(255, 255, 255)
        col_w = [35, 35, 80, 22, 18]
        headers = ["Platform", "Username", "Profile / Link URL", "Source", "Status"]
        for i, h in enumerate(headers):
            pdf.cell(col_w[i], 6, h, border=1, fill=True, align="C" if i in [3, 4] else "L")
        pdf.ln()

        # Rows
        pdf.set_font("Helvetica", "", 8)
        for idx, acct in enumerate(accounts[:60]):
            bg = (248, 250, 252) if idx % 2 == 0 else (255, 255, 255)
            pdf.set_fill_color(*bg)
            pdf.set_text_color(51, 65, 85)

            platform = str(acct.get("platform", ""))[:20]
            username = str(acct.get("username", ""))[:20]
            url = str(acct.get("url", ""))
            source = str(acct.get("source", ""))[:12]
            conf = str(acct.get("confidence", "found"))[:10]

            pdf.cell(col_w[0], 5.5, platform, border=1, fill=True)
            pdf.cell(col_w[1], 5.5, username, border=1, fill=True)

            # URL column with clickable hyperlink
            url_display = url[:48] if len(url) > 48 else url
            pdf.set_text_color(37, 99, 235)  # Blue link
            pdf.cell(col_w[2], 5.5, url_display, border=1, fill=True, link=url if url.startswith("http") else None)
            pdf.set_text_color(51, 65, 85)

            pdf.cell(col_w[3], 5.5, source, border=1, fill=True, align="C")
            pdf.cell(col_w[4], 5.5, conf, border=1, fill=True, align="C")
            pdf.ln()

        if len(accounts) > 60:
            pdf.set_font("Helvetica", "I", 8)
            pdf.set_text_color(100, 116, 139)
            pdf.cell(0, 5, f"... and {len(accounts) - 60} more accounts (see CSV export for complete list)", ln=True)
        pdf.ln(6)

    # ── Discovered Entities Table ─────────────────────────────────────────────
    entities = doc.get("entities") or []
    if entities:
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(15, 23, 42)
        pdf.cell(0, 7, f"Discovered Entities & Intelligence Assets ({len(entities)})", ln=True)
        pdf.ln(2)

        pdf.set_font("Helvetica", "B", 8)
        pdf.set_fill_color(30, 41, 59)  # Dark slate
        pdf.set_text_color(255, 255, 255)
        col_we = [28, 85, 28, 30, 19]
        headers_e = ["Type", "Value / Asset", "Source", "Module", "Confidence"]
        for i, h in enumerate(headers_e):
            pdf.cell(col_we[i], 6, h, border=1, fill=True, align="C" if i in [3, 4] else "L")
        pdf.ln()

        pdf.set_font("Helvetica", "", 8)
        for idx, ent in enumerate(entities[:40]):
            bg = (248, 250, 252) if idx % 2 == 0 else (255, 255, 255)
            pdf.set_fill_color(*bg)
            pdf.set_text_color(51, 65, 85)

            etype = str(ent.get("type", ""))[:16]
            val = str(ent.get("value", ""))[:52]
            source = str(ent.get("source", ""))[:14]
            module = str(ent.get("module", ""))[:16]
            conf = str(ent.get("confidence", "medium"))[:10]

            pdf.cell(col_we[0], 5.5, etype, border=1, fill=True)
            pdf.cell(col_we[1], 5.5, val, border=1, fill=True)
            pdf.cell(col_we[2], 5.5, source, border=1, fill=True)
            pdf.cell(col_we[3], 5.5, module, border=1, fill=True, align="C")
            pdf.cell(col_we[4], 5.5, conf, border=1, fill=True, align="C")
            pdf.ln()

        if len(entities) > 40:
            pdf.set_font("Helvetica", "I", 8)
            pdf.set_text_color(100, 116, 139)
            pdf.cell(0, 5, f"... and {len(entities) - 40} more entities", ln=True)
        pdf.ln(6)

    # ── Risk Signals ──────────────────────────────────────────────────────────
    signals = doc.get("risk_signals") or []
    if signals:
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(15, 23, 42)
        pdf.cell(0, 7, "Identified Risk Signals", ln=True)
        pdf.ln(2)

        pdf.set_font("Helvetica", "", 9)
        for sig in signals:
            sev = str(sig.get("severity", "low")).upper()
            stype = sig.get("signal_type", "")
            detail = sig.get("detail", "")[:120]

            if sev in ["HIGH", "CRITICAL"]:
                pdf.set_text_color(220, 38, 38)
            elif sev == "MEDIUM":
                pdf.set_text_color(217, 119, 6)
            else:
                pdf.set_text_color(16, 185, 129)

            pdf.cell(20, 5, f"[{sev}]", ln=False)
            pdf.set_text_color(51, 65, 85)
            pdf.cell(0, 5, f"{stype}: {detail}", ln=True)
        pdf.ln(4)

    # Footer note
    pdf.ln(6)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(148, 163, 184)
    pdf.cell(0, 4, "Generated by ShamrockLeads OSINT Intelligence Engine | Confidentially issued for internal bail underwriting.", ln=True, align="C")

    return pdf.output()
