import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))

from lib.market_identity import validated_report_targets  # noqa:E402
from lib.second_cycle import CycleError  # noqa:E402


def request(ref,handle):return {'ref':ref,'handle':handle,'sourceId':'source-'+ref,'pid':'1'*19}
def target(ref,handle):return {'targetRef':ref,'status':'identity_verified','currentHandleResolved':True,
                               'find':{'identity':{'handle':handle,'oecId':'123','market':'br'}}}
def receipt(ref,code='0'):return {'targetRef':ref,'stage':'find','status':'returned','httpStatus':200,
                                   'code':code,'verificationRequired':False}
def report(status,targets,requests):return {'market':'br','account':'acc1','status':status,
 'identityFileUnchanged':True,'oldDatabaseWrites':0,'realSends':0,'targets':targets,'requests':requests}


class MarketIdentityReportTests(unittest.TestCase):
 def test_completed_report_requires_every_frozen_target(self):
  requested=[request('a','alice'),request('b','bob')]
  with self.assertRaisesRegex(CycleError,'report_invalid'):
   validated_report_targets(report('completed',[target('a','alice')],[receipt('a')]),requested,'br','acc1')

 def test_blocked_report_keeps_confirmed_prefix_and_leaves_auth_target_pending(self):
  requested=[request('a','alice'),request('b','bob'),request('c','carol')]
  value=validated_report_targets(report('blocked',[target('a','alice'),target('b','bob')],
    [receipt('a'),receipt('b'),receipt('c','16201010')]),requested,'br','acc1')
  valid,blocked,code=value
  self.assertEqual([row[0]['targetRef'] for row in valid],['a','b'])
  self.assertEqual(blocked,{'c'})
  self.assertEqual(code,'market_identity_auth_required')

 def test_blocked_report_never_turns_a_failed_receipt_into_unresolved(self):
  requested=[request('a','alice')]
  unresolved={'targetRef':'a','status':'unresolved','reason':'no_exact_handle'}
  valid,blocked,code=validated_report_targets(
    report('blocked',[unresolved],[receipt('a','16201010')]),requested,'br','acc1')
  self.assertEqual(valid,[]);self.assertEqual(blocked,{'a'});self.assertEqual(code,'market_identity_auth_required')

 def test_blocked_profile_keeps_confirmed_find_without_repeating_it(self):
  requested=[request('a','alice')]
  partial=target('a','alice')|{'status':None}
  profile={'targetRef':'a','stage':'profile','status':'returned','httpStatus':200,
           'code':'100000','verificationRequired':False}
  valid,blocked,code=validated_report_targets(
    report('blocked',[partial],[receipt('a'),profile]),requested,'br','acc1')
  self.assertEqual([row[0]['targetRef'] for row in valid],['a'])
  self.assertEqual(blocked,{'a'})
  self.assertEqual(code,'market_identity_blocked')


if __name__=='__main__':unittest.main()
