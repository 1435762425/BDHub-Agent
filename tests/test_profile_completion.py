"""Offline contract tests for minimum-data Profile completion diagnostics."""
import importlib.util
import json
from pathlib import Path
import unittest


PATH = Path(__file__).resolve().parents[1] / "scripts/lib/profile_completion.py"
SPEC = importlib.util.spec_from_file_location("profile_completion", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
summarize_profile = MODULE.summarize_profile
merge_profile_summaries = MODULE.merge_profile_summaries


def field(value):
    return {"value": value, "status": 0, "is_authorized": True}


def profile(oec="123", market="IT", **extra):
    return {"creator_oecuid": field(oec), "selection_region": field(market), **extra}


class ProfileCompletionTests(unittest.TestCase):
    def test_absent_empty_shell_and_explicit_error_are_different(self):
        result = summarize_profile({"product_price_range": {"status": 0, "is_authorized": True},
                                    "gpm": {"status": 1, "value": 10}})
        self.assertEqual(result["fields"]["follower_cnt"], {"status": "absent"})
        self.assertEqual(result["fields"]["product_price_range"], {"status": "no_value"})
        self.assertEqual(result["fields"]["gpm"], {"status": "error"})

    def test_unauthorized_even_with_value_never_exposes_value(self):
        result = summarize_profile({"follower_cnt": {"is_authorized": False, "value": 100},
                                    "gpm": field({"is_authorized": False, "value": 12})})
        self.assertEqual(result["fields"]["follower_cnt"], {"status": "unauthorized"})
        self.assertEqual(result["fields"]["gpm"], {"status": "unauthorized"})
        self.assertEqual(result["availableFields"], [])

    def test_numeric_and_nested_zero_are_available(self):
        result = summarize_profile({"follower_cnt": field(0), "units_sold": field({"value": "0"}),
                                    "med_gmv_revenue": field({"value": {"value": "0.00", "symbol": "€", "format": "€0"}})})
        for name in ("follower_cnt", "units_sold", "med_gmv_revenue"):
            self.assertEqual(result["fields"][name]["status"], "zero")
            self.assertIn(name, result["availableFields"])
        self.assertEqual(result["fields"]["med_gmv_revenue"]["value"],
                         {"decimal": "0.00", "rawSymbol": "€", "format": "€0"})

    def test_money_exact_decimal_and_range_are_preserved(self):
        result = summarize_profile({"med_gmv_revenue": field({"value": "9007199254740993.01", "symbol": "$"}),
                                    "ec_live_gpm": field({"value": {"minimal": "2.5", "maximum": "8.8"}})})
        self.assertEqual(result["fields"]["med_gmv_revenue"]["value"],
                         {"decimal": "9007199254740993.01", "rawSymbol": "$"})
        self.assertEqual(result["fields"]["ec_live_gpm"]["value"], {"minimum": "2.5", "maximum": "8.8"})
        self.assertNotIn("currency", json.dumps(result))
        self.assertNotIn("period", json.dumps(result))

    def test_non_finite_negative_fractional_counts_and_bad_ranges_rejected(self):
        for value in ("NaN", "Infinity", -1, True, "1.2", "1e999999999"):
            with self.subTest(value=value):
                self.assertEqual(summarize_profile({"follower_cnt": field(value)})["fields"]["follower_cnt"]["status"], "error")
        result = summarize_profile({"gpm": field({"minimal": "9", "maximum": "2"})})
        self.assertEqual(result["fields"]["gpm"]["status"], "error")

    def test_privacy_allowlist_sanitizes_nested_objects(self):
        secret = "secret-that-must-not-survive"
        result = summarize_profile(profile(handle=field("alice"), bio=secret, email=secret, token=secret,
            avatar=secret, industry_groups=field([{"key": "10", "name": "Beauty", "value": "0.5", "email": secret}]),
            content_groups=field([{"key": "2", "value": "0.25", "token": secret}]),
            top_video_data=field([{"name": secret, "item_id": secret, "play_cnt": 5,
                                   "video": {"post_url": secret, "video_infos": [{"main_url": secret}]}, "token": secret}]),
            partnered_brand={"value": True, "brand": [{"id": "1", "name": "Brand", "token": secret}]}))
        self.assertNotIn(secret, json.dumps(result))
        self.assertEqual(set(result["fields"]), set(MODULE.PROFILE_FIELDS))
        self.assertEqual(result["fields"]["industry_groups"]["value"], [{"id": "10", "label": "Beauty", "weight": "0.5"}])
        self.assertEqual(result["fields"]["top_video_data"]["value"],
                         {"count": 1, "structureKeys": ["item_id", "name", "play_cnt", "video"]})
        self.assertEqual(result["fields"]["partnered_brand"]["value"],
                         {"hasBrands": True, "brands": [{"id": "1", "label": "Brand"}]})

    def test_merge_rejects_oec_or_market_conflict(self):
        first = summarize_profile(profile(follower_cnt=field(10)))
        for second in (profile("456"), profile(market="MX")):
            with self.assertRaisesRegex(ValueError, "identity conflict"):
                merge_profile_summaries([first, summarize_profile(second)])

    def test_merge_allows_missing_identity_and_keeps_actual_values(self):
        first = summarize_profile(profile(handle=field("old_handle"), follower_cnt=field(10)))
        second = summarize_profile({"handle": field("new_handle"), "follower_cnt": {"status": 0}, "units_sold": field(0)})
        third = summarize_profile(profile(market="it", follower_cnt=field(20)))
        merged = merge_profile_summaries([first, second, third])
        self.assertEqual(merged["identity"], {"oecId": "123", "handle": "new_handle", "market": "it"})
        self.assertEqual(merged["fields"]["follower_cnt"], {"status": "value", "value": 20})
        self.assertEqual(merged["fields"]["units_sold"], {"status": "zero", "value": 0})
        self.assertEqual(first["fields"]["follower_cnt"]["value"], 10)
        self.assertEqual(merge_profile_summaries([first, second])["fields"]["follower_cnt"]["value"], 10)

    def test_merge_empty_and_invalid_summary(self):
        self.assertEqual(merge_profile_summaries([]), summarize_profile({}))
        summary = summarize_profile(profile())
        summary["identity"]["oecId"] = "999"
        with self.assertRaisesRegex(ValueError, "identity"):
            merge_profile_summaries([summary])

    def test_nested_shell_and_errors_keep_their_status(self):
        result = summarize_profile({"follower_cnt": field({"status": 0}),
                                    "units_sold": field({"value": 1, "status": 7}),
                                    "gpm": field({"value": {"status": 0}})})
        self.assertEqual(result["fields"]["follower_cnt"]["status"], "no_value")
        self.assertEqual(result["fields"]["units_sold"]["status"], "error")
        self.assertEqual(result["fields"]["gpm"]["status"], "no_value")


if __name__ == "__main__":
    unittest.main()
