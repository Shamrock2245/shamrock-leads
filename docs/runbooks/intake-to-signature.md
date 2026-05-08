# Intake-to-Signature Runbook

> **Status:** `[IMPLEMENTED — All Phases Complete]`
> **This runbook describes the production workflow for converting an intake into a signed bond.**

---

## Overview

This runbook describes the complete path from "someone calls about bonding out a defendant" to "signed paperwork in the case folder."

---

## Step 1: Receive Intake `[Phase 3]`

**Channels:**
- Wix Portal (web form → GAS → HTTP to shamrock-leads)
- Telegram Bot (@ShamrockBail_bot → webhook → GAS → HTTP)
- Shannon voice AI (phone → ElevenLabs → Netlify → GAS → HTTP)
- Direct call/SMS (Twilio → Node-RED → GAS → HTTP)

**Collected Data:**
- Indemnitor name, phone, email
- Defendant name (as known to indemnitor)
- County of arrest (if known)
- Booking number (if known)
- Relationship to defendant
- Government ID (photo upload)

**Output:** Indemnitor record created in `indemnitors` collection.

---

## Step 2: Match to Defendant `[Phase 4]`

**Process:**
1. Query `defendants` (or `arrests`) by name + county + booking number
2. Score candidate matches (0.0–1.0)
3. High confidence (≥0.85 + booking match): auto-validate
4. Medium confidence (0.60–0.84): human review
5. Low confidence (<0.60): manual investigation

**Output:** Match record with `status = validated`.

**Policy:** See `docs/policies/matching-policy.md`.

---

## Step 3: Select Surety `[Phase 5]`

**Decision Tree:**
1. Is defendant's case outside Florida? → Must use **Palmetto**
2. Check POA availability for needed bond tier
3. Apply default surety preference (`DEFAULT_SURETY` env var)
4. **Human confirms** surety selection — never auto-select

**Output:** `Surety_ID` chosen (`osi` or `palmetto`).

---

## Step 4: Assign POA from Inventory `[Phase 5]`

**Process:**
1. Determine needed POA tier from bond amount
2. Query `poa_inventory` for matching `surety_id + poa_prefix + status:available`
3. Select first available POA in correct tier
4. Update POA status: `available` → `assigned`
5. Record assignment as AuditEvent

**Tier Selection:**
- Bond ≤ $3K → OSI3 or PSC5
- Bond ≤ $5K → PSC5
- Bond ≤ $6K → OSI6 or PSC5
- Bond ≤ $15K → PSC15
- Bond ≤ $16K → OSI16 or PSC15
- Bond ≤ $25K → PSC25
- ... (match bond amount to smallest sufficient tier)

**Output:** `POA_Number` assigned, POA record updated.

---

## Step 5: Create Bond Case `[Phase 5]`

**Preconditions** (all must be true):
- ✅ Defendant record exists
- ✅ Indemnitor record exists
- ✅ Match is validated
- ✅ Surety selected
- ✅ POA assigned from correct surety
- ✅ Case number present

**Calculate Premium:**
```
premium = bond_amount × 0.10
surety_owed = premium × surety_rate   # OSI: 7.5%, Palmetto: 10%
buf_owed = premium × 0.05
agent_retains = premium - surety_owed - buf_owed
```

**Output:** BondCase record created with `bond_status = open`.

---

## Step 6: Generate Paperwork `[Phase 6]`

**Process:**
1. Select SignNow template set by `Surety_ID`
2. Copy templates
3. Hydrate fields from BondCase + Defendant + Indemnitor
4. Create DocumentPacket record (`Packet_Version = 1`)

**Output:** Packet ready for signature.

---

## Step 7: Send for Signature `[Phase 7]`

**Process:**
1. Verify recipient = validated indemnitor
2. Generate SignNow embedded invite link
3. Deliver via SMS (primary), Telegram, WhatsApp, or email
4. Update: `packet_status = sent`, `signature_status = sent`

**Output:** Signing link in indemnitor's hands.

---

## Step 8: Signature Completion `[Phase 7]`

**Triggered by:** SignNow `document.complete` webhook.

**Process:**
1. Verify packet → bond case linkage
2. Download signed PDFs
3. Save to Google Drive case folder
4. Update: `packet_status = signed`, `signature_status = signed`
5. Slack alert: "Bond paperwork signed for [Case #]"
6. Trigger payment request (Phase 8)

---

## Step 9: Collect Payment `[Phase 8]`

**Process:**
1. Generate SwipeSimple payment link for `premium_amount`
2. Send to validated indemnitor
3. Track: `sent` → `paid` / `partial` / `failed`
4. Flag delinquent plans (>30 days)

---

## End State

When all steps complete:
- `bond_status = posted`
- `packet_status = signed`
- `signature_status = signed`
- `payment_status = paid`
- POA status: `used`
- Signed PDFs in Drive
- Case ready for court
