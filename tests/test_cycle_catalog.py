import unittest,sys,json
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.cycle_catalog import normalize,new_state,step,commission_rule,commission_calculator
from lib.second_cycle import CycleError,assess_offer
AT=1789257600
RULE={'id':'saved-rule'}
C={'campaign_id':'234','crs_campaign_type':5,'promotion_start_time':str((AT-86400)*1000),'promotion_end_time':str((AT+90*86400)*1000)}
P={'product_id':'123','product_name':'product','stock':'101','total_commission_percent':'1500','plan_commission_percent':'1000','product_status':2,'product_rating':'3.8'}
def calc(t,p):return SimpleNamespace(valid=t is not None and p is not None,creator_pct=13)
class CatalogTests(unittest.TestCase):
 def test_samples_are_not_inferred_from_product_stock(self):
  r=normalize(P,C,'campaign',RULE,calc,'ref',AT)
  self.assertEqual(r['sample'],{'supported':None,'quota':None,'observedAt':AT,'evidenceRef':'ref'})
  for invalid in (True,-1,'1.5','NaN','Infinity'):
   with self.subTest(invalid=invalid):
    r=normalize(P|{'sample_quota':invalid},C,'campaign',RULE,calc,'ref',AT)
    self.assertIsNone(r['sample']['quota']);self.assertIsNone(r['sample']['supported'])
 def test_sample_quota_and_explicit_unavailable_remain_distinct(self):
  r=normalize(P|{'sample_quota':'2'},C,'campaign',RULE,calc,'ref',AT)
  self.assertEqual(r['sample']['quota'],2);self.assertIs(r['sample']['supported'],True)
  r=normalize(P|{'sample_quota':2,'has_free_sample':False},C,'campaign',RULE,calc,'ref',AT)
  self.assertIs(r['sample']['supported'],False)
  r=normalize(P|{'sample_quota':0,'is_free_sample':True},C,'campaign',RULE,calc,'ref',AT)
  self.assertEqual(r['sample']['quota'],0);self.assertIs(r['sample']['supported'],True)
 def test_only_explicit_level_one_category_is_used_for_main_category_matching(self):
  r=normalize(P|{'category_id':'42'},C,'campaign',RULE,calc,'ref',AT)
  self.assertNotIn('categoryId',r)
  r=normalize(P|{'first_category_id':'42','first_category_name':'Beauty'},C,'campaign',RULE,calc,'ref',AT)
  self.assertEqual((r['categoryId'],r['categoryName'],r['categoryEvidenceRef']),('42','Beauty','ref'))
 def test_normalize(self):
  r=normalize(P,C,'campaign',RULE,calc,'ref',AT)
  self.assertTrue(assess_offer(r,AT)['eligible']);self.assertFalse(r['cardBindingVerified']);self.assertEqual(r['commissionState'],'proposed_not_applied');self.assertEqual(r['publicPercent'],'10');self.assertEqual(r['totalPercent'],'15')
 def test_missing_stock_not_zero(self):
  r=normalize(P|{'stock':None},C,'campaign',RULE,calc,'ref',AT);self.assertIsNone(r['stock']);self.assertIn('missing_stock',assess_offer(r,AT)['reasons'])
 def test_full_managed_no_quantity_gate_but_delisted_is_still_blocked(self):
  for stock in (None,'0','100'):
   r=normalize(P|{'stock':stock,'unavailable_type':8},C|{'crs_campaign_type':9},'selected',RULE,calc,'official',AT)
   self.assertIsNone(r['stock']);self.assertTrue(assess_offer(r,AT)['eligible'])
  r=normalize(P|{'stock':None,'product_status':5},C|{'crs_campaign_type':9},'selected',RULE,calc,'official',AT);self.assertFalse(r['available'])
 def test_future_and_expired(self):
  for c in [C|{'promotion_start_time':str((AT+1)*1000)},C|{'promotion_end_time':str(AT*1000)}]:self.assertFalse(normalize(P,c,'campaign',RULE,calc,'ref',AT)['available'])
 def test_explicit_unavailable(self):
  for p in [P|{'is_under_governed':True},P|{'unavailable_type':1},P|{'product_status':5}]:self.assertFalse(normalize(p,C,'campaign',RULE,calc,'ref',AT)['available'])
 def test_missing_status(self):
  r=normalize(P|{'product_status':None},C,'campaign',RULE,calc,'ref',AT);self.assertIsNone(r['available'])
 def test_campaign_full_sequence_and_checkpoint(self):
  s=new_state('campaign',RULE,{},AT)
  step(s,lambda *args:({'data':{'total_num':1,'campaign':[C]}},'sha1'),calc,AT)
  self.assertEqual(s['phase'],'products');s=json.loads(json.dumps(s))
  step(s,lambda *args:({'data':{'total_num':1,'campaign_product':[P]}},'sha2'),calc,AT)
  self.assertEqual(s['state'],'completed');self.assertEqual(len(s['offers']),1)
 def test_partial_pages_use_total_not_short_page(self):
  s=new_state('selected',RULE,{},AT)
  step(s,lambda *a:({'total_num':'2','data':[{'campaign_product':P,'campaign_info':C}]},'one'),calc,AT)
  self.assertEqual(s['page'],2);self.assertEqual(s['state'],'running')
  step(s,lambda *a:({'total_num':2,'data':[{'campaign_product':P|{'product_id':'124'},'campaign_info':C}]},'two'),calc,AT)
  self.assertEqual(s['state'],'completed')
 def test_empty_full_catalog(self):
  s=new_state('selected',RULE,{},AT);step(s,lambda *a:({'total_num':0},'zero'),calc,AT);self.assertEqual(s['state'],'completed')
 def test_changed_total_rejected(self):
  s=new_state('selected',RULE,{},AT);s['total']=2
  with self.assertRaisesRegex(CycleError,'total_changed'):step(s,lambda *a:({'total_num':1,'data':[]},'x'),calc,AT)
 def test_duplicate_page_rejected(self):
  s=new_state('selected',RULE,{},AT);f=lambda *a:({'total_num':2,'data':[{'campaign_product':P,'campaign_info':C}]},'one')
  step(s,f,calc,AT)
  with self.assertRaisesRegex(CycleError,'duplicate'):step(s,f,calc,AT)
 def test_campaign_identity_required(self):
  with self.assertRaisesRegex(CycleError,'identity_missing'):normalize(P,{},'selected',RULE,calc,'ref',AT)
 def test_unverified_pagination_stops(self):
  s=new_state('selected',RULE,{},AT)
  with self.assertRaisesRegex(CycleError,'total_missing'):step(s,lambda *a:({'data':[]},'x'),calc,AT)
