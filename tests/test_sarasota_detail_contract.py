"""Synthetic Sarasota detail-page fixtures. No network and no real personal data."""
from datetime import date

from scrapers.counties.sarasota import SarasotaCountyScraper
from scrapers.counties.sarasota_contract import (
    MAX_DETAILS_PER_RUN,
    REQUEST_PAUSE_S,
    SarasotaContractError,
    assess_detail,
    listing_entries,
    parse_detail_page,
    split_listing_label,
)

COLUMNS = (
    "Booking Number",
    "Offense Description",
    "Counts",
    "Arraign Date",
    "Bond Amount",
    "Bond Type",
    "Intake Date/Time",
    "Court Case Number",
    "Release Date/Time",
    "Hold",
)
PIN = "1000000001"
LABEL = "TESTER, ALEX QUINN - 02/02/2000"
INTAKE = "2024-12-31 23:59:00.100"


def _row(
    booking,
    offense,
    *,
    counts="1",
    arraign="01/15/2025",
    bond="$15000.00",
    bond_type="",
    intake=INTAKE,
    case="2024 CF 000111 NC",
    release="",
    hold="N",
):
    return [booking, offense, counts, arraign, bond, bond_type, intake, case, release, hold]


def _page(rows, *, pin=PIN, stacked=None):
    def table(body_rows, table_id=""):
        attr = f' id="{table_id}"' if table_id else ' class="stacked"'
        head = "".join(f"<th>{name}</th>" for name in COLUMNS)
        body = "".join(
            "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in body_rows
        )
        return f"<table{attr}><tr>{head}</tr>{body}</table>"

    stacked_html = table(stacked) if stacked else ""
    return (
        "<table id='personal'>"
        f"<tr><th>PIN</th><td>{pin}</td></tr>"
        "<tr><th>Date of Birth</th><td>02/02/2000</td></tr>"
        "<tr><th>Race</th><td>W</td></tr>"
        "<tr><th>Sex</th><td>M</td></tr>"
        "<tr><th>Location</th><td>POD 1</td></tr>"
        "</table>"
        + table(rows, "data-table")
        + stacked_html
    )


def test_listing_href_spaces_are_stripped_and_the_label_date_is_dropped():
    html = '<a href="viewInmate.php?id=1000000001     ">TESTER, ALEX QUINN - 02/02/2000</a>'
    pin, label = listing_entries(html)[0]
    assert pin == "1000000001"
    name = split_listing_label(label)
    assert name["last"] == "TESTER"
    assert name["first"] == "ALEX"
    assert name["middle"] == "QUINN"
    assert "02/02/2000" not in name["full_name"]


def test_detail_rows_are_keyed_on_the_court_case_number():
    """Two bookings that share one case number are one record. The case is the key."""
    shared = "2024 CF 000111 NC"
    page = _page(
        [
            _row("202500001111", "BATTERY", bond="$15000.00", case=f"  {shared}  ", hold="N"),
            _row("202400002222", "THEFT", bond="No Bond Available", case=shared, hold=""),
            _row("202500001111", "RESIST", bond="$0.00", case="2024 MM 000222 NC", hold="Y"),
        ],
        stacked=[_row("202399999999", "STACKED ONLY", case="2024 CF 999999 NC")],
    )
    records, stats = parse_detail_page(page, pin=PIN, label=LABEL, on_roster=True)
    by_case = {record.Case_Number: record for record in records}
    assert set(by_case) == {shared, "2024 MM 000222 NC"}
    assert "2024 CF 999999 NC" not in by_case
    assert stats["skipped_rows"] == 0
    shared_record = by_case[shared]
    assert shared_record.Booking_Number == ""
    assert shared_record.extra_data["record_key"] == shared
    assert shared_record.extra_data["booking_numbers"] == ["202500001111", "202400002222"]
    assert shared_record.Person_ID == PIN
    assert shared_record.Booking_Number != PIN
    assert PIN not in shared_record.extra_data["booking_numbers"]
    assert shared_record.Last_Name == "TESTER" and shared_record.First_Name == "ALEX"
    assert shared_record.DOB == ""
    assert "02/02/2000" not in shared_record.Full_Name
    assert shared_record.Status == "In Custody"
    assert shared_record.Race == "W" and shared_record.Sex == "M"
    other = by_case["2024 MM 000222 NC"]
    assert other.Booking_Number == ""
    assert other.extra_data["booking_numbers"] == ["202500001111"]
    assert other.Bond_Amount == "0"


