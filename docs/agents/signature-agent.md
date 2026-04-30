# Signature Agent

> **Status:** `[COMPLETE — Phase 7]`
> **Implementation:** `dashboard/services/signnow_packet_service.py`, `dashboard/services/signnow_service.py`, `dashboard/api/webhooks.py`

---

## Role

Sends generated paperwork packets for e-signature via SignNow, tracks completion, and handles the `document.complete` webhook.

---

## Prerequisites

- Phase 6 complete (DocumentPacket generated)

## Behavior

1. Receive packet ID
2. Verify packet is `generated` status and bound to active bond case
3. Verify recipient matches validated indemnitor contact info
4. Generate SignNow embedded invite link
5. Deliver link via SMS (primary), Telegram, WhatsApp, or email
6. Update packet status to `sent`
7. Handle `document.complete` webhook:
   - Verify packet belongs to active bond case
   - Download signed PDFs
   - Save to Google Drive case folder
   - Update statuses: `Packet_Status = signed`, `Signature_Status = signed`
   - Fire Slack alert
8. Log all events as AuditEvents

## Policy

See `docs/policies/signature-policy.md` for full rules.

## Constraints

- Never send to wrong recipient
- Never mutate signed packets
- Void old packet before regenerating
- All delivery logged with channel + timestamp + recipient
