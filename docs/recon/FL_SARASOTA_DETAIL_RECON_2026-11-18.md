# Sarasota FL detail-page recon (2026-11-18)

**Privacy:** no names, dates of birth, or other personal values were kept. Counts and field shapes only.

**How it was read:** stealth Chrome via Patchright, 6 page loads, read-only. This note does not promote Sarasota. Health stays `fail_closed` and `SOURCE_CONTRACT_VALIDATED` stays false.

## Listing

`https://cms.revize.com/revize/apps/sarasota/`

- A "Current Inmate Population" dropdown, about 1,065 entries.
- Each entry is `viewInmate.php?id=` plus 10 digits. The href has trailing spaces; strip them.
- The label is `LAST,FIRST MIDDLE - MM/DD/YYYY`. That is a name and a date of birth. Do not log or store the date from the label.
- The listing still has no booking number and no booking timestamp.

## Detail page

Personal block:

| Label | What it is |
|---|---|
| PIN | The same 10-digit `viewInmate` id. A person id, not a booking key. |
| Date of Birth | Published on the detail page. Not taken from the listing label. |
| Race | Published. |
| Sex | Published. |
| Location | Published housing text. |

Charge table: parse the desktop copy `table#data-table`. Ignore the stacked small-screen duplicate tables.

Columns: Booking Number, Offense Description, Counts, Arraign Date, Bond Amount, Bond Type, Intake Date/Time, Court Case Number, Release Date/Time, Hold.

| Column | Shape seen |
|---|---|
| Booking Number | 12 digits, `YYYY` plus 8. |
| Court Case Number | `#### CF ###### NC` or `#### MM ###### NC`, with the spaces. |
| Intake Date/Time | `YYYY-MM-DD HH:MM:SS.mmm`. |
| Bond Amount | `$#####.##`, or the text `No Bond Available`. |
| Bond Type | Blank on every row sampled. |
| Release Date/Time | Blank on every row sampled. |
| Hold | `Y`, `N`, or blank. |

Relationships in the sample:

- One person can have several booking numbers and several case numbers.
- Two bookings can share one case number.
- A booking-number year can differ from the intake year.
- Bookings from one stay can share one intake timestamp.

## Parser rules (not a promotion)

- Accept a charge row only when it has a labelled 12-digit Booking Number and an Intake Date/Time.
- One record per distinct Booking Number. Charges stay verbatim. Bond stays on each charge. Intake is the booking timestamp. Court case numbers stay in their own field.
- Do not build a booking number from the PIN, the case number, or a date. A row with no booking number is skipped.
- Unknown stays `""`. A published `$0.00` stays 0. `No Bond Available` stays text, not 0.
- There is no custody label. Mark in custody only when the person is on the Current Inmate Population list and Release Date/Time is blank, and record `status_source` with that reason. A filled Release Date/Time is not in custody.
- Keep the PIN as a person field, never as the key.
- About 1,065 detail pages exist, so a run caps detail fetches at 200 and pauses 0.4 seconds before each one. The start index moves one window per UTC day. A page with no desktop charge table, or without the booking columns, fails the walk.

`scrape()` still returns nothing.
