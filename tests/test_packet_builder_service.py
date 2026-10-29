"""Unit tests for adaptive packet builder (no MongoDB required)."""
import pytest

from dashboard.services.packet_builder_service import (
    apply_self_indemnitor,
    assemble_manifest,
    build_adaptive_field_map,
    charge_details_from_sources,
    hydration_score,
    is_lee_county,
    lee_clerk_search_url,
    template_slug_for_catalog_key,
    verify_self_indemnitor_pin,
)


def test_self_indemnitor_pin_gate(monkeypatch):
    monkeypatch.setenv("SELF_INDEMNITOR_PIN", "918273")
    assert verify_self_indemnitor_pin("918273") is True
    assert verify_self_indemnitor_pin(" wrong ") is False
    assert verify_self_indemnitor_pin("") is False


def test_self_indemnitor_pin_fails_closed_when_unset(monkeypatch):
    import dashboard.services.packet_builder_service as pbs

    monkeypatch.delenv("SELF_INDEMNITOR_PIN", raising=False)
    monkeypatch.setattr(pbs, "SELF_INDEMNITOR_PIN", "")
    assert verify_self_indemnitor_pin("918273") is False
    assert verify_self_indemnitor_pin("") is False


def test_apply_self_indemnitor_copies_defendant(monkeypatch):
    monkeypatch.setenv("SELF_INDEMNITOR_PIN", "918273")
    ctx = {
        "defendant": {
            "name": "Jane Defendant",
            "phone": "2395550199",
            "address": "100 Oak St",
            "dob": "02/02/1992",
            "email": "jane@example.com",
        },
        "indemnitor": {"phone": ""},
        "bond_amount": 1500,
    }
    out = apply_self_indemnitor(ctx, "918273")
    assert out["self_indemnitor"] is True
    assert out["indemnitor"]["name"] == "Jane Defendant"
    assert out["indemnitor"]["phone"] == "2395550199"
    assert out["indemnitor"]["relationship"] == "Self"
    with pytest.raises(PermissionError):
        apply_self_indemnitor(ctx, "000000")


def test_lee_charge_details_from_arrest_extra():
    rows = charge_details_from_sources(
        arrest={
            "extra": {
                "charge_details": [
                    {"charge": "BATTERY", "bond_amount": 1000, "case_number": "26CF1"},
                    {"charge": "RESIST", "bond_amount": 500, "case_number": "26CF1"},
                ]
            }
        },
        default_case="FALLBACK",
        default_bond=999,
    )
    assert len(rows) == 2
    assert rows[0]["charge"] == "BATTERY"
    assert rows[0]["bond_amount"] == 1000.0
    assert rows[1]["case_number"] == "26CF1"
    assert is_lee_county("Lee County")
    assert is_lee_county("Lee")
    assert is_lee_county("Lee (FL)")
    assert not is_lee_county("Lehigh")
    assert not is_lee_county("Lee", "GA")
    assert "leeclerk.org" in lee_clerk_search_url("26CF1", "1030001")


def test_adaptive_field_map_and_hydration():
    ctx = {
        "defendant": {
            "name": "John Doe",
            "dob": "01/01/1990",
            "address": "1 Main",
            "phone": "2395550100",
        },
        "indemnitor": {
            "name": "Mary Doe",
            "phone": "2395550101",
            "address": "2 Main",
            "email": "mary@example.com",
        },
        "bond_amount": 5000,
        "premium_amount": 500,
        "county": "Lee",
        "booking_number": "BK1",
        "case_number": "CASE1",
        "poa_number": "POA99",
        "surety_id": "osi",
        "charges": "Battery",
    }
    fields = build_adaptive_field_map(ctx)
    assert fields["defendant_name"] == "John Doe"
    assert fields["IndemnitorName"] == "Mary Doe"
    assert "BondAmount" in fields
    audit = hydration_score(fields)
    assert audit["hydration_score"] >= 90
    assert audit["hydrated_count"] >= 10


