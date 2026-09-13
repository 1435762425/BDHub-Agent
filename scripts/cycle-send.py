#!/usr/bin/env python3
"""Prepare or execute one independently approved v4 cycle canary; no old trial reuse."""
import argparse,json,signal,sqlite3,sys,time,fcntl
from pathlib import Path
from contextlib import contextmanager
from dataclasses import asdict
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from lib.second_cycle import CycleStore,CycleError,digest
from lib.cycle_review import choose_candidates
from lib.cycle_materials import render
from lib.cycle_delivery import Deliveries
from lib.cycle_executor import execute
from lib.cycle_send_runtime import fresh_card
from lib.second_live_runtime import _authenticated,sender_binding_sha256,live_runtime
from lib.italy_im_session import ItalyImReadSession
from lib.italy_im_delivery import ItalyVerifiedProductCard

def history_eligible(history,now,own_current_refs=0):
 if not history.get('identityVerified') or history.get('hasMore'):raise CycleError('history_incomplete')
 counts=history['senderCounts']
 if counts.get('creatorReplies') or counts.get('showcaseNotifications'):raise CycleError('conversation_needs_content_review')
 if counts.get('otherOrUnknown'):raise CycleError('unknown_message_needs_review')
 times=history.get('outboundCreateTimeRaw',[])
 if any(type(t) is not int or not 946684800000<=t<=int(now*1000)+300000 for t in times) or len(times)!=counts['ourMessages'] or history.get('outboundTimeMissingCount'):raise CycleError('message_time_missing')
 # A conservative canary eligibility check, not a claim about the exact platform reset anchor.
 recent=[t for t in times if t>=int((now-32*86400)*1000)]
 if len(recent)>own_current_refs:raise CycleError('recent_contact_needs_allowance_review')

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['prepare','run']);p.add_argument('--handle');p.add_argument('--delivery-id');p.add_argument('--approved-hash');a=p.parse_args()
 def deadline(*_):raise TimeoutError('cycle_send_deadline')
 signal.signal(signal.SIGALRM,deadline);signal.setitimer(signal.ITIMER_REAL,55)
 report={'realSends':0};ROOT.joinpath('var/cycle-send').mkdir(exist_ok=True)
 with (ROOT/'var/cycle-send.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  with CycleStore(ROOT/'var/second-cycle.sqlite') as store:
   deliveries=Deliveries(store);plan=store.db.execute("SELECT id FROM plan WHERE market='it' AND institution='bjn-local-research'").fetchone()[0]
   if a.action=='prepare':
    if not a.handle:raise CycleError('handle_required')
    with sqlite3.connect((ROOT/'var/creator-identities.sqlite').as_uri()+'?mode=ro',uri=True) as ids:
     def person(c,o):
      r=ids.execute("SELECT current_handle FROM creator_identity WHERE market='it' AND creator_id=? AND oec_id=? AND handle_conflict=0",(c,o)).fetchone();return {'handle':r[0]} if r else None
     cs,_=choose_candidates(store,plan,person,100)
    matches=[c for c in cs if c['handle']==a.handle]
    if len(matches)!=1:raise CycleError('candidate_not_unique')
    c=matches[0]
    with sqlite3.connect((ROOT/'var/it-conversations.sqlite').as_uri()+'?mode=ro',uri=True) as idx:convs=idx.execute("SELECT cid FROM conversation WHERE scope='it:acc6' AND oec=? AND kind=2",(c['oecId'],)).fetchall()
    if len(convs)!=1:raise CycleError('known_conversation_required')
    c['conversationId']=convs[0][0];c['message']=render(c['name'],c['offer'],'standard',c['handle'])
    with _authenticated(report,stopped=lambda:False) as (account,identity,headers,auth,maintenance,available):
     with ItalyImReadSession(auth,report,maintenance_due=maintenance) as reads:
      conv=reads.conversation(c['conversationId'],c['oecId']);h=reads.history_summary(conv,include_sender_counts=True);history_eligible(h,time.time());c['historyAtPreparation']=h
     card,proof=fresh_card(c,account,identity,headers,maintenance,lambda:False);c['nativeCard']=asdict(card);c['card']=proof;c['senderBindingHash']=sender_binding_sha256(auth)
    d=deliveries.prepare(plan,c);path=ROOT/'var/cycle-send'/f"{d['id']}.json";path.write_text(json.dumps({'deliveryId':d['id'],'snapshotHash':digest(c),'candidate':c,'authorization':'required','expiresAt':d['expires']},ensure_ascii=False,indent=2));print(json.dumps({'deliveryId':d['id'],'snapshotHash':digest(c),'handle':c['handle'],'text':c['message']['textIt'],'report':str(path)},ensure_ascii=False))
   else:
    d=deliveries.get(a.delivery_id);c=d['snapshot']
    if d['state']=='confirmed':
     print(json.dumps({'deliveryId':d['id'],'state':'confirmed','alreadyConfirmed':True,'newRequests':0}));return
    def authorize(candidate):
     if a.approved_hash!=digest(candidate):raise CycleError('concrete_canary_approval_required')
    authorize(c)
    @contextmanager
    def runtime(candidate,read_only=False):
     previous=ItalyVerifiedProductCard(**candidate['nativeCard'])
     def validator(card,account,identity,headers,maintenance,stopped):
      current,_=fresh_card(candidate,account,identity,headers,maintenance,stopped)
      if current.binding_sha256!=previous.binding_sha256:raise CycleError('card_binding_changed')
      return current
     with live_runtime(candidate['senderBindingHash'],report,card_validator=validator,stopped=lambda:False) as rt:yield {**rt,'card':previous}
    def preflight(candidate,rt,conv):
     h=rt['reads'].history_summary(conv,include_sender_counts=True)
     confirmed=sum(p['state']=='confirmed' for p in deliveries.get(d['id'])['parts']);history_eligible(h,time.time(),confirmed)
    try:
     result=execute(deliveries,d['id'],runtime,authorize,preflight);print(json.dumps({'deliveryId':d['id'],'state':result['state']}))
    finally:
     current=deliveries.get(d['id']);report['confirmedComponents']=sum(p['state']=='confirmed' for p in current['parts']);report['attemptedComponents']=sum(p['started'] is not None for p in current['parts']);report.pop('realSends',None)
     (ROOT/'var/cycle-send'/f"{d['id']}.runtime.json").write_text(json.dumps(report,indent=2))
 signal.setitimer(signal.ITIMER_REAL,0)
if __name__=='__main__':main()
