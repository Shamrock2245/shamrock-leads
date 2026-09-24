# SwipeSimple Share Invoice — Source Contract (Option 2)

> **Status:** AWAITING Brendan DevTools cURL capture (2026-09-24, Invoicing Desk / Brendan O'Neal Cos)  
> **Primary create path:** captured Share Invoice **HTTP replay** in `swipesimple_invoice_service.py`  
> **Playwright:** session refresh / bootstrap **ONLY** (`swipesimple_playwright_bootstrap.py`) — never primary create

---

## Fill from DevTools cURL (do not commit secrets)

| Field | Value (fill) | Notes |
|-------|----------------|-------|
| **URL** | _TBD_ | Full Share Invoice / Web Link endpoint |
| **Method** | _TBD_ | Likely `POST` |
| **Headers (names only)** | _TBD_ | e.g. `Cookie`, `X-CSRF-Token`, `Content-Type`, `Accept`, `Origin`, `Referer` — **values stay in env** |
| **JSON / form body keys** | _TBD_ | List key names only (amount, invoice #, customer, etc.) |

### Env / secret store shape (values never logged)

| Env var | Role |
|---------|------|
| `SWIPESIMPLE_SESSION` **or** `SWIPESIMPLE_COOKIE_JAR` | Session material for HTTP replay |
| `SWIPESIMPLE_CSRF_TOKEN` | Optional CSRF |
| `SWIPESIMPLE_MERCHANT_ID` | Optional merchant scope |
| `SWIPESIMPLE_BASE_URL` | Optional; default `https://app.swipesimple.com` |
| `SWIPESIMPLE_USERNAME` / `SWIPESIMPLE_PASSWORD` | Bootstrap login only (Playwright refresh) |

---

## Hard rules summary

1. **KEEP SwipeSimple** — production = Option 2 HTTP replay of Share Invoice.
2. **Fail-closed:** premium must match BondCase exactly (`premium_amount` / `total_premium` / `premium`); never invent premiums or links.
3. **Invoice # = booking #** (`booking_number` on BondCase / `active_bonds`).
4. **One invoice per bond** — idempotency key = `bond_id` / `bond_case_id`; if link already stored, return existing.
5. **Prefer web payment link**; dispatch via BlueBubbles / email (no dual SwipeSimple SMS unless asked later).
6. **Fully automatic day-to-day:** bond → invoice → payment link → BlueBubbles/email → reconcile PAID (no HITL).
7. **Never log/echo secrets** (session, cookies, CSRF, passwords).
8. **Do not call SwipeSimple live** until this contract is filled and HTTP is wired.

---

## Persisted bond fields (after successful create)

Written to `bond_cases` and `active_bonds`:

| Field | Meaning |
|-------|---------|
| `swipesimple_invoice_id` | Vendor invoice id (if returned) |
| `swipesimple_invoice_number` | Must equal `booking_number` |
| `swipesimple_payment_link` | Web URL we dispatch |
| `swipesimple_invoice_created_at` | ISO timestamp |
| `swipesimple_invoice_bond_id` | Idempotency key at create time |

Related existing fields reused on reconcile: `payment_status`, `premium_paid`, `last_payment_*`, ledger via `LedgerService`.

---

## Module map

| Module | Role |
|--------|------|
| `swipesimple_invoice_service.py` | `create_locked_invoice` / `dispatch_invoice` / `reconcile_payment` |
| `swipesimple_playwright_bootstrap.py` | Session refresh / capture only |
| `scripts/swipesimple_session_refresh.py` | CLI wrapper for bootstrap |
| `packet_payment_link_service.py` | Existing **static** pay-link dispatch (pre–Share Invoice) |
| `swipesimple_receipt_poller.py` / `swipesimple_reconciliation_service.py` | Existing receipt → payment paths |

---

## Wiring checklist (post-capture)

- [ ] Paste URL / method / header **names** / body **keys** above
- [ ] Implement `_share_invoice_http` in `swipesimple_invoice_service.py`
- [ ] Confirm invoice number field maps to `booking_number`
- [ ] Confirm amount field is BondCase premium (fail-closed mismatch)
- [ ] Persist `swipesimple_payment_link` then enable `dispatch_invoice` sends
- [ ] Smoke reconcile against Gmail poller / webhook without double-PAID