def test_catalog_to_template_and_manifest():
    assert template_slug_for_catalog_key("indemnity_agreement") == "indemnity-agreement"
    assert template_slug_for_catalog_key("master_bail_application") == "defendant-application"
    cats = {
        "universal": ["indemnity_agreement", "master_bail_application"],
        "payment_plan": ["payment_plan_agreement"],
        "osi_surety": ["osi_appearance_bond"],
        "palmetto_surety": ["palmetto_appearance_bond"],
        "conditional": ["cosigner_addendum"],
    }
    man = assemble_manifest(cats, surety_id="osi", include_payment_plan=True, self_indemnitor=True)
    keys = [m["catalog_key"] for m in man]
    assert "indemnity_agreement" in keys
    assert "payment_plan_agreement" in keys
    assert "osi_appearance_bond" in keys
    # self-indemnitor skips cosigner addendum
    assert "cosigner_addendum" not in keys
    print_only = [m for m in man if m["print_only"]]
    ab = next(m for m in print_only if m["template_slug"] == "appearance-bond")
    assert ab["e_sign"] is False
    assert ab["signature_mode"] == "wet_ink_live"
    assert ab["delivery"] == "print_and_jail"
    assert "wet" in ab["procedure"].lower() or "jail" in ab["procedure"].lower()


def test_split_name_handles_jail_roster_comma_form():
    from dashboard.services.packet_builder_service import _split_name

    first, middle, last = _split_name("PERKINS, MICHAEL JAMES")
    assert first == "MICHAEL"
    assert middle == "JAMES"
    assert last == "PERKINS"
    first2, mid2, last2 = _split_name("Jane Ann Doe")
    assert first2 == "Jane"
    assert mid2 == "Ann"
    assert last2 == "Doe"


def test_adaptive_field_map_includes_write_bond_date_and_words_keys():
    fields = build_adaptive_field_map(
        {
            "defendant": {"name": "John Doe"},
            "indemnitor": {"name": "Mary Doe"},
            "bond_amount": 5000,
            "premium_amount": 500,
            "county": "Lee",
            "booking_number": "BK1",
            "case_number": "CASE1",
            "poa_number": "POA99",
            "surety_id": "osi",
        }
    )
    assert fields.get("today_day")
    assert fields.get("today_month")
    assert fields.get("today_year_2digit")
    assert fields.get("bond_date_day") == fields.get("today_day")
    assert "Thousand" in (fields.get("bond_amount_words") or "")


