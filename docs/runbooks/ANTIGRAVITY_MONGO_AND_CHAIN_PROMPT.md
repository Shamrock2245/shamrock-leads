# Antigravity prompt — Paperwork Desk Mongo write + Write Bond chain gap

Copy everything below the line into Antigravity.

---

## Goal
Make Shamrock paperwork production-ready so Paperwork Desk (and other Cursor agents) never need an emergency direct-Mongo backfill for a live bond again.

There are **two related gaps**:

### Gap A — Agents cannot write Mongo from the box
- Paperwork Desk / cloud agents only have `SHAMROCK_MONGO_RO_URI` (Atlas user is insert-denied).
- Live staff writes today use the Mac checkout `shamrock-leads/.env` → `MONGODB_URI` (user `shamrock_leads`, db `ShamrockBailDB`).
- Today’s Schmidt Match/BondCase backfill had to run on Brendan’s Mac because the box cannot insert.

### Gap B — Product/API: Write Bond does not create the validated chain
- Canonical chain: ArrestLead → Defendant → Indemnitor → **validated Match** → **BondCase** → Packet.
- Production code has **no insert path** into `matches` or `bond_cases` collections. Routers only find/update them.
- `POST /api/bonds/match` and intake promote only write `active_bonds`.
- Write Bond + `write_bond_forward` **require** an existing validated Match + BondCase (fail closed) — they do not create them.
- Shannon / office DocuSeal path (`skip_bond_binding`) creates packets with `pending_staff_match: true` as a workaround.
- Fischer (1031222) and Schmidt (1033474) both needed a **staff_direct Mongo backfill** to create defendant + indemnitor + validated match + bond_case and clear `pending_staff_match`.

Schmidt backfill already done (do not redo):
- booking `1033474`, packet `PKT-SCHMIDT-1033474`
- defendant `e43526a0-5d36-4805-a245-5a1fd40b705b`
- indemnitor `df2c221f-9afd-4113-9586-a96b28ddfffc` (Ruby Schmidt → admin@shamrockbailbonds.biz)
- match `7278a61b-8ba4-41eb-a8f1-ea46cf2cdb93` status `validated`
- bond_case `31bccd64-1e85-4842-ad12-0660b7c6740a`
- `pending_staff_match: false`, primary POA `OSI-P6-116-26-0015`, case `26CF017605`, $12,000 / premium $1,200

## What to build (in order)

### 1) Agent Mongo write secret (ops, same day)
1. Prefer a **least-privilege Atlas user** (not the full app `shamrock_leads` if avoidable): readWrite on `ShamrockBailDB` only for collections Paperwork needs for backfill/repair:
   - `defendants`, `indemnitors`, `matches`, `bond_cases`, `active_bonds`, `paperwork_packets`, `poa_inventory`, `audit_events`
   - No dropDatabase / userAdmin.
2. Inject into Cursor / Grok Bot cloud agent secrets as:
   - `SHAMROCK_MONGO_WRITE_URI` (connection string)
   - Keep existing `SHAMROCK_MONGO_RO_URI` for read audits.
3. Document in `AGENTS.md` / Paperwork runbook: agents use WRITE URI only for staff-directed backfill/repair; never for scrapers; never log the URI.
4. Smoke from the box: connect with WRITE URI, insert+delete a probe doc in `audit_events` (or a dedicated `_agent_write_probe`), confirm RO still cannot insert.

### 2) Staff-gated API so the next live bond does not need Mongo scripts (product, primary fix)
Add a God-Admin / staff-only endpoint, e.g.:

`POST /api/staff/chain/ensure-match-bondcase`

Body (from existing CRM facts only — fail closed, invent nothing):
- `booking_number` (required)
- `surety_id` (`osi` | `palmetto`)
- `poa_number` / `poa_numbers` (must already be assigned in `poa_inventory` for that booking/case)
- `case_number`
- `indemnitor` fields only if already on `active_bonds` / intake (no fabricated phone/email)
- `packet_id` optional — if present, link and set `pending_staff_match: false` only after chain validates

Behavior (idempotent):
1. Load arrest + active_bonds for booking; 404/409 if missing.
2. Ensure `defendants` row (normalize from arrest or create Fischer-shaped staff_direct doc).
3. Ensure `indemnitors` row from active_bonds indemnitor (require name + email already on file).
4. Upsert `matches` with `status/Status: validated`, confidence 100, staff actor email, reason noting staff-directed ensure.
5. Upsert `bond_cases` with bond/premium/charges/POAs/court from arrest + active_bonds + charge_details (do not invent amounts).
6. Patch `active_bonds` with `bond_case_id`, `match_id`, `defendant_id`, `indemnitor_id`, `poa_number`, `insurance_company`/`surety_id`.
7. If `packet_id` given, patch packet identity fields + `pending_staff_match: false`.
8. Write `audit_events` (`staff_ensure_match_bondcase`).
9. Return all IDs.

Wire Write Bond / packet finalize so the happy path is:
**Write Bond → ensure-match-bondcase (or create chain inline) → finalize DocuSeal**  
Shannon office path may remain for true emergencies but should not be the default for in-office bonds.

Tests:
- Unit/integration: creates chain once; second call is no-op/idempotent; refuses missing premium/bond/POA; refuses inventing indemnitor contact; God-Admin auth required.

### 3) Nice-to-fix (not blockers for Mongo, but paperwork polish)
- `GET /api/appearance-bond-pdf` latin-1 crash on em dash (`—`) in charge text; `print-package` works — encode UTF-8 or sanitize.
- Old Shannon `pending_staff_match: true` packets from Aug 2026 (Lamb/Coulter/Ortiz) with no booking — hygiene archive/void, not live Schmidt.

## Constraints
- Fail closed on identity; never invent bond amounts, POAs, premiums, or customer phones/emails.
- No customer SMS/email from this work (`send_email: false` / admin@ only).
- Do not touch `shamrock-trading-bot`.
- PRs need green CI; do not merge without Brendan/CoS OK.
- Repo: `shamrock-leads` (Desktop path `~/Desktop/shamrock-active-software/shamrock-leads`).

## Done when
1. Box agent can `insert` with `SHAMROCK_MONGO_WRITE_URI` and RO still cannot.
2. Staff-gated ensure-match-bondcase API exists with tests + short runbook.
3. Write Bond docs/example (`scripts/examples/write_bond_super_crm.js`) updated so the 4-step flow includes chain ensure before finalize (no Shannon skip required for normal office bonds).
4. STATUS.md notes Gap A/B closed.

## Out of scope
- Re-running Schmidt backfill.
- Changing OSI template 1 / Palmetto template 5 IDs.
