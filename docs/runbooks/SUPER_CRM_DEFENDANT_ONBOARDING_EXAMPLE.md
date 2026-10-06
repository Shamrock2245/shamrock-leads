# Super CRM — Example: Programmatic Defendant Onboarding & Bond Finalization

> **Target Platform:** Super CRM (`https://leads.shamrockbailbonds.biz` / `:5050`)  
> **Source Script:** [`scripts/examples/write_bond_super_crm.js`](../../scripts/examples/write_bond_super_crm.js)  
> **Related Specifications:** [`docs/SUPER_CRM.md`](../SUPER_CRM.md) · [`docs/runbooks/intake-to-signature.md`](./intake-to-signature.md) · [`docs/CURRENT_PAPERWORK_ARCHITECTURE_DOCUSEAL.md`](../CURRENT_PAPERWORK_ARCHITECTURE_DOCUSEAL.md)

---

## 1. Overview

This guide documents the end-to-end programmatic workflow to get a new defendant into the Super CRM, assign Powers of Attorney (POAs) across individual charges, generate the 14-document DocuSeal packet, and retrieve both the remote signing link and the in-person iPad signing launch URL.

This exact workflow is demonstrated with **Aaron Paul Schmidt** (Lee County booking `#1033474`, Court Case `#26CF017605`), charged with four separate offenses requiring specific POA tiers under **O'Shaughnahill Surety & Insurance (OSI)**.

---

## 2. Browser DevTools Console Snippet

When logged into the Super CRM dashboard as staff, paste the following snippet directly into the browser DevTools Console:

