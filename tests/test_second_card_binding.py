"""Card provenance and refresh contracts with synthetic fixtures only."""
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from lib.italy_cards import PID, LIST_ID, CAMPAIGN_ID, CARD_PATH, MEMBERS_PATH, combine_card_facts
from lib.italy_im_auth import ImProbeDeadline
from lib.italy_im_delivery import CARD_ORIGIN, CARD_TITLE_KEY, card_binding_sha256
from lib.second_card_binding import (MAX_FACTS_BYTES, SecondCardBindingError, card_from_facts,
    commercial_facts_from_facts, load_card_facts, refresh_card_binding, validate_promotion_claims)

TIME = "2026-09-12T16:00:01.402583+00:00"
LATER = "2026-09-12T16:00:02.321307+00:00"


def facts(*, im_stock="1355", member_stock="1355"):
    listing = {"product_list_id": LIST_ID, "product_list_name": "BJN | Freegrin fixture ",
               "campaign_id": "0", "campaign_name": None,
               "campaign_products": [{"product_id": PID, "product_name": "Freegrin fixture",
                                      "stock": im_stock, "creator_commission_percent": "1200"}]}
    member = {"product_id": PID, "campaign_id": CAMPAIGN_ID, "product_name": "Freegrin fixture",
              "stock": member_stock, "product_status": 2, "creator_commission_percent": "1200",
              "plan_commission_percent": "1000", "total_commission_percent": "1500"}
    return combine_card_facts([listing], [member], im_observed_at=TIME, members_observed_at=LATER)