class CommissionRule(unittest.TestCase):
 '''佣金只走已确认的新规则；旧版 link_rules_for 的"平均分佣"不再参与。'''

 def setUp(self):
  self.root=Path(__file__).resolve().parents[1]
  self.rule=commission_rule(self.root)

 def test_rule_is_the_confirmed_policy_and_is_fingerprinted(self):
  self.assertEqual(self.rule['id'],'commission-1-to-2-v1')
  self.assertEqual(self.rule['agencyMinPoints'],'1')
  self.assertEqual(self.rule['agencyPreferredPoints'],'2')
  self.assertEqual(self.rule['newCreatorMinBoostPoints'],'1')

 def test_calculator_implements_agency_one_to_two_points(self):
  calc=commission_calculator(self.rule)
  # 总15/公开12 → 差3点：机构留 min(2, 3-1)=2，达人 13
  self.assertEqual(calc('1500','1200').creator_pct,13)
  # 差2点：机构只能留 1，达人 = 总-1（且仍高于公开 1 点）
  self.assertEqual(calc('1400','1200').creator_pct,13)
  # 差6点：机构仍然只留 2
  self.assertEqual(calc('1800','1200').creator_pct,16)

 def test_an_unusable_gap_is_invalid_not_a_crash(self):
  calc=commission_calculator(self.rule)
  self.assertFalse(calc('1300','1200').valid);self.assertEqual(calc('1300','1200').error,'insufficient_commission_gap')
  self.assertFalse(calc('1200','1200').valid)
  self.assertFalse(calc(None,'1200').valid)

 def test_normalize_records_the_new_rule_and_its_number(self):
  calc=commission_calculator(self.rule)
  r=normalize(P,C,'campaign',self.rule,calc,'ref',AT)
  self.assertEqual(r['creatorPercent'],'13');self.assertEqual(r['commissionRuleId'],'commission-1-to-2-v1')
  self.assertEqual(r['commissionRuleFingerprint'],__import__('lib.second_cycle',fromlist=['digest']).digest(self.rule))
  self.assertTrue(assess_offer(r,AT)['eligible'])

 def test_a_thin_gap_offer_has_no_creator_percent(self):
  calc=commission_calculator(self.rule)
  r=normalize(P|{'total_commission_percent':'1300','plan_commission_percent':'1200'},C,'campaign',self.rule,calc,'ref',AT)
  self.assertIsNone(r['creatorPercent']);self.assertIn('missing_creatorPercent',assess_offer(r,AT)['reasons'])




class TransientReadTests(unittest.TestCase):
    def test_network_drops_and_busy_codes_are_reread_but_answers_are_not(self):
        import importlib.util
        from types import SimpleNamespace
        spec=importlib.util.spec_from_file_location('sync_cycle_catalog_cli',Path(__file__).resolve().parents[1]/'scripts/sync-cycle-catalog.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        def result(http=200,code=0,turing=False):return SimpleNamespace(http_status=http,code=code,has_turing=turing,system_error_3=False)
        for transient in (result(http=0,code=None),result(code=98001001),result(http=503,code=None),result(code=100000)):
            self.assertTrue(module.transient_read(transient))
        for final in (result(),result(code=16201010),result(code=98001001,turing=True),result(code=98001002)):
            self.assertFalse(module.transient_read(final))


if __name__=='__main__':unittest.main()
