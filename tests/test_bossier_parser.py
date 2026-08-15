import json
import unittest

from scrapers.counties_la.bossier import BossierParishScraper


def public_card(**overrides):
    card = {
        "_id": {"$oid": "source-card-id"},
        "firstName": "CASEY RENE",
        "lastName": "EXAMPLE",
        "inmateID": "258150",
        "content": "<b>Record Details:</b><br/>Inmate ID: 258150<br/><b>Custody Details:</b><br/>Booked Date: 07/06/2026 07:29:00 CDT",
        "images": [{"source": "public-listing-image"}],
    }
    card.update(overrides)
    return card


def public_page(*cards):
    flight = "\n".join(json.dumps(card) for card in cards)
    return f"<script>self.__next_f.push([1, {json.dumps(flight)}])</script>"


class TestBossierParishScraper(unittest.TestCase):
    def setUp(self):
        self.scraper = BossierParishScraper()

    def test_maps_required_public_flight_card_fields(self):
        records = self.scraper._parse_page(public_page(public_card()))

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.County, "Bossier")
        self.assertEqual(record.State, "LA")
        self.assertEqual(record.Booking_Number, "258150")
        self.assertEqual(record.Person_ID, "258150")
        self.assertEqual(record.Full_Name, "CASEY RENE EXAMPLE")
        self.assertEqual(record.First_Name, "Casey")
        self.assertEqual(record.Middle_Name, "Rene")
        self.assertEqual(record.Last_Name, "Example")
        self.assertEqual(record.Booking_Date, "2026-07-06")
        self.assertEqual(record.Booking_Time, "07:29:00")
        self.assertEqual(record.extra_data["booking_key_origin"], "source-issued public Inmate ID")
        self.assertEqual(record.Detail_URL, self.scraper.PORTAL_URL)
        self.assertEqual(record.Mugshot_URL, "")

    def test_missing_required_source_fields_fail_closed_per_card(self):
        self.assertEqual(
            self.scraper._parse_page(public_page(public_card(inmateID=""))),
            [],
        )
        self.assertEqual(
            self.scraper._parse_page(
                public_page(public_card(content="Inmate ID: 258150<br/>Booked Date:"))
            ),
            [],
        )
        self.assertEqual(
            self.scraper._parse_page(
                public_page(public_card(content="Inmate ID: 258150<br/>Booked Date: invalid"))
            ),
            [],
        )

    def test_parser_does_not_follow_profile_or_image_paths(self):
        records = self.scraper._parse_page(
            public_page(
                public_card(
                    content="<a href='/inmateLookup/source-profile'>Profile</a>Inmate ID: 258150<br/>Booked Date: 07/06/2026 07:29:00 CDT",
                    images=[{"source": "public-listing-image"}],
                )
            )
        )
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].Detail_URL, self.scraper.PORTAL_URL)
        self.assertEqual(records[0].Mugshot_URL, "")

    def test_parser_ignores_non_card_or_non_source_payloads(self):
        self.assertEqual(self.scraper._parse_page("<html></html>"), [])
        self.assertEqual(
            self.scraper._parse_page(public_page({"inmateID": "258150"})),
            [],
        )

    def test_booked_date_normalization_requires_source_date_and_time(self):
        self.assertEqual(
            self.scraper._parse_booked_date("07/06/2026 07:29:00 CDT"),
            ("2026-07-06", "07:29:00"),
        )
        self.assertEqual(self.scraper._parse_booked_date("07/06/2026"), ("", ""))


if __name__ == "__main__":
    unittest.main()
