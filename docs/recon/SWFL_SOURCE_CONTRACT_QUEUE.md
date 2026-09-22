# SWFL Source-Contract Queue — Sarasota / Charlotte / Manatee

> **Status (2026-09-22):** Evidence-bound recon after PR #27 merge. **Fail closed until validated.**
> No emails. No production secrets changes. No invented credentials.
> This document does **not** authorize `verified_public`, live reopen, Mongo writes, or alerts.

Related: [`COUNTY_SOURCE_CONTRACT_MATRIX.md`](./COUNTY_SOURCE_CONTRACT_MATRIX.md), [`../COUNTY_REGISTRY.md`](../COUNTY_REGISTRY.md), [`../JAILTRACKER_SOURCE_SAFETY.md`](../JAILTRACKER_SOURCE_SAFETY.md).

## Findings table

| County | Runtime gate today | Health `SCRAPER_SOURCE_STATES` | Why empty / gated | What reopen would require |
|---|---|---|---|---|
| **Sarasota (FL)** | `SOURCE_CONTRACT_VALIDATED=False`; `scrape()` returns `[]` (no network) | `fail_closed` (already) | No official booking-safe broad roster verified through ordinary public access; third-party mirror, JailTracker CAPTCHA, proxy, profile, DOB, mugshot paths retired | Official public broad roster with complete identity + source-issued booking id + booking timestamp; fixture mapping tests; non-writing aggregate smoke; then flip validation + Health label |
| **Charlotte (FL)** | `resolve_residential_proxy(require=True)` then Revize roster via Patchright; raises if no US residential egress | **omitted → `unverified`** (intentional) | Cloudflare on Revize; datacenter/VPS exits fail preflight; detail pages blocked | Brendan-confirmed healthy US residential exit (APE/Warren sticky or office/Tailscale SOCKS) + bounded CF-cleared roster smoke proving Booking # / name / charge / arrest date; then decide `verified_public` vs keep gated |
| **Manatee (FL)** | Same pattern as Charlotte (`sticky_session=fl-manatee`) | **omitted → `unverified`** (intentional) | Same Revize + CF + residential preflight | Same Brendan residential proof + Manatee roster smoke; do not copy Charlotte success as Manatee proof |

## Why Charlotte / Manatee are NOT labeled `fail_closed` in this pass

PR #27 explicitly deferred them. Runtime is **not** an unconditional Broward/Sarasota-style empty guard: when residential egress is healthy the scrapers still attempt to emit roster rows. Labeling `fail_closed` in `SCRAPER_SOURCE_STATES` without also setting `SOURCE_CONTRACT_VALIDATED=False` would misrepresent Health. Hard-gating them would stop any currently productive residential runs and needs an explicit Brendan decision.

They remain **`recon_only` / Health `unverified`** — not `verified_public`.

## What CAN be done in-repo without live residential egress or new secrets

- Document contract status (this file + matrix evidence notes + registry reopen language).
- Keep Sarasota fail-closed; do not claim Sarasota reopen.
- Align matrix FL summary counts with post-#27 row states (`fail_closed` 9 / `recon_only` 58).
- Add safer stubs / Health labels later **only** when Cos/Brendan approve hard-gating Charlotte/Manatee.
- Fixture-only parser tests against synthetic Revize table HTML (no live CF, no proxy secrets).

## What BLOCKS a real reopen (needs Brendan)

1. **Residential egress proof** for Charlotte and Manatee (Warren APE sticky and/or office/Tailscale SOCKS CONNECT + residential exit-IP org check). No new proxy credentials invented in-repo.
2. **Bounded live smoke** (staff-operated): CF clear, roster rows with source Booking #, complete name, charge, arrest date; no detail-page bypass, no CAPTCHA bypass, no synthetic booking ids.
3. **Sarasota official source**: a sheriff-published booking-safe broad roster (or equivalent ordinary public API). FL JailTracker `POST /Offender` historically empty HTTP 400 for `SARASOTA_COUNTY_FL` / `MANATEE_COUNTY_FL` / `CHARLOTTE_COUNTY_FL` — not a reopen path.
4. **Optional product decision**: hard-gate Charlotte/Manatee (`SOURCE_CONTRACT_VALIDATED=False` + `fail_closed` Health) until (1)–(2) land — only if Brendan wants to stop residential emission meanwhile.

## Explicit non-goals for this queue PR

- Do not mark Charlotte or Manatee `verified_public`.
- Do not touch `shamrock-trading-bot`.
- Do not change production secrets or invent credentials.
- Do not send emails.
