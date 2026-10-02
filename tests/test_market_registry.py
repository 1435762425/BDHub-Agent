import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))

from lib.market_accounts import load_config  # noqa:E402
from lib.market_registry import capability_state,enabled_market_keys,load_registry,operational_market_keys,supports  # noqa:E402
from lib.operations_workflow import save_setting  # noqa:E402
from lib.schema_migrations import apply_database  # noqa:E402
from lib.second_cycle import CycleError,CycleStore  # noqa:E402


class MarketRegistryTests(unittest.TestCase):
 def test_visible_markets_share_one_registry_and_runtime_pairs_stay_bounded(self):
  registry=load_registry(ROOT);accounts=load_config(ROOT)
  self.assertEqual(enabled_market_keys(ROOT),('be','br','de','it','jp','my','mx','nl','ph','sg','th','uk','us','vn'))
  self.assertEqual(set(operational_market_keys(ROOT)),{'it','br','de','my','uk'})
  self.assertEqual(registry['markets']['de']['runtimeState'],'paused')
  self.assertEqual(set(accounts['markets']),set(operational_market_keys(ROOT)))
  assigned=[]
  for key,pair in accounts['markets'].items():
   self.assertEqual(set(pair['roles'].values()),set(pair['accounts']))
   assigned.extend(pair['accounts'])
  self.assertEqual(len(assigned),len(set(assigned)))

 def test_paused_market_holds_accounts_before_content_and_capabilities(self):
  import copy,json
  from lib.market_registry import validate_registry
  base=json.loads((ROOT/'config/markets.json').read_text())
  validate_registry(base)
  for field,value in (('contentReady',False),('capabilities',{'campaignCatalog':True,'fullManagedCatalog':None})):
   ready=copy.deepcopy(base);ready['markets']['de'].update({'runtimeState':'ready',field:value})
   with self.assertRaises(ValueError):validate_registry(ready)
  empty=copy.deepcopy(base);empty['markets']['de']['accounts']={'communications':None,'supply':None}
  with self.assertRaises(ValueError):validate_registry(empty)

 def test_only_it_and_uk_expose_full_managed_catalog(self):
  self.assertTrue(supports(ROOT,'it','fullManagedCatalog'))
  self.assertTrue(supports(ROOT,'uk','fullManagedCatalog'))
  self.assertFalse(supports(ROOT,'br','fullManagedCatalog'))
  self.assertFalse(supports(ROOT,'my','fullManagedCatalog'))
  self.assertEqual(capability_state(ROOT,'be','fullManagedCatalog'),'unavailable')

 def test_unsupported_full_catalog_switch_fails_without_changing_revision(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);(root/'var').mkdir()
   with CycleStore(root/'var/second-cycle.sqlite') as store:store.plan('bjn-local-research','br')
   apply_database(root,'second-cycle')
   with CycleStore(root/'var/second-cycle.sqlite') as store:
    with self.assertRaisesRegex(CycleError,'full_catalog_not_supported'):
     save_setting(store,'br','market-setting-0001',0,{'fullCatalogWeeklyEnabled':True})
    with self.assertRaisesRegex(CycleError,'market_automation_capabilities_pending'):
     save_setting(store,'br','market-setting-0002',0,{'automaticOperationsEnabled':True})
    self.assertIsNone(store.db.execute("SELECT 1 FROM market_automation_setting WHERE market='br'").fetchone())


if __name__=='__main__':unittest.main()
