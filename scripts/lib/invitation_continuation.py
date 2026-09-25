"""Narrow proof that only verified inbox changes interrupted a frozen card/text pair.

No control is cleared, no snapshot is rewritten and no transport permission is
created here. Every normal plan, time, material, account and write gate still runs.
"""
import json
from lib.second_cycle import encoded

CHECK='invitation_inbox_revision'
MODES=('continuous-v1','market-continuous-v1','market-canary-v1')
ACTIVE_PENDING={'awaiting_content','awaiting_classification','review_partial','template_ready',
                'policy_review','facts_ready_for_review','needs_facts'}


def _tables(db):return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _pair(store,did):
    db=store.db
    if not {'cycle_delivery','cycle_delivery_part','cycle_delivery_check'}<=_tables(db):return None
    d=db.execute('SELECT * FROM cycle_delivery WHERE id=?',(did,)).fetchone()
    if not d or d['state']!='running' or d['expires']<=store.clock():return None
    c=json.loads(d['snapshot'])
    if c.get('executionMode') not in MODES:return None
    parts={p['kind']:p for p in db.execute('SELECT * FROM cycle_delivery_part WHERE delivery_id=?',(did,))}
    card,text=parts.get('card'),parts.get('text')
    if not card or not text or card['state']!='confirmed' or card['started'] is None or \
       text['state']!='ready' or text['started'] is not None or text['receipt'] or text['confirmation']:return None
    proof=json.loads(card['confirmation'] or '{}')
    if not proof.get('messageId') or proof.get('requestRef')!=card['request_ref']:return None
    cid=c.get('conversationId')
    if not cid and 'cycle_conversation_intent' in _tables(db):
        intent=db.execute("SELECT cid FROM cycle_conversation_intent WHERE delivery_id=? AND state='confirmed'",(did,)).fetchone()
        cid=intent[0] if intent else None
    if not cid:return None
    return d,c,card,str(cid)


def _latest(db,did):
    row=db.execute('SELECT payload FROM cycle_delivery_check WHERE delivery_id=? AND kind=? ORDER BY checked DESC,rowid DESC LIMIT 1',(did,CHECK)).fetchone()
    return json.loads(row[0]) if row else None


def record(store,plan,cid,oec,before,events):
    """Called inside the existing inbox/content transaction, after its revision update."""
    db=store.db
    if not db.in_transaction:raise ValueError('invitation_proof_requires_transaction')
    if not {'cycle_delivery','cycle_delivery_part','cycle_delivery_check'}<=_tables(db):return
    after=db.execute('SELECT * FROM relationship WHERE plan_id=? AND oec=?',(plan,oec)).fetchone()
    if not after or before['mode']!='auto' or after['mode']!='auto' or before['rejected'] or after['rejected'] or \
       after['revision']<=before['revision']:return
    cp=db.execute('SELECT state FROM inbox_checkpoint WHERE plan_id=? AND cid=? AND oec=?',(plan,cid,oec)).fetchone()
    if not cp or cp[0]!='tracking':return
    for (did,) in db.execute("SELECT id FROM cycle_delivery WHERE plan_id=? AND creator_id=? AND state='running'",(plan,after['creator_id'])).fetchall():
        pair=_pair(store,did)
        if not pair:continue
        d,c,card,actual_cid=pair
        if actual_cid!=str(cid):continue
        prior=_latest(db,did)
        expected=prior['afterRevision'] if prior else c['controlRevision']
        if before['revision']!=expected:continue  # A pause, handoff or another change broke this chain.
        valid=[];outside_pair=False
        for event in events:
            if event['kind'] not in ('creatorReplies','showcaseNotifications'):continue
            stamp=event.get('occurred_ms')
            if stamp is None or stamp<card['started']*1000 or stamp>store.clock()*1000+300000:
                outside_pair=True;continue
            valid.append(str(event['message_id']))
        if not valid or outside_pair:continue
        db.execute('INSERT INTO cycle_delivery_check VALUES(?,?,?,?)',(did,CHECK,store.clock(),encoded({
            'originalRevision':c['controlRevision'],'beforeRevision':before['revision'],'afterRevision':after['revision'],
            'cid':actual_cid,'messageIds':sorted(set(valid)),'inboxUntil':after['inbox_until'],
            'unlocked':after['unlocked'],'reason':'verified_inbox_only','platformWrites':0})))


