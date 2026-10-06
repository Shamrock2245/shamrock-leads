"""
Unit and integration tests for POST /api/staff/chain/ensure-match-bondcase.
Verifies:
- God-Admin / staff auth gating (401 when unauthorized).
- Fail-closed validation (missing arrest, active bond, POA, or on-file contact).
- Canonical chain creation (Defendant → Indemnitor → Match → BondCase).
- Idempotency on repeated calls.
- Packet linkage and pending_staff_match clearing.
"""
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.routers.staff_chain import staff_chain_bp

TEST_PIN = "224545"


@pytest.fixture
def app():
    test_app = FastAPI()
    test_app.include_router(staff_chain_bp)
    return test_app


@pytest.fixture
def client(app):
    return TestClient(app)


def test_ensure_match_bondcase_requires_auth(client):
    """Refuses unauthorized requests without staff PIN or session."""
    with patch("dashboard.routers.staff_chain.DASHBOARD_PIN", TEST_PIN), \
         patch.dict("os.environ", {"DASHBOARD_PIN": TEST_PIN}):
        res = client.post("/api/staff/chain/ensure-match-bondcase", json={"booking_number": "1099999"})
        assert res.status_code == 401
        data = res.json()
        assert data["success"] is False
        assert data["error"] == "unauthorized"


def test_ensure_match_bondcase_missing_booking(client):
    """Requires booking_number."""
    with patch("dashboard.routers.staff_chain.DASHBOARD_PIN", TEST_PIN), \
         patch.dict("os.environ", {"DASHBOARD_PIN": TEST_PIN}):
        res = client.post(
            "/api/staff/chain/ensure-match-bondcase",
            headers={"X-Admin-Token": TEST_PIN},
            json={},
        )
        assert res.status_code == 400
        assert res.json()["error"] == "booking_number_required"


@patch("dashboard.services.staff_chain_service.get_collection")
def test_ensure_match_bondcase_missing_arrest(mock_get_col, client):
    """Fails closed (404) if arrest record is missing."""
    arrests_col = AsyncMock()
    arrests_col.find_one = AsyncMock(return_value=None)
    mock_get_col.return_value = arrests_col

    with patch("dashboard.routers.staff_chain.DASHBOARD_PIN", TEST_PIN), \
         patch.dict("os.environ", {"DASHBOARD_PIN": TEST_PIN}):
        res = client.post(
            "/api/staff/chain/ensure-match-bondcase",
            headers={"X-Admin-Token": TEST_PIN},
            json={"booking_number": "1099999"},
        )
        assert res.status_code == 404
        assert res.json()["error"] == "arrest_missing"


@patch("dashboard.services.staff_chain_service.get_collection")
def test_ensure_match_bondcase_missing_active_bond(mock_get_col, client):
    """Fails closed (404) if active_bond record is missing."""
    def _col_side_effect(name):
        col = AsyncMock()
        if name == "arrests":
            col.find_one = AsyncMock(return_value={"booking_number": "1099999", "first_name": "JOHN"})
        elif name == "active_bonds":
            col.find_one = AsyncMock(return_value=None)
        return col

    mock_get_col.side_effect = _col_side_effect

    with patch("dashboard.routers.staff_chain.DASHBOARD_PIN", TEST_PIN), \
         patch.dict("os.environ", {"DASHBOARD_PIN": TEST_PIN}):
        res = client.post(
            "/api/staff/chain/ensure-match-bondcase",
            headers={"X-Admin-Token": TEST_PIN},
            json={"booking_number": "1099999"},
        )
        assert res.status_code == 404
        assert res.json()["error"] == "active_bond_missing"


@patch("dashboard.services.staff_chain_service.get_collection")
def test_ensure_match_bondcase_refuses_missing_poa(mock_get_col, client):
    """Refuses when no POA provided or assigned."""
    def _col_side_effect(name):
        col = AsyncMock()
        if name == "arrests":
            col.find_one = AsyncMock(return_value={"booking_number": "1099999", "first_name": "JOHN", "last_name": "DOE"})
        elif name == "active_bonds":
            col.find_one = AsyncMock(return_value={
                "booking_number": "1099999",
                "case_number": "26CF001",
                "surety_id": "osi",
                "bond_amount": 5000,
                "premium": 500,
                "indemnitor_name": "Jane Doe",
                "indemnitor_email": "jane@example.com",
            })
        return col

    mock_get_col.side_effect = _col_side_effect

    with patch("dashboard.routers.staff_chain.DASHBOARD_PIN", TEST_PIN), \
         patch.dict("os.environ", {"DASHBOARD_PIN": TEST_PIN}):
        res = client.post(
            "/api/staff/chain/ensure-match-bondcase",
            headers={"X-Admin-Token": TEST_PIN},
            json={"booking_number": "1099999", "surety_id": "osi", "case_number": "26CF001"},
        )
        assert res.status_code == 400
        assert res.json()["error"] == "poa_required"


