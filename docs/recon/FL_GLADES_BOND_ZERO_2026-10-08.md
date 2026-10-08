# FL Glades — card-level vs charge-level $0.00 (2026-10-08)

**Scope:** Glades County (FL) SmartWEB JAIL View, bond fields only (PR #139).
**Method:** one live read of the current roster from the agent box over plain HTTPS (no proxy, no stealth, no CAPTCHA). Counts only; no names, booking numbers or other personal data recorded.
**Decision (CoS, 2026-10-08):** a card-level `Bond Amount: $0.00` stays unknown (`""`); a charge-level printed `$0.00` is a real `0`.

## Counts (live 2026-10-08)

| Metric | Value |
|---|---:|
| Current cards | 20 |
| Card-level `Bond Amount` = NO BOND | 14 (all 14 have NO BOND charges) |
| Card-level `Bond Amount` = $0.00 | 6 (all 6 have positive charge bonds, $15k–$245k) |
| Card-level positive figure | 0 (never seen) |
| Charge-level printed $0.00 | 1 (beside a positive charge; no bond-type cell) |

So the card-level field is never the booking total: it is NO BOND when the charges are NO BOND, and the JAIL View default `$0.00` when the charges carry positive bonds. A card-level `$0.00` therefore stays `""`, like SmartWEB (#146) and unlike Okaloosa's roster `0`, which is also unknown. The only charge-level `$0.00` sits next to a positive charge, and there is no evidence that a printed charge `$0.00` means unpublished, so it is kept as `0`.

## Parser rule (`scrapers/counties/glades.py`, #139)

- Booking total = sum of this card's BOND-column charge cells, only when every cell is a dollar amount (a printed `$0.00` counts).
- Any NO BOND hold or non-money cell → `""`.
- No charge cells → the card-level `Bond Amount` is used only if positive, else `""`.

Re-parse of the same live page with the #139 parser: 14 empty, 6 positive.