def test_row_without_a_case_number_is_skipped_and_logged(caplog):
    """A blank case number writes nothing. The log has county, reason, and count only."""
    page = _page([
        _row("202500001111", "BATTERY", case="", bond="$15000.00"),
        _row("202400002222", "RESIST", case="   ", bond="$500.00"),
    ])
    with caplog.at_level("INFO", logger="scrapers.counties.sarasota_contract"):
        records, stats = parse_detail_page(page, pin=PIN, label=LABEL, on_roster=True)
    assert records == []
    assert stats["skipped_rows"] == 2
    assert any(
        "Sarasota FL skipped 2 charge rows: no court case number" in message
        for message in caplog.messages
    )
    logged = " ".join(caplog.messages)
    assert PIN not in logged
    assert "202500001111" not in logged
    assert "202400002222" not in logged
    assert "TESTER" not in logged
    assert "02/02/2000" not in logged


def test_two_charges_on_one_case_stay_distinct():
    """Two charge rows on one case stay two charge entries on one record."""
    case = "2024 CF 000111 NC"
    page = _page([
        _row("202500001111", "BATTERY", case=case, bond="$15000.00"),
        _row("202500001111", "RESIST", case=case, bond="$500.00"),
    ])
    records, stats = parse_detail_page(page, pin=PIN, label=LABEL, on_roster=True)
    assert stats["skipped_rows"] == 0
    assert len(records) == 1
    record = records[0]
    assert record.Case_Number == case
    assert record.Booking_Number == ""
    charges = record.extra_data["charge_details"]
    assert len(charges) == 2
    assert [row["description"] for row in charges] == ["BATTERY", "RESIST"]
    assert charges[0] is not charges[1]
    assert "BATTERY" not in charges[1]["description"]
    assert "RESIST" not in charges[0]["description"]
    assert record.extra_data["booking_numbers"] == ["202500001111"]


def test_booking_number_without_a_case_number_writes_nothing(caplog):
    """A booking number and a PIN, with no case number, are not keys."""
    page = _page([
        _row("202500001111", "BATTERY", case="", bond="$15000.00"),
        _row(PIN, "PIN IS NOT A BOOKING", case=""),
    ])
    with caplog.at_level("INFO", logger="scrapers.counties.sarasota_contract"):
        records, stats = parse_detail_page(page, pin=PIN, label=LABEL, on_roster=True)
    assert records == []
    assert stats["skipped_rows"] == 2
    assert all(record.Booking_Number == "" for record in records)
    logged = " ".join(caplog.messages)
    assert "202500001111" not in logged
    assert PIN not in logged


def test_release_time_filled_is_not_in_custody():
    page = _page([
        _row("202500001111", "BATTERY", release="2025-01-02 01:02:03.004"),
    ])
    records, _stats = parse_detail_page(page, pin=PIN, label=LABEL, on_roster=True)
    assert records[0].Status == ""
    assert records[0].Release_Date == "2025-01-02 01:02:03.004"
    assert records[0].extra_data["status_source"] == "release_date_time_filled"
    assert records[0].Status != "In Custody"


def test_blank_release_off_the_roster_is_not_in_custody():
    page = _page([_row("202500001111", "BATTERY")])
    records, _stats = parse_detail_page(page, pin=PIN, label=LABEL, on_roster=False)
    assert records[0].Status == ""
    assert "status_source" not in records[0].extra_data


def test_detail_gate_opens_on_a_case_number_and_stays_shut_without_one():
    good = _page([_row("202500001111", "BATTERY", case="2024 CF 000111 NC")])
    verdict = assess_detail(good, pin=PIN, on_roster=True)
    assert verdict.reopen_ok is True
    assert verdict.bookings == 1

    no_case = _page([_row("202500001111", "NO CASE", case="")])
    closed = assess_detail(no_case, pin=PIN)
    assert closed.reopen_ok is False
    assert "Court Case Number" in " ".join(closed.reasons)


