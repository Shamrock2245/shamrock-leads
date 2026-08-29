# Staff Runbook: B3 Write-Bond → Paperwork Event Forwarding Smoke

> **Target Checklist Item:** [`ECOSYSTEM_PROD_CHECKLIST.md`](../ECOSYSTEM_PROD_CHECKLIST.md) §B3 (`GAS_WEB_APP_URL` + `GAS_API_KEY` forward write-bond / paperwork events)  
> **Status:** 🔲 **Open — awaiting a real BondCase** (Operator reports the path has worked; no live case is available yet for the documented smoke.)  
> **Execution Rule:** **Do not run or simulate this smoke without an authorized staff member supplying an exact, pre-existing validated BondCase and approving the action in the active session.**

---

## 1. Prerequisites (Must Be Verified Before Smoke)

Staff must confirm the following prerequisites in the system before proceeding:

1. **Pre-Existing Authoritative BondCase Record:**
   - A real, validated case already exists in `active_bonds` or `bond_cases` (no synthetic cases, no stubs).
   - `Bond_Case_ID` (or `Booking_Number`) is known.
   - `Case_Number` is present and non-empty (e.g. `26-CF-001234`).
   - `Surety_ID` is explicit (`osi` or `palmetto`).
2. **Validated Match Record:**
   - Record exists in `matches` linked to the case's `Defendant_ID` and `Indemnitor_ID`.
   - Match `Status` is `validated`, `approved`, or `matched`.
   - Verified `Defendant` record exists in `defendants`.
   - Verified `Indemnitor` record exists in `indemnitors`.
3. **Assigned & Sufficient POA:**
   - `POA_Number` exists in `poa_inventory` with `status: "assigned"` or `"used"`.
   - `surety_id` matches the BondCase surety.
   - `max_bond_value` is greater than or equal to the `Bond_Amount`.
4. **Stable GAS Factory Configuration:**
   - `GAS_WEB_APP_URL` is configured in `.env` and points to the unchanged stable deployment URL (`...CvP-Z/exec`).
   - `GAS_API_KEY` is configured and matches GAS Script Properties.
   - Central factory responds to `?action=health` with HTTP `200` (`{"success":true,"version":"V409"}`).

---

## 2. Stop Conditions (Abort Immediately If Any Occur)

**ABORT the smoke immediately and escalate if:**
- Two or more defendants or indemnitors match the intake ambiguity.
- The case number or POA number is missing, ambiguous, or inferred.
- The POA is from the wrong surety's inventory or has an insufficient limit tier.
- The GAS URL would change or point to an unrecognized deployment ID.
- Any attempt is made to automatically contact a client, generate an unsolicited signing link, or create a duplicate paperwork packet.

---

## 3. Step-by-Step Staff Execution Procedure

### Step 1: Execute Read-Only Preflight Probe
Run the non-modifying preflight probe against the candidate `bond_case_id` to verify all invariant gates fail closed or report eligibility:

```bash
# Via cURL from authorized staff session:
curl -sS -X POST "https://leads.shamrockbailbonds.biz/api/paperwork/write-bond-forward/preflight" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <GAS_API_KEY>" \
  -d '{
    "bond_case_id": "STAFF_SELECTED_BOND_CASE_ID",
    "correlation_id": "B3-SMOKE-PREFLIGHT-01"
  }'
```

**Expected Preflight Result:**
```json
{
  "success": true,
  "state": "eligible_for_staff_approval",
  "correlation_id": "B3-SMOKE-PREFLIGHT-01",
  "block_reasons": [],
  "details": {
    "bond_case_id": "STAFF_SELECTED_BOND_CASE_ID",
    "booking_number": "REDACTED",
    "case_number": "REDACTED",
    "surety_id": "osi",
    "poa_number": "REDACTED",
    "bond_amount": 5000.0,
    "gas_configured": true,
    "gas_target_fingerprint": "debd946c22",
    "idempotent": true
  }
}
```

*If `state == "blocked"`, inspect `block_reasons` and resolve missing CRM data in Super CRM before re-attempting.*

---

### Step 2: Staff Confirmation & Dry-Run (Optional)
To verify the payload construction without sending an outbound network request:

```bash
curl -sS -X POST "https://leads.shamrockbailbonds.biz/api/paperwork/write-bond-forward/execute" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <GAS_API_KEY>" \
  -d '{
    "bond_case_id": "STAFF_SELECTED_BOND_CASE_ID",
    "staff_actor": "admin@shamrockbailbonds.biz",
    "correlation_id": "B3-SMOKE-DRYRUN-01",
    "confirmed": true,
    "dry_run": true
  }'
```

---

### Step 3: Staff-Authorized Live Forwarding Execution
When authorized by staff in the session, execute the single live outbound event:

```bash
curl -sS -X POST "https://leads.shamrockbailbonds.biz/api/paperwork/write-bond-forward/execute" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <GAS_API_KEY>" \
  -d '{
    "bond_case_id": "STAFF_SELECTED_BOND_CASE_ID",
    "staff_actor": "admin@shamrockbailbonds.biz",
    "correlation_id": "B3-SMOKE-LIVE-01",
    "confirmed": true
  }'
```

**Expected Live Result:**
```json
{
  "success": true,
  "state": "forwarded",
  "correlation_id": "B3-SMOKE-LIVE-01",
  "bond_case_id": "STAFF_SELECTED_BOND_CASE_ID",
  "booking_number": "REDACTED",
  "case_number": "REDACTED",
  "surety_id": "osi",
  "poa_number": "REDACTED",
  "gas_response_status": 200,
  "gas_target_fingerprint": "debd946c22",
  "message": "Write-bond event forwarded to central GAS factory with verified receipt."
}
```

---

## 4. Required Redacted Proof & Evidence Definition

To mark **B3** as `[x]` in `docs/ECOSYSTEM_PROD_CHECKLIST.md`, staff must capture the following four pieces of redacted evidence:

1. **GAS Receipt Evidence:**
   - Verification that the central factory (`...CvP-Z/exec`) received `action: "logWixEvent"`, `event_type: "write_bond_forward"` with correlation ID `B3-SMOKE-LIVE-01` and returned HTTP `200` (`success: true`).
2. **URL Stability Evidence:**
   - Target GAS deployment fingerprint matches the approved central factory fingerprint (`debd946c22`) without any URL rotation.
3. **Audit Log Record:**
   - An immutable record in MongoDB `audit_events` and `gas_event_log` with `event_type: "write_bond_forwarded_to_gas"`, matching `correlation_id`, `status_code: 200`, and staff actor attribution.
4. **No Side-Effect Verification:**
   - Confirm zero duplicate `paperwork_packets` documents were minted and zero outbound SMS/iMessage/email communications were dispatched to the client.

---

## 5. Post-Smoke Checklist Update

Once all 4 evidence criteria are met and confirmed by Brendan:
1. Update `docs/ECOSYSTEM_PROD_CHECKLIST.md` line 56:
   ```markdown
   | B3 | `GAS_WEB_APP_URL` + `GAS_API_KEY` forward write-bond / paperwork events | Ops | [x] *verified YYYY-MM-DD* — staff-confirmed live write-bond forward (correlation B3-SMOKE-...) received with HTTP 200 on stable factory; zero duplicate packets or client messages. |
   ```
2. Update `STATUS.md` with the verified correlation ID and audit timestamp.
