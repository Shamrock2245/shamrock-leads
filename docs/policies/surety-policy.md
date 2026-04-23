# Surety Policy

> **Status:** `[ACTIVE — Enforced from Phase 5]`
> **Sureties:** OSI (Old Southern Indemnity), Palmetto Surety Corporation

---

## Purpose

This policy governs surety selection, POA assignment, premium calculation, and reporting for all bonded cases. Every bond written by Shamrock is backed by one of two surety companies: **OSI** or **Palmetto**.

---

## Surety Profiles

### OSI — Old Southern Indemnity

| Property | Value |
|----------|-------|
| Surety ID | `osi` |
| Licensed States | FL |
| Premium to Surety | **$7.50 per $100 in premium** |
| Build-Up Fund (BUF) | **$5.00 per $100 in premium** |
| Agent Retention | Remainder after surety + BUF |
| POA Prefixes | `OSI3`, `OSI6`, `OSI51`, `OSI101`, `OSI251` |

### Palmetto Surety Corporation

| Property | Value |
|----------|-------|
| Surety ID | `palmetto` |
| Licensed States | FL, SC, NC, TN, TX, CT, LA, MS |
| Premium to Surety | **$10.00 per $100 in premium** |
| Build-Up Fund (BUF) | **$5.00 per $100 in premium** |
| Agent Retention | Remainder after surety + BUF |
| POA Prefixes | `PSC5`, `PSC15`, `PSC25`, `PSC50`, `PSC75`, `PSC101`, `PSC200`, `PSC250` |

### Premium Split Example

For a **$10,000 bond** at standard 10% premium ($1,000 collected):

| | OSI | Palmetto |
|--|-----|----------|
| Premium Collected | $1,000.00 | $1,000.00 |
| To Surety | $75.00 (7.5%) | $100.00 (10%) |
| To BUF | $50.00 (5%) | $50.00 (5%) |
| **Agent Retains** | **$875.00** | **$850.00** |

---

## POA Prefix Tiers

POA prefixes correspond to maximum bond amount tiers. Each prefix is a separate physical POA form with a specific bond limit.

### OSI POA Prefixes
| Prefix | Inferred Tier |
|--------|---------------|
| `OSI3` | Low-tier bonds |
| `OSI6` | Mid-low tier |
| `OSI51` | Mid tier |
| `OSI101` | High tier |
| `OSI251` | Maximum tier |

### Palmetto POA Prefixes
| Prefix | Inferred Tier |
|--------|---------------|
| `PSC5` | $5K and under |
| `PSC15` | $15K and under |
| `PSC25` | $25K and under |
| `PSC50` | $50K and under |
| `PSC75` | $75K and under |
| `PSC101` | $100K and under |
| `PSC200` | $200K and under |
| `PSC250` | $250K and under |

> **Note:** Exact tier-to-amount mappings will be confirmed from surety documentation. The prefix naming convention strongly implies bond amount ceilings.

---

## Surety Selection Rules

### When to Use Which Surety

1. **Default**: Use the surety specified by `DEFAULT_SURETY` environment variable (if set)
2. **Out-of-State**: If defendant's case is outside Florida, **must use Palmetto** (multi-state license)
3. **POA Availability**: If the needed POA tier is out of stock for one surety, use the other
4. **Agent Preference**: Human bondsman may override surety selection for any business reason
5. **Never auto-select**: System proposes, human confirms. No automated surety assignment.

### Validation Rules

- `Surety_ID` must be `osi` or `palmetto`
- Selected POA prefix must belong to the selected surety
- POA must be in `available` status in the POAInventory
- Assigned POA's tier must cover the bond amount

---

## POA Inventory Management

### Ingestion

POA inventory is populated by scanning the **inventory receipt** received from each surety company:

1. Agent receives physical POA book from surety
2. Agent scans the inventory receipt
3. OCR or manual entry creates records in `poa_inventory` collection
4. Each POA number in the book gets a record with `status: available`

### Lifecycle

```
Available → Assigned → Used → Reported
                  ↘ Voided → Reported
```

### Rules

1. **Available**: POA exists in inventory, not assigned to any case
2. **Assigned**: POA selected for a case but bond not yet posted
3. **Used**: Bond posted — POA permanently consumed
4. **Voided**: POA cancelled — must record reason. Never returns to available.
5. **Reported**: POA included in monthly production report to surety

### Monthly Reconciliation

- All `used` and `voided` POAs for the month must be reported to the respective surety
- Reconciliation tracks: POA number, bond amount, case number, date used/voided
- `Reported_At` timestamp in POAInventory marks when reported
- Unreported POAs flagged in monthly compliance check

---

## Escalation Conditions

Escalate immediately if:
- POA number from wrong surety's inventory
- POA already assigned to another active case
- No available POAs in needed tier for either surety
- Premium split rates change (update this policy immediately)
- New surety company added
- Surety license status changes
