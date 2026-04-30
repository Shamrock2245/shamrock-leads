# Bond Case Schema

> **Status:** `[COMPLETE — Phase 5]`

---

## Purpose

Defines the BondCase MongoDB collection schema — the operational backbone once a lead converts to an active bail bond.

---

## Collection: `bond_cases`

```json
{
  "_id": "ObjectId",
  "bond_case_id": "uuid-string",
  "defendant_id": "uuid-string",
  "indemnitor_id": "uuid-string",
  "match_id": "uuid-string",
  "surety_id": "osi | palmetto",
  "county": "Lee",
  "booking_number": "2026-123456",
  "case_number": "26-CF-001234",
  "poa_number": "20134295",
  "poa_prefix": "OSI3",
  "bond_amount": 3000.00,
  "premium_amount": 300.00,
  "surety_owed": 22.50,
  "buf_owed": 15.00,
  "agent_retains": 262.50,
  "bond_type": "Surety",
  "bond_status": "open",
  "packet_status": "not_generated",
  "signature_status": "not_sent",
  "payment_status": "not_requested",
  "created_by": "human:brendan",
  "created_at": "2026-04-23T12:00:00Z",
  "updated_at": "2026-04-23T12:00:00Z",
  "posted_at": null,
  "discharged_at": null,
  "forfeited_at": null,
  "voided_at": null,
  "void_reason": null,
  "notes": ""
}
```

---

## Status Enums

### `bond_status`
| Value | Meaning |
|-------|---------|
| `open` | Case created, bond not yet posted with court |
| `posted` | Bond filed with court, defendant released |
| `discharged` | Case resolved, bond obligation ended |
| `forfeited` | Defendant FTA, bond forfeited by court |
| `voided` | Bond voided before posting |

### `packet_status`
| Value | Meaning |
|-------|---------|
| `not_generated` | No paperwork yet |
| `generated` | Packet created but not sent |
| `sent` | Sent for signature |
| `signed` | All parties signed |
| `voided` | Packet voided (regeneration needed) |

### `signature_status`
| Value | Meaning |
|-------|---------|
| `not_sent` | No signing link sent |
| `sent` | Link delivered |
| `viewed` | Recipient opened the link |
| `signed` | Completed |
| `declined` | Recipient declined |

### `payment_status`
| Value | Meaning |
|-------|---------|
| `not_requested` | No payment link sent |
| `sent` | Payment link delivered |
| `partial` | Partial payment received |
| `paid` | Full premium collected |
| `delinquent` | Payment plan >30 days past due |

---

## Indexes

- Unique: `bond_case_id`
- Unique: `poa_number` (among active cases — `bond_status != voided`)
- Compound unique: `poa_number + case_number`
- Index: `defendant_id`
- Index: `indemnitor_id`
- Index: `surety_id + bond_status`
- Index: `county + bond_status`
- Index: `created_at`

---

## Preconditions for Creation

A BondCase document must NOT be inserted unless:

1. `defendant_id` resolves to an existing Defendant document
2. `indemnitor_id` resolves to an existing Indemnitor document
3. `match_id` resolves to a Match with `status = validated`
4. `surety_id` is `osi` or `palmetto`
5. `poa_number` exists in `poa_inventory` with `status = available`
6. `poa_prefix` belongs to the specified `surety_id`
7. Bond amount does not exceed the POA tier's max bond value
8. `case_number` is present

If any precondition fails, reject the creation and log the reason.
