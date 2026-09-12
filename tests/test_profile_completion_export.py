"""Pure checks for fixed-cohort export; no database or files are written."""
import importlib.util
import json
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1] / "scripts/export-italy-profile-completion.py"
SPEC = importlib.util.spec_from_file_location("completion_export", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class CompletionExportTests(unittest.TestCase):
    def setUp(self):
        self.time = "2026-07-30T05:00:00+00:00"
        self.expected = {"123": {"oec_id": "123", "handle": "alice", "captured_at": self.time}}
        def f(value):
            return {"value": value, "status": 0, "is_authorized": True}
        self.row = {"oec_id": "123", "snapshot_id": "snapshot-1", "captured_at": self.time,
                    "profile": {"creator_oecuid": f("123"), "handle": f("alice"), "selection_region": f("IT"),
                                "follower_cnt": f(0), "product_price_range": {"status": 0},
                                "content_groups": f([{"key": "10", "value": "1.0"}]),
                                "top_video_data": f([{"name": "SECRET", "video": {"post_url": "SECRET"}}])}}

    def test_coverage_hash_and_privacy(self):
        result = MODULE.build_document(self.expected, [self.row])
        self.assertEqual(result["coverage"]["contentGroupsAvailable"], 1)
        self.assertEqual(result["coverage"]["topVideoCountAvailable"], 1)
        self.assertEqual(result["coverage"]["fieldStates"]["follower_cnt"]["zero"], 1)
        self.assertEqual(result["coverage"]["fieldStates"]["product_price_range"]["no_value"], 1)
        self.assertEqual(result["provenance"]["recordsSha256"], MODULE.digest(result["records"]))
        self.assertNotIn("SECRET", json.dumps(result))
        self.assertEqual(result["source"], "existing_local_snapshot")
        self.assertFalse(result["liveProfileRefresh"])
        self.assertEqual(result["platformRequests"], 0)

    def test_missing_duplicate_or_wrong_version_are_rejected(self):
        for rows in ([], [self.row, self.row], [{**self.row, "captured_at": "2026-07-31T05:00:00+00:00"}]):
            with self.subTest(rows=len(rows)), self.assertRaises(ValueError):
                MODULE.build_document(self.expected, rows)

    def test_identity_disagreement_is_rejected(self):
        for key, value in (("creator_oecuid", "456"), ("selection_region", "MX"), ("handle", "bob")):
            row = {**self.row, "profile": {**self.row["profile"], key: {"value": value}}}
            with self.subTest(key=key), self.assertRaises(ValueError):
                MODULE.build_document(self.expected, [row])

    def test_sql_projects_only_allowlisted_raw_fields(self):
        query = MODULE.profile_select_sql()
        self.assertNotIn("SELECT *", query)
        self.assertNotIn("raw_json AS", query)
        for forbidden in ("'bio'", "'email'", "'avatar'", "'contact'", "'token'"):
            self.assertNotIn(forbidden, query)
        for key in MODULE.PROFILE_FIELDS:
            self.assertIn(f"raw_json -> '{key}' AS {key}", query)
        self.assertIn("oec_id IN :oec_ids", query)
        self.assertIn("captured_at <= :cohort_end", query)


if __name__ == "__main__":
    unittest.main()
