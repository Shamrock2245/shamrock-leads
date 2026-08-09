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
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap">
  <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
  <style>
    :root {{
      --bg: #0d1117;
      --panel: #161b22;
      --border: #30363d;
      --text: #c9d1d9;
      --heading: #ffffff;
      --accent: #238636;
      --accent-hover: #2ea043;
      --muted: #8b949e;
      --code-bg: #21262d;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.6;
      display: flex;
      min-height: 100vh;
    }}
    header {{
      position: fixed;
      top: 0; left: 0; right: 0; height: 60px;
      background: var(--panel);
      border-bottom: 1px solid var(--border);
      display: flex; align-items: center; justify-content: space-between;
      padding: 0 24px; z-index: 100;
    }}
    .brand {{
      display: flex; align-items: center; gap: 12px;
      font-weight: 700; font-size: 18px; color: var(--heading);
    }}
    .brand span {{ color: #3fb950; }}
    .header-actions {{ display: flex; gap: 12px; }}
    .btn {{
      background: var(--accent); color: white; border: none;
      padding: 8px 16px; border-radius: 6px; font-weight: 600;
      cursor: pointer; transition: background 0.2s; font-size: 14px;
      text-decoration: none; display: inline-flex; align-items: center; gap: 6px;
    }}
    .btn:hover {{ background: var(--accent-hover); }}
    .btn-secondary {{ background: #21262d; color: var(--text); border: 1px solid var(--border); }}
    .btn-secondary:hover {{ background: #30363d; }}

    main {{
      margin-top: 60px;
      display: flex; width: 100%;
    }}
    sidebar {{
      width: 280px; position: fixed; top: 60px; bottom: 0; left: 0;
      background: var(--panel); border-right: 1px solid var(--border);
      padding: 20px; overflow-y: auto;
    }}
    sidebar h3 {{
      font-size: 12px; text-transform: uppercase; letter-spacing: 1px;
      color: var(--muted); margin-bottom: 12px;
    }}
    sidebar ul {{ list-style: none; }}
    sidebar li {{ margin-bottom: 8px; }}
    sidebar a {{
      color: var(--text); text-decoration: none; font-size: 14px;
      display: block; padding: 6px 10px; border-radius: 4px; transition: background 0.15s;
    }}
    sidebar a:hover {{ background: #21262d; color: var(--heading); }}

    .content-wrap {{
      margin-left: 280px; padding: 40px 60px; max-width: 960px; flex: 1;
    }}
    .markdown-body h1 {{ font-size: 28px; color: var(--heading); border-bottom: 1px solid var(--border); padding-bottom: 12px; margin-bottom: 24px; }}
    .markdown-body h2 {{ font-size: 22px; color: var(--heading); margin-top: 36px; margin-bottom: 16px; border-bottom: 1px solid var(--border); padding-bottom: 8px; }}
    .markdown-body h3 {{ font-size: 18px; color: var(--heading); margin-top: 24px; margin-bottom: 12px; }}
    .markdown-body p, .markdown-body ul, .markdown-body ol {{ margin-bottom: 16px; font-size: 15px; color: var(--text); }}
    .markdown-body ul, .markdown-body ol {{ padding-left: 24px; }}
    .markdown-body table {{
      width: 100%; border-collapse: collapse; margin-bottom: 24px; margin-top: 12px;
    }}
    .markdown-body th, .markdown-body td {{
      border: 1px solid var(--border); padding: 10px 14px; font-size: 14px; text-align: left;
    }}
    .markdown-body th {{ background: var(--panel); color: var(--heading); font-weight: 600; }}
    .markdown-body code {{
      background: var(--code-bg); padding: 2px 6px; border-radius: 4px; font-family: monospace; font-size: 13px; color: #79c0ff;
    }}
    .markdown-body pre {{
      background: var(--code-bg); padding: 16px; border-radius: 8px; overflow-x: auto; margin-bottom: 20px; border: 1px solid var(--border);
    }}
    .markdown-body pre code {{ background: transparent; padding: 0; color: var(--text); }}
    .markdown-body blockquote {{
      border-left: 4px solid var(--accent); padding-left: 16px; color: var(--muted); margin-bottom: 20px; font-style: italic; background: rgba(35,134,54,0.05); padding-top: 8px; padding-bottom: 8px; border-radius: 0 4px 4px 0;
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
