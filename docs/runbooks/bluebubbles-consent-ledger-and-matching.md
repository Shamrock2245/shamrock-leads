# BlueBubbles (0178) — consent ledger, send gate, thread matching

> Added with the "BB batch A" PR. Do not merge/deploy without Brendan's OK.

## What changed for staff

| Area | Behaviour |
|------|-----------|
| **Webhook admin** | `POST /api/webhooks/bluebubbles/register` and `DELETE /api/webhooks/bluebubbles/{id}` need a staff session or `X-API-Key` / `X-Internal-Token` (GAS_API_KEY / LEADS_INTERNAL_TOKEN). The inbound receiver `POST /api/webhooks/bluebubbles` is unchanged and stays open (BlueBubbles sends no signature). Boot-time registration is server-side (`dashboard/cron.py` → `ensure_webhook`) and needs nothing new. |
| **Inbound matching** | A text to 0178 is matched against active `prospective_bonds` (indemnitor / co-indemnitor / defendant phone), non-closed `active_bonds` (indemnitor, co-indemnitors `indemnitors[]`, defendant) and open `intake_queue` rows. One person on one booking = one party. If the number belongs to **more than one party/case**, the message is attached to **all** of them (`matches[]`, `booking_numbers[]`) and flagged `needs_staff_review=true`, `ambiguous_match=true`; no case is picked and the agent brain does **not** auto-reply. |
| **Agent brain** | Unchanged scope: still only for an unambiguous active prospective bond matched on `indemnitor.phone`. |
| **iMac-sent texts** | `isFromMe` webhook events are stored as `direction="outbound"` (after a ~20 s delay so a CRM send logs its own row first; deduped by BB GUID, tempGuid, content hash, and same-text CRM row). Never triggers auto-reply or STOP handling. No historical backfill. |
| **STOP / START** | STOP, STOPALL/"stop all", UNSUBSCRIBE, CANCEL, END, QUIT, OPTOUT/"opt out" → `sms_consent_ledger` event `opt_out` (phone, time, BB message GUID, keyword). START / UNSTOP (whole message) → `opt_in`. A message that *starts with* a stop word ("Stop texting me") is also an opt-out but is flagged `needs_staff_review`. |
| **Send gate** | Every BlueBubbles send (dashboard, agent brain, reminders, queue retries, DocuSeal signing links, attachments, tapbacks, BB-scheduled messages at scheduling time) checks the ledger first. Opted-out → **blocked**, nothing sent/queued, result `{"blocked": true, "reason": "recipient_opted_out", "purpose": ...}`; log line `[consent_gate] BLOCKED send to ...NNNN reason=recipient_opted_out purpose=...`. |
| **DocuSeal links** | Staff "deliver" and the initial-delivery automation are allowed for everyone who has **not** opted out. Only the opted-out signer is skipped (desk shows 409 `recipient_opted_out` / recipient `state=blocked`). |

## Re-enabling a number

The recipient texts **START** (or UNSTOP) to 0178. Staff cannot override consent from the CRM in this release.

## Pre-existing opt-outs

Numbers that texted STOP before this release have no ledger row; the gate falls back to the existing opt-out log in `imessage_outreach` (`category="opt_out"`) and honours it (read-only, no backfill).

## Env (all optional)

| Env | Default | Meaning |
|-----|---------|---------|
| `BB_OUTBOUND_INGEST_DELAY_SECONDS` | `20` | Delay before storing a webhook `isFromMe` message (`0` = immediate). |
| `BB_OPTOUT_GATE_FAIL_CLOSED` | off | If the ledger lookup errors (Mongo down): off = allow + log error; `true` = block with `reason=consent_check_unavailable`. |

## Rollback

Revert the PR. Ledger rows in `sms_consent_ledger` are inert without the code; `imessage_outreach` extra fields (`matches`, `booking_numbers`, `needs_staff_review`, …) are additive.
