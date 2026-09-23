"""Verified platform messages sent outside this application's intent ledger."""
import json


def recorded_message_ids(db,plan,oec,cid):
    tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    ids=set()
    receipts=[]
    if {'cycle_delivery','cycle_delivery_part'}<=tables:
        for row in db.execute('SELECT p.receipt,p.confirmation,d.snapshot,d.id FROM cycle_delivery_part p '
                              'JOIN cycle_delivery d ON d.id=p.delivery_id WHERE d.plan_id=? AND d.oec=?',(plan,oec)):
            try:actual_cid=json.loads(row[2]).get('conversationId')
            except (ValueError,TypeError):actual_cid=None
            if not actual_cid and 'cycle_conversation_intent' in tables:
                intent=db.execute('SELECT cid FROM cycle_conversation_intent WHERE delivery_id=?',(row[3],)).fetchone()
                actual_cid=intent[0] if intent else None
            if str(actual_cid or '')==str(cid):receipts.append((row[0],row[1]))
    if 'service_reply' in tables:
        receipts.extend(db.execute('SELECT receipt,proof FROM service_reply WHERE plan_id=? AND oec=? AND cid=?',(plan,oec,cid)))
    for row in receipts:
        for raw in row:
            try:value=json.loads(raw) if raw else {}
            except (ValueError,TypeError):continue
            if isinstance(value,dict) and value.get('messageId') is not None:ids.add(str(value['messageId']))
    return ids


def outbound_messages(db,plan,cid,oec,*,before=None,limit=200):
    """Project observed bodies; an observation never creates or confirms a send intent."""
    tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {'inbox_event','inbox_content_head','inbox_content_version'}<=tables:return []
    known=recorded_message_ids(db,plan,oec,cid)
    rows=db.execute('''SELECT e.message_id,e.occurred_ms,e.observed_at,v.payload
      FROM inbox_event e JOIN inbox_content_head h USING(plan_id,cid,message_id)
      JOIN inbox_content_version v ON v.plan_id=h.plan_id AND v.cid=h.cid
        AND v.message_id=h.message_id AND v.hash=h.hash
      WHERE e.plan_id=? AND e.cid=? AND e.oec=? AND e.kind='ourMessages'
      ORDER BY coalesce(e.occurred_ms,e.observed_at*1000) DESC,e.message_id DESC''',(plan,cid,oec))
    result=[]
    for row in rows:
        if str(row['message_id']) in known:continue
        stamp=row['occurred_ms']/1000 if row['occurred_ms'] else row['observed_at']
        if before is not None and stamp>before:continue
        content=json.loads(row['payload']);plain=content.get('format')=='text'
        result.append({'id':'platform-'+str(row['message_id']),'messageId':str(row['message_id']),
                       'direction':'outbound','kind':'text' if plain else 'attachment_or_unsupported',
                       'text':str(content.get('text') or '')[:4000] if plain else '[机构后台商品卡或附件]',
                       'occurredAt':stamp,'status':'observed','source':'platform'})
        if len(result)>=limit:break
    return sorted(result,key=lambda row:(row['occurredAt'],row['id']))


def replied_after(db,plan,cid,oec,stamp):
    rows=outbound_messages(db,plan,cid,oec,limit=1)
    return bool(rows and rows[-1]['occurredAt']>stamp)


def missing_body_targets(db,plan,limit=20):
    tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {'inbox_event','inbox_content_head'}<=tables:return []
    known={};seen=set();targets=[]
    rows=db.execute('''WITH recent AS (
      SELECT e.*,row_number() OVER(PARTITION BY cid ORDER BY coalesce(occurred_ms,observed_at*1000) DESC,message_id DESC) position
      FROM inbox_event e WHERE plan_id=?)
      SELECT e.cid,e.oec,e.message_id FROM recent e
      LEFT JOIN inbox_content_head h USING(plan_id,cid,message_id)
      WHERE e.position<=20 AND e.kind='ourMessages' AND h.message_id IS NULL
      ORDER BY coalesce(e.occurred_ms,e.observed_at*1000) DESC''',(plan,))
    for row in rows:
        if row['cid'] in seen:continue
        key=(row['oec'],row['cid'])
        if key not in known:known[key]=recorded_message_ids(db,plan,*key)
        if row['message_id'] in known[key]:continue
        seen.add(row['cid']);targets.append((row['cid'],row['oec']))
        if len(targets)>=limit:break
    return targets
