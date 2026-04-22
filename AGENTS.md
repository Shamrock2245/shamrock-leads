# 🤖 ShamrockLeads — Agent Handbook

> **Last Updated:** April 22, 2026
> **Repo:** `Shamrock2245/shamrock-leads`
> **Mission:** Scrape every arrest in every Florida county. Score every lead. Find every family.

---

## 1. What This Repo Does

ShamrockLeads is a **statewide arrest intelligence platform** that:

1. **Scrapes** all 67 Florida county jail rosters on scheduled intervals
2. **Normalizes** arrest data into a 39-column `ArrestRecord` schema
3. **Scores** every arrestee as a bail bond lead (0–100, Hot/Warm/Cold/Disqualified)
4. **Discovers** contact information for the arrestee's family/friends (indemnitor leads)
5. **Alerts** bondsmen via Slack with real-time hot lead notifications
6. **Stores** everything in MongoDB Atlas with Google Sheets as a legacy fallback

### Pipeline Flow

```
County Jail Roster → Scraper → ArrestRecord → Lead Scorer → Writer(s) → Slack Alert
                                                  ↓
                                         Contact Discovery
                                              ↓
                                      Family/Friend Name + Phone
                                              ↓
                                        Outreach Queue
```

---

## 2. Digital Workforce

| Agent | Role | Where It Runs | Key File |
|-------|------|---------------|----------|
| **The Clerk** | Jail roster parsing, HTML→JSON, anti-bot evasion | `scrapers/counties/*.py` | `base_scraper.py` |
| **The Analyst** | Lead scoring (0–100), risk classification | `scoring/lead_scorer.py` | `lead_scorer.py` |
| **The Finder** | OSINT: family/friend contact discovery | `discovery/contact_finder.py` | *(Phase 4)* |
| **The Closer** | Outreach sequencing: SMS/WhatsApp drip | `outreach/drip_engine.py` | *(Phase 6)* |
| **The Watchdog** | Scraper health monitoring, failure alerts | `writers/slack_notifier.py` | `slack_notifier.py` |

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Hetzner VPS (Docker)                  │
│                                                         │
│  ┌──────────────────────┐  ┌──────────────────────────┐ │
│  │   shamrock-leads     │  │       node-red            │ │
│  │   (Python 3.12)      │  │   (Ops Dashboard)         │ │
│  │                      │  │                            │ │
│  │  APScheduler         │  │  7 dashboard pages         │ │
│  │    ↓                 │  │  39+ cron queries          │ │
│  │  67 County Scrapers  │  │  Slack relay               │ │
│  │    ↓                 │  │                            │ │
│  │  Lead Scorer         │  └─────────┬────────────────┘ │
│  │    ↓                 │            │                   │
│  │  Writers             │            │ HTTP              │
│  │   ├── MongoDB        │            │                   │
│  │   ├── Google Sheets  │            ▼                   │
│  │   └── Slack          │  ┌──────────────────────────┐ │
│  └──────────────────────┘  │  Netlify API             │ │
│                            │  (REST endpoints)         │ │
│                            │  GET /api/arrests          │ │
│                            │  GET /api/leads            │ │
│                            │  GET /api/stats            │ │
│                            └──────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
                       │
              ┌────────┴────────┐
              │  MongoDB Atlas  │
              │  (Central DB)   │
              └─────────────────┘
