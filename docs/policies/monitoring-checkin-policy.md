# Bond Monitoring & Check-In Policy

> **Status:** `[ACTIVE — A (explicit check-in) + C (condition language / ops)]`  
> **Deferred:** Vendor GPS / continuous location (B) — do not implement until approved.  
> **Last Updated:** 2026-07-10

---

## Purpose

Define **transparent, consent-based** defendant check-in and location verification after bond paperwork is signed, plus the **contractual / ops language** staff use when enabling monitoring.

This policy is **not** covert device tracking. Shamrock does **not** install spyware, silent GPS, or any tracking that the defendant does not know about and affirmatively opt into at check-in time.

---

## Track Map

| Track | Description | Status |
|-------|-------------|--------|
| **A** | Explicit check-in / location via magic-link portal after signing | **Live** |
| **B** | Continuous GPS via **in-stack Traccar** (Traccar Client / OsmAnd / hardware) | **Live** — not a third-party GPS vendor |
| **C** | Written monitoring conditions + CRM/ops workflow | **Live** |

**No external GPS vendor.** Shamrock already runs Traccar (`shamrock-traccar`). Continuous tracking = defendant knowingly installs Traccar Client (or staff attaches hardware tracker) pointed at our OsmAnd port. Portal check-ins inject one-shot fixes into the same Traccar device so Tracking + live map stay unified.

---

## Core Rules

### Rule 1: Transparent Only

- Location is collected **only** when the defendant opens the Shamrock check-in link and **explicitly consents** (checkbox + browser geolocation prompt).
- No background location, no always-on tracking, no SMS/IP silent pings framed as “unknown to defendant.”
- Optional selfie is **opt-in** at the same moment as check-in, with clear purpose text.

### Rule 2: Contractual Basis (C)

Every active bond that requires check-ins must have monitoring conditions that the defendant (and indemnitor, where applicable) have acknowledged in the bond/surety packet or a written addendum.

**Standard condition language (use verbatim or as addendum):**

> **Defendant Check-In & Location Verification.** As a condition of this bail bond, the Defendant agrees to complete scheduled check-ins with Shamrock Bail Bonds using the secure link provided by Shamrock. Each check-in may require: (1) confirmation of current contact information and residence; (2) voluntary disclosure of approximate location via the device’s location services **only when the Defendant taps “Check In” and grants permission**; and (3) optional photo verification when requested. Failure to complete required check-ins may result in increased supervision, bond surrender proceedings, or other remedies available under the bond and applicable law. Location data is used solely for bond compliance and risk management and is not sold to third parties.

**Indemnitor notice (optional short form for portal SMS to cosigner):**

> The bond includes defendant check-in requirements. Shamrock will contact the defendant for compliance check-ins. Missed check-ins may increase risk of forfeiture and may require indemnitor cooperation.

### Rule 3: Human Gate on Client Contact

- **No automated client outreach** for check-in enrollment without staff action (Prime Directive: human-in-the-loop).
- On SignNow complete, the system **may**: enable `check_in_required` on the bond, create a staff CRM task, generate a defendant portal token, and notify staff (Slack / dashboard).
- The system **must not** auto-text or auto-iMessage the defendant a check-in link unless a human triggers **Send check-in link** (or equivalent staff action).

### Rule 4: Fail Closed

- Invalid/expired portal tokens → no data, no check-in.
- Non-defendant roles cannot submit check-ins.
- Check-in without consent flag → reject.
- GPS is preferred but **not hard-required** if the device denies permission; staff may still mark manual office check-in.

### Rule 5: Record Identity

- Check-ins attach to `booking_number` on `active_bonds` and rows in `bond_checkins`.
- Audit every enrollment, send-link, and portal check-in (no full PII in Slack).

### Rule 6: Frequency Defaults

| Risk posture | Default frequency | First due |
|--------------|-------------------|-----------|
| Standard (default after signing) | Every **7** days | 7 days from enable |
| Elevated / monitoring status | Every **3** days | 3 days from enable |
| High alert | Daily | 1 day from enable |
| Staff override | Custom `check_in_frequency_days` | Staff-set |

Missed-check-in scan (`POST /api/active-bonds/missed-checkins`) creates high-severity alerts for overdue bonds with `check_in_required: true`.

