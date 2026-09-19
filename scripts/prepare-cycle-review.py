#!/usr/bin/env python3
"""Read-only relationship review and local batch snapshot. No send/approval tools."""
import argparse,hashlib,importlib.util,json,sqlite3,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];LEGACY=ROOT.parent/'01-BDSystem-V2'
sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT/'scripts'))
from lib.legacy_runtime import configure_vendored_bdhub
configure_vendored_bdhub(root=ROOT,legacy_root=LEGACY)
from lib.second_cycle import CycleStore,CycleError
from lib.cycle_review import ReviewBatches,choose_candidates

def legacy_evidence(oecs):
 from bdhub import config
 from bdhub.hub.engine import make_engine
 from sqlalchemy import text
 engine=make_engine(config.load(),connect_timeout_seconds=5);result={}
 try:
  with engine.connect() as connection:
   with connection.begin():
    connection.execute(text('SET TRANSACTION READ ONLY'));connection.execute(text("SET LOCAL statement_timeout='15000ms'"))
    for oec in oecs:
     row=dict(connection.execute(text("SELECT count(*) FILTER(WHERE is_from_me=true) outbound,count(*) FILTER(WHERE is_from_me=false AND sender_role=1) replies,max(create_ms) latest_ms FROM im_message WHERE bd_market='it' AND creator_oec_id=:oec"),{'oec':oec}).mappings().one())
     manual=connection.execute(text("SELECT status FROM creator_im_state WHERE bd_market='it' AND creator_identity_key=:key"),{'key':'oec:'+oec}).scalar_one_or_none()
     active=connection.execute(text("SELECT count(*) FROM im_delivery_intent WHERE bd_market='it' AND status NOT IN ('sent','failed') AND conversation_id IN (SELECT conversation_id FROM im_conversation WHERE bd_market='it' AND creator_oec_id=:oec)"),{'oec':oec}).scalar_one()
     result[oec]={'status':'observed','localOutboundCount':row['outbound'],'localReplyCount':row['replies'],'lastMessageMs':row['latest_ms'],'manualState':manual,'activeOrUnknownIntents':active,'coverage':'legacy IT database only; absence is not no remote history','observedAt':time.time()}
 finally:engine.dispose()
 return result

def platform_evidence(oecs):
 from bdhub.send.taplink.transport import account_for
 from bdhub.hub.markets import identity_for,require_capability
 from bdhub.enrich.identity_store import load_identity
 from bdhub import scheduled_relogin
 from lib.italy_cards import legacy_params
 import requests
 cfg,account=account_for('it','acc6',check_maintenance=False)
 # This GET reads relationship signals; require_im() is a legacy send gate.
 require_capability('it','im')
 identity=identity_for('it',account=account,cfg=cfg).require_monitor()
 if not identity.partner_id_is_own:raise CycleError('identity_not_own')
 spec=importlib.util.spec_from_file_location('review_guard',ROOT/'scripts/probe-italy-profile.py');guard=importlib.util.module_from_spec(spec);spec.loader.exec_module(guard)
 result={};before=hashlib.sha256(Path(account.headers_json).read_bytes()).hexdigest()
 with guard.readonly_guard(account),requests.Session() as session:
  session.trust_env=False;headers={k:v for k,v in load_identity(account.headers_json).headers.items() if not k.startswith(':') and k.lower() not in ('host','content-length','origin','referer')};headers.update(origin='https://partner.eu.tiktokshop.com',referer='https://partner.eu.tiktokshop.com/');params=legacy_params(identity,account)
  for oec in oecs:
   if scheduled_relogin.maintenance_due(account,initialize=False,ignore_retry_throttle=True):raise CycleError('maintenance_due')
   time.sleep(1);r=session.get(identity.host+'/api/v1/affiliate/partner/im/collaboration/get',params=params|{'creator_id':oec},headers=headers,timeout=(5,20),allow_redirects=False)
   item={'observedAt':time.time(),'http':r.status_code,'status':'unavailable','semantics':'relationship signal only; not verified message quota permission'}
   if r.status_code==200 and not r.headers.get('bdturing-verify'):
    b=r.json()
    if type(b.get('code')) is int and b['code']==0:
     item.update(status='observed',hasPermission=b.get('has_permission') if type(b.get('has_permission')) is bool else None,collaborationTypes=b.get('collaboration_type') if isinstance(b.get('collaboration_type'),list) and all(type(v) is int for v in b['collaboration_type']) else None,responseHash=hashlib.sha256(r.content).hexdigest())
   result[oec]=item
 if hashlib.sha256(Path(account.headers_json).read_bytes()).hexdigest()!=before:raise CycleError('identity_file_changed')
 return result

