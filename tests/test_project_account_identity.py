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



class PrepareProfileTests(unittest.TestCase):
    def test_new_generation_copies_login_state_but_not_rebuildable_caches(self):
        with tempfile.TemporaryDirectory() as temp:
            current = Path(temp) / "current"
            for rel in ("Default/Cookies", "Default/Local Storage/leveldb/000.log",
                        "Default/Service Worker/Database/LOG", "Default/Service Worker/CacheStorage/x/blob",
                        "Default/Cache/Cache_Data/data_0", "Default/Code Cache/js/index",
                        "BrowserMetrics/m.pma", ".bdhub.lease.guard"):
                (current / rel).parent.mkdir(parents=True, exist_ok=True)
                (current / rel).write_text("x")
            adapter = identity.ProjectAccountIdentityAdapter(Path(temp))
            target = Path(temp) / "candidate"
            with mock.patch.object(identity, "project_identity_paths", return_value={"profileDir": current}):
                adapter._prepare_profile("acc8", target)
            copied = {str(p.relative_to(target)) for p in target.rglob("*") if p.is_file()}
        self.assertEqual(copied, {"Default/Cookies", "Default/Local Storage/leveldb/000.log",
                                  "Default/Service Worker/Database/LOG", ".bdhub.lease.guard"})


class CampaignValidationTests(unittest.TestCase):
    def test_explicit_integer_zero_proves_an_omitted_or_null_list_is_empty(self):
        for data in ({"total": 0}, {"total_num": 0, "campaign": None},
                     {"total": 0, "total_num": 0}, {"total_num": 0, "campaign": []}):
            with self.subTest(data=data):
                self.assertEqual(identity._validated_campaign_rows(data), [])
        rows = [{"campaign_id": "123"}]
        self.assertIs(identity._validated_campaign_rows({"campaign": rows}), rows)

    def test_unknown_counts_and_malformed_lists_are_not_empty_success(self):
        for data in (None, [], {}, {"total": False}, {"total": "0"}, {"total": 0.0},
                     {"total": -1}, {"total_num": 1}, {"total": None},
                     {"total": 0, "total_num": 1}, {"total": 0, "total_num": False},
                     {"total": 0, "campaign": {}}, {"total": 0, "campaign": ""}):
            with self.subTest(data=data), self.assertRaisesRegex(ValueError, "campaign_read_invalid"):
                identity._validated_campaign_rows(data)

    def test_failure_diagnostic_contains_only_whitelisted_structure(self):
        try:
            identity._validated_campaign_rows({"total": "secret", "campaign": {"token": "secret"},
                                               "cookie": "secret"})
        except identity.CampaignReadInvalid as error:
            diagnostic = error.diagnostic
        self.assertEqual(diagnostic, {"endpoint": "campaign/list", "dataType": "dict",
                                     "campaignPresent": True, "campaignType": "dict",
                                     "countFields": {"total": {"type": "str", "value": None}}})
        self.assertNotIn("secret", json.dumps(diagnostic))

    def test_nonzero_remote_code_never_reaches_empty_campaign_compatibility(self):
        from types import SimpleNamespace
        from lib.legacy_runtime import configure_vendored_bdhub
        configure_vendored_bdhub(root=ROOT)
        from bdhub.hub import markets
        from bdhub.research import commerce_transport
        from bdhub.send.taplink.transport import TapLinkTransport
        # The real read boundary rejects the remote result before the empty-list parser runs.
        class Reader:
            require_read = staticmethod(TapLinkTransport.require_read)
            def __init__(self, *args, **kwargs):
                self.session = SimpleNamespace(trust_env=False, close=lambda: None)
            def _xhr(self, **kwargs):
                return SimpleNamespace(http_status=200, code=16201010, ambiguous=False,
                                       has_turing=False, system_error_3=False,
                                       payload={"code": 16201010, "data": {"total": 0}})
        wire = SimpleNamespace(require_product_search=lambda: wire, aid=1, partner_id="2")
        with mock.patch.object(commerce_transport, "CommerceTransport", Reader), \
             mock.patch.object(markets, "identity_for", return_value=wire), \
             mock.patch.object(identity, "_validated_campaign_rows") as parse:
            with self.assertRaisesRegex(ValueError, "taplink_remote_read_failed"):
                identity._validate_candidate({}, object(), "br", "supply", "candidate", full_managed=False)
            parse.assert_not_called()


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

    def test_france_is_market_region_17_with_campaign_joins_enabled_after_its_canary(self):
        from lib.legacy_runtime import configure_vendored_bdhub
        configure_vendored_bdhub(root=ROOT)
        from bdhub.hub.markets import MARKETS
        france = MARKETS["fr"]
        self.assertEqual((france.im_market, france.im_market_partner_id, france.signer_region),
                         ("17", "8683739727707539216", "eu"))
        self.assertEqual(france.capabilities.status("product_search"), "enabled")
        self.assertEqual(france.capabilities.status("campaign_join"), "enabled")
        from bdhub.research.commerce_transport import require_market
        require_market("fr")


if __name__ == "__main__":
    unittest.main()
