import sys,threading,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.cohort_find import SharedPacer,run_find_cohort
class Client:
 instances=[]
 def __init__(self,config,identity,scratch):
  self.config=config;self.business_retries=2;self.captcha_attempts=3;self.request_count=0;self.session=SimpleNamespace(close=lambda:None,post=lambda *a,**kw:None);self.pacer=None;Client.instances.append(self)
 def _signed_post_once(self,stage,body):
  self.pacer.acquire();self.request_count+=1
  return SimpleNamespace(status_code=200,headers={}),{'handle':body['query'],'oec':'123'+body['query'][-1]}
 def _solve_captcha(self,*a):raise AssertionError('no challenge expected')
 def post(self,*a):return self._signed_post_once(*a)
class CohortFindTests(unittest.TestCase):
 def test_three_lanes_have_one_aggregate_pacer_and_independent_receipts(self):
  Client.instances=[];c=Client({},None,None);targets=[{'ref':str(i),'externalId':str(i),'handle':'creator'+str(i)} for i in range(9)];report={'targets':[],'requests':[],'status':'starting'}
  probe=SimpleNamespace(PureHttpPartnerClient=Client,find_exact=lambda p,h:p)
  child=SimpleNamespace(_configure_market_transport=lambda *a:None,_oec=lambda p:p['oec'],_handle=lambda p:p['handle'])
  class Fast:
   def __init__(self,*a):pass
   def acquire(self):time.sleep(.001)
  with patch('lib.cohort_find.SharedPacer',Fast):
   run_find_cohort(probe,child,{},None,targets,c,report,lambda:None,lambda *a:dict(allowed=True,httpStatus=200,code='0',verificationRequired=False),lambda p:{'identity':{'market':'it'}},lambda c:{'request_count':c.request_count},lambda ref:ref!='8')
  self.assertEqual(len(Client.instances),3);self.assertEqual(len({id(x.pacer) for x in Client.instances}),1)
  self.assertEqual(len(report['requests']),8);self.assertEqual(report['counters']['request_count'],8)
  self.assertEqual(sum(t.get('status')=='identity_verified' for t in report['targets']),8)
  self.assertEqual(len({r['targetRef'] for r in report['requests']}),8)
 def test_invalid_lanes_rejected_before_constructing_clients(self):
  for lanes in (True,0,4,12):
   with self.assertRaises(ValueError):run_find_cohort(None,None,None,None,None,None,None,None,None,None,None,None,lanes=lanes)
 def test_aggregate_start_times_never_bunch(self):
  p=SharedPacer(3);times=[]
  def go():p.acquire();times.append(time.monotonic())
  threads=[threading.Thread(target=go) for _ in range(3)]
  for t in threads:t.start()
  for t in threads:t.join()
  self.assertTrue(all(b-a>=.32 for a,b in zip(sorted(times),sorted(times)[1:])))
if __name__=='__main__':unittest.main()
