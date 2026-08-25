"""SwipeSimple Transactions CSV parse + in-file dedup."""
from dashboard.routers.accounting import parse_swipesimple_csv


SAMPLE = """Transaction #,Date,Type,Amount,Result,Method,Brand,Last 4,Auth Code,Cardholder Name,Reference Number,Merchant Account,Taken By
10329975-7462,08/25/2026 9:57 AM,Sale,$400.00,Approved,Payment Link,Mastercard,9171,025536,Jeanette M Perkovich ,Bail Bond Payment - Shamrock Bail Bonds,SHAMROCK BAIL LLC,office@example.com
10827649-0247,08/24/2026 7:30 PM,Sale,$50.00,Approved,Payment Link,Mastercard,0605,183007,Crystal Norling,Bail Bond Payment,SHAMROCK BAIL LLC,office@example.com
10567631-4250,08/17/2026 4:32 PM,Sale,$750.00,Declined,Keyed,Visa,3924,,DAISY GRINDSCHS,,SHAMROCK BAIL LLC,office@example.com
10556913-8925,08/15/2026 9:53 AM,Refund,$350.00,Approved,Cash,,,,,,SHAMROCK BAIL LLC,office@example.com
10556913-8925,08/15/2026 9:52 AM,Sale,$350.00,Approved,Cash,,,,,,SHAMROCK BAIL LLC,office@example.com
10556913-8925,08/15/2026 9:52 AM,Sale,$350.00,Approved,Cash,,,,,,SHAMROCK BAIL LLC,office@example.com
"""


def test_parse_skips_declined_and_file_dupes_keeps_sale_and_refund():
    txns, errors, skipped = parse_swipesimple_csv(SAMPLE)
    assert not errors
    assert skipped >= 2  # declined + exact duplicate sale
    assert len(txns) == 4
    types = sorted(t["type"] for t in txns if t["reference_id"] == "10556913-8925")
    assert types == ["premium", "refund"]
    newest = txns[0]
    assert newest["reference_id"] == "10329975-7462"
    assert newest["amount"] == 400.0
    assert newest["method"] == "card"
    assert newest["channel"] == "Payment Link"
    assert newest["customer_name"] == "Jeanette M Perkovich"
    cash = next(t for t in txns if t["reference_id"] == "10556913-8925" and t["type"] == "premium")
    assert cash["method"] == "cash"


def test_parse_rejects_sales_summary():
    raw = "Total Net Card,$1000\nTotal Net Cash,$200\n"
    try:
        parse_swipesimple_csv(raw)
        assert False, "expected summary rejection"
    except ValueError as exc:
        assert "Transactions export" in str(exc)
