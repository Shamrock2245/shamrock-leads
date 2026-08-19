# Shamrock Paperwork Portal + DocuSeal

> **Status:** APPROVED architecture (2026-08-06)
> **E-sign backbone:** DocuSeal only
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
| Packet field hydration (OSI / Palmetto) | `docuseal_service` → targets the two live DocuSeal templates |
| Identity media (DL/selfie storage) | `identity_media_service` + `dashboard/uploads` |
| Open-source OCR stack | `scrapers/captcha_ocr.py` + `requirements-ocr-extra.txt` (**PaddleOCR**, Tesseract, EasyOCR, ddddocr) |
| VPS + Docker + Nginx TLS | Production Hetzner |
| Brand assets (transparent logo) | `docs/brand/shamrock_logo_transparent.png` |

We are **not** inventing a new CRM — we are adding a **role-scoped portal** and standardizing e-sign on **DocuSeal** (AGPL open source, Docker-first).

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
| 7 | **E-sign:** DocuSeal self-hosted — **no SignNow** in the active workflow. |
| 8 | **UX:** Dark mode toggle for portal users; Shamrock brand (green/dark, transparent logo). |
| 9 | **SEO:** Portal routes `noindex` (same rule as school LMS). |

---

## 3. Role flows

### 3.1 Indemnitor (`/i/:token` or magic link → PIN)

Order is fixed (no skip):

1. **Unlock** with **6-digit PIN** — required *in addition to* magic link (or the PIN they set after first login / staff-issued access).
2. **Selfie** (required) + **government ID upload** (required).
3. **OCR** pre-fills legal name, DOB, address, DL#, expiration, etc.
4. **Address confirmation modal:** “Is this address correct?” → **Yes** keeps OCR value / **No** opens editable address fields.
5. Complete **remaining fields not filled by OCR** (phone, employer, relationship, etc.).
6. **Initial + sign** via embedded DocuSeal submitter form.
7. Status written back to Mongo + `audit_events`.

### 3.2 Defendant (`/d/:token`) — full parity with indemnitor

Defendant is **not** a lighter path. Same gates:

1. **6-digit PIN unlock**
2. **Selfie required**
3. **ID upload required**
4. **OCR pre-fill** + **address confirmation popup** (correct? if not, correct it)
5. **Remaining defendant fields** not filled by OCR
6. **Initial + sign** (DocuSeal)

**Packet hydration rule:** OCR identity for the party maps into **every template field** where that party’s name/address/DOB/DL appears across the **entire** OSI or Palmetto packet (not a single form page). Rules are the same for both sureties; field *keys* differ slightly per template set.

### 3.3 Staff exception — post bond without complete paperwork / payment

If a bond is posted **without** full payment and/or all signatures, it is **blocked** unless staff completes the in-app exception modal:

1. Staff opens **exception modal** (`/staff/...` or Bond Desk deep-link).
2. **Second PIN input** (6-digit `PAPERWORK_STAFF_EXCEPTION_PIN` — separate from party PINs and recommended separate from agency dashboard PIN).
3. Required fields (cannot submit incomplete):
   - **Reason** (enum + free text) — why posting early
   - **Expected paperwork completion** date/time (when docs *will* be finished)
   - **Acknowledgments** (all required checkboxes):
     - Premium is due **no matter the circumstance**
     - Party remains **liable for the full premium** if they choose to do the bond
     - Company policy accepted by staff on record
4. Immutable `audit_events` row + bond status note; exception cannot be silent.
5. Portal stays open for indemnitor/defendant to finish selfie / ID / remaining fields / sign.

### 3.4 POA change after transaction

- Only **power/POA number** is editable post-start.
- Requires staff auth + reason (void, error, replacement).
- Old POA released per inventory rules; new POA from same surety inventory.
- Packet may need re-hydration / re-send in DocuSeal (new submission).

---

## 4. OCR architecture (open source only)

### Decision: **PaddleOCR primary** (no cloud ID vendors)

Your Grok shortlist (desktop screenshot 2026-08-06) + what we already ship:

