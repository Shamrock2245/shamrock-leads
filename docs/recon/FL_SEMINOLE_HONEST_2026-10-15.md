# Seminole FL: honest plain HTTPS probe and roster scope (2026-10-15)

Supersedes #208. Probes were minimal and honest: `scrapers.plain_http` with the honest User-Agent, certificate checks on, no impersonation, no proxy and no CAPTCHA solving. No names or DOBs were logged.

## Probes (box egress)
1. `GET https://seminole.northpointesuite.com/custodyportal/` via plain_http raised `EgressBlocked("egress_block: Seminole HTTP 403")`.
2. The same GET without the wrapper, to read the headers, returned HTTP 403, 118 bytes, `Server: cloudflare` with a CF-RAY header, and a body of just "403 Forbidden".
   This is the same 403 recorded in #208 from main 301a7dd. The old code only got through by impersonating Chrome with curl_cffi.

## Roster finding
- No DoSearch or detail response could be read honestly, so the scope of the "current" filter (`inmatestatus=current`) can't be verified. It is **not** used as custody proof.
- Status comes only from the detail page's published `Status` label, with `status_source = "published_detail_status"`. Otherwise it is "".
- The booking key is the source `Booking - <digits>` header. No header means no record. personId is kept as Person_ID only and is never used as a key.

## Fail-closed question (for CoS)
If a Leads Ops or VPS direct read also gets the Cloudflare 403, the honest scraper fails loudly every run as `egress_block` (blocked in Health, 0 rows). Seminole should then move to `fail_closed` (`SOURCE_CONTRACT_VALIDATED=False` plus a registry entry), like Leon and St. Johns. This PR keeps Health at `unverified` and does not make that change.
