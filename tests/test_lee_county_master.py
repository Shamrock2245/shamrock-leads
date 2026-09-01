"""
Unit tests for Lee County Master Command Center and Auto-Pilot Outreach Engine.
"""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from core.lee_county_master import (
    normalize_phone_e164,
    calculate_make_it_work_terms,
    generate_family_outreach_message,
    find_family_contacts,
    send_lee_county_outreach,
    run_lee_county_autopilot_sweep,
)


class TestLeeCountyMaster(unittest.IsolatedAsyncioTestCase):

    def test_phone_normalization(self):
        self.assertEqual(normalize_phone_e164("2393322245"), "+12393322245")
        self.assertEqual(normalize_phone_e164("(239) 332-2245"), "+12393322245")
        self.assertEqual(normalize_phone_e164("+12393322245"), "+12393322245")
        self.assertEqual(normalize_phone_e164(""), "")

    def test_make_it_work_terms_calculation(self):
        terms_10k = calculate_make_it_work_terms(10000.0)
        self.assertEqual(terms_10k["total_bond"], 10000.0)
        self.assertEqual(terms_10k["statutory_premium"], 1000.0)
        self.assertEqual(terms_10k["down_payment"], 500.0)  # 5% down of total bond
        self.assertEqual(terms_10k["remaining_balance"], 500.0)
        self.assertEqual(terms_10k["weekly_4_installments"], 125.0)

        # Minimum statutory bond test
        terms_500 = calculate_make_it_work_terms(500.0)
        self.assertEqual(terms_500["statutory_premium"], 100.0)  # statutory minimum $100
        self.assertEqual(terms_500["down_payment"], 50.0)

    def test_outreach_message_generation(self):
        msg_bond = generate_family_outreach_message(
            defendant_first_name="John",
            family_name="Sarah",
            bond_amount=5000.0,
            booking_number="26-00123",
        )
        self.assertIn("Sarah", msg_bond)
        self.assertIn("John", msg_bond)
        self.assertIn("Lee County Jail (Ortiz Ave)", msg_bond)
        self.assertIn("$5,000", msg_bond)
        self.assertIn("$250", msg_bond)  # 5% down
        self.assertIn("https://paperwork.shamrockbailbonds.biz?booking=26-00123&county=Lee", msg_bond)

        msg_fa = generate_family_outreach_message(
            defendant_first_name="Mark",
            family_name="",
            bond_amount=0.0,
            booking_number="26-00456",
        )
        self.assertIn("10:00 AM First Appearance", msg_fa)
        self.assertIn("Mark", msg_fa)

    async def test_find_family_contacts_aggregation(self):
        mock_db = MagicMock()

        # Mock family_graph
        mock_family_col = MagicMock()
        mock_cursor_fg = AsyncMock()
        mock_cursor_fg.__aiter__.return_value = [
            {"relative_name": "Mary Smith", "relationship": "Mother", "phone": "239-555-1111", "confidence": 0.95}
        ]
        mock_family_col.find.return_value = mock_cursor_fg

        # Mock indemnitors
        mock_indemn_col = MagicMock()
        mock_cursor_ind = AsyncMock()
        mock_cursor_ind.__aiter__.return_value = [
            {"name": "Robert Smith", "relationship": "Brother", "phone": "239-555-2222"}
        ]
        mock_indemn_col.find.return_value = mock_cursor_ind

        # Mock intakes
        mock_intakes_col = MagicMock()
        mock_cursor_int = AsyncMock()
        mock_cursor_int.__aiter__.return_value = []
        mock_intakes_col.find.return_value = mock_cursor_int

        # Mock enrichment
        mock_enrich_col = MagicMock()
        mock_cursor_en = AsyncMock()
        mock_cursor_en.__aiter__.return_value = []
        mock_enrich_col.find.return_value = mock_cursor_en

        mock_db.__getitem__.side_effect = lambda key: {
            "family_graph": mock_family_col,
            "indemnitors": mock_indemn_col,
            "intakes": mock_intakes_col,
            "enrichment_data": mock_enrich_col,
        }[key]

        contacts = await find_family_contacts(mock_db, defendant_name="John Smith", booking_number="26-00123")
        self.assertEqual(len(contacts), 2)
        phones = [c["phone"] for c in contacts]
        self.assertIn("+12395551111", phones)
        self.assertIn("+12395552222", phones)

    async def test_send_outreach_dnc_and_cooldown(self):
        mock_db = MagicMock()
        mock_dnc = AsyncMock()
        mock_outreach = AsyncMock()
        mock_arrests = AsyncMock()

        mock_db.__getitem__.side_effect = lambda key: {
            "dnc_list": mock_dnc,
            "lee_county_outreach_log": mock_outreach,
            "arrests": mock_arrests,
        }[key]

        # Case 1: On DNC List
        mock_dnc.find_one.return_value = {"phone": "+12395559999", "reason": "Opt-out"}
        res_dnc = await send_lee_county_outreach(mock_db, "26-001", "+12395559999", "Test")
        self.assertFalse(res_dnc["ok"])
        self.assertIn("Do Not Call (DNC)", res_dnc["error"])

        # Case 2: Cooldown active
        mock_dnc.find_one.return_value = None
        mock_outreach.find_one.return_value = {"sent_at": "2026-09-01T12:00:00Z"}
        res_cd = await send_lee_county_outreach(mock_db, "26-001", "+12395558888", "Test")
        self.assertFalse(res_cd["ok"])
        self.assertIn("Outreach already sent", res_cd["error"])

    async def test_autopilot_sweep_disabled(self):
        mock_db = MagicMock()
        mock_cfg_col = AsyncMock()
        mock_cfg_col.find_one.return_value = {"county": "Lee", "autopilot_enabled": False}
        mock_db.__getitem__.return_value = mock_cfg_col

        res = await run_lee_county_autopilot_sweep(mock_db)
        self.assertTrue(res["ok"])
        self.assertEqual(res["status"], "skipped")
        self.assertIn("Autopilot is disabled", res["reason"])


if __name__ == "__main__":
    unittest.main()
