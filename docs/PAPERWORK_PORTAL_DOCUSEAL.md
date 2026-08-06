# Shamrock Paperwork Portal + DocuSeal

> **Status:** APPROVED architecture (2026-08-06)  
> **Replaces:** SignNow as e-sign backbone  
> **Host:** Hetzner VPS · Docker Compose profile `paperwork`  
> **Public portals:** `https://paperwork.shamrockbailbonds.biz`  
> **DocuSeal:** `https://sign.shamrockbailbonds.biz` (self-hosted open source)  
> **Upstream:** https://github.com/docusealco/docuseal  

---

## 1. Can we do this?

**Yes.** We already have:

| Capability | Where it lives today |
|------------|----------------------|
| Bond case, surety, POA, match gate | Mongo + Bond Desk |
| Packet field hydration (OSI / Palmetto) | `signnow_packet_service` → will target DocuSeal templates |
| Identity media (DL/selfie storage) | `identity_media_service` + `dashboard/uploads` |
| Open-source OCR stack | `scrapers/captcha_ocr.py` + `requirements-ocr-extra.txt` (**PaddleOCR**, Tesseract, EasyOCR, ddddocr) |
| VPS + Docker + Nginx TLS | Production Hetzner |
| Brand assets (transparent logo) | `docs/brand/shamrock_logo_transparent.png` |

We are **not** inventing a new CRM — we are adding a **role-scoped portal** and swapping e-sign from SignNow → **DocuSeal** (AGPL open source, Docker-first).

---

## 2. Product decisions (locked)

| # | Decision |
|---|----------|
| 1 | **One subdomain with roles:** `paperwork.shamrockbailbonds.biz` → `/i/...` indemnitor, `/d/...` defendant, `/staff/...` staff-only modals |
| 2 | **Auth:** Magic link **or** email entry → then **6-digit PIN** (set after first login or staff-assigned). PIN is case/role-scoped, not the agency dashboard PIN. |
| 3 | **OCR:** Open source only. **Primary = PaddleOCR** (already in stack + Grok shortlist). Tesseract fallback. Not cloud ID vendors. |
| 4 | **Surety:** Staff chooses OSI or Palmetto on the bond case; portals only fill role fields. Same rules; template field maps differ slightly. |
| 5 | **Post-transaction edits:** Only **POA / power number** may change after bond workflow starts (voids, human error). Everything else is versioned / audit, not free edit. |
| 6 | **Bond without full payment/signatures:** Allowed only via **staff exception modal**: second staff PIN + written reason + policy acknowledgments (premium still due; full premium liability). |
| 7 | **E-sign:** DocuSeal self-hosted — **no SignNow** for new packets. |
| 8 | **UX:** Dark mode toggle for portal users; Shamrock brand (green/dark, transparent logo). |
| 9 | **SEO:** Portal routes `noindex` (same rule as school LMS). |

---

## 3. Role flows

### 3.1 Indemnitor (`/i/:token` or magic link → PIN)

1. Unlock with **6-digit PIN** (after magic link or staff-issued access).
2. **Selfie** (required) + **ID scan** (required).
3. **OCR** pre-fills legal name, DOB, address, DL#, etc.
4. **Address confirmation modal:** “Is this address correct?” → Yes / No → if No, edit address.
5. Complete any remaining indemnitor fields not filled by OCR.
6. **Initial + sign** via embedded DocuSeal submitter form.
7. Status written back to Mongo + audit_events.

### 3.2 Defendant (`/d/:token`)

Same structure as indemnitor:

1. PIN unlock  
2. Selfie **required**  
3. ID scan **required**  
4. OCR pre-fill + **address confirmation popup**  
5. Remaining defendant fields  
6. Initial + sign (DocuSeal)

OCR name/address values hydrate **every packet field** where defendant/indemnitor identity appears for the selected surety template set.

### 3.3 Staff exception — post bond without complete paperwork

If staff posts a bond before payment and/or all signatures:

1. Staff opens **exception modal** in portal staff surface (or Bond Desk).
2. **Second staff PIN** (6-digit staff paperwork PIN or dashboard PIN — separate secret recommended).
3. Required fields:
   - Reason (enum + free text)
   - Expected paperwork completion date/time
   - Acknowledgments (checkboxes):
     - Premium is due regardless of circumstance  
     - Party remains liable for full premium if they elect to proceed  
     - Company policy accepted  
4. Immutable `audit_events` row + bond status note.
5. Portal remains open for parties to finish selfie/ID/sign later.

### 3.4 POA change after transaction

- Only **power/POA number** is editable post-start.
- Requires staff auth + reason (void, error, replacement).
- Old POA released per inventory rules; new POA from same surety inventory.
- Packet may need re-hydration / re-send in DocuSeal (new submission).

---

## 4. OCR architecture (open source)

### Decision: **PaddleOCR primary**

From stack + your Grok OCR shortlist:

| Engine | Role |
|--------|------|
| **PaddleOCR** | Primary for FL DL / ID photos (layout + multilingual) |
| **Tesseract** | Lightweight fallback (already in Docker images) |
| EasyOCR | Optional secondary vote |
| ddddocr | Keep for captchas only — **not** for IDs |

**Pipeline:**

```
ID image upload
  → preprocess (deskew, contrast, crop)
  → PaddleOCR text lines
  → field parser (regex + keyword anchors for Name / DOB / Address / DL / Exp)
  → optional PDF417 barcode decode if present (AAMVA)
  → return structured IdOcrResult
  → UI confirm address modal
  → map into bond packet prefill (OSI vs Palmetto field keys)
```

Implementation module (planned): `services/id_ocr_service.py`  
API (portal): `POST /api/paperwork/ocr/id` (authenticated session).

**PII:** ID images stored under identity media rules; never log raw DL numbers to Slack/console.

---

## 5. DocuSeal (replace SignNow)

### 5.1 What DocuSeal is