```

---

## 4. The Scoring System ("Underwriting")

Every arrest is scored 0–100 with classification:

| Score | Status | Action |
|-------|--------|--------|
| 80+ | 🔥 **Hot** | Immediate Slack alert + outreach queue |
| 50–79 | 🟡 **Warm** | Logged, low-priority follow-up |
| 30–49 | ❄️ **Cold** | Stored only, no action |
| <30 | ❌ **Disqualified** | No bond / released / $0 bond |

### Scoring Factors

| Factor | Signal | Points |
|--------|--------|--------|
| Bond Amount | $500–$999 | +30 |
| Bond Amount | $1,000–$4,999 | +40 |
| Bond Amount | $5,000+ | +50 |
| Arrest Recency | <24 hours | +20 |
| Arrest Recency | <48 hours | +10 |
| Charge Severity | Felony keywords | +20 |
| Charge Severity | DUI/Battery/Domestic | +15 |
| Local Resident | Florida address | +20 |
| Disqualifier | "Released" / "ROR" | → 0 (Disqualified) |
| Disqualifier | Bond = $0 | → 0 (Disqualified) |

---

## 5. Contact Discovery ("The Finder")

> ⚠️ **Phase 4 — Not yet implemented. Legal review pending.**

The Finder uses public records to identify potential indemnitors:

### Data Sources (Public)
- FL voter registration records (name, address, household)
- Property records (county appraiser databases)
- Court records (co-defendants, prior cases)
- Social media profiles (public Facebook, LinkedIn)
- Emergency contact inference (shared addresses)

### Output Schema
```json
{
  "defendant_name": "John Smith",
  "booking_number": "2026-123456",
  "county": "Lee",
  "potential_contacts": [
    {
      "name": "Jane Smith",
      "relationship": "Spouse (inferred - same address)",
      "phone": "+1239XXXXXXX",
      "source": "voter_registration",
      "confidence": 0.85
    }
  ]
}
```

### Compliance Rules
- **No solicitation** until Florida Statute 648 compliance is verified
- All data must be from **publicly available** sources
- Contacts are **surfaced to human bondsman** — never auto-contacted
- PII is never logged to Slack or public channels

---

## 6. County JMS Vendors

Florida counties use different Jail Management Systems. Understanding the vendor determines the scraping strategy:

| JMS Vendor | Counties Using | Scraper Pattern |
|------------|---------------|-----------------|
| **Odyssey (Tyler)** | Lee, Collier, Charlotte, Sarasota | REST API + charge enrichment |
| **New World (Tyler)** | Manatee, Hillsborough | HTML table parsing |
| **JailTracker** | Hendry, DeSoto, Glades | Paginated HTML + CAPTCHA |
| **Superion (CentralSquare)** | Various small counties | XML/SOAP endpoints |
| **Custom/In-House** | Many rural counties | HTTP GET + regex extraction |

See `docs/COUNTY_REGISTRY.md` for the full 67-county breakdown.

---

## 7. Prime Directives

1. **Scrape Respectfully** — Rate-limit all requests. Respect `robots.txt`. Rotate user agents. Never DDoS a county server.
2. **Idempotent Writes** — `Booking_Number` + `County` is the dedup key. Every write checks before inserting.
3. **Score Everything** — No record enters the database without a lead score. Even $0 bonds get scored (as Disqualified).
4. **Fail Loudly** — Every scraper error fires a Slack alert. Silent failures are unacceptable.
5. **Human-in-the-Loop for Outreach** — No automated client contact without explicit human approval.
6. **PII is Sacred** — Never log phone numbers, SSNs, or addresses to Slack or console in production.

---

## 8. Development Patterns

### Adding a New County Scraper
See `.agent/workflows/add-county-scraper.md` for the full workflow.

### Debugging a Broken Scraper
See `.agent/skills/scraper-debugger/SKILL.md` for the systematic approach.

### Tuning Lead Scores
See `.agent/skills/lead-scoring-tuning/SKILL.md` for weight adjustment procedures.

---

## 9. Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `MONGODB_URI` | ✅ | MongoDB Atlas connection string |
| `MONGODB_DB_NAME` | ✅ | Database name (default: `shamrock_leads`) |
| `GOOGLE_SPREADSHEET_ID` | Optional | Legacy Sheets writer |
| `GOOGLE_APPLICATION_CREDENTIALS` | Optional | GCP service account JSON path |
| `SLACK_WEBHOOK_ARRESTS` | ✅ | #new-arrests channel |
| `SLACK_WEBHOOK_LEADS` | ✅ | #leads channel (hot leads) |
| `SLACK_WEBHOOK_ERRORS` | ✅ | #scraper-errors channel |
| `OPENAI_API_KEY` | Optional | AI-powered contact discovery |
| `SCRAPER_LOG_LEVEL` | Optional | `DEBUG`, `INFO`, `WARNING` |
| `SCRAPER_MAX_CONCURRENT` | Optional | Max parallel scrapers |

---

## 10. Deployment

```bash
# Build and deploy to Hetzner
docker-compose build
docker-compose up -d

# Check health
docker-compose ps
docker logs shamrock-leads --tail 50
docker logs shamrock-node-red --tail 50

# Run one-shot for a specific county
docker exec shamrock-leads python main.py --county lee --once
```
