# Duval FL: honest plain HTTPS, fail closed (2026-10-21)

**Source:** Jacksonville Sheriff's Office inmate search, `https://inmatesearch.jaxsheriff.org/` (ATIMS, Angular SPA).
**Decision proposed:** `fail_closed` (`SOURCE_CONTRACT_VALIDATED = False`, Health `fail_closed`, matrix FL 031 `fail_closed`). Reopening is a CoS decision.
**Method:** one honest GET through `scrapers.plain_http` (honest User-Agent, certificate checks on, no proxy, no browser, no impersonation, no gate click). One read-only `openssl s_client` handshake to read the presented chain. No names or DOBs were read or recorded.

## Evidence
1. **Probe, 2026-10-21 08:17 EDT (agent box):** `plain_http.request(GET /)` raised `SourceTLSInvalid: tls_cert_invalid: Duval certificate failed verification (SSLError)`. No HTTP status was received. There is no `verify=False` retry.
2. **Presented chain:** `openssl s_client -servername inmatesearch.jaxsheriff.org` shows depth 0 only, `CN=inmatesearch.jaxsheriff.org, O=JSO`, issuer `Network Solutions RSA OV SSL CA 3`. The result is `verify error:num=20 (unable to get local issuer certificate)` and `num=21 (unable to verify the first certificate)`, return code 21. The server does not send its intermediate. This is a county-side misconfiguration, like Osceola's legacy host and Nassau.
3. **Same as 2026-10-15:** `FL_DUVAL_ROSTER_SCOPE_2026-10-15.md` recorded the same `tls_cert_invalid` result.
4. **Contract not readable honestly:** the old module drove DrissionPage (shared Chrome options: Chrome UA and stealth JS), typed a last name of `"a"`, and intercepted the SPA's `api` traffic. The search sits behind an ATIMS "I'm not a robot" gate (#189: never clicked). The JSON API's URL, fields and booking-number format have never been read through an honest verified request.
5. **Roster scope:** not proven custody-only (`FL_DUVAL_ROSTER_SCOPE_2026-10-15.md`), so presence never sets "In Custody".

## What the module does now
- No source request while fail closed (`scrape()` returns `[]` and BaseScraper's preflight guard records the reason).
- Reopen path (tests only): one `plain_http` GET. A block raises `egress_block`, a bad certificate raises `tls_cert_invalid`, the robot gate raises `captcha_gate` (`AntiBotBlocked`), any other non-200 raises `DuvalHTTPError` (classified by status), and a page or payload that doesn't match raises `ParseDriftError`. Nothing is swallowed.
- Keys: only a published booking-number field. The old `id` / `inmateId` / `jacketNumber` fallbacks (person or internal ids) are removed, and the DOM path no longer takes any 4+ digit number as a key.
- Bond: only a published bond field (a published 0 is kept), else `""`. The DOM path no longer takes the first dollar figure in a row.
- Status: published release date -> `Released`, else a published status field verbatim, else `""`. `status_source` is recorded.

## Reopen options (CoS)
1. JSO fixes its chain (sends the Network Solutions intermediate). Re-probe honestly, then read the API contract.
2. Owner approval to pin the specific Network Solutions RSA OV SSL CA 3 intermediate (still full verification, never `verify=False`), like the Nassau option.
3. In either case the ATIMS robot gate must not need a click. If it does, Duval stays closed (CAPTCHA only for Broward and Lake).
