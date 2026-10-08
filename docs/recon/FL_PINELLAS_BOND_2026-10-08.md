# Pinellas (FL/103) bond honesty, 2026-10-08

## Live check (box, 08:30–08:42 EDT)

Plain headless Google Chrome (`playwright`, not the module's patchright) on `https://whosinjail.pinellassheriff.gov/`. No challenge. Booking-date search with "Include Charge Information", then the name-click Subject Charge Report modal. Read only.

- Bookings 2026-10-06 and 2026-10-07, first 25 rows each (50 modals). `Bond Assessed` cells: 49 positive dollar amounts and **33 real `$0.00`**. Per booking: 24 all positive, 14 all `$0.00`, 6 mixed positive and `$0.00`, and 6 modals that did not render in time. No blank or text cells were seen, but the parser must still handle them.
- Re-run with the new parser on 2026-10-07 (25 rows): 13 positive, 10 published zero, 2 unknown (modal not read), 23 with charges.

## Change

| Case | Before | After |
|---|---|---|
| Modal not rendered (roster-only row) | `"0"` | booking **skipped** for this run (no blank `$set` over stored values); all modals failing raises |
| Modal without any `Bond Assessed` | `"0"` | `""` |
| Any charge's `Bond Assessed` blank or non-numeric (for example `NO BOND`, `HOLD`) | that charge counted as $0 | total `""` (a blank can be a hold) |
| Every charge publishes an amount | sum | sum (a published `$0.00` counts; all zero gives `"0"`) |

Old rows: Pinellas does publish real `$0.00`, so stored `"0"` values are **not** reclassified as unknown on hydrate. They cannot be told apart from the old unread-modal `"0"`, and the next scrape of a booking rewrites it. Transport (patchright) is unchanged in this PR. The stealth-browser question for this module is flagged separately for the owner.

Every date search failing now raises. Before, each date's exception was logged and the run returned `[]` as a success.
