"""fl_smartweb: Status only from the card's own Status token; unknown is ""; downstream copes with ""."""
import pytest

from core.models import ArrestRecord
from core.pending_bond_recheck import is_in_custody, is_released
from scoring.feature_engineering import _is_in_custody as fe_in_custody, _is_released as fe_released
from scoring.lead_scorer import LeadScorer
from scrapers import fl_smartweb


def _card(status_line):
    return f"""<table>
<tr class="InmateRecordRow">
  <td><img src='ViewImage.aspx?bookno=ECC26JBN000001'></td>
  <td><table><thead><tr><td class="SearchHeader">ROE, JOHN A &nbsp; (B/ MALE )</td></tr></thead>
  <tbody>
    {status_line}
    <tr><td>Visitation Status: Denied</td></tr>
    <tr><td>Booking No: ECC26JBN000001</td></tr>
    <tr><td>Booking Date: 10/06/2026 08:21 AM</td></tr>
  </tbody></table></td>
</tr></table>"""


def _status(line):
    recs = fl_smartweb._parse_html(_card(line), set(), county="Escambia", facility="X",
                                   detail_url="https://example.test/")
    assert len(recs) == 1
    return recs[0].Status


@pytest.mark.parametrize("line,want", [
    ("<tr><td>Status: In Jail</td></tr>", "In Custody"),
    ("<tr><td>Status: In Custody</td></tr>", "In Custody"),
    ("<tr><td>Status: Out of Jail</td></tr>", "Released"),
    ("<tr><td>Status: Released</td></tr>", "Released"),
    ("<tr><td>Status: IN JAIL</td></tr>", "In Custody"),
    ("", ""),                                         # absent: was "In Custody"
    ("<tr><td>Status: Pending</td></tr>", ""),        # unrecognised: was "In Custody"
    ("<tr><td>Status: Transferred</td></tr>", ""),
])
def test_status_only_from_source(line, want):
    assert _status(line) == want


@pytest.mark.parametrize("raw,want", [
    ("In Jail", "In Custody"), ("in  custody", "In Custody"), ("Out of Jail", "Released"),
    ("OUT OF JAIL", "Released"), ("Released", "Released"),
    ("", ""), (None, ""), ("Jail", ""), ("Out of Jail Transfer", ""), ("Denied", ""),
])
def test_status_from_source_is_whole_token(raw, want):
    assert fl_smartweb.status_from_source(raw) == want


def test_no_substring_default_left():
    import inspect
    src = inspect.getsource(fl_smartweb._parse_html)
    assert 'status = "In Custody"' not in src


# ── downstream copes with an empty status ────────────────────────────────────

def test_recheck_predicates_treat_blank_as_unknown():
    assert is_in_custody("") is False and is_released("") is False
    assert is_in_custody(None) is False


def test_scoring_blank_status_is_neutral():
    assert LeadScorer()._score_status("") == (0, "")
    assert fe_in_custody("") is False and fe_released("") is False


def test_model_round_trip_and_sheet_row_with_blank_status():
    rec = ArrestRecord(County="Escambia", State="FL", Booking_Number="ECC26JBN000001", Status="")
    doc = rec.to_mongo_doc()
    assert doc["status"] == ""
    assert ArrestRecord.from_mongo_doc(doc).Status == ""
    row = rec.to_sheet_row()
    assert isinstance(row, list) and "In Custody" not in row
