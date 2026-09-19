import json,sys,tempfile,unittest,sqlite3
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.catalog_links import new_commission,choose_existing,link_decision,CatalogLinks,catalog_owns_pid,policy_fingerprint
from lib.catalog_binding import CatalogBindings
from lib.schema_migrations import apply_database
from lib.second_cycle import digest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT.parent/'01-BDSystem-V2'))
class LinkTests(unittest.TestCase):
 def setUp(self):self.policy=json.loads((ROOT/'config/catalog-link-policy.json').read_text())
 def test_new_commission_edges_and_precision(self):
  for total,public,creator,agency in [(1200,1000,1100,100),(1250,1000,1100,150),(1500,1000,1300,200)]:
   q=new_commission(total,public,self.policy);self.assertEqual((q['creatorRaw'],q['agencyRaw']),(creator,agency))
  for total,public in [(1199,1000),(None,1000),('NaN',1000),(1250.1,1000)]:
   with self.assertRaises(ValueError):new_commission(total,public,self.policy)
 def test_old_links_rank_by_actual_creator_rate_and_then_use(self):
  a={'listId':'a','totalRaw':1500,'publicRaw':1000,'creatorRaw':1350,'platformValid':True,'productEligible':True,'previouslyUsed':False}
  b=a|{'listId':'b','creatorRaw':1400};used=b|{'listId':'c','previouslyUsed':True}
  self.assertEqual(choose_existing([a,b,used],self.policy)['listId'],'c')
  self.assertEqual(choose_existing([a,b|{'creatorRaw':1450}],self.policy)['listId'],'a')
  old=a|{'totalRaw':1200,'creatorRaw':1050};self.assertIsNotNone(choose_existing([old],self.policy))
 def test_existing_or_incomplete_lookup_never_authorizes_new_creation(self):
  self.assertEqual(link_decision([],self.policy,search_complete=False)['state'],'lookup_incomplete')
  self.assertEqual(link_decision([{'listId':'old'}],self.policy,search_complete=True)['state'],'historical_links_ignored')
  self.assertEqual(link_decision([],self.policy,search_complete=True)['state'],'may_prepare_creation')
 def test_intent_pins_account_is_idempotent_and_blocks_replay(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'var').mkdir();(root/'config').mkdir();(root/'config/catalog-link-policy.json').write_text(json.dumps(self.policy));sqlite3.connect(root/'var/catalog-links.sqlite').close();apply_database(root,'catalog-links');l=CatalogLinks(root)
   s={'pid':'123','account':'acc9','market':'it','route':'selected','purpose':'acc9_single_card_canary','campaignId':'456','creatorPercent':'13','listName':'BJN test','policyFingerprint':policy_fingerprint(self.policy),'searchTotal':0,'payload':{'name':'BJN test','campaign_id':'0','source':2,'items':[{'product_id':'123','campaign_id':'456','creator_commission_rate':'1300'}]}}
   r=l.prepare(s);self.assertEqual(l.prepare(s|{'preparedAt':999})['id'],r['id'])
   with self.assertRaises(ValueError):l.begin(r['id'],'acc6')
   l.begin(r['id'],'acc9');l.unknown(r['id'],'timeout')
   with self.assertRaises(ValueError):l.begin(r['id'],'acc9')
   card={'state':'verified_read_only','listId':'789','pid':'123','sourceCampaignId':'456','creatorPercent':'13','wireCampaignId':'0','verifiedListName':'BJN test'}
   with self.assertRaises(ValueError):l.confirm(r['id'],card|{'sourceCampaignId':'wrong'})
   with self.assertRaises(ValueError):l.confirm(r['id'],card|{'creatorPercent':'12'})
   l.confirm(r['id'],card);self.assertEqual(l.get(r['id'])['state'],'verified')
   c=sqlite3.connect(root/'var/second-cycle.sqlite');self.assertTrue(catalog_owns_pid(c,'123'));c.close();l.db.close()
 def test_campaign_route_freezes_its_own_payload_shape_and_wire_id(self):
  """非全托建链意图：载荷形状不同（活动在顶层、没有 source），回读的线上活动号也不同。

  两条渠道同一套账本，所以这里逐项钉死：用全托的载荷去冻结非全托意图必须被拒，
  用全托的 wireCampaignId='0' 去确认非全托卡片也必须被拒。
  """
  from bdhub.send.taplink.protocol import create_payload
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'var').mkdir();(root/'config').mkdir();(root/'config/catalog-link-policy.json').write_text(json.dumps(self.policy));sqlite3.connect(root/'var/catalog-links.sqlite').close();apply_database(root,'catalog-links');l=CatalogLinks(root)
   payload=create_payload(pid='123',campaign_id='456',creator_pct='13',name='BJN test',route='campaign')
   self.assertEqual(payload,{'name':'BJN test','campaign_id':'456','items':[{'product_id':'123','creator_commission_rate':'1300'}]})
   s={'pid':'123','account':'acc9','market':'it','route':'campaign','purpose':'catalog_batch_link','campaignId':'456','creatorPercent':'13',
      'listName':'BJN test','shortName':'test','policyVersion':self.policy['version'],'policyFingerprint':policy_fingerprint(self.policy),
      'namingVersion':'link-naming-v1','namingFingerprint':'a'*64,'searchTotal':0,'standardSearchComplete':True,'sourceRun':'run-1',
      'offer':{'pid':'123','campaignId':'456','catalogSource':'campaign','creatorPercent':'13','publicPercent':'10','totalPercent':'15'},'payload':payload}
   r=l.prepare(s);self.assertEqual(r['state'],'prepared')
   with self.assertRaises(ValueError):l.prepare(s|{'pid':'124','payload':create_payload(pid='124',campaign_id='456',creator_pct='13',name='BJN test',route='selected')})
   l.begin(r['id'],'acc9')
   card={'state':'verified_read_only','listId':'789','pid':'123','sourceCampaignId':'456','creatorPercent':'13','wireCampaignId':'456','verifiedListName':'BJN test','checkedAt':1.0,'evidenceRefs':['proof']}
   with self.assertRaises(ValueError):l.confirm(r['id'],card|{'wireCampaignId':'0'})
   l.confirm(r['id'],card);self.assertEqual(l.get(r['id'])['state'],'verified')
   changed=l.prepare(s|{'offer':s['offer']|{'publicPercent':'11'}})
   self.assertNotEqual(changed['id'],r['id']);self.assertEqual(changed['state'],'prepared')
   with self.assertRaises(ValueError):l.prepare(s|{'pid':'125','route':'bogus','campaignId':'456'})
   l.db.close()
 def test_unsubmitted_legacy_intent_is_superseded_but_attempted_one_still_blocks(self):
  from bdhub.send.taplink.protocol import create_payload
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'var').mkdir();(root/'config').mkdir();(root/'config/catalog-link-policy.json').write_text(json.dumps(self.policy));sqlite3.connect(root/'var/catalog-links.sqlite').close();apply_database(root,'catalog-links')
   with sqlite3.connect(root/'var/second-cycle.sqlite') as db:
    db.execute("CREATE TABLE cycle_card_creation(id TEXT,plan_id TEXT,pid TEXT,offer_key TEXT,offer_json TEXT,plan_revision INTEGER,list_name TEXT,state TEXT,receipt TEXT,readback TEXT,created_at REAL)")
    db.execute("INSERT INTO cycle_card_creation VALUES('old','p','123','o','{}',1,'old','prepared',NULL,NULL,1)")
   l=CatalogLinks(root);payload=create_payload(pid='123',campaign_id='456',creator_pct='13',name='BJN test',route='selected')
   s={'pid':'123','account':'acc9','market':'it','route':'selected','purpose':'catalog_batch_link','campaignId':'456','creatorPercent':'13','listName':'BJN test','shortName':'test','policyVersion':self.policy['version'],'policyFingerprint':policy_fingerprint(self.policy),'namingVersion':'link-naming-v1','namingFingerprint':'a'*64,'searchTotal':0,'standardSearchComplete':True,'sourceRun':'run','offer':{'pid':'123','campaignId':'456','catalogSource':'selected','creatorPercent':'13','publicPercent':'10','totalPercent':'15'},'payload':payload}
   current=l.prepare(s);self.assertEqual(current['state'],'prepared')
   with sqlite3.connect(root/'var/second-cycle.sqlite') as db:self.assertEqual(db.execute("SELECT state FROM cycle_card_creation WHERE id='old'").fetchone()[0],'superseded')
   bindings=CatalogBindings(root)
   try:bindings.promote(s,{'state':'verified_read_only','pid':'123','sourceCampaignId':'456','creatorPercent':'13','verifiedListName':'BJN test','listId':'789','checkedAt':2.0,'evidenceRefs':['proof']})
   finally:bindings.close()
   self.assertEqual(l.supersede_redundant_prepared(),1);self.assertEqual(l.get(current['id'])['state'],'superseded')
   with sqlite3.connect(root/'var/second-cycle.sqlite') as db:db.execute("INSERT INTO cycle_card_creation VALUES('active','p','124','o','{}',1,'old','started','{}',NULL,1)")
   s2=s|{'pid':'124','payload':create_payload(pid='124',campaign_id='456',creator_pct='13',name='BJN test',route='selected')}
   with self.assertRaisesRegex(ValueError,'legacy_creation_in_progress'):l.prepare(s2)
   l.db.close()
if __name__=='__main__':unittest.main()
