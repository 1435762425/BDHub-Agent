"""No network: persistent windows, fair fragments, bounded failures and per-market quota."""
import json,sqlite3,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from lib.second_cycle import CycleStore,CycleError
from lib.schema_migrations import apply_database
from lib import rolling_leads as queue
from lib.kalodata_video_evidence import VIDEO_LIST_PATH,VIDEO_DETAIL_PATH

NOW=1790326800.0
PIDS=[str(1729000000000000000+i) for i in range(105)]

class Provider:
 def __init__(self,request):self.request=request
 def __enter__(self):return self
 def __exit__(self,*_):return False

class RollingLeads(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
  (self.root/'var').mkdir();(self.root/'config').mkdir()
  for name in ('markets.json','leads-queue.json','operations-policy.json','market-accounts.json'):
   (self.root/'config'/name).write_bytes((ROOT/'config'/name).read_bytes())
  with CycleStore(self.root/'var/second-cycle.sqlite') as store:
   self.plans={m:store.plan('bjn-local-research',m) for m in ('it','br','my','uk')}
  apply_database(self.root,'second-cycle')
 def tearDown(self):self.temp.cleanup()
 def scope(self,*pids):return {pid:{'units':100,'readyAt':NOW-100,'key':'material-'+pid} for pid in pids}
 def row(self,market,pid,kind):
  with CycleStore(self.root/'var/second-cycle.sqlite',readonly=True) as store:
   return dict(store.db.execute('SELECT * FROM lead_query_task WHERE market=? AND pid=? AND kind=?',(market,pid,kind)).fetchone())
 def test_tasks_are_unique_and_a_b_rotate_across_restarts(self):
  scope=self.scope(*PIDS[:2]);queue.sync(self.root,'it',at=NOW,scope=scope)
  queue.sync(self.root,'it',at=NOW+1,scope=scope)
  order=[]
  for _ in range(6):
   task=queue.freeze(self.root,queue.choose(self.root,'it',at=NOW),at=NOW)
   order.append((task['kind'],task['pid']))
   queue.settle(self.root,task,{'status':'yielded','networkRequests':3},at=NOW)
  self.assertEqual(order,[('A',PIDS[0]),('B',PIDS[0]),('A',PIDS[0]),('B',PIDS[0]),('A',PIDS[1]),('B',PIDS[1])])
  self.assertEqual(queue.status(self.root,'it',at=NOW)['types']['A']['total'],2)
 def test_old_due_refresh_beats_new_first_time_and_ties_use_sales(self):
  queue.sync(self.root,'it',at=NOW,scope=self.scope(PIDS[0]))
  task=queue.freeze(self.root,queue.choose(self.root,'it',at=NOW,kind='A'),at=NOW)
  queue.settle(self.root,task,{'status':'completed','networkRequests':1},at=NOW)
  later=NOW+8*86400;scope=self.scope(*PIDS[:3]);scope[PIDS[1]]['readyAt']=later;scope[PIDS[2]]['readyAt']=later;scope[PIDS[2]]['units']=500
  queue.sync(self.root,'it',at=later,scope=scope)
  first=queue.freeze(self.root,queue.choose(self.root,'it',at=later,kind='A'),at=later)
  self.assertEqual(first['pid'],PIDS[0])
  queue.settle(self.root,first,{'status':'completed','networkRequests':1},at=later)
  self.assertEqual(queue.choose(self.root,'it',at=later,kind='A')['pid'],PIDS[2])
 def test_success_waits_seven_days_and_material_pause_does_not_reset_it(self):
  scope=self.scope(PIDS[0]);queue.sync(self.root,'it',at=NOW,scope=scope)
  task=queue.freeze(self.root,queue.choose(self.root,'it',at=NOW,kind='A'),at=NOW)
  queue.settle(self.root,task,{'status':'completed','networkRequests':1},at=NOW+50)
  queue.sync(self.root,'it',at=NOW+100,scope={});queue.sync(self.root,'it',at=NOW+101,scope=scope)
  self.assertIsNone(queue.choose(self.root,'it',at=NOW+7*86400-1,kind='A'))
  queue.sync(self.root,'it',at=NOW+7*86400,scope=scope)
  new=queue.freeze(self.root,queue.choose(self.root,'it',at=NOW+7*86400,kind='A'),at=NOW+7*86400)
  self.assertNotEqual(task['query_id'],new['query_id'])
 def test_quota_keeps_original_window_attempts_and_other_market_runs(self):
  for m in ('it','uk'):queue.sync(self.root,m,at=NOW,scope=self.scope(PIDS[0]))
  task=queue.freeze(self.root,queue.choose(self.root,'it',at=NOW,kind='A'),at=NOW)
  queue.failed(self.root,task,'kalodata_daily_quota_exhausted',at=NOW)
  self.assertIsNone(queue.choose(self.root,'it',at=NOW+3599))
  self.assertIsNotNone(queue.choose(self.root,'uk',at=NOW))
  self.assertEqual(self.row('it',PIDS[0],'A')['attempts'],0)
  resumed=queue.choose(self.root,'it',at=NOW+86400,kind='A')
  self.assertEqual(queue.freeze(self.root,resumed,at=NOW+86400)['window_end'],task['window_end'])
  self.assertEqual(resumed['ready_at'],task['ready_at'])
 def test_three_technical_failures_isolate_only_that_pid(self):
  scope=self.scope(*PIDS[:2]);queue.sync(self.root,'it',at=NOW,scope=scope)
  task=queue.freeze(self.root,queue.choose(self.root,'it',at=NOW,kind='A'),at=NOW)
  for n in range(3):
   task=self.row('it',task['pid'],'A');queue.failed(self.root,task,'kalodata_video_read_failed',at=NOW+n*4000)
  self.assertEqual(self.row('it',PIDS[0],'A')['state'],'isolated')
  self.assertEqual(queue.choose(self.root,'it',at=NOW+9000,kind='A')['pid'],PIDS[1])
  queue.sync(self.root,'it',at=NOW+86400,scope=scope)
  self.assertEqual(self.row('it',PIDS[0],'A')['state'],'isolated')
 def test_a50_handles_invalid_rows_duplicates_and_cross_day_page_resume(self):
  queue.sync(self.root,'it',at=NOW,scope=self.scope(PIDS[0]))
  task=queue.freeze(self.root,queue.choose(self.root,'it',at=NOW,kind='A'),at=NOW)
  calls=[]
  def reader(path,payload):
   calls.append((payload['pageNo'],payload['startDate'],payload['endDate']))
   start=(payload['pageNo']-1)*50
   rows=[{'id':f'creator-{i}','handle':f'handle_{i}','sale':1,'revenue':1000-i} for i in range(start,start+50)]
   if payload['pageNo']==1:rows[0]['handle']='bad handle'
   else:rows[0]={'id':'creator-1','handle':'handle_1','sale':1,'revenue':999}
   return {'success':True,'data':rows}
  first=queue.read_a(self.root,task,reader,max_requests=1,clock=lambda:NOW)
  self.assertEqual(first['status'],'yielded')
  with CycleStore(self.root/'var/second-cycle.sqlite',readonly=True) as store:
   self.assertEqual(store.db.execute('SELECT count(*) FROM lead_query_head').fetchone()[0],0)
  final=queue.read_a(self.root,task,reader,max_requests=1,clock=lambda:NOW+86400)
  self.assertEqual(final['selected'],50);self.assertEqual([c[0] for c in calls],[1,2]);self.assertEqual(calls[0][1:],calls[1][1:])
  with CycleStore(self.root/'var/second-cycle.sqlite',readonly=True) as store:
   self.assertEqual(store.db.execute('SELECT count(*) FROM lead_query_selection').fetchone()[0],50)
   self.assertEqual(store.db.execute('SELECT policy_version FROM lead_query_run').fetchone()[0],queue.POLICIES['A'])
 def test_b_fragment_preserves_previous_result_and_market_cache_scope(self):
  calls=[]
  def reader(path,payload):
   calls.append((path,payload['id']))
   if path==VIDEO_LIST_PATH:return {'success':True,'data':[
    {'id':'7674344776354860309','views':999,'create_time':'2026/09/22'},
    {'id':'7674344776354860308','views':1000,'create_time':'2026/09/21'}]}
   return {'success':True,'data':{'id':payload['id'],'handle':'video.person','creator_id':'7592705921965884434','views':1000,'release_time':'2026/09/21','sale':0}}
  for market in ('it','br','my','uk'):
   queue.sync(self.root,market,at=NOW,scope=self.scope(PIDS[0]))
   task=queue.freeze(self.root,queue.choose(self.root,market,at=NOW,kind='B'),at=NOW)
   first=queue.read_b(self.root,task,reader,max_requests=1,clock=lambda:NOW)
   self.assertEqual(first['status'],'yielded')
   with CycleStore(self.root/'var/second-cycle.sqlite',readonly=True) as store:
    self.assertEqual(store.db.execute('SELECT count(*) FROM video_lead_current WHERE market=?',(market,)).fetchone()[0],0)
   second=queue.read_b(self.root,task,reader,max_requests=1,clock=lambda:NOW+1)
   self.assertEqual(second['leads'],1)
  self.assertEqual(sum(p==VIDEO_DETAIL_PATH for p,_ in calls),4)
  with CycleStore(self.root/'var/second-cycle.sqlite',readonly=True) as store:
   self.assertEqual(store.db.execute('SELECT count(*) FROM video_lead_current').fetchone()[0],4)
   self.assertEqual(store.db.execute("SELECT count(*) FROM current_identity_source WHERE source_kind='kalodata_video'").fetchone()[0],4)
 def test_scheduled_pause_performs_no_provider_initialization(self):
  result=queue.run(self.root,'it',scope=self.scope(PIDS[0]),scheduled=True,
                   provider_factory=lambda *_:(_ for _ in ()).throw(AssertionError('network forbidden')))
  self.assertEqual(result['stopped'],'automation_paused')
 def test_a_complete_empty_result_is_success_not_a_failure(self):
  calls=[]
  def provider(root,task):return Provider(lambda path,payload:(calls.append(payload) or {'success':True,'data':[]}))
  result=queue.run(self.root,'it',kind='A',scope=self.scope(PIDS[0]),provider_factory=provider,clock=lambda:NOW)
  self.assertEqual((result['completed'],result['A'],result['networkRequests']),(1,1,1))
  self.assertEqual(self.row('it',PIDS[0],'A')['state'],'waiting')

 def test_daily_continuation_runs_without_new_catalog_and_preserves_stop_switch(self):
  from lib.operations_scheduler import _collect_ready_runs
  from lib.jobs import load as jobs
  queue.sync(self.root,'it',at=NOW,scope=self.scope(PIDS[0]))
  with CycleStore(self.root/'var/second-cycle.sqlite',clock=lambda:NOW) as store:
   with store.tx():store.db.execute("INSERT INTO market_automation_setting VALUES('it',1,0,0,1,?)",(NOW,))
   with patch('lib.operations_scheduler._background'),patch('lib.operations_scheduler._scheduled_sources',return_value=([],{'campaign':NOW+86400})):
    runs,_,_=_collect_ready_runs(self.root,store,jobs(self.root),NOW,{'nextDue':{},'error':None})
   run=next(r for r in runs if r['market']=='it')
   self.assertEqual([(s['stage'],s['state']) for s in run['stages'][:4]],
                    [('taplink_clean','skipped'),('catalog','skipped'),('taplink_prepare','skipped'),('kalodata','queued')])
   with store.tx():store.db.execute("UPDATE market_automation_setting SET automatic_operations_enabled=0 WHERE market='it'")
  self.assertFalse(queue.automatic_enabled(self.root,'it'))

 def test_freeze_refuses_currency_drift_and_does_not_change_original_window(self):
  queue.sync(self.root,'it',at=NOW,scope=self.scope(PIDS[0]))
  task=queue.freeze(self.root,queue.choose(self.root,'it',at=NOW,kind='A'),at=NOW)
  with patch('lib.market_registry.market',return_value={'currency':'USD'}):
   with self.assertRaisesRegex(CycleError,'kalodata_query_scope_changed'):queue.freeze(self.root,task,at=NOW+86400)
  self.assertEqual(self.row('it',PIDS[0],'A')['window_end'],task['window_end'])

 def test_b_window_boundary_day_with_fifty_rows_reads_next_page(self):
  from lib.kalodata_video_evidence import parse_video_list
  rows=[{'id':str(7674344776354860300+i),'views':999,'create_time':'2026/08/25'} for i in range(50)]
  parsed=parse_video_list({'success':True,'data':rows},PIDS[0],window_start='2026-08-25',window_end='2026-09-23')
  self.assertFalse(parsed['reachedWindowStart'])

 def test_hundredth_pid_page_three_resumes_after_quota_without_republishing_99(self):
  from lib.lead_selection import publish_query
  scope=self.scope(*PIDS[:100]);queue.sync(self.root,'it',at=NOW,scope=scope)
  start,end=queue.windows(NOW,'A')
  for pid in PIDS[:99]:
   publish_query(self.root,plan_id=self.plans['it'],query_id='complete-'+pid,pid=pid,edges=[],
                 receipt_fingerprints=['empty-'+pid],policy_version=queue.POLICIES['A'],window_start=start,window_end=end,at=NOW)
  with CycleStore(self.root/'var/second-cycle.sqlite') as store,store.tx():
   store.db.execute("UPDATE lead_query_task SET state='waiting',last_completed=?,next_due=? WHERE kind='A' AND pid<>?",(NOW,NOW+7*86400,PIDS[99]))
  task=queue.freeze(self.root,queue.choose(self.root,'it',at=NOW,kind='A'),at=NOW);calls=[]
  self.assertEqual(task['pid'],PIDS[99])
  def first(path,payload):
   page=payload['pageNo'];calls.append(page)
   if page==3:raise CycleError('kalodata_daily_quota_exhausted')
   count=20 if page==1 else 40
   return {'success':True,'data':[{'id':str(i),'handle':f'creator_{i}','sale':1 if i<count else 0,'revenue':1000-i} for i in range(50)]}
  with self.assertRaisesRegex(CycleError,'kalodata_daily_quota_exhausted'):queue.read_a(self.root,task,first,clock=lambda:NOW)
  queue.failed(self.root,task,'kalodata_daily_quota_exhausted',at=NOW)
  with CycleStore(self.root/'var/second-cycle.sqlite',readonly=True) as store:
   before=dict(store.db.execute('SELECT pid,query_id FROM lead_query_head'))
   self.assertEqual(len(before),99)
  def resumed(path,payload):
   calls.append(payload['pageNo']);self.assertEqual((payload['startDate'],payload['endDate']),(start,end))
   return {'success':True,'data':[{'id':str(i),'handle':f'creator_{i}','sale':1,'revenue':1000-i} for i in range(40,60)]}
  result=queue.read_a(self.root,task,resumed,clock=lambda:NOW+86400)
  self.assertEqual(result['selected'],50);self.assertEqual(calls,[1,2,3,3])
  with CycleStore(self.root/'var/second-cycle.sqlite',readonly=True) as store:
   after=dict(store.db.execute('SELECT pid,query_id FROM lead_query_head'))
   self.assertEqual({pid:after[pid] for pid in before},before)
   self.assertEqual(len(after),100)

 def test_old_identity_failure_does_not_block_reads_or_get_silently_retried(self):
  from lib.operations_workflow import create_run
  from lib.operations_scheduler import _create_lead_continuation
  with CycleStore(self.root/'var/second-cycle.sqlite',clock=lambda:NOW) as store:
   with store.tx():store.db.execute("INSERT INTO market_automation_setting VALUES('it',1,0,0,1,?)",(NOW,))
   old=create_run(store,market='it',trigger_source='manual',scheduled_at=NOW-100,only_stage='oecid')
   with store.tx():
    store.db.execute("UPDATE workflow_run SET state='needs_human',finished_at=? WHERE run_id=?",(NOW-1,old['runId']))
    store.db.execute("UPDATE workflow_stage_run SET state='needs_human',error_code='identity_failed',finished_at=? WHERE run_id=? AND stage='oecid'",(NOW-1,old['runId']))
   current=_create_lead_continuation(self.root,store,'it',NOW,old['runId'])
   stages={r['stage']:r['state'] for r in current['stages']}
   self.assertEqual(stages['kalodata'],'queued');self.assertEqual(stages['oecid'],'skipped')
   self.assertEqual(stages['send_pool'],'waiting_upstream')
   self.assertEqual(store.db.execute('SELECT state FROM workflow_run WHERE run_id=?',(old['runId'],)).fetchone()[0],'needs_human')
   self.assertEqual(queue.identity_hold(self.root,'it')['runId'],old['runId'])

 def test_valid_negative_identity_is_reused_for_new_window_and_market_remains_separate(self):
  from lib.market_identity import reuse_judgments,pending
  from lib.lead_selection import publish_query
  start,end=queue.windows(NOW,'A')
  for market in ('br','my'):
   edge={'sourceId':'current-'+market,'pid':PIDS[0],'sourceKind':'kalodata_http','sourceHandle':'same.handle',
         'sourceRank':1,'units':1,'windowStart':start,'windowEnd':end,'revenueValue':'10','currency':'BRL' if market=='br' else 'MYR'}
   publish_query(self.root,plan_id=self.plans[market],query_id='new-'+market,pid=PIDS[0],edges=[edge],receipt_fingerprints=['page'],at=NOW)
  with CycleStore(self.root/'var/second-cycle.sqlite') as store,store.tx():
   store.db.execute("INSERT INTO source_edge_index VALUES(?,?,?,?,1,1,?,?,'kalodata_http',NULL,NULL)",(self.plans['br'],'old-source',PIDS[1],'same.handle',start,end))
   store.db.execute("INSERT INTO cycle_identity_outcome VALUES(?,?,'unresolved')",(self.plans['br'],'old-source'))
  reuse_judgments(self.root,'br')
  self.assertEqual(pending(self.root,'br')['items'],[])
  self.assertEqual(len(pending(self.root,'my')['items']),1)

 def test_provider_initialization_failure_pauses_market_without_spending_pid_attempts(self):
  def bad_provider(*_):raise FileNotFoundError('private login file')
  result=queue.run(self.root,'it',scope=self.scope(*PIDS[:3]),provider_factory=bad_provider,clock=lambda:NOW)
  self.assertEqual(result['networkRequests'],0)
  self.assertEqual(result['stopped'],'kalodata_initialization_failed')
  self.assertEqual(result['queue']['control']['state'],'waiting_account')
  self.assertEqual(self.row('it',PIDS[0],'A')['attempts'],0)
  self.assertNotIn('private login file',json.dumps(result))

if __name__=='__main__':unittest.main()
