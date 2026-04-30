# Surety Configuration Schema

> **Status:** `[COMPLETE — Phase 5]`

---

## Purpose

Defines the data model for surety companies and POA inventory. Based on actual inventory receipts from O'Shaughnahill (OSI) and Palmetto Surety Corporation.

---

## Surety Companies

### OSI — O'Shaughnahill Surety & Insurance, Inc.

| Property | Value |
|----------|-------|
| `Surety_ID` | `osi` |
| Company Name | O'Shaughnahill Surety & Insurance, Inc. |
| Address | 428 South Congress Ave, West Palm Beach, FL 3340 |
| Contact | Merlin@Oshaughnahill.com |
| Licensed States | FL |
| Premium to Surety | $7.50 per $100 in premium |
| Build-Up Fund | $5.00 per $100 in premium |

#### OSI POA Tiers

| Prefix | Max Bond Value | Description |
|--------|---------------|-------------|
| `OSI3` | $3,000 | Low-value bonds |
| `OSI6` | $6,000 | Mid-low bonds |
| `OSI16` | $16,000 | Mid-range bonds |
| `OSI51` | $51,000 | Mid-high bonds |
| `OSI101` | $101,000 | High-value bonds |
| `OSI251` | $251,000 | Maximum-value bonds |

---

### Palmetto Surety Corporation

| Property | Value |
|----------|-------|
| `Surety_ID` | `palmetto` |
| Company Name | Palmetto Surety Corporation |
| Address | 75 Port City Landing, Ste. 130, Mt. Pleasant, SC 29464 |
| Phone | 843.971.5441 / 866-372-0827 |
| Website | https://palmettosurety.net |
| Licensed States | FL, SC, NC, TN, TX, CT, LA, MS |
| Premium to Surety | $10.00 per $100 in premium |
| Build-Up Fund | $5.00 per $100 in premium |

#### Palmetto POA Tiers

| Prefix | Max Bond Value | Description |
|--------|---------------|-------------|
| `PSC5` | $5,000 | Low-value bonds |
| `PSC15` | $15,000 | Mid-low bonds |
| `PSC25` | $25,000 | Mid-range bonds |
| `PSC50` | $50,000 | Mid-high bonds |
| `PSC75` | $75,000 | High bonds |
| `PSC105` | $105,000 | Very high bonds |
| `PSC200` | $200,000 | Premium bonds |
| `PSC250` | $250,000 | Maximum bonds |

> **Note:** PSC200 and PSC250 are valid tiers but may not be in every inventory shipment.

---

## Current Inventory (as of 04/20/2026)

### OSI Inventory — Receipt dated 04/20/26, Total: 75 powers

| Value | Qty | Prefix | Range Start | Range End | Expiration |
|-------|-----|--------|-------------|-----------|------------|
| $3,000 | 30 | OSI3 | 20134295 | 20134324 | 31-Dec-26 |
| $6,000 | 15 | OSI6 | 20132136 | 20132150 | 31-Dec-26 |
| $16,000 | 16 | OSI16 | 20136624 | 20136639 | 31-Dec-26 |
| $51,000 | 10 | OSI51 | 20127651 | 20127660 | 31-Dec-26 |
| $101,000 | 2 | OSI101 | 20128283 | 20128284 | 31-Dec-26 |
| $251,000 | 2 | OSI251 | 20129019 | 20129020 | 30-Dec-26 |

### Palmetto Inventory — Package #192184, dated 04/20/2026, Total: 146 powers

| Prefix | Range Start | Range End | Qty |
|--------|-------------|-----------|-----|
| PSC5 | 2644670 | 2644777 | 108 |
| PSC15 | 2644778 | 2644790 | 13 |
| PSC25 | 2644791 | 2644809 | 19 |
| PSC50 | 2644810 | 2644813 | 4 |
| PSC75 | 2644814 | 2644814 | 1 |
| PSC105 | 2644815 | 2644815 | 1 |

**Grand Total: 221 powers available (75 OSI + 146 Palmetto)**

---

## POAInventory Collection Schema

```json
{
  "_id": "ObjectId",
  "poa_number": "20134295",
  "poa_prefix": "OSI3",
  "surety_id": "osi",
  "max_bond_value": 3000,
  "book_number": "receipt_2026-04-20",
  "status": "available",
  "expiration": "2026-12-31",
  "bond_case_id": null,
  "assigned_to_agent": "Brendan",
  "received_at": "2026-04-20T00:00:00Z",
  "used_at": null,
  "voided_at": null,
  "void_reason": null,
  "reported_at": null
}
```

### Indexes

- Unique index on `poa_number`
- Index on `surety_id + status` (for inventory queries)
- Index on `poa_prefix + status` (for tier availability)
- Index on `expiration` (for expiry warnings)

---

## Premium Calculation

Standard Florida bail bond premium: **10% of bond amount**

### Split Formulas

**OSI:**
```
premium = bond_amount * 0.10
surety_owed = premium * 0.075      # $7.50 per $100
buf_owed = premium * 0.05          # $5.00 per $100
agent_retains = premium - surety_owed - buf_owed
```

**Palmetto:**
```
premium = bond_amount * 0.10
surety_owed = premium * 0.10       # $10.00 per $100
buf_owed = premium * 0.05          # $5.00 per $100
agent_retains = premium - surety_owed - buf_owed
```

### Quick Reference

| Bond Amount | Premium | OSI Surety | OSI BUF | OSI Keeps | PSC Surety | PSC BUF | PSC Keeps |
|-------------|---------|-----------|---------|-----------|------------|---------|-----------|
| $3,000 | $300 | $22.50 | $15.00 | $262.50 | $30.00 | $15.00 | $255.00 |
| $5,000 | $500 | $37.50 | $25.00 | $437.50 | $50.00 | $25.00 | $425.00 |
| $10,000 | $1,000 | $75.00 | $50.00 | $875.00 | $100.00 | $50.00 | $850.00 |
| $25,000 | $2,500 | $187.50 | $125.00 | $2,187.50 | $250.00 | $125.00 | $2,125.00 |
| $50,000 | $5,000 | $375.00 | $250.00 | $4,375.00 | $500.00 | $250.00 | $4,250.00 |
| $100,000 | $10,000 | $750.00 | $500.00 | $8,750.00 | $1,000.00 | $500.00 | $8,500.00 |
