# SwipeSimple Share Invoice — Production Checklist

> **Audience:** Brendan + Leads Ops / Paperwork Desk  
> **PR:** feat/swipesimple-invoice-scaffold (do not merge until checklist below is green)  
> **Hard rule:** Do **not** set `SWIPESIMPLE_LIVE=1` or `SWIPESIMPLE_DISPATCH_LIVE=1` until Brendan approves a smoke test.

Related: `SWIPESIMPLE_INVOICE_CONTRACT.md`, `swipesimple_invoice_service.py`,
`swipesimple_playwright_bootstrap.py` (session/CSRF refresh **only**).

---

## Already in code (this PR)

- [x] Locked create: `POST /invoices` form-urlencoded + `copy_link`
- [x] Amounts in cents; BondCase dollars → exact integer cents (fail-closed)
- [x] Invoice # / `reference_id` = booking #
- [x] One invoice per bond_id (idempotent); unresolved-id marker blocks duplicate create
- [x] CSRF path configurable: `SWIPESIMPLE_NEW_INVOICE_PATH` (default `/invoices/new`)
- [x] Customer mapping (indemnitor preferred); empty `customer_id` OK for new customers
- [x] `dispatch_invoice` dry-run by default; live send gated by `SWIPESIMPLE_DISPATCH_LIVE`
- [x] Entrypoint: `maybe_issue_share_invoice_for_bond` (+ optional intake promote hook)
- [x] `reconcile_payment` matches booking / reference_id / stored invoice # → PAID + LedgerService
- [x] Offline unit tests (no network)

---

## Remaining human steps (Brendan / Leads Ops)

### 1. Secrets & session (Leads Ops)

- [ ] Store SwipeSimple session cookie jar in secret store as `SWIPESIMPLE_SESSION` **or** `SWIPESIMPLE_COOKIE_JAR` (never commit / never log values)
- [ ] Optional cached CSRF: `SWIPESIMPLE_CSRF_TOKEN` (refresh via Playwright bootstrap)
- [ ] Confirm Playwright bootstrap credentials: `SWIPESIMPLE_USERNAME` / `SWIPESIMPLE_PASSWORD` (refresh **only** — never create invoices)
- [ ] Confirm merchant / catalog defaults still match production:
  - Merchant `acc_bd9fed047bd6f7c6`
  - Catalog item `im_bae23df0a0cb4e01a688bdd6bf1` — Bail Bond Premium
- [ ] If new-invoice HTML path differs in prod, set `SWIPESIMPLE_NEW_INVOICE_PATH`

### 2. BlueBubbles / email (Leads Ops)

- [ ] BlueBubbles URL + password env present for indemnitor iMessage dispatch
- [ ] GmailReaderService OAuth has `gmail.send` for email channel (if used)
- [ ] Keep `SWIPESIMPLE_DISPATCH_LIVE` **unset** until smoke approved

### 3. Brendan smoke ($0.01) — approval required

- [ ] Brendan authorizes a **$0.01** draft smoke on a non-customer test booking #
- [ ] Temporarily set `SWIPESIMPLE_LIVE=1` in a controlled env **only** for that smoke
- [ ] Verify: draft appears, `reference_id` = booking #, `copy_link` returns a real URL
- [ ] Verify: invoice_id resolution via Location and/or `/api/v4/invoices?reference_id=`
- [ ] If id unresolved: confirm fail-closed marker (no duplicate draft) then harden parse
- [ ] **Unset** `SWIPESIMPLE_LIVE` after smoke unless go-live is approved the same day
- [ ] Do **not** enable `SWIPESIMPLE_DISPATCH_LIVE` during first smoke (no customer messages)

### 4. Paperwork Desk / Bond Desk integration

- [ ] Preferred call site after paperwork-complete / bond ready:
  ```python
  from dashboard.services.swipesimple_invoice_service import maybe_issue_share_invoice_for_bond
  await maybe_issue_share_invoice_for_bond(bond_id, channel="imessage", source="paperwork_desk")
  ```
- [ ] Optional intake promote auto-hook: set `SWIPESIMPLE_SHARE_INVOICE_ON_PROMOTE=1`
  (still respects LIVE + DISPATCH gates; soft-fails)
- [ ] Confirm BondCase always has authoritative `premium` + `booking_number` before create

### 5. Merge & deploy

- [ ] Brendan sign-off on smoke + checklist
- [ ] Merge PR `#51` (feat/swipesimple-invoice-scaffold)
- [ ] Deploy dashboard with secrets injected (session jar, optional CSRF)
- [ ] Enable `SWIPESIMPLE_LIVE=1` in production **only** after merge + deploy review
- [ ] Enable `SWIPESIMPLE_DISPATCH_LIVE=1` only when ready to message indemnitors
- [ ] Monitor first live bonds: create → link persist → dry-run/dispatch → reconcile PAID

---

## Fail-closed reminders

| Rule | Behavior |
|------|----------|
| Missing / non-exact premium | Error — never invent amount |
| Missing booking # | Error — never invent invoice # |
| Existing payment link for bond | Return existing (idempotent) |
| Create succeeded, id unresolved | Mark pending — **do not** create another draft |
| `SWIPESIMPLE_LIVE` unset | No HTTP to swipesimple.com |
| `SWIPESIMPLE_DISPATCH_LIVE` unset | Build/log payload only — no BB/email send |

---

## Blocked on humans (not code)

| Owner | Blocker |
|-------|---------|
| Brendan | $0.01 smoke approval + go-live for `SWIPESIMPLE_LIVE` |
| Leads Ops | Cookie jar / CSRF refresh secrets in prod |
| Leads Ops | BlueBubbles (+ optional Gmail) creds for dispatch |
| Paperwork Desk | Call `maybe_issue_share_invoice_for_bond` when bond is ready (or enable promote hook) |