@pytest.mark.asyncio
async def test_resolve_case_context_reads_snake_case_arrest_from_bookmarklet():
    """booking_extract_merge writes snake_case; hydrate must read those keys."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from dashboard.services.packet_builder_service import resolve_case_context

    arrest_doc = {
        "booking_number": "1029767",
        "county": "Lee",
        "state": "FL",
        "full_name": "PERKINS, MICHAEL JAMES",
        "first_name": "Michael",
        "last_name": "Perkins",
        "dob": "1985-04-12",
        "address": "100 Oak St, Fort Myers, FL 33901",
        "city": "Fort Myers",
        "zip": "33901",
        "height": "5-10",
        "weight": "180",
        "race": "W",
        "sex": "M",
        "facility": "Lee County Jail",
        "charges": "BATTERY | RESIST OFFICER",
        "charge_details": [
            {"charge": "BATTERY", "bond_amount": 5000, "case_number": "26CF016741"},
            {"charge": "RESIST OFFICER", "bond_amount": 1000, "case_number": "26CF016741"},
        ],
        "bond_amount": 6000,
        "case_number": "26CF016741",
        "court_date": "9/8/2026",
        "court_time": "8:30 AM",
    }

    class _Cursor:
        def __init__(self, docs):
            self._docs = docs

        async def to_list(self, length=3):
            return self._docs[:length]

    arrests = MagicMock()
    arrests.find = MagicMock(return_value=_Cursor([arrest_doc]))
    empty = MagicMock()
    empty.find_one = AsyncMock(return_value=None)
    empty.find = MagicMock(return_value=_Cursor([]))

    def _get(name):
        if name == "arrests":
            return arrests
        return empty

    with patch("dashboard.extensions.get_collection", side_effect=_get):
        ctx = await resolve_case_context(
            booking_number="1029767",
            county="Lee",
            state="FL",
        )

    assert "arrest" in ctx["sources"]
    assert ctx["defendant"]["name"] == "PERKINS, MICHAEL JAMES"
    assert ctx["defendant"]["first_name"] == "Michael"
    assert ctx["defendant"]["last_name"] == "Perkins"
    assert ctx["defendant"]["dob"] == "1985-04-12"
    assert ctx["defendant"]["height"] == "5-10"
    assert ctx["defendant"]["race"] == "W"
    assert ctx["facility"] == "Lee County Jail"
    assert "BATTERY" in (ctx.get("charges") or "")
    assert ctx["case_number"] == "26CF016741"
    assert len(ctx["charge_details"]) == 2
    assert ctx["bond_amount"] == 6000.0


@pytest.mark.asyncio
async def test_resolve_case_context_fail_closed_on_ambiguous_booking():
    from unittest.mock import AsyncMock, MagicMock, patch

    from dashboard.services.packet_builder_service import resolve_case_context

    class _Cursor:
        def __init__(self, docs):
            self._docs = docs

        async def to_list(self, length=3):
            return self._docs[:length]

    hits = [
        {"booking_number": "BK1", "county": "Lee", "full_name": "DOE, JOHN"},
        {"booking_number": "BK1", "county": "Lee", "full_name": "DOE, JANE"},
    ]
    arrests = MagicMock()
    arrests.find = MagicMock(return_value=_Cursor(hits))
    empty = MagicMock()
    empty.find_one = AsyncMock(return_value=None)
    empty.find = MagicMock(return_value=_Cursor([]))

    def _get(name):
        return arrests if name == "arrests" else empty

    with patch("dashboard.extensions.get_collection", side_effect=_get):
        ctx = await resolve_case_context(booking_number="BK1", county="Lee", state="FL")

    assert "arrest_ambiguous" in ctx["sources"]
    assert "arrest" not in ctx["sources"]
    assert not (ctx.get("defendant") or {}).get("name")


@pytest.mark.asyncio
async def test_resolve_case_context_keeps_bonded_coindemnitor():
    """active_bonds.indemnitors reaches the packet context, primary first."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from dashboard.services.docuseal_service import build_bond_data_from_dashboard
    from dashboard.services.packet_builder_service import resolve_case_context

    bond = {
        "bond_case_id": "BC-T7",
        "booking_number": "TEST-BK-CO",
        "surety_id": "palmetto",
        "poa_number": "TEST-POA-1",
        "defendant_name": "TEST DEFENDANT",
        "indemnitor_name": "TEST INDEMNITOR",
        "indemnitor": {"name": "TEST INDEMNITOR", "email": "admin@shamrockbailbonds.biz"},
        "indemnitors": [
            {"name": "TEST COINDEMNITOR", "email": "admin@shamrockbailbonds.biz", "role": "co_indemnitor"},
        ],
        "county": "Lee",
        "state": "FL",
        "case_number": "TEST-CASE-1",
        "Bond_Amount": 1000,
    }

    class _Cursor:
        def __init__(self, rows):
            self._rows = rows

        async def to_list(self, length=3):
            return list(self._rows)[:length]

    bonds = MagicMock()
    bonds.find_one = AsyncMock(return_value=bond)
    bonds.find = MagicMock(return_value=_Cursor([bond]))
    empty = MagicMock()
    empty.find_one = AsyncMock(return_value=None)
    empty.find = MagicMock(return_value=_Cursor([]))

    def _get(name):
        if name in ("active_bonds", "bond_cases"):
            return bonds
        return empty

    with patch("dashboard.extensions.get_collection", side_effect=_get):
        ctx = await resolve_case_context(bond_case_id="BC-T7")

    names = [row.get("name") for row in ctx["indemnitors"]]
    assert names[0] == "TEST INDEMNITOR"
    assert "TEST COINDEMNITOR" in names
    built = build_bond_data_from_dashboard(ctx=ctx, surety_id="palmetto")
    assert [row.get("name") for row in built["indemnitors"]] == names


def _bond_cursor(docs):
    class _Cursor:
        def __init__(self, rows):
            self._rows = rows

        async def to_list(self, length=3):
            return list(self._rows)[:length]

    return _Cursor(docs)


