import sys,tempfile,unittest,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.second_cycle import CycleStore,CycleError
from lib.cycle_agent import AgentEvaluation,validate
class AgentTests(unittest.TestCase):
 def setUp(self):self.t=tempfile.TemporaryDirectory();self.s=CycleStore(Path(self.t.name)/'db');self.a=AgentEvaluation(self.s);self.c=[{'text':'Quanto è la commissione?','format':'text'}]
 def tearDown(self):self.s.close();self.t.cleanup()
 def response(self,*a,**kw):return {'content':json.dumps({'category':'commission_question','evidenceQuote':'commissione','requiredTools':['get_current_creator_commission']}),'usage':{'totalTokens':20}}
 def test_cached_evaluation_never_repeats_model_or_grants_send(self):
  a=self.a.evaluate(self.c,self.response);b=self.a.evaluate(self.c,lambda *a,**kw:1/0);self.assertFalse(a['executionAllowed']);self.assertTrue(b['cached']);self.assertEqual(a['id'],b['id'])
 def test_last_outbound_is_scoped_and_part_of_cache_identity(self):
  seen=[]
  def call(messages,**kw):seen.append(json.loads(messages[-1]['content']));return self.response()
  self.a.evaluate(self.c,call,background={'recentOutbound':'Ti va di fare un video?'})
  self.assertEqual(seen[0]['context']['recentOutbound'],'Ti va di fare un video?')
  with self.assertRaises(CycleError):self.a.evaluate(self.c,call,background={'agencyCommission':'99'})
 def test_unknown_request_is_not_retried(self):
  with self.assertRaises(CycleError):self.a.evaluate(self.c,lambda *a,**kw:1/0)
  with self.assertRaisesRegex(CycleError,'previous_request'):self.a.evaluate(self.c,self.response)
 def test_fabricated_quote_or_unregistered_tool_is_rejected(self):
  for v in [{'category':'commission_question','evidenceQuote':'different','requiredTools':['get_current_creator_commission']},{'category':'commission_question','evidenceQuote':'commissione','requiredTools':['approve_sample']}]:
   with self.assertRaises(CycleError):validate(v,self.c)
 def test_model_cannot_add_reply_or_execute_field(self):
  v=json.loads(self.response()['content']);v['reply']='We sent a sample'
  with self.assertRaises(CycleError):validate(v,self.c)
if __name__=='__main__':unittest.main()
