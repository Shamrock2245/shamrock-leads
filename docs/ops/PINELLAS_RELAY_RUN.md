# Pinellas relay runs (Leads Ops)

**Owner exception:** Brendan, 2026-10-08 1:38 PM ET ("we will connect at home, with residential egress"); CoS agreed. Pinellas (FL) runs **only on Brendan's home relay, which Leads Ops operates**, with a **non-stealth** browser. Same relay-only pattern as Manatee and Charlotte (#124, `docs/ops/REVIZE_RELAY_RUN.md`).

Why a browser: Who's In Jail (`https://whosinjail.pinellassheriff.gov/`) is a Blazor Server app. A plain-HTTP read gets only the JS shell; rows render over the app's SignalR circuit (`docs/recon/FL_PINELLAS_RELAY_ONLY_2026-10-08.md`).

## What is enforced in code

| | Pinellas |
|---|---|
| Egress env | `PINELLAS_EGRESS_MODE=direct` (default, only value) |
| Browser | stock Playwright **bundled Chromium**, headless, `--no-proxy-server`, no `*PROXY*` env vars (shared `launch_plain_browser`); not `channel="chrome"` |
| User-Agent | honest: `scrapers.counties.pinellas.USER_AGENT` names the bot (`ShamrockLeadsBot/1.0 ...`); no Chrome spoofing |
| Exit check | `check_exit_ip(None, trust_env=False)` before the browser starts; an unknown or non-residential exit raises `EgressBlocked` and nothing is fetched |
| Patchright / stealth / proxy / impersonation / challenge solving | none (`tests/test_pinellas_relay_only.py`) |
| Scheduling | relay-only (`config/relay_only.py`): no VPS/Hetzner interval job; a dashboard trigger on the VPS is marked `relay_only` and not run |

## Relay setup (once)

```bash
pip install -r requirements.txt          # includes playwright>=1.48
python -m playwright install chromium    # bundled Chromium (Linux hosts: add --with-deps)
```

`patchright` is not needed for Pinellas.

## Runs

Read smoke first (writes nothing, prints aggregates only, no names):

```bash
PINELLAS_EGRESS_MODE=direct python scripts/pinellas_relay_smoke.py
```

Exit codes: `0` ok (keyed rows), `2` egress_block (VPN on or a non-residential exit), `3` no keyed rows or every charge modal failed (`modals_not_rendered`, with `modal_failure_reasons`), `1` anything else. On a `2`, retry once later. Do not loop, and do not reach for a proxy or a stealth browser.

### Modal debug (relay only, off by default)

```bash
PINELLAS_EGRESS_MODE=direct PINELLAS_MODAL_DEBUG=1 python scripts/pinellas_relay_smoke.py
# equivalent: PINELLAS_EGRESS_MODE=direct python scripts/pinellas_relay_smoke.py --debug
```

This writes `logs/pinellas-modal-debug-<YYYYmmdd-HHMMSS>.jsonl` (the path is in the smoke JSON as `debug_log`) and nothing else. One line per modal attempt has the selector, wait condition, timeout, elapsed ms, click mode, exception type/text, console errors, page errors, failed requests, SignalR websocket open/close/error, DOM tag/class counts, and a names-free modal structure (field labels only; values become `#text(len)`). Without the flag, every failed attempt still logs a names-free `reason=` WARNING line. `PINELLAS_MODAL_TIMEOUT_MS` sets the per-attempt wait (default 20000, clamped to 2000–45000).

Write run: `python main.py --relay-only` runs every relay-only county (Manatee, Charlotte, Pinellas) once and exits non-zero if any run errors. Pinellas alone: `PINELLAS_EGRESS_MODE=direct python main.py Pinellas` (`MONGODB_URI` set). The old VPS interval was 90 min; `DAYS_BACK = 3`.

## Health

Pinellas stays `unverified` (not `verified_public`) until a Leads Ops prod write smoke **through the relay** shows real source booking numbers, charges, and bonds following the #142 rules (`""` when any charge is unpublished, published `$0.00` kept as `"0"`).

## Auto-disable and egress blocks

The auto-disable threshold is 5 consecutive counted failures (`scrapers/scraper_resilience.py` `auto_disable_threshold()`, env `SCRAPER_AUTO_DISABLE_THRESHOLD`). For relay-only counties (Pinellas, Charlotte, Manatee), an egress-blocked failure (exit gate `EgressBlocked`, a Cloudflare 403 or challenge, any non-cooldown `anti_bot`) is not counted. It goes to `scraper_status.egress_blocked_failures` with `last_error_class: egress_blocked`. Parser failures still count. Charlotte and Manatee are also in `AUTO_DISABLE_EXEMPT_LABELS`, so they are never skipped and alert once at 5. Pinellas is not exempt, so 5 real failures in a row auto-disable it.

To reset a streak inflated by blocked-exit runs before this change, Leads Ops runs (with `MONGODB_URI`):

```bash
python scripts/scraper_reenable.py "Charlotte (FL)" --by leads-ops
python scripts/scraper_reenable.py "Manatee (FL)" --by leads-ops
```

`reenable_scraper` sets `consecutive_failures` and `egress_blocked_failures` to 0 even when the scope is not auto-disabled. The dashboard ▶ Re-enable button only matches `auto_disabled: true` documents, so it will not reset these two.
