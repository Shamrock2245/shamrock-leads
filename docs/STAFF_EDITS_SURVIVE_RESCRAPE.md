# Staff edits survive rescrapes (all counties)

**Rule:** a bond or charge row that staff set is never replaced by a scraped value. Source values that disagree are kept next to it in `scraped_*` fields so staff can see drift.

## Where it lives

`core/staff_edits.py` is the only place that decides which fields belong to staff and how a scraped `$set` is merged around them.

| Piece | What it does |
|---|---|
| `staff_bond_marker()` | `$set` fields for `staff_edits.bond = {amount, type, at, by, source}`. A staff `$0` is stored as `0.0`, a known $0. |
| `staff_charges_marker()` | `$set` fields for `staff_edits.charges = {removed, baseline, at, by, source}`. `removed` lists the charges staff deleted. `baseline` is the scraped charge list staff saw on their first edit. |
| `staff_provenance(doc)` | Reads the markers. For older records it also reads the legacy flags (see below). |
| `protect_scraped_update(set_doc, existing)` | Rewrites a scraped `$set` so it cannot overwrite staff values. |

### What a protected rescrape writes

- **Staff bond:** `bond_amount`, `bond_amount_raw`, `total_bond_amount` and `bond_type` are dropped from the `$set`. The source values go to `scraped_bond_amount`, `scraped_bond_amount_raw`, `scraped_bond_type` and `scraped_bond_at`. If the update re-scores the lead, the score is recomputed with the staff bond, and the scraped score goes to `scraped_lead_score`.
- **Staff charges:** the staff rows are kept exactly as saved, with their per-charge bond, case number and POA. A scraped charge is added only if staff do not already have it, did not remove it, and it was not on the baseline. Added rows are tagged `"source": "scraped"`, and staff-saved rows are tagged `"source": "staff"`. `charges` is rebuilt from the merged rows. The raw source values go to `scraped_charges`, `scraped_charge_details` and `scraped_charges_at`. Scraper placeholders such as `Unknown` are never added as a charge.
- **No staff edits:** the `$set` passes through unchanged, so the record updates normally.

## Write paths covered

| Path | How |
|---|---|
| `writers/mongo_writer.MongoWriter.write_records` | Covers every county run through `BaseScraper.run`, including the Southern Software (SSW) and SmartWEB helpers, which return `ArrestRecord`s and never write to Mongo directly. It does one provenance read per state and county (`fetch_provenance_docs`), then protects each `UpdateOne`. If that read fails, the write fails too: it never writes blind. |
| `core/first_appearance_watcher.py` | A staff bond (including a staff `$0`) is kept, and the source bond goes to `scraped_*`. No bond-set alert is sent for that record. |
| `core/scheduler.py` custody recheck | The live-roster `update_fields` are protected. This is a write-path fix only; no scheduling change. |
| `dashboard/services/lee_clerk_watch._refresh_jail_source` | Protected. |
| `POST /api/leads/refresh-from-source` | Protected. The response carries `staff_bond_kept` and `staff_charges_kept`. |
| `dashboard/services/booking_extract_merge` (bookmarklet) | Protected, because the booking page is source data. |
| `dashboard/services/confirmed_booking_intake` refresh | Protected. |

## Staff edit paths (these record provenance)

- `update-bond-amount` writes the bond marker.
- `update-charge-bonds` writes the charges marker and tags every row `source: staff`. A blank per-charge amount stays `None` (unknown), and a typed `0` is a known $0. If every row's amount is blank, the bond is left as it was: no $0 bond is written and no bond marker is recorded. When the Write Bond modal omits POAs, the saved POA for that charge is kept.
- `update-lead-details` writes the bond marker for a bond. For charge text it writes staff rows, keeping each row's bond, case number and POA when the description is unchanged, plus the charges marker.
- `admin_hygiene.patch_arrest` writes the same markers.

The Write Bond modal (`sl-features.js`) now shows a blank field for an unknown amount instead of `0`, and sends `null` for a blank field.

## Existing records (no backfill)

No migration and no prod rewrite. Provenance is inferred from the fields older records already have:

- `bond_override: true`, or `last_checked_mode == "MANUAL_CHARGE_BONDS"`, together with a **positive** `bond_amount`, counts as a staff bond. A legacy zero is ambiguous, because earlier rescrapes reset staff amounts to `0.0` and left the flag. It is not protected and hydrates as unknown.
- `last_checked_mode == "MANUAL_CHARGE_BONDS"` means the top-level `charge_details` are staff rows. The current rows become the baseline.

The first protected rescrape of such a record writes the matching `staff_edits.*` marker with `inferred_from`. Provenance then stays durable after `last_checked_mode` changes. Records are only touched by writes that were already happening.

## Hydrate

`packet_builder_service.arrest_bond_value` reads `staff_edits.bond` first, so a staff `$0` hydrates as a known `$0`. Top-level `charge_details` still beats the scraped `extra.charge_details`, as before.

## Tests

`tests/test_staff_edits_survive_rescrape.py` covers:

- a staff bond override, a staff $0, and a legacy override, each followed by a rescrape;
- per-charge manual bonds followed by a rescrape;
- a staff-edited row, a staff-added charge and a staff-removed charge, each followed by changed scraped charges;
- a new scraped charge being added;
- a record with no staff edits updating normally;
- the state/county scope;
- an SSW county (Henderson NC) and a SmartWEB county (Bradford FL) through MongoWriter;
- the First Appearance watcher, the custody recheck, the Lee jail refresh, the bookmarklet merge, the three edit endpoints, and hydrate.

Health is unchanged. It stays `unverified` until a Leads Ops prod write smoke.