```javascript
(async () => {
  const j = (u, b) => fetch(u, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(b)
  }).then(async r => ({
    status: r.status,
    data: await r.json().catch(() => ({}))
  }));

  // Step 1: Add POAs to inventory for the surety (e.g. OSI)
  const items = [
    { poa_number: 'OSI-P6-116-26-0015', poa_full: 'OSI-P6-116-26-0015', poa_prefix: 'OSI-P6', surety_id: 'osi', max_bond_value: 6000 },
    { poa_number: 'OSI-P6-116-26-0016', poa_full: 'OSI-P6-116-26-0016', poa_prefix: 'OSI-P6', surety_id: 'osi', max_bond_value: 6000 },
    { poa_number: 'OSI-P3-116-26-0018', poa_full: 'OSI-P3-116-26-0018', poa_prefix: 'OSI-P3', surety_id: 'osi', max_bond_value: 3000 },
    { poa_number: 'OSI-P3-116-26-0019', poa_full: 'OSI-P3-116-26-0019', poa_prefix: 'OSI-P3', surety_id: 'osi', max_bond_value: 3000 },
  ];
  const add = await j('/api/poa/add', { surety_id: 'osi', items });

  // Step 2: Bulk-assign POAs to defendant / bond case
  const assign = await j('/api/poa/bulk-assign', {
    surety_id: 'osi',
    bond_case_id: '1033474',
    defendant_name: 'SCHMIDT, AARON PAUL',
    assignments: [
      { poa_number: 'OSI-P6-116-26-0015', charge: '893.13-6a', appearance_bond_number: '26CF017605' },
      { poa_number: 'OSI-P6-116-26-0016', charge: '790.23-1a', appearance_bond_number: '26CF017605' },
      { poa_number: 'OSI-P3-116-26-0018', charge: '843.02', appearance_bond_number: '26CF017605' },
      { poa_number: 'OSI-P3-116-26-0019', charge: '893.147-1', appearance_bond_number: '26CF017605' },
    ]
  });

  // Step 3: Finalize DocuSeal paperwork packet with structured charge details
  const fin = await j('/api/paperwork/packet/finalize', {
    booking_number: '1033474',
    county: 'Lee',
    surety_id: 'osi',
    provider: 'docuseal',
    signer_email: 'admin@shamrockbailbonds.biz',
    include_payment_plan: true,
    include_defendant: true,
    send_email: false,
    poa_number: 'OSI-P6-116-26-0015',
    charge_details: [
      { offense_code: '893.13-6a', bond_amount: 5000, case_number: '26CF017605', poa_number: 'OSI-P6-116-26-0015' },
      { offense_code: '790.23-1a', bond_amount: 5000, case_number: '26CF017605', poa_number: 'OSI-P6-116-26-0016' },
      { offense_code: '843.02', bond_amount: 1000, case_number: '26CF017605', poa_number: 'OSI-P3-116-26-0018' },
      { offense_code: '893.147-1', bond_amount: 1000, case_number: '26CF017605', poa_number: 'OSI-P3-116-26-0019' },
    ]
  });

  // Step 4: Extract DocuSeal signing link and build iPad in-person signing URL
  const d = fin.data || {};
  const ds = d.send_results?.docuseal || {};
  const link = d.signing_link || ds.signing_link || ds.sign_links?.[0] || ds.submitters?.[0]?.sign_url || '';
  const ipad = link ? `https://paperwork.shamrockbailbonds.biz/?mode=ipad&link=${encodeURIComponent(link)}` : '';

  const out = {
    add,
    assign,
    finalizeStatus: fin.status,
    packet_id: d.packet_id,
    error: d.error || d.message,
    link,
    ipad
  };

  console.log(out);
  return out;
})();
```

---

## 3. Step-by-Step API Breakdown

### Step 1: Inventory Upload (`POST /api/poa/add`)
Before powers can be assigned to a bond, they must be registered in the `poa_inventory` collection with their maximum bond value tier.
- **`poa_number`**: Unique serial number (e.g., `OSI-P6-116-26-0015`).
- **`poa_prefix`**: Tier code (`OSI-P6` for $6,000 max, `OSI-P3` for $3,000 max).
- **`surety_id`**: Either `osi` or `palmetto`.
- **`max_bond_value`**: Dollar cap for the power.

### Step 2: Bulk Assignment (`POST /api/poa/bulk-assign`)
Links specific POAs from inventory to the target defendant's charges and court case number:
- **`bond_case_id`**: Booking number or BondCase UUID (`1033474`).
- **`defendant_name`**: Standardized defendant name (`SCHMIDT, AARON PAUL`).
- **`assignments`**: Array mapping each `poa_number` to its statutory `charge` and `appearance_bond_number` (court case number, e.g. `26CF017605`).

### Step 3: Packet Finalization (`POST /api/paperwork/packet/finalize`)
Orchestrates the 14-document packet via DocuSeal:
- **`booking_number`**: Arrest identifier (`1033474`).
- **`county`**: Florida county (`Lee`).
- **`surety_id`**: Explicit surety (`osi`).
- **`provider`**: `docuseal` (open-source e-sign backbone).
- **`signer_email`**: Recipient email address.
- **`include_payment_plan`**: Adds payment plan agreement when true.
- **`include_defendant`**: Includes defendant signature sections.
- **`send_email: false`**: Suppresses automatic provider email so Shamrock controls distribution via iMessage / SMS / iPad.
- **`charge_details`**: Structured array with per-charge bond amounts, statutory offense codes, court case numbers, and power numbers.

### Step 4: Signing URLs
- **Remote Signer Link**: Direct DocuSeal submitter URL returned in `send_results.docuseal`.
- **In-Person iPad URL**: Wrapped launchpad URL:
  `https://paperwork.shamrockbailbonds.biz/?mode=ipad&link=<ENCODED_DOCUSEAL_LINK>`
  This enables smooth Apple Pencil / stylus in-person signing on the office iPad without leaving the Shamrock portal.

---

## 4. Output Contract

The script outputs a summary object in the console:

```json
{
  "add": { "status": 200, "data": { "success": true, "inserted": 4 } },
  "assign": { "status": 200, "data": { "success": true, "assigned": 4 } },
  "finalizeStatus": 200,
  "packet_id": "pkt_7d9e2a1b-4f5c-43a1-9012-abcdef123456",
  "link": "https://sign.shamrockbailbonds.biz/s/abc123xyz",
  "ipad": "https://paperwork.shamrockbailbonds.biz/?mode=ipad&link=https%3A%2F%2Fsign.shamrockbailbonds.biz%2Fs%2Fabc123xyz"
}
```
