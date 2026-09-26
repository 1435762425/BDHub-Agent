"""Explicit diagnosis and evidence-bound recovery of legacy inbox ``gap`` checkpoints.

A legacy gap stored every later event as historical, so its conversation never reached the reply
queue again. Recovery never rewrites those events: it only re-enters the resumable backfill with the
last event that was tracked live before the gap as the coverage watermark. Once a later read joins
that event, messages newly read below the gap follow the normal live rule; events already stored as
historical during the gap stay historical and are reported for human reading.
"""
from lib.second_cycle import CycleError


def _plan(store,market):
    row=store.db.execute("SELECT id FROM plan WHERE market=? AND institution='bjn-local-research'",(market,)).fetchone()
    if not row:raise CycleError('plan_missing')
    return row[0]


def _evidence(db,plan,cid):
    live=db.execute("SELECT max(rowid) FROM inbox_event WHERE plan_id=? AND cid=? AND historical=0",(plan,cid)).fetchone()[0]
    held=db.execute("SELECT count(*) FROM inbox_event WHERE plan_id=? AND cid=? AND historical=1 AND rowid>?",
                    (plan,cid,live or 0)).fetchone()[0]
    return live,held


def diagnose(store,market):
    """Read-only: every gap conversation, whether it can be safely recovered and what is missing."""
    plan=_plan(store,market);items=[]
    for row in store.db.execute("SELECT cid,oec,checked_at FROM inbox_checkpoint WHERE plan_id=? AND state='gap' ORDER BY cid",(plan,)):
        watermark,held=_evidence(store.db,plan,row['cid'])
        items.append({'conversationId':row['cid'],'oec':row['oec'],'checkedAt':row['checked_at'],
                      'recoverable':watermark is not None,'watermark':watermark,'historicalDuringGap':held,
                      **({} if watermark is not None else {'missing':'no_live_tracked_event_before_gap'})})
    return {'market':market,'gaps':len(items),'items':items,'platformWrites':0,'realSends':0}


def recover(store,market,cid,*,confirm=False):
    """Re-enter the resumable backfill for one gap, bound to its pre-gap watermark; local state only."""
    if not isinstance(cid,str) or not cid.isdigit():raise CycleError('inbox_gap_scope_invalid')
    plan=_plan(store,market)
    def assess():
        cp=store.db.execute("SELECT * FROM inbox_checkpoint WHERE plan_id=? AND cid=?",(plan,cid)).fetchone()
        if not cp or cp['state']!='gap':raise CycleError('inbox_gap_not_found')
        return (cp,*_evidence(store.db,plan,cid))
    cp,watermark,held=assess()
    if watermark is None:
        return {'state':'not_recoverable','conversationId':cid,'missing':'no_live_tracked_event_before_gap',
                'platformWrites':0,'realSends':0}
    if not confirm:
        return {'state':'ready_to_recover','conversationId':cid,'watermark':watermark,'historicalDuringGap':held,
                'platformWrites':0,'realSends':0}
    with store.tx():
        cp,watermark,held=assess()  # Re-checked under the write lock.
        now=store.clock()
        store.db.execute("UPDATE inbox_checkpoint SET state='backfilling' WHERE plan_id=? AND cid=? AND state='gap'",(plan,cid))
        store.db.execute("INSERT OR REPLACE INTO inbox_backfill VALUES(?,?,?,'','','0',?,?,0,?)",(plan,cid,cp['oec'],watermark,now,now))
    return {'state':'backfilling','conversationId':cid,'watermark':watermark,'historicalDuringGap':held,
            'platformWrites':0,'realSends':0}
