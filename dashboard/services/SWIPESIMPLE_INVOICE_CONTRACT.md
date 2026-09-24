# SwipeSimple Invoice — Locked HTTP Contract (Option 2)

> **Status:** LOCKED from Brendan DevTools create capture (2026-09-24)  
> **Primary create path:** Rails form `POST /invoices` + `copy_link` in `swipesimple_invoice_service.py`  
> **Playwright:** session / CSRF refresh **ONLY** (`swipesimple_playwright_bootstrap.py`) — never primary create  
> **Live gate:** `SWIPESIMPLE_LIVE=1` required. Default **OFF** — accidental import must never hit production.

---

## Production design

1. **KEEP SwipeSimple** — primary = HTTP create (form-urlencoded) + `copy_link`.
2. **Playwright** = session cookie / CSRF refresh fallback only (never create invoices).
3. **Fail-closed premium:** BondCase premium (dollars) must convert to **exact integer cents**; mismatch → error, never invent amounts.
4. **Invoice # = booking #** (`booking_number` / `reference_id`).
5. **One invoice per bond** — idempotency key = `bond_id` / `bond_case_id`; if payment link already stored, return existing.
6. **Prefer web payment link**; dispatch via BlueBubbles / email. No dual SwipeSimple SMS.
7. **Never log/echo secrets** (session cookies, CSRF / authenticity_token, passwords).
8. **Do not call SwipeSimple live** unless `SWIPESIMPLE_LIVE=1` **and** Brendan has given go-ahead.

---

## CREATE invoice (locked)

| Field | Value |
|-------|-------|
| **URL** | `https://swipesimple.com/invoices` |
| **Method** | `POST` |
| **Content-Type** | `application/x-www-form-urlencoded` (Rails form — **NOT** JSON `/api/v4`) |
| **Auth** | Session `Cookie` + form `authenticity_token` (CSRF). **No** `Authorization` header. |
| **Response** | `302 Found` → `/invoices` (no JSON body) |

### Request header names (values in env only)

`Accept`, `Accept-Encoding`, `Accept-Language`, `Content-Type`, `Cookie`, `Origin`, `Referer`, `User-Agent`  
(plus browser Sec-* on captures — not required for server replay)

### Form fields

| Form key | Source / notes |
|----------|----------------|
| `authenticity_token` | CSRF from new-invoice page (or cached `SWIPESIMPLE_CSRF_TOKEN`) |
| `invoice[merchant_account_id]` | `acc_bd9fed047bd6f7c6` (default; overridable via env) |
| `invoice[customer][id]` | Existing SwipeSimple customer id when known; **empty OK** for new customer (name/email/phone still sent) |
| `invoice[customer][name]` | Defendant / indemnitor display name from BondCase |
| `customer-proxy` | Same as customer id when known |
| `invoice[email]` | Top-level email (often empty when nested set) |
| `invoice[customer][email]` | Customer email |
| `invoice[phone]` | Top-level phone |
| `invoice[customer][phone]` | Customer phone |
| `invoice[reference_id]` | **Booking #** (= invoice #) |
| `invoice[items][][id]` | Catalog: `im_bae23df0a0cb4e01a688bdd6bf1` |
| `invoice[items][][name]` | `Bail Bond Premium` |
| `invoice[items][][quantity]` | `1` |
| `invoice[items][][price]` | Premium in **cents** (integer) |
| `invoice[discounts][]` | Empty |
| `invoice[prompt_for_tip]` | `0` |
| `invoice[amount]` | Same cents total |
| `invoice[unadjusted_amount]` | Same cents total |
| `invoice[save_as_draft]` | `true` |

### Amounts: cents

SwipeSimple form amounts are **cents**: `100` = `$1.00`.  
BondCase stores premium in **dollars**. Conversion must yield an **exact integer** cent value; otherwise fail-closed (`premium_not_exact_cents`).

### Locked merchant / catalog IDs

| Constant | Value |
|----------|-------|
| Merchant account | `acc_bd9fed047bd6f7c6` |
| Catalog item id | `im_bae23df0a0cb4e01a688bdd6bf1` |
| Catalog item name | `Bail Bond Premium` |

---

## CSRF flow

1. Prefer env `SWIPESIMPLE_CSRF_TOKEN` when freshly set by bootstrap.
2. Else **GET** new-invoice form page (Rails default `GET {base}/invoices/new`) with session `Cookie`.
3. Parse `authenticity_token` from HTML (`input[name=authenticity_token]` or meta `csrf-token`).
4. Include token in create form body. Never log the token value.

Path is configurable via env `SWIPESIMPLE_NEW_INVOICE_PATH` (default `/invoices/new`).
HTML parse covers `input[name=authenticity_token]` and meta `csrf-token`.

---

## copy_link (locked — after create)

