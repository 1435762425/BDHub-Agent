"""Deterministic template tests. No provider, database, filesystem or network use."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from lib.second_templates import SecondTemplateError, list_templates, render_template as raw_render_template
from lib.second_outreach_source import ITALIAN_PRODUCT_NAMES


def product(pid="1729480019490150432"):
    return {"id": "product-" + pid, "pid": pid, "title": "源商品标题", "nameIt": ITALIAN_PRODUCT_NAMES[pid]}


def promotion_facts(**changes):
    return {pid: {"creatorPercent": "12", "publicPercent": "10", "sourceRef": "fixture:card-readback-" + pid,
                  "observedAt": "2026-09-13T00:00:00Z", **changes} for pid in ITALIAN_PRODUCT_NAMES}


def render_template(template_id, handle, products, **options):
    """Tests explicitly supply a known higher commission unless testing absence."""
    options.setdefault("promotion_facts", promotion_facts())
    return raw_render_template(template_id, handle, products, **options)


class SecondTemplateTests(unittest.TestCase):
    def test_three_fixed_templates_have_stable_versions_and_fingerprints(self):
        templates = list_templates()
        self.assertEqual(len(templates), 3)
        self.assertEqual({row["id"] for row in templates}, {"it-second-brief", "it-second-choice", "it-second-explore"})
        self.assertTrue(all(row["version"] == 3 and len(row["fingerprint"]) == 64 for row in templates))
        self.assertTrue(all(row["promotionReason"] == "commission_advantage" for row in templates))
        templates[0]["textIt"] = "not the template"
        self.assertNotEqual(list_templates()[0]["textIt"], "not the template")
        self.assertEqual(list_templates(), list_templates())

    def test_italian_and_chinese_names_match_each_verified_product(self):
        chinese = {"1729779362302171335": "Freegrin口腔片", "1729480019490150432": "颈枕", "1729502070782139035": "瑜伽裤",
                   "1729584712875612996": "女士塑身衣", "1729480472768911992": "NJTY T3数字万用表"}
        for template in list_templates():
            for pid, italian in ITALIAN_PRODUCT_NAMES.items():
                result = render_template(template["id"], "@creator_test", [product(pid)])
                self.assertIn(italian, result["textIt"])
                self.assertIn(chinese[pid], result["translationZh"])
                self.assertEqual(result["variables"]["productNamesIt"], [italian])
                self.assertEqual(result["variables"]["productNamesZh"], [chinese[pid]])
                self.assertEqual(result["templateFingerprint"], template["fingerprint"])

    def test_single_two_and_three_product_connection_is_deterministic(self):
        products = [product(pid) for pid in ("1729480019490150432", "1729502070782139035", "1729480472768911992")]
        for template in list_templates():
            two = render_template(template["id"], None, products[:2])
            self.assertIn("al cuscino cervicale e ai leggings da yoga" if template["id"] == "it-second-brief" else "il cuscino cervicale e i leggings da yoga", two["textIt"])
            self.assertIn("颈枕和瑜伽裤", two["translationZh"])
            three = render_template(template["id"], None, products)
            self.assertIn("al cuscino cervicale, ai leggings da yoga e al multimetro digitale NJTY T3" if template["id"] == "it-second-brief" else "il cuscino cervicale, i leggings da yoga e il multimetro digitale NJTY T3", three["textIt"])
            self.assertIn("颈枕、瑜伽裤和NJTY T3数字万用表", three["translationZh"])
            self.assertEqual(render_template(template["id"], None, products), three)

    def test_optional_handle_has_no_placeholder_or_gender_assumption(self):
        absent = render_template("it-second-brief", None, [product()])
        self.assertTrue(absent["textIt"].startswith("Ciao!"))
        self.assertTrue(absent["translationZh"].startswith("你好！"))
        self.assertNotIn("@", absent["textIt"])
        named = render_template("it-second-brief", " @Creator.Test ", [product()])
        self.assertTrue(named["textIt"].startswith("Ciao @creator.test!"))
        self.assertEqual(named["variables"]["recipientHandle"], "creator.test")
        for handle in ("", "@", "hello world", "alice!", "alice\n20%", "https://example.invalid", {"name": "alice"}):
            with self.assertRaises(SecondTemplateError): render_template("it-second-brief", handle, [product()])

    def test_unverified_product_name_duplicate_pid_and_counts_are_rejected(self):
        for item in ({**product(), "nameIt": "commissione del 20%"}, {**product(), "pid": "999"}, {**product(), "pid": 1729480019490150432}):
            with self.assertRaises(SecondTemplateError) as found: render_template("it-second-brief", None, [item])
            self.assertEqual(found.exception.code, "template_product_name_unverified")
        with self.assertRaises(SecondTemplateError) as found: render_template("it-second-brief", None, [product(), product()])
        self.assertEqual(found.exception.code, "template_duplicate_product")
        for products in ([], [product()] * 4, None):
            with self.assertRaises(SecondTemplateError): render_template("it-second-brief", None, products)
        with self.assertRaises(SecondTemplateError): render_template("unknown", None, [product()])

    def test_free_commercial_variables_cannot_be_injected(self):
        for key in ("commission", "price", "sample", "stock", "brandIdentity", "url", "variables"):
            with self.assertRaises(SecondTemplateError) as found:
                render_template("it-second-brief", None, [{**product(), key: "arbitrary commercial claim"}])
            self.assertEqual(found.exception.code, "template_product_fields_invalid")
        with self.assertRaises(TypeError): render_template("it-second-brief", None, [product()], commission="20%")
        ordinary = render_template("it-second-brief", None, [product()])
        poisoned = {**product(), "title": "PRIVATE_RAW_TITLE 免费样品佣金20%", "units": 9999, "sourceRef": "PRIVATE_SOURCE_REF",
                    "windowStart": 1, "windowEnd": 2, "windowBasis": None, "observedAt": 2}
        self.assertEqual(render_template("it-second-brief", None, [poisoned]), ordinary)
        self.assertEqual(set(ordinary["variables"]), {"recipientHandle", "productNamesIt", "productNamesZh"})

    def test_text_hash_tracks_copy_while_above_public_rate_changes_only_update_evidence(self):
        result = render_template("it-second-brief", "alice", [product()])
        self.assertEqual(result["textSha256"], hashlib.sha256(result["textIt"].encode("utf-8")).hexdigest())
        self.assertEqual(render_template("it-second-brief", "@ALICE", [product()])["textSha256"], result["textSha256"])
        other = render_template("it-second-brief", "bob", [product()])
        self.assertNotEqual(other["textSha256"], result["textSha256"])
        self.assertEqual(other["templateFingerprint"], result["templateFingerprint"])
        raised = render_template("it-second-brief", "alice", [product()], promotion_facts=promotion_facts(creatorPercent="13", observedAt="2026-09-13T01:00:00Z"))
        self.assertEqual(result["textSha256"], raised["textSha256"])
        self.assertEqual(result["textIt"], raised["textIt"])
        self.assertNotEqual(result["promotionClaims"], raised["promotionClaims"])

    def test_templates_are_bounded_repush_copy_without_invented_terms_contacts_season_or_sales(self):
        products = [product(pid) for pid in list(ITALIAN_PRODUCT_NAMES)[:3]]
        for template in list_templates():
            result = render_template(template["id"], "a" * 64, products)
            self.assertLessEqual(len(result["textIt"]), 900)
            self.assertLessEqual(len(result["translationZh"]), 900)
            for forbidden in ("http", "whatsapp", "correo", "@gmail", "+86", "stock", "%", "ho visto", "hai venduto", "vendite", "agosto", "scuola", "regreso", "nostro brand", "signora", "signore", "campione gratuito", "ti ho aumentato", "tua vecchia commissione", "已经合作", "看过", "卖过", "八月", "返校", "销量", "已帮你提升", "原佣金", "库存", "免费样品", "保证寄样", "请在", "聊聊合作"):
                self.assertNotIn(forbidden, result["textIt"].lower() + result["translationZh"])
            self.assertNotIn("{", result["textIt"])
            self.assertIn("commission", result["textIt"])
            self.assertIn("piani pubblici", result["textIt"])
            self.assertIn("佣金比各自的公开计划更高", result["translationZh"])


    def test_user_source_and_card_before_text_are_explicit_delivery_requirements(self):
        products = [product(pid) for pid in list(ITALIAN_PRODUCT_NAMES)[:3]]
        for template in list_templates():
            self.assertEqual(template["source"], "user_mx_second_template_20260912")
            self.assertTrue(template["requiresCard"])
            self.assertEqual(template["deliveryOrder"], "card_then_text")
            self.assertIn("MX", template["description"])
            for count in (1, 2, 3):
                result = render_template(template["id"], None, products[:count])
                self.assertEqual(result["templateVersion"], 3)
                self.assertTrue(result["requiresCard"])
                self.assertEqual(result["requiredCardPids"], [p["pid"] for p in products[:count]])
                self.assertEqual(result["deliveryOrder"], "card_then_text")
                self.assertEqual(result["source"], template["source"])
                self.assertIn("BJN", result["textIt"])
                self.assertIn("qui sopra", result["textIt"])
                self.assertIn("vetrina", result["textIt"])
                self.assertIn("video o LIVE", result["textIt"])
                self.assertTrue("un'altra spinta" in result["textIt"] or "riproporre" in result["textIt"] or "riprendere" in result["textIt"])
                self.assertEqual(result["promotionReason"], "commission_advantage")
                self.assertEqual([claim["pid"] for claim in result["promotionClaims"]], result["requiredCardPids"])

    def test_link_and_sample_wording_follow_sku_count_with_conditional_sample_only(self):
        one = [product("1729502070782139035")]  # Plural Italian name, but a single SKU/card.
        many = one + [product("1729480019490150432")]
        for template in list_templates():
            singular = render_template(template["id"], None, one)
            plural = render_template(template["id"], None, many)
            self.assertIn("Il link è qui sopra", singular["textIt"])
            self.assertIn("aggiungilo", singular["textIt"])
            self.assertIn("usa quello", singular["textIt"])
            self.assertNotIn("aggiungili", singular["textIt"])
            self.assertIn("I link sono qui sopra", plural["textIt"])
            self.assertIn("aggiungili", plural["textIt"])
            self.assertIn("usa quelli", plural["textIt"])
            self.assertNotIn("aggiungilo", plural["textIt"])
            for result in (singular, plural):
                self.assertIn("un campione", result["textIt"])
                self.assertTrue(any(value in result["textIt"] for value in ("se è disponibile l'opzione", "se hai l'opzione", "se c'è l'opzione")))
                self.assertIn("如果需要", result["translationZh"])
                self.assertIn("入口", result["translationZh"])
                self.assertNotIn("免费", result["translationZh"])

    def test_missing_invalid_or_non_advantage_rates_never_fall_back_to_weak_copy(self):
        pid = product()["pid"]
        bad = [None, {}, {pid: {"creatorPercent": "12"}}]
        for change in ({"creatorPercent": None}, {"publicPercent": None}, {"creatorPercent": 12}, {"publicPercent": True},
                       {"creatorPercent": "NaN"}, {"creatorPercent": "Infinity"}, {"creatorPercent": "12%"},
                       {"creatorPercent": "1e2"}, {"creatorPercent": "101"}, {"publicPercent": "-1"},
                       {"creatorPercent": "10"}, {"creatorPercent": "9.5"}, {"creatorPercent": "0", "publicPercent": "0"},
                       {"sourceRef": ""}, {"sourceRef": "bad\nsource"}, {"observedAt": "2026-09-13T00:00:00"},
                       {"observedAt": "bad-time"}):
            bad.append(promotion_facts(**change))
        for template in list_templates():
            for facts in bad:
                with self.subTest(template=template["id"], facts=facts), self.assertRaises(SecondTemplateError) as error:
                    raw_render_template(template["id"], None, [product()], promotion_facts=facts)
                self.assertEqual(error.exception.code, "promotion_reason_missing")
            with self.assertRaises(SecondTemplateError) as error:raw_render_template(template["id"], None, [product()])
            self.assertEqual(error.exception.code, "promotion_reason_missing")
        with self.assertRaises(TypeError):raw_render_template("it-second-brief", None, [product()], promotion_facts())

    def test_every_selected_pid_requires_its_own_exact_reason_and_preserved_provenance(self):
        products = [product(pid) for pid in list(ITALIAN_PRODUCT_NAMES)[:2]]
        facts = promotion_facts(creatorPercent="10.001", publicPercent="10.000")
        for missing in products:
            incomplete = {pid: value for pid, value in facts.items() if pid != missing["pid"]}
            with self.assertRaises(SecondTemplateError) as error:render_template("it-second-brief", None, products, promotion_facts=incomplete)
            self.assertEqual(error.exception.code, "promotion_reason_missing")
        result = render_template("it-second-brief", None, products, promotion_facts=facts)
        self.assertEqual(result["promotionClaims"], [{"kind": "commission_above_public", "pid": p["pid"], **facts[p["pid"]]} for p in products])
        facts[products[0]["pid"]]["creatorPercent"] = "0"
        self.assertEqual(result["promotionClaims"][0]["creatorPercent"], "10.001")
        explicit_zero = render_template("it-second-choice", None, [product()], promotion_facts=promotion_facts(creatorPercent="0.1", publicPercent="0"))
        self.assertEqual(explicit_zero["promotionClaims"][0]["publicPercent"], "0")

    def test_no_external_request_or_source_loader_is_needed(self):
        with patch("socket.socket", side_effect=AssertionError("no network")), patch("lib.second_outreach_source.load_second_source", side_effect=AssertionError("no source read")), patch.object(Path, "read_text", side_effect=AssertionError("no file read")):
            for template in list_templates(): render_template(template["id"], None, [product()])


if __name__ == "__main__": unittest.main()
