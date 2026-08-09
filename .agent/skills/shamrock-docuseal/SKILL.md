---
name: shamrock-docuseal
description: >
  Shamrock Bail Bonds DocuSeal e-sign automation. Use for bond paperwork packets,
  multi-party sign links (indemnitor / co-indemnitor / defendant / bondsman), template
  ops via CLI against self-hosted sign.shamrockbailbonds.biz, CRM packet push, webhooks,
  Drive Completed Bonds archive, and SignNow→DocuSeal migration. Always load this skill
  (plus docuseal-cli / docuseal-code when shell or embed/API details are needed) before
  changing DocuSeal integration code or running `docuseal` against Shamrock production.
---

# Shamrock × DocuSeal

## When to use

- Bond packet e-sign (new bonds → DocuSeal only; SignNow is legacy)
- Template upload / clone / field audit via CLI
- Debugging submissions, submitters, sign links, webhooks
- Prefill / role mapping / surety template IDs
- Embedding or portal signing UX (prefer free `/s/{slug}` links over Pro embed)

## Stack (source of truth)

| Layer | Location |
|-------|----------|
| Self-hosted DocuSeal | Docker `docuseal` + `docuseal-postgres` (`compose --profile paperwork`) |
| Public host | `https://sign.shamrockbailbonds.biz` |
| Python client | `dashboard/services/docuseal_service.py` |
| Webhook | `POST /api/webhooks/docuseal` → Mongo + Drive Completed Bonds |
| Packet push | `POST /api/paperwork/{packet_id}/docuseal` |
| Health / templates | `GET /api/paperwork/docuseal/health`, `.../templates` |
| Prefill preview | `POST /api/paperwork/docuseal/prefill-preview` |
| Ops doc | `docs/PAPERWORK_PORTAL_DOCUSEAL.md` |
| Audit | `docs/DOCUSEAL_AUDIT_2026-08-07.md` |
| Official CLI skill | `.agent/skills/docuseal-cli/` (or `docuseal-cli` skill) |
| Official API/embed skill | `.agent/skills/docuseal-code/` (or `docuseal-code` skill) |

## CLI prerequisites

```bash
# One-time (Node 18+)
npm install -g docuseal   # or: npx docuseal …

# Point at SELF-HOSTED Shamrock (never cloud global for prod packets)
export DOCUSEAL_API_KEY="..."   # DocuSeal admin → API key
export DOCUSEAL_SERVER="https://sign.shamrockbailbonds.biz"

# Persist config (optional)
docuseal configure --api-key "$DOCUSEAL_API_KEY" --server "https://sign.shamrockbailbonds.biz"
docuseal configure --list
```

**Priority:** CLI flag > env > `~/.config/docuseal/credentials.json`.

**Rules for agents:**

1. Always pass full flags (CLI is non-interactive; no prompts).
2. Output is JSON — parse it; do not invent IDs.
3. Use `--server https://sign.shamrockbailbonds.biz` if env might be wrong.
4. Never log emails, phones, SSNs, addresses in Slack/console.
5. Prefer CRM HTTP (`docuseal_service` / paperwork routes) for bond packets so Mongo + audit + Drive stay in sync. Use CLI for ops: list templates, inspect fields, clone, archive, ad-hoc status checks.

## Role names (must match live template exactly)

Live OSI combined template (often id `1` / `DOCUSEAL_TEMPLATE_ID_OSI`):

| Role string | Party |
|-------------|--------|
| `indemnitor` | Primary indemnitor |
| `Coindemnitor` | Co-indemnitor |
| `Defendant` | Defendant |
| `Bondsman` | Optional; only if `DOCUSEAL_INCLUDE_BONDSMAN=true` |

Mismatch = failed submission. Confirm with:

```bash
docuseal templates retrieve 1 --server https://sign.shamrockbailbonds.biz
```

## Env vars

| Variable | Purpose |
|----------|---------|
| `DOCUSEAL_URL` | Public API/UI base (`https://sign.shamrockbailbonds.biz`) |
| `DOCUSEAL_INTERNAL_URL` | Optional Docker DNS (`http://docuseal:3000`) for dashboard→API |
| `DOCUSEAL_API_KEY` | REST `X-Auth-Token` (same key for CLI) |
| `DOCUSEAL_SERVER` | CLI only: full self-hosted URL |
| `DOCUSEAL_WEBHOOK_SECRET` | HMAC verify on webhook |
| `DOCUSEAL_TEMPLATE_ID` / `_OSI` / `_PALMETTO` | Surety template IDs |
| `DOCUSEAL_INCLUDE_BONDSMAN` | Include Bondsman submitter |
| `DEFAULT_ESIGN_PROVIDER` | `docuseal` for new packets |

Palmetto must set `DOCUSEAL_TEMPLATE_ID_PALMETTO` — no silent OSI fallback.

## Automation map: CRM vs CLI

| Task | Prefer | Why |
|------|--------|-----|
| Create bond packet submission with prefill | **CRM** `create_submission_for_packet` / `POST …/docuseal` | Roles, prefill, packet IDs, audit |
| Prefill from bond case | **CRM** `prefill_values_from_bond` / prefill-preview | Money words, charge grid, surety fields |
| Webhook complete → Drive archive | **CRM** webhook + `docuseal_poller` | Completed Bonds folder tree |
| List / inspect templates | **CRM** `GET …/docuseal/templates[/{id}]` or **CLI** | Field audit vs prefill keys |
| Refresh packet sign status | **CRM** `GET …/{packet_id}/docuseal/status` | Syncs Mongo submitters + links |
| Re-send / fix submitter email | **CRM** `POST …/{packet_id}/docuseal/resend` or **CLI** | Packet-linked resend preferred |
| List pending submissions | **CRM** `GET …/docuseal/submissions?status=pending` or **CLI** | Ops chase |
| Clone / archive template | **Service** `clone_template` / `archive_template` or **CLI** | Ops |
| Download signed PDF ad-hoc | **CLI** `submissions documents <id> --merge` | Ops; prod path uses webhook→Drive |
| Create template from PDF/DOCX/HTML | **CLI** `templates create-*` | Marked **Pro** on cloud; self-host may differ — test once |

