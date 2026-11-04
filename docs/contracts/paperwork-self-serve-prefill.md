# Self-serve ID prefill

`PAPERWORK_SELF_SERVE_PREFILL` defaults off. Off, the paperwork portal keeps today's copy and does not create a BondCase from an ID scan.

On, after the signer confirms a server-side scan:

1. `ensure_self_serve_bondcase` looks up a BondCase by normalized phone, name, and date of birth. A booking already on the session is offered to `ensure_match_bondcase` first. That function still refuses a missing arrest, POA, premium, or bond amount.
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
