import importlib.util
import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))

spec=importlib.util.spec_from_file_location('catalog_link_prepare_cli',ROOT/'scripts/catalog-link-prepare.py')
prepare=importlib.util.module_from_spec(spec);spec.loader.exec_module(prepare)


class FakePrep:
    def __init__(self,*,retire_error=None):
        self.retire_error=retire_error;self.retired=[];self.progress=[]
    def retire_live_change(self,run_id,pid,cid,src,intent_id,code):
        if self.retire_error is not None:raise self.retire_error
        self.retired.append((run_id,pid,cid,src,intent_id,code));return True
    def mark_progress(self,run_id,pid,cid,src,state,*,error=None):
        self.progress.append((run_id,pid,cid,src,state,error))


class RetireOrBlock(unittest.TestCase):
    def call(self,prep,code,*,attempted=False):
        retired=[];blocked=[]
        outcome=prepare.retire_or_block(prep,'run-1','123','456','selected','intent-1',code,
                                        attempted=attempted,retired=retired,blocked=blocked,seconds=1.5)
        return outcome,retired,blocked

    def test_eligible_change_not_attempted_is_retired_without_progress(self):
        prep=FakePrep()
        outcome,retired,blocked=self.call(prep,'product_no_longer_eligible')
        self.assertEqual(outcome,'retired')
        self.assertEqual(retired,[{'pid':'123','reason':'product_no_longer_eligible','intentId':'intent-1','platformCreateAttempts':0}])
        self.assertEqual(blocked,[]);self.assertEqual(prep.progress,[])
        self.assertEqual(prep.retired,[('run-1','123','456','selected','intent-1','product_no_longer_eligible')])

    def test_retire_failure_falls_back_to_blocked_missing(self):
        prep=FakePrep(retire_error=ValueError('catalog_intent_requires_verification'))
        outcome,retired,blocked=self.call(prep,'commercial_facts_changed:creatorPercent 15->10')
        self.assertEqual(outcome,'blocked');self.assertEqual(retired,[])
        self.assertEqual(len(blocked),1)
        self.assertIn('retire_failed',blocked[0]['error'])
        self.assertTrue(blocked[0]['error'].startswith('commercial_facts_changed:creatorPercent 15->10;retire_failed:'))
        self.assertEqual(blocked[0]['seconds'],1.5)
        self.assertEqual(prep.progress,[('run-1','123','456','selected','missing',blocked[0]['error'])])

    def test_attempted_never_retires_and_marks_unknown(self):
        prep=FakePrep()
        outcome,retired,blocked=self.call(prep,'product_no_longer_eligible',attempted=True)
        self.assertEqual(outcome,'blocked');self.assertEqual(retired,[]);self.assertEqual(prep.retired,[])
        self.assertEqual(prep.progress,[('run-1','123','456','selected','unknown','product_no_longer_eligible')])
        self.assertEqual(blocked,[{'pid':'123','error':'product_no_longer_eligible','seconds':1.5}])

    def test_other_code_is_blocked_missing(self):
        prep=FakePrep()
        outcome,retired,blocked=self.call(prep,'card_search_unresolved')
        self.assertEqual(outcome,'blocked');self.assertEqual(retired,[]);self.assertEqual(prep.retired,[])
        self.assertEqual(prep.progress,[('run-1','123','456','selected','missing','card_search_unresolved')])

    def test_mark_progress_failure_never_escapes(self):
        class Broken(FakePrep):
            def mark_progress(self,*args,**kwargs):raise RuntimeError('ledger_locked')
        prep=Broken()
        outcome,retired,blocked=self.call(prep,'card_search_unresolved')
        self.assertEqual(outcome,'blocked');self.assertEqual(len(blocked),1)


if __name__=='__main__':unittest.main()
