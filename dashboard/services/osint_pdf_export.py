"""
OSINT PDF Export Service — ShamrockLeads
========================================
Renders the recovery-agent dossier from ``build_osint_report``.

Layout (letter-size, portrait):
  Page 1  case header · search inputs · KPI strip · recovery leads · next steps
  Then    accounts by category · other entities · risk signals · engine log

fpdf2 core fonts are Latin-1 only. Every string goes through ``_t`` so an emoji
or em dash in a scraped profile cannot crash the export.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from dashboard.services.osint_report import build_osint_report

GREEN = (0, 100, 60)
GREEN_SOFT = (232, 245, 238)
INK = (15, 23, 42)
BODY = (51, 65, 85)
MUTED = (100, 116, 139)
LINE = (226, 232, 240)
ZEBRA = (248, 250, 252)
LINK = (29, 78, 216)
RED = (185, 28, 28)
AMBER = (180, 83, 9)
OK = (21, 128, 61)

_REPLACE = {
    "\u2014": "-", "\u2013": "-", "\u2012": "-", "\u2212": "-",
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201c": '"', "\u201d": '"',
    "\u2026": "...", "\u2022": "*", "\u00b7": "-", "\u2192": "->", "\u2190": "<-",
    "\u00a0": " ", "\u200b": "", "\u2122": "(TM)",
}


def _t(value: Any, limit: Optional[int] = None) -> str:
    s = "" if value is None else str(value)
    for k, v in _REPLACE.items():
        s = s.replace(k, v)
    s = s.encode("latin-1", "ignore").decode("latin-1")
    s = " ".join(s.split())
    if limit and len(s) > limit:
        s = s[: max(0, limit - 3)] + "..."
    return s


def _make_pdf(report: Dict[str, Any]):
    from fpdf import FPDF

    header = report["header"]

    class DossierPDF(FPDF):
        def header(self):  # noqa: D401 - fpdf hook
            self.set_fill_color(*GREEN)
            self.rect(0, 0, self.w, 12, style="F")
            self.set_xy(10, 3)
            self.set_font("Helvetica", "B", 9)
            self.set_text_color(255, 255, 255)
            self.cell(0, 6, "SHAMROCK BAIL BONDS  |  OSINT RECOVERY DOSSIER  |  CONFIDENTIAL")
            self.set_xy(-80, 3)
            self.set_font("Helvetica", "", 8)
            self.cell(70, 6, _t(header["subject_name"], 40), align="R")
            self.set_y(16)

        def footer(self):  # noqa: D401 - fpdf hook
            self.set_y(-13)
            self.set_draw_color(*LINE)
            self.line(10, self.get_y(), self.w - 10, self.get_y())
            self.set_font("Helvetica", "I", 7)
            self.set_text_color(*MUTED)
            self.cell(0, 6, _t(f"Scan {header['scan_id']}  -  Leads require verification before any action  -  "
                               f"Internal use only"), align="L")
            self.set_x(-30)
            self.cell(20, 6, f"Page {self.page_no()}/{{nb}}", align="R")

    pdf = DossierPDF(orientation="P", unit="mm", format="Letter")
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.set_margins(10, 16, 10)
    return pdf


def _section(pdf, title: str, count: Optional[int] = None) -> None:
    if pdf.get_y() > pdf.h - 40:
        pdf.add_page()
    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(*GREEN)
    label = title if count is None else f"{title} ({count})"
    pdf.cell(0, 7, _t(label), new_x="LMARGIN", new_y="NEXT")
    pdf.set_draw_color(*GREEN)
    pdf.set_line_width(0.4)
    pdf.line(10, pdf.get_y(), pdf.w - 10, pdf.get_y())
    pdf.set_line_width(0.2)
    pdf.ln(2)


def _empty(pdf, text: str) -> None:
    pdf.set_font("Helvetica", "I", 8.5)
    pdf.set_text_color(*MUTED)
    pdf.cell(0, 5, _t(text), new_x="LMARGIN", new_y="NEXT")


def _table(
    pdf,
    headings: Sequence[str],
    rows: List[Sequence[Any]],
    widths: Sequence[float],
    *,
    links: Optional[List[Dict[int, str]]] = None,
    limits: Optional[Sequence[int]] = None,
    font_size: float = 7.8,
) -> None:
    """Wrapped, page-breaking table with optional per-cell hyperlinks."""
    from fpdf.fonts import FontFace

    head_style = FontFace(emphasis="BOLD", color=(255, 255, 255), fill_color=INK)
    pdf.set_font("Helvetica", "", font_size)
    pdf.set_text_color(*BODY)
    pdf.set_draw_color(*LINE)
    with pdf.table(
        col_widths=tuple(widths),
        width=sum(widths),
        align="LEFT",
        headings_style=head_style,
        line_height=font_size * 0.55,
        cell_fill_color=ZEBRA,
        cell_fill_mode="ROWS",
        text_align="LEFT",
        borders_layout="HORIZONTAL_LINES",
    ) as table:
        hrow = table.row()
        for h in headings:
            hrow.cell(_t(h))
        for r_idx, row in enumerate(rows):
            trow = table.row()
            row_links = (links[r_idx] if links and r_idx < len(links) else {}) or {}
            for c_idx, val in enumerate(row):
                lim = limits[c_idx] if limits and c_idx < len(limits) else None
                link = row_links.get(c_idx)
                if link:
                    trow.cell(_t(val, lim), link=link, style=FontFace(color=LINK, emphasis="UNDERLINE"))
                else:
                    trow.cell(_t(val, lim))
    pdf.ln(1)


def generate_osint_pdf(doc: Dict[str, Any]) -> bytes:
    """Build the recovery dossier PDF for one stored scan document."""
    report = build_osint_report(doc)
    h = report["header"]
    k = report["kpis"]
    leads = report["leads"]

    pdf = _make_pdf(report)
    pdf.add_page()
    page_w = pdf.w - 20

    # ── Title block ───────────────────────────────────────────────────────────
    pdf.set_font("Helvetica", "B", 17)
    pdf.set_text_color(*INK)
    pdf.cell(0, 9, _t(h["subject_name"], 60), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 8.5)
    pdf.set_text_color(*MUTED)
    meta = [h["subject_type"]]
    if h["subject_id"]:
        meta.append(f"Case/Subject ID {h['subject_id']}")
    meta.append(f"Scanned {h['created_at'] or 'n/a'}")
    if h["requested_by"]:
        meta.append(f"by {h['requested_by']}")
    pdf.cell(0, 5, _t("  |  ".join(meta)), new_x="LMARGIN", new_y="NEXT")

    status_color = {"COMPLETED": OK, "FAILED": RED}.get(h["status"], AMBER)
    pdf.set_font("Helvetica", "B", 8.5)
    pdf.set_text_color(*status_color)
    pdf.cell(0, 5, _t(f"Scan status: {h['status']}"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    # ── KPI strip ─────────────────────────────────────────────────────────────
    tiles = [
        ("Locations", k["locations"]), ("Contact points", k["contacts"]),
        ("Accounts", k["accounts"]), ("Platforms", k["platforms"]),
        ("Entities", k["entities"]), ("Risk (advisory)", f"+{k['risk_score']}"),
    ]
    tile_w = page_w / len(tiles)
    y0 = pdf.get_y()
    for i, (label, val) in enumerate(tiles):
        x = 10 + i * tile_w
        pdf.set_fill_color(*GREEN_SOFT)
        pdf.set_draw_color(*LINE)
        pdf.rect(x + 0.6, y0, tile_w - 1.2, 15, style="DF")
        pdf.set_xy(x, y0 + 1.6)
        pdf.set_font("Helvetica", "B", 13)
        pdf.set_text_color(*GREEN)
        pdf.cell(tile_w, 6, _t(val), align="C")
        pdf.set_xy(x, y0 + 8.5)
        pdf.set_font("Helvetica", "", 7)
        pdf.set_text_color(*MUTED)
        pdf.cell(tile_w, 4, _t(label), align="C")
    pdf.set_y(y0 + 18)

    # ── Search inputs ─────────────────────────────────────────────────────────
    _section(pdf, "What was searched")
    _table(pdf, ["Input", "Value"], report["inputs"], [40, page_w - 40], limits=[30, 140])
    if h["engines"]:
        pdf.set_font("Helvetica", "", 7.5)
        pdf.set_text_color(*MUTED)
        pdf.multi_cell(0, 4, _t("Engines: " + ", ".join(h["engines"])), new_x="LMARGIN", new_y="NEXT")
    if h["notes"]:
        pdf.set_font("Helvetica", "I", 7.5)
        pdf.multi_cell(0, 4, _t("Notes: " + h["notes"], 400), new_x="LMARGIN", new_y="NEXT")

    if report["ai_summary"]:
        _section(pdf, "AI summary")
        pdf.set_font("Helvetica", "", 8.5)
        pdf.set_text_color(*BODY)
        pdf.multi_cell(0, 4.6, _t(report["ai_summary"], 1600), new_x="LMARGIN", new_y="NEXT")

    # ── Recovery leads ────────────────────────────────────────────────────────
    _section(pdf, "Recovery leads: locations", len(leads["locations"]))
    if leads["locations"]:
        rows = [[l["value"], l["detail"], l["source"], "Open map"] for l in leads["locations"]]
        links = [{3: l["map_url"]} for l in leads["locations"]]
        _table(pdf, ["Location", "Detail", "Source", "Map"], rows,
               [78, 62, 25, page_w - 165], links=links, limits=[120, 90, 18, 10])
    else:
        _empty(pdf, "No locations found. ExifTool (photo GPS), GHunt (Maps reviews) and SpiderFoot can add them.")

    _section(pdf, "Recovery leads: phones & emails", len(leads["contacts"]))
    if leads["contacts"]:
        rows = [[c["type"].upper(), c["value"], c["new"], c["source"], c["context"]] for c in leads["contacts"]]
        _table(pdf, ["Type", "Value", "Status", "Source", "Context"], rows,
               [16, 62, 20, 25, page_w - 123], limits=[6, 60, 10, 16, 90])
    else:
        _empty(pdf, "No phone numbers or emails surfaced beyond what was searched.")

    _section(pdf, "Recovery leads: linked handles", len(leads["handles"]))
    if leads["handles"]:
        rows = [[f"@{x['handle']}", x["platform_count"], x["new"], x["platforms"]] for x in leads["handles"][:40]]
        _table(pdf, ["Handle", "Sites", "Status", "Platforms"], rows,
               [48, 14, 20, page_w - 82], limits=[40, 4, 10, 120])
    else:
        _empty(pdf, "No usernames confirmed.")

    if leads["vehicle"]:
        _section(pdf, "Vehicle: Flock LE search audit logs (HIBF)", len(leads["vehicle"]))
        rows = [[v["value"], v["context"]] for v in leads["vehicle"]]
        _table(pdf, ["Finding", "Detail"], rows, [70, page_w - 70], limits=[80, 140])
        _empty(pdf, "These are public records of law-enforcement plate searches, not live camera sightings.")

    if leads["breaches"]:
        _section(pdf, "Breach & dump sources (h8mail)", len(leads["breaches"]))
        rows = [[b["value"], b["source"], b["context"]] for b in leads["breaches"]]
        _table(pdf, ["Breach / source", "Engine", "Context"], rows,
               [80, 22, page_w - 102], limits=[90, 14, 100])

    # ── Next steps ────────────────────────────────────────────────────────────
    _section(pdf, "Suggested next steps")
    if report["next_steps"]:
        pdf.set_font("Helvetica", "", 8.5)
        pdf.set_text_color(*BODY)
        for i, step in enumerate(report["next_steps"], 1):
            pdf.multi_cell(0, 4.6, _t(f"{i}. {step}"), new_x="LMARGIN", new_y="NEXT")
    else:
        _empty(pdf, "No follow-up suggested.")

    # ── Accounts by category ──────────────────────────────────────────────────
    total_acc = sum(len(rows) for _, rows in report["accounts_by_category"])
    _section(pdf, "Accounts found, by category", total_acc)
    if not report["accounts_by_category"]:
        _empty(pdf, "No accounts found.")
    shown = 0
    for cat, accs in report["accounts_by_category"]:
        if shown >= 150:
            break
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*INK)
        pdf.cell(0, 6, _t(f"{cat.capitalize()} ({len(accs)})"), new_x="LMARGIN", new_y="NEXT")
        take = accs[: max(0, 150 - shown)]
        rows = [[a["platform"], a["username"], a["url"], a["confidence"], a["source"]] for a in take]
        links = [{2: a["url"]} if a["url"].startswith("http") else {} for a in take]
        _table(pdf, ["Platform", "Username", "Profile link", "Confidence", "Engine"], rows,
               [32, 32, page_w - 108, 22, 22], links=links, limits=[24, 24, 80, 10, 12])
        shown += len(take)
    if total_acc > shown:
        _empty(pdf, f"{total_acc - shown} more accounts are in the CSV export.")

    if report["other_entities"]:
        _section(pdf, "Other findings", len(report["other_entities"]))
        rows = [[e["type"], e["value"], e["source"], e["context"]] for e in report["other_entities"][:80]]
        _table(pdf, ["Type", "Value", "Engine", "Context"], rows,
               [24, 78, 22, page_w - 124], limits=[16, 90, 14, 80])

    # ── Risk + engine log ─────────────────────────────────────────────────────
    _section(pdf, "Risk signals (advisory only)", len(report["signals"]))
    if report["signals"]:
        rows = [[s["severity"], s["type"], s["detail"]] for s in report["signals"]]
        _table(pdf, ["Severity", "Signal", "Detail"], rows, [20, 50, page_w - 70], limits=[10, 40, 220])
    else:
        _empty(pdf, "No risk signals.")

    _section(pdf, "Engine run log")
    if report["engine_log"]:
        rows = [[e["engine"], e["status"], e["found"], e["note"] or "-"] for e in report["engine_log"]]
        _table(pdf, ["Engine", "Status", "Found", "Notes"], rows,
               [62, 22, 14, page_w - 98], limits=[40, 10, 6, 160])
    for w in report["warnings"][:12]:
        pdf.set_font("Helvetica", "", 7.5)
        pdf.set_text_color(*AMBER)
        pdf.multi_cell(0, 4, _t("Warning: " + w, 300), new_x="LMARGIN", new_y="NEXT")
    if report["hidden_irrelevant"]:
        _empty(pdf, f"{report['hidden_irrelevant']} findings marked irrelevant by staff are left out of this report.")

    pdf.ln(4)
    pdf.set_font("Helvetica", "I", 7.5)
    pdf.set_text_color(*MUTED)
    pdf.multi_cell(
        0, 4,
        _t("These findings come from public sources and automated matching. A matching username is not proof "
           "of identity. Verify every lead before acting on it. OSINT risk is advisory and is never applied to "
           f"bond risk automatically. Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} by "
           "ShamrockLeads."),
        new_x="LMARGIN", new_y="NEXT",
    )
    return bytes(pdf.output())
