import tempfile,unittest,sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from lib.market_read_model import publish,read  # noqa:E402
from lib.schema_migrations import apply_database  # noqa:E402
from lib.second_cycle import CycleError,CycleStore  # noqa:E402

class MarketReadModelTests(unittest.TestCase):
 def test_publish_is_scoped_versioned_and_idempotent(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);(root/'var').mkdir()
   with CycleStore(root/'var/second-cycle.sqlite',lambda:100) as store:store.plan('bjn-local-research','it')
   apply_database(root,'second-cycle',clock=lambda:100)
   with CycleStore(root/'var/second-cycle.sqlite',lambda:100) as store:
    first=publish(store,'it','operations',{'market':'it','value':1})
    self.assertTrue(first['changed']);self.assertEqual(first['revision'],1)
    again=publish(store,'it','operations',{'market':'it','value':1},observed_at=110)
    self.assertFalse(again['changed']);self.assertEqual(again['generationId'],first['generationId'])
    second=publish(store,'it','operations',{'market':'it','value':2},observed_at=120)
    self.assertTrue(second['changed']);self.assertEqual(second['revision'],2)
    self.assertEqual(read(store,'it','operations',max_age=5,now=124)['value'],2)
    self.assertIsNone(read(store,'it','operations',max_age=5,now=126))
    self.assertEqual(store.db.execute("SELECT count(*) FROM market_read_generation").fetchone()[0],2)
    with self.assertRaisesRegex(CycleError,'scope_mismatch'):
     publish(store,'br','operations',{'market':'it'})

if __name__=='__main__':unittest.main()
