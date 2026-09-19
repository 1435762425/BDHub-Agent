"""Bounded recipient preparation lanes; each creator retains card-before-text confirmation.

One account guard/authentication per short cohort. Each lane owns its HTTP session,
sequence, SQLite connection and report. No transport/session is shared by threads.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from dataclasses import asdict
from pathlib import Path
import importlib.util
import json
import sqlite3
import threading
import time

from lib.second_cycle import CycleStore, CycleError, digest
from lib.cycle_delivery import Deliveries
from lib.cycle_executor import execute
from lib.cycle_materials import render
from lib.cycle_review import choose_candidates
from lib.cycle_send_runtime import descriptor, fresh_card
from lib.second_live_runtime import _authenticated, live_runtime, sender_binding_sha256
from lib.italy_im_delivery import ItalyVerifiedProductCard
from lib.cycle_inbox import Inbox
from lib.cycle_service import Service

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location('cycle_send_history', ROOT/'scripts/cycle-send.py')
_history = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_history)


class RequestBudget:
    """Aggregate IM reads + creates + sends; waiting lanes never create bursts."""
    def __init__(self, qps=3, *, clock=time.monotonic, sleep=time.sleep):
        if type(qps) not in (int, float) or not 1 <= qps <= 3:
            raise ValueError('invalid_request_budget')
        self.interval, self.clock, self.sleep = 1/qps, clock, sleep
        self.lock = threading.Lock(); self.next_at = 0; self.requests = 0; self.wait_seconds = 0

    def acquire(self):
        with self.lock:
            delay=max(0, self.next_at-self.clock()); self.wait_seconds+=delay
            self.sleep(delay); self.requests+=1
            self.next_at = self.clock()+self.interval


class CardCache:
    """Only a fresh exact offer/list proof, ten seconds, never stale-on-error."""
    def __init__(self, clock=time.monotonic):
        self.clock = clock; self.lock = threading.Lock(); self.entries = {}; self.key_locks = {}

    def get(self, candidate, load):
        key = digest([candidate['offer'], candidate['card']['listId']])
        with self.lock:
            key_lock = self.key_locks.setdefault(key, threading.Lock())
        with key_lock:
            prior = self.entries.get(key)
            if prior and self.clock()-prior[0] < 10:
                return prior[1]
            self.entries.pop(key, None)
            value = load()
            self.entries[key] = (self.clock(), value)
            return value


RUNTIME_CANDIDATE_KEYS = {'conversationId', 'nativeCard', 'senderBindingHash', 'frozenCandidateHash'}
LOCAL_MATERIAL_ERRORS = {'material_stale', 'offer_changed', 'offer_not_eligible',
                         'offer_currently_ineligible', 'card_binding_changed'}


def _frozen_hash(candidate):
    return digest({key: value for key, value in candidate.items() if key not in RUNTIME_CANDIDATE_KEYS})


def _set_batch_state(store, batch_id, state):
    store.db.execute('UPDATE cycle_bulk SET state=? WHERE id=?', (state, batch_id))
    if store.db.execute("SELECT 1 FROM sqlite_master WHERE name='cycle_bulk_freeze'").fetchone():
        store.db.execute('UPDATE cycle_bulk_freeze SET state=? WHERE batch_id=?', (state, batch_id))


def _must_abort(error):
    if getattr(error, 'outcome', None) == 'result_unknown':
        return True
    code = getattr(error, 'code', None) or (str(error) if isinstance(error, CycleError) else '')
    if code in ('new_contact_capacity_reached', 'delivery_unknown', 'reply_reconciliation_required'):
        return True
    return not isinstance(error, CycleError) and getattr(error, 'outcome', None) != 'rejected'


def _frozen_rows(store, batch_id):
    if not store.db.execute("SELECT 1 FROM sqlite_master WHERE name='cycle_bulk_candidate'").fetchone():
        return {}
    rows = {row['creator_id']: dict(row) for row in store.db.execute(
        'SELECT * FROM cycle_bulk_candidate WHERE batch_id=? ORDER BY position_order', (batch_id,))}
    for row in rows.values():
        candidate = json.loads(row['candidate_json'])
        if digest(candidate) != row['candidate_hash']:
            raise CycleError('frozen_candidate_corrupt')
        if any(str(value) != str(expected) for value, expected in (
                (candidate['creatorId'], row['creator_id']), (candidate['oecId'], row['oec']),
                (candidate['pid'], row['pid']), (candidate['source']['sourceId'], row['source_id']),
                (candidate['offer']['offerKey'], row['offer_key']),
                (candidate['card']['listId'], row['current_list_id']))):
            raise CycleError('frozen_candidate_corrupt')
        row['candidate'] = candidate
    return rows


def _local_card(store, frozen):
    """Validate only local current projections; sending must not remotely refresh TapLink."""
    candidate = frozen['candidate']
    current = {offer['offerKey']: offer for _, offer in store._offers(
        store.db.execute('SELECT plan_id FROM cycle_bulk WHERE id=?',
                         (frozen['batch_id'],)).fetchone()[0])}
    offer = current.get(candidate['offer']['offerKey'])
    if not offer or digest(offer) != candidate['offerFingerprint']:
        raise CycleError('material_stale')
    from lib.catalog_binding import offer_fingerprint
    expected_fingerprint = offer_fingerprint(offer)
    path = ROOT / 'var/catalog-links.sqlite'
    if not path.exists():
        raise CycleError('material_stale')
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)) as links:
        links.row_factory = sqlite3.Row
        row = links.execute("SELECT offer_fingerprint,list_id,state FROM catalog_current_binding "
                            "WHERE market='it' AND catalog_source=? AND pid=? AND campaign_id=?",
                            (str(offer.get('catalogSource') or ''), str(offer['pid']),
                             str(offer.get('campaignId') or ''))).fetchone()
    if not row or row['state'] != 'active' or row['offer_fingerprint'] != expected_fingerprint or \
            row['offer_fingerprint'] != frozen['offer_fingerprint'] or \
            str(row['list_id']) != str(frozen['current_list_id']) or \
            str(candidate['card']['listId']) != str(frozen['current_list_id']):
        raise CycleError('material_stale')
    return descriptor(candidate['card']), candidate['card']


def _cancel_material_delivery(store, delivery_id):
    if not delivery_id:
        return
    parts = list(store.db.execute('SELECT kind,state,started FROM cycle_delivery_part WHERE delivery_id=?',
                                  (delivery_id,)))
    if any(row['state'] in ('inflight', 'accepted', 'unknown') for row in parts):
        return
    card_confirmed = any(row['kind'] == 'card' and row['state'] == 'confirmed' for row in parts)
    store.db.execute("UPDATE cycle_delivery_part SET state='cancelled' WHERE delivery_id=? "
                     "AND state='ready' AND started IS NULL", (delivery_id,))
    store.db.execute('UPDATE cycle_delivery SET state=? WHERE id=?',
                     ('partial_delivery' if card_confirmed else 'cancelled_material_stale', delivery_id))


def _mark_material_waiting(candidate, error):
    try:
        from lib.catalog_binding import CatalogBindings
        bindings = CatalogBindings(ROOT)
        try:
            bindings.mark_waiting_refresh(candidate['offer'], reason='platform_card_rejected',
                                          evidence_ref=getattr(error, 'response_ref', None))
        finally:
            bindings.close()
        return True
    except (sqlite3.Error, ValueError):
        return False


def run_cohort(batch_id, *, limit=8, lanes=2, stopped=lambda: False):
    if type(limit) is not int or not 1 <= limit <= 12 or lanes not in (1, 2, 4):
        raise CycleError('invalid_cohort_scope')
    database = ROOT/'var/second-cycle.sqlite'
    with CycleStore(database) as s:
        batch = s.db.execute('SELECT * FROM cycle_bulk WHERE id=?', (batch_id,)).fetchone()
        if not batch or batch['state'] not in ('running','waiting_supply'):
            raise CycleError('bulk_not_running')
        auth_scope = json.loads(batch['authorization'])
        if auth_scope.get('source') != 'current_user_request' or auth_scope.get('maxPeople') != batch['target']:
            raise CycleError('bulk_scope_conflict')
        plan = batch['plan_id']
        done = s.db.execute("SELECT count(*) FROM cycle_bulk_item WHERE batch_id=? AND state IN ('confirmed','contacted_inquiry')", (batch_id,)).fetchone()[0]
        remaining = min(limit, batch['target']-done)
        if remaining <= 0: return {'items': [], 'state': 'completed'}
        items = [dict(r) for r in s.db.execute("SELECT * FROM cycle_bulk_item WHERE batch_id=? AND state IN ('sending','preparing','pending') AND retry_at<=? ORDER BY CASE state WHEN 'sending' THEN 0 WHEN 'preparing' THEN 1 ELSE 2 END,rowid LIMIT ?", (batch_id,time.time(),remaining))]
        # Recovery always gets its own cohort before any new candidate can dispatch.
        recovering = [i for i in items if i['delivery_id'] or s.db.execute("SELECT 1 FROM cycle_delivery WHERE plan_id=? AND creator_id=? AND state IN ('ready','running','unknown')", (plan,i['creator_id'])).fetchone()]
        if recovering: items = recovering[:1]
        if not items: return {'items': [], 'state': 'waiting_supply'}
        frozen_rows = _frozen_rows(s, batch_id)
        frozen = bool(frozen_rows)
        if frozen:
            if auth_scope.get('materialPolicy') != 'frozen-current-binding-v1' or \
                    any(item['creator_id'] not in frozen_rows for item in items):
                raise CycleError('frozen_batch_incomplete')
            candidates = {creator: row['candidate'] for creator, row in frozen_rows.items()}
        else:
            with closing(sqlite3.connect((ROOT/'var/creator-identities.sqlite').as_uri()+'?mode=ro', uri=True)) as ids:
                def person(c,o):
                    row=ids.execute("SELECT current_handle FROM creator_identity WHERE market='it' AND creator_id=? AND oec_id=? AND handle_conflict=0",(c,o)).fetchone()
                    return {'handle':row[0]} if row else None
                cs,_=choose_candidates(s,plan,person,100)
            candidates={c['creatorId']:c for c in cs}
    abort = threading.Event(); component_lock = threading.BoundedSemaphore(lanes)
    budget = RequestBudget(3); card_budget = RequestBudget(3); cards = CardCache(); auth_report = {}
    # Stop admission after 35s. An active group is allowed to finish/read back.
    admission_deadline = time.monotonic()+35
    started = time.time()
    with _authenticated(auth_report, stopped=stopped) as context:
        account, identity, headers, auth, maintenance, available = context
        auth_seconds = time.time()-started
        binding = sender_binding_sha256(auth)

        @contextmanager
        def component_gate():
            with component_lock:
                if abort.is_set(): raise CycleError('cohort_stopped')
                try: yield
                except BaseException as error:
                    if _must_abort(error): abort.set()
                    raise

        def lane(item):
            if stopped() or abort.is_set() or time.monotonic()>=admission_deadline:
                return {'creatorId':item['creator_id'],'state':'deferred'}
            report={'sendCapability':auth_report.get('sendCapability'), 'cohortStarted':started,
                    'lanes':lanes, 'imQps':3, 'sharedAuthentication':True}
            did = item['delivery_id']; stage = 'prepare'; begin = time.monotonic()
            with CycleStore(database) as s:
                deliveries = Deliveries(s,concurrent_recipient_limit=lanes)
                try:
                    prior = s.db.execute("SELECT id FROM cycle_delivery WHERE plan_id=? AND creator_id=? AND state IN ('ready','running','unknown') ORDER BY created DESC LIMIT 1",(plan,item['creator_id'])).fetchone()
                    did = did or (prior[0] if prior else None)
                    if did:
                        candidate=deliveries.get(did)['snapshot']
                        if frozen:
                            frozen_row = frozen_rows[item['creator_id']]
                            if candidate.get('frozenCandidateHash') != frozen_row['candidate_hash'] or \
                                    _frozen_hash(candidate) != frozen_row['candidate_hash']:
                                raise CycleError('frozen_delivery_conflict')
                    else:
                        if item['creator_id'] not in candidates: raise CycleError('candidate_not_ready')
                        candidate=dict(candidates[item['creator_id']])
                        frozen_row = frozen_rows.get(item['creator_id'])
                        with closing(sqlite3.connect((ROOT/'var/it-conversations.sqlite').as_uri()+'?mode=ro',uri=True)) as idx:
                            convs=idx.execute("SELECT cid FROM conversation WHERE scope='it:acc6' AND oec=? AND kind=2",(candidate['oecId'],)).fetchall()
                        if len(convs)>1: raise CycleError('ambiguous_conversation')
                        candidate['conversationId']=convs[0][0] if convs else None
                        if not frozen:
                            candidate['message']=render(candidate['name'],candidate['offer'],'standard',candidate['handle'])
                        else:
                            candidate['frozenCandidateHash'] = frozen_row['candidate_hash']
                        s.db.execute("UPDATE cycle_bulk_item SET state='preparing' WHERE batch_id=? AND creator_id=?",(batch_id,item['creator_id']))
                    def current():
                        available()
                        if frozen:
                            return _local_card(s, frozen_rows[item['creator_id']])
                        return cards.get(candidate, lambda: fresh_card(candidate,account,identity,headers,maintenance,stopped,request_budget=card_budget))
                    card, proof = current()
                    if did:
                        if card.binding_sha256 != candidate['nativeCard']['binding_sha256']: raise CycleError('card_binding_changed')
                    else:
                        candidate.update(nativeCard=asdict(card),card=proof,senderBindingHash=binding)
                        d=deliveries.prepare(plan,candidate); did=d['id']
                        (ROOT/'var/cycle-send'/f'{did}.json').write_text(json.dumps({'deliveryId':did,'snapshotHash':digest(candidate),'candidate':candidate,'authorization':batch_id,'expiresAt':d['expires']},ensure_ascii=False,indent=2))
                    s.db.execute("UPDATE cycle_bulk_item SET state='sending',delivery_id=? WHERE batch_id=? AND creator_id=?",(did,batch_id,item['creator_id']))
                    s.db.execute('INSERT INTO cycle_bulk_timing(batch_id,stage,seconds,exit_code,at) VALUES(?,?,?,?,?)',(batch_id,'cohort_prepare',time.monotonic()-begin,0,time.time()))
                    begin=time.monotonic(); stage='run'
                    def authorize(c):
                        if stopped() or abort.is_set(): raise CycleError('cohort_stopped')
                        b=s.db.execute('SELECT state,authorization FROM cycle_bulk WHERE id=?',(batch_id,)).fetchone()
                        if not b or b['state'] not in ('running','waiting_supply') or b['authorization']!=batch['authorization']:raise CycleError('bulk_not_running')
                        if frozen:
                            frozen_row = frozen_rows[item['creator_id']]
                            if c.get('frozenCandidateHash') != frozen_row['candidate_hash'] or \
                                    _frozen_hash(c) != frozen_row['candidate_hash']:
                                raise CycleError('bulk_scope_conflict')
                        elif digest(c)!=digest(candidate):raise CycleError('bulk_scope_conflict')
                    def validator(previous,*_):
                        result,_=current()
                        if result.binding_sha256!=previous.binding_sha256:raise CycleError('card_binding_changed')
                        return result
                    def preflight(c, rt, conv):
                        h=rt['reads'].history_summary(conv,include_sender_counts=True,include_events=True,include_contents=True)
                        Inbox(s).ingest(plan,conv.conversation_id,c['oecId'],h)
                        Service(s).capture(plan,conv.conversation_id,c['oecId'],h.get('contents',[]))
                        with closing(sqlite3.connect(ROOT/'var/it-conversations.sqlite',timeout=10)) as idx,idx:
                            prior=idx.execute("SELECT oec FROM conversation WHERE scope='it:acc6' AND cid=?",(conv.conversation_id,)).fetchone()
                            if prior and prior[0]!=c['oecId']:raise CycleError('conversation_index_conflict')
                            idx.execute("INSERT OR IGNORE INTO conversation VALUES('it:acc6',?,?,2,?)",(conv.conversation_id,c['oecId'],time.time()))
                        confirmed=sum(p['state']=='confirmed' for p in deliveries.get(did)['parts'])
                        _history.history_eligible(h,time.time(),confirmed)
                    with live_runtime(candidate['senderBindingHash'],report,authenticated_context=context,
                            request_budget=budget,card_validator=validator,send_interval=0.75,stopped=stopped) as rt:
                        @contextmanager
                        def protected_write_gate():
                            # Publish the stop before releasing the native sender FIFO.
                            # A waiting lane must not slip into the error-persistence gap.
                            with rt['write_gate']() as mark:
                                if abort.is_set():raise CycleError('cohort_stopped')
                                try:yield mark
                                except BaseException as error:
                                    if _must_abort(error): abort.set()
                                    raise
                        @contextmanager
                        def runtime(c,read_only=False):
                            yield {**rt,'write_gate':protected_write_gate,'card':ItalyVerifiedProductCard(**c['nativeCard']), 'component_gate':component_gate,'conversation':lambda cid,oec:rt['reads'].conversation(cid,oec,max_age=15)}
                        result=execute(deliveries,did,runtime,authorize,preflight)
                    state=result['state']
                    if state=='confirmed':s.db.execute("UPDATE cycle_bulk_item SET state='confirmed',reason=NULL WHERE batch_id=? AND creator_id=?",(batch_id,item['creator_id']))
                    elif state=='unknown':
                        abort.set();_set_batch_state(s,batch_id,'waiting_reconciliation')
                    return {'creatorId':item['creator_id'],'state':state}
                except Exception as error:
                    code=getattr(error,'code',None) or (str(error) if isinstance(error,CycleError) else type(error).__name__)
                    if getattr(error,'response_ref',None):
                        s.db.execute('INSERT INTO cycle_platform_signal(delivery_id,at,outcome,code,native_status,check_code,check_message,response_ref) VALUES(?,?,?,?,?,?,?,?)',(did,time.time(),*[getattr(error,k,None) for k in ('outcome','code','native_status','check_code','check_message','response_ref')]))
                    d=deliveries.get(did) if did else None
                    if d and d['state']=='unknown':
                        abort.set();_set_batch_state(s,batch_id,'waiting_reconciliation')
                    elif getattr(error,'outcome',None)=='rejected':
                        parts = {part['kind']: part['state'] for part in (d or {}).get('parts', [])}
                        if getattr(error,'check_code',None) is not None and error.check_code < 0:
                            _cancel_material_delivery(s,did)
                            s.db.execute("UPDATE cycle_bulk_item SET state='recipient_limit',reason=? "
                                         "WHERE batch_id=? AND creator_id=?",
                                         (code,batch_id,item['creator_id']))
                            return {'creatorId':item['creator_id'],'state':'recipient_limit','reason':code}
                        if frozen and parts.get('card')=='rejected':
                            _cancel_material_delivery(s,did)
                            if not _mark_material_waiting(candidate,error):
                                abort.set();_set_batch_state(s,batch_id,'material_refresh_record_failed')
                                s.db.execute("UPDATE cycle_bulk_item SET state='needs_review',reason=? "
                                             "WHERE batch_id=? AND creator_id=?",
                                             ('material_refresh_record_failed',batch_id,item['creator_id']))
                                return {'creatorId':item['creator_id'],'state':'error',
                                        'reason':'material_refresh_record_failed'}
                            s.db.execute("UPDATE cycle_bulk_item SET state='material_stale',reason=? "
                                         "WHERE batch_id=? AND creator_id=?",
                                         (code,batch_id,item['creator_id']))
                            return {'creatorId':item['creator_id'],'state':'material_stale','reason':code}
                        abort.set();_set_batch_state(s,batch_id,'platform_rejected')
                    elif code=='new_contact_capacity_reached':
                        abort.set();_set_batch_state(s,batch_id,'local_capacity_reached')
                    elif frozen and code in LOCAL_MATERIAL_ERRORS:
                        _cancel_material_delivery(s,did)
                        s.db.execute("UPDATE cycle_bulk_item SET state='material_stale',reason=? "
                                     "WHERE batch_id=? AND creator_id=?",
                                     (code,batch_id,item['creator_id']))
                        return {'creatorId':item['creator_id'],'state':'material_stale','reason':code}
                    if did and code in ('conversation_needs_content_review','relationship_changed') and deliveries.interrupted_by_inquiry(did):
                        s.db.execute("UPDATE cycle_bulk_item SET state='contacted_inquiry',reason=NULL WHERE batch_id=? AND creator_id=?",(batch_id,item['creator_id']))
                    else:
                        retry=code in ('cohort_stopped','candidate_not_ready','bulk_not_running','verify_before_dispatch') or (code in ('ReadTimeout','ConnectTimeout','TimeoutError') and item['retry_count']<3)
                        state='sending' if did and retry else 'pending' if retry else 'needs_review'
                        s.db.execute('UPDATE cycle_bulk_item SET state=?,reason=?,retry_at=?,retry_count=retry_count+? WHERE batch_id=? AND creator_id=?',(state,code,time.time()+5 if retry else 0,int(code in ('ReadTimeout','ConnectTimeout','TimeoutError')),batch_id,item['creator_id']))
                    return {'creatorId':item['creator_id'],'state':'deferred' if code=='cohort_stopped' else 'error','reason':code}
                finally:
                    s.db.execute('INSERT INTO cycle_bulk_timing(batch_id,stage,seconds,exit_code,at) VALUES(?,?,?,?,?)',(batch_id,'cohort_'+stage,time.monotonic()-begin,0 if did and deliveries.get(did)['state']=='confirmed' else 1,time.time()))
                    if did:
                        report.update(confirmedComponents=sum(p['state']=='confirmed' for p in deliveries.get(did)['parts']))
                        (ROOT/'var/cycle-send'/f'{did}.runtime.json').write_text(json.dumps(report,indent=2))
        with ThreadPoolExecutor(max_workers=lanes) as pool:
            results=list(pool.map(lane,items))
    return {'items':results,'seconds':round(time.time()-started,2),'lanes':lanes,'authentications':1,'authSeconds':round(auth_seconds,2),'imRequestSlots':budget.requests,'cardRequestSlots':card_budget.requests,'imPacingWaitSeconds':round(budget.wait_seconds,2)}
