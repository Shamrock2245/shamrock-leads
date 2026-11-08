# Self-serve ID prefill

`PAPERWORK_SELF_SERVE_PREFILL` defaults off. Off, the paperwork portal keeps today's copy and does not create a BondCase from an ID scan.

On, after the signer confirms a server-side scan:

1. `ensure_self_serve_bondcase` looks up a BondCase by normalized phone, name, and date of birth. A booking already on the session is checked against the stored role phone, name, and date of birth before `ensure_match_bondcase`. A mismatch returns `role_phone_mismatch` or `role_conflict` and does not restamp the match, case, or POA. A booking that passes that check is offered to `ensure_match_bondcase`. That function still refuses a missing arrest, POA, premium, or bond amount.
2. A miss inserts one draft BondCase (`source=self_serve`, `status=pending_owner_review`, actor `auto`) plus the defendant or indemnitor row for that role. Bond, premium, and POA are not filled in.
3. `build_self_serve_packet` writes the confirmed fields with `DocuSealService.prefill_values_from_bond`. A draft calls `DocuSealService.create_submission` with `send_email` false and `send_sms` false. A case that already has a validated match, POA, case number, booking, and premium calls `create_submission_for_packet` instead. No pay link is returned.
4. The signer goes from the confirmed fields to the embedded signing URL.

Scans stay insert-only on `id_scan_records` (name and issuing state). The full OCR snapshot and each signer edit are new rows. An edit does not change the original scan. One role's fields are never copied onto another role.

## Fallback hook

Leads Ops' auto-packet builder should replace the DocuSeal call inside `build_self_serve_packet`. Until that builder is the one that creates the packet, a failed create leaves the confirmed fields for this read:

```python
async def get_pending_id_prefill(case_id: str, role: str, person_id: str) -> dict:
    ...
```

`role` is `indemnitor`, `coindemnitor`, or `defendant`. The result is `{}` or:

```python
{
    "case_id": "",
    "role": "",
    "person_id": "",
    "scan_id": "",
    "confirm_id": "",
    "fields": {},
}
```

`fields` uses the same role keys `prefill_values_from_bond` already reads (`indemnitor_name`, `indemnitor_dob`, `indemnitor_address`, `indemnitor_city`, `indemnitor_state`, `indemnitor_zip`, `indemnitor_dl`, `indemnitor_dl_state`, and the defendant and co-indemnitor equivalents, plus sex and `*_dl_expiration`). The key is the BondCase id, the role, and the person id. A POA change does not move the row.

No signing URL is returned when the scan is missing, the session is unverified or voided, the signer or packet is voided, or the DocuSeal call fails.

A draft packet is still created and the signer still goes into signing. The packet is marked `pending_owner_review` and `self_serve_unvalidated_draft`, and an audit row records that a signing URL was issued before staff review. An open packet is reused only when `self_serve_packet_key` matches, or when the person id and any phone or email already stored on that packet match. A signed, completed, filed, or archived packet is not edited; a new packet is inserted and the replacement is audited. A reused BondCase gets its own identity audit and the earlier audit rows stay. The signer is matched only in the requested role. Defendant and co-indemnitor contact is not written into the primary indemnitor slot of `ensure_match_bondcase`.

A new draft uses a deterministic ``_id`` (a uuid5 of phone, name, date of birth, and role). The packet for that signer uses a deterministic ``_id`` too. Both workers insert that primary key. MongoDB's existing unique ``_id`` index rejects the second insert with duplicate key 11000, and that worker continues the stored row. No ``create_index`` and no migration are added. A legacy case is reused only when the role-slot lookup sees the same phone, name, and date of birth. Two visible matches fail closed. An existing row is not deleted. A claim that already started a DocuSeal submission and has no stored signing URL is not submitted again.

If the packet shell is inserted and a later local step fails before DocuSeal is called, that shell is marked `compensated` and a saga-owned draft case is marked `compensated`. The rows, scans, and audits stay. A preexisting case is not changed. The first create response uses the same signing-URL rule: HTTPS, the configured DocuSeal origin, and path `/s/{slug}`. A hostile `embed_src` is not stored or returned, and that packet stays `recovery_required` instead of being compensated or submitted again. After the provider call has started, a lost response is reconciled with `list_submitters(external_id=...)`. The page counts as complete only when `pagination.count` equals the returned rows and `pagination.next` is null. The one row must match the role, email, phone, and external id stored before the call, use DocuSeal status `awaiting`, `sent`, or `opened`, and use this server's `https` `/s/{slug}` URL. Any other result is `recovery_required`: no second submission, no archive, and no refund. A packet that is signed or completed during the lookup is not rewritten. Confirmed fields stay.

A new draft gets OSI only when this request is creating the case and the portal session already has a server-recorded walk-in channel (`inperson`, `ipad`, or `kiosk`). That channel is the signed launch proof stored at PIN send, or the mode on a handoff session. A raw query value, including `1`, `true`, or `inperson=1`, is not a channel. An existing case is not given a surety here, including one that has none. Palmetto and OSI already on a case stay. An explicit unknown or inactive carrier is left as it is. Any other new draft still has no surety, and no DocuSeal submission is created. The bond packet templates are carrier-specific. Confirmed fields stay when a packet is not opened. A case is fully bound for `create_submission_for_packet` only when the bond amount and the premium are each already a positive amount. A stored phone on the requested role that differs from the verified phone is not overwritten. A newer successful scan is appended and read from `id_scan_records`. The previous `id_scan` is not deleted.