@patch("dashboard.services.staff_chain_service.get_collection")
def test_ensure_match_bondcase_refuses_invented_indemnitor(mock_get_col, client):
    """Refuses when active_bonds/intake has no verified indemnitor name or email."""
    def _col_side_effect(name):
        col = AsyncMock()
        if name == "arrests":
            col.find_one = AsyncMock(return_value={"booking_number": "1099999", "first_name": "JOHN", "last_name": "DOE"})
        elif name == "active_bonds":
            col.find_one = AsyncMock(return_value={
                "booking_number": "1099999",
                "case_number": "26CF001",
                "surety_id": "osi",
                "poa_numbers": ["OSI-P6-116-26-0001"],
                "bond_amount": 5000,
                "premium": 500,
                # No indemnitor contact on file
            })
        elif name == "poa_inventory":
            col.find_one = AsyncMock(return_value={
                "poa_number": "OSI-P6-116-26-0001",
                "status": "assigned",
                "bond_case_id": "1099999",
            })
        elif name == "intake_queue":
            col.find_one = AsyncMock(return_value=None)
        return col

    mock_get_col.side_effect = _col_side_effect

    with patch("dashboard.routers.staff_chain.DASHBOARD_PIN", TEST_PIN), \
         patch.dict("os.environ", {"DASHBOARD_PIN": TEST_PIN}):
        res = client.post(
            "/api/staff/chain/ensure-match-bondcase",
            headers={"X-Admin-Token": TEST_PIN},
            json={"booking_number": "1099999", "poa_numbers": ["OSI-P6-116-26-0001"]},
        )
        assert res.status_code == 400
        assert res.json()["error"] == "indemnitor_contact_missing"


