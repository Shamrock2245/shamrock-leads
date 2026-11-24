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

Owner ruling, relayed by Chief of Staff on 2026-11-20: the key is the Court Case Number.

- Accept a charge row only when the Court Case Number cell is non-blank after trimming the ends. Internal spaces stay. Do not derive, hash, or synthesize a case number.
- One record per distinct case number on that detail page. Every charge row on that case stays its own charge entry. Do not merge or dedupe charges.
- Booking Number and Intake Date/Time are data. If a case spans more than one booking, both booking numbers and both intake timestamps stay on the record as lists. They are not the key.
- A row with no case number is skipped. The log line is the county, the reason `no court case number`, and the count. No names, dates of birth, or ids.
- Do not fall back to the PIN or the Booking Number.
- Unknown stays `""`. A published `$0.00` stays 0. `No Bond Available` stays text, not 0.
- There is no custody label. Mark in custody only when the person is on the Current Inmate Population list and Release Date/Time is blank, and record `status_source` with that reason. A filled Release Date/Time is not in custody.
- Keep the PIN as a person field, never as the key.
- About 1,065 detail pages exist, so a run caps detail fetches at 200 and pauses 0.4 seconds before each one. The start index moves one window per UTC day. A page with no desktop charge table, or without the Court Case Number column, fails the walk.

The 2026-11-18 read did not keep a 5-person fixture file. The note above records that two bookings can share one case number. The parser puts those booking numbers on that one case record. No published case numbers from that read are stored here.

`scrape()` still returns nothing.
