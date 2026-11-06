# Paperwork handoff token

The Wix Indemnitor Portal opens the paperwork app at
`https://paperwork.shamrockbailbonds.biz/?role=&case=&mode=&ht=<token>`.
The portal does not send its Wix session token.

`ht` is a short-lived, single-use token minted by the leads CRM. A valid
token lands that indemnitor, co-indemnitor, or defendant on their own case
and role without a second PIN. Anything else leaves the normal PIN form in
place and grants nothing from the token.

This paperwork app lives in this repo (`dashboard/routers/pin_portal.py`,
host `paperwork.shamrockbailbonds.biz`).

## Who mints

Minting is a separate change. This repo's paperwork side does **not**
register `POST /api/portal/handoff-token`.

When that route exists it is staff-gated or portal-auth-gated. It is never
a public unauthenticated mint.

| | |
| --- | --- |
| Method and path | `POST /api/portal/handoff-token` |
| Body | `{ "case_id", "role", "person_id" }` |
| Response | `{ "token", "expires_at" }` |

`case_id` is the bond case id (`bond_case_id` / `Bond_Case_ID`). It is not a
POA number. `role` is `indemnitor`, `coindemnitor`, or `defendant`.
`person_id` is that signer's id. `expires_at` is the ISO-8601 UTC time of
the `exp` claim.

The minter copies those three fields into the signed claims and sets `mode`
from the portal action that opened paperwork (`id-upload`, `add-cosigner`,
`inperson`, `ipad`, `kiosk`, or empty). Mode is not an unsigned query
parameter the paperwork app will trust. The wire helper the minter should
call is `dashboard.services.paperwork_handoff.sign_handoff_token` (or
`encode_handoff_token` for an already-built claim set).

The minter must not insert the `jti` row. Consumption does that.

## Claims

Signed JSON object, UTF-8, compact separators, keys sorted:

| Claim | Meaning |
| --- | --- |
| `aud` | Always `paperwork` |
| `case_id` | Bond case id. Not a POA number |
| `role` | `indemnitor`, `coindemnitor`, or `defendant` |
| `person_id` | That signer's id on the case |
| `mode` | `id-upload`, `add-cosigner`, `inperson`, `ipad`, `kiosk`, or `""` |
| `iat` | Issued-at, unix seconds |
| `exp` | Expiry, unix seconds. `exp - iat` is at most 900 |
| `jti` | Single-use nonce |

`mode=add-cosigner` is valid only with `role=coindemnitor`.

## Signing

`v1.<base64url(json-bytes)>.<base64url(HMAC-SHA256(secret, json-bytes))>`

Base64url is unpadded. The MAC covers the exact JSON bytes inside the token.

| Env | Default | Purpose |
| --- | --- | --- |
| `PAPERWORK_HANDOFF_ENABLED` | off | `1` / `true` / `yes` / `on` enables consume. Anything else, including unset, is off |
| `PAPERWORK_HANDOFF_SECRET` | unset | HMAC key. When it is unset, consume fails closed even if the flag is on |

Neither variable is written into compose, deploy workflows, or `.env` files
by this change. Set them on the leads process when the path is turned on.
The flag stays off until then, so a merge does not change the PIN portal.

TTL is 15 minutes (`900` seconds), plus 30 seconds of clock skew on `iat`.
A token whose `exp` is already past, or whose lifetime is longer than 15
minutes, is rejected.

## Single use

Collection: `paperwork_handoff_jti` (tenant-owned).

After the signature, the window, the hints, and the signer all check out,
consume runs one atomic `findOneAndUpdate` on `{_id: jti}` with `upsert`
and `$setOnInsert`, returning the previous document. No previous document
means this caller inserted the row and may open a session. A previous
document means the token was already used.

Rejected tokens are not consumed, so a mismatched hint does not burn a
still-valid token. A successful consume cannot be replayed.

## What the paperwork app does

`POST /api/portal/handoff/consume` with `{ "ht", "role", "case", "mode" }`.

