# HANDOFF — Surety Bond Report Auto-Sort, 2012+ Ranges, Graceful Errors + CI Recovery

> Status: **IN PROGRESS** — core implementation landed, verification + deploy recovery remain.
> Date: 2026-07-24 · Author: Manus session (shamrock-leads project)

## Mission (original request)

1. Bond reports for the surety must **auto re-sort oldest → newest bond written** whenever the
   report is printed or saved with a typical keystroke (⌘S on macOS), with every row keeping its
   power #, bond date, and all relative details.
2. Bond reports must be runnable for **any date range from 2012-01-01 → present**.
3. All failures must be **graceful** and surface as much relevant diagnostic info as available.
4. Everything must land on `main` **with all GitHub checks passing** and working in production.

## ✅ Completed in this commit

| Area | File | What was done |
|---|---|---|
| XLSX builder | `dashboard/services/bond_report_xlsx.py` | Added `REPORT_EPOCH` (2012-01-01), `_bond_sort_key()`, and `sort_bonds_chronologically()` (never raises; undated rows trail dated rows, logged with counts). `build_official_bond_report()` now re-asserts oldest→newest order for bonds, voids, discharges, transfers. TZ-aware dates normalized so sorting can't crash on mixed aware/naive datetimes. |
| Automation API | `dashboard/routers/automation_sweeps.py` → `POST /api/automation/bond-report` | Accepts optional `start_date` / `end_date` (YYYY-MM-DD), clamps to 2012-01-01, swaps inverted ranges, collects `warnings[]` instead of failing. Mongo query now sorts `bond_date` ascending (limit raised 2000→5000), date window applied to actives + discharges. Response metadata includes `sort_order`, `start_date`, `end_date`, `warnings`. 500 responses now return `error_type`, request `context`, and a remediation `hint`. |
| Reports UI | `dashboard/sl-reports.js` | Added `REPORT_EPOCH`, new `all` preset ("All Time — Since 2012"), custom-range clamping/swapping with toast. Added `sortBondsOldestFirst()` (graceful, undated rows last) applied to the itemized liability register (groups flattened → chronological), discharge and forfeiture tables. Added `resortForOutput()` — DOM-level re-sort of every rendered report table (totals rows pinned to bottom, index cells renumbered, diagnostic summary + warning toast on partial failure). Hooked into: `exportPDF()` (and thus `printReport()`), **⌘S/Ctrl+S** (prevents default, re-sorts, opens printable copy), **⌘P/Ctrl+P** (re-sorts before native dialog), and `window.beforeprint` safety net. Exposed `resortForOutput` + `sortBondsOldestFirst` on the public API. |
| Dashboard HTML | `dashboard/index.html` | Added "All Time" preset button, `min="2012-01-01"` on both report date inputs, bumped cache-buster `sl-reports.js?v=5`. |

Local verification already run: `node --check dashboard/sl-reports.js` ✅, `python3 -m py_compile` on both Python files ✅.

## 🔧 Remaining work (do next, in order)

1. **Unit tests (TDD requirement)** — add `tests/test_bond_report_sorting.py`:
   - `sort_bonds_chronologically`: mixed ISO strings / datetimes / missing dates → oldest first, undated last, never raises.
   - `/api/automation/bond-report` date parsing: invalid date → warning not crash; pre-2012 → clamped; swapped range → corrected.
   - Follow the style of `tests/test_liability_report_wiring.py` (pytest + TestClient with mocked `get_collection`).
2. **Check `dashboard/routers/reports.py`** (`/api/reports/surety-liability` etc.) — confirm the
   backend passes `start`/`end` through for all report types and that nothing caps ranges to the
   current year. Extend the same 2012 clamp + warnings pattern there if needed.
3. **Weekly cron reports** (`dashboard/cron.py`, `surety_weekly_reports`) — confirm they call the
   updated builder and inherit chronological order (they should automatically, since the builder
   sorts; verify no local re-sort overrides it).
4. **Deploy to Hetzner is RED since run #458** — root cause is the **VPS itself, not the code**:
   - Server `178.156.179.237` accepts TCP but drops SSH banner exchange and returns empty HTTP replies
     (likely OOM/disk-full or fail2ban after run #458 died mid-deploy at 2m50s; later runs fail in ~37s).
   - Action: reboot / power-cycle from console.hetzner.cloud, check `df -h`, `free -m`, `fail2ban-client status sshd`,
     and re-run the failed workflow. Also note older knowledge references a second box at `5.161.126.32`
     (`ssh -i .shamrock_deploy_key root@5.161.126.32`) — confirm which host the workflow targets in
     `.github/workflows/deploy-hetzner.yml` secrets.
5. **Re-run GitHub Actions on main** after the VPS is healthy; confirm the "Deploy to Hetzner"
   workflow goes green for this commit and the four failing feature commits before it
   (27bc602, a81ba0b, 6fffe83, 2c62285 — those failures were infra, not code, but verify each deploys cleanly).
6. **Smoke test in production** once deployed:
   - Reports tab → Surety Liability → verify register renders oldest→newest.
   - Press ⌘S → toast "Report re-sorted oldest → newest…" and printable copy opens sorted.
   - Press ⌘P / File▸Print → on-screen table re-sorts before dialog.
   - "All Time" preset returns records back to 2012; custom range pre-2012 clamps with toast.
   - `POST /api/automation/bond-report` with `{"start_date":"2015-01-01","end_date":"2016-12-31"}` →
     XLSX rows ascend by bond date; bad dates return `warnings` not 500.
7. **PII check** — confirm new warning/error payloads and logs never include defendant PII
   (they currently only carry dates, counts, surety code — keep it that way).

## Key contracts introduced (do not break)

- `sort_bonds_chronologically(records) -> list[dict]` — public, imported nowhere else yet; safe for tests.
- `SLReports.resortForOutput()` and `SLReports.sortBondsOldestFirst(list, dateFields?)` — public JS API.
- `POST /api/automation/bond-report` body: `{ surety, include_discharges, store, start_date?, end_date? }`;
  response adds `sort_order`, `start_date`, `end_date`, `warnings`.
- `REPORT_EPOCH = 2012-01-01` in both Python and JS — change in both places or neither.
