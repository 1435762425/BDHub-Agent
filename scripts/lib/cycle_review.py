"""Immutable review batches, not executable sending trials."""
import json,time
from datetime import datetime,timezone
from lib.second_cycle import CycleError,encoded,digest,assess_offer
from lib.cycle_materials import select_offers,render,name_key
SCHEMA='''CREATE TABLE IF NOT EXISTS cycle_review_batch(id TEXT PRIMARY KEY,request_id TEXT NOT NULL UNIQUE,request_json TEXT NOT NULL,snapshot_hash TEXT NOT NULL,payload TEXT NOT NULL,created_at REAL NOT NULL);
CREATE TRIGGER IF NOT EXISTS review_no_update BEFORE UPDATE ON cycle_review_batch BEGIN SELECT RAISE(ABORT,'review is immutable'); END;
CREATE TRIGGER IF NOT EXISTS review_no_delete BEFORE DELETE ON cycle_review_batch BEGIN SELECT RAISE(ABORT,'review is immutable'); END;'''

def choose_candidates(store,plan,identity_reader,limit=3):
 if type(limit) is not int or not 1<=limit<=3:raise CycleError('review_limit')
 offers={o['pid']:o for o in select_offers(store,plan)}
 rows=store.db.execute('SELECT e.payload,r.creator_id,r.oec,r.evidence_ref FROM cycle_identity_resolution r JOIN source_edge e USING(plan_id,source_id) WHERE r.plan_id=?',(plan,)).fetchall()
 candidates=[];skipped=[]
 for row in rows:
  edge=json.loads(row['payload']);control=store.db.execute('SELECT * FROM relationship WHERE plan_id=? AND creator_id=?',(plan,row['creator_id'])).fetchone();o=offers.get(edge['pid'])
  if not o or not control or control['mode']!='auto' or control['rejected'] or control['inbox_until']:
   skipped.append({'sourceId':edge['sourceId'],'reason':'control_or_offer_not_ready'});continue
  person=identity_reader(row['creator_id'],row['oec'])
  if not person or not person.get('handle'):
   skipped.append({'sourceId':edge['sourceId'],'reason':'current_identity_missing'});continue
  named=store.db.execute('SELECT payload FROM cycle_product_name WHERE id=?',(name_key(o),)).fetchone()
  name=json.loads(named[0]) if named else None
  check=store.db.execute('SELECT payload FROM cycle_card_check WHERE plan_id=? AND offer_key=? AND offer_fingerprint=?',(plan,o['offerKey'],digest(o))).fetchone()
  card=json.loads(check[0]) if check else None
  if not name or not card or card.get('state')!='verified_read_only':
   skipped.append({'sourceId':edge['sourceId'],'reason':'material_or_card_missing'});continue
  candidates.append({'planRevision':store._plan(plan)['revision'],'creatorId':row['creator_id'],'oecId':row['oec'],'handle':person['handle'],'identityEvidence':row['evidence_ref'],'controlRevision':control['revision'],
   'pid':o['pid'],'offer':o,'offerFingerprint':digest(o),'materialKey':name_key(o),'name':name,'card':card,'source':edge,'relationshipUnlocked':bool(control['unlocked'])})
 candidates.sort(key=lambda r:(r['source']['windowEnd'],r['source']['units'],r['creatorId']),reverse=True)
 result=[];seen=set()
 for c in candidates:
  if c['oecId'] in seen:continue
  seen.add(c['oecId']);result.append(c)
  if len(result)==limit:break
 return result,skipped

