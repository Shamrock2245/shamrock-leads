# DeSoto (FL): fail closed until a source booking number is proven (2026-10-15)

**Decision (CoS, 2026-10-15, option 1 on #222):** DeSoto is `fail_closed`, with no source request and no writes.

## Why
- `https://jail.desotosheriff.org/DCN/inmates` (DCN DevExpress roster) publishes no booking number. The only per-row id is the URL `bid`.
- CoS review on 2026-10-08 (`FL_HOME_COUNTIES_SOURCE_CONTRACT_2026-10-08.md`): `bid` is the jail site's internal record id, not a source booking number (the same kind of id #136 rejects). DeSoto was held at `recon_only` / Health `unverified` (#138), but it stayed on the write list, so rows kept being written under `bid` keys. This closes that.
- One honest probe from the agent box on 2026-10-15 (plain GET, honest UA, certificate verified) returned HTTP 403. It was 200 on 2026-10-08.

## What is in place
- `scrapers/counties/desoto.py` has `SOURCE_CONTRACT_VALIDATED = False`. `scrape()` returns `[]` before any request.
- The plain-HTTPS rewrite from #222 is kept in `_fetch_and_parse()`: honest UA, verified TLS, `egress_block`/`tls_cert_invalid`, bond published-only, custody only from the source, `bid` required and never synthesized. The old stealth DrissionPage browser with `--ignore-certificate-errors` is gone.
- Health `SCRAPER_SOURCE_STATES["DeSoto (FL)"] = "fail_closed"`. `config/source_guard.SOURCE_HOST_LABELS` already maps `desotosheriff.com` (parent of `jail.desotosheriff.org`) to `DeSoto (FL)`, so re-checks and detail fetches are refused.
- Rows already stored are untouched. Any cleanup waits on Brendan.
- DeSoto remains in `AUTO_DISABLE_EXEMPT_LABELS` / `KEY_FL_COUNTIES`. A fail_closed run returns nothing; it does not error.

## To reopen
The source has to publish a printed booking number, and it has to be proven stable and unique per booking across sessions. Then: CoS sign-off, switch the key to that number, and set `SOURCE_CONTRACT_VALIDATED = True` and the Health state.
