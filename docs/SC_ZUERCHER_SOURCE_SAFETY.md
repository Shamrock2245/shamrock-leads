# South Carolina Zuercher Source-Safety Registry

**Last reconciled:** 2026-08-14 EDT  
**Deployment evidence:** Guard commit `7718bf8` deployed successfully. Leads `/health`, Sign, School, Paperwork, and Social `/auth` returned 200. Per-scraper Mongo persistence and alert delivery remain unproven.
**Purpose:** This registry records the official-source audit for five registered South Carolina Zuercher wrappers. A portal’s existence or an Arrest Date column does not establish a safe intake contract. To emit an arrest record, the broad public source must provide complete identity, a source-issued booking or inmate identifier, and a booking date or timestamp.

| County | Existing wrapper | Official public source | Audited contract | Current policy |
|---|---|---|---|---|
| Anderson | `anderson.py` | `anderson-so-sc.zuercherportal.com` | Search-only portal; no supported broad roster contract observed | **Fail closed** |
| Cherokee | `cherokee.py` | `cherokee-so-sc.zuercherportal.com` | No safely validated broad roster contract | **Fail closed** |
| Colleton | `colleton.py` | `colleton-so-sc.zuercherportal.com/#/inmates` | Public roster lacks a source-issued booking/inmate ID and booking timestamp | **Fail closed** |
| Kershaw | `kershaw.py` | `kershaw-so-sc.zuercherportal.com` | Public roster lacks a source-issued booking/inmate ID and booking timestamp | **Fail closed** |
| Laurens | `laurens.py` | `laurens-911-sc.zuercherportal.com/#/inmates` | Public interface unavailable; no validated broad roster contract | **Fail closed** |

## Guard behavior

`ZuercherBaseScraper` now requires an explicit source-issued booking or inmate ID and source booking date before constructing an `ArrestRecord`. It no longer creates synthetic keys from a name and arrest date, and it preserves custody as unknown unless explicitly supplied by the source. The five audited wrappers set `SOURCE_CONTRACT_VALIDATED = False`, return an empty list **before any HTTP request**, and retain their scheduler registrations only for accurate operational inventory. This guard was deployed in `7718bf8`.

## Revalidation criteria

A guarded wrapper may be re-enabled only after a fresh official-source validation proves a supported public broad-list contract with complete identity, a source-issued booking/inmate identifier, booking date or timestamp, county/state scope, and no CAPTCHA, WAF, login, or unsupported query bypass. Any reactivation requires deterministic tests, a non-writing aggregate smoke, a normal deployment verification, and observed per-scraper persistence and alert telemetry before it is marked productive.
