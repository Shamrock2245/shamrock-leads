---
name: surety-compliance-auditor
description: Instructs the digital workforce on strict programmatic guardrails for surety selection (OSI vs. Palmetto), Power of Attorney (POA) book management, and database relation assertions.
---

# Surety Compliance Auditor — Programmatic Guardrails

> Enforces surety integrity, POA inventory management, and interstate compliance bounds at the database level.

---

## 🏛 Surety Selection Logic

Shamrock represents **two** insurance/surety companies. Programmatic services must actively audit and validate assignments before generating SignNow document packets or posting bonds.

| Surety | ID | Priority | Usage Boundary |
|--------|-----|----------|----------------|
| **O'Shaughnahill Surety & Insurance (OSI)** | `osi` | **Primary** | Default choice for all Florida cases. |
| **Palmetto Surety Corporation** | `palmetto` | **Secondary** | Used for interstate cases, or when OSI inventory is depleted. |

---

## 🔒 Programming Guardrails & Assertions

Any service handling POA books, case creation, or paperwork generation **must assert the following logic**:

### 1. Interstate Validation Rule
If the arrest county is outside Florida (interstate), **Palmetto is required**. The code must assert:
```python
if case.state != "FL":
    assert case.surety_id == "palmetto", "Interstate bonds must be assigned to Palmetto Surety Corporation"
```

### 2. Depletion Warning Thresholds
If the active inventory of OSI POAs drops below **10%** of total capacity for any given tier (e.g. $5K, $10K, $25K books), the system should issue a warning in `#leads` and recommend switching to Palmetto for the next batch of bonds in that tier.

### 3. Strict Pre-Assignment Check
A Power of Attorney (POA) **cannot** be assigned to a Bonded Case unless:
- The Defendant record is verified and exists.
- The Indemnitor matches the Defendant record (match confidence validated).
- The `surety_id` on the case matches the `surety_id` of the POA inventory item.
- The `POA_Number` is marked as `available` in the `poa_inventory` collection.

```python
async def validate_poa_assignment(db, case_id: str, poa_number: str):
    # Fetch POA
    poa = await db["poa_inventory"].find_one({"poa_number": poa_number})
    assert poa, f"POA {poa_number} does not exist in inventory"
    assert poa["status"] == "available", f"POA {poa_number} is already in status: {poa['status']}"

    # Fetch Case
    case = await db["active_bonds"].find_one({"bond_case_id": case_id})
    assert case, f"Bond case {case_id} not found"
    assert case["surety_id"] == poa["surety_id"], f"Mismatched surety: Case={case['surety_id']}, POA={poa['surety_id']}"
```

---

## 📈 Auto-Release & Forfeiture Rules

When a bond status transitions to `exonerated`, `forfeited`, or `surrendered`:
1. **Auto-Release POA**: The attached `POA_Number` must be marked as `released` (or `returned_to_surety` if forfeited/surrendered) in `poa_inventory`.
2. **Immutable Audit Event**: Mark the release timestamp, the associated case, and the actor authorizing the state change in `audit_events`.
3. Never delete a POA or case record from database history. Maintain clean audits for surety compliance review.
