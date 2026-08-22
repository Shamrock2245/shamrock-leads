# Production Verification: DocuSeal Configuration & Surety Resolution

> **Date / Timestamp:** 2026-08-22 20:37 UTC  
> **Verifier:** `@paperwork-clipboard` / Platform Systems Engineering  
> **Scope:** Cross-repository audit spanning `shamrock-leads` (Super CRM) and `shamrock-telegram-app`  
> **Target Checklist Items:** [`ECOSYSTEM_PROD_CHECKLIST.md`](../ECOSYSTEM_PROD_CHECKLIST.md) §B5 & §E3  
> **Live Template Status:** 🟢 **VERIFIED ACTIVE** (Live DocuSeal API Token Authenticated)  
> **Overall Checklist Status:** 🔲 **Human-Gated / Live Smoke Pending** (Awaiting staff selection of an existing validated case)

---

## 1. Summary of Configuration & Verified Resolution State

| Configuration Key Name | Configured Target / Purpose | Template Name in DocuSeal | Template ID | Live API Probe (`/api/templates`) | Verified Roles | Live Status |
|---|---|---|---|---|---|---|
| `DOCUSEAL_URL` | Self-hosted Host (`https://sign.shamrockbailbonds.biz`) | N/A | N/A | HTTPS GET `/` (Nginx/DocuSeal) | N/A | 🟢 `200 OK` |
| `DOCUSEAL_API_KEY` | DocuSeal API Token | N/A | `masked` | HTTPS GET `/api/templates` | Admin Auth | 🟢 `200 OK` |
| `DOCUSEAL_TEMPLATE_ID_OSI` | OSI 13-Doc Combined Packet | `shamrock-osi-paperwork-complete` | `1` | HTTPS GET `/api/templates/1` | `bondsman`, `indemnitor`, `defendant`, `coindemnitor` | 🟢 Verified Active |
| `DOCUSEAL_TEMPLATE_ID_PALMETTO` | Palmetto Surety Corporation Packet | `shamrock-palmetto-paperwork-complete` | `3` | HTTPS GET `/api/templates/3` | `indemnitor`, `defendant`, `bondsman`, `coindemnitor` | 🟢 Verified Active |
| `DOCUSEAL_WEBHOOK_SECRET` | Signature verification secret | N/A | `masked` | HMAC-SHA256 crypto check | N/A | 🟢 Pass |

---

## 2. Invariant & Safety Guard Verification

### A. Surety-to-Template Binding Isolation
- **Palmetto Fallback Guard:** Verified that `resolve_template_id_for_surety("palmetto")` strictly reads `DOCUSEAL_TEMPLATE_ID_PALMETTO` (ID `3`). It **never** silently falls back to OSI when Palmetto's template is absent or invalid (`tests/test_docuseal_surety_template_guards.py::test_palmetto_never_falls_back_to_osi` ✅).
- **Missing Template Guard:** Missing template configuration returns `None` and raises `DocuSealPacketValidationError` prior to initiating any network transport (`test_create_submission_blocks_empty_template_id` ✅).
- **Match / Case Binding Invariants:** `validate_docuseal_packet_binding()` fails closed if `surety_id` is missing, not in `('osi', 'palmetto')`, or if `case_number`, `poa_number`, `booking_number`, or validated party contacts are absent.

### B. Telegram Mini-App Authority & Boundary
- **Role Isolation:** All mini-app surfaces in `shamrock-telegram-app` (`intake/`, `paperwork/`, `documents/`, `defendant/`) act strictly as **intake collectors and launchpads**.
- **Zero Direct Submissions:** Telegram never creates DocuSeal submissions, mints signing sessions, or interacts directly with the DocuSeal API.
- **Direct Route Deprecation:** `POST /api/send-paperwork` returns HTTP `409` (`DIRECT_PAPERWORK_RETIRED`), directing all signing workflows to staff-issued packets released inside Super CRM.
- **Embedded Signing:** The `/paperwork/` UI embeds DocuSeal **only** via a staff-issued `/s/{slug}` session URL provided by Super CRM after full validation.

---

## 3. Live API Probe Details & Redacted Findings

### Probe 1: Public Web Server Response
- **Endpoint:** `GET https://sign.shamrockbailbonds.biz/`
- **Method Category:** HTTP/2 TLS Probe
- **Response:** `HTTP/2 200 OK` (`server: nginx/1.24.0 (Ubuntu)`, `set-cookie: _docu_seal_session=...`)
- **Result:** Host is online, active, and properly reverse-proxied with SSL.

### Probe 2: Authenticated Template Directory
- **Endpoint:** `GET https://sign.shamrockbailbonds.biz/api/templates?limit=100`
- **Method Category:** REST API Probe with `X-Auth-Token: <DOCUSEAL_API_KEY>` & `Accept: application/json`
- **Response:** `HTTP/2 200 OK` (4 templates returned)
- **Verified Templates:**
  1. **ID `1`**: `shamrock-osi-paperwork-complete` (Slug: `JkHoQrfKUJJM4t`, 13 documents, roles: `bondsman`, `indemnitor`, `defendant`, `coindemnitor`)
  2. **ID `3`**: `shamrock-palmetto-paperwork-complete` (Slug: `Cy7nqpbT2koQmy`, 11 documents, roles: `indemnitor`, `defendant`, `bondsman`, `coindemnitor`)
  3. **ID `4`**: `shamrock-osi-paperwork-complete (Clone)`
  4. **ID `5`**: `shamrock-palmetto-paperwork-complete BACKUP pre-tag`

---

## 4. Current Status of Checklist Items B5 & E3

- **B5 (`DOCUSEAL_*` valid / templates resolve):** 🟢 **Verified Live** (Templates 1 & 3 confirmed active and matching Super CRM configuration).
- **E3 (DocuSeal packet generated & staff-approved):** 🔲 **Remains OPEN** pending staff selection of an authoritative case and authorization of a single live packet release.