| Engine | In stack today? | Role for paperwork |
|--------|-----------------|--------------------|
| **PaddleOCR** | Yes (`requirements.txt` optional + captcha ensemble) | **Primary** — FL DL / ID photos, layout-aware |
| **Tesseract** | Yes (Docker binary + CLI) | Fallback for clean printed lines |
| **EasyOCR** | Optional extra | Secondary vote if Paddle confidence low |
| **Surya** | Not installed | Optional later for multi-page / layout docs — **not** required for DL face |
| **ddddocr** | Yes | Captchas only — **never** for government IDs |

**Why PaddleOCR:** already in our OCR ensemble (`scrapers/captcha_ocr.py`), free/open-source, strongest production layout OCR on the shortlist, no per-scan SaaS cost, runs on the VPS.

**Pipeline:**

```
ID image upload (portal, authenticated)
  → store under identity_media (encrypted path rules)
  → preprocess (deskew, contrast, crop card region)
  → PaddleOCR text lines (+ Tesseract fallback)
  → field parser (regex + keyword anchors: Name / DOB / Address / DL / Exp)
  → optional PDF417 barcode decode if present (AAMVA)
  → return structured IdOcrResult
  → UI: address confirm modal → remaining fields form
  → map into bond packet prefill for ALL indemnitor or defendant slots
      (OSI vs Palmetto field key maps)
```

Implementation module (planned): `services/id_ocr_service.py`
API (portal): `POST /api/paperwork/ocr/id` (session + role scoped).

**PII:** ID images stored under identity media rules; never log raw DL numbers, full addresses, or SSNs to Slack/console.

---

## 5. DocuSeal

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
  → build prefill map via `DocuSealService.prefill_values_from_bond`
  → DocuSeal API: create submission from surety template
  → assign submitters: indemnitor email, defendant email
  → store docuseal_submission_id on paperwork_packets
  → parties open portal → complete ID/OCR → open embedded sign URL
  → webhook form.completed → update packet status, audit, unlock payment / active bond
```

### 5.5 Active DocuSeal-only workflow

| Phase | Action |
|-------|--------|
| M0 | Deploy DocuSeal + docs (this phase) |
| M1 | Upload OSI + Palmetto PDFs as DocuSeal templates; map fields |
| M2 | Portal MVP: auth + ID OCR + embed sign |
| M3 | Parallel run: new bonds → DocuSeal only |
| M4 | Complete: SignNow is retired for active workflow; historical fields are read-only only |

Active work targets DocuSeal only using the OSI and Palmetto templates in the DocuSeal account.

---

## 5.6 DocuSeal REST API Reference & Multi-User Support Manual

### 1. API Endpoints Overview (`https://sign.shamrockbailbonds.biz/api/v1`)
- **Authorization**: Header `X-Auth-Token: <DOCUSEAL_API_KEY>` or `Authorization: Bearer <DOCUSEAL_API_KEY>`.
- **`GET /api/v1/templates`**: List active templates. `DOCUSEAL_TEMPLATE_ID_OSI` (1) for OSI, `DOCUSEAL_TEMPLATE_ID_PALMETTO` for Palmetto.
- **`POST /api/v1/submissions`**: Create signing submission with submitters list and prefill values.
- **`PUT /api/v1/submitters/{id}`**: Update prefill field values (`values` / `fields`), change email/phone, or request email/SMS re-send.
- **`POST /api/webhooks/docuseal`**: Receives `form.started`, `form.viewed`, and `form.completed`. Signed PDFs are auto-formatted as `<LastName>_<MMDDYY>_<SURETY>.pdf` and archived to Google Drive `Completed Bonds/{surety}/{date}/`.

### 2. Employee Guidance (Bondsmen, Staff, God-Admin)
- **Sign First, Bind Defendant Later**: If an indemnitor arrives before the defendant's booking record is indexed, create an unassigned packet (`defendant_name="To Be Named"`). The indemnitor can complete and sign their paperwork immediately. Staff can attach/bind defendant details later via `POST /api/paperwork/packets/{packet_id}/bind-defendant`.
- **Resending Links**: In case of lost SMS or email, staff can click **Resend Link** in the dashboard (`POST /api/paperwork/{packet_id}/docuseal/resend`) or copy the direct `/s/{slug}` link.
- **Template Audit**: Confirm template roles match `indemnitor`, `Coindemnitor`, `Defendant` before dispatching.

