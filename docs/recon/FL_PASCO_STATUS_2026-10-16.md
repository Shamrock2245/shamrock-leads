# FL Pasco — custody status recon (2026-10-16)

Honest probes from the box (plain_http, honest UA, TLS verified; no names/DOBs logged):
- `GET /jmc/` → 200; the JMC Nuxt router (`_nuxt/d67b210.js`) defines separate `/inCustody` and `/pastArrests` views.
- `GET /jmcapi/custody/GetInCustody` → 200, 1791 rows. Keys: age, bookingDate, bookingNumber, bookingTime, dob, endOfSentence, filepath, firstName, lastName, middleName, nameId, nmimage, race, recblock, sex, suffix. No release date or status field on any row.

Finding: the roster is the source's own custody-only view (an in-custody route distinct from past arrests, backed by the GetInCustody endpoint). Status "In Custody" is kept, tied to that view with `status_source=published_in_custody_view`, the same pattern as the SSW "Current Confinements" view.

Booking key: source `bookingNumber`. fail_closed: no.
