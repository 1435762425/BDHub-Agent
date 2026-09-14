import sys,unittest
from pathlib import Path
from contextlib import contextmanager
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib import global_source_transport as transport
class RoutingTests(unittest.TestCase):
 def test_busy_is_waiting_but_other_runtime_failures_are_not(self):
  self.assertTrue(transport.is_account_busy(BlockingIOError()))
  self.assertTrue(transport.is_account_busy(RuntimeError('account_in_use')))
  self.assertFalse(transport.is_account_busy(RuntimeError('guard_changed')))
  self.assertFalse(transport.is_account_busy(ValueError('source_institution_changed')))
 def test_read_migration_does_not_move_selection_writes(self):
  calls=[]
  @contextmanager
  def fake(report,**kwargs):calls.append(kwargs);yield object()
  with patch.object(transport,'_opportunity_transport',fake),patch('lib.market_accounts.catalog_read_account',return_value='acc9') as resolver:
   with transport.opportunity_reader({}):pass
   with transport.opportunity_selector({},{}):pass
  self.assertEqual(calls[0]['account_name'],'acc9');self.assertNotIn('selection_scope',calls[0])
  self.assertEqual(calls[1]['account_name'],'acc6');self.assertEqual(calls[1]['selection_scope'],{})
  self.assertEqual(resolver.call_count,1)
 def test_card_canary_uses_acc9_and_copies_its_frozen_payload(self):
  calls=[]
  @contextmanager
  def fake(report,**kwargs):calls.append(kwargs);yield object()
  payload={'items':[{'product_id':'123'}],'name':'original'}
  with patch.object(transport,'_opportunity_transport',fake),patch('lib.market_accounts.catalog_read_account',return_value='acc9'):
   with transport.opportunity_card_creator({},payload):payload['name']='changed'
  self.assertEqual(calls[0]['account_name'],'acc9');self.assertEqual(calls[0]['creation_scope']['name'],'original')
  self.assertNotIn('selection_scope',calls[0])
if __name__=='__main__':unittest.main()
