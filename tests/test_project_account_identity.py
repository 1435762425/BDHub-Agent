import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lib import project_account_identity as identity  # noqa: E402


class ReconnectInboxTests(unittest.TestCase):
    """The real adapter step that runs right after a new identity generation is published."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "var").mkdir()
        self.adapter = identity.ProjectAccountIdentityAdapter(self.root)
        self.adapter.context = {"market": "my"}
        self.status = self.root / "var/market-inbox-my.json"

    def tearDown(self):
        self.temp.cleanup()

    def test_missing_inbox_status_reports_not_reconnected(self):
        self.assertFalse(self.adapter.reconnect_inbox("acc8", None, {}))

    def test_a_later_clean_inbox_round_counts_as_reconnected(self):
        self.status.write_text(json.dumps({"checkedAt": 100, "state": "attention",
                                           "errorCode": "taplink_remote_read_failed"}))

        def next_round(_seconds):
            self.status.write_text(json.dumps({"checkedAt": 200, "state": "completed", "errorCode": None}))

        with mock.patch.object(identity.time, "sleep", side_effect=next_round):
            self.assertTrue(self.adapter.reconnect_inbox("acc8", None, {}))



class ValidationErrorCodeTests(unittest.TestCase):
    def test_capability_gate_code_is_kept_and_other_errors_reduce_to_their_type(self):
        from lib.project_account_identity import _error_code
        self.assertEqual(_error_code(ValueError("market_institution_not_verified")), "market_institution_not_verified")
        self.assertEqual(_error_code(RuntimeError("market_capability_unavailable:de:product_search:pending")),
                         "market_capability_unavailable:de:product_search:pending")
        self.assertEqual(_error_code(RuntimeError("GET https://x/?token=secret failed")), "RuntimeError")
        self.assertEqual(_error_code(RuntimeError("")), "RuntimeError")

    def test_germany_allows_the_product_search_its_login_validation_needs(self):
        from lib.legacy_runtime import configure_vendored_bdhub
        configure_vendored_bdhub(root=ROOT)
        from bdhub.hub.markets import MARKETS
        self.assertEqual(MARKETS["de"].capabilities.status("product_search"), "enabled")
        # The Campaign join canary on 2026-10-02 opened batch joins for Germany.
        self.assertEqual(MARKETS["de"].capabilities.status("campaign_join"), "enabled")


if __name__ == "__main__":
    unittest.main()