### 3. Client Guidance (Defendants & Indemnitors)
- **Mobile & Touch Signing (`https://paperwork.shamrockbailbonds.biz/`)**: Optimized for touchscreens, mobile Safari/Chrome, and Apple Pencil stylus input.
- **ID Scan Auto-Fill**: Indemnitors scan their Driver's License/ID → PaddleOCR auto-populates name, address, DL#, and DOB.
- **Instant Indemnitor Signing**: Indemnitors click **✍️ Sign Paperwork Now** to sign their documents instantly on phone/iPad without delay.


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
  - esign_provider: docuseal|none
  - docuseal_template_id, docuseal_submission_id
  - surety_id: osi|palmetto

bond_exceptions
  - bond_case_id, reason, expected_complete_at
  - staff_actor, staff_pin_verified_at
  - acknowledgments: { premium_due, full_liability, policy }
  - audit_event_id
```

---

## 7. Packet composition (source of truth: `templates/`)

Staff starts the workflow from the dashboard (**Write Bond** / **✍️ Bond** on
`leads.shamrockbailbonds.biz`). Agent selects **surety** (OSI or Palmetto) at
workflow start → that choice selects which surety folder is merged with the
always-on Shamrock agnostic set.

Local blanks live under:

```
templates/
├── surety-agnostic-shamrock/   # EVERY bond (both sureties)
├── osi/                        # When surety_id = osi
└── palmetto/                   # When surety_id = palmetto
```

| Surety chosen | Packet = |
|---------------|----------|
| **OSI** | `surety-agnostic-shamrock/*` + `osi/*` |
| **Palmetto** | `surety-agnostic-shamrock/*` + `palmetto/*` (+ shared legal from `osi/`: promissory-note, disclosure-form) |

Hydration reuses the existing field maps in:

- `dashboard/services/docuseal_service.py`
- `dashboard/bond_pdf_service.py` (appearance bonds)
- `dashboard/paperwork_pdf_service.py` (stitch / paths)
- Dashboard Write Bond modal (`sl-features.js` → `POST /api/write-bond`)

### 7.1 Surety-agnostic (every bond)

| Document | File | Who acts | Notes |
|----------|------|----------|-------|
| **Cover / header** | `paperwork-header.pdf` | — (first page of every packet) | Packet page 1 |
| **FAQ — Cosigners** | `faq-cosigners.pdf` | Indemnitor **and** defendant **initial** | Both parties initial so each understands the other’s role |
| **FAQ — Defendants** | `faq-defendants.pdf` | Indemnitor **and** defendant **initial** | Same dual-role initial requirement |
| **Master waiver** | `master-waiver.pdf` | Every indemnitor **and** defendant **sign** | Multi-indemnitor: **each** indemnitor must sign |
| **SSA release** | `ssa-release.pdf` | Every non-agent person on the bond | **Every indemnitor** + **defendant** (agents do **not** sign) |
| **Payment plan** | `payment-plan.pdf` | Defendant **and** indemnitor **sign** | **Always** in the packet (OSI template 1 + Palmetto template 3). Paid-in-full: down = premium, balance = $0, empty schedule. Financing: staff fills `payment_due_date_1–4` / `payment_amount_1–4` before send. No extra initials; no credit-card / wage-assignment add-ons. |

### 7.2 Surety-specific (folder selected by agent)

| Document | OSI | Palmetto | Multiplication |
|----------|-----|----------|----------------|
| Appearance bond | `Appearance Bond blank.pdf` | `Shamrock Palmetto Official Appearance Bond.pdf` | **1 PDF per criminal charge** (print / wet-ink — not e-sign) |
| Indemnity agreement | `indemnity-agreement.pdf` | `indemnity-agreement-palmetto.pdf` | **Per indemnitor** |
| Defendant application | `defendant-application.pdf` | `defendant-application-palmetto.pdf` | Static / per defendant |
| Surety terms | `surety-terms.pdf` | `surety-terms-palmetto.pdf` | Shared |
| Collateral receipt | `collateral-receipt.pdf` | `collateral-receipt-palmetto.pdf` | Serialized receipt # (OCR locate field; see §7.5) |
| Promissory note | `promissory-note.pdf` (shared legal) | same | Shared |
| Disclosure | `disclosure-form.pdf` (shared legal) | same | Shared |

### 7.3 Appearance bond fill rules (OSI + Palmetto)

Implemented today in `dashboard/bond_pdf_service.py` — keep identical under DocuSeal era
(appearance bonds stay **print / wet-ink / jail**, not portal e-sign).

| Rule | Detail |
|------|--------|
| **One form per charge** | N charges → N appearance bond PDFs |
| **One POA per appearance bond** | Never reuse a power number across charges |
| **Multiple case numbers OK** | One defendant may have several cases; case # may repeat across counts on same case |
| **Penal amount** | Full bond amount for that charge written in the bond body / amount fields |
| **Premium** | `premium = max(100.0, bond_amount * 0.10)` — 10% of penal, **minimum $100** |
| **Written premium** | Words: `WrittenPremiumAmount` (OSI) / `writtenPremiumAmount` / `writtenPremiumAmountField` (Palmetto) |
| **Numeric premium** | Digits: `NumericPremiumAmount` (OSI) / `calculatedPremiumField` (Palmetto) |
| **Court date unknown** | Put **`TBN`** (“To Be Notified”) in court date; **skip / leave blank court time** when date is TBN or out-of-county / not yet set |
| **Readable fill** | Use auto-scaling fonts so long names/addresses still fit and remain legible after fill (`_set_widget_value_with_scaling`) |

OSI key fields (actual PDF widgets):
`DefFirstName`, `DefLastName`, `DefAddress`, `DefCounty`, `DefCharge1`/`DefCharge1Line2`,
`BondAmountCharge1`, `CaseNum`, `PowerNum`, `CourtDate`, `CourtTime`,
`WrittenPremiumAmount`, `NumericPremiumAmount`, agent/agency fields, …

Palmetto key fields:
`defendantNameField`, `DefendantAddress`, `countyField`, `numericBondAmount`,
`chargesField1`/`chargesField2`, `powerNumField`, `CourtDateAndTimeField`,
`writtenPremiumAmount` / `writtenPremiumAmountField`, `calculatedPremiumField`, …

### 7.4 Multi-indemnitor + multi-signer rules

- Cases may have **1..N indemnitors**. Every document that requires an indemnitor signature
  must collect **all** of them (master waiver, SSA release, indemnity agreement copies, FAQ initials).
- Defendant always signs their own roles (FAQ initials, master waiver, SSA, defendant application as applicable).
- DocuSeal submitters: one submitter identity per person (role + email/phone + portal PIN).
- Portal PINs: staff assigns from Write Bond / packet finalize on the dashboard (case-scoped, 6-digit).

### 7.5 Collateral receipt serial numbers

- Physical / pre-printed collateral receipts carry **serialized numbers**.
- On scan/upload (or when preparing fill), **OCR** detects the printed serial location and
  maps it into the receipt form (do not invent serials).
- PaddleOCR primary (same open-source stack as ID OCR).

### 7.6 Kiosk mode (staff + walk-in)

Dashboard already surfaces **Kiosk (iPad) / Side-by-Side In Person** intake channel.

Kiosk mode for paperwork must support:

1. Staff selects surety (OSI / Palmetto)
2. Walk through packet checklist (agnostic + surety set)
3. Manual field override **or** full auto-hydrate from lead / match / charge_details
4. Issue PIN(s) + open portal (or hand tablet to party for selfie/ID/sign)
5. Exception path (second staff PIN) if posting without complete payment/signatures

Kiosk is not a separate product — same DocuSeal templates + same hydration, full-screen staff UX.

### 7.7 Post-sign archive → Google Drive

When **all required parties** have signed (DocuSeal webhooks complete):

1. Merge final PDFs
2. Upload under **Completed Bonds**:
   `https://drive.google.com/drive/folders/1WnjwtxoaoXVW8_B6s-0ftdCPf_5WfKgs`
3. Hierarchy (already partially implemented in `bond_lifecycle.py` / `bonds.py`):
   `Completed Bonds / {Surety label} / {Defendant folder} / signed packet`
4. Env: `COMPLETED_BONDS_FOLDER_ID` / `GOOGLE_DRIVE_OUTPUT_FOLDER_ID` = that folder ID

**Auth (required for production archive):**

| Priority | Method | Setup |
| -------- | ------ | ----- |
| 1 (required for My Drive) | User OAuth | `python scripts/get_gmail_token.py` grants Gmail + Calendar + **Drive**; set `GOOGLE_GMAIL_REFRESH_TOKEN` (optional alias `GOOGLE_DRIVE_REFRESH_TOKEN`) |
| 2 (Shared Drives only) | Service account | `GOOGLE_APPLICATION_CREDENTIALS` → SA JSON; folder must be a **Shared Drive** with SA as Content Manager |

**Known failure modes:**

| Error | Cause | Fix |
| ----- | ----- | --- |
| `invalid_scope` | OAuth token minted with Gmail/Calendar only | Re-run `get_gmail_token.py` (now includes Drive) and update `.env` |
| `storageQuotaExceeded` | SA writing into personal My Drive | Use OAuth (above). SA has zero My Drive quota |

```bash
python scripts/verify_drive_auth.py          # preflight (write probe)
python scripts/e2e_test_paperwork.py         # full DocuSeal + Drive
python scripts/e2e_test_paperwork.py --drive-only
```

DocuSeal completion uploads signed PDFs through the Drive helper.

### 7.8 Write Bond → portal PIN handoff

```
Dashboard (leads.…)  Write Bond / ✍️ Bond
  → choose surety (OSI | Palmetto)
  → charges / POAs / case #s / court date (or TBN)
  → hydrate packet from lead + indemnitor(s) + defendant
  → generate appearance bonds (print-only) + DocuSeal submission (e-sign set)
  → assign 6-digit PIN(s) for indemnitor(s) and defendant
  → send magic link / SMS / hand tablet (kiosk)
  → parties complete selfie + ID + remaining fields + initials/sign
  → webhooks → Mongo + Drive Completed Bonds
```

---

## 8. Portal UX (dark mode)

- Default: system preference; user toggle → `localStorage` key `sl-paperwork-theme`
- Dark palette aligned with Super CRM (slate + shamrock green)
- Mobile-first (selfie + ID on phone); **kiosk** full-screen variant for office iPad
- Progress stepper: Unlock → Identity → Confirm → Form → Sign → Done
- Form field text must remain **readable** after party/staff entry (font size floors, wrap, scale)

---

## 9. Security / compliance

- Fail closed: no packet without validated match + bond case + surety
- Portal PINs ≠ agency dashboard PIN
- Staff exception requires second PIN + immutable audit
- Minimize PII in logs
- All portal routes `noindex` + robots disallow
- DocuSeal webhooks HMAC-verified
- Appearance bonds never leave the print/wet-ink path for jail filing

---

## 10. Implementation roadmap

| Slice | Deliverable |
|-------|-------------|
| **S0** | This doc + DocuSeal compose profile + nginx conf + brand assets |
| **S1** | **Code done:** `docuseal_service.py`, webhook `/api/webhooks/docuseal`, packet push `/api/paperwork/{id}/docuseal`, health/templates. **Ops remaining:** VPS `compose --profile paperwork`, DNS/TLS, admin API key, template upload, webhook URL in DocuSeal admin. |
| **S1b** | SwipeSimple Gmail receipt poller for **bond** payments (school $199/$649 left to GAS) — `swipesimple_receipt_poller.py` + cron 5m |
| **S2** | `id_ocr_service` (PaddleOCR) + staff-facing test endpoint |
| **S3** | Portal shell: magic link + 6-digit PIN + dark mode + branding |
| **S4** | Indemnitor + defendant identity flow (selfie, ID, address confirm) |
| **S5** | Multi-submitter polish + sign-link handoff from Write Bond + webhook → Drive Completed Bonds (Drive helper already wired) |
| **S6** | Staff exception modal + POA-only post-edit + **kiosk mode** walkthrough |
| **S7** | Dual-role FAQ initials fields + collateral serial OCR + keep DocuSeal-only workflow enforced |

### S1 API surface (dashboard)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/paperwork/docuseal/health` | API key + reachability |
| GET | `/api/paperwork/docuseal/templates` | List DocuSeal templates |
| GET | `/api/paperwork/docuseal/templates/{id}` | Retrieve one template (fields/roles audit) |
| GET | `/api/paperwork/docuseal/submissions` | List submissions (`?status=pending`) |
| GET | `/api/paperwork/{packet_id}/docuseal/status` | Refresh status + sign links from DocuSeal |
| POST | `/api/paperwork/{packet_id}/docuseal/resend` | Re-send to pending submitters (optional role/id/email) |
| POST | `/api/paperwork/{packet_id}/docuseal` | Create multi-party submission (sign links, no forced email) |
| POST | `/api/webhooks/docuseal` | form/submission complete → Mongo + Drive Completed Bonds |
| POST | `/api/paperwork/docuseal/poll-swipesimple` | Manual Gmail SwipeSimple receipt poll |

---

## 11. Ops commands

```bash
# Start DocuSeal stack
cd /opt/shamrock-leads
docker compose --profile paperwork up -d

# Backup DocuSeal DB daily (required)
docker exec shamrock-docuseal-postgres pg_dump -U docuseal docuseal | gzip > /opt/backups/docuseal-$(date +%F).sql.gz
```

---

## 11b. DocuSeal CLI + agent skills

**CLI upstream:** https://github.com/docusealco/docuseal-cli
**Agent skills upstream:** https://github.com/docusealco/docuseal-agent-skills

### Install (dev workstation)

```bash
# CLI (Node 18+)
npm install -g docuseal

# Official skills (prefer named skills, not --all — avoids 70+ agent vendor dirs)
npx skills add docusealco/docuseal-agent-skills -y -s docuseal-cli -s docuseal-code

# Point CLI at self-hosted Shamrock (not DocuSeal cloud)
export DOCUSEAL_API_KEY="…"   # from DocuSeal admin after first login
export DOCUSEAL_SERVER="https://sign.shamrockbailbonds.biz"
docuseal configure --api-key "$DOCUSEAL_API_KEY" --server "$DOCUSEAL_SERVER"
docuseal configure --list
docuseal templates list -l 20
```

### Skills in this repo

| Skill | Path | Use |
|-------|------|-----|
| `docuseal-cli` | `.agent/skills/docuseal-cli/` | Shell CLI: templates, submissions, submitters |
| `docuseal-code` | `.agent/skills/docuseal-code/` | REST API, webhooks, embed/SDK reference |
| `shamrock-docuseal` | `.agent/skills/shamrock-docuseal/` | Shamrock roles, env, CRM vs CLI playbooks |

Also under `.agents/skills/` (agentskills.io layout). Global copies: `~/.grok/skills/`, `~/.claude/skills/`.

### What agents should automate with CLI

| Task | CLI |
|------|-----|
| Health / template inventory | `templates list`, `templates retrieve <id>` |
| Clone / archive templates | `templates clone`, `templates archive` |
| Pending signatures | `submissions list --status pending` |
| Inspect one packet | `submissions retrieve <id>` |
| Download signed docs (ops) | `submissions documents <id> --merge` |
| Re-send / fix submitter | `submitters update <id> --send-email` |
| Ad-hoc multi-party create | `submissions create --no-send-email -d 'submitters[…]'` |

**Bond packets in production** still go through `docuseal_service` / `POST /api/paperwork/{id}/docuseal` so Mongo, audit events, and Drive Completed Bonds stay consistent. CLI is for ops and agent automation around that path—not a bypass of the match → bond case → packet chain.

### Pro-marked CLI commands

`templates create-pdf|create-docx|create-html`, merge, and some create-from-file submission paths are marked **(Pro)** on DocuSeal cloud. On self-hosted OSS, verify before depending on them; UI template upload remains a valid fallback.

---

## 12. Related docs

- Template inventory: `templates/README.md`
- Surety rules: `docs/policies/surety-policy.md`
- Matching: `docs/policies/matching-policy.md`
- Signature policy: update to DocuSeal in S5
- Brand: `BRAND.md` + `docs/brand/shamrock_logo_transparent.png`
- Appearance bond code: `dashboard/bond_pdf_service.py`
- Packet manifest / hydration: `dashboard/services/docuseal_service.py`
- CLI skill: `.agent/skills/docuseal-cli/SKILL.md`
- Shamrock bridge skill: `.agent/skills/shamrock-docuseal/SKILL.md`

---

**Owner:** Shamrock platform
**Last updated:** 2026-08-09
**Sign-off:** Product decisions locked per Brendan greenlight + packet walkthrough (agnostic forms dual-initials, multi-indemnitor, appearance bond 10%/$100 + TBN, collateral OCR serials, kiosk, Drive Completed Bonds, Write Bond PIN handoff).