class ReviewBatches:
 def __init__(self,store):self.store=store;store.db.executescript(SCHEMA)
 def existing(self,request_id,request):
  row=self.store.db.execute('SELECT * FROM cycle_review_batch WHERE request_id=?',(request_id,)).fetchone()
  if row:
   if row['request_json']!=encoded(request):raise CycleError('review_request_conflict')
   return json.loads(row['payload'])
 def create(self,request_id,request,candidates,evidence,skipped):
  previous=self.existing(request_id,request)
  if previous:return previous
  plan=self.store._plan(request['planId'])
  if plan['state']!='active':raise CycleError('plan_paused')
  items=[]
  for candidate in candidates:
   if candidate['planRevision']!=plan['revision']:raise CycleError('plan_changed')
   c=dict(candidate);c['message']=render(c['name'],c['offer'],request['template'],c['handle']);c['checks']=evidence[c['oecId']]
   # A partial local history and an unexplained permission flag are not a quota ledger.
   c['blockingReasons']=['v4_sender_not_connected','institution_market_quota_not_verified','remote_conversation_history_not_verified'];c['executionAllowed']=False
   if c['checks'].get('remoteHistory',{}).get('status')=='observed_summary':
    c['blockingReasons'].remove('remote_conversation_history_not_verified');c['blockingReasons'].append('remote_sender_counts_not_verified')
   history=c['checks'].get('remoteHistory',{}).get('history',{})
   counts=history.get('senderCounts',{})
   c['observedContactSignal']='creator_reply' if counts.get('creatorReplies',0)>0 else 'showcase_notification' if counts.get('showcaseNotifications',0)>0 else 'not_established'
   if c['observedContactSignal']!='not_established':
    c['blockingReasons'].remove('institution_market_quota_not_verified');c['blockingReasons'].append('interaction_evidence_not_applied_to_controller')
   if counts:
    if 'remote_sender_counts_not_verified' in c['blockingReasons']:c['blockingReasons'].remove('remote_sender_counts_not_verified')
    c['blockingReasons'].append('marketing_frequency_not_verified')
    if not c.get('relationshipUnlocked') and c['observedContactSignal']=='not_established' and counts.get('ourMessages',0)+2>5:
     c['blockingReasons'].append('message_allowance_window_unverified')
     raw=history.get('outboundCreateTimeRaw',[])
     valid=[v for v in raw if type(v) is int and 946684800000<=v<=int(self.store.clock()*1000)+300000]
     last=max(valid) if valid else None
     complete_times=len(valid)==counts['ourMessages'] and history.get('outboundTimeMissingCount',0)==0
     c['allowanceReview']={'state':'awaiting_window_verification','historicalOutboundObserved':counts['ourMessages'],'renewalPolicy':'monthly_user_confirmed','resetAt':None,'permanentExclusion':False,'lastObservedOutboundAt':datetime.fromtimestamp(last/1000,timezone.utc).isoformat() if last else None,'observedOutboundTimesComplete':complete_times,'elapsedDaysSinceObservedOutbound':int((self.store.clock()-last/1000)/86400) if last and complete_times else None,'automaticResetApplied':False}
   old=c['checks'].get('legacy',{})
   if old.get('manualState') in ('processing','pending_reply','rejected'):c['blockingReasons'].append('legacy_relationship_needs_review')
   if old.get('activeOrUnknownIntents',0)>0:c['blockingReasons'].append('legacy_delivery_needs_reconciliation')
   items.append(c)
  snapshot={'schema':'bdhub.cycle-review.v1','planId':request['planId'],'planRevision':plan['revision'],'items':items,'skipped':skipped,'preparedAt':self.store.clock(),'expiresAt':self.store.clock()+1800,'executionAllowed':False,'realSends':0}
  fingerprint=digest(snapshot);payload={**snapshot,'id':'review-'+fingerprint[:24],'snapshotHash':fingerprint}
  with self.store.tx():
   if self.store._plan(request['planId'])['revision']!=plan['revision']:raise CycleError('plan_changed')
   current={o['offerKey']:o for _,o in self.store._offers(request['planId'])}
   for c in candidates:
    if c['offer']['offerKey'] not in current or digest(current[c['offer']['offerKey']])!=c['offerFingerprint']:raise CycleError('offer_changed')
    r=self.store.db.execute('SELECT * FROM relationship WHERE plan_id=? AND creator_id=?',(request['planId'],c['creatorId'])).fetchone()
    if not r or r['revision']!=c['controlRevision'] or r['mode']!='auto' or r['rejected'] or r['inbox_until']:raise CycleError('relationship_changed')
   self.store.db.execute('INSERT INTO cycle_review_batch VALUES(?,?,?,?,?,?)',(payload['id'],request_id,encoded(request),fingerprint,encoded(payload),self.store.clock()))
  return payload

def latest_review(store,plan):
 if not store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cycle_review_batch'").fetchone():return None
 r=store.db.execute('SELECT payload FROM cycle_review_batch WHERE json_extract(payload,\'$.planId\')=? ORDER BY created_at DESC LIMIT 1',(plan,)).fetchone()
 if not r:return None
 payload=json.loads(r[0]);return {**payload,'expired':store.clock()>=payload['expiresAt']}
