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
| **Manatee (FL) jail** | `SOURCE_CONTRACT_VALIDATED=False`; no source request | `fail_closed` since 2026-10-09 | Revize roster answers HTTP 403 Cloudflare challenge (`cf-mitigated: challenge`) from every exit tried. The earlier residential-proxy and APE sticky-session path is retired. | A public roster with a real source booking number. Do not reopen through a proxy. |
| **Manatee Clerk (FL)** | Separate court-filing scope (`manatee_clerk.py`); plain HTTPS, no proxy | **omitted → `unverified`** | Court filings, not a jail roster. Write smoke is `requested` and has not passed. | Leads Ops write smoke (`python main.py "Manatee Clerk"`, then `scripts/smoke_evidence_check.py`). A passed smoke does not by itself set `verified_public`. |

## Manatee jail is `fail_closed`; the Clerk scope is not

The 2026-09-22 pass left Manatee unlabeled. That is no longer the registry. Manatee jail has been `fail_closed` since 2026-10-09, with `SOURCE_CONTRACT_VALIDATED=False`. The residential-proxy / APE path is retired. Manatee Clerk is a different scope and stays Health `unverified` until the requested write smoke passes and a separate PR sets the source state. Neither scope is `verified_public`.

## What CAN be done in-repo without live residential egress or new secrets

- Document contract status (this file + matrix evidence notes + registry reopen language).
- Keep Sarasota fail-closed; do not claim Sarasota reopen.
- Align matrix FL summary counts with post-#27 row states (`fail_closed` 9 / `recon_only` 58).
- Manatee jail is already `fail_closed`. Do not add a proxy or APE path back.
- Fixture-only parser tests against synthetic Revize table HTML (no live CF, no proxy secrets).

## What BLOCKS a real reopen (needs Brendan)

1. **Manatee jail** does not reopen on a proxy or APE path. Reopen needs a public roster with a real source booking number. The 403 Cloudflare challenge is the current block.
2. **Manatee Clerk** is waiting on the Leads Ops write smoke. #308 is merged (`0e845b1`) and main `badcf2a` is deployed (Hetzner run `43025037940`, success Nov 11 2026). Do not mark it `verified_public` from that deploy.
3. **Sarasota official source**: a sheriff-published booking-safe broad roster (or equivalent ordinary public API). FL JailTracker `POST /Offender` historically empty HTTP 400 for `SARASOTA_COUNTY_FL` / `MANATEE_COUNTY_FL` / `CHARLOTTE_COUNTY_FL` — not a reopen path.

## Explicit non-goals for this queue PR

- Do not mark Charlotte or Manatee `verified_public`.
- Do not touch `shamrock-trading-bot`.
- Do not change production secrets or invent credentials.
- Do not send emails.

## 2026-10-07 audit update

See `FL_SARASOTA_MANATEE_AUDIT_2026-10-07.md`. Sarasota stays fail closed: the official current-inmate listing has only per-person link ids, with no booking number or booking timestamp. The 2026-10-07 Manatee hardening note (header-mapped keys, `MANATEE_EGRESS_MODE=direct`) is historical. Manatee jail has been `fail_closed` since 2026-10-09 on the 403 Cloudflare challenge, and that proxy/APE path is retired. Manatee Clerk stays `unverified`, write smoke `requested`.

## 2026-10-07 Charlotte hardening

See `FL_CHARLOTTE_REVIZE_2026-10-07.md`. This paragraph is the 2026-10-07 Charlotte note. Manatee jail is no longer on that path (`fail_closed` since 2026-10-09). Charlotte then used the same shared Revize contract (`scrapers/revize_roster.py`): header-mapped columns, source Booking # cross-checked against its link, every charge kept, required Released, bond unknown=`""` (was `"0"`), fail-closed paging, and `EgressBlocked` on CF / unverified exits. Live check from the box: `/`, `/bookings`, `?page=2` → 403 CF challenge. Stays `unverified` until a Leads Ops residential read + prod write smoke (`CHARLOTTE_EGRESS_MODE=direct`).
