"""Persistent 4A:1B recipient allocations, distinct from external delivery results."""
from datetime import datetime,timedelta,timezone
from lib.second_cycle import CycleError

POLICY='outreach-ab-4-to-1-v1'
BEIJING=timezone(timedelta(hours=8))


def day(at):return datetime.fromtimestamp(at,BEIJING).date().isoformat()


def enabled(db):return bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='outreach_allocation'").fetchone())


def position(db,market):
    if not enabled(db):raise CycleError('outreach_allocation_schema_required')
    row=db.execute('SELECT position FROM outreach_rotation WHERE market=?',(market,)).fetchone()
    return row[0] if row else 0


def select(store,market,plan,state,build):
    """Material/template/capacity validation is supplied by each existing market adapter.

    Examine B candidates with their A alternatives first; a creator with a valid
    A position cannot occupy B's turn. Failed scans never move the durable cursor.
    """
    from lib.lead_pool import lead_strength
    cursor=position(store.db,market);stamp=day(store.clock());wanted='B' if cursor==4 else 'A'
    pools=state.get('pools') or {};rows=list(pools.get('ready') or [])+list(pools.get('queued') or [])
    rows.sort(key=lead_strength)
    claimed={r[0] for r in store.db.execute("""SELECT r.creator_id FROM relationship r
        JOIN outreach_allocation a ON a.oec=r.oec WHERE r.plan_id=? AND a.market=? AND a.day=?""",(plan,market,stamp))}
    rows=[r for r in rows if r['creatorId'] not in claimed]
    from lib.cycle_delivery import platform_rejections_today,PLATFORM_REJECTION_HOLD
    capacity_blocked=False
    if rows and platform_rejections_today(store,plan)>=PLATFORM_REJECTION_HOLD:
        permitted={r[0] for r in store.db.execute("""SELECT r.creator_id FROM relationship r WHERE r.plan_id=?
          AND (r.unlocked=1 OR EXISTS(SELECT 1 FROM cycle_contact_reservation old WHERE old.plan_id=r.plan_id AND old.oec=r.oec AND old.reserved>?))""",(plan,store.clock()-86400))}
        capacity_blocked=any(r['creatorId'] not in permitted for r in rows)
        rows=[r for r in rows if r['creatorId'] in permitted]
    a_rows={}
    for row in rows:
        if row.get('sourceClass','A')=='A':a_rows.setdefault(row['creatorId'],[]).append(row)
    cache={}
    def candidate(row):
        key=(row['creatorId'],row['pid'],row.get('sourceClass','A'))
        if key not in cache:cache[key]=build(row)
        return cache[key]
    for kind in (wanted,'A' if wanted=='B' else 'B'):
        for row in rows:
            if row.get('sourceClass','A')!=kind:continue
            if kind=='B' and any(candidate(a) is not None for a in a_rows.get(row['creatorId'],[])):continue
            value=candidate(row)
            if value is None:continue
            allocation={'policy':POLICY,'market':market,'day':stamp,'position':cursor,
                'preferredClass':wanted,'actualClass':kind,
                'borrowedReason':None if kind==wanted else 'preferred_class_unavailable'}
            return {**value,'sendAllocation':allocation}
    if capacity_blocked:raise CycleError('new_contact_capacity_reached')
    return None


def claim(store,plan,candidate,delivery_id):
    """Called in the same transaction as the immutable delivery/parts insertion."""
    allocation=candidate.get('sendAllocation')
    if allocation is None:return  # Historical/frozen legacy modes retain their original contract.
    market=store._plan(plan)['market'];cursor=position(store.db,market)
    if candidate['source'].get('sourceKind') not in ('kalodata_http','kalodata_video'):raise CycleError('outreach_source_class_invalid')
    kind='B' if candidate['source']['sourceKind']=='kalodata_video' else 'A'
    if candidate['source'].get('sourceClass',kind)!=kind:raise CycleError('outreach_source_class_invalid')
    wanted='B' if cursor==4 else 'A';borrowed=None if kind==wanted else 'preferred_class_unavailable'
    expected={'policy':POLICY,'market':market,'day':day(store.clock()),'position':cursor,
              'preferredClass':wanted,'actualClass':kind,'borrowedReason':borrowed}
    if allocation!=expected:raise CycleError('outreach_allocation_stale')
    if store.db.execute('SELECT 1 FROM outreach_allocation WHERE market=? AND day=? AND oec=?',
                        (market,allocation['day'],candidate['oecId'])).fetchone():raise CycleError('outreach_recipient_already_allocated')
    store.db.execute('INSERT INTO outreach_allocation VALUES(?,?,?,?,?,?,?,?,?,?)',
        (delivery_id,market,allocation['day'],candidate['oecId'],candidate['creatorId'],kind,wanted,
         cursor,borrowed,store.clock()))
    store.db.execute('INSERT INTO outreach_rotation VALUES(?,?,?) ON CONFLICT(market) DO UPDATE SET position=excluded.position,updated=excluded.updated',
                     (market,(cursor+1)%5,store.clock()))


def summary(db,market,at):
    """Read only, including delivery-level outcomes; never infers success from allocations."""
    if not enabled(db):return None
    cursor=position(db,market);stamp=day(at)
    states={};arranged={'A':0,'B':0};borrowed={'A':0,'B':0}
    has_delivery=db.execute("SELECT 1 FROM sqlite_master WHERE name='cycle_delivery'").fetchone()
    for kind,state,n in (db.execute("""SELECT a.actual_class,d.state,count(*) FROM outreach_allocation a
      JOIN cycle_delivery d ON d.id=a.delivery_id WHERE a.market=? AND a.day=? GROUP BY a.actual_class,d.state""",(market,stamp)) if has_delivery else []):
        arranged[kind]+=n;states.setdefault(kind,{})[state]=n
    for kind,n in db.execute('SELECT actual_class,count(*) FROM outreach_allocation WHERE market=? AND day=? AND borrowed_reason IS NOT NULL GROUP BY actual_class',(market,stamp)):borrowed[kind]=n
    return {'policy':POLICY,'day':stamp,'position':cursor,'nextPreferred':'B' if cursor==4 else 'A',
            'arranged':arranged,'borrowed':borrowed,'outcomes':states,'scope':'post_cutover_allocations'}


def allocated_creators(db,plan,market,at):
    if not enabled(db) or not db.execute('SELECT 1 FROM outreach_allocation WHERE market=? AND day=? LIMIT 1',(market,day(at))).fetchone():return set()
    return {r[0] for r in db.execute("""SELECT r.creator_id FROM relationship r JOIN outreach_allocation a ON a.oec=r.oec
       WHERE r.plan_id=? AND a.market=? AND a.day=?""",(plan,market,day(at)))}
