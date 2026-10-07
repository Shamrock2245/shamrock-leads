"""
Unit and integration tests for POST /api/staff/chain/ensure-match-bondcase.
Verifies:
- God-Admin / staff auth gating (401 when unauthorized, including sub-agent sessions).
- Fail-closed validation (missing arrest, active bond, POA, premium, or on-file contact).
- Canonical chain creation (Defendant → Indemnitor → Match → BondCase).
- Idempotency on repeated calls, including after poa_inventory is stamped with the BondCase UUID.
- Packet finalize fails closed when inline ensure fails. Shannon keeps skip_bond_binding.
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


def _auth_env():
    return patch.dict("os.environ", {"DASHBOARD_PIN": TEST_PIN, "SECRET_KEY": "ci-not-a-real-secret"})


def _pin_header():
    return {"X-Admin-Token": TEST_PIN}


class _Memory:
    """One document per collection. Writes stick, so a second ensure sees them."""

    def __init__(self):
        self.docs: dict[str, list] = {}

    def seed(self, name: str, doc: dict):
        self.docs.setdefault(name, []).append(doc)
        return doc

    def first(self, name: str):
        rows = self.docs.get(name) or []
        return rows[0] if rows else None

    def bind(self, mock_get_col):
        def _col(name):
            col = AsyncMock()
            bucket = self.docs.setdefault(name, [])

            async def find_one(query=None, *args, **kwargs):
                return bucket[0] if bucket else None

            async def insert_one(doc, *args, **kwargs):
                bucket.append(doc)
                return MagicMock(inserted_id="mem")

            async def update_one(query, update, *args, **kwargs):
                if bucket:
                    bucket[0].update((update or {}).get("$set") or {})
                return MagicMock(matched_count=1, modified_count=1)

            async def update_many(query, update, *args, **kwargs):
                sets = (update or {}).get("$set") or {}
                for doc in bucket:
                    doc.update(sets)
                return MagicMock(modified_count=len(bucket))

            col.find_one = AsyncMock(side_effect=find_one)
            col.insert_one = AsyncMock(side_effect=insert_one)
            col.update_one = AsyncMock(side_effect=update_one)
            col.update_many = AsyncMock(side_effect=update_many)
            return col

        mock_get_col.side_effect = _col


def _ready_memory(*, premium=750, poa_bond_case_id="1099999", poa_assigned_to="", active_bond_case_id=""):
    mem = _Memory()
    active = {
        "_id": "act_1",
        "booking_number": "1099999",
        "case_number": "26CF001",
        "surety_id": "osi",
        "bond_amount": 5000,
        "indemnitor_name": "Jane Doe",
        "indemnitor_email": "jane@example.com",
        "indemnitor_phone": "2395551212",
    }
    if premium is not None:
        active["premium"] = premium
    if active_bond_case_id:
        active["bond_case_id"] = active_bond_case_id
    mem.seed("active_bonds", active)
    mem.seed("arrests", {
        "_id": "arr_1",
        "booking_number": "1099999",
        "first_name": "JOHN",
        "middle_name": "A",
        "last_name": "DOE",
        "county": "Lee",
        "dob": "1990-01-01",
        "charges": "893.13-6a",
    })
    poa = {
        "poa_number": "OSI-P6-116-26-0001",
        "status": "assigned",
        "bond_case_id": poa_bond_case_id,
    }
    if poa_assigned_to:
        poa["assigned_to"] = poa_assigned_to
    mem.seed("poa_inventory", poa)
    return mem


def _post_ensure(client, body):
    with _auth_env(), patch("dashboard.routers.staff_chain.DASHBOARD_PIN", TEST_PIN):
        return client.post(
            "/api/staff/chain/ensure-match-bondcase",
            headers=_pin_header(),
            json=body,
        )


@patch("dashboard.services.staff_chain_service.get_collection")
def test_ensure_idempotent_after_poa_uuid_write(mock_get_col, client):
    """First ensure stamps the BondCase UUID onto the POA; the second must be 200."""
    mem = _ready_memory()
    mem.bind(mock_get_col)
    body = {
        "booking_number": "1099999",
        "surety_id": "osi",
        "case_number": "26CF001",
        "poa_numbers": ["OSI-P6-116-26-0001"],
    }

    res1 = _post_ensure(client, body)
    assert res1.status_code == 200, res1.text
    d1 = res1.json()
    bond_case_id = d1["bond_case_id"]
    assert bond_case_id and bond_case_id != "1099999"
    assert d1["premium"] == 750.0

    poa = mem.first("poa_inventory")
    assert poa["bond_case_id"] == bond_case_id
    assert poa["assigned_to"] == bond_case_id

    res2 = _post_ensure(client, body)
    assert res2.status_code == 200, res2.text
    d2 = res2.json()
    assert d2["success"] is True
    assert d2["bond_case_id"] == bond_case_id
    assert d2["defendant_id"] == d1["defendant_id"]
    assert d2["match_id"] == d1["match_id"]
    assert d2["indemnitor_id"] == d1["indemnitor_id"]
    assert d2["premium"] == 750.0
    assert d2.get("error") is None


@patch("dashboard.services.staff_chain_service.get_collection")
def test_ensure_accepts_legacy_mixed_poa_ownership(mock_get_col, client):
    """Production rows from the first ensure wrote UUID + booking. Re-run stays ours."""
    existing_id = "31bccd64-1e85-4842-ad12-0660b7c6740a"
    mem = _ready_memory(
        poa_bond_case_id=existing_id,
        poa_assigned_to="1099999",
        active_bond_case_id=existing_id,
    )
    mem.seed("bond_cases", {
        "_id": "bc_existing",
        "bond_case_id": existing_id,
        "Bond_Case_ID": existing_id,
        "booking_number": "1099999",
    })
    mem.bind(mock_get_col)

    res = _post_ensure(client, {
        "booking_number": "1099999",
        "surety_id": "osi",
        "case_number": "26CF001",
        "poa_numbers": ["OSI-P6-116-26-0001"],
    })
    assert res.status_code == 200, res.text
    assert res.json()["bond_case_id"] == existing_id
    poa = mem.first("poa_inventory")
    assert poa["bond_case_id"] == existing_id
    assert poa["assigned_to"] == existing_id


@patch("dashboard.services.staff_chain_service.get_collection")
def test_ensure_refuses_poa_assigned_to_other_case(mock_get_col, client):
    mem = _ready_memory(poa_bond_case_id="other-bond-case-uuid")
    mem.bind(mock_get_col)
    res = _post_ensure(client, {
        "booking_number": "1099999",
        "poa_numbers": ["OSI-P6-116-26-0001"],
    })
    assert res.status_code == 409
    assert res.json()["error"].startswith("poa_assigned_elsewhere:")


@patch("dashboard.services.staff_chain_service.get_collection")
def test_ensure_refuses_unassigned_poa(mock_get_col, client):
    mem = _ready_memory(poa_bond_case_id="")
    mem.bind(mock_get_col)
    res = _post_ensure(client, {
        "booking_number": "1099999",
        "poa_numbers": ["OSI-P6-116-26-0001"],
    })
    assert res.status_code == 400
    assert res.json()["error"].startswith("poa_not_assigned:")


@patch("dashboard.services.staff_chain_service.get_collection")
def test_ensure_refuses_missing_premium(mock_get_col, client):
    mem = _ready_memory(premium=None)
    mem.bind(mock_get_col)
    res = _post_ensure(client, {
        "booking_number": "1099999",
        "poa_numbers": ["OSI-P6-116-26-0001"],
    })
    assert res.status_code == 400
    body = res.json()
    assert body["error"] == "premium_required"
    assert body.get("premium") != 500
    assert mem.first("bond_cases") is None


@patch("dashboard.services.staff_chain_service.get_collection")
def test_ensure_refuses_zero_premium_even_when_on_file_amount_exists(mock_get_col, client):
    mem = _ready_memory(premium=750)
    mem.bind(mock_get_col)
    res = _post_ensure(client, {
        "booking_number": "1099999",
        "poa_numbers": ["OSI-P6-116-26-0001"],
        "premium": 0,
    })
    assert res.status_code == 400
    assert res.json()["error"] == "premium_required"
    assert mem.first("bond_cases") is None


@patch("dashboard.services.staff_chain_service.get_collection")
def test_ensure_accepts_explicit_premium_when_file_has_none(mock_get_col, client):
    mem = _ready_memory(premium=None)
    mem.first("active_bonds").pop("indemnitor_name", None)
    mem.first("active_bonds").pop("indemnitor_email", None)
    mem.bind(mock_get_col)
    res = _post_ensure(client, {
        "booking_number": "1099999",
        "poa_numbers": ["OSI-P6-116-26-0001"],
        "premium": 750,
    })
    assert res.status_code == 400
    assert res.json()["error"] == "indemnitor_contact_missing"


def test_sub_agent_session_cannot_ensure(client):
    from dashboard.auth.pin_middleware import COOKIE_NAME, _sign_token

    with _auth_env():
        token = _sign_token(
            email="agent-p000001@shamrockbailbonds.biz",
            role="sub_agent",
            agent_name="Office Agent",
            license_number="P000001",
            is_admin=True,
        )
        res = client.post(
            "/api/staff/chain/ensure-match-bondcase",
            cookies={COOKIE_NAME: token},
            json={"booking_number": "1099999"},
        )
    assert res.status_code == 401
    assert res.json()["error"] == "unauthorized"


def test_non_staff_session_cannot_ensure(client):
    from dashboard.auth.pin_middleware import COOKIE_NAME, _sign_token

    with _auth_env():
        token = _sign_token(email="clerk@example.com", role="clerk")
        res = client.post(
            "/api/staff/chain/ensure-match-bondcase",
            cookies={COOKIE_NAME: token},
            json={"booking_number": "1099999"},
        )
    assert res.status_code == 401


def test_god_admin_and_staff_sessions_pass_auth(client):
    from dashboard.auth.pin_middleware import COOKIE_NAME, _sign_token

    with _auth_env():
        for role, email in (("god_admin", "admin@shamrockbailbonds.biz"), ("staff", "office@example.com")):
            token = _sign_token(email=email, role=role, is_admin=(role == "god_admin"))
            res = client.post(
                "/api/staff/chain/ensure-match-bondcase",
                cookies={COOKIE_NAME: token},
                json={},
            )
            assert res.status_code == 400, role
            assert res.json()["error"] == "booking_number_required"


def test_machine_token_passes_auth_and_wrong_token_does_not(client):
    with patch.dict("os.environ", {"GAS_API_KEY": "gas-test-key", "DASHBOARD_PIN": TEST_PIN, "SECRET_KEY": "ci-not-a-real-secret"}):
        ok = client.post(
            "/api/staff/chain/ensure-match-bondcase",
            headers={"X-API-Key": "gas-test-key"},
            json={},
        )
        bad = client.post(
            "/api/staff/chain/ensure-match-bondcase",
            headers={"X-API-Key": "nope", "X-Admin-Token": "nope"},
            json={"booking_number": "1099999"},
        )
    assert ok.status_code == 400
    assert ok.json()["error"] == "booking_number_required"
    assert bad.status_code == 401


def test_shannon_email_path_keeps_skip_bond_binding():
    import inspect
    from dashboard.routers.paperwork import shannon_email_indemnitor_paperwork

    src = inspect.getsource(shannon_email_indemnitor_paperwork)
    assert "skip_bond_binding=True" in src
    assert "pending_staff_match" in src


def test_finalize_ensure_failure_is_fail_closed():
    from fastapi.responses import JSONResponse
    from dashboard.routers.paperwork import paperwork_bp

    app = FastAPI()
    app.include_router(paperwork_bp)
    local = TestClient(app)
    ctx = {
        "booking_number": "1099999",
        "match_status": "unknown",
        "bond_case_id": "",
        "match_id": "",
        "county": "Lee",
        "state": "FL",
    }
    ensure = AsyncMock(return_value={
        "success": False,
        "status_code": 400,
        "error": "poa_required",
        "message": "At least one POA number is required for bond chain validation",
    })
    with patch(
        "dashboard.services.packet_builder_service.resolve_case_context",
        new_callable=AsyncMock,
        return_value=ctx,
    ), patch(
        "dashboard.services.staff_chain_service.ensure_match_bondcase",
        ensure,
    ), patch(
        "dashboard.services.docuseal_service.get_docuseal_service",
    ) as docuseal, patch(
        "dashboard.routers.helpers.reject_unless_write_book",
        new_callable=AsyncMock,
        return_value=JSONResponse({"success": False, "error": "should_not_reach_write_book"}, status_code=418),
    ) as write_book:
        res = local.post("/api/paperwork/packet/finalize", json={
            "booking_number": "1099999",
            "surety_id": "osi",
            "provider": "none",
        })
    assert res.status_code == 400
    body = res.json()
    assert body["success"] is False
    assert body["error"] == "poa_required"
    assert body["bound"] is False
    ensure.assert_awaited()
    docuseal.assert_not_called()
    write_book.assert_not_called()


def test_finalize_ensure_exception_does_not_succeed():
    from dashboard.routers.paperwork import paperwork_bp

    app = FastAPI()
    app.include_router(paperwork_bp)
    local = TestClient(app)
    ctx = {
        "booking_number": "1099999",
        "match_status": "pending",
        "county": "Lee",
        "state": "FL",
    }
    with patch(
        "dashboard.services.packet_builder_service.resolve_case_context",
        new_callable=AsyncMock,
        return_value=ctx,
    ), patch(
        "dashboard.services.staff_chain_service.ensure_match_bondcase",
        new_callable=AsyncMock,
        side_effect=RuntimeError("db down"),
    ):
        res = local.post("/api/paperwork/packet/finalize", json={"booking_number": "1099999"})
    assert res.status_code == 500
    body = res.json()
    assert body["success"] is False
    assert body["error"] == "chain_ensure_failed"
    assert body["bound"] is False
    assert "db down" not in res.text


def test_finalize_skips_ensure_when_chain_already_validated():
    from fastapi.responses import JSONResponse
    from dashboard.routers.paperwork import paperwork_bp

    app = FastAPI()
    app.include_router(paperwork_bp)
    local = TestClient(app)
    ctx = {
        "booking_number": "1099999",
        "match_status": "validated",
        "bond_case_id": "bc-already",
        "match_id": "match-already",
        "county": "Lee",
        "state": "FL",
    }
    ensure = AsyncMock()
    with patch(
        "dashboard.services.packet_builder_service.resolve_case_context",
        new_callable=AsyncMock,
        return_value=ctx,
    ), patch(
        "dashboard.services.staff_chain_service.ensure_match_bondcase",
        ensure,
    ), patch(
        "dashboard.routers.helpers.reject_unless_write_book",
        new_callable=AsyncMock,
        return_value=JSONResponse({"success": False, "error": "sentinel_past_ensure"}, status_code=418),
    ):
        res = local.post("/api/paperwork/packet/finalize", json={"booking_number": "1099999"})
    assert res.status_code == 418
    assert res.json()["error"] == "sentinel_past_ensure"
    ensure.assert_not_called()

