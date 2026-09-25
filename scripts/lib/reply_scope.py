"""Exact inbound versions handled by one service decision, using the existing inbox and cursor."""
import json
from lib.second_cycle import CycleError, digest

ACTIVE = ('awaiting_content','awaiting_classification','review_partial','template_ready',
          'policy_review','facts_ready_for_review','needs_facts')


def available(db):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='service_message_resolution'").fetchone())


def unresolved(store, plan, creator):
    db=store.db
    rel=db.execute('SELECT oec FROM relationship WHERE plan_id=? AND creator_id=?',(plan,creator)).fetchone()
    if not rel:return []
    cursor=db.execute('SELECT event_rowid FROM service_cursor WHERE plan_id=? AND creator_id=?',(plan,creator)).fetchone()
    rows=db.execute('''SELECT e.rowid event_rowid,e.*,h.hash,v.payload content
      FROM inbox_event e LEFT JOIN inbox_content_head h USING(plan_id,cid,message_id)
      LEFT JOIN inbox_content_version v ON v.plan_id=h.plan_id AND v.cid=h.cid
        AND v.message_id=h.message_id AND v.hash=h.hash
      WHERE e.plan_id=? AND e.oec=? AND e.kind='creatorReplies' AND e.historical=0 AND e.rowid>?
      ORDER BY coalesce(e.occurred_ms,e.observed_at*1000),e.rowid''',(plan,rel[0],cursor[0] if cursor else 0)).fetchall()
    from lib.observed_messages import outbound_messages
    cutoffs={};result=[];scoped=available(db)
    for row in rows:
        if scoped and db.execute('SELECT 1 FROM service_message_resolution WHERE plan_id=? AND cid=? AND message_id=? AND content_hash=?',
                (plan,row['cid'],row['message_id'],row['hash'] or '')).fetchone():continue
        cid=row['cid'];stamp=row['occurred_ms']/1000 if row['occurred_ms'] else row['observed_at']
        if cid not in cutoffs:
            external=outbound_messages(db,plan,cid,rel[0],limit=1)
            manual=db.execute("SELECT max(coalesce(started,created)) FROM service_reply WHERE plan_id=? AND creator_id=? AND cid=? AND kind='manual' AND state='confirmed'",(plan,creator,cid)).fetchone()
            cutoffs[cid]=max(external[-1]['occurredAt'] if external else 0,manual[0] or 0)
        # An edited inbound version is a new question even if the original timestamp predates the answer.
        versions=db.execute('SELECT count(*),max(observed) FROM inbox_content_version WHERE plan_id=? AND cid=? AND message_id=?',(plan,cid,row['message_id'])).fetchone()
        if cutoffs[cid]>stamp and not (versions[0]>1 and versions[1]>cutoffs[cid]):continue
        content=json.loads(row['content']) if row['content'] else None
        result.append({'eventRowid':row['event_rowid'],'conversationId':cid,'messageId':row['message_id'],
                       'contentHash':row['hash'] or '', 'at':stamp,'observedAt':row['observed_at'],
                       'turnId':'turn-'+digest([plan,cid,row['message_id'],row['hash']])[:24],
                       'format':content.get('format') if content else None,'text':content.get('text') if content else None})
    return result


def freeze(rows):
    return [{k:r[k] for k in ('eventRowid','conversationId','messageId','contentHash')} for r in rows]


def check(store,plan,creator,scope):
    if not scope or freeze(unresolved(store,plan,creator))!=scope:raise CycleError('reply_context_changed')
    for cid in {r['conversationId'] for r in scope}:
        row=store.db.execute('SELECT state FROM inbox_checkpoint WHERE plan_id=? AND cid=?',(plan,cid)).fetchone()
        if not row or row[0]!='tracking':raise CycleError('reply_context_changed')


def from_reply(db,reply_id):
    row=db.execute("SELECT input_json FROM agent_reply_decision_v2 WHERE service_reply_id=? AND state='ready' LIMIT 1",(reply_id,)).fetchone()
    return json.loads(row[0]).get('context',{}).get('replyScope') if row else None


def settle(store,plan,creator,scope,reference,state,*,expected_control_revision):
    """Record only frozen versions. New/edited messages remain pending even during confirmation."""
    db=store.db
    if not db.in_transaction:raise CycleError('reply_scope_transaction_required')
    if not available(db):raise CycleError('reply_scope_migration_required')
    for item in scope:
        db.execute('INSERT OR IGNORE INTO service_message_resolution VALUES(?,?,?,?,?,?,?)',
                   (plan,item['conversationId'],item['messageId'],item['contentHash'],creator,reference,store.clock()))
    remaining=unresolved(store,plan,creator)
    # The cursor advances only through a contiguous prefix. Exact evidence above prevents replay of
    # already answered later messages when an earlier message is edited or arrives out of order.
    end=max((x['eventRowid'] for x in scope),default=0)
    if remaining:end=min(end,min(x['eventRowid'] for x in remaining)-1)
    db.execute('INSERT INTO service_cursor VALUES(?,?,?) ON CONFLICT(plan_id,creator_id) DO UPDATE SET event_rowid=max(event_rowid,excluded.event_rowid)',(plan,creator,end))
    rel=db.execute('SELECT revision,mode,rejected FROM relationship WHERE plan_id=? AND creator_id=?',(plan,creator)).fetchone()
    # A later operator action owns the state. Settlement never undoes it.
    if not remaining and rel and rel['mode']=='auto' and not rel['rejected'] and rel['revision']==expected_control_revision:
        db.execute('UPDATE inbox_pending SET state=? WHERE plan_id=? AND creator_id=?',(state,plan,creator))
        db.execute("UPDATE relationship SET inbox_until=0,revision=revision+1 WHERE plan_id=? AND creator_id=?",(plan,creator))
    return remaining


def retry_ready(store,plan,creator,scope,now,guide_revision):
    """Technical waits apply to this exact unanswered scope and guide, not a newer question."""
    attempts=[]
    for row in store.db.execute("SELECT state,input_json,created_at,guide_revision FROM agent_reply_decision_v2 WHERE plan_id=? AND creator_id=? AND mode='production' ORDER BY created_at DESC",(plan,creator)):
        context=json.loads(row['input_json']).get('context',{})
        if context.get('replyScope')==scope and row['guide_revision']==guide_revision:attempts.append(row)
    if any(r['state']=='input_blocked' for r in attempts):return False
    if not attempts or any(r['state']=='ready' for r in attempts):return True
    return len(attempts)<3 and now-attempts[0]['created_at']>=3600
