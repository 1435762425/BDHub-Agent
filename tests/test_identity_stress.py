import json,tempfile,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.identity_stress import stress_case
class StressTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.token='a'*32
  self.folder=self.root/'var/identity-stress'/self.token;self.folder.mkdir(parents=True)
  self.targets=[{'ref':'x','externalId':'x','handle':'known'}]
  self.manifest={'cases':{'single-8':{'accounts':['acc6'],'qps':8,'lanes':9}},'targets':{'acc6':self.targets},'identityFingerprints':{'acc6':'frozen'}}
  (self.folder/'manifest.json').write_text(json.dumps(self.manifest))
  self.body={'stressRun':self.token,'stressCase':'single-8','identityOnly':True,'targets':self.targets}
 def tearDown(self):self.tmp.cleanup()
 def test_explicit_frozen_read_case(self):
  folder,case,fingerprint=stress_case(self.root,self.body,'acc6');self.assertEqual(case['qps'],8);self.assertEqual(fingerprint,'frozen')
 def test_scope_and_identity_mode_cannot_expand(self):
  for changes in ({'stressRun':'../other'},{'stressCase':'invented'},{'identityOnly':False},{'cohortId':'live-task'},{'targets':[]}):
   with self.assertRaises(ValueError):stress_case(self.root,self.body|changes,'acc6')
  with self.assertRaises(ValueError):stress_case(self.root,self.body,'acc1')
 def test_rate_above_bound_rejected(self):
  self.manifest['cases']['single-8']['qps']=100
  (self.folder/'manifest.json').write_text(json.dumps(self.manifest))
  with self.assertRaises(ValueError):stress_case(self.root,self.body,'acc6')
if __name__=='__main__':unittest.main()