@patch("dashboard.services.staff_chain_service.get_collection")
def test_ensure_match_bondcase_success_and_idempotency(mock_get_col, client):
    """Successfully creates canonical chain and is idempotent on repeat."""
    store = {
        "defendants": None,
        "indemnitors": None,
        "matches": None,
        "bond_cases": None,
        "active_bonds": {
            "_id": "act_1",
            "booking_number": "1099999",
            "case_number": "26CF001",
            "surety_id": "osi",
            "bond_amount": 5000,
            "premium": 500,
            "indemnitor_name": "Jane Doe",
            "indemnitor_email": "jane@example.com",
            "indemnitor_phone": "2395551212",
        },
        "arrests": {
            "_id": "arr_1",
            "booking_number": "1099999",
            "first_name": "JOHN",
            "middle_name": "A",
            "last_name": "DOE",
            "county": "Lee",
            "dob": "1990-01-01",
            "charges": "893.13-6a",
        },
        "poa_inventory": {
            "poa_number": "OSI-P6-116-26-0001",
            "status": "assigned",
            "bond_case_id": "1099999",
        },
        "paperwork_packets": {
            "_id": "pkt_1",
            "packet_id": "PKT-TEST-1099999",
            "pending_staff_match": True,
        },
    }

    def _col_side_effect(name):
        col = AsyncMock()
        if name == "arrests":
            col.find_one = AsyncMock(return_value=store["arrests"])
        elif name == "active_bonds":
            col.find_one = AsyncMock(return_value=store["active_bonds"])
            col.update_one = AsyncMock(return_value=MagicMock(matched_count=1, modified_count=1))
        elif name == "poa_inventory":
            col.find_one = AsyncMock(return_value=store["poa_inventory"])
            col.update_many = AsyncMock(return_value=MagicMock(modified_count=1))
        elif name == "defendants":
            col.find_one = AsyncMock(side_effect=lambda q: store["defendants"])
            async def _ins_def(d):
                store["defendants"] = d
                return MagicMock(inserted_id="def_1")
            col.insert_one = AsyncMock(side_effect=_ins_def)
        elif name == "indemnitors":
            col.find_one = AsyncMock(side_effect=lambda q: store["indemnitors"])
            async def _ins_ind(d):
                store["indemnitors"] = d
                return MagicMock(inserted_id="ind_1")
            col.insert_one = AsyncMock(side_effect=_ins_ind)
        elif name == "matches":
            col.find_one = AsyncMock(side_effect=lambda q: store["matches"])
            async def _ins_match(d):
                store["matches"] = d
                return MagicMock(inserted_id="match_1")
            col.insert_one = AsyncMock(side_effect=_ins_match)
            col.update_one = AsyncMock(return_value=MagicMock(matched_count=1, modified_count=1))
        elif name == "bond_cases":
            col.find_one = AsyncMock(side_effect=lambda q: store["bond_cases"])
            async def _ins_bc(d):
                store["bond_cases"] = d
                return MagicMock(inserted_id="bc_1")
            col.insert_one = AsyncMock(side_effect=_ins_bc)
            col.update_one = AsyncMock(return_value=MagicMock(matched_count=1, modified_count=1))
        elif name == "paperwork_packets":
            col.find_one = AsyncMock(return_value=store["paperwork_packets"])
            async def _upd_pkt(q, u):
                store["paperwork_packets"]["pending_staff_match"] = False
                return MagicMock(matched_count=1, modified_count=1)
            col.update_one = AsyncMock(side_effect=_upd_pkt)
        elif name == "audit_events":
            col.insert_one = AsyncMock(return_value=MagicMock(inserted_id="audit_1"))
        return col

    mock_get_col.side_effect = _col_side_effect

    with patch("dashboard.routers.staff_chain.DASHBOARD_PIN", TEST_PIN), \
         patch.dict("os.environ", {"DASHBOARD_PIN": TEST_PIN}):
        # First call: creates chain
        res1 = client.post(
            "/api/staff/chain/ensure-match-bondcase",
            headers={"X-Admin-Token": TEST_PIN},
            json={
                "booking_number": "1099999",
                "surety_id": "osi",
                "case_number": "26CF001",
                "poa_numbers": ["OSI-P6-116-26-0001"],
                "packet_id": "PKT-TEST-1099999",
            },
        )
        assert res1.status_code == 200
        d1 = res1.json()
        assert d1["success"] is True
        assert d1["pending_staff_match"] is False
        assert d1["surety_id"] == "osi"
        def_id_1 = d1["defendant_id"]
        bc_id_1 = d1["bond_case_id"]
        match_id_1 = d1["match_id"]
        ind_id_1 = d1["indemnitor_id"]

        assert def_id_1 and bc_id_1 and match_id_1 and ind_id_1

        # Second call: idempotent, preserves IDs
        res2 = client.post(
            "/api/staff/chain/ensure-match-bondcase",
            headers={"X-Admin-Token": TEST_PIN},
            json={
                "booking_number": "1099999",
                "surety_id": "osi",
                "case_number": "26CF001",
                "poa_numbers": ["OSI-P6-116-26-0001"],
                "packet_id": "PKT-TEST-1099999",
            },
        )
        assert res2.status_code == 200
        d2 = res2.json()
        assert d2["success"] is True
        assert d2["defendant_id"] == def_id_1
        assert d2["bond_case_id"] == bc_id_1
        assert d2["match_id"] == match_id_1
        assert d2["indemnitor_id"] == ind_id_1


def test_appearance_bond_pdf_em_dash_header_sanitization():
    """Verify that em dashes (—) in charge descriptions do not crash header latin-1 encoding."""
    from dashboard.routers.bonds import bonds_bp
    test_app = FastAPI()
    test_app.include_router(bonds_bp)
    test_client = TestClient(test_app)

    # Mock the appearance bond generation dependencies
    with patch("dashboard.routers.bonds._hydrate_appearance_bond_payload", new_callable=AsyncMock) as mock_hyd, \
         patch("dashboard.routers.bonds._build_appearance_bond_data") as mock_build, \
         patch("dashboard.routers.helpers.reject_unless_write_book", new_callable=AsyncMock, return_value=None), \
         patch("dashboard.bond_pdf_service.generate_appearance_bonds", return_value=[b"%PDF-1.4 test"]), \
         patch("dashboard.bond_pdf_service.merge_uncollated_bonds", return_value=b"%PDF-1.4 merged"), \
         patch("dashboard.bond_pdf_service.store_appearance_bond_pdfs", return_value=[]):

        mock_hyd.side_effect = lambda d: d
        # Charge description contains em-dash (\u2014)
        mock_build.return_value = ({
            "surety": "osi",
            "name": "TEST DEFENDANT",
            "booking_number": "1099999",
            "charge_details": [{
                "charge": "893.13-6a \u2014 POSSESS CONTROLLED SUBSTANCE",
                "case_number": "26CF001",
            }],
        }, None)

        res = test_client.get("/api/appearance-bond-pdf?booking=1099999&surety=osi")
        assert res.status_code == 200
        # Confirm header was safely encoded without throwing UnicodeEncodeError
        assert "\u2014" not in res.headers.get("X-Appearance-Bond-Charge", "")
        assert "-" in res.headers.get("X-Appearance-Bond-Charge", "")