def test_two_bookings_on_one_case_keep_both_intakes():
    case = "2024 CF 000111 NC"
    page = _page([
        _row("202500001111", "BATTERY", case=case, intake="2024-12-31 23:59:00.100"),
        _row("202400002222", "RESIST", case=case, intake="2025-01-01 00:00:00.000"),
    ])
    records, _stats = parse_detail_page(page, pin=PIN, label=LABEL, on_roster=True)
    assert len(records) == 1
    record = records[0]
    assert record.Case_Number == case
    assert record.Booking_Number == ""
    assert record.extra_data["booking_numbers"] == ["202500001111", "202400002222"]
    assert record.extra_data["intakes"] == [
        "2024-12-31 23:59:00.100",
        "2025-01-01 00:00:00.000",
    ]
    assert record.Booking_Date == ""
    assert record.Booking_Time == ""


def test_detail_walk_caps_and_pauses_like_hernando():
    assert MAX_DETAILS_PER_RUN == 200
    assert REQUEST_PAUSE_S == 0.4
    links = "".join(
        f'<a href="viewInmate.php?id=100000000{i}">TESTER, CASE {i} - 02/02/2000</a>'
        for i in (1, 2, 3)
    )
    fetched = []
    pauses = []

    def fetch(pin):
        fetched.append(pin)
        if pin == "1000000002":
            raise RuntimeError("detail unavailable")
        return _page(
            [_row(f"20240000000{pin[-1]}", "BATTERY", case=f"2024 CF 00000{pin[-1]} NC")],
            pin=pin,
        )

    def sleep(seconds):
        pauses.append(seconds)

    scraper = SarasotaCountyScraper()
    records, summary = scraper.build_records(
        links, fetch, sleep=sleep, limit=2, pause_s=0.4, offset=0,
    )
    assert fetched == ["1000000001", "1000000002"]
    assert pauses == [0.4, 0.4]
    assert summary["listed"] == 3
    assert summary["fetched"] == 2
    assert summary["skipped_over_cap"] == 1
    assert summary["fetch_failures"] == 1
    assert [record.Case_Number for record in records] == ["2024 CF 000001 NC"]
    assert records[0].Booking_Number == ""
    assert records[0].extra_data["booking_numbers"] == ["202400000001"]
    assert scraper.DETAIL_CAP == 200
    assert scraper.DETAIL_PAUSE_S == 0.4
    assert scraper.SOURCE_CONTRACT_VALIDATED is False
    assert scraper.scrape() == []
    assert scraper.ACCESS_PLAN.county == "Sarasota"


def _pins(n):
    return "".join(
        f'<a href="viewInmate.php?id=100000000{i}">TESTER, CASE {i} - 02/02/2000</a>'
        for i in range(1, n + 1)
    )


def test_detail_window_rotates_by_utc_day():
    links = _pins(5)
    seen = []

    def fetch(pin):
        seen.append(pin)
        return _page([_row(f"20240000000{pin[-1]}", "BATTERY")], pin=pin)

    def run(day):
        seen.clear()
        walk_records, summary = SarasotaCountyScraper().build_records(
            links, fetch, sleep=lambda _s: None, limit=2, today=day,
        )
        assert walk_records
        return list(seen), summary["offset"]

    first, offset_a = run(date(2026, 1, 1))
    second, offset_b = run(date(2026, 1, 2))
    third, _offset_c = run(date(2026, 1, 3))
    assert offset_a != offset_b
    assert set(first + second + third) == {f"100000000{i}" for i in range(1, 6)}


def test_missing_charge_table_refuses_a_partial_roster():
    links = _pins(2)

    def fetch(pin):
        if pin.endswith("1"):
            return _page([_row("202400000001", "BATTERY")], pin=pin)
        return "<html><title>Just a moment</title></html>"

    try:
        SarasotaCountyScraper().build_records(
            links, fetch, sleep=lambda _s: None, limit=2, offset=0,
        )
        raised = False
    except SarasotaContractError as exc:
        raised = True
        message = str(exc)
    assert raised
    assert "refusing a partial roster" in message
    assert "missing table" in message
