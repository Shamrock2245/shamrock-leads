"""Mecklenburg NC: honest plain_http, typed errors, published-only status/bond/charges."""
import unittest
from unittest import mock

import requests

from scrapers.counties_nc import mecklenburg as mk
from scrapers.plain_http import HONEST_USER_AGENT, EgressBlocked
from scrapers.scraper_resilience import ParseDriftError

LISTING = {"Name": "X", "JID": "26-125813", "PID": "9", "DetailsUrl": "/Inmate/Details?pid=9"}


class _Resp:
    def __init__(self, payload=None, code=200, text=None):
        self.status_code, self._p = code, payload
        self.headers = {"content-type": "application/json"}
        self.text = text if text is not None else ""

    def json(self):
        if self._p is None:
            raise ValueError("not json")
        return self._p

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


def _run(responses):
    it = iter(responses)
    with mock.patch.object(requests.Session, "request", side_effect=lambda *a, **k: next(it)), \
            mock.patch.object(mk.time, "sleep"):
        return mk.MecklenburgScraper().scrape()


class MecklenburgHonestTests(unittest.TestCase):
    def setUp(self):
        self.s = mk.MecklenburgScraper()

    def test_honest_ua(self):
        self.assertEqual(mk.HEADERS["User-Agent"], HONEST_USER_AGENT)

    def test_no_hardcoded_status_anywhere(self):
        self.assertEqual(self.s.parse_search_payload([LISTING])[0].Status, "")
        rec = self.s.record_from_detail(LISTING, {}, [])
        self.assertEqual((rec.Status, rec.Charges, rec.Bond_Amount), ("", "", ""))

    def test_published_zero_bail_kept(self):
        self.assertEqual(self.s._parse_charges([{"Description": "A", "ActualBailAmount": 0}]), ("A", "0"))
        self.assertEqual(self.s._parse_charges([{"Description": "A", "ActualBailAmount": "$1,500.00"}]), ("A", "1500"))

    def test_landing_block_raises(self):
        with self.assertRaises(EgressBlocked):
            _run([_Resp(code=403, text="")])

    def test_landing_http_error_raises_not_empty(self):
        with self.assertRaises(requests.HTTPError):
            _run([_Resp(code=500)])

    def test_roster_non_json_raises_parse_drift(self):
        with self.assertRaises(ParseDriftError):
            _run([_Resp({}), _Resp(None)])

    def test_roster_block_raises(self):
        with self.assertRaises(EgressBlocked):
            _run([_Resp({}), _Resp(code=429)])

    def test_detail_failure_keeps_blank_fields(self):
        recs = _run([_Resp({}), _Resp([dict(LISTING, TotalRows=1)]), _Resp(code=500)])
        self.assertEqual([(r.Booking_Number, r.Status, r.Charges, r.Bond_Amount) for r in recs],
                         [("26-125813", "", "", "")])

    def test_detail_block_raises(self):
        with self.assertRaises(EgressBlocked):
            _run([_Resp({}), _Resp([dict(LISTING, TotalRows=1)]), _Resp(code=403)])


if __name__ == "__main__":
    unittest.main()
