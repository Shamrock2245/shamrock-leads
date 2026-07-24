# HANDOFF — Surety Bond Report Auto-Sort, 2012+ Ranges, Graceful Errors + CI Recovery

> Status: **CODE COMPLETE** — unit tests green; VPS deploy recovery still ops-side.
> Date: 2026-07-24 · Follow-up: Grok session completed remaining TDD + reports.py work

## Mission (original request)

1. Bond reports for the surety must **auto re-sort oldest → newest bond written** whenever the
   report is printed or saved with a typical keystroke (⌘S on macOS), with every row keeping its
   power #, bond date, and all relative details.
2. Bond reports must be runnable for **any date range from 2012-01-01 → present**.
3. All failures must be **graceful** and surface as much relevant diagnostic info as available.
4. Everything must land on `main` **with all GitHub checks passing** and working in production.

## ✅ Completed

| Area | File | What was done |
|---|---|---|
| XLSX builder | `dashboard/services/bond_report_xlsx.py` | `REPORT_EPOCH` (2012-01-01), `REPORT_ROW_LIMIT` (5000), `_bond_sort_key()`, `sort_bonds_chronologically()`, `parse_report_date_window()`, `mongo_bond_date_filter()`. `build_official_bond_report()` re-asserts oldest→newest for bonds/voids/discharges/transfers. |
| Automation API | `dashboard/routers/automation_sweeps.py` → `POST /api/automation/bond-report` | Optional `start_date` / `end_date`, 2012 clamp, swap inverted ranges, `warnings[]`, Mongo sort ascending, rich 500 with `error_type` / `context` / `hint` (no PII). |
| Dashboard reports API | `dashboard/routers/reports.py` | Shared date window helpers; surety-liability / discharged / forfeitures / agent-production pass start/end, clamp to 2012, return `warnings` + `sort_order`, limit raised to 5000, Mongo uses YYYY-MM-DD (not full ISO). No current-year cap. |
| Weekly cron | `dashboard/cron.py` `surety_weekly_reports` | Fetches ascending `bond_date`; builder re-sorts — chronological order inherited. |
| Reports UI | `dashboard/sl-reports.js` | `All Time` preset, range clamp, `sortBondsOldestFirst`, `resortForOutput` on ⌘S/⌘P/`beforeprint`, API warning toasts. |
| Dashboard HTML | `dashboard/index.html` | All Time button, `min="2012-01-01"`, cache-bust `sl-reports.js?v=6`. |
| Unit tests | `tests/test_bond_report_sorting.py` | Sort mixed dates / undated last / never raises; date parse invalid→warning, pre-2012 clamp, swap; automation + surety-liability API coverage. **20 tests green** with liability wiring suite. |

## 🔧 Remaining (ops / prod — not code)

1. **Hetzner VPS recovery** — Deploy red since run #458; root cause is the VPS (SSH banner drop / empty HTTP), not this code. Reboot from console.hetzner.cloud, check `df -h`, `free -m`, `fail2ban-client status sshd`, re-run workflow. Confirm host in deploy secrets vs older box `5.161.126.32`.
2. **Re-run GitHub Actions on main** after VPS healthy; green Deploy workflow for this commit + prior infra-failed commits.
3. **Prod smoke test** once deployed:
   - Reports → Surety Liability → oldest→newest register
   - ⌘S → toast + printable sorted copy
   - ⌘P / File▸Print → DOM re-sort before dialog
   - All Time → records back to 2012; pre-2012 custom range clamps with toast
   - `POST /api/automation/bond-report` with `{"start_date":"2015-01-01","end_date":"2016-12-31"}` → ascending XLSX; bad dates → `warnings` not 500

## Key contracts (do not break)

- `sort_bonds_chronologically(records) -> list[dict]`
- `parse_report_date_window(start, end) -> (start_dt|None, end_dt|None, warnings)`
- `mongo_bond_date_filter(start, end, field=...) -> (filter_dict, warnings)`
- `SLReports.resortForOutput()` and `SLReports.sortBondsOldestFirst(list, dateFields?)`
- `POST /api/automation/bond-report` body: `{ surety, include_discharges, store, start_date?, end_date? }`;
  response adds `sort_order`, `start_date`, `end_date`, `warnings`
- `REPORT_EPOCH = 2012-01-01` in Python **and** JS — change both or neither
- Warning / error payloads: dates, counts, surety code only — **never defendant PII**
