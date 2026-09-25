import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))

from lib.market_identity import _probe_with_init_retry  # noqa:E402


class FakeRunner:
 def __init__(self,reports):self.reports=list(reports);self.calls=[]
 def __call__(self,argv,**kwargs):
  self.calls.append(argv)
  output=Path(argv[argv.index('--output')+1]);output.mkdir(parents=True,exist_ok=True)
  (output/'report.private.json').write_text(json.dumps(self.reports.pop(0)),encoding='utf-8')
  return type('Child',(),{'returncode':2})()


def init_failure():return {'status':'blocked','reason':'probe_initialization_or_validation_error','requests':[]}
def completed():return {'status':'completed','requests':[{'targetRef':'x'}]}


class InitRetryTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);(self.root/'var').mkdir()
  self.chunk=[{'sourceId':'s1','handle':'alice'}]
  self.payload=[{'ref':'market_identity_x','handle':'alice','externalId':'s1'}]
  self.tokens=['tok1','tok2']

 def test_init_failure_then_completed_retries_once(self):
  runner=FakeRunner([init_failure(),completed()]);slept=[]
  child,paths,private,retried=_probe_with_init_retry(self.root,'br','acc1',self.chunk,self.payload,self.tokens,runner,False,slept.append)
  self.assertTrue(retried);self.assertEqual(len(paths),2);self.assertEqual(slept,[5.0])
  self.assertEqual(private['status'],'completed');self.assertEqual(len(runner.calls),2)
  self.assertNotEqual(paths[0],paths[1])

 def test_blocked_with_requests_does_not_retry(self):
  runner=FakeRunner([{'status':'blocked','reason':'probe_initialization_or_validation_error','requests':[{'targetRef':'x'}]}]);slept=[]
  child,paths,private,retried=_probe_with_init_retry(self.root,'br','acc1',self.chunk,self.payload,self.tokens,runner,False,slept.append)
  self.assertFalse(retried);self.assertEqual(len(paths),1);self.assertEqual(slept,[]);self.assertEqual(len(runner.calls),1)

 def test_init_failure_twice_returns_second_failure(self):
  runner=FakeRunner([init_failure(),init_failure()]);slept=[]
  child,paths,private,retried=_probe_with_init_retry(self.root,'br','acc1',self.chunk,self.payload,self.tokens,runner,False,slept.append)
  self.assertTrue(retried);self.assertEqual(len(paths),2);self.assertEqual(len(runner.calls),2)
  self.assertEqual(private['reason'],'probe_initialization_or_validation_error')

 def test_completed_at_once_does_not_sleep(self):
  runner=FakeRunner([completed()]);slept=[]
  child,paths,private,retried=_probe_with_init_retry(self.root,'br','acc1',self.chunk,self.payload,self.tokens,runner,False,slept.append)
  self.assertFalse(retried);self.assertEqual(len(paths),1);self.assertEqual(slept,[]);self.assertEqual(len(runner.calls),1)


if __name__=='__main__':unittest.main()
