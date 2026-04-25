# Surety Policy

> **Status:** `[ACTIVE — Enforced from Phase 5]`
> **Sureties:** OSI (O'Shaughnahill Surety & Insurance, Inc.), Palmetto Surety Corporation

---

## Purpose

This policy governs surety selection, POA assignment, premium calculation, and reporting for all bonded cases. Every bond written by Shamrock is backed by one of two surety companies: **OSI** or **Palmetto**.

---

## Surety Profiles

### OSI — O'Shaughnahill Surety & Insurance, Inc.

| Property | Value |
|----------|-------|
| Surety ID | `osi` |
| Licensed States | FL |
| Premium to Surety | **$7.50 per $100 in premium** |
| Build-Up Fund (BUF) | **$5.00 per $100 in premium** |
| Agent Retention | Remainder after surety + BUF |
| POA Prefixes | `OSI3`, `OSI6`, `OSI16`, `OSI51`, `OSI101`, `OSI251` |

### Palmetto Surety Corporation

| Property | Value |
|----------|-------|
| Surety ID | `palmetto` |
| Licensed States | FL, SC, NC, TN, TX, CT, LA, MS |
| Premium to Surety | **$10.00 per $100 in premium** |
| Build-Up Fund (BUF) | **$5.00 per $100 in premium** |
| Agent Retention | Remainder after surety + BUF |
| POA Prefixes | `PSC5`, `PSC15`, `PSC25`, `PSC50`, `PSC75`, `PSC105`, `PSC200`, `PSC250` |

### Premium Split Example

For a **$10,000 bond** at standard 10% premium ($1,000 collected):

| | OSI | Palmetto |
|--|-----|----------|
| Premium Collected | $1,000.00 | $1,000.00 |
| To Surety | $75.00 (7.5%) | $100.00 (10%) |
| To BUF | $50.00 (5%) | $50.00 (5%) |
| **Agent Retains** | **$875.00** | **$850.00** |

---

## POA Number Format

POA numbers follow the pattern: `{PREFIX} - {SERIAL}`

Examples:
- OSI: `OSI3 - 20134323`
- Palmetto: `PSC15 - 2644778`

The **prefix** indicates the surety and tier. The **serial number** is unique per POA.

---

## POA Prefix Tiers

POA prefixes correspond to maximum bond amount tiers. Each prefix is a separate physical POA form with a specific bond limit. Always use the **smallest tier that covers the bond amount**.

### OSI POA Prefixes
| Prefix | Max Bond Amount |
|--------|----------------|
| `OSI3` | $3,000 |
| `OSI6` | $6,000 |
| `OSI16` | $16,000 |
| `OSI51` | $51,000 |
| `OSI101` | $101,000 |
| `OSI251` | $251,000 |

### Palmetto POA Prefixes
| Prefix | Max Bond Amount |
|--------|----------------|
| `PSC5` | $5,000 |
| `PSC15` | $15,000 |
| `PSC25` | $25,000 |
| `PSC50` | $50,000 |
| `PSC75` | $75,000 |
| `PSC105` | $105,000 |
| `PSC200` | $200,000 |
| `PSC250` | $250,000 |

---

## Surety Selection Rules

### Decision Logic

The surety selection is made by the bondsman based on:

1. **OSI is preferred** when possible (better agent retention: $875 vs $850 per $10K)
2. **Available POA inventory** — if OSI is out of stock in the needed tier, use Palmetto
3. **Bond amount** — the bond must fit within an available tier for the selected surety
4. **Out-of-State** — if the defendant's case is outside Florida, **must use Palmetto** (multi-state license: FL, SC, NC, TN, TX, CT, LA, MS)
5. **Never auto-select** — system proposes the optimal surety, human confirms

### Selection Algorithm (for system proposals)

```
1. If case state ≠ FL → Palmetto (only option)
2. Check OSI inventory for smallest tier covering bond_amount
3. If OSI tier available → propose OSI
4. Else check Palmetto inventory for smallest tier covering bond_amount
5. If Palmetto tier available → propose Palmetto
6. Else → ESCALATE (no available POAs for this bond amount)
```

### Validation Rules

- `Surety_ID` must be `osi` or `palmetto`
- Selected POA prefix must belong to the selected surety
- POA must be in `available` status in the POAInventory
- Assigned POA's tier must cover the bond amount
- POA serial must match the pattern for the prefix

---

## Writing Agents

Currently, **Brendan** is the sole writing agent handling all bond executions. Sub-agents will be added in the future. When sub-agents are added:

- Each sub-agent will have their own POA book assignments
- POA inventory will be partitioned by writing agent
- Commission tracking will be per-agent
- The `WritingAgent` entity in the data model supports this expansion

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
- Premium split rates change (update this policy AND `core/models.py` immediately)
- New surety company added
- Surety license status changes
