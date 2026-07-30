# Bond Paperwork Templates

Local PDF blanks used by the packet stitcher, Adobe PDF Services fill/flatten,
and offline preview. **SignNow cloud template IDs** live in
`dashboard/services/signnow_packet_service.py` (`TEMPLATE_MAP`).

## Layout

```
templates/
├── surety-agnostic-shamrock/   # Always included (both sureties)
│   ├── paperwork-header.pdf
│   ├── faq-cosigners.pdf
│   ├── faq-defendants.pdf
│   ├── master-waiver.pdf
│   ├── ssa-release.pdf
│   └── payment-plan.pdf
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

| Surety | Packet =
|--------|---------|
| **OSI** | `surety-agnostic-shamrock/*` + `osi/*` |
| **Palmetto** | `surety-agnostic-shamrock/*` + `palmetto/*` (+ shared legal from `osi/`) |

## Data flow

1. **Defendant** — arrest scrape → lead / defendant record (name, booking, charges, bond, DOB, address, …)
2. **Indemnitor** — intake (Wix / Telegram / walk-in / phone) → match → indemnitor profile
3. **Hydrate** — `SignNowPacketService` field map + `paperwork_pdf_service` anchor fill
4. **Flatten** — Adobe PDF Services (`build_flattened_packet`) or local PyMuPDF
5. **E-sign** — SignNow (primary) **or** Adobe Acrobat Sign (per-client `esign_provider`)

Appearance bonds are **print-only / wet-ink only** (never e-signed via SignNow
or Adobe Sign). Generated via `dashboard/bond_pdf_service.py` from the OSI /
Palmetto appearance bond PDFs and **stored as unsigned files**.

### Appearance bond procedure (jail)

```
1. System fills PDF from arrest + charge + POA data
2. Store UNSIGNED file (dashboard/uploads/appearance_bonds/<packet>/)
3. Print the unsigned form(s)
4. Live (wet-ink) signature on the paper
5. Take signed original(s) to the jail
```

Do **not** route appearance bonds through SignNow, Adobe Acrobat Sign, or email
e-sign. Indemnitor/defendant packet docs still use e-sign; the court bond form
does not.

### Appearance bond identity (non-negotiable)

| Rule | Meaning |
|------|---------|
| **1 bond form per charge** | N charges → N appearance bond PDFs for that defendant |
| **Case number per charge** | Each charge (and its bond form) is filed under a case # |
| **Multiple cases OK** | One defendant can have 26-CF-001 **and** 26-MM-002 (etc.) |
| **1 POA per charge** | Each power of attorney serial is exclusive to one charge — never re-use across charges |
| **Unsigned file storage** | System keeps the blank-filled print copy; live signature is on paper |

Example: Defendant with 2 counts on case `26-CF-100` and 1 count on `26-MM-200`
→ **3 unsigned appearance bond files**, **3 POAs**, case numbers
`[26-CF-100, 26-CF-100, 26-MM-200]` → print all three → wet-sign → jail.

## Code map

| Concern | Module |
|---------|--------|
| Path resolution + stitch | `dashboard/paperwork_pdf_service.py` |
| Appearance bond fill | `dashboard/bond_pdf_service.py` |
| Context + flatten + provider | `dashboard/services/packet_builder_service.py` |
| SignNow cloud packets | `dashboard/services/signnow_packet_service.py` |
| Adobe PDF Services + Sign | `dashboard/services/adobe_pdf_service.py` |
| API (config / finalize) | `dashboard/routers/paperwork.py` |
