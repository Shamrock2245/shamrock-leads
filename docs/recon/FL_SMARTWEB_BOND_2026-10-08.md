# FL SmartWEB JAIL View: bond semantics (2026-10-08)

Module: `scrapers/fl_smartweb.py`. Used by Escambia, Santa Rosa, Putnam, Sumter, Bradford, Dixie, Taylor, Gilchrist, Hamilton and Madison.

## Source
- Each card has a `JailViewCharges` grid. Column 7 is the charge's bond cell. Live values seen: `$N`, `$N SURETY`, `$0.00`, `NO BOND`, and blank.
- The card header has `Bond Amount: $N`. It reads `$0.00` on cards with no charges entered and on cards whose charge grid has positive bonds (fixture in `test_fl_smartweb_five.py`), so a card-level $0.00 is a default, not a value.

## Live probe (box, 2026-10-08 ~08:50 EDT)
Charge cells over a 7-day window: Escambia had 68 `$N`, 55 `NO BOND` and 7 `$0.00`. Putnam, Santa Rosa and Sumter each had 4 `$0.00`. Real $0 exists, so these counties are not no-bond rosters.

## Live read check with the fix (3-day window, ~09:05 EDT)
| County | Rows | With charges | Positive | $0 (charge grid) | Unknown |
|---|---|---|---|---|---|
| Escambia | 78 | 76 | 32 | 5 | 41 |
| Putnam | 23 | 23 | 8 | 4 | 11 |
| Santa Rosa | 29 | 22 | 8 | 4 | 17 |
| Sumter | 22 | 20 | 6 | 2 | 14 |
| Dixie | 6 | 5 | 0 | 4 | 2 |
| Taylor | — | — | — | — | — (connect timeout from the box) |

Before the fix, every Unknown row above was written as `"0"` or as a partial sum.