def allowed(store,did):
    pair=_pair(store,did)
    if not pair:return False
    d,c,card,cid=pair;db=store.db;tables=_tables(db)
    r=db.execute('SELECT * FROM relationship WHERE plan_id=? AND creator_id=?',(d['plan_id'],d['creator_id'])).fetchone()
    if not r or r['mode']!='auto' or r['rejected'] or r['oec']!=d['oec']:return False
    evidence=_latest(db,did)
    if not evidence or evidence['originalRevision']!=c['controlRevision'] or evidence['cid']!=cid or \
       evidence['afterRevision']!=r['revision'] or evidence['inboxUntil']!=r['inbox_until'] or evidence['unlocked']!=r['unlocked']:return False
    if 'inbox_checkpoint' not in tables:return False
    cp=db.execute('SELECT state FROM inbox_checkpoint WHERE plan_id=? AND cid=? AND oec=?',(d['plan_id'],cid,d['oec'])).fetchone()
    if not cp or cp[0]!='tracking':return False
    if 'service_case' in tables and db.execute("SELECT 1 FROM service_case WHERE plan_id=? AND creator_id=? AND state='open'",(d['plan_id'],d['creator_id'])).fetchone():return False
    if 'inbox_pending' in tables:
        pending=db.execute('SELECT state FROM inbox_pending WHERE plan_id=? AND creator_id=?',(d['plan_id'],d['creator_id'])).fetchone()
        if pending and pending[0] not in ACTIVE_PENDING:return False
    return True


def confirmed_contact_start(db,plan,cid,oec):
    """The latest confirmed card anchors live replies even if its text comes later."""
    tables=_tables(db)
    if not {'cycle_delivery','cycle_delivery_part','cycle_conversation_intent'}<=tables:return None
    row=db.execute("""SELECT max(CASE WHEN p.kind='card' THEN p.started END),max(CASE WHEN p.kind='text' THEN p.started END)
      FROM relationship r CROSS JOIN cycle_delivery d CROSS JOIN cycle_delivery_part p
      LEFT JOIN cycle_conversation_intent i ON i.delivery_id=d.id AND i.state='confirmed'
      WHERE r.plan_id=? AND r.oec=? AND d.plan_id=r.plan_id AND d.creator_id=r.creator_id AND d.oec=r.oec
        AND p.delivery_id=d.id AND p.state='confirmed' AND coalesce(nullif(json_extract(d.snapshot,'$.conversationId'),''),i.cid)=?""",(plan,oec,str(cid))).fetchone()
    return row[0] if row and row[0] is not None else row[1] if row else None


def local_permit_error(error,part):
    """Recover a known local refusal only while the component is provably unsubmitted."""
    from lib.second_cycle import CycleError
    original=getattr(error,'__context__',None)
    if getattr(error,'code',None)=='it_delivery_dispatch_not_allowed' and isinstance(original,CycleError) and \
       part['state']=='ready' and part['started'] is None and not part['receipt'] and not part['confirmation']:
        return original
    return None


def require_original_sender(candidate,market,account,im_id):
    from lib.second_cycle import CycleError
    from lib.delivery_reconciliation import LEGACY_ACCOUNTS
    if candidate.get('senderAccount',LEGACY_ACCOUNTS.get(market))!=account or \
       ('senderImId' in candidate and candidate['senderImId']!=im_id):
        raise CycleError('invitation_original_sender_changed')