Open-source document fill + e-sign ([docusealco/docuseal](https://github.com/docusealco/docuseal)):

- PDF form builder, multi-submitter, API + webhooks  
- Docker image: `docuseal/docuseal`  
- Embedded signing (JS / React) for our portal  
- Self-host on VPS with Postgres  

### 5.2 Deploy topology

```
Browser
  │
  ├─ paperwork.shamrockbailbonds.biz  → Portal (Next/FastAPI) :5310
  │         │ selfie / ID / form / theme
  │         └─ embeds DocuSeal signing form
  │
  └─ sign.shamrockbailbonds.biz       → DocuSeal :5300
            │ templates, submissions, signed PDFs
            └─ webhook → leads dashboard API
```

Compose profile: **`paperwork`**

```bash
docker compose --profile paperwork up -d
```

Services:

| Service | Image | Port (host) | Purpose |
|---------|-------|-------------|---------|
| `docuseal` | `docuseal/docuseal:latest` | 5300 | Signing UI + API |
| `docuseal-postgres` | `postgres:16` | internal | DocuSeal DB |
| `paperwork-portal` | (app image, later) | 5310 | PIN portal |

Nginx:

- `sign.shamrockbailbonds.biz` → `127.0.0.1:5300`  
- `paperwork.shamrockbailbonds.biz` → `127.0.0.1:5310`  

Volumes (named, backed up daily — **do not lose like Postiz**):

- `docuseal-data`  
- `docuseal-postgres-data`  

### 5.3 Env (VPS `.env`)

```env
DOCUSEAL_HOST=sign.shamrockbailbonds.biz
DOCUSEAL_URL=https://sign.shamrockbailbonds.biz
DOCUSEAL_API_KEY=          # from DocuSeal admin after first login
DOCUSEAL_WEBHOOK_SECRET=
DOCUSEAL_DB_PASSWORD=
PAPERWORK_PUBLIC_URL=https://paperwork.shamrockbailbonds.biz
PAPERWORK_PIN_PEPPER=      # separate from DASHBOARD_PIN
PAPERWORK_STAFF_EXCEPTION_PIN=  # 6-digit second pin for exception modal
```

### 5.4 Integration pattern (CRM → DocuSeal)

```
BondCase ready (match + surety + POA)
  → build prefill map (existing SignNow hydrator logic, retargeted)
  → DocuSeal API: create submission from surety template
  → assign submitters: indemnitor email, defendant email
  → store docuseal_submission_id on paperwork_packets
  → parties open portal → complete ID/OCR → open embedded sign URL
  → webhook form.completed → update packet status, audit, unlock payment / active bond
```

### 5.5 Migration from SignNow

| Phase | Action |
|-------|--------|
| M0 | Deploy DocuSeal + docs (this phase) |
| M1 | Upload OSI + Palmetto PDFs as DocuSeal templates; map fields |
| M2 | Portal MVP: auth + ID OCR + embed sign |
| M3 | Parallel run: new bonds → DocuSeal only |
| M4 | Freeze SignNow for new packets; keep read-only archive of old packets |

Legacy SignNow code remains until M4 for historical packets; **new** work targets DocuSeal only.

### 5.6 Branding DocuSeal

- Company name: **Shamrock Bail Bonds**  
- Logo: `docs/brand/shamrock_logo_transparent.png` (RGBA, bg removed)  
- Colors: Shamrock green `#00d26a` / dark slate  
- Prefer white-label options available on self-host / Pro if needed  

---

## 6. Data model additions (Mongo)

```
paperwork_access
  - access_id, bond_case_id, role: indemnitor|defendant|staff
  - pin_hash (6-digit, peppered), pin_set_at
  - magic_token_hash, expires_at
  - selfie_media_id, id_media_id, ocr_payload
  - status: pending|identity|form|signing|complete

paperwork_packets  (extend)
  - esign_provider: docuseal|signnow_legacy
  - docuseal_template_id, docuseal_submission_id
  - surety_id: osi|palmetto

bond_exceptions
  - bond_case_id, reason, expected_complete_at
  - staff_actor, staff_pin_verified_at
  - acknowledgments: { premium_due, full_liability, policy }
  - audit_event_id
```

---

## 7. Portal UX (dark mode)

- Default: system preference; user toggle → `localStorage` key `sl-paperwork-theme`
- Dark palette aligned with Super CRM (slate + shamrock green)
- Mobile-first (selfie + ID on phone)
- Progress stepper: Unlock → Identity → Confirm → Form → Sign → Done

---

## 8. Security / compliance

- Fail closed: no packet without validated match + bond case + surety  
- Portal PINs ≠ agency dashboard PIN  
- Staff exception requires second PIN + immutable audit  
- Minimize PII in logs  
- All portal routes `noindex` + robots disallow  
- DocuSeal webhooks HMAC-verified  

---

## 9. Implementation roadmap

| Slice | Deliverable |
|-------|-------------|
| **S0** | This doc + DocuSeal compose profile + nginx conf + brand assets |
| **S1** | DocuSeal live on VPS (`sign.…`), admin login, template upload smoke |
| **S2** | `id_ocr_service` (PaddleOCR) + staff-facing test endpoint |
| **S3** | Portal shell: magic link + 6-digit PIN + dark mode + branding |
| **S4** | Indemnitor + defendant identity flow (selfie, ID, address confirm) |
| **S5** | DocuSeal embed + webhook → packet complete |
| **S6** | Staff exception modal + POA-only post-edit |
| **S7** | Deprecate SignNow for new bonds |

---

## 10. Ops commands

```bash
# Start DocuSeal stack
cd /opt/shamrock-leads
docker compose --profile paperwork up -d

# Backup DocuSeal DB daily (required)
docker exec shamrock-docuseal-postgres pg_dump -U docuseal docuseal | gzip > /opt/backups/docuseal-$(date +%F).sql.gz
```

---

## 11. Related docs

- Surety rules: `docs/policies/surety-policy.md`  
- Matching: `docs/policies/matching-policy.md`  
- Signature policy: update to DocuSeal in S5  
- Brand: `BRAND.md` + `docs/brand/shamrock_logo_transparent.png`  

---

**Owner:** Shamrock platform  
**Last updated:** 2026-08-06  
**Sign-off:** Product decisions locked per Brendan greenlight + adjustments (defendant ID/selfie parity, 6-digit PIN, open-source OCR, DocuSeal, staff exception second PIN, POA-only post-edit, dark mode).
