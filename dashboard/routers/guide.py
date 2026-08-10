"""
ShamrockLeads — Ecosystem Master Operations Manual Router
Endpoints: /guide (HTML viewer), /api/guide (JSON data)
"""

import os
import logging
from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse

logger = logging.getLogger(__name__)

guide_bp = APIRouter(tags=["guide"])

MANUAL_PATH = Path(__file__).resolve().parent.parent.parent / "docs" / "ECOSYSTEM_OPERATIONS_MANUAL.md"

def _load_manual_markdown() -> str:
    if MANUAL_PATH.exists():
        return MANUAL_PATH.read_text(encoding="utf-8")
    return "# Shamrock Ecosystem Operations Manual\n\nManual file not found."

@guide_bp.get("/api/guide")
async def api_get_guide():
    """Return raw markdown content of the Ecosystem Operations Manual."""
    content = _load_manual_markdown()
    return JSONResponse({"success": True, "content": content})

@guide_bp.get("/guide", response_class=HTMLResponse)
async def get_guide_html():
    """Render full HTML Ecosystem Operations Manual page."""
    md_content = _load_manual_markdown()
    
    # Clean HTML escaping for embedding into JS string
    md_escaped = md_content.replace("\\", "\\\\").replace("`", "\\`").replace("$", "\\$")
    
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Shamrock Ecosystem Master Operations Manual</title>
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=Inter:wght@400;500;600;700&display=swap">
  <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
  <style>
    :root {{
      --bg: #0b1220;
      --panel: rgba(15, 23, 42, 0.92);
      --border: rgba(148, 163, 184, 0.14);
      --text: #cbd5e1;
      --heading: #f8fafc;
      --accent: #10b981;
      --accent-hover: #059669;
      --muted: #64748b;
      --code-bg: #0f172a;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: 'Inter', 'Space Grotesk', -apple-system, BlinkMacSystemFont, sans-serif;
      background:
        radial-gradient(1000px 420px at 10% -10%, rgba(16,185,129,0.12), transparent 50%),
        radial-gradient(800px 360px at 100% 0%, rgba(14,165,233,0.08), transparent 45%),
        var(--bg);
      color: var(--text);
      line-height: 1.65;
      display: flex;
      min-height: 100vh;
    }}
    header {{
      position: fixed;
      top: 0; left: 0; right: 0; height: 64px;
      background: rgba(11, 18, 32, 0.85);
      backdrop-filter: blur(14px);
      -webkit-backdrop-filter: blur(14px);
      border-bottom: 1px solid var(--border);
      display: flex; align-items: center; justify-content: space-between;
      padding: 0 28px; z-index: 100;
    }}
    .brand {{
      display: flex; align-items: center; gap: 12px;
      font-family: 'Space Grotesk', sans-serif;
      font-weight: 700; font-size: 17px; color: var(--heading);
      letter-spacing: -0.02em;
    }}
    .brand span {{ color: #34d399; }}
    .header-actions {{ display: flex; gap: 10px; }}
    .btn {{
      background: linear-gradient(135deg, #10b981, #059669); color: #022c22; border: none;
      padding: 9px 16px; border-radius: 10px; font-weight: 700;
      cursor: pointer; transition: transform 0.12s, box-shadow 0.15s; font-size: 13px;
      text-decoration: none; display: inline-flex; align-items: center; gap: 6px;
      box-shadow: 0 8px 20px rgba(16,185,129,0.22);
    }}
    .btn:hover {{ transform: translateY(-1px); box-shadow: 0 10px 24px rgba(16,185,129,0.3); }}
    .btn-secondary {{
      background: rgba(30,41,59,0.9); color: var(--text);
      border: 1px solid var(--border); box-shadow: none;
    }}
    .btn-secondary:hover {{ background: #1e293b; transform: none; }}

    main {{
      margin-top: 64px;
      display: flex; width: 100%;
    }}
    sidebar {{
      width: 280px; position: fixed; top: 64px; bottom: 0; left: 0;
      background: rgba(15, 23, 42, 0.7);
      backdrop-filter: blur(10px);
      border-right: 1px solid var(--border);
      padding: 24px 16px; overflow-y: auto;
    }}
    sidebar h3 {{
      font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em;
      color: var(--muted); margin-bottom: 14px; padding: 0 10px;
      font-weight: 700;
    }}
    sidebar ul {{ list-style: none; }}
    sidebar li {{ margin-bottom: 4px; }}
    sidebar a {{
      color: #94a3b8; text-decoration: none; font-size: 13.5px;
      display: block; padding: 9px 12px; border-radius: 10px;
      transition: background 0.15s, color 0.15s, border-color 0.15s;
      border: 1px solid transparent;
    }}
    sidebar a:hover {{ background: rgba(30,41,59,0.8); color: var(--heading); border-color: var(--border); }}

    .content-wrap {{
      margin-left: 280px; padding: 48px 56px 80px; max-width: 900px; flex: 1;
    }}
    .markdown-body {{
      background: rgba(15, 23, 42, 0.55);
      border: 1px solid var(--border);
      border-radius: 18px;
      padding: 36px 40px;
      box-shadow: 0 24px 60px rgba(0,0,0,0.28);
    }}
    .markdown-body h1 {{ font-family:'Space Grotesk',sans-serif; font-size: 30px; letter-spacing:-0.03em; color: var(--heading); border-bottom: 1px solid var(--border); padding-bottom: 14px; margin-bottom: 24px; }}
    .markdown-body h2 {{ font-family:'Space Grotesk',sans-serif; font-size: 21px; letter-spacing:-0.02em; color: var(--heading); margin-top: 40px; margin-bottom: 14px; border-bottom: 1px solid var(--border); padding-bottom: 10px; }}
    .markdown-body h3 {{ font-size: 18px; color: var(--heading); margin-top: 24px; margin-bottom: 12px; }}
    .markdown-body p, .markdown-body ul, .markdown-body ol {{ margin-bottom: 16px; font-size: 15px; color: var(--text); }}
    .markdown-body ul, .markdown-body ol {{ padding-left: 24px; }}
    .markdown-body table {{
      width: 100%; border-collapse: collapse; margin-bottom: 24px; margin-top: 12px;
    }}
    .markdown-body th, .markdown-body td {{
      border: 1px solid var(--border); padding: 10px 14px; font-size: 14px; text-align: left;
    }}
    .markdown-body th {{ background: rgba(30,41,59,0.9); color: var(--heading); font-weight: 600; }}
    .markdown-body code {{
      background: var(--code-bg); padding: 2px 7px; border-radius: 6px; font-family: ui-monospace, monospace; font-size: 12.5px; color: #86efac; border: 1px solid rgba(148,163,184,0.12);
    }}
    .markdown-body pre {{
      background: var(--code-bg); padding: 16px; border-radius: 8px; overflow-x: auto; margin-bottom: 20px; border: 1px solid var(--border);
    }}
    .markdown-body pre code {{ background: transparent; padding: 0; color: var(--text); }}
    .markdown-body blockquote {{
      border-left: 4px solid var(--accent); padding-left: 16px; color: var(--muted); margin-bottom: 20px; font-style: italic; background: rgba(35,134,54,0.05); padding-top: 8px; padding-bottom: 8px; border-radius: 0 4px 4px 0;
    }}

    @media (max-width: 900px) {{
      sidebar {{ display: none; }}
      .content-wrap {{ margin-left: 0; padding: 24px 16px 48px; }}
      .markdown-body {{ padding: 22px 18px; border-radius: 14px; }}
    }}
    @media print {{
      header, sidebar {{ display: none !important; }}
      .content-wrap {{ margin-left: 0 !important; padding: 0 !important; width: 100% !important; max-width: 100% !important; }}
      body {{ background: white !important; color: black !important; }}
      .markdown-body h1, .markdown-body h2, .markdown-body h3 {{ color: black !important; border-color: #ccc !important; }}
      .markdown-body th {{ background: #f0f0f0 !important; color: black !important; }}
      .markdown-body table, .markdown-body th, .markdown-body td {{ border-color: #ccc !important; }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="brand">
      ☘️ <span>Shamrock Bail Bonds</span> Ecosystem Manual
    </div>
    <div class="header-actions">
      <a href="/" class="btn btn-secondary">⬅ Back to Dashboard</a>
      <button class="btn" onclick="window.print()">🖨 Print / Save PDF</button>
    </div>
  </header>

  <main>
    <sidebar>
      <h3>Table of Contents</h3>
      <ul id="tocList">
        <li><a href="#quick-start-first-day-on-the-job">Quick start</a></li>
        <li><a href="#chapter-1--leads-dashboard-shamrockleads">1. Leads dashboard</a></li>
        <li><a href="#chapter-2--how-to-write-a-bond-step-by-step">2. How to write a bond</a></li>
        <li><a href="#chapter-3--paperwork--docuseal-for-office-staff">3. Paperwork &amp; DocuSeal</a></li>
        <li><a href="#chapter-4--active-bonds-kanban">4. Active bonds</a></li>
        <li><a href="#chapter-5--bail-school-website-for-staff">5. Bail School</a></li>
        <li><a href="#chapter-6--social-media-postiz">6. Social (Postiz)</a></li>
        <li><a href="#chapter-7--imessage--shannon-ai-texting">7. iMessage &amp; Shannon</a></li>
        <li><a href="#chapter-8--support--escalations">8. Support</a></li>
        <li><a href="#appendix--one-page-write-bond-cheat-sheet">Write Bond cheat sheet</a></li>
      </ul>
    </sidebar>

    <div class="content-wrap">
      <div id="mdRender" class="markdown-body"></div>
    </div>
  </main>

  <script>
    const rawMd = `{md_escaped}`;
    const root = document.getElementById('mdRender');
    root.innerHTML = marked.parse(rawMd);
    // Add IDs to headings so sidebar TOC works (any marked version)
    const slugify = (t) => String(t || '')
      .toLowerCase()
      .replace(/<[^>]+>/g, '')
      .replace(/[^\\w\\s-]/g, '')
      .trim()
      .replace(/\\s+/g, '-');
    root.querySelectorAll('h1,h2,h3').forEach((el) => {{
      if (!el.id) el.id = slugify(el.textContent);
    }});
  </script>
</body>
</html>
"""
    return HTMLResponse(content=html)