### Rule 7: Traccar Is the GPS Engine (Track B)

| Mode | How it works | Consent |
|------|----------------|---------|
| **Portal check-in (A)** | Defendant taps Check In → GPS → OsmAnd inject into Traccar + `bond_checkins` | Explicit checkbox + browser prompt |
| **Continuous (B)** | Defendant installs **Traccar Client**; device ID = `shamrock-{BOOKING}`; server = public OsmAnd port | Explicit install + bond condition |
| **Hardware** | GPS103 / H02 / GT06 / Teltonika to Traccar protocol ports | Staff assigns IMEI; defendant informed |

- Provision device: `POST /api/active-bonds/{booking}/provision-traccar` or enable-checkin (default)
- Webhook: Traccar → `POST /api/traccar/webhook` → `geo_devices` + `location_history`
- Frontend geo APIs: `/api/geo-intel/*`
- **Forbidden:** third-party GPS SaaS vendors, covert trackers, silent SMS/IP as primary continuous GPS

---

## Ops Workflow (C)

### When paperwork is fully signed

1. System sets `check_in_required: true` (if not already), frequency default 7 days, `next_checkin_due`.
2. System generates a **defendant** portal magic link (`/c/{token}`).
3. System creates staff task: **“Send check-in enrollment link”** (`task_type: checkin_enroll`).
4. Staff reviews bond conditions → confirms language was signed / addendum attached.
5. Staff uses **Send check-in link** (dashboard) to deliver portal URL via iMessage/SMS to the **validated defendant phone**.
6. Defendant opens link → reads disclosure → checks consent → shares location (optional selfie) → submits.
7. CRM Tracking + compliance reports show last check-in / overdue.

### Manual enable (any active bond)

Staff can call enable-checkin without waiting for a new signature (legacy cases, elevated risk).

### Disable

Set `check_in_required: false` when bond is exonerated / discharged (already done on discharge paths). Cancel pending check-in tasks.

---

## What We Collect at Check-In (A)

| Field | Required | Purpose |
|-------|----------|---------|
| Timestamp | Yes | Compliance proof |
| Explicit consent (`consent: true`) | Yes | Legal transparency |
| GPS lat/lng/accuracy | Preferred | Location verification |
| Optional selfie | No | Identity verification when requested |
| Notes | No | Defendant free text |
| Method / source | Yes | `portal_self_service` vs staff `manual` |

Stored in `bond_checkins` + optional `location_history` on the bond. Consent metadata: `consent_version`, `consent_at`.

---

## Forbidden

- Covert tracking “without defendant knowing”
- Expanding bond-tracker silent SMS/IP capture as the primary continuous GPS path
- Third-party GPS vendor SaaS (redundant — Traccar is in-stack)
- Auto client messaging of check-in links without staff send action

---

## System Touchpoints

| Component | Role |
|-----------|------|
| `docs/policies/monitoring-checkin-policy.md` | This policy |
| `dashboard/services/checkin_enrollment_service.py` | Enable monitoring, token, tasks, send copy |
| `dashboard/services/client_portal_service.py` | Token + portal check-in write path |
| `dashboard/portal.html` | Defendant UI + consent |
| `POST /api/portal/{token}/checkin` | Public check-in API |
| `POST /api/active-bonds/{booking}/enable-checkin` | Staff enable |
| `POST /api/active-bonds/{booking}/send-checkin-link` | Staff-gated send |
| SignNow webhook / lifecycle poller | Post-sign enrollment (staff task only) |
| `bond_checkins` / Tracking tab | Ops visibility |

---

## Escalation

Escalate if:

- Defendant disputes they agreed to check-ins (pull signed packet)
- Check-in link sent to wrong phone
- Location data requested for non-compliance use (e.g. marketing)
- Request for continuous/covert tracking → refuse; route to B discussion with counsel

---

## Related Policies

- `signature-policy.md` — packet complete triggers enrollment hook
- `matching-policy.md` — correct defendant identity before any link send
- `surety-policy.md` — surety does not change check-in transparency rules
- Agents.md §10 Safety Rules — no guessing identity; audit everything; minimize PII in Slack
