import sys,tempfile,unittest
from pathlib import Path
from datetime import datetime,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.second_cycle import CycleStore,CycleError,assess_offer
NOW=1789257600.0

def offer(pid='1',**changes):
 return dict({'pid':pid,'offerKey':'o'+pid,'stock':'101','creatorPercent':'12','publicPercent':'10','endAt':NOW+46*86400,'available':True,'rating':'3.8','evidenceRef':'e'+pid},**changes)
def edge(pid='1',person='c1',source='e1',**changes):
 return dict({'sourceId':source,'creatorId':person,'oec':'123' if person=='c1' else '456' if person else None,'pid':pid,'offerKey':'o'+pid,'units':10,'evidenceRef':'source','observedAt':NOW,'windowStart':'2026-08-31','windowEnd':'2026-09-13','historicalOwnership':'unverified'},**changes)

class CycleTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'cycle.sqlite';self.now=NOW
  self.s=CycleStore(self.path,lambda:self.now);self.p=self.s.plan('institution','it')
 def tearDown(self):self.s.close();self.tmp.cleanup()
 def publish(self,offers=None,at=NOW,**kw):return self.s.publish(self.p,'source',at,offers or [offer()],**kw)
 def schedule(self,**kw):return self.s.replenish(self.p,new_remaining=kw.pop('new_remaining',10),established_capacity=0,window_end='2026-09-13',**kw)
 def test_strict_eligibility(self):
  for fields,reason in [({'stock':'100'},'stock_not_over_100'),({'endAt':NOW+45*86400},'expiry_not_over_45_days'),({'creatorPercent':'10'},'no_creator_commission_advantage'),({'stock':None},'missing_stock'),({'available':False},'unavailable')]:
   self.assertIn(reason,assess_offer(offer(**fields),NOW)['reasons'])
  self.assertTrue(assess_offer(offer(),NOW)['eligible']);self.assertFalse(assess_offer(offer(),NOW)['ratingPreferred'])
 def test_invalid_numbers(self):
  for field in ['stock','creatorPercent','publicPercent']:
   for v in [True,'NaN','Infinity','wrong']:
    self.assertFalse(assess_offer(offer(**{field:v}),NOW)['eligible'])
 def test_capacity_not_accounts(self):
  self.assertEqual(self.p,self.s.plan('institution','it'))
  with self.assertRaises(CycleError):self.schedule(new_remaining=501)
  self.assertNotEqual(self.p,self.s.plan('another','it'))
 def test_partial_catalog_keeps_complete(self):
  self.publish();self.publish([offer('2')],NOW+1,complete=False)
  self.assertEqual([x['pid'] for x in self.s.status(self.p)['offers']],['1'])
 def test_old_snapshot_never_replaces(self):
  self.publish();self.publish([offer('2')],NOW-1)
  self.assertEqual(self.s.status(self.p)['offers'][0]['pid'],'1')
 def test_same_time_conflict_rolls_back(self):
  self.publish()
  with self.assertRaises(CycleError):self.publish([offer('2')])
  self.assertEqual(self.s.db.execute('SELECT count(*) FROM catalog').fetchone()[0],1)
 def test_zero_capacity_no_jobs(self):
  self.publish();self.assertEqual(self.schedule(new_remaining=0)['created'],[])
 def test_bounded_pid_supply(self):
  self.publish([offer(str(i)) for i in range(100)])
  r=self.schedule(new_remaining=500,max_pids=3)
  self.assertEqual(len(r['created']),3);self.assertEqual(r['windowDays'],14)
  self.assertEqual(self.s.db.execute('SELECT count(*) FROM source_job').fetchone()[0],3)
 def test_existing_pending_deducted(self):
  self.publish([offer('1'),offer('2')]);self.schedule()
  self.assertEqual(self.schedule()['created'],[])
 def test_same_person_counts_once(self):
  self.publish([offer('1'),offer('2')]);self.s.import_edges(self.p,[edge(),edge('2',source='e2')])
  self.assertEqual(self.s.status(self.p)['relationships'],1)
  self.assertEqual(self.schedule(new_remaining=1)['created'],[])
 def test_source_replay_and_conflict(self):
  self.s.import_edges(self.p,[edge()]);self.s.import_edges(self.p,[edge()])
  self.assertEqual(self.s.status(self.p)['sourceEdges'],1)
  with self.assertRaises(CycleError):self.s.import_edges(self.p,[edge(units=20)])
 def test_identity_conflict_atomic(self):
  self.s.import_edges(self.p,[edge()])
  with self.assertRaises(CycleError):self.s.import_edges(self.p,[edge(source='e2',oec='999')])
  self.assertEqual(self.s.status(self.p)['sourceEdges'],1)
 def test_unresolved_is_not_relationship(self):
  self.s.import_edges(self.p,[edge(person=None)])
  self.assertEqual(self.s.status(self.p)['relationships'],0)
  self.assertEqual(self.s.status(self.p)['sourceEdges'],1)
 def test_human_and_rejection_preserved_on_import(self):
  self.publish();self.s.import_edges(self.p,[edge()])
  self.s.control(self.p,'human',1,'human','c1',True)
  self.s.import_edges(self.p,[edge(source='e2',observedAt=NOW+1)])
  self.s.control(self.p,'return',2,'auto','c1')
  self.assertEqual(self.s.status(self.p)['eligibleUniqueCreators'],0)
 def test_control_replay_and_revision(self):
  self.assertFalse(self.s.control(self.p,'pause',1,'paused')['duplicate'])
  self.assertTrue(self.s.control(self.p,'pause',1,'paused')['duplicate'])
  with self.assertRaises(CycleError):self.s.control(self.p,'different',1,'active')
 def test_pause_blocks_claim_and_page(self):
  self.publish();self.schedule();c=self.s.claim(self.p,'worker')
  self.s.control(self.p,'pause',1,'paused')
  self.assertIsNone(self.s.claim(self.p,'other'))
  with self.assertRaisesRegex(CycleError,'plan_paused'):self.s.page(c,[edge()],'',True)
  self.assertEqual(self.s.status(self.p)['sourceEdges'],0)
 def test_lease_fencing_two_connections(self):
  self.publish();self.schedule();c=self.s.claim(self.p,'old',1)
  with CycleStore(self.path,lambda:self.now) as second:
   self.assertIsNone(second.claim(self.p,'new'));self.now+=2;fresh=second.claim(self.p,'new')
   with self.assertRaisesRegex(CycleError,'lease_lost'):self.s.page(c,[edge()],'',True)
   second.page(fresh,[edge()],'',True)
  self.assertEqual(self.s.status(self.p)['opportunities'],1)
 def test_page_checkpoint_restart(self):
  self.publish();self.schedule();c=self.s.claim(self.p,'one');self.s.page(c,[edge()],'page2',False)
  self.s.close();self.s=CycleStore(self.path,lambda:self.now)
  c=self.s.claim(self.p,'two');self.assertEqual(c['cursor'],'page2')
  self.s.page(c,[edge(person='c2',source='e2')],'',True)
  self.assertEqual(self.s.status(self.p)['relationships'],2)
 def test_scope_mismatch_rolls_back(self):
  self.publish();self.schedule();c=self.s.claim(self.p,'one')
  with self.assertRaisesRegex(CycleError,'page_scope'):self.s.page(c,[edge('2')],'',True)
  self.assertEqual(self.s.status(self.p)['sourceEdges'],0)
 def test_catalog_change_invalidates_inflight(self):
  self.publish();self.schedule();c=self.s.claim(self.p,'one');self.publish([offer(stock='0')],NOW+1)
  with self.assertRaisesRegex(CycleError,'catalog_changed'):self.s.page(c,[edge()],'',True)
  self.assertEqual(self.schedule()['pendingEstimatedCreators'],0)
  self.assertEqual(self.s.status(self.p)['jobs']['obsolete'],1)
 def test_inbound_debounce_does_not_mean_service_done(self):
  self.publish();self.s.import_edges(self.p,[edge()]);self.s.inbound(self.p,'c1','m1');self.now+=20;self.s.inbound(self.p,'c1','m2')
  self.assertEqual(self.s.db.execute('SELECT inbox_until FROM relationship').fetchone()[0],self.now+60)
  self.now+=70;self.assertEqual(self.s.status(self.p)['eligibleUniqueCreators'],0)
  self.s.resolve_inbound(self.p,'c1','resolve',3);self.assertEqual(self.s.status(self.p)['eligibleUniqueCreators'],1)
 def test_capacity_shrink_stops_new_claims(self):
  self.publish([offer('1'),offer('2')]);self.schedule(new_remaining=20)
  self.schedule(new_remaining=0);self.assertIsNone(self.s.claim(self.p,'worker'))
 def test_established_people_do_not_fill_new_contact_target(self):
  self.publish([offer('1'),offer('2')]);self.s.import_edges(self.p,[edge()]);self.s.inbound(self.p,'c1','reply');self.s.resolve_inbound(self.p,'c1','done',2)
  self.assertEqual(len(self.schedule(new_remaining=1)['created']),1)
 def test_eligibility_requires_same_pid_not_only_offer_key(self):
  self.publish();self.s.import_edges(self.p,[edge('2',offerKey='o1')])
  self.assertEqual(self.s.status(self.p)['eligibleUniqueCreators'],0)
 def test_readonly_status_does_not_mutate(self):
  self.publish()
  with CycleStore(self.path,lambda:self.now,readonly=True) as ro:
   self.assertEqual(ro.status(self.p)['relationships'],0)
   with self.assertRaises(Exception):ro.plan('new','it')
 def test_new_offer_projection_keeps_history_and_control(self):
  self.s.import_edges(self.p,[edge(offerKey='unknown')]);self.s.control(self.p,'pause',1,'paused','c1')
  self.publish();self.s.project_current_offers(self.p);self.s.project_current_offers(self.p)
  self.assertEqual(self.s.status(self.p)['opportunities'],1)
  self.assertEqual(self.s.status(self.p)['sourceEdges'],1)
  self.assertEqual(self.s.status(self.p)['eligibleUniqueCreators'],0)
  self.assertEqual(__import__('json').loads(self.s.db.execute('SELECT payload FROM source_edge').fetchone()[0])['offerKey'],'unknown')
 def test_old_window_does_not_fill_current_supply(self):
  self.publish();self.s.import_edges(self.p,[edge(windowStart='2026-08-26',windowEnd='2026-09-08')])
  self.assertEqual(self.s.status(self.p)['eligibleUniqueCreators'],1)
  self.assertEqual(len(self.schedule(new_remaining=1)['created']),1)
 def test_status_limits_offers_but_keeps_full_counts(self):
  self.publish([offer(str(i)) for i in range(100)])
  r=self.s.status(self.p);self.assertEqual(r['offerCount'],100);self.assertEqual(len(r['offers']),40)
 def test_live_catalog_supersedes_historical_commercial_view(self):
  self.s.publish(self.p,'italy-historical-source',NOW,[offer()])
  self.s.publish(self.p,'live-it-selected',NOW+1,[])
  self.assertEqual(self.s.status(self.p)['offerCount'],0)
  self.assertEqual(self.s.db.execute('SELECT count(*) FROM catalog').fetchone()[0],2)
 def test_no_execution_capability(self):
  self.assertFalse(self.s.status(self.p)['executionAllowed']);self.assertFalse(self.s.status(self.p)['legacyTrialControlIntegrated'])
 def test_latest_window_not_highest_historical_units(self):
  self.s.import_edges(self.p,[edge(units=100)]);self.s.import_edges(self.p,[edge(source='e2',units=1,observedAt=NOW+1)])
  self.assertEqual(self.s.db.execute('SELECT units FROM opportunity').fetchone()[0],1)

if __name__=='__main__':unittest.main()
