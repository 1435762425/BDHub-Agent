"""A committed setting is reported as committed even when the page re-read fails afterwards."""
import contextlib,importlib.util,io,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from lib.schema_migrations import apply_database
from lib.second_cycle import CycleStore

def load_cli():
 spec=importlib.util.spec_from_file_location('operations_home_cli',ROOT/'scripts/operations-home.py')
 module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

class OperationsHomeCommitTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);(self.root/'var').mkdir()
  with CycleStore(self.root/'var/second-cycle.sqlite') as store:store.plan('bjn-local-research','it')
  apply_database(self.root,'second-cycle');self.cli=load_cli();self.cli.ROOT=self.root
 def tearDown(self):self.tmp.cleanup()
 def save(self,request_id,home=None):
  body={'market':'it','requestId':request_id,'expectedRevision':0,'changes':{'fullCatalogWeeklyEnabled':False}}
  out=io.StringIO()
  with patch.object(sys,'argv',['operations-home.py','save','--json',json.dumps(body)]),contextlib.redirect_stdout(out):
   with patch.object(self.cli,'home',home or (lambda store,market:{'schemaVersion':'bdhub.operations-home.v1'})):code=self.cli.main()
  return code,json.loads(out.getvalue())
 def test_failed_reread_after_commit_reports_the_committed_setting(self):
  def broken(store,market):raise RuntimeError('projection down')
  code,value=self.save('home-commit-0001',broken)
  self.assertEqual((code,value['error']),(2,'operations_home_refresh_failed'))
  self.assertEqual((value['commit']['committed'],value['commit']['setting']['revision']),(True,1))
  with CycleStore(self.root/'var/second-cycle.sqlite',readonly=True) as store:
   self.assertEqual(store.db.execute("SELECT revision FROM market_automation_setting WHERE market='it'").fetchone()[0],1)
  code,again=self.save('home-commit-0001')
  self.assertEqual((code,again['commit']['duplicate'],again['commit']['setting']['revision']),(0,True,1))
 def test_normal_save_carries_its_receipt(self):
  code,value=self.save('home-commit-0002')
  self.assertEqual((code,value['commit']['requestId'],value['commit']['launchErrors']),(0,'home-commit-0002',[]))
