# Signature Policy

> **Status:** `[ACTIVE — Enforced from Phase 7]`

---

## Purpose

This policy governs the generation, delivery, and tracking of signature packets via SignNow. Paperwork is surety-specific — the template set depends on which surety (OSI or Palmetto) is backing the bond.

---

## Core Rules

### Rule 1: Packet Binding

Every document packet must be bound to exactly one `Bond_Case_ID`. A packet must reference:
- `Bond_Case_ID`
- `Defendant_ID`
- `Indemnitor_ID`
- `Surety_ID`
- `POA_Number`
- `Case_Number`

**If any of these are missing or mismatched, the packet must not be generated.**

### Rule 2: Template Selection by Surety

| Surety | Template Set | Notes |
|--------|-------------|-------|
| OSI (O'Shaughnahill) | OSI-specific packet | Different forms, different legal language |
| Palmetto | Palmetto-specific packet | Different forms, different legal language |

The `Surety_ID` on the `BondCase` determines which template set to copy from SignNow.

### Rule 3: No In-Place Mutation

Once a packet has been sent or signed:
- **Never modify the existing packet**
- Create a new packet version (`Packet_Version` increments)
- Void the old packet
- Log the replacement in AuditEvent with reason

### Rule 4: Recipient Verification

Before sending a signing link:
- Verify recipient phone/email matches the validated `Indemnitor_ID`
- Verify the indemnitor's match to the defendant is still `validated`
- Verify the bond case is still `open` or `posted`

### Rule 5: Completion Tracking

- SignNow fires `document.complete` webhook when all parties sign
- Webhook handler must verify packet belongs to an active bond case
- Signed PDFs are auto-saved to Google Drive case folder
- Slack alert fires on completion
- Bond case `Packet_Status` updates to `signed`
- Bond case `Signature_Status` updates to `signed`
- **Check-in enrollment (A+C):** system enables transparent `check_in_required` monitoring, generates a defendant portal magic link, and creates a staff CRM task to **send** the check-in link. **No automatic client SMS/iMessage** — see `monitoring-checkin-policy.md`.

---

## Delivery Channels

Signing links may be delivered via:
1. **SMS** (Twilio) — Primary
2. **Telegram** — Via bot deep link
3. **WhatsApp** (Twilio) — When available
4. **Email** — Fallback

All delivery must log: channel used, timestamp, recipient identifier.

---

## Void Conditions

A packet must be voided if:
- Wrong defendant or indemnitor referenced
- Wrong surety template used
- POA number is incorrect
- Case number changed
- Indemnitor requests cancellation before signing
- Human override for any reason

Voided packets: set `Document_Status = voided`, log `Voided_At` + reason, create AuditEvent.

---

## Escalation Conditions

Escalate immediately if:
- Signing link delivered to wrong phone/email
- Signed packet has incorrect defendant/indemnitor/POA/surety
- `document.complete` webhook references unknown packet
- Multiple active packets exist for same bond case
