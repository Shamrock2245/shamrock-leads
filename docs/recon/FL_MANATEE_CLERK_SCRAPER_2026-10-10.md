# Manatee Clerk (FL) court-filing scraper, 2026-10-10

**Owner exception:** Brendan, 2026-10-10 (relayed by CoS). The Clerk's court-records site is approved as a lead source for Manatee even though it publishes no booking number. It is a **separate scope**, `Manatee Clerk (FL)` (`scrapers/counties/manatee_clerk.py`), and staff see the source as **"Manatee Clerk (court filing)"**. The Manatee jail scraper (`Manatee (FL)`, `scrapers/counties/manatee.py`) is not touched and stays `fail_closed` (`FL_MANATEE_SOURCE_RECON_2026-10-09.md`).

**Health:** `unverified` (no `SCRAPER_SOURCE_STATES` entry, with a comment) until a Leads Ops write smoke (requested in `docs/recon/smoke_evidence.json`). Not `verified_public`.

## Source

| | |
|---|---|
| List | `GET https://records.manateeclerk.com/CourtRecords/Search/CaseType/{page}/{size}/{MM-DD-YYYY}/{MM-DD-YYYY}?caseTypeId=N`: 10 FELONY, 35 MISDEMEANOR, 37 MISDEMEANOR-MISC (18 CRIMINAL TRAFFIC is not read) |
| List columns | View (a per-row form), Case Number, Party Name, Party Type, Case Type, Case Status, File Date, DOB (year). `Matching Results: N` when there are rows; `NO RECORDS FOUND` when there are none |
| Detail | `POST /CourtRecords/Case/Details` with the row form's `__RequestVerificationToken`, `caseId` and `searchAddress` |
| Detail labels used | Case, Filed, Status, Type, Judge; Parties (Party Type, Name, Gender, DOB; the mailing address and attorney in the same cell are **not** read); Charges (Offense Date, Statute, Description, Degree, Citation); Events (date, time, event, Location, Room); Bonds (Bond Type, Active Amount); OBTS headings (`OBTS <n> - Charge <k>`, `Agency: <code> - …`) with "Arrest Summons Served" per count |
| Access | Plain HTTPS, `Microsoft-IIS/10.0`, no Cloudflare, no CAPTCHA; the terms page has no automation prohibition |

The site keeps search state per session (a second case-type search in the same session came back empty once during the check), so the scraper opens one session per case type.

## Behaviour

- Honest UA `ShamrockLeadsBot/1.0 (+https://shamrockbailbonds.biz; Manatee Clerk court filing reader; plain HTTPS)`, 2.5 s between requests, at most 80 detail POSTs per run, 4 list pages of 50 per case type, cases filed yesterday and today, every 120 min.
- The list walk fails loudly when the published count is over the 200-row page cap, when a page repeats rows already listed, or when the walk ends short of the count.
- A challenge or CAPTCHA (`cf-mitigated`, Turnstile / reCAPTCHA / hCaptcha / "Just a moment..." markers, or HTTP 401 / 403 / 429) stops the run at once with `EgressBlocked` (`egress_block:` prefix: classified `anti_bot` / egress-blocked and non-retryable, so the base retry never repeats it). Transient 5xx and timeouts keep the base transient-network retry. No stealth, proxy, impersonation or CAPTCHA solving. A short-of-count page walk, a missing results table, a list page with neither rows nor `NO RECORDS FOUND`, a Bonds header change, or a detail whose case number does not match the list row raises `ParseDriftError`.
- `config/source_guard.py` is honoured: `scrape()` checks `fail_closed_reason(scraper=self, url=…)` first, and `manateeclerk.com` maps to `Manatee Clerk (FL)` (not to the jail scope).

## Keys and fields

- **No booking number.** `Booking_Number` stays blank and never holds the case number.
- Internal key `mc_case_v1:` + sha256(normalised case number), in `extra.mc_case_key`. **Normalisation:** upper-case, then drop every character that is not A-Z or 0-9 (spaces, tabs, dashes, dots, slashes). The Clerk prints the Florida uniform case number as one token (e.g. `2026CF009901AX`, synthetic), so `2026-CF-009901-AX`, `2026 cf 009901 ax` and `2026cf009901ax` are the same case; leading zeros and the party suffix are kept.
- OBTS is **not** in the key. `obts_number` is a plain field: when a later scrape finds an OBTS on a case that had none, it upserts the same record and fills the field; a later scrape with no OBTS leaves the stored value alone (`MongoWriter` sets these fields only when present).
- Multi-defendant cases fail closed: the detail page is case-wide (charges, bonds and OBTS are not per person), so a case with more than one Defendant on the list or the detail is skipped (counted as `multi_defendant_skipped`, no names). `MongoWriter` accepts a blank booking only for the exact scope `("FL", "Manatee Clerk")` with a key matching `^mc_case_v1:[0-9a-f]{64}$` (`core/booking_identity.py`). The stored doc's `booking_number` is the key (the record id for the unique index and dashboard routes), plus `booking_key_internal: true` and `mc_case_key`. Every display helper (`public_booking_number`, `serialize_doc`'s `booking_number_display`, `redact_internal_keys`, `slBookingLabel` / `slRedactKeys`) prints it blank. Every other county's blank-booking guard is unchanged.
- DOB and sex come only from the detail Defendant whose name matches the list row (the list prints LAST, FIRST and the detail FIRST LAST, so names are compared as token sets). No match, or more than one, leaves them blank (counted in the run log, never names), so a co-defendant's identity is never copied.
- `case_number` = the Clerk case number; `obts_number` = the OBTS number(s); `filing_date`, `case_status`, `source_label` are their own fields.
- Charges verbatim (Charge Description, joined with ` | `); `charge_details` per charge: offense date, statute, description, degree, citation, Arrest Summons Served.
- `Arrest_Date` = the earliest Arrest Summons Served date; `Booking_Date` stays blank (a filing date is not a booking date). `Court_Date` / `Court_Time` / `Court_Location` = the next scheduled event. `Agency` = the arresting agency code(s). `Status` = `Unknown` (custody is not published). `Detail_URL` is blank (the detail is a token POST).
- **Bond** only from the Bonds table's bond rows (sum of Active Amount). The `N Bond(s)` totals row is ignored, so the common `0 Bond(s) / $0.00` shape gives `""` (unknown), never `"0"`. A bond row the Clerk publishes as `$0.00` counts (`"0.00"`).

## Live read (2026-10-10, from the agent box)

Status, labels and counts only; values were masked and nothing personal was stored or committed.

| Request | Result |
|---|---|
| FELONY list, filed 10/07–10/08 | 200, IIS, no challenge; 11 rows, `Matching Results: 11`, all Party Type `Defendant`, Case Type `Felony`, status `OPEN` |
| MISDEMEANOR (35) list, same dates | 200; 11 rows, all `Defendant` |
| MISDEMEANOR-MISC (37) list | 200; `NO RECORDS FOUND` |
| 6 FELONY detail POSTs | all 200; labels as in the table above; 1 defendant each; 1–3 charges with matching OBTS headings; 5 had only the `0 Bond(s) / $0.00` totals row (bond `""`), 1 had 3 `SURETY BOND` rows plus the totals row |
| Challenge / CAPTCHA markers | none on any page |