@pytest.mark.asyncio
async def test_coindemnitor_comes_only_from_this_bonded_case():
    """Another county's bond with the same booking number is not a co-indemnitor."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from dashboard.services.packet_builder_service import resolve_case_context

    lee = {
        "bond_case_id": "BC-LEE",
        "booking_number": "TEST-BK-SAME",
        "county": "Lee",
        "state": "FL",
        "indemnitor_name": "TEST INDEMNITOR",
        "indemnitors": [
            {"name": "TEST COINDEMNITOR", "email": "admin@shamrockbailbonds.biz", "role": "co_indemnitor"},
        ],
        "defendant_name": "TEST DEFENDANT",
    }
    collier = {
        "bond_case_id": "BC-COLLIER",
        "booking_number": "TEST-BK-SAME",
        "county": "Collier",
        "state": "FL",
        "indemnitor_name": "TEST OTHER INDEMNITOR",
        "indemnitors": [
            {"name": "TEST OTHER COINDEMNITOR", "email": "admin@shamrockbailbonds.biz", "role": "co_indemnitor"},
        ],
        "defendant_name": "TEST DEFENDANT",
    }
    bonds = MagicMock()
    bonds.find = MagicMock(return_value=_bond_cursor([collier, lee]))
    empty = MagicMock()
    empty.find_one = AsyncMock(return_value=None)
    empty.find = MagicMock(return_value=_bond_cursor([]))

    def _get(name):
        return bonds if name == "active_bonds" else empty

    with patch("dashboard.extensions.get_collection", side_effect=_get):
        ctx = await resolve_case_context(
            booking_number="TEST-BK-SAME", county="Lee", state="FL",
        )

    names = [row.get("name") for row in ctx.get("indemnitors") or []]
    assert "TEST COINDEMNITOR" in names
    assert "TEST OTHER COINDEMNITOR" not in names
    assert "bond_ambiguous" not in ctx["sources"]


@pytest.mark.asyncio
async def test_coindemnitor_fails_closed_when_two_cases_share_the_booking():
    from unittest.mock import AsyncMock, MagicMock, patch

    from dashboard.services.packet_builder_service import resolve_case_context

    first = {
        "bond_case_id": "BC-ONE",
        "booking_number": "TEST-BK-SAME",
        "county": "Lee",
        "state": "FL",
        "indemnitors": [{"name": "TEST COINDEMNITOR ONE", "role": "co_indemnitor"}],
    }
    second = {
        "bond_case_id": "BC-TWO",
        "booking_number": "TEST-BK-SAME",
        "county": "Lee",
        "state": "FL",
        "indemnitors": [{"name": "TEST COINDEMNITOR TWO", "role": "co_indemnitor"}],
    }
    bonds = MagicMock()
    bonds.find = MagicMock(return_value=_bond_cursor([first, second]))
    empty = MagicMock()
    empty.find_one = AsyncMock(return_value=None)
    empty.find = MagicMock(return_value=_bond_cursor([]))

    def _get(name):
        return bonds if name == "active_bonds" else empty

    with patch("dashboard.extensions.get_collection", side_effect=_get):
        ctx = await resolve_case_context(
            booking_number="TEST-BK-SAME", county="Lee", state="FL",
        )

    assert ctx.get("indemnitors") in (None, [])
    assert "bond_ambiguous" in ctx["sources"]
    assert "bond" not in ctx["sources"]


@pytest.mark.asyncio
async def test_coindemnitor_not_taken_from_another_booking():
    """A bond for a different booking, even the same defendant, is not this case."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from dashboard.services.packet_builder_service import resolve_case_context

    other_booking = {
        "bond_case_id": "BC-OTHER",
        "booking_number": "TEST-BK-OTHER",
        "county": "Lee",
        "state": "FL",
        "defendant_name": "TEST DEFENDANT",
        "indemnitors": [
            {"name": "TEST OTHER COINDEMNITOR", "role": "co_indemnitor"},
        ],
    }
    bonds = MagicMock()
    bonds.find = MagicMock(return_value=_bond_cursor([other_booking]))
    empty = MagicMock()
    empty.find_one = AsyncMock(return_value=None)
    empty.find = MagicMock(return_value=_bond_cursor([]))

    def _get(name):
        return bonds if name == "active_bonds" else empty

    with patch("dashboard.extensions.get_collection", side_effect=_get):
        ctx = await resolve_case_context(
            bond_case_id="BC-THIS",
            booking_number="TEST-BK-THIS",
            county="Lee",
            state="FL",
        )

    assert "TEST OTHER COINDEMNITOR" not in [
        row.get("name") for row in ctx.get("indemnitors") or []
    ]
    assert "bond" not in ctx["sources"]


def test_adaptive_map_includes_dl_and_offense_rows():
    from dashboard.services.packet_builder_service import build_adaptive_field_map
    fields = build_adaptive_field_map({
        "defendant": {"name": "A B", "dl": "D9", "address": "1 St", "dob": "2000-01-01"},
        "indemnitor": {"name": "C D", "dl": "I9", "dob": "1980-01-01", "last_name": "D", "first_name": "C"},
        "charges": "BATTERY | RESIST",
        "surety_id": "osi",
        "bond_amount": 1000,
    })
    assert fields.get("defendant_dl") == "D9"
    assert fields.get("IndLastName") == "D"
    assert fields.get("offense_1") == "BATTERY"
    assert fields.get("offense_2") == "RESIST"
