import sys,tempfile,unittest,json,importlib.util
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.cycle_materials import Materials,checked_names,name_key,render,material_status
from lib.second_cycle import CycleStore,CycleError
from test_second_cycle import offer,edge,NOW
class MaterialTests(unittest.TestCase):
 def setUp(self):self.t=tempfile.TemporaryDirectory();self.s=CycleStore(Path(self.t.name)/'db');self.m=Materials(self.s);self.o=offer(title='Cuscino cervicale lungo titolo',endAt=NOW+90*86400)
 def tearDown(self):self.s.close();self.t.cleanup()
 def model(self,*a,**kw):return {'content':json.dumps({'items':[{'ref':'0','shortNameIt':'cuscino cervicale','mentionIt':'questo cuscino cervicale','shortNameZh':'颈枕'}]}),'usage':{'test':True}}
 def test_prepared_first_five_do_not_hide_later_product_demand(self):
  from lib.second_cycle import digest,encoded
  self.s.clock=lambda:NOW;plan=self.s.plan('test','it');offers=[offer(str(i),title='Cuscino '+str(i)) for i in range(1,7)]
  self.s.publish(plan,'s',NOW,offers);self.s.import_edges(plan,[edge(str(i),source='e'+str(i)) for i in range(1,7)])
  def model(*a,**kw):return {'content':json.dumps({'items':[{'ref':str(i),'shortNameIt':'cuscino','mentionIt':'questo cuscino','shortNameZh':'枕头'} for i in range(5)]})}
  self.m.prepare_names(offers[:5],model)
  for o in offers[:5]:self.s.db.execute('INSERT INTO cycle_card_check VALUES(?,?,?,?)',(plan,o['offerKey'],digest(o),encoded({'state':'verified_read_only'})))
  self.assertEqual([o['pid'] for o in self.m.candidates(plan)],['6'])
 def test_product_cache_ignores_offer_and_creator_changes(self):
  self.assertEqual(self.m.prepare_names([self.o],self.model)['modelCalls'],1)
  self.assertEqual(self.m.prepare_names([self.o|{'creatorPercent':'15'}],lambda *a:1/0)['modelCalls'],0)
 def test_title_change_creates_new_key(self):self.assertNotEqual(name_key(self.o),name_key(self.o|{'title':'Body modellante'}))
 def test_unknown_request_not_repeated(self):
  with self.assertRaises(CycleError):self.m.prepare_names([self.o],lambda *a,**kw:1/0)
  with self.assertRaisesRegex(CycleError,'previous_request'):self.m.prepare_names([self.o],self.model)
 def test_response_saved_can_recover_without_model(self):
  bad=lambda *a,**kw:{'content':'{}'}
  with self.assertRaises(CycleError):self.m.prepare_names([self.o],bad)
  self.s.db.execute("UPDATE cycle_name_job SET response=?",(json.dumps(self.model()),))
  self.assertEqual(self.m.prepare_names([self.o],lambda *a:1/0)['modelCalls'],0)
 def test_templates_do_not_invite_samples_or_emphasize_new_link(self):
  self.m.prepare_names([self.o],self.model)
  for kind in ['standard','brief','video_live']:
   r=render(self.m.name(self.o),self.o,kind,'creator');self.assertIn('12%',r['textIt']);self.assertNotIn('BJN',r['textIt']);self.assertNotIn('campione',r['textIt']);self.assertFalse(r['executionAllowed'])
 def test_overlapping_batches_do_not_duplicate_model_requests(self):
  def crash(*a,**kw):raise KeyboardInterrupt()
  with self.assertRaises(KeyboardInterrupt):self.m.prepare_names([self.o],crash)
  other=self.o|{'pid':'2','title':'Other product'}
  with self.assertRaisesRegex(CycleError,'reserved_by_other_batch'):self.m.prepare_names([other,self.o],lambda *a:1/0)
  self.assertEqual(self.s.db.execute('SELECT count(*) FROM cycle_name_job').fetchone()[0],1)
  self.assertEqual(self.s.db.execute('SELECT count(*) FROM cycle_name_reservation').fetchone()[0],1)
 def test_invalid_names_rejected(self):
  for name in ['gratis','爆款塑身衣','<script>','50% discount']:
   with self.assertRaises(CycleError):checked_names({'items':[{'ref':'0','shortNameIt':name,'mentionIt':'test','shortNameZh':'测试'}]},[self.o])
 def test_readonly_status_does_not_initialize_tables(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'db';s=CycleStore(path);p=s.plan('test','it');s.close()
   with CycleStore(path,readonly=True) as ro:self.assertEqual(material_status(ro,p),[])
class CardTests(unittest.TestCase):
 def setUp(self):
  spec=importlib.util.spec_from_file_location('card_material_probe',Path(__file__).resolve().parents[1]/'scripts/prepare-cycle-materials.py');self.mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.mod)
  self.o=offer(pid='123',campaignId='456',catalogSource='selected')
 def read(self,path,q):
  if path==self.mod.CARD:return {'data':{'total':1,'list':[{'product_list_id':'789','campaign_id':'0','campaign_products':[{'product_id':'123','creator_commission_percent':'1200'}]}]}},'im'
  return {'data':{'total_num':1,'campaign_products':[{'product_id':'123','campaign_id':'456','creator_commission_percent':'1200','stock':'200','product_status':2}]}},'members'
 def test_campaign_members_inherit_verified_parent_campaign_only(self):
  def read(path,q):
   b,sha=self.read(path,q)
   if path==self.mod.CARD:b['data']['list'][0]['campaign_id']='456'
   else:b['data']['campaign_products'][0].pop('campaign_id')
   return b,sha
  self.assertEqual(self.mod.inspect_card(self.o|{'catalogSource':'campaign'},read)['state'],'verified_read_only')
  def selected_read(path,q):
   b,sha=read(path,q)
   if path==self.mod.CARD:b['data']['list'][0]['campaign_id']='0'
   return b,sha
  self.assertNotEqual(self.mod.inspect_card(self.o,selected_read)['state'],'verified_read_only')
 def test_exact_card_with_member_readback(self):self.assertEqual(self.mod.inspect_card(self.o,self.read)['state'],'verified_read_only')
 def test_full_managed_card_does_not_need_stock_number(self):
  from lib.product_stock_policy import mark_full_managed
  def read(path,q):
   b,sha=self.read(path,q)
   if path==self.mod.MEMBERS:b['data']['campaign_products'][0].pop('stock');b['data']['campaign_products'][0]['unavailable_type']=8
   return b,sha
  card=self.mod.inspect_card(mark_full_managed(self.o,'official-source'),read);self.assertEqual(card['state'],'verified_read_only');self.assertIsNone(card['stock']);self.assertFalse(card['stockRequired'])
 def test_rate_mismatch_is_not_verified(self):self.assertEqual(self.mod.inspect_card(self.o|{'creatorPercent':'13'},self.read)['state'],'needs_card_preparation')
 def test_partial_member_page_is_not_accepted(self):
  def read(path,q):
   r,sha=self.read(path,q)
   if path==self.mod.MEMBERS:r['data']['total_num']=2
   return r,sha
  with self.assertRaises(CycleError):self.mod.inspect_card(self.o,read)
if __name__=='__main__':unittest.main()
