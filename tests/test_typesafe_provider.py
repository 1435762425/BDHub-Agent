import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from lib.second_cycle import CycleError  # noqa:E402
from lib.typesafe_provider import list_models,status,system_one  # noqa:E402


class Response:
 def __init__(self,status_code=200,value=None):self.status_code=status_code;self.content=json.dumps(value or {}).encode()


class Session:
 def __init__(self,response,calls):self.response=response;self.calls=calls;self.trust_env=True
 def __enter__(self):return self
 def __exit__(self,*_):pass
 def request(self,*args,**kwargs):self.calls.append((args,kwargs,self.trust_env));return self.response


class TypeSafeProvider(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);(self.root/'config').mkdir()
  (self.root/'config/typesafe.json').write_text(json.dumps({'version':'typesafe-provider-v1',
   'endpoint':'https://api.typesafe.ai/v1/systemone','model':'jev-1.13.0','apiKey':'PRIVATE'}))
 def tearDown(self):self.temp.cleanup()
 def factory(self,response,calls):return lambda:Session(response,calls)
 def test_status_never_returns_the_key_and_models_use_fixed_official_host(self):
  self.assertNotIn('apiKey',status(self.root));calls=[]
  value=list_models(self.root,session_factory=self.factory(Response(value={'models':[{'name':'jev-latest'}]}),calls))
  self.assertEqual(value['models'],['jev-latest']);self.assertEqual(calls[0][0],('GET','https://api.typesafe.ai/v1/models'))
  self.assertEqual(calls[0][1]['headers']['Authorization'],'Bearer PRIVATE');self.assertFalse(calls[0][2])
 def test_system_one_validates_choice_response(self):
  calls=[];body={'model':'jev-1.13.0','answers':{'action':{'type':'choice','choice':'human','probabilities':{'human':1.0},'confidence':1.0}},'usage':{'input_tokens':10,'output_tokens':1}}
  value=system_one(self.root,{'text':'ciao'},{'action':{'type':'choice','instructions':'route','criteria':{'human':None}}},session_factory=self.factory(Response(value=body),calls))
  self.assertEqual(value['answers']['action']['choice'],'human');self.assertEqual(calls[0][0],('POST','https://api.typesafe.ai/v1/systemone'))
  self.assertEqual(calls[0][1]['json']['model'],'jev-1.13.0')
 def test_auth_and_unexpected_payload_are_named_errors(self):
  for response,code in ((Response(401,{}),'typesafe_auth_rejected'),(Response(429,{}),'typesafe_rate_limited'),(Response(200,{'model':'wrong','answers':{},'usage':{}}),'typesafe_response_invalid')):
   with self.assertRaisesRegex(CycleError,code):system_one(self.root,'x',{'q':{'type':'noul','instructions':'q'}},session_factory=self.factory(response,[]))


if __name__=='__main__':unittest.main()
