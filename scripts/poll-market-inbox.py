#!/usr/bin/env python3
"""Read-only BR/MY/UK IM discovery and durable inbox ingestion."""
import argparse,fcntl,hashlib,json,os,signal,sys,time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.dont_write_bytecode=True
sys.path.insert(0,str(ROOT/'scripts'))
from lib.cycle_inbox import Inbox,inbox_status
from lib.cycle_service import Service
from lib.login_recovery import AUTH_REQUIRED,request_refresh
from lib.market_im_runtime import authenticated
from lib.reply_events import backfill
from lib.second_cycle import CycleStore

STOP=False
def stop(*_):
 global STOP;STOP=True

def _paths(market):
 return (ROOT/f'var/market-inbox-{market}.json',ROOT/f'var/market-inbox-cursor-{market}.json',ROOT/f'var/market-inbox-{market}.lock')

def _cursor(path):
 try:return int(json.loads(path.read_text(encoding='utf-8')).get('cursor') or 0)
 except (OSError,ValueError,TypeError):return 0

def _atomic(path,value):
 tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');tmp.replace(path)

def failure_state(error):
 return 'waiting_account' if type(error).__name__=='ProfileBusyError' else 'attention'

def _merge_targets(store,plan,recent,older,limit):
 """Give recent discoveries a bounded share and keep the oldest checkpoint moving."""
 known={row[0]:(row[1],row[2]) for row in store.db.execute(
  'SELECT cid,oec,checked_at FROM inbox_checkpoint WHERE plan_id=?',(plan,))}
 valid_oecs={row[0] for row in store.db.execute('SELECT oec FROM relationship WHERE plan_id=?',(plan,))}
 def valid(item):
  cid=str(item.get('conversationId') or '');oec=str(item.get('oecId') or '')
  return item.get('conversationType')==2 and cid.isascii() and cid.isdigit() and oec.isascii() and oec.isdigit()
 head=[];seen=set()
 for item in recent+older:
  if not valid(item):continue
  cid=str(item['conversationId']);oec=str(item['oecId'])
  if cid in seen:continue
  if cid in known and known[cid][1]>store.clock()-90:continue
  if cid in known and known[cid][0]!=oec:continue
  if oec not in valid_oecs:continue
  seen.add(cid);head.append((cid,oec))
 from lib.observed_messages import missing_body_targets
 repair=[(cid,oec) for cid,oec in missing_body_targets(store.db,plan,limit)
         if oec in valid_oecs and (cid not in known or known[cid][0]==oec)]
 head=[item for item in head if item not in repair]
 hot_count=max(1,limit//2)
 hot=(repair+head[:hot_count])[:limit]
 cold=sorted(((stamp,cid,oec) for cid,(oec,stamp) in known.items()
              if oec in valid_oecs and cid not in {item[0] for item in hot}),key=lambda row:(row[0],row[1]))
 selected=hot+[(cid,oec) for _,cid,oec in cold[:limit-len(hot)]]
 if len(selected)<limit:selected.extend([item for item in head[hot_count:] if item not in selected][:limit-len(selected)])
 return selected

def tick(market,limit=12):
 report={'market':market,'processed':0,'added':0,'historical':0,'liveReplies':0,'platformWrites':0,'realSends':0}
 status_path,cursor_path,_=_paths(market)
 with CycleStore(ROOT/'var/second-cycle.sqlite') as store:
  plan_row=store.db.execute("SELECT id FROM plan WHERE market=? AND institution='bjn-local-research' AND state='active'",(market,)).fetchone()
  if not plan_row:return report|{'state':'plan_paused'}
  plan=plan_row[0];inbox=Inbox(store);service=Service(store)
  if (ROOT/f'var/market-inbox-{market}.pause').exists():return report|{'state':'paused'}
  stage='auth';target=None
  try:
   with authenticated(ROOT,market,report,read_only=True,stopped=lambda:STOP) as runtime:
    _atomic(status_path,{'market':market,'pid':os.getpid(),'state':'scanning','checkedAt':time.time()})
    stage='discovery';session=runtime['session'];recent=session.initialize(0);older=[]
    cursor=_cursor(cursor_path)
    if cursor:
     page=session.initialize(cursor);older=page['conversations']
     _atomic(cursor_path,{'cursor':int(page['nextCursor']) if page['hasMore'] else 0})
    elif recent['hasMore']:
     _atomic(cursor_path,{'cursor':int(recent['nextCursor'])})
    targets=_merge_targets(store,plan,recent['conversations'],older,limit)
    report['indexedTargets']=store.db.execute('SELECT count(*) FROM inbox_checkpoint WHERE plan_id=?',(plan,)).fetchone()[0]
    report['recentConversations']=len(recent['conversations'])
    for cid,oec in targets:
     if STOP or (ROOT/f'var/market-inbox-{market}.pause').exists():break
     stage='conversation';target=(cid,oec)
     conversation=session.conversation(cid,oec)
     history=session.history_summary(conversation,include_events=True,include_contents=True)
     stage='ingest'
     result=inbox.ingest(plan,cid,oec,history)
     stage='capture'
     service.capture(plan,cid,oec,history.get('contents',[]))
     report['processed']+=1
     for key in ('added','historical','liveReplies'):report[key]+=result[key]
   if store.db.execute("SELECT 1 FROM sqlite_master WHERE name='inbound_turn'").fetchone():
    projection=backfill(store);report['replyProjection']={key:projection[key] for key in ('episodesAdded','turnsAdded','linksAdded')}
   report['state']='completed'
  except Exception as error:
   report['state']=failure_state(error)
   report['errorCode']=getattr(error,'code',None) or (str(error) if isinstance(error,ValueError) else type(error).__name__)
   report['failureStage']=stage
   if stage=='auth' and getattr(error,'platform_code',None)==AUTH_REQUIRED:
    # A lapsed communications login stops inbox, replies and sends alike: ask for one refresh of this generation.
    report['accountRecovery']=request_refresh(store,ROOT,market)
   if target:
    report['failureTargetHash']=hashlib.sha256(f'{plan}:{target[0]}:{target[1]}'.encode()).hexdigest()[:16]
    report['failureTargetRelationPresent']=bool(store.db.execute(
     'SELECT 1 FROM relationship WHERE plan_id=? AND oec=?',(plan,target[1])).fetchone())
    report['failureTargetCheckpointPresent']=bool(store.db.execute(
     'SELECT 1 FROM inbox_checkpoint WHERE plan_id=? AND cid=? AND oec=?',(plan,*target)).fetchone())
  report['status']=inbox_status(store,plan);report['checkedAt']=time.time()
  return report

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--market',required=True,choices=('br','my','uk'))
 p.add_argument('--worker',action='store_true');p.add_argument('--limit',type=int,default=20);p.add_argument('--interval',type=int,default=10)
 a=p.parse_args()
 if not 1<=a.limit<=30 or not 10<=a.interval<=3600:p.error('invalid interval or limit')
 signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
 status_path,_,lock_path=_paths(a.market)
 with lock_path.open('a') as lock:
  try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:raise SystemExit('market_inbox_worker_busy')
  while not STOP:
   result=tick(a.market,a.limit);result['pid']=os.getpid();result['running']=a.worker and not STOP
   _atomic(status_path,result)
   print(json.dumps({key:result.get(key) for key in ('market','state','processed','liveReplies','errorCode')},ensure_ascii=False),flush=True)
   if not a.worker:break
   delay=3 if result.get('state')=='waiting_account' else max(60,a.interval) if result.get('state')=='attention' else a.interval
   for _ in range(delay):
    if STOP:break
    time.sleep(1)
if __name__=='__main__':main()
