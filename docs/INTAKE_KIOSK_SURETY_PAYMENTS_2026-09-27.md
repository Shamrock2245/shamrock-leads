# Intake, kiosk, surety & payment changes — 2026-09-27

Branch `fix/kiosk-and-wix-intake` (LOCAL, not deployed). Companion branch:
`shamrock-bail-portal-site` → `fix/wix-to-leads-intake`.

## 1. Website applications → CRM first, then Sheets + Slack

**Owner decision:** Wix applications go to the CRM first. MongoDB
`intake_queue` is the source of truth. After a successful save the CRM copies
the application to Google Sheets (one "Intake Ledger" row per intake, for
reporting / data visualisation) and posts a Slack notice.

```
Wix wizard → Wix backend leadsIntake.jsw
   → POST /api/webhooks/wix-intake   (header X-Wix-Webhook-Secret)
       1. constant-time secret check (fails closed if WIX_WEBHOOK_SECRET unset)
       2. wix_wizard_adapter: nested wizard JSON → flat intake; role, form_type
       3. idempotency on clientNonce (intake id WX-<sha256[:12]>) → duplicate:true
       4. _normalize_intake(source="wix_webhook") — NO county/state/surety defaults
       5. insert into intake_queue   ← if this fails: 500 {success:false}
       6. MatchingEngine auto-match (non-fatal)
       7. intake_fanout.schedule_after_save(intake)   ← fire-and-forget
       8. 200 {success:true, intake_id, duplicate, form_type, matched, payment_link}
```

### Fan-out (dashboard/services/intake_fanout.py)
- Runs **after** the Mongo write, in the background. Any error is caught and
  logged; the intake response never waits for or depends on it.
- One outbox row per (intake_id, target) in `intake_fanout_outbox`,
  targets `sheets` and `slack`. Status:
  `pending → in_flight → sent | failed (retry) | skipped_unconfigured | dead`.
- Retries: cron job `intake_fanout_retry` (every 5 min, `dashboard/cron.py`),
  exponential backoff, `MAX_ATTEMPTS = 10`, then `dead` + error log.
  Rows are claimed atomically, so two workers never send the same row.
- Sheets: reuses the existing GAS web app (`GAS_WEB_APP_URL` + `GAS_API_KEY`)
  with the new action `appendIntakeLedger` (portal-site
  `backend-gas/IntakeLedger.js`). Idempotent per `intake_id`; tab
  "Intake Ledger" in `INTAKE_LEDGER_SHEET_ID` (Script Property) or the main
  `CONFIG.SHEET_ID`.
- Slack: reuses `automation_digest.post_slack` with `SLACK_WEBHOOK_INTAKE`
  (falls back to `SLACK_WEBHOOK_LEADS`). Message has a CRM link.
- Ledger row PII is minimal: names, county, booking #, bond amount, surety,
  phone **last 4**, has_email, match info. No SSN, DOB, DL # or street address.
- Kill switch: `INTAKE_FANOUT_DISABLED=1` (rows stay `pending` and are sent
  when it is switched back).
- Inspect: `db.intake_fanout_outbox.find({status:{$in:["failed","dead"]}})`.
  To resend a dead row set `status:"pending", attempts:0, next_attempt_at:now`.

## 2. Surety registry (dashboard/services/surety_registry.py)
| Surety | State |
|---|---|
| OSI | ✅ active (DocuSeal template `DOCUSEAL_TEMPLATE_ID_OSI` / `DOCUSEAL_TEMPLATE_ID`) |
| Palmetto | ✅ active (`DOCUSEAL_TEMPLATE_ID_PALMETTO`) |
| Lexington National, Roche, Universal, Bankers | ⏸ inactive — shown greyed "coming soon" in Write Bond, fail closed |

- Unknown or inactive surety → `UnsupportedSuretyError` (codes
  `surety_required`, `surety_inactive`, `unsupported_surety`). The old silent
  "fallback to OSI" paths (bond PDFs, paperwork PDFs, DocuSeal template
  resolve, packet builder, Drive archive, PIN-portal deferred intake,
  indemnitor Drive upload) are gone.
- A website intake without a surety is fine; staff pick it at Write Bond.
- `GET /api/paperwork/sureties` feeds the picker.
- **Activating a surety:** the 6-step checklist at the top of
  `surety_registry.py` (DocuSeal template → env var → POA prefixes/inventory →
  Drive label → local blanks → flip `active` + test + deploy).

## 3. Pay-by-card links (dashboard/services/payment_links.py)
Every source gets pay-by-card. A case's own SwipeSimple invoice link wins;
otherwise Telegram → `lnk_07a13eb404d7f3057a56d56d8bb488c8`, every other
source → `lnk_b6bf996f4c57bb340a150e297e769abd`. Override without code:
`SWIPESIMPLE_LINK_TELEGRAM`, `SWIPESIMPLE_LINK_DEFAULT` (legacy
`SWIPESIMPLE_PAYMENT_LINK` / `SWIPESIMPLE_BOND_PAYMENT_LINK` / `PAYMENT_LINK`
still honoured). `GET /api/paperwork/payment-links` shows what is in effect.
Only resolves URLs — send gates (`SWIPESIMPLE_DISPATCH_LIVE`, etc.) unchanged.

## 4. Kiosk / in-office tablet (routers/pin_portal.py)
- Launch per signer: `/sign/{packet_id}/{role}?mode=kiosk` (role =
  defendant, indemnitor, coindemnitor). Defendants can sign at the kiosk.
- ID scan is **preview-only** (`/api/portal/kiosk-id-ocr` returns fields + a
  15-min signed `scan_token`). Nothing is saved until the signer confirms
  (`POST /api/portal/kiosk-id-confirm`), in order: name/address → phone → email.
- The scan is applied to **that role only**: a co-indemnitor scan goes to
  `coindemnitor_fields` and never overwrites the primary indemnitor. For a
  defendant, the booking name wins; a different scanned name is kept as
  `defendant_name_scanned` for staff review.
- Privacy: no applicant data in localStorage (in-memory only); after 3 min idle
  a "Still there?" prompt, 30 s later the screen wipes and returns to the
  neutral `/kiosk` page; after signing, `/done?kiosk=1` resets in 20 s.
- Public (phone) portal: the ID scan waits for PIN verification, then uses the
  PIN session.
- Recommended iPad setup: Safari → `/kiosk`, Guided Access on, staff start
  each signer from the CRM.
- Needs `SECRET_KEY` set on the VPS (signs `scan_token`).

## 5. Go-live checklist (owner approval required)
1. VPS `.env`: `WIX_WEBHOOK_SECRET` (new), `SLACK_WEBHOOK_INTAKE` (optional),
   confirm `GAS_WEB_APP_URL`, `GAS_API_KEY`, `SECRET_KEY`,
   `DOCUSEAL_TEMPLATE_ID_PALMETTO`.
2. Merge + deploy leads to Hetzner.
3. Wix Secrets Manager: `WIX_WEBHOOK_SECRET` (same value).
4. GAS: clasp push + new deployment version of the same `/exec` (IntakeLedger.js
   + Code.js routing); optional Script Property `INTAKE_LEDGER_SHEET_ID`.
5. Portal-site merge (auto-publish; check the wix.config.json UI pin first),
   re-paste wizard embeds, Editor removals for the retired staff portal.
6. One test submission; confirm intake_queue, ledger row, Slack; archive it.
