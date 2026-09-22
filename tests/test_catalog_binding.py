import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lib.catalog_binding import CatalogBindings,audit_existing  # noqa: E402
from lib.schema_migrations import apply_database  # noqa: E402
from lib.second_cycle import digest  # noqa: E402


class CatalogBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "var").mkdir();(self.root/"config").mkdir()
        for name in ("catalog-link-policy.json","link-naming.json"):
            (self.root/"config"/name).write_text((ROOT/"config"/name).read_text(encoding="utf-8"),encoding="utf-8")
        sqlite3.connect(self.root / "var/catalog-links.sqlite").close()
        apply_database(self.root, "catalog-links")
        self.bindings = CatalogBindings(self.root)
        offer = {"pid": "1", "campaignId": "2", "catalogSource": "selected",
                 "creatorPercent": "13", "publicPercent": "10", "totalPercent": "15"}
        offer["planFingerprint"] = digest(offer)
        self.spec = {"market": "it", "route": "selected", "pid": "1", "campaignId": "2",
                     "creatorPercent": "13", "listName": "🔥 BJN Quaderno 13% abcdef",
                     "policyVersion": "commission-1-to-2-v1", "namingVersion": "link-naming-v1",
                     "offer": offer}
        self.card = {"state": "verified_read_only", "pid": "1", "sourceCampaignId": "2",
                     "creatorPercent": "13", "verifiedListName": self.spec["listName"],
                     "listId": "99", "checkedAt": 100.0, "evidenceRefs": ["proof"]}

    def tearDown(self):
        self.bindings.close()
        self.temp.cleanup()

    def test_exact_standard_card_becomes_the_only_current_binding(self):
        first = self.bindings.promote(self.spec, self.card, "intent-1", now=101.0)
        second = self.bindings.promote(self.spec, self.card, "intent-1", now=102.0)
        self.assertEqual(first["list_id"], "99")
        self.assertEqual(second["list_id"], "99")
        self.assertEqual(self.bindings.active_pids(), {"1"})
        self.assertEqual(self.bindings.active_for_offer(self.spec["offer"])["list_id"], "99")
        self.assertEqual(self.bindings.db.execute(
            "SELECT count(*) FROM catalog_current_binding_event").fetchone()[0], 1)

    def test_old_rule_name_rate_or_binding_never_promotes(self):
        for spec, card in [
            (self.spec | {"namingVersion": "old"}, self.card),
            (self.spec, self.card | {"creatorPercent": "12"}),
            (self.spec, self.card | {"sourceCampaignId": "3"}),
            (self.spec, self.card | {"verifiedListName": "legacy"}),
        ]:
            with self.subTest(spec=spec.get("namingVersion"), card=card):
                with self.assertRaises(ValueError):
                    self.bindings.promote(spec, card)
        self.assertEqual(self.bindings.active_pids(), set())

    def test_newer_binding_cannot_be_replaced_by_an_older_conflicting_observation(self):
        self.bindings.promote(self.spec, self.card | {"checkedAt": 200.0})
        same=self.bindings.promote(self.spec,self.card|{"checkedAt":100.0})
        self.assertEqual(same["verified_at"],200.0)
        with self.assertRaisesRegex(ValueError, "catalog_binding_stale_observation"):
            self.bindings.promote(self.spec, self.card | {"listId": "100", "checkedAt": 100.0})
        self.assertEqual(self.bindings.get("it", "selected", "1", "2")["list_id"], "99")

    def test_existing_verified_intent_is_promoted_only_when_both_rules_match(self):
        from lib.link_naming import load,name_for
        naming=load(self.root);rendered=name_for(self.root,pid='1',campaign='2',creator_percent='13',
            short_name='Quaderno',public_percent='10',total_percent='15',config=naming)
        offer=self.spec['offer']|{'agencyPercent':'2','title':'Quaderno'}
        spec=self.spec|{'purpose':'catalog_batch_link','account':'acc9','shortName':'Quaderno',
                        'listName':rendered['name'],'offer':offer}
        card=self.card|{'verifiedListName':rendered['name']}
        self.bindings.db.execute('''CREATE TABLE catalog_link_intent(id TEXT PRIMARY KEY,pid TEXT,account TEXT,state TEXT,
          spec TEXT,receipt TEXT,readback TEXT,created REAL,updated REAL)''')
        self.bindings.db.execute("INSERT INTO catalog_link_intent VALUES('i','1','acc9','verified',?,NULL,?,1,1)",
                                 (json.dumps(spec),json.dumps(card)))
        checked=audit_existing(self.root,apply=False,now=10)
        self.assertEqual((checked['qualifying'],checked['promoted'],checked['platformWrites']),(1,0,0))
        self.assertIsNone(self.bindings.get('it','selected','1','2'))
        applied=audit_existing(self.root,apply=True,now=10)
        self.assertEqual((applied['qualifying'],applied['promoted']),(1,1))
        self.assertEqual(self.bindings.get('it','selected','1','2')['list_id'],'99')

    def test_current_offer_reconciliation_deactivates_changes_and_can_restore_exact_binding(self):
        self.bindings.promote(self.spec, self.card, "intent-1", now=101.0)
        current = self.spec['offer']
        same = self.bindings.reconcile_current_offers(
            'it', 'selected', [current], evidence_ref='screen-1', now=102.0)
        self.assertEqual(same['unchanged'], 1)
        changed = dict(current)
        changed['creatorPercent'] = '14'
        changed['planFingerprint'] = digest(changed)
        result = self.bindings.reconcile_current_offers(
            'it', 'selected', [changed], evidence_ref='screen-2', now=103.0)
        self.assertEqual(result['waiting_refresh'], 1)
        self.assertEqual(self.bindings.get('it', 'selected', '1', '2')['state'], 'waiting_refresh')
        result = self.bindings.reconcile_current_offers(
            'it', 'selected', [], evidence_ref='screen-3', now=104.0)
        self.assertEqual(result['inactive'], 1)
        result = self.bindings.reconcile_current_offers(
            'it', 'selected', [current], evidence_ref='screen-4', now=105.0)
        self.assertEqual(result['active'], 1)
        self.assertEqual(self.bindings.active_pids(), {'1'})
        events = list(self.bindings.db.execute(
            "SELECT state FROM catalog_current_binding_event ORDER BY observed_at"
        ))
        self.assertEqual([row[0] for row in events],
                         ['active', 'waiting_refresh', 'inactive', 'active'])


if __name__ == "__main__":
    unittest.main()
