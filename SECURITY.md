# SECURITY.md — ShamrockLeads Security Policy

---

## Secrets Management

### Where Secrets Live
| Context | Location |
|---------|----------|
| Local development | `.env` file (git-ignored) |
| Docker containers | `docker-compose.yml` `environment:` block referencing `.env` |
| Hetzner VPS | `.env` on server filesystem |
| GAS backend | Script Properties |
| Wix frontend | Wix Secrets Manager |

### Rules
- **Never commit secrets** to version control. `.env` is in `.gitignore`.
- **Never log secrets** in console output, Slack messages, or error reports.
- **Never hardcode secrets** in source files. Always use `os.environ` or `config/settings.py`.
- **Rotate immediately** if a secret is exposed in logs, commits, or messages.

---

## PII Protection

### What Is PII in This System
- Defendant names, DOB, addresses, mugshots
- Indemnitor names, phone numbers, email addresses, government ID
- Court case numbers (can identify individuals)
- Payment information

### PII Rules
1. **Minimize storage** — Only collect PII that is necessary for the workflow.
2. **Never log full PII** — Mask phone numbers (`+1239***1234`), redact addresses, truncate names in debug output.
3. **Slack alerts use partial data** — Hot lead alerts show county, bond amount, and lead score. Never full name + address + DOB together.
4. **Access control** — MongoDB Atlas requires authenticated connections. No anonymous access.
5. **Mugshot URLs** — These are public URLs from county jail rosters. Storing the URL is acceptable; downloading and re-hosting mugshots requires review.

---

## Network Security

### MongoDB Atlas
- Connection via TLS-encrypted `mongodb+srv://` URI
- Database users: `admin`, `shamrock_leads` (VPS), `shamrock_mac` (local)
- IP access list includes `0.0.0.0/0` (open) — consider restricting to VPS IP in production hardening

### Hetzner VPS
- SSH key-based authentication only
- Docker containers do not expose ports externally except dashboard (5050) and Node-RED (1880)
- Scraper traffic exits via VPS IP — no residential proxy currently

### Slack Webhooks
- Webhook URLs are secrets (stored in `.env`)
- Webhooks are write-only (no inbound data from Slack)

---

## Scraping Ethics & Legal

1. **Rate-limit all requests** — Minimum 1-second delay between requests to same host.
2. **Respect `robots.txt`** — Check before scraping new counties.
3. **Rotate User-Agent** — BaseScraper rotates UA strings to reduce fingerprinting.
4. **Public data only** — All scraped data is from publicly accessible jail rosters.
5. **No credential stuffing** — Only Hillsborough (HCSO) requires login credentials.
6. **No DDoS** — Auto-disable after 5 consecutive failures prevents runaway request loops.

---

## Incident Response

### If a Secret Is Exposed
1. Rotate the secret immediately
2. Check git history — if committed, use `git filter-branch` or BFG Repo Cleaner
3. Revoke old credentials (MongoDB user, Slack webhook, API key)
4. Audit access logs for unauthorized use

### If PII Is Leaked
1. Identify the scope (which records, which channels)
2. Delete the leaked content (Slack message, log file, etc.)
3. Assess whether notification is required
4. Document the incident

### If a Scraper Is Blocked
1. BaseScraper auto-disables after 5 failures
2. Check error classification in Slack alert
3. Follow `.agent/skills/scraper-debugger/SKILL.md`
4. Do not attempt to circumvent legal blocks (court orders, explicit takedowns)

---

## Compliance Notes

### Florida Statute 648 (Bail Bond Agents)
- All outreach to potential indemnitors must comply with F.S. 648
- No automated solicitation without human approval
- Human-in-the-loop requirement is enforced at the agent level (see AGENTS.md Rule 6)

### 10DLC (SMS Compliance)
- All SMS/WhatsApp messaging must use registered 10DLC campaigns
- Opt-out management required for any automated messaging
- Currently blocked pending carrier registration

### POA Reporting
- All used and voided POAs must be reported monthly to the respective surety
- OSI and Palmetto have separate reporting requirements
- POAInventory collection tracks `reported_at` for reconciliation
