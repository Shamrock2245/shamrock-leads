# Staff test-case mode — Write Bond smoke

Off by default. This path lets a staff session run `POST /api/paperwork/packet/finalize` on a fake `TEST-` case. It does not match a real booking, does not draw a power from inventory, and does not email, text, iMessage, Slack, or charge anyone.

## Gates

All three are required. If any one is missing, the request fails closed and does not fall through to a real finalize.

| Gate | Setting |
| --- | --- |
| Environment | `STAFF_TEST_CASE_MODE=1` |
| Request | `test_case: true` |
| Auth | PIN session with role `god_admin`, `admin`, or `staff`; or `X-Admin-Token` / `X-PIN` / Bearer equal to `DASHBOARD_PIN`; or `X-API-Key` / `X-Internal-Token` matching `GAS_API_KEY` or `LEADS_INTERNAL_TOKEN` |

A sub-agent cookie is rejected. Unset or any other value of `STAFF_TEST_CASE_MODE` leaves finalize exactly as it is today for requests that do not set `test_case`.

The booking number and case number must match `TEST-` plus letters, digits, and hyphens (`TEST-SMOKE1`, `TEST-CASE-SMOKE1`). The packet id must match `PKT-TEST-…`. If you omit it, the server generates one. A real booking, or a `TEST-` booking that already exists as an arrest, bond, or defendant without `is_test: true`, is refused. Those collections are only read.

## What the smoke sends

Every DocuSeal submitter email is `admin@shamrockbailbonds.biz`. `STAFF_TEST_CASE_SIGNER_EMAIL` may replace it only when that address is on `STAFF_TEST_CASE_EMAIL_ALLOWLIST` and ends with `@shamrockbailbonds.biz`. Phones are removed. `send_email` and `send_sms` are false. `deliver_initial_docuseal_links` is not called. No SwipeSimple link, payment, or charge is created.

The power defaults to `TEST-POA-0001` and is not loaded from `poa_inventory`. A non-`TEST-` power is rejected unless both `STAFF_TEST_CASE_REAL_POWER=1` and `allow_real_power: true` are set. Even then this mode does not assign or mark a power used. Chief of Staff and Brendan decide later whether a real power may be consumed.

The writing agent is still the normal pair: the house row (Brendan O'Neal / P139768) when the case has no agent, or the license holder when the case has only a license (G356764 is Kayla Lukesic).

## Example

Do not point this at a live booking. DocuSeal must already be configured in the environment you use; this runbook does not call it for you.

```http
POST /api/paperwork/packet/finalize
Content-Type: application/json
```

```json
{
  "test_case": true,
  "surety_id": "osi",
  "booking_number": "TEST-SMOKE1",
  "case_number": "TEST-CASE-SMOKE1",
  "packet_id": "PKT-TEST-SMOKE1",
  "defendant_name": "Sample Party One",
  "indemnitor_name": "Sample Party Two",
  "bond_amount": 5000,
  "county": "Lee",
  "state": "FL",
  "send_email": true
}
```

`send_email: true` is ignored. The saved packet and its `audit_events` row are `is_test: true` with that `PKT-TEST-` id. A later DocuSeal webhook for that packet is stored the same way, and the completion handler skips Drive, payment, court, and Slack.

## Reports

Bordereau reads bonds, inventory, and payments, not these test packets. The daily ledger and CRM overview counts do not query `paperwork_packets` or these audit rows. A default `is_test` exclusion on every dashboard and Sheets reader is a follow-up. Until then, filter `is_test: true` out of any report you build by hand.
