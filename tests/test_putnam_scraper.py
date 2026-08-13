"""Unit tests for the Putnam County, Tennessee public-roster parser."""
import unittest

from bs4 import BeautifulSoup

from scrapers.counties_tn.putnam import PutnamScraper


SAMPLE_CARD = """
<article class="inmate">
  <section>
    <h1>SAMPLE, PERSON Q</h1>
    <data class="data-left">Age:</data><data class="data-right">38</data>
    <data class="data-left">Class:</data><data class="data-right">*</data>
    <data class="data-left">Race/Sex:</data><data class="data-right">W/F</data>
    <data class="data-left">Intake Date:</data><data class="data-right">08/12/2026 09:30 AM</data>
    <data class="data-left">City:</data><data class="data-right">COOKEVILLE</data>
    <data class="data-left">Arrested By Department:</data><data class="data-right">PUTNAM COUNTY SHERIFFS OFFICE</data>
    <data class="data-left">Arrested By Officer:</data><data class="data-right">OFFICER SAMPLE</data>
    <data class="data-left">Release Date:</data><data class="data-right"></data>
  </section>
  <section>
    <table class="charges">
      <tr><th>Charge</th><th>Bond</th></tr>
      <tr><td>PUBLIC ROSTER TEST</td><td>1,500</td></tr>
      <tr><td>SECOND PUBLIC ROSTER TEST</td><td>$250.00</td></tr>
    </table>
  </section>
</article>
"""


class TestPutnamScraper(unittest.TestCase):
    def setUp(self):
        self.scraper = PutnamScraper.__new__(PutnamScraper)

    def _record(self, html=SAMPLE_CARD):
        card = BeautifulSoup(html, "html.parser").select_one("article.inmate")
        return self.scraper._card_to_record(card)

    def test_maps_public_roster_fields(self):
        record = self._record()
        self.assertEqual(record.County, "Putnam")
        self.assertEqual(record.State, "TN")
        self.assertEqual(record.Full_Name, "Sample, Person Q")
        self.assertEqual(record.First_Name, "Person")
        self.assertEqual(record.Middle_Name, "Q")
        self.assertEqual(record.Last_Name, "Sample")
        self.assertEqual(record.Booking_Date, "08/12/2026 09:30 AM")
        self.assertEqual(record.Age_At_Arrest, "38")
        self.assertEqual(record.Race, "W")
        self.assertEqual(record.Sex, "F")
        self.assertEqual(record.City, "COOKEVILLE")
        self.assertEqual(record.Agency, "PUTNAM COUNTY SHERIFFS OFFICE")
        self.assertEqual(record.Charges, "PUBLIC ROSTER TEST | SECOND PUBLIC ROSTER TEST")
        self.assertEqual(record.Bond_Amount, "1750.00")
        self.assertEqual(record.Status, "In Custody")
        self.assertEqual(record.Release_Date, "")
        self.assertEqual(record.Detail_URL, "https://isoms.putnamcountytnsheriff.gov:8001/Jail")

    def test_release_date_sets_released_status(self):
        html = SAMPLE_CARD.replace(
            '<data class="data-right"></data>',
            '<data class="data-right">08/12/2026 04:30 PM</data>',
            1,
        )
        record = self._record(html)
        self.assertEqual(record.Status, "Released")
        self.assertEqual(record.Release_Date, "08/12/2026 04:30 PM")

    def test_surrogate_key_is_stable_and_source_labeled(self):
        first = self._record()
        second = self._record()
        self.assertEqual(first.Booking_Number, second.Booking_Number)
        self.assertTrue(first.Booking_Number.startswith("PUTNAM-"))
        self.assertEqual(
            first.extra_data["booking_number_origin"],
            "deterministic_public_roster_surrogate",
        )

    def test_page_total_uses_public_pagination_links(self):
        html = """
        <a href="/Jail?hours=0&amp;pagenum=0">1</a>
        <a href="/Jail?hours=0&amp;pagenum=16">17</a>
        """
        soup = BeautifulSoup(html, "html.parser")
        self.assertEqual(PutnamScraper._page_total(soup), 17)


if __name__ == "__main__":
    unittest.main()
