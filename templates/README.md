# Bond Paperwork Templates

Local PDF blanks used by the packet stitcher, appearance-bond filler, offline
preview, and (target) **DocuSeal** self-hosted e-sign.

> **E-sign backbone:** DocuSeal (`sign.shamrockbailbonds.biz`) — see
> `docs/PAPERWORK_PORTAL_DOCUSEAL.md`. SignNow cloud template IDs in
> `dashboard/services/signnow_packet_service.py` remain for **legacy packets only**
> until cutover (S7).

## Layout

```
templates/
├── surety-agnostic-shamrock/   # Always included (both sureties)
│   ├── paperwork-header.pdf    # Cover page — first page of every packet
│   ├── faq-cosigners.pdf       # Both indemnitor + defendant initial
│   ├── faq-defendants.pdf      # Both indemnitor + defendant initial
│   ├── master-waiver.pdf       # Every indemnitor + defendant sign
│   ├── ssa-release.pdf         # Every non-agent person (all indemnitors + defendant)
│   └── payment-plan.pdf        # Only when balance remains on the bond
├── osi/                        # When surety_id = osi
│   ├── Appearance Bond blank.pdf
│   ├── indemnity-agreement.pdf
│   ├── defendant-application.pdf
│   ├── surety-terms.pdf
│   ├── collateral-receipt.pdf
│   ├── promissory-note.pdf     # shared legal (both sureties)
│   └── disclosure-form.pdf     # shared legal (both sureties)
└── palmetto/                   # When surety_id = palmetto
    ├── Shamrock Palmetto Official Appearance Bond.pdf
    ├── indemnity-agreement-palmetto.pdf
    ├── defendant-application-palmetto.pdf
    ├── surety-terms-palmetto.pdf
    └── collateral-receipt-palmetto.pdf
```

## Composition rule

| Surety (agent picks at Write Bond) | Packet = |
|------------------------------------|----------|
| **OSI** | `surety-agnostic-shamrock/*` + `osi/*` |
| **Palmetto** | `surety-agnostic-shamrock/*` + `palmetto/*` (+ shared legal from `osi/`) |

Staff starts from dashboard **Write Bond** / **✍️ Bond** on
`leads.shamrockbailbonds.biz` → selects surety → hydrates from lead / match /
charge_details → issues portal PIN(s) → parties sign via paperwork portal.

## Surety-agnostic form rules (every bond)

| Doc | Requirement |
|-----|-------------|
| `paperwork-header.pdf` | Cover page; always first |
| `faq-cosigners.pdf` | Place for **indemnitor and defendant** to **initial** (roles mutual understanding) |
| `faq-defendants.pdf` | Same dual-role initials |
| `master-waiver.pdf` | **Defendant + every indemnitor** sign (N indemnitors → N signature slots / copies) |
| `ssa-release.pdf` | Signed by **every person involved except agents** — all indemnitors + defendant |
| `payment-plan.pdf` | Include **only if** there is a remaining balance; send with packet |

## Appearance bond procedure (jail — print / wet-ink)

```
1. System fills PDF from arrest + charge + POA data (bond_pdf_service)
2. Store UNSIGNED file (dashboard/uploads/appearance_bonds/<packet>/)
3. Print the unsigned form(s)
4. Live (wet-ink) signature on the paper
5. Take signed original(s) to the jail
```

Do **not** route appearance bonds through DocuSeal, SignNow, Adobe Acrobat Sign,
or email e-sign. Indemnitor/defendant packet docs still use e-sign; the court
bond form does not.

### Appearance bond identity (non-negotiable)

| Rule | Meaning |
|------|---------|
| **1 bond form per charge** | N charges → N appearance bond PDFs for that defendant |
| **Case number per charge** | Each charge (and its bond form) is filed under a case # |
| **Multiple cases OK** | One defendant can have 26-CF-001 **and** 26-MM-002 (etc.) |
| **1 POA per charge** | Each power of attorney serial is exclusive to one charge — never re-use across charges |
| **Penal amount** | Full bond amount for that charge in the body / amount fields |
| **Premium** | `max($100, 10% of penal)` — written words + numeric fields |
| **Court unknown** | Court date = **`TBN`** (To Be Notified); leave court **time blank** when TBN |
| **Unsigned file storage** | System keeps the blank-filled print copy; live signature is on paper |
| **Readable fields** | Auto-scale fonts so filled values stay legible |

Example: Defendant with 2 counts on case `26-CF-100` and 1 count on `26-MM-200`
→ **3 unsigned appearance bond files**, **3 POAs**, case numbers
`[26-CF-100, 26-CF-100, 26-MM-200]` → print all three → wet-sign → jail.

### Premium field names (actual PDF widgets)

| Surety | Words (10% / min $100) | Numeric |
|--------|------------------------|---------|
| **OSI** | `WrittenPremiumAmount` | `NumericPremiumAmount` |
| **Palmetto** | `writtenPremiumAmount` / `writtenPremiumAmountField` | `calculatedPremiumField` |

## Collateral receipts

- Serialized pre-printed receipt numbers must appear on the filled form.
- Use **OCR** (PaddleOCR) to locate / capture the printed serial — do not invent.
- OSI: `collateral-receipt.pdf` · Palmetto: `collateral-receipt-palmetto.pdf`

## Multi-indemnitor

If more than one indemnitor is on the case:

- Every indemnitor signs **master-waiver**, **ssa-release**, and **indemnity-agreement**
- FAQ dual-initials still include defendant + each indemnitor as required by form layout
- Portal: separate PIN / submitter per person (or sequential kiosk handoff)

## Kiosk mode

Staff can walk the Write Bond flow **manually and/or automated** on office
hardware (dashboard channel `kiosk` / Side-by-Side In Person): surety select →
hydrate → review fields → issue PIN → party completes identity + sign on tablet.

## Data flow

1. **Defendant** — arrest scrape → lead / defendant record
2. **Indemnitor(s)** — intake → match → indemnitor profile(s)
3. **Write Bond** — surety + charges + POAs + court (or TBN)
4. **Hydrate** — field maps from packet service + `bond_pdf_service`
5. **E-sign set** — DocuSeal multi-submitter (portal + kiosk)
6. **Appearance bonds** — print-only parallel track
7. **Complete** — webhooks → Mongo audit → Google Drive **Completed Bonds**
   (`COMPLETED_BONDS_FOLDER_ID` = `1WnjwtxoaoXVW8_B6s-0ftdCPf_5WfKgs`)

```
Completed Bonds /
  {OSI | Palmetto} /
    {Defendant folder} /
      signed packet PDF(s)
```

## Code map

| Concern | Module |
|---------|--------|
| Path resolution + stitch | `dashboard/paperwork_pdf_service.py` |
| Appearance bond fill | `dashboard/bond_pdf_service.py` |
| Context + flatten + provider | `dashboard/services/packet_builder_service.py` |
| SignNow cloud packets (legacy) | `dashboard/services/signnow_packet_service.py` |
| DocuSeal (target) | `docs/PAPERWORK_PORTAL_DOCUSEAL.md` + service TBD |
| Adobe PDF Services + Sign | `dashboard/services/adobe_pdf_service.py` |
| API (config / finalize) | `dashboard/routers/paperwork.py` |
| Write Bond UI | `dashboard/sl-features.js` (`openBondModal`) |
| Drive archive | `dashboard/routers/bond_lifecycle.py`, `bonds.py` |