def remote_history_evidence(oecs):
 from lib.second_live_runtime import _authenticated
 from lib.italy_im_session import ItalyImReadSession
 import signal
 result={o:{'status':'not_located_in_sample','completeSearch':False} for o in oecs};report={}
 prior_path=ROOT/'var/italy-im-verify-20260912-first/conversations.private.json'
 if prior_path.exists():
  prior=json.loads(prior_path.read_text())
  if prior.get('market')=='it' and prior.get('account')=='acc6':
   for c in prior.get('verified',[]):
    if c.get('oecId') in result and c.get('oecAndTicketVerified'):
     result[c['oecId']]['priorObservation']={'observedAt':prior['observedAt'],'history':c['history'],'conversationId':c['conversationId']}
 old=signal.getsignal(signal.SIGALRM)
 def timeout(*args):raise TimeoutError()
 signal.signal(signal.SIGALRM,timeout);signal.setitimer(signal.ITIMER_REAL,60)
 try:
  with _authenticated(report,stopped=lambda:False) as (_,_,_,auth,maintenance,available):
   reads=ItalyImReadSession(auth,report,maintenance_due=maintenance)
   try:
    cursor=0;seen=set();found={}
    index_path=ROOT/'var/it-conversations.sqlite'
    if index_path.exists():
     with sqlite3.connect(index_path.as_uri()+'?mode=ro',uri=True) as index:
      for o in oecs:
       rows=index.execute('SELECT cid,kind FROM conversation WHERE scope=? AND oec=?',('it:acc6',o)).fetchall()
       if len(rows)==1:found[o]={'conversationId':rows[0][0],'conversationType':rows[0][1]}
       elif len(rows)>1:result[o]['indexAmbiguous']=True
    for _ in range(0 if len(found)==len(oecs) else 3):
     page=reads.initialize(cursor)
     for c in page['conversations']:
      if c['oecId'] in result:
       o=c['oecId']
       if o in found and found[o]['conversationId']!=c['conversationId']:result[o]['indexAmbiguous']=True
       if not result[o].get('indexAmbiguous'):found[o]=c
     if len(found)==len(oecs) or not page['hasMore']:break
     next_cursor=int(page['nextCursor'])
     if next_cursor==cursor or next_cursor in seen:break
     seen.add(cursor);cursor=next_cursor
    for o in oecs:
     if result[o].get('indexAmbiguous'):
      result[o]['status']='ambiguous_conversations';continue
     c=found.get(o)
     if not c and result[o].get('priorObservation'):c={'conversationId':result[o]['priorObservation']['conversationId'],'conversationType':2}
     if c:
      conversation=reads.conversation(c['conversationId'],o,conversation_type=c['conversationType'])
      result[o].update(status='observed_summary',conversationId=c['conversationId'],history=reads.history_summary(conversation,include_sender_counts=True),observedAt=time.time(),evidenceRef=conversation.evidence_ref)
   finally:reads.close()
 except Exception as error:
  for o in result:
   if result[o]['status']!='observed_summary':result[o].update(status='read_unavailable',errorCode=getattr(error,'code',type(error).__name__))
 finally:
  signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,old)
 return result


def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--request-id',required=True);p.add_argument('--report',type=Path,required=True);a=p.parse_args()
 if not a.report.resolve().is_relative_to(ROOT/'var') or a.report.exists():p.error('new var report required')
 with CycleStore(ROOT/'var/second-cycle.sqlite') as store:
  plan=store.db.execute("SELECT id FROM plan WHERE institution='bjn-local-research' AND market='it'").fetchone()[0];reviews=ReviewBatches(store);request={'planId':plan,'limit':3,'template':'standard'}
  result=reviews.existing(a.request_id,request)
  if result is None:
   with sqlite3.connect((ROOT/'var/creator-identities.sqlite').as_uri()+'?mode=ro',uri=True) as ids:
    def person(creator,oec):
     row=ids.execute("SELECT current_handle FROM creator_identity WHERE market='it' AND creator_id=? AND oec_id=? AND handle_conflict=0",(creator,oec)).fetchone()
     return {'handle':row[0]} if row else None
    candidates,skipped=choose_candidates(store,plan,person)
   if not candidates:raise CycleError('no_review_candidates')
   oecs=[c['oecId'] for c in candidates];old=legacy_evidence(oecs);remote=platform_evidence(oecs);history=remote_history_evidence(oecs)
   result=reviews.create(a.request_id,request,candidates,{o:{'legacy':old[o],'platform':remote[o],'remoteHistory':history[o]} for o in oecs},skipped)
  a.report.parent.mkdir(parents=True,exist_ok=True);a.report.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
  print(json.dumps({'batchId':result['id'],'people':len(result['items']),'executionAllowed':False,'report':str(a.report)},ensure_ascii=False))
if __name__=='__main__':main()
