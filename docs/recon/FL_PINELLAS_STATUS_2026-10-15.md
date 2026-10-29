# Pinellas FL: status source (2026-10-15, code-only)

There was no live probe. Pinellas is relay-only (owner exception, Brendan 2026-10-08), and a box read gets only the Blazor JS shell (`docs/recon/FL_PINELLAS_RELAY_ONLY_2026-10-08.md`). This change is based on the code and the existing fixtures.

## What the code reads
- The row extractor (`scrapers/counties/pinellas.py`, roster JS) splits the row's `.td-booking` cell into lines: line 1 is the booking date/time, and line 2 is the published custody line (`custody`).
- The existing fixture (`tests/test_fl_bond_charges_hydrate.py`) uses "In Custody" for line 2. That fixture is synthetic; the box has no capture of the live values.

## Roster scope: not proven
- The run drives Who's In Jail's **booking-date search**. It lists bookings by date, and the old code already handled a "released" value, so the scope can include released bookings. "Who's In Jail" is the site's name, not a current-only label on the result view.
- So a blank custody line can't be read as "In Custody".

## Rule (this PR)
- Line 2 published:
  - containing "released" gives "Released";
  - "in custody" or "in jail" gives "In Custody";
  - any other text is kept verbatim.
  - Each case records `status_source = "published_booking_cell_custody"`.
- Line 2 blank gives Status "" with no status_source. It was "In Custody".
- Relay-only setup, browser and modal flow, bond, charges, case number and booking key are unchanged.

## Follow-up
The next Leads Ops relay write smoke should note the distinct line-2 values. That would confirm the mapping, or let a source-published label be named exactly.
