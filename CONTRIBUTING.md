# CONTRIBUTING.md — ShamrockLeads Development Guide

> **Read Order:** `BRAND.md` → `AGENTS.md` → `DATA_MODEL.md` → `ROADMAP.md` → this file.
> **Last Updated:** 2026-05-15

---

## Development Workflow

### 1. Branch Strategy

```
main                ← production (auto-deploys to Hetzner VPS)
├── feat/*          ← new features
├── fix/*           ← bug fixes
├── scraper/*       ← new county scrapers
└── docs/*          ← documentation updates
```

- All work happens on feature branches
- PRs merge into `main`
- GitHub Actions deploys to Hetzner on push to `main`

### 2. Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Run scraper engine locally
python main.py              # All counties
python main.py lee          # Single county
python main.py --dry-run    # Parse without writing

# Run dashboard locally
cd dashboard
hypercorn "dashboard:create_app()" --bind 0.0.0.0:5050 --access-logfile -
# → http://localhost:5050
```

### 3. Docker Development

```bash
# Full stack
docker compose up -d --build

# Dashboard only (faster iteration)
docker compose build --no-cache dashboard && docker compose up -d dashboard

# Scraper only
docker compose build --no-cache shamrock-leads && docker compose up -d shamrock-leads

# Logs
docker logs -f shamrock-dashboard --tail 50
docker logs -f shamrock-leads --tail 50

# Shell into container
docker exec -it shamrock-dashboard /bin/bash
```

---

## Code Conventions

### Python (Backend)

- **Style:** PEP 8, 4-space indent, 120-char max line length
- **Typing:** Type hints on all function signatures
- **Async:** All dashboard API endpoints are `async def`
- **Imports:** stdlib → third-party → local, alphabetized within each group
- **Logging:** `logger = logging.getLogger(__name__)` — never `print()`
- **Error handling:** Catch specific exceptions. Log with context. Never swallow errors silently.

### JavaScript (Frontend)

- **Pattern:** IIFE modules (`window.SLModuleName = (function() { ... })();`)
- **Naming:** `sl-module-name.js` file → `SLModuleName` global
- **No frameworks:** Vanilla JS only. No React, Vue, or jQuery.
- **DOM:** Use `document.getElementById()` with unique, descriptive IDs
- **Fetch:** Always use `try/catch` with `SL.toast()` for error feedback
- **Design tokens:** Use CSS custom properties (`var(--accent)`, `var(--bg-card)`) — never hardcode colors

### CSS

- **Architecture:** Design tokens in `sl-design-system.css` → base in `styles.css` → overhaul layer in `sl-overhaul.css`
- **Colors:** Dark theme. `#0f172a` background, `#10b981` accent, `#f59e0b` warning, `#ef4444` danger
- **Never hardcode colors** — always use CSS custom properties
- **Mobile-first:** Touch targets ≥ 44px. Input fields ≥ 16px (prevent iOS auto-zoom)

---

## Adding a County Scraper

See `.agent/skills/scraper-builder/SKILL.md` for the full workflow.

Quick steps:
1. Research the county's JMS vendor → `docs/COUNTY_REGISTRY.md`
2. Create `scrapers/counties/{county}.py` inheriting from `BaseScraper`
3. Implement `scrape_arrests()` → return `list[ArrestRecord]`
4. Register in `main.py` and `core/scheduler.py`
5. Test: `python main.py {county_name}`
6. Monitor 24h of production data
7. Update `docs/COUNTY_REGISTRY.md`

---

## Adding a Dashboard API Endpoint

1. Create or edit `dashboard/api/{module}.py`
2. Use Quart Blueprint pattern:
   ```python
   from quart import Blueprint, jsonify
   bp = Blueprint('module_name', __name__)
   
   @bp.route('/api/module/endpoint', methods=['GET'])
   async def get_something():
       db = current_app.config['db']
       # ... async MongoDB query via motor
       return jsonify(result)
   ```
3. Register blueprint in `dashboard/__init__.py`
4. Add frontend JS in corresponding `sl-*.js` module

---

## Adding a Frontend Module

1. Create `dashboard/sl-{name}.js` as an IIFE:
   ```javascript
   window.SLModuleName = (function() {
       async function init() { /* ... */ }
       return { init };
   })();
   ```
2. Add `<script src="/sl-{name}.js"></script>` to `dashboard/index.html`
3. Wire tab activation in `sl-core.js` `activateTab()` function
4. Use design tokens from `sl-design-system.css`

---

## Commit Message Format

```
type(scope): brief description

- Detail 1
- Detail 2
```

**Types:** `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `chore`, `scraper`

**Examples:**
```
feat(dashboard): add court countdown column to Active Bonds
fix(scraper): update Collier County base URL after site migration  
scraper(bay): add Bay County scraper (SmartCOP base)
docs(readme): update metrics and add Traccar to architecture
```

---

## Deployment to Production

### Automatic (GitHub Actions)
Push to `main` triggers the deploy workflow → SSH to Hetzner → `git pull` → `docker compose build` → `docker compose up -d`.

### Manual
```bash
ssh root@178.156.179.237 "cd /opt/shamrock-leads && git pull origin main && docker compose build --no-cache && docker compose up -d"
```

### Verify
```bash
# Check services
docker compose ps

# Check logs
docker logs shamrock-dashboard --tail 20
docker logs shamrock-leads --tail 20

# Health check
curl -s http://178.156.179.237:8088/health | python -m json.tool
```

---

## Testing

```bash
# Run single scraper test
python main.py lee --dry-run

# Test API endpoint
curl -s http://localhost:5050/api/stats | python -m json.tool

# Test iMessage health
curl -s http://localhost:5050/api/bb/health | python -m json.tool
```

---

## Non-Negotiable Rules

1. **No PII in logs** — Never log phone numbers, SSNs, or addresses
2. **No hardcoded secrets** — `.env` only
3. **No silent failures** — Every error fires a Slack alert
4. **No duplicate records** — Always check dedup before insert
5. **No broken chains** — ArrestLead → Defendant → Indemnitor → Match → BondCase → Packet → Signature → Payment
6. **No non-Shamrock resources** — `Shamrock2245` org, `admin@shamrockbailbonds.biz` email only
7. **Audit everything** — Every state change writes to `audit_events`

---

*Maintained by: Brendan / Shamrock Active Software LLC*
