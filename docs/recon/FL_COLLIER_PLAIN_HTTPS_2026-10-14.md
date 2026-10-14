# Collier (FL): plain verified HTTPS, custody unknown (2026-10-14)

CoS asked to drop Collier's curl_cffi Chrome disguise and stop hard-coding
"In Custody".

## Live check (agent box, plain `requests`, honest User-Agent, TLS verified)

| Time (ET) | GET `/arrestsearch/Report.aspx` | timerLoad UpdatePanel postback |
|---|---|---|
| 8:22 AM | 200, ViewState present | 200, body `pageRedirect` -> `/arrestsearch/errorpage.aspx` ("There was an error.") |
| 8:23 AM | 200 | same |

No CAPTCHA and no 403, but the postback that loads the grid is refused for
the plain client every time. I didn't re-test with impersonation (not
allowed), so I can't tell a fingerprint block from a site fault. CoS (2026-10-14) kept Collier `unverified`, not fail_closed: each run now
fails visibly with `egress_block` until a plain read works. A read from the Leads Ops relay (stock client, honest UA) is the next test.

## Custody

`Report.aspx` is an arrest report, not a custody roster, and publishes no
custody/release field. `Status` is now `""` (unknown). `MongoWriter` no longer
`$set`s a blank status over a stored one (e.g. a recheck's "Released").
