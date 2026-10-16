"""Lancaster/York SC: honest plain_http, published-only status/bond/charges."""
import unittest
from unittest import mock

import requests

from scrapers.counties_sc import lancaster as lan
from scrapers.counties_sc import york as yk
from scrapers.plain_http import HONEST_USER_AGENT, EgressBlocked

DETAIL = """<html><body>
<div class="Booking"><h3><label>Booking</label><span>2026-00001234</span></h3></div>
<label>Name</label><span>DOE, JOHN</span>
<label>Booking Date</label><span>10/1/2026 5:59 AM</span>
%s
<table><tr><th>Number</th><th>Charge Description</th><th>Bond</th></tr>%s</table>
</body></html>"""

LISTING = """<html><body><form>
<input type="checkbox" name="InCustody" value="true" %s/>
<table><tr><td><a href="/NewWorld.InmateInquiry/SC0290000/Inmate/Detail/-11">DOE, JOHN</a></td></tr></table>
</form></body></html>"""


class _Resp:
    def __init__(self, text, code=200):
        self.text, self.status_code = text, code
        self.headers = {"content-type": "text/html"}
        self.url = lan.PORTAL_URL


class LancasterTests(unittest.TestCase):
    def setUp(self):
        self.s = lan.LancasterScraper()

    def _rec(self, extra="", rows="", status=("", "")):
        return self.s.parse_detail_to_record(DETAIL % (extra, rows), fallback_name="x", detail_url="u", status=status)

    def test_no_hardcoded_status_bond_charges(self):
        r = self._rec()
        self.assertEqual((r.Status, r.Bond_Amount, r.Charges), ("", "", ""))
        self.assertNotIn("status_source", r.extra_data)

    def test_status_from_echoed_filter(self):
        r = self._rec(status=("In Custody", "published_in_custody_filter"))
        self.assertEqual(r.Status, "In Custody")
        self.assertEqual(r.extra_data["status_source"], "published_in_custody_filter")

    def test_published_zero_bond_kept(self):
        r = self._rec(extra="<label>Total Bond Amount</label><span>$0.00</span>")
        self.assertEqual(r.Bond_Amount, "0")

    def test_partial_charge_bonds_not_summed(self):
        rows = ('<tr><td>1</td><td class="ChargeDescription">A</td><td class="ChargeBond">$500</td></tr>'
                '<tr><td>2</td><td class="ChargeDescription">B</td><td class="ChargeBond"></td></tr>')
        self.assertEqual(self._rec(rows=rows).Bond_Amount, "")

    def test_reference_ids_never_dollars(self):
        rows = '<tr><td>1</td><td class="ChargeDescription">A</td><td class="ChargeBond">2026-00009999</td></tr>'
        r = self._rec(rows=rows)
        self.assertEqual((r.Charges, r.Bond_Amount), ("A", ""))

    def test_scrape_status_needs_echoed_filter(self):
        for checked, want in (("checked", "In Custody"), ("", "")):
            pages = iter([_Resp(LISTING % checked), _Resp(DETAIL % ("", ""))])
            with mock.patch.object(lan._HonestSession, "request", side_effect=lambda *a, **k: next(pages)), \
                    mock.patch.object(lan.time, "sleep"):
                recs = self.s.scrape()
            self.assertEqual([r.Status for r in recs], [want])

    def test_honest_ua_and_blocks_raise(self):
        self.assertEqual(lan.HEADERS["User-Agent"], HONEST_USER_AGENT)
        with mock.patch.object(lan._HonestSession, "request", side_effect=EgressBlocked("egress_block: x")):
            with self.assertRaises(EgressBlocked):
                self.s.scrape()


def _item_html(release):
    from tests.test_york_sc_scraper import _item
    return _item(release=release)


def _york_row(release, bond="$1,000.00"):
    return f"""<table id="dgJackets"><tr class="dgItem"><td>
<table><tr><td>Name:</td><td>DOE, JOHN</td></tr></table>
<table><tr><th>Booking Number</th><th>Booking Date</th><th>Release Date</th><th>Total Bond</th></tr>
<tr><td>2026-1</td><td>10/01/2026 10:00</td><td>{release}</td><td>{bond}</td></tr></table>
</td></tr></table>"""


class YorkTests(unittest.TestCase):
    def test_bond_blank_is_empty_zero_kept(self):
        self.assertEqual(yk._bond(""), "")
        self.assertEqual(yk._bond("$0.00"), "0")
        self.assertEqual(yk._bond("$1,250.00"), "1250")

    def test_honest_ua(self):
        self.assertEqual(yk.USER_AGENT, HONEST_USER_AGENT)

    def test_fetch_uses_plain_http_and_raises_blocks(self):
        sess = requests.Session()
        with mock.patch.object(sess, "request", return_value=_Resp("", 403)):
            with self.assertRaises(EgressBlocked):
                yk.YorkScraper()._fetch(sess)

    def test_status_published_only(self):
        from tests.test_york_sc_scraper import _page
        for release, want, src in (("*In Jail", "In Custody", "published_release_cell_in_jail"),
                                   ("10/05/2026 08:00", "Released", "published_release_date"),
                                   ("", "", None)):
            recs, _ = yk.parse_page(_page(_item_html(release)))
            self.assertEqual(recs[0].Status, want)
            self.assertEqual(recs[0].extra_data.get("status_source"), src)

    def test_status_source_constants(self):
        self.assertEqual(yk.STATUS_SOURCE_IN_JAIL, "published_release_cell_in_jail")
        self.assertEqual(yk.STATUS_SOURCE_RELEASE_DATE, "published_release_date")


if __name__ == "__main__":
    unittest.main()
