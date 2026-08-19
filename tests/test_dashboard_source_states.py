"""Regression coverage for truthful scraper source-contract state in the dashboard."""
from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _source_states() -> dict[str, str]:
    """Load the literal UI state registry without importing optional DB clients."""
    tree = ast.parse((ROOT / "dashboard" / "extensions.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "SCRAPER_SOURCE_STATES":
            return ast.literal_eval(node.value)
    raise AssertionError("SCRAPER_SOURCE_STATES declaration not found")


class DashboardSourceStateTests(unittest.TestCase):
    def test_known_verified_and_guarded_labels_are_explicit(self) -> None:
        states = _source_states()
        self.assertEqual(states["Rankin (MS)"], "verified_public")
        self.assertEqual(states["Giles (TN)"], "fail_closed")
        self.assertEqual(states["Jefferson (AL)"], "fail_closed")
        self.assertEqual(states["East Baton Rouge (LA)"], "fail_closed")
        self.assertEqual(states["Jefferson (LA)"], "fail_closed")
        self.assertEqual(states["Lafayette (LA)"], "fail_closed")
        self.assertEqual(states["Ascension (LA)"], "fail_closed")
        self.assertEqual(states["Forsyth (NC)"], "fail_closed")
        self.assertEqual(states["Madison (AL)"], "fail_closed")
        self.assertEqual(states["Mobile (AL)"], "fail_closed")
        self.assertEqual(states["Clermont (OH)"], "fail_closed")
        self.assertEqual(states["Clinton (OH)"], "fail_closed")
        self.assertEqual(states["Huron (OH)"], "fail_closed")

    def test_omitted_registered_label_uses_the_documented_unverified_default(self) -> None:
        states = _source_states()
        self.assertNotIn("Unknown County (ZZ)", states)
        extensions = (ROOT / "dashboard" / "extensions.py").read_text()
        self.assertIn('return SCRAPER_SOURCE_STATES.get(label, "unverified")', extensions)

    def test_health_api_and_ui_render_source_state_without_manual_run_for_guards(self) -> None:
        stats = (ROOT / "dashboard" / "routers" / "stats.py").read_text()
        health = (ROOT / "dashboard" / "sl-health.js").read_text()
        page = (ROOT / "dashboard" / "index.html").read_text()

        self.assertIn('"source_state": scraper_source_state(label)', stats)
        self.assertIn("SOURCE_STATE_CONFIG", health)
        self.assertIn("r.source_state !== 'fail_closed'", health)
        self.assertIn("isGuarded", health)
        self.assertIn("Source Contract", page)
        self.assertIn("data-filter=\"fail_closed\"", page)


if __name__ == "__main__":
    unittest.main()