### Bond packet via CRM (correct production path)

```text
Validated match + BondCase + surety + POA
  → paperwork packet record
  → POST /api/paperwork/{packet_id}/docuseal
  → DocuSeal multi-submitter submission (send_email false by default)
  → store docuseal_submission_id + per-party sign links (/s/{slug})
  → portal / iMessage / SMS handoff of links
  → webhook form.completed / submission.completed
  → Mongo status + Drive Completed Bonds/{surety}/{defendant_date}/
```

### Ad-hoc multi-party create via CLI (ops / dry-run only)

```bash
docuseal submissions create \
  --server https://sign.shamrockbailbonds.biz \
  --template-id 1 \
  --no-send-email \
  -d 'submitters[0][role]=indemnitor' \
  -d 'submitters[0][email]=party1@example.com' \
  -d 'submitters[0][values][defendant_name]=DOE, JOHN' \
  -d 'submitters[1][role]=Defendant' \
  -d 'submitters[1][email]=party2@example.com'
```

Do **not** use CLI create for live bonds unless the agent also writes packet IDs and audit events — prefer the dashboard service.

## Safety (non-negotiable)

1. **No paperwork before validated case** — defendant, indemnitor, match, bond case, surety, POA.
2. **Fail closed** on ambiguous identity or wrong surety template.
3. **No sending links to unvalidated parties.**
4. **Surety-aware** — OSI vs Palmetto template IDs never mixed.
5. **Audit** status changes (webhook path already does this).
6. **PII sacred** — no phones/SSNs/addresses in agent logs or Slack.
7. Self-hosted OSS: prefer **sign links** (`/s/{slug}`), not paid Pro embed JWT path.

## Agent playbooks

### A. Health check

```bash
docuseal configure --list
docuseal templates list --server https://sign.shamrockbailbonds.biz -l 20
# or CRM:
curl -sS -H "Cookie: …" https://leads.shamrockbailbonds.biz/api/paperwork/docuseal/health
```

### B. Template field audit (before changing prefill keys)

```bash
docuseal templates retrieve "$TEMPLATE_ID" --server https://sign.shamrockbailbonds.biz
# Compare field names to keys in prefill_values_from_bond()
```

### C. Stuck / pending signatures

**Prefer CRM (keeps Mongo in sync):**
```bash
# Session cookie / dashboard PIN auth required
curl -sS -b cookies.txt "https://leads.shamrockbailbonds.biz/api/paperwork/PACKET_ID/docuseal/status"
curl -sS -b cookies.txt -X POST -H 'Content-Type: application/json' \
  -d '{"role":"indemnitor","send_email":true}' \
  "https://leads.shamrockbailbonds.biz/api/paperwork/PACKET_ID/docuseal/resend"
```

**CLI (ops / no packet context):**
```bash
docuseal submissions list --status pending --server https://sign.shamrockbailbonds.biz -l 50
docuseal submissions retrieve "$SUBMISSION_ID" --server https://sign.shamrockbailbonds.biz
docuseal submitters update "$SUBMITTER_ID" --send-email --server https://sign.shamrockbailbonds.biz
```
### D. Upload / refresh combined packet PDF as template

```bash
# If create-pdf available on this instance:
docuseal templates create-pdf \
  --file ./path/to/osi-combined.pdf \
  --name "shamrock-osi-paperwork-complete" \
  --server https://sign.shamrockbailbonds.biz
# Then map roles in DocuSeal UI; set DOCUSEAL_TEMPLATE_ID_OSI to new id
```

### E. Code changes

1. Load **docuseal-code** for API schemas / webhooks / embed.
2. Edit only `docuseal_service.py`, paperwork routes, webhooks — keep chain of custody.
3. Run `tests/test_docuseal_service.py`.
4. Do not remove SignNow paths until historical packets are retired (M4).

## Common mistakes (Shamrock-specific)

| Mistake | Fix |
|---------|-----|
| CLI pointed at `global` / `api.docuseal.com` | Set `DOCUSEAL_SERVER=https://sign.shamrockbailbonds.biz` |
| Role `Indemnitor` vs `indemnitor` | Match live template strings exactly |
| Creating submission only in CLI for a real bond | Use paperwork API so packet + Mongo update |
| Forcing email send for jail defendant | Use `--no-send-email` + portal/link handoff |
| Palmetto using OSI template | Require `DOCUSEAL_TEMPLATE_ID_PALMETTO` |
| Assuming create-pdf works without Pro | Test on self-host; UI upload is fallback |

## Related skills

- `docuseal-cli` — full CLI flags, `-d` notation, Pro notes
- `docuseal-code` — REST, webhooks, embed JWT, SDK examples
- `pdf-processing` — local PDF prep before template upload
- `gws-drive` — Completed Bonds Drive archive
- `surety-compliance-auditor` — surety/POA compliance
- `soc2-compliance-auditor` — audit / PII