| Field | Value |
|-------|-------|
| **URL** | `https://swipesimple.com/api/v4/invoices/{invoice_id}/copy_link` |
| **Method** | `POST` |
| **Body** | Empty |
| **Auth** | Cookies: `cognito_access_token` + `_swipesimple_session` (via session cookie jar) |

Returns the web payment link we persist and dispatch (BlueBubbles / email).

### invoice_id after create 302

Create responds `302 Found` → `/invoices` with **no JSON body** and typically **no invoice id in Location**.  
Service implements best-effort resolution (Location parse → list/search by `reference_id` = booking #).  
Resolution order: Location → create body → **prefer** `GET /api/v4/invoices?reference_id=<booking#>` (JSON walk) → q/search → HTML `/invoices` list.
**Fail-closed:** if unresolved, raise and set `swipesimple_invoice_unresolved=true` on the bond — **do not create a duplicate draft**. Ops/API must resolve vendor id before `copy_link` / retry create.

---

## Env / secret store (values NEVER logged)

| Env var | Role |
|---------|------|
| `SWIPESIMPLE_LIVE` | **`1` / `true` to enable live HTTP.** Default unset/off — no network to SwipeSimple. |
| `SWIPESIMPLE_SESSION` **or** `SWIPESIMPLE_COOKIE_JAR` | Session Cookie header / jar for HTTP |
| `SWIPESIMPLE_CSRF_TOKEN` | Optional cached authenticity_token |
| `SWIPESIMPLE_MERCHANT_ID` | Optional; default `acc_bd9fed047bd6f7c6` |
| `SWIPESIMPLE_CATALOG_ITEM_ID` | Optional; default Bail Bond Premium id above |
| `SWIPESIMPLE_CATALOG_ITEM_NAME` | Optional; default `Bail Bond Premium` |
| `SWIPESIMPLE_BASE_URL` | Optional; default `https://swipesimple.com` |
| `SWIPESIMPLE_NEW_INVOICE_PATH` | Optional; default `/invoices/new` |
| `SWIPESIMPLE_DISPATCH_LIVE` | **`1` / `true` to send BB/email.** Default off — dry-run only. |
| `SWIPESIMPLE_SHARE_INVOICE_ON_PROMOTE` | Opt-in intake promote → Share Invoice (default off) |
| `SWIPESIMPLE_USERNAME` / `SWIPESIMPLE_PASSWORD` | Playwright bootstrap login only |

---

## Persisted bond fields (after successful create + copy_link)

Written to `bond_cases` and `active_bonds`:

| Field | Meaning |
|-------|---------|
| `swipesimple_invoice_id` | Vendor invoice id (required for copy_link) |
| `swipesimple_invoice_number` | Must equal `booking_number` |
| `swipesimple_payment_link` | Web URL we dispatch |
| `swipesimple_invoice_created_at` | ISO timestamp |
| `swipesimple_invoice_bond_id` | Idempotency key at create time |

Related on reconcile: `payment_status`, `premium_paid`, `last_payment_*`, ledger via `LedgerService`.

---

## Module map

| Module | Role |
|--------|------|
| `swipesimple_invoice_service.py` | Form create + copy_link; `create_locked_invoice` / `dispatch_invoice` / `reconcile_payment` / `maybe_issue_share_invoice_for_bond` |
| `SWIPESIMPLE_PRODUCTION_CHECKLIST.md` | Human go-live steps (secrets, smoke, merge, deploy) |
| `swipesimple_playwright_bootstrap.py` | Session / CSRF refresh only |
| `scripts/swipesimple_session_refresh.py` | CLI wrapper for bootstrap |
| `packet_payment_link_service.py` | Existing **static** pay-link dispatch (pre–Share Invoice) |
| `swipesimple_receipt_poller.py` / `swipesimple_reconciliation_service.py` | Existing receipt → payment paths |

---

## Fail-closed rules checklist

- [x] Create = form-urlencoded `POST /invoices` (not JSON API create)
- [x] Amounts in cents; BondCase dollars → exact integer cents
- [x] Invoice / reference_id = booking #
- [x] One invoice per bond_id (idempotent)
- [x] Live HTTP gated by `SWIPESIMPLE_LIVE`
- [x] Secrets never logged
- [x] invoice_id resolution: Location → body → `/api/v4/invoices?reference_id=` → list HTML; unresolved marks pending (no duplicate)
- [x] Dispatch dry-run by default (`SWIPESIMPLE_DISPATCH_LIVE`)
- [x] Customer mapping; empty customer id OK for new customers
- [x] Entrypoint `maybe_issue_share_invoice_for_bond` (+ optional promote hook)
- [ ] Brendan go-ahead before setting `SWIPESIMPLE_LIVE=1` in production
- [ ] Brendan go-ahead before setting `SWIPESIMPLE_DISPATCH_LIVE=1`
