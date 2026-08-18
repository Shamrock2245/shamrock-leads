# Signature Policy

> **Status:** `[ACTIVE — Enforced from Phase 7]`

---

## Purpose

This policy governs generation, delivery, and tracking of signature packets via self-hosted DocuSeal only. SignNow is retired from the workflow and may appear only on historical records. Paperwork is surety-specific — the template depends on which surety (OSI or Palmetto) is backing the bond.

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

The `Surety_ID` on the `BondCase` determines the DocuSeal template. Palmetto requires its explicit configured template and must never fall back to OSI.

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

- DocuSeal fires `form.completed` as each party signs and `submission.completed` when all parties finish
- Webhook handler must verify packet belongs to an active bond case
- Signed PDFs are auto-saved to Google Drive case folder
- Slack alert fires on completion
- Bond case `Packet_Status` updates to `signed`
- Bond case `Signature_Status` updates to `signed`
- **Check-in enrollment (A+C):** system enables transparent `check_in_required` monitoring, generates a defendant portal magic link, and creates a staff CRM task to **send** the check-in link. **No automatic client SMS/iMessage** — see `monitoring-checkin-policy.md`.

### Rule 6: Appearance Bonds Are Print / Wet-Ink Only

**Appearance bonds are not e-signature documents.**

| Step | Action |
|------|--------|
| 1 | System fills and **stores an UNSIGNED PDF** (one form per charge) |
| 2 | Staff **prints** the unsigned form(s) |
| 3 | **Live wet-ink signature** on the paper |
| 4 | Take the signed original(s) **to the jail** |

- Never send appearance bonds via DocuSeal or any other e-sign provider; appearance bonds require wet ink
- One appearance bond PDF per charge; one POA per charge; case number(s) per charge
- Indemnitor/defendant packet docs (indemnity, SSA, applications, etc.) still use e-sign
- Stored under `dashboard/uploads/appearance_bonds/<packet_id>/` with status `unsigned_stored`

---

## Delivery Channels

Signing links may be delivered via:
1. **BlueBubbles iMessage** — Preferred client rail for an explicitly approved first packet notice.
2. **SMS** (Twilio) — Primary fallback when separately selected by staff.
3. **Telegram** — Via bot deep link.
4. **WhatsApp** (Twilio) — When available.
5. **Email** — Fallback.

### Explicit automatic-delivery exception: initial DocuSeal notice

A finalized DocuSeal packet may trigger **one automatic BlueBubbles iMessage** to each explicitly packet-bound indemnitor or co-indemnitor when all of the following are true:

- The packet is in `pending_signature`, is not voided, and has an active DocuSeal submission with `docuseal_status=sent`.
- The target has matching DocuSeal `metadata.packet_id`, an `external_id` for that packet, a signer-specific DocuSeal link, and a validated phone returned for that signer.
- The responsible automation is explicitly enabled and contains the approved indemnitor message template with the `{signing_link}` placeholder.
- A defendant is included only after a separate explicit opt-in **and** a separately approved defendant message template.
- A defendant additionally requires a staff-recorded, non-PII `verified_opt_in` authorization on the authoritative active bond. The authorization records contact verification and iMessage opt-in without storing contact details in the audit evidence.
- The new packet must snapshot that authorization with the exact `Defendant_ID`; the returned DocuSeal submitter must retain that same snapshot, role `defendant`, packet ID, exact defendant external ID, and a direct HTTPS signer URL on `sign.shamrockbailbonds.biz`.

The exception is **iMessage-only**. It does not fall back to generic packet contact fields, SMS, automatic retries, chase sequences, a changed/voided packet, an untrusted signing-link host, or a prior delivery evaluation. Any missing binding, template, phone, signing link, BlueBubbles availability, authorization snapshot, or delivery result blocks that recipient and records a non-PII audit outcome. Manual resend remains the recovery path.

A staff-initiated manual delivery also requires an authenticated staff session, an active sent DocuSeal submission, an explicit recipient role, and that role's exact packet-bound DocuSeal signer. It cannot select an arbitrary phone, use a generic packet contact fallback, or return a signing link in its response.

All delivery must log: channel used, timestamp, recipient role, packet ID, and result state; logs and audit details must not contain phone numbers, emails, addresses, SSNs, or signing-link URLs.

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