The page script sends that body when `ht` is present. The server checks the
flag, verifies the token, and consumes it. OCR does not run on this request.

Success:

```json
{
  "ok": true,
  "session_token": "PORTAL-HT-…",
  "role": "coindemnitor",
  "case_id": "TEST-CASE",
  "mode": "add-cosigner",
  "route": "add-cosigner",
  "packet_id": "PKT-TEST",
  "signing_link": "",
  "id_upload_delegated": false
}
```

The session is the portal session (`portal_pins`, `auth_method: handoff`,
verified, about two hours). It is not stored in `localStorage`. A refresh
has to use the PIN, because the token was already consumed.

Failure, including flag off, is always:

```json
{ "ok": false, "fallback": "pin" }
```

No session, no signing URL, no pay link, no case body, no reason code.
The client shows the existing PIN form.

## Query string

`?role=&case=&mode=&ht=<token>`

| Param | Trust |
| --- | --- |
| `ht` | The credential, when the flag is on |
| `role`, `case` | Untrusted hints. Compared with the claims after normalization (`co-indemnitor` and `coindemnitor` agree). A non-empty hint that disagrees rejects the token and falls back to PIN. An empty hint is not a disagreement |
| `mode` | Untrusted. It does not override the signed mode, and a different query mode does not by itself reject the token |

`case_id` is accepted as an alias of the `case` hint only.

Without a valid `ht`, `role`, `case`, and `mode` do not create a session
and do not open a case. After a normal PIN verification they may only
pre-select the in-person, scan, or co-indemnitor screen. The PIN session's
own role stays the role.

`mode` values:

| Mode | After a valid token |
| --- | --- |
| `add-cosigner` | The existing co-indemnitor ID step (role-scoped fields, that role's DocuSeal submitter only). It does not create or revoke a co-signer link, void a packet, or change a POA number |
| `id-upload` | If `window.SLPaperworkIdUpload` or `dashboard.services.paperwork_id_upload.handle_id_upload_mode` is present, the claim is passed there. Otherwise the normal ID step. This repo does not implement that screen |
| `inperson`, `ipad`, `kiosk` | The existing in-person / kiosk presentation |
| empty | The role's case. A signing URL is returned only when the gates below pass |

A `?link=` signing URL is not opened while `ht` is present. If the token
fails, that link is not a fallback.

## Fail closed

Checked before any OCR and before the `jti` is consumed:

- flag off, or secret missing
- malformed token, bad signature, wrong audience
- expired, or lifetime over 15 minutes
- `role` / `case` hint disagrees with the claims
- `add-cosigner` with a role other than `coindemnitor`
- case missing, two bonds in one collection, or the person id is not exactly that role's id on every bond copy
- signer record missing, `identity_verified: false`, or status `unverified`
- signer or that role's submitter status `voided`, `cancelled`, `canceled`, or `declined`

A reused `jti` is rejected after the atomic consume finds the existing row.
No second session is opened.

The same gates stay on the signing URL:

- a voided packet or a voided / unverified submitter does not get a DocuSeal URL
- no ID scan on that signer means no DocuSeal URL
- no signature means no pay link (the automatic SwipeSimple skip
  `packet_unsigned` is unchanged). This consume response never includes a
  pay link
- DocuSeal submitter updates from this path pass `send_email: false` and
  `send_sms: false`, and only after a scan is on file

A POA number change does not invalidate the token or the packet sign URL.
The token and the lookup use the bond case id.

## ID scans and audit

Signer scans are insert-only. A new scan is `$push`ed onto
`id_scan_history`. An existing `id_scan` is not overwritten. Audit rows are
insert-only (`handoff_consumed`, `handoff_rejected`). Nothing in this path
deletes `audit_events` or scan history.

The audit and the log record the reason, `jti`, role, case id, person id,
and mode. They do not record the token, a name, a phone number, an email
address, or the scan image.

## PIN

Someone with no `ht`, or with a token the server rejected, uses the
existing 6-digit PIN. That path is unchanged.
