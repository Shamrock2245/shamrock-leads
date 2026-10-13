# Revize relay runs: Manatee + Charlotte (Leads Ops)

> **2026-10-09: Manatee and Charlotte are `fail_closed`.** Both Revize rosters answer a Cloudflare challenge on page 1 from T-Mobile AS21928 and Comcast AS7922 (and the box). `python main.py --relay-only` now **skips** them (result `status: fail_closed, skipped: true`, not an error), and the smoke scripts exit `4` without fetching. Only Pinellas runs on the relay. Evidence and ranked alternatives: `docs/recon/FL_CHARLOTTE_SOURCE_RECON_2026-10-09.md`, `docs/recon/FL_MANATEE_SOURCE_RECON_2026-10-09.md`. Reopen = relay read + write smoke with no challenge, then flip `SOURCE_CONTRACT_VALIDATED`.

Manatee and Charlotte (FL) are both Revize CMS rosters behind Cloudflare. They run **only on Brendan's home relay, which Leads Ops operates**. Each run uses the relay's own home-ISP exit, or the iPhone hotspot. "Residential" means that connection. It is **not** a proxy service.

## What is enforced in code

| | Manatee | Charlotte |
|---|---|---|
| Egress env | `MANATEE_EGRESS_MODE=direct` (default, only value) | `CHARLOTTE_EGRESS_MODE=direct` (default, only value) |
| Browser | stock Playwright Chromium, headless, `--no-proxy-server`, no `*PROXY*` env vars | same (shared `launch_plain_browser`) |
| Exit check | `check_exit_ip(None, trust_env=False)`; an unknown or unverified exit is refused | same (`revize_roster.resolve_egress`) |
| Proxy / SOCKS / APE / stealth | none (`tests/test_manatee_no_proxy_path.py`) | none (`tests/test_charlotte_no_proxy_path.py`) |
| Egress block | `EgressBlocked` (`anti_bot`, `egress_block`), nothing written | same |
| Scheduling | relay-only (`config/relay_only.py`) | relay-only |

Any other egress mode, including the old `auto`, is a config error. The shared proxy resolver in `scrapers/socks_proxy.py` remains only for Marion and Hillsborough. The Patchright stealth launcher in `scrapers/cf_browser.py` has been removed.

## Relay-only scheduling

- The **VPS/Hetzner scheduler** (`python main.py` with no arguments) registers both counties but never gives them an interval job. Its status lists them under `relay_only`.
- A **dashboard trigger** for either county (run-now, or the custody recheck that refresh-from-source queues) is marked `status: relay_only` on the VPS and is not run there.
- The **relay entry point** runs every relay-only county once (Manatee, Charlotte, and since 2026-10-08 Pinellas: see `docs/ops/PINELLAS_RELAY_RUN.md`), then exits:

  ```bash
  python main.py --relay-only
  ```

  The exit code is `0` when every run succeeds and `1` when any run errors, including an `egress_block`. Schedule it from the relay with launchd or cron. The old VPS intervals were 75 min for Manatee and 90 min for Charlotte. A single county can also be run on its own: `python main.py Manatee` or `python main.py Charlotte`.

Before the first relay write, run the read smokes. They write nothing and print aggregates only:

```bash
MANATEE_EGRESS_MODE=direct python scripts/manatee_residential_smoke.py
CHARLOTTE_EGRESS_MODE=direct python scripts/charlotte_residential_smoke.py
```

Exit codes: `0` ok, `2` egress_block (VPN on, a non-residential exit, or Cloudflare did not clear for a stock browser), `3` parse_drift. On a `2`, retry once later. Do not loop, and do not reach for a proxy or a stealth browser.

Egress-blocked failures (exit gate, Cloudflare 403/challenge) are counted in `egress_blocked_failures`, not toward auto-disable. See the "Auto-disable and egress blocks" section of `docs/ops/PINELLAS_RELAY_RUN.md` for the counter reset.

## Health

Health stays `unverified` for both counties until a Leads Ops prod write smoke from the relay (`MONGODB_URI` set) shows real `Booking #` keys, a blank `bond_amount_raw` and charges filled in. The agent box has no `MONGODB_URI` and no residential exit.

## Charlotte `Released` column

The contract still requires `Released`. Blank / In Custody / No / N/A means in custody; Yes / Released / a date means released. Any other value fails closed. It has not been loosened: no relay smoke output for Charlotte exists yet. Send the read-smoke JSON from the first relay run to Scraper Watch before changing it.