class SecondCardBindingTests(unittest.TestCase):
    def test_fixed_benefit_uses_fresh_rates_without_requiring_original_numbers(self):
        claim=[{'kind':'commission_above_public','pid':PID}]
        current=commercial_facts_from_facts(facts())
        validate_promotion_claims(claim,current)
        current['sources']['membership']['creatorCommission']['percent']='13'
        validate_promotion_claims(claim,current)
        current['sources']['membership']['creatorCommission']['percent']='10'
        self.assert_code('promotion_condition_changed',lambda:validate_promotion_claims(claim,current))

    def test_unknown_or_conflicting_current_card_rate_cannot_support_higher_commission(self):
        claim=[{'kind':'commission_above_public','pid':PID}]
        current=commercial_facts_from_facts(facts())
        current['sources']['membership']['publicCommission']['state']='missing'
        self.assert_code('promotion_condition_changed',lambda:validate_promotion_claims(claim,current))
        current=commercial_facts_from_facts(facts());current['sources']['im']['creatorCommission']['percent']='9'
        self.assert_code('promotion_condition_changed',lambda:validate_promotion_claims(claim,current))
        self.assert_code('promotion_condition_changed',lambda:validate_promotion_claims([{'kind':'commission_above_public','pid':'other'}],current))

    def assert_code(self, code, callback):
        with self.assertRaises(SecondCardBindingError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)

    def test_mx_governance_and_unavailable_flags_block_even_when_binding_matches(self):
        for key in ("imProduct", "membershipProduct"):
            governed=facts();governed[key]["isUnderGoverned"]=True
            self.assert_code("card_under_governance",lambda:card_from_facts(governed))
            unavailable=facts();unavailable[key]["unavailableType"]={"state":"observed","raw":"1","value":"1"}
            self.assert_code("card_unavailable",lambda:card_from_facts(unavailable))
        self.assertTrue(card_from_facts(facts()).verified)

    def test_invalid_stock_does_not_become_an_unknown_but_usable_stock(self):
        self.assert_code("card_stock_invalid",lambda:card_from_facts(facts(im_stock="not-a-number")))
        self.assertTrue(card_from_facts(facts(im_stock=None,member_stock=None)).verified)

    def test_card_preserves_wire_campaign_zero_names_and_canonical_evidence_hash(self):
        source = facts(); original = deepcopy(source)
        card = card_from_facts(source)
        self.assertEqual(card.campaign_id, "0")
        self.assertEqual(card.product_id, PID); self.assertEqual(card.list_id, LIST_ID)
        self.assertEqual(card.campaign_name, ""); self.assertEqual(card.list_name, "BJN | Freegrin fixture ")
        self.assertEqual(card.origin, CARD_ORIGIN); self.assertEqual(card.title_key, CARD_TITLE_KEY)
        self.assertEqual(card.verified_at, LATER)
        self.assertEqual(card.binding_sha256, card_binding_sha256(card))
        self.assertEqual(card.evidence_sha256, hashlib.sha256(json.dumps(source, sort_keys=True,
            ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest())
        self.assertNotIn("sourceCampaignId", asdict(card))
        self.assertEqual(source, original)
        reversed_keys = {key: source[key] for key in reversed(source)}
        self.assertEqual(card_from_facts(reversed_keys), card)

    def test_scope_and_membership_must_match_actual_fixed_read(self):
        paths = [("market",), ("account",), ("expected", "pid"), ("expected", "listId"),
                 ("expected", "campaignId"), ("expected", "source"), ("card", "pid"),
                 ("card", "listId"), ("card", "sourceCampaignId"), ("card", "imListCampaignId"),
                 ("imProduct", "pid"), ("imProduct", "campaignId"),
                 ("membershipProduct", "pid"), ("membershipProduct", "campaignId")]
        for path in paths:
            with self.subTest(path=path):
                source = facts(); target = source
                for key in path[:-1]: target = target[key]
                target[path[-1]] = "999"
                self.assert_code("card_facts_scope_mismatch", lambda: card_from_facts(source))
        source = facts(); source["imProduct"]["campaignId"] = CAMPAIGN_ID
        self.assertEqual(card_from_facts(source).campaign_id, "0")
        source["card"]["imListCampaignId"] = None
        self.assert_code("card_facts_scope_mismatch", lambda: card_from_facts(source))

    def test_binding_flags_counts_and_schema_are_not_replaceable_by_truthy_values(self):
        for key, value in (("schema", "other.v1"), ("platformWrites", 1), ("sendRequests", False), ("browserInitializations", "0")):
            source = facts(); source[key] = value
            self.assert_code("card_facts_invalid", lambda: card_from_facts(source))
        for key, value in (("bindingVerified", 1), ("status", "binding_not_verified"), ("bindingEvidence", None)):
            source = facts(); source[key] = value
            self.assert_code("card_facts_unverified", lambda: card_from_facts(source))
        for key in facts()["checks"]:
            source = facts(); source["checks"][key] = 1
            self.assert_code("card_facts_unverified", lambda: card_from_facts(source))
        for value in (0, 2, True, "1"):
            source = facts(); source["counts"]["matchingMembers"] = value
            self.assert_code("card_facts_unverified", lambda: card_from_facts(source))

    def test_endpoint_and_timestamp_provenance_is_required(self):
        for product in ("imProduct", "membershipProduct"):
            for value in (None, "invalid", "2026-09-12T16:00:00", "PRIVATE_TIMESTAMP"):
                source = facts(); source[product]["source"]["observedAt"] = value
                self.assert_code("card_facts_invalid", lambda: card_from_facts(source))
            source = facts(); source[product]["source"]["endpointPath"] = "/some/other/endpoint"
            self.assert_code("card_facts_invalid", lambda: card_from_facts(source))
        source = facts(); source["imProduct"]["source"]["observedAt"] = "2026-09-13T00:00:03+08:00"
        self.assertEqual(card_from_facts(source).verified_at, source["imProduct"]["source"]["observedAt"])

    def test_known_zero_negative_stock_rejects_card_but_ui_retains_facts(self):
        for key in ("im_stock", "member_stock"):
            for stock in ("0", "-1", -2):
                with self.subTest(source=key, stock=stock):
                    source = facts(**{key: stock})
                    self.assert_code("card_stock_unavailable", lambda: card_from_facts(source))
                    self.assertTrue(commercial_facts_from_facts(source)["knownStockBlocksCard"])
        source = facts(im_stock="1", member_stock="2")
        self.assertTrue(card_from_facts(source).verified)
        self.assertFalse(commercial_facts_from_facts(source)["knownStockBlocksCard"])

    def test_unknown_stock_and_commercial_values_remain_unknown_without_new_requirements(self):
        source = facts(im_stock=None, member_stock=None)
        source["membershipProduct"]["creatorCommission"] = {"state": "missing", "rawHundredthsOfPercent": None, "percent": None}
        card = card_from_facts(source); view = commercial_facts_from_facts(source)
        self.assertTrue(card.verified); self.assertFalse(view["knownStockBlocksCard"])
        self.assertEqual(view["sources"]["im"]["stock"], {"state": "missing", "raw": None, "value": None})
        self.assertEqual(view["sources"]["membership"]["stock"], {"state": "missing", "raw": None, "value": None})
        self.assertIsNone(view["sources"]["membership"]["creatorCommission"]["percent"])
        self.assertIsNone(view["sources"]["im"]["isUnderGoverned"])
        self.assertEqual(view["sources"]["membership"]["productStatus"], "2")
        self.assertIsNone(view["campaignName"])
        self.assertNotIn("sample", json.dumps(view).lower())
        self.assertNotIn("commissionRaised", view); self.assertNotIn("sendReady", view)

    def test_commercial_projection_keeps_rates_and_both_campaign_meanings_separate(self):
        source = facts(); view = commercial_facts_from_facts(source)
        self.assertEqual(view["imWireCampaignId"], "0"); self.assertEqual(view["sourceCampaignId"], CAMPAIGN_ID)
        self.assertEqual(view["sources"]["im"]["creatorCommission"]["percent"], "12")
        self.assertIsNone(view["sources"]["im"]["publicCommission"]["percent"])
        self.assertEqual(view["sources"]["membership"]["publicCommission"]["percent"], "10")
        self.assertEqual(view["sources"]["membership"]["totalCommission"]["percent"], "15")
        view["sources"]["membership"]["stock"]["value"] = "0"
        self.assertEqual(source["membershipProduct"]["stock"]["value"], "1355")
        for name in ("creatorCommission", "publicCommission", "totalCommission"):
            source["membershipProduct"][name] = {"state": "observed", "rawHundredthsOfPercent": "0", "percent": "0"}
        self.assertTrue(card_from_facts(source).verified)

    def test_malformed_numeric_or_name_facts_never_become_a_descriptor(self):
        corruptions = [lambda s: s["membershipProduct"]["stock"].update(value="1"),
                       lambda s: s["membershipProduct"]["creatorCommission"].update(percent="1200"),
                       lambda s: s["membershipProduct"]["stock"].update(raw="NaN", value="NaN"),
                       lambda s: s["membershipProduct"]["stock"].update(raw="1.5", value="1.5"),
                       lambda s: s["membershipProduct"]["stock"].update(state="missing"),
                       lambda s: s["membershipProduct"]["stock"].update(state={}),
                       lambda s: s["membershipProduct"].pop("productName"),
                       lambda s: s["card"].update(listName=None),
                       lambda s: s["card"].update(campaignName="bad\x00name"),
                       lambda s: s.update(extra=float("nan"))]
        for corrupt in corruptions:
            source = facts(); corrupt(source)
            self.assert_code("card_facts_invalid", lambda: card_from_facts(source))
        source = facts(); source["card"].pop("campaignName")
        self.assert_code("card_facts_scope_mismatch", lambda: card_from_facts(source))
        source = facts(); source["membershipProduct"]["campaignId"] = []
        self.assert_code("card_facts_scope_mismatch", lambda: card_from_facts(source))

    def test_refresh_ignores_observation_and_rate_changes_but_returns_latest_evidence(self):
        before = card_from_facts(facts()); refreshed = facts(member_stock="1400")
        refreshed["membershipProduct"]["source"]["observedAt"] = "2026-09-13T12:00:00Z"
        refreshed["membershipProduct"]["creatorCommission"] = {"state": "observed", "rawHundredthsOfPercent": "1250.5", "percent": "12.505"}
        collector = Mock(return_value=refreshed)
        account, identity, headers, report, maintenance, stopped, on_update = [object() for _ in range(7)]
        with patch("socket.socket", side_effect=AssertionError("no network")):
            current, commercial, evidence = refresh_card_binding(before, account, identity, headers, report,
                collector=collector, maintenance_due=maintenance, stopped=stopped, on_update=on_update)
        collector.assert_called_once_with(account, identity, headers, report,
            maintenance_due=maintenance, stopped=stopped, on_update=on_update)
        self.assertEqual(current.binding_sha256, before.binding_sha256)
        self.assertNotEqual(current.evidence_sha256, before.evidence_sha256)
        self.assertNotEqual(current.verified_at, before.verified_at)
        self.assertEqual(commercial["sources"]["membership"]["creatorCommission"]["percent"], "12.505")
        self.assertEqual(evidence, refreshed)
        evidence["card"]["listName"] = "changed externally"
        self.assertNotEqual(evidence, refreshed)

    def test_refresh_rejects_business_binding_change_or_stock_loss_without_retry(self):
        before = card_from_facts(facts())
        for key in ("listName", "campaignName"):
            source = facts(); source["card"][key] = "New business name"
            collector = Mock(return_value=source)
            self.assert_code("card_binding_changed", lambda: refresh_card_binding(before, None, None, None, {}, collector=collector))
            collector.assert_called_once()
        collector = Mock(return_value=facts(member_stock="0"))
        self.assert_code("card_stock_unavailable", lambda: refresh_card_binding(before, None, None, None, {}, collector=collector))
        collector.assert_called_once()
        collector = Mock(side_effect=ImProbeDeadline())
        with self.assertRaises(ImProbeDeadline): refresh_card_binding(before, None, None, None, {}, collector=collector)
        collector.assert_called_once()

    def test_load_is_bounded_to_new_var_and_does_not_import_live_data(self):
        with TemporaryDirectory() as directory:
            root = Path(directory); var = root / "var"; var.mkdir()
            target = var / "facts.json"; target.write_text(json.dumps(facts()), encoding="utf-8")
            self.assertEqual(load_card_facts(target, var_root=var), facts())
            self.assertEqual(load_card_facts("facts.json", var_root=var), facts())
            outside = root / "outside.json"; outside.write_text(target.read_text())
            self.assert_code("card_facts_path_invalid", lambda: load_card_facts(outside, var_root=var))
            link = var / "link.json"; link.symlink_to(outside)
            self.assert_code("card_facts_path_invalid", lambda: load_card_facts(link, var_root=var))
            target.write_bytes(b" " * (MAX_FACTS_BYTES + 1))
            self.assert_code("card_facts_file_invalid", lambda: load_card_facts(target, var_root=var))
            target.write_text("not JSON")
            self.assert_code("card_facts_file_invalid", lambda: load_card_facts(target, var_root=var))
            target.write_text('{"schema": "old", "schema": "new"}')
            self.assert_code("card_facts_file_invalid", lambda: load_card_facts(target, var_root=var))
            fifo = var / "pipe"; os.mkfifo(fifo)
            self.assert_code("card_facts_file_invalid", lambda: load_card_facts(fifo, var_root=var))
            target.write_text(json.dumps({"schema": "unrelated"}))
            self.assert_code("card_facts_invalid", lambda: load_card_facts(target, var_root=var))


if __name__ == "__main__":
    unittest.main()
