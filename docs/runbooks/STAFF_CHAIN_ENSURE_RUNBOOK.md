# Staff Runbook: Ensuring Match + BondCase Paperwork Chain

> **Endpoint:** `POST /api/staff/chain/ensure-match-bondcase`  
> **Auth:** God-Admin, admin, or staff session (`role` `god_admin` / `admin` / `staff`). Sub-agent and other authenticated sessions are refused. Machine tokens: `X-API-Key` or `X-Internal-Token` matching `GAS_API_KEY` / `LEADS_INTERNAL_TOKEN`. Or `X-Admin-Token` / `X-PIN` / Bearer matching `DASHBOARD_PIN`.
> **Purpose:** Closes Gap B — programmatically bridges `ArrestLead` → `Defendant` → `Indemnitor` → `validated Match` → `BondCase` → `Packet` so emergency manual Mongo scripts are never needed for live bonds.

---

## 1. When to Use

Use this endpoint whenever a bond is ready for paperwork execution and either:
1. You are running the 5-step programmatic onboarding script (`scripts/examples/write_bond_super_crm.js`).
2. An in-office DocuSeal packet requires a validated Match and BondCase before signing.
3. A past packet was issued with `pending_staff_match: true` and needs to be linked and validated.

---

## 2. API Contract

### Request Headers
```http
POST /api/staff/chain/ensure-match-bondcase HTTP/1.1
Content-Type: application/json
X-Admin-Token: <DASHBOARD_PIN>
```
*(Or send `X-API-Key: <GAS_API_KEY>`, `X-Internal-Token`, or standard authenticated staff browser cookie.)*

### Request Body
```json
{
  "booking_number": "1033474",
  "surety_id": "osi",
  "case_number": "26CF017605",
  "poa_numbers": [
    "OSI-P6-116-26-0015",
    "OSI-P6-116-26-0016",
    "OSI-P3-116-26-0018",
    "OSI-P3-116-26-0019"
  ],
  "bond_amount": 12000,
  "premium": 1200,
  "packet_id": "PKT-SCHMIDT-1033474"
}
```

### Parameters
| Field | Type | Required | Description |
|---|---|---|---|
| `booking_number` | string | ✅ Yes | Booking number of the arrestee. |
| `surety_id` | string | Optional | `osi` or `palmetto` (defaults to active_bonds record). |
| `case_number` | string | Optional | Court case number (defaults to active_bonds/arrest). |
| `poa_numbers` | list[str] | Optional | List of POAs assigned in `poa_inventory` for this case. |
| `poa_number` | string | Optional | Single primary POA number. |
| `bond_amount` | float | Optional | Total bond amount (defaults to active_bonds/arrest). |
| `premium` | float | Required on file or in the body | Positive premium already stored on the active bond (`premium` or `premium_amount`), or an explicit amount on this request. Missing and zero are refused. The service does not calculate 10% of the bond. |
| `packet_id` | string | Optional | If provided, links packet and sets `pending_staff_match: false`. |
| `charge_details` | list[dict] | Optional | Charge breakdown details. |

---

## 3. Guarantees & Invariants

1. **Fail-Closed Verification:**
   - Requires verified arrest record in `arrests` collection.
   - Requires verified active bond in `active_bonds` collection.
   - Requires verified indemnitor contact (name + email) already on file in CRM (no fabricated contact).
   - Requires POAs to exist in `poa_inventory` and already be assigned to this booking, this case number, or this BondCase UUID. Available inventory is never auto-assigned.
   - Requires a positive premium on the active bond or in the request. A missing or zero premium is `premium_required`. No percentage is invented.
2. **Idempotent:**
   - Re-running the endpoint with the same booking number returns the existing UUIDs (`defendant_id`, `indemnitor_id`, `match_id`, `bond_case_id`) without creating duplicates.
   - Ensure writes `poa_inventory.bond_case_id` and `assigned_to` to the BondCase UUID. A second ensure treats that UUID, the booking number, and the case number as ownership and returns 200. It does not 409 with `poa_assigned_elsewhere`.
3. **Finalize binding:**
   - `POST /api/paperwork/packet/finalize` calls ensure when the Match/BondCase chain is not already validated. If ensure fails, finalize returns that error (4xx) with `bound: false` and does not create or send a packet.
   - Shannon voice create (`skip_bond_binding` + `pending_staff_match`) is unchanged.
4. **Audit Trail:**
   - Automatically writes an immutable audit record to `audit_events` (`staff_ensure_match_bondcase`).
   - Appends a transition note to `active_bonds.status_history`.

---

## 4. Example cURL

```bash
curl -X POST "https://leads.shamrockbailbonds.biz/api/staff/chain/ensure-match-bondcase" \
  -H "Content-Type: application/json" \
  -H "X-Admin-Token: 224545" \
  -d '{
    "booking_number": "1033474",
    "surety_id": "osi",
    "case_number": "26CF017605",
    "poa_numbers": ["OSI-P6-116-26-0015", "OSI-P6-116-26-0016"]
  }'
```

Premium is not calculated. This call succeeds only when the active bond already has a positive `premium` or `premium_amount`. Otherwise send that amount in the body. A missing or zero premium returns `400 premium_required`.

### Success Response (200 OK)
```json
{
  "success": true,
  "action": "ensured",
  "booking_number": "1033474",
  "defendant_id": "e43526a0-5d36-4805-a245-5a1fd40b705b",
  "indemnitor_id": "df2c221f-9afd-4113-9586-a96b28ddfffc",
  "match_id": "7278a61b-8ba4-41eb-a8f1-ea46cf2cdb93",
  "bond_case_id": "31bccd64-1e85-4842-ad12-0660b7c6740a",
  "packet_id": "PKT-SCHMIDT-1033474",
  "case_number": "26CF017605",
  "surety_id": "osi",
  "poa_numbers": ["OSI-P6-116-26-0015", "OSI-P6-116-26-0016"],
  "primary_poa": "OSI-P6-116-26-0015",
  "bond_amount": 12000.0,
  "premium": 1200.0,
  "pending_staff_match": false
}
```
