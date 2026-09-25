import sys,tempfile,unittest,json,hashlib
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.second_cycle import CycleStore,CycleError
from lib.cycle_delivery import Deliveries
from lib.schema_migrations import apply_database
from lib.outreach_allocation import select,summary,position,day
from test_second_cycle import NOW,offer,edge

class AllocationTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.now=NOW
  self.store=CycleStore(self.root/'var/second-cycle.sqlite',lambda:self.now);self.plan=self.store.plan('test','it')
  self.d=Deliveries(self.store);apply_database(self.root,'second-cycle')
  self.offer=offer(endAt=NOW+90*86400);self.store.publish(self.plan,'s',self.now,[self.offer]);self.counter=0
 def tearDown(self):self.store.close();self.tmp.cleanup()
 def candidate(self,creator,kind='A',source=None):
  sid=source or 'src-'+creator
  oec=str(int(hashlib.sha256(creator.encode()).hexdigest()[:15],16))
  self.store.import_edges(self.plan,[edge(person=creator,source=sid,oec=oec)])
  return {'creatorId':creator,'oecId':oec,'pid':offer()['pid'],'source':{'sourceId':sid,'sourceKind':'kalodata_http' if kind=='A' else 'kalodata_video','sourceClass':kind,
   'videoReleasedAt':datetime.fromtimestamp(self.now-3*86400,timezone.utc).date().isoformat()},'offer':self.offer,
   'planRevision':1,'controlRevision':1,'message':{'version':4,'deliveryOrder':'card_then_text','textIt':'frozen'}}
 def choose(self,candidates,cursor=None):
  if cursor is not None:self.store.db.execute('INSERT OR REPLACE INTO outreach_rotation VALUES(?,?,?)',('it',cursor,self.now))
  rows=[{'creatorId':c['creatorId'],'pid':c['pid'],'sourceClass':c['source']['sourceClass'],'gmv':str(1000-i),'units':1,'rank':i,'videoViews':10000-i,'videoReleasedAt':'2026-09-20'} for i,c in enumerate(candidates)]
  mapping={(c['creatorId'],c['pid'],c['source']['sourceClass']):c for c in candidates}
  return select(self.store,'it',self.plan,{'pools':{'ready':rows}},lambda r:mapping[(r['creatorId'],r['pid'],r['sourceClass'])])
 def freeze(self,c):
  d=self.d.prepare(self.plan,c);self.store.db.execute("UPDATE cycle_delivery SET state='cancelled' WHERE id=?",(d['id'],));return d
 def test_ten_distinct_recipients_are_four_a_then_b_twice(self):
  classes=[]
  for i in range(10):
   c=self.choose([self.candidate('a'+str(i),'A'),self.candidate('b'+str(i),'B')]);self.freeze(c);classes.append(c['sendAllocation']['actualClass'])
  self.assertEqual(classes,list('AAAABAAAAB'))
  value=summary(self.store.db,'it',self.now);self.assertEqual(value['arranged'],{'A':8,'B':2})
  self.assertEqual(value['outcomes']['A'],{'cancelled':8})
 def test_empty_side_borrows_without_debt_and_repeated_reads_do_not_advance(self):
  c=self.choose([self.candidate('only-b','B')]);self.assertEqual(position(self.store.db,'it'),0)
  self.assertEqual(c['sendAllocation']['borrowedReason'],'preferred_class_unavailable');self.freeze(c)
  self.assertEqual(position(self.store.db,'it'),1)
  for i in range(4):self.freeze(self.choose([self.candidate('aa'+str(i),'A'),self.candidate('bb'+str(i),'B')]))
  self.assertEqual(position(self.store.db,'it'),0)
  self.assertEqual(summary(self.store.db,'it',self.now)['arranged'],{'A':3,'B':2})
 def test_restart_and_midnight_keep_rotation_and_original_claim_day(self):
  c=self.choose([self.candidate('a1')],cursor=3);d=self.freeze(c);stamp=c['sendAllocation']['day']
  self.now+=86400;self.d=Deliveries(self.store)
  self.assertEqual(self.d.prepare(self.plan,c)['id'],d['id']);self.assertEqual(position(self.store.db,'it'),4)
  self.assertEqual(self.store.db.execute('SELECT day FROM outreach_allocation').fetchone()[0],stamp)
  next_c=self.choose([self.candidate('a2','A'),self.candidate('b2','B')]);self.assertEqual(next_c['sendAllocation']['actualClass'],'B')
  self.freeze(next_c);self.assertEqual(summary(self.store.db,'it',self.now)['arranged'],{'A':0,'B':1})
 def test_stale_cursor_and_failed_gate_roll_back_delivery_and_counter(self):
  c=self.choose([self.candidate('first')]);self.store.db.execute('INSERT INTO outreach_rotation VALUES(?,?,?)',('it',1,self.now))
  with self.assertRaisesRegex(CycleError,'outreach_allocation_stale'):self.d.prepare(self.plan,c)
  self.assertEqual(self.store.db.execute('SELECT count(*) FROM cycle_delivery').fetchone()[0],0)
  self.assertEqual(position(self.store.db,'it'),1)
  c=self.choose([self.candidate('second')]);self.store.db.execute("UPDATE relationship SET mode='human' WHERE creator_id='second'")
  with self.assertRaisesRegex(CycleError,'relationship_changed'):self.d.prepare(self.plan,c)
  self.assertEqual(self.store.db.execute('SELECT count(*) FROM outreach_allocation').fetchone()[0],0)
 def test_capacity_refusal_is_before_allocation_and_never_changes_daily_limit(self):
  c=self.choose([self.candidate('limited')])
  with patch.object(self.d,'candidate_capacity_available',return_value=False):
   with self.assertRaisesRegex(CycleError,'new_contact_capacity_reached'):self.d.prepare(self.plan,c)
  self.assertEqual(position(self.store.db,'it'),0);self.assertEqual(self.store.db.execute('SELECT count(*) FROM cycle_delivery').fetchone()[0],0)
 def test_same_day_other_pid_cannot_allocate_recipient_again(self):
  c=self.choose([self.candidate('same')]);self.freeze(c)
  self.assertIsNone(self.choose([self.candidate('same',source='another-pid-source')]))
 def test_b_turn_does_not_take_a_creator_who_has_an_eligible_a_position(self):
  a=self.candidate('both','A');b=dict(a);b['source']=dict(a['source'],sourceClass='B',sourceKind='kalodata_video');b['pid']='other'
  other=self.candidate('b-only','B')
  chosen=self.choose([a,b,other],cursor=4);self.assertEqual(chosen['creatorId'],'b-only')
 def test_ineligible_a_allows_same_creators_b_without_losing_another_b_to_a_truncation(self):
  self.store.db.execute('INSERT INTO outreach_rotation VALUES(?,?,?)',('it',4,self.now))
  rows=[{'creatorId':'both','pid':'invalid-a','sourceClass':'A','gmv':'999'},
        {'creatorId':'both','pid':'valid-b','sourceClass':'B','videoViews':2000},
        *[{'creatorId':'a'+str(i),'pid':'p'+str(i),'sourceClass':'A','gmv':'5'} for i in range(250)]]
  seen=[]
  def build(row):
   seen.append(row['pid']);return None if row['pid']=='invalid-a' else {'creatorId':row['creatorId'],'pid':row['pid'],'source':{'sourceClass':row['sourceClass']}}
  chosen=select(self.store,'it',self.plan,{'pools':{'ready':rows[:1]+rows[2:],'queued':[rows[1]]}},build)
  self.assertEqual(chosen['pid'],'valid-b');self.assertEqual(position(self.store.db,'it'),4)
 def test_unknown_components_and_late_confirmation_do_not_recount(self):
  c=self.choose([self.candidate('unknown')]);d=self.d.prepare(self.plan,c)
  self.d.unknown(d['id'],'card');self.assertEqual(self.d.prepare(self.plan,c)['id'],d['id'])
  self.now+=86400;self.assertEqual(self.d.prepare(self.plan,c)['id'],d['id'])
  self.assertEqual(position(self.store.db,'it'),1);self.assertEqual(self.store.db.execute('SELECT count(*) FROM outreach_allocation').fetchone()[0],1)
 def test_two_entries_with_the_same_cursor_only_one_can_commit(self):
  first=self.choose([self.candidate('first')]);second=self.choose([self.candidate('second')])
  self.freeze(first)
  with self.assertRaisesRegex(CycleError,'outreach_allocation_stale'):self.d.prepare(self.plan,second)
  self.assertEqual(self.store.db.execute('SELECT count(*) FROM outreach_allocation').fetchone()[0],1)
  self.assertEqual(self.store.db.execute('SELECT count(*) FROM cycle_delivery').fetchone()[0],1)
 def test_midnight_between_selection_and_freeze_consumes_no_slot(self):
  c=self.choose([self.candidate('late')]);self.now+=86400
  with self.assertRaisesRegex(CycleError,'outreach_allocation_stale'):self.d.prepare(self.plan,c)
  self.assertEqual(position(self.store.db,'it'),0)
 def test_direct_duplicate_recipient_claim_rolls_back_even_with_new_source(self):
  c=self.choose([self.candidate('same')]);self.freeze(c)
  another=self.candidate('same',source='second-source');another['sendAllocation']={**c['sendAllocation'],'position':1}
  with self.assertRaisesRegex(CycleError,'outreach_recipient_already_allocated'):self.d.prepare(self.plan,another)
  self.assertEqual(position(self.store.db,'it'),1)
 def test_platform_quota_prefilter_skips_expensive_material_checks_but_keeps_unlocked(self):
  c=self.candidate('newcontact')
  rows=[{'creatorId':c['creatorId'],'pid':c['pid'],'sourceClass':'A'}];calls=[]
  with patch('lib.cycle_delivery.platform_rejections_today',return_value=1):
   with self.assertRaisesRegex(CycleError,'new_contact_capacity_reached'):
    select(self.store,'it',self.plan,{'pools':{'ready':rows}},lambda row:calls.append(row))
   self.assertEqual(calls,[])
   self.store.db.execute("UPDATE relationship SET unlocked=1 WHERE creator_id='newcontact'")
   picked=select(self.store,'it',self.plan,{'pools':{'ready':rows}},lambda row:c)
   self.assertEqual(picked['creatorId'],'newcontact')
 def test_other_market_has_independent_cursor_and_legacy_is_not_backfilled(self):
  c=self.candidate('old');self.d.prepare(self.plan,c)
  self.assertEqual(position(self.store.db,'it'),0);self.assertEqual(position(self.store.db,'uk'),0)
  self.assertEqual(summary(self.store.db,'it',self.now)['arranged'],{'A':0,'B':0})
  self.assertFalse(apply_database(self.root,'second-cycle')['appliedNow'])
