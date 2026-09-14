import json,sys,tempfile,unittest,sqlite3
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.catalog_links import new_commission,choose_existing,link_decision,CatalogLinks,catalog_owns_pid
from lib.second_cycle import digest
ROOT=Path(__file__).resolve().parents[1]
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
  self.assertEqual(link_decision([{'listId':'old'}],self.policy,search_complete=True)['state'],'old_links_require_review')
  self.assertEqual(link_decision([],self.policy,search_complete=True)['state'],'may_prepare_creation')
 def test_intent_pins_account_is_idempotent_and_blocks_replay(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'var').mkdir();(root/'config').mkdir();(root/'config/catalog-link-policy.json').write_text(json.dumps(self.policy));l=CatalogLinks(root)
   s={'pid':'123','account':'acc9','market':'it','route':'selected','purpose':'acc9_single_card_canary','campaignId':'456','creatorPercent':'13','listName':'BJN test','policyFingerprint':digest(self.policy),'searchTotal':0,'payload':{'name':'BJN test','campaign_id':'0','source':2,'items':[{'product_id':'123','campaign_id':'456','creator_commission_rate':'1300'}]}}
   r=l.prepare(s);self.assertEqual(l.prepare(s|{'preparedAt':999})['id'],r['id'])
   with self.assertRaises(ValueError):l.begin(r['id'],'acc6')
   l.begin(r['id'],'acc9');l.unknown(r['id'],'timeout')
   with self.assertRaises(ValueError):l.begin(r['id'],'acc9')
   card={'state':'verified_read_only','listId':'789','pid':'123','sourceCampaignId':'456','creatorPercent':'13','wireCampaignId':'0','verifiedListName':'BJN test'}
   with self.assertRaises(ValueError):l.confirm(r['id'],card|{'sourceCampaignId':'wrong'})
   with self.assertRaises(ValueError):l.confirm(r['id'],card|{'creatorPercent':'12'})
   l.confirm(r['id'],card);self.assertEqual(l.get(r['id'])['state'],'verified')
   c=sqlite3.connect(root/'var/second-cycle.sqlite');self.assertTrue(catalog_owns_pid(c,'123'));c.close();l.db.close()
if __name__=='__main__':unittest.main()
