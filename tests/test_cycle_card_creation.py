import sys,tempfile,unittest,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.second_cycle import CycleStore,CycleError
from lib.cycle_materials import Materials
from lib.cycle_card_creation import CardCreation
from test_second_cycle import offer,NOW
class CreationTests(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();self.s=CycleStore(Path(self.t.name)/'db');Materials(self.s);self.c=CardCreation(self.s);self.p=self.s.plan('test','it');self.o=offer(title='test',catalogSource='selected',campaignId='2',endAt=NOW+90*86400)
  self.i=self.c.prepare(self.p,self.o,'cuscino')['id']
 def tearDown(self):self.s.close();self.t.cleanup()
 def card(self):return {'state':'verified_read_only','pid':'1','sourceCampaignId':'2','creatorPercent':'12','listId':'3','wireCampaignId':'0','verifiedListName':self.c.get(self.i)['list_name']}
 def test_campaign_wire_id_is_preserved(self):
  other=self.o|{'pid':'7','offerKey':'o7','catalogSource':'campaign'};id=self.c.prepare(self.p,other,'cuscino')['id'];self.c.begin(id)
  card=self.card()|{'pid':'7','wireCampaignId':'2','verifiedListName':self.c.get(id)['list_name']}
  self.c.confirm(id,card);self.assertEqual(self.c.get(id)['state'],'verified')
 def test_prepare_idempotent(self):self.assertEqual(self.c.prepare(self.p,self.o,'cuscino')['id'],self.i)
 def test_no_double_attempt(self):
  self.c.begin(self.i)
  with self.assertRaises(CycleError):self.c.begin(self.i)
 def test_unknown_no_new_intent_for_same_pid(self):
  self.c.begin(self.i);self.c.unknown(self.i)
  with self.assertRaises(CycleError):self.c.prepare(self.p,self.o|{'creatorPercent':'13'},'cuscino')
 def test_pause_prevents_attempt(self):
  self.s.control(self.p,'pause',1,'paused')
  with self.assertRaises(CycleError):self.c.begin(self.i)
 def test_unknown_can_be_verified_without_reposting(self):
  self.c.begin(self.i);self.c.unknown(self.i);self.c.confirm(self.i,self.card())
  self.assertEqual(self.c.get(self.i)['state'],'verified')
 def test_wrong_pid_and_rate_not_verified(self):
  self.c.begin(self.i)
  for card in [self.card()|{'pid':'wrong'},self.card()|{'creatorPercent':'11'}]:
   with self.assertRaises(CycleError):self.c.confirm(self.i,card)
 def test_other_unknown_stops_next_write(self):
  self.c.begin(self.i);self.c.unknown(self.i)
  other=self.c.prepare(self.p,self.o|{'pid':'4','offerKey':'o4'},'other')['id']
  with self.assertRaises(CycleError):self.c.begin(other)
 def test_receipt_id_must_match_readback(self):
  self.c.begin(self.i);self.c.save_receipt(self.i,{'list_id':'99'})
  with self.assertRaisesRegex(CycleError,'receipt_mismatch'):self.c.confirm(self.i,self.card())
 def test_receipt_and_reuse_paths(self):
  self.c.confirm(self.i,self.card(),reused=True);self.assertEqual(self.c.get(self.i)['state'],'verified')
if __name__=='__main__':unittest.main()
