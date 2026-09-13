import sys,unittest,tempfile,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.product_stock_policy import mark_full_managed
from lib.second_cycle import assess_offer,CycleStore
from lib.cycle_card_creation import CardCreation
from lib.cycle_materials import Materials
from test_second_cycle import offer,edge,NOW
class StockPolicyTests(unittest.TestCase):
 def test_full_managed_never_requires_quantity(self):
  for stock in (None,'0','1','100','-1','NaN','not_a_number',False):
   o=mark_full_managed(offer(),'official-source')|{'stock':stock};r=assess_offer(o,NOW);self.assertTrue(r['eligible'],(stock,r));self.assertFalse(r['stockRequired'])
 def test_unknown_or_ordinary_stock_rule_is_preserved(self):
  for o in (offer(stock=None),offer(stock='0'),offer(stock='100'),offer(stock=None,managementType='full_managed')):self.assertFalse(assess_offer(o,NOW)['eligible'])
 def test_full_managed_still_requires_commission_expiry_and_availability(self):
  for changes in ({'creatorPercent':'9'},{'endAt':NOW+86400},{'available':False}):self.assertFalse(assess_offer(mark_full_managed(offer(**changes),'official-source'),NOW)['eligible'])
 def test_policy_projection_keeps_history_and_does_not_clear_nonstock_holds(self):
  with tempfile.TemporaryDirectory() as t:
   with CycleStore(Path(t)/'db',lambda:NOW) as s:
    p=s.plan('test','it');o=offer(title='test',catalogSource='selected',campaignId='2');s.publish(p,'source',NOW,[o]);s.import_edges(p,[edge()]);Materials(s);c=CardCreation(s);i=c.prepare(p,o,'cuscino')['id']
    c.invalidate_preflight(i,o|{'stock':'0','observedAt':NOW,'evidenceRef':'current-stock'})
    self.assertFalse(assess_offer(s._offers(p)[0][1],NOW)['eligible'])
    s.db.execute("INSERT INTO cycle_product_management VALUES(?,?,'full_managed',?,?)",(p,'1','official-source',NOW))
    self.assertTrue(assess_offer(s._offers(p)[0][1],NOW)['eligible']);self.assertEqual(c.get(i)['state'],'invalidated')
    self.assertEqual(json.loads(s.db.execute('SELECT payload FROM catalog').fetchone()[0])[0]['stock'],'101')
    evidence=json.loads(c.get(i)['readback']);evidence['assessment']['reasons']=['stock_not_over_100','unavailable'];s.db.execute('UPDATE cycle_card_creation SET readback=? WHERE id=?',(json.dumps(evidence),i))
    self.assertFalse(assess_offer(s._offers(p)[0][1],NOW)['eligible'])
 def test_only_unsubmitted_inventory_policy_preparation_can_be_superseded(self):
  with tempfile.TemporaryDirectory() as t:
   with CycleStore(Path(t)/'db',lambda:NOW) as s:
    p=s.plan('test','it');o=offer(title='test',catalogSource='selected',campaignId='2');Materials(s);c=CardCreation(s);old=c.prepare(p,o,'cuscino')['id'];new=c.prepare(p,mark_full_managed(o,'official-source'),'cuscino')['id']
    self.assertNotEqual(old,new);self.assertEqual(c.get(old)['state'],'superseded');c.begin(new);c.unknown(new)
    with self.assertRaises(Exception):c.prepare(p,mark_full_managed(o,'another-source'),'cuscino')
if __name__=='__main__':unittest.main()
