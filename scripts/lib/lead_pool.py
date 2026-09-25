"""The creator-lead sending pool.

One position is a pair of *one creator and one product*: that is what a message is about. The
Proactive outreach to the same creator is spaced at least 72 hours apart. Every product position
shares that cooldown; when it ends, the best remaining position takes the slot.

The pool is never stored. Its order depends on the current time, so any saved order is stale within
minutes. It is recomputed from the tables that already exist, and a creator moves from one layer to
the next purely because time passed. No background job is involved.

Layers, hardest constraint first:

* ``ready``          the creator is clear and off cooldown - sendable right now
* ``cooling``        sent recently; becomes ready when the cooldown expires
* ``awaiting_reply`` the creator has an unresolved case, or a human took over - needs a person
* ``excluded``       the creator asked not to be contacted
* ``sent``           this position was already delivered

Replying is what makes ``awaiting_reply`` different from ``cooling``: one is a matter of time, the
other is a matter of work, and showing them together would make a person wait for nothing.
"""
import json
import sqlite3
import time
from contextlib import closing
from datetime import date
from decimal import Decimal,InvalidOperation
from pathlib import Path
from lib.outreach_policy import MARKETING_COOLDOWN_SECONDS,last_contact_by_creator

RESOLVED_PENDING = frozenset({'resolved_by_human','suppressed_no_reply','resolved_no_reply','answered','no_reply'})
_ELIGIBLE_CACHE = {}
# ``ready`` holds one position per creator -- the slot that would be sent next. A creator's other
# positions stay in ``queued``: they are not lost, they simply are not the next thing to do.
LAYER_ORDER = ('ready', 'queued', 'cooling', 'awaiting_reply', 'excluded', 'product_inactive', 'sent')


def root_of(module_file=__file__):
    return Path(module_file).resolve().parents[2]


def _rows(conn, sql, args=()):
    return conn.execute(sql, args).fetchall()


def pool(root, *, market='it', now=None, limit=20, cache_eligible_seconds=0):
    """Build the pool. ``limit`` caps how many rows each layer returns, not the counts."""
    root = Path(root)
    db = root / 'var/second-cycle.sqlite'
    if not db.exists():
        return {'available': False, 'counts': {}, 'layers': {}, 'pools': {}}
    now = time.time() if now is None else now
    with closing(sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        conn.row_factory = sqlite3.Row;conn.execute('BEGIN')
        tables={row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required={'source_edge_index','lead_query_run','lead_query_selection','lead_query_head'}
        if not required<=tables:return {'available':False,'counts':{},'layers':{},'pools':{},'schema':'bdhub.lead-pool.v3'}
        eligible=_eligible_pids(conn,now,tables,market,db_path=str(db.resolve()),cache_seconds=cache_eligible_seconds)
        return _build(conn, now, limit, eligible_pids=eligible,root=root,market=market)


def _eligible_pids(conn,now,tables,market='it',*,db_path='',cache_seconds=0):
    if not {'plan','catalog','catalog_head'}<=tables:return None
    from lib.second_cycle import assess_offer
    pids=set()
    cache_key=None
    if cache_seconds:
        heads=tuple(row[0] for row in conn.execute("SELECT c.id FROM catalog_head h JOIN catalog c ON c.id=h.snapshot_id "
                                                  "JOIN plan p ON p.id=h.plan_id WHERE p.market=? ORDER BY c.id",(market,)))
        cache_key=(db_path,market,heads)
        cached=_ELIGIBLE_CACHE.get(cache_key)
        if cached and time.monotonic()-cached[0]<cache_seconds:return cached[1]
    rows=conn.execute("SELECT c.payload FROM catalog_head h JOIN catalog c ON c.id=h.snapshot_id "
                      "JOIN plan p ON p.id=h.plan_id WHERE p.market=?",(market,)).fetchall()
    for row in rows:
        try:offers=json.loads(row[0])
        except (TypeError,ValueError):continue
        for offer in offers if isinstance(offers,list) else []:
            if isinstance(offer,dict) and offer.get('pid') is not None and assess_offer(offer,now)['eligible']:
                pids.add(str(offer['pid']))
    if cache_key is not None:
        _ELIGIBLE_CACHE.clear()
        _ELIGIBLE_CACHE[cache_key]=(time.monotonic(),pids)
    return pids


def _money(value,currency):
    if value is None or not isinstance(currency,str) or len(currency)!=3:return None
    try:return Decimal(str(value))
    except InvalidOperation:return None


def _video_owner(root,market='it'):
    path=Path(root)/'var/creator-identities.sqlite'
    if not path.exists():return {}
    try:
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            return {str(row[1]).lower():str(row[0]) for row in db.execute(
                "SELECT creator_id,current_handle FROM creator_identity WHERE market=? "
                "AND handle_conflict=0 AND current_handle IS NOT NULL",(market,))}
    except sqlite3.Error:return {}


def _build(conn, now, limit, eligible_pids=None,root=None,market='it'):
    # CROSS JOIN pins the intended small-head → selected rows → indexed evidence order.  Ordinary
    # JOIN let SQLite start with every historical edge for each PID (12s on 13k rows).
    plan_columns={row[1] for row in conn.execute('PRAGMA table_info(plan)')}
    has_plan={'id','institution','market','state'}<=plan_columns
    plan=conn.execute("SELECT id FROM plan WHERE institution='bjn-local-research' AND market=? AND state='active'",(market,)).fetchone() if has_plan else None
    if has_plan and not plan:return {'available':False,'counts':{},'layers':{},'pools':{},'schema':'bdhub.lead-pool.v3'}
    plan_id=plan[0] if plan else None
    try:
        from lib.market_registry import market as market_row
        currency=market_row(root,market)['currency'] if root else 'EUR'
    except (FileNotFoundError,ValueError):currency='EUR' if market=='it' else None
    current="""SELECT h.plan_id,s.source_id,x.pid,x.source_handle,x.source_rank,x.units,
      x.revenue_value,x.revenue_currency
      FROM lead_query_head h CROSS JOIN lead_query_selection s CROSS JOIN source_edge_index x
      WHERE s.query_id=h.query_id AND x.plan_id=h.plan_id AND x.source_id=s.source_id""" + (" AND h.plan_id=?" if plan_id else "")
    current_args=(plan_id,) if plan_id else ()
    leads = _rows(conn, f"SELECT count(*) FROM ({current})",current_args)[0][0]
    outcomes = {row[0]: row[1] for row in _rows(conn, f"SELECT o.status,count(*) FROM ({current}) k "
        "JOIN cycle_identity_outcome o ON o.plan_id=k.plan_id AND o.source_id=k.source_id GROUP BY o.status",current_args)}
    handles = _rows(conn, f"SELECT count(DISTINCT source_handle) FROM ({current})",current_args)
    # A position only exists once the lead has been matched to a real creator.
    #
    # 达人级一次（2026-09-15 用户确认）：平台回答的是"这个 handle 是谁"，**与商品无关**；商品卡也只按
    # 商品核验（`fresh_card` 读的就是 offer）。所以一位达人有了 OECID，他名下的**所有**线索商品就都是
    # 可发位置——不需要每个商品各自再查一次身份。原来按"每条线索各自一行解析"建位置，等于让同一位达人
    # 的其它线索永远进不了池子，还逼着后台把它们反复重查。
    # 位置：**达人 × 商品**。先取"已就位达人 → 他的 handle"（2,000 多行），再按 handle 取他名下的
    # 全部线索商品；两段都是单次扫描 + 一次 IN 查找。**不要**写成两张大表按 JSON 字段连接：那会对
    # 每行都调 json_extract，2,000×13,000 次，实测把接口拖到 60 秒超时。
    resolved = _rows(conn, """SELECT x.source_handle AS handle,min(r.creator_id) AS creator_id,
        count(DISTINCT r.creator_id) AS owners FROM cycle_identity_resolution r
        JOIN source_edge_index x ON x.plan_id=r.plan_id AND x.source_id=r.source_id
        WHERE """+("x.plan_id=? AND " if plan_id else "")+"""x.source_kind IN ('kalodata_http','kalodata_video') GROUP BY x.source_handle HAVING owners=1""",
        (plan_id,) if plan_id else ())
    owner = {row['handle']: row['creator_id'] for row in resolved}
    if owner:
        marks = ','.join('?' * len(owner))
        edges = _rows(conn, f"""SELECT source_handle AS handle,pid,min(source_rank) AS rank,max(units) AS units,
            max(CASE WHEN revenue_currency=? THEN CAST(revenue_value AS REAL) END) AS gmv
            FROM ({current}) WHERE source_handle IN ({marks}) GROUP BY source_handle,pid""", (currency,*current_args,*tuple(owner)))
        positions = [{'creator_id': owner[row['handle']], 'handle': row['handle'], 'pid': row['pid'],
                      'rank': row['rank'], 'units': row['units'],'gmv':str(row['gmv']) if row['gmv'] is not None else None,
                      'sourceClass':'A','videoViews':None,'videoId':None,'videoReleasedAt':None} for row in edges]
    else:
        positions = []
    video_rows=_rows(conn,'SELECT * FROM video_lead_current WHERE market=?',(market,)) if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='video_lead_current'").fetchone() else []
    video_owner=_video_owner(root,market) if root else {};video_unresolved=0
    from lib.video_window import current as video_current
    for row in video_rows:
        if not video_current(row['released_at'],now):continue
        creator=video_owner.get(str(row['handle']).lower())
        if not creator:video_unresolved+=1;continue
        positions.append({'creator_id':creator,'handle':row['handle'],'pid':row['pid'],'rank':None,'units':0,
                          'gmv':None,'sourceClass':'B','videoViews':row['views'],'videoId':row['video_id'],
                          'videoReleasedAt':row['released_at']})
    merged={}
    for row in positions:
        key=(row['creator_id'],str(row['pid']));old=merged.get(key)
        if old is None:merged[key]=row;continue
        if old['sourceClass']=='A':
            if row['sourceClass']=='B':
                old['videoViews']=row['videoViews'];old['videoId']=row['videoId'];old['videoReleasedAt']=row['videoReleasedAt']
        elif row['sourceClass']=='A':
            row['videoViews']=old['videoViews'];row['videoId']=old['videoId'];row['videoReleasedAt']=old['videoReleasedAt'];merged[key]=row
        elif (row['videoViews'],row['videoReleasedAt'],row['videoId'])>(old['videoViews'],old['videoReleasedAt'],old['videoId']):
            merged[key]=row
    positions=list(merged.values())
    relationships = {row['creator_id']: row for row in _rows(
        conn, 'SELECT creator_id,unlocked,mode,rejected,inbox_until FROM relationship'+(' WHERE plan_id=?' if plan_id else ''),
        (plan_id,) if plan_id else ())}
    collaboration = (
        {row['creator_id']: row['status'] for row in _rows(
            conn, 'SELECT creator_id,status FROM creator_collaboration_current'+(' WHERE plan_id=?' if plan_id else ''),
            (plan_id,) if plan_id else ())}
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                        "AND name='creator_collaboration_current'").fetchone() else {}
    )
    last_sent = last_contact_by_creator(conn, plan_id)
    sent_pairs = {(row[0], str(row[1])): row[2] for row in _rows(
        conn, "SELECT d.creator_id,d.pid,max(p.started) FROM cycle_delivery d "
              "JOIN cycle_delivery_part p ON p.delivery_id=d.id "
              "WHERE "+("d.plan_id=? AND " if plan_id else "")+"d.state IN ('confirmed','partial_delivery') GROUP BY d.creator_id,d.pid",
              (plan_id,) if plan_id else ())}
    open_cases = {row[0]: row[1] for row in _rows(
        conn, "SELECT creator_id,updated FROM service_case WHERE "+("plan_id=? AND " if plan_id else "")+"state='open'",
        (plan_id,) if plan_id else ())}
    pending = ({row[0]: (row[1],row[2]) for row in _rows(
        conn, 'SELECT creator_id,state,due_at FROM inbox_pending'+(' WHERE plan_id=?' if plan_id else ''),
        (plan_id,) if plan_id else ())}
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='inbox_pending'").fetchone() else {})
    creators = {row['creator_id'] for row in _rows(conn, 'SELECT DISTINCT creator_id FROM relationship'+(' WHERE plan_id=?' if plan_id else ''),
        (plan_id,) if plan_id else ())}

    layers = {name: [] for name in LAYER_ORDER}
    unique_creators = set()
    for row in positions:
        creator = row['creator_id']
        unique_creators.add(creator)
        relationship = relationships.get(creator)
        if relationship is None:
            continue
        unlocked = bool(relationship['unlocked'])
        collaboration_status = collaboration.get(creator, 'rejected' if relationship['rejected'] else 'normal')
        excluded = bool(relationship['rejected']) or collaboration_status in ('paid', 'rejected')
        pending_row=pending.get(creator)
        blocked = creator in open_cases or relationship['mode'] == 'human' or \
                  bool(relationship['inbox_until'] and relationship['inbox_until']>now) or \
                  bool(pending_row and pending_row[0] not in RESOLVED_PENDING)
        previously = last_sent.get(creator)
        ready_at = None if previously is None else previously + MARKETING_COOLDOWN_SECONDS
        pair_sent = sent_pairs.get((creator, str(row['pid'])))
        product_active=eligible_pids is None or str(row['pid']) in eligible_pids
        if pair_sent is not None:
            layer = 'sent'
        elif not product_active:
            layer = 'product_inactive'
        elif excluded:
            layer = 'excluded'
        elif blocked:
            layer = 'awaiting_reply'
        elif ready_at is None or now >= ready_at:
            layer = 'ready'
        else:
            layer = 'cooling'
        layers[layer].append({'creatorId': creator, 'handle': row['handle'] or '',
                              'pid': str(row['pid']), 'rank': row['rank'], 'units': row['units'],
                              'sourceClass':row['sourceClass'],'gmv':row['gmv'],
                              'videoViews':row['videoViews'],'videoId':row['videoId'],
                              'videoReleasedAt':row['videoReleasedAt'],
                              'unlocked': unlocked, 'sentAt': pair_sent,
                              'collaborationStatus': collaboration_status,
                              'readyAt': ready_at, 'layer': layer,
                              'caseUpdatedAt': open_cases.get(creator) or (pending_row[1] if pending_row else None)})

    # Ready positions: strongest lead first, one slot per creator so a single creator cannot fill
    # the whole page while others never surface. Cooling follows the clock, not the lead strength.
    def strength(row):
        if row['sourceClass']=='A':
            gmv=_money(row['gmv'],currency)
            return (0,0 if gmv is not None else 1,-(gmv or Decimal(0)),-(row['units'] or 0),
                    row['rank'] if row['rank'] is not None else 10**9,row['pid'],row['creatorId'])
        try:released=date.fromisoformat(row['videoReleasedAt']).toordinal()
        except (TypeError,ValueError):released=0
        return (1,-(row['videoViews'] or 0),-released,row['pid'],row['creatorId'])
    layers['ready'].sort(key=strength)
    # One slot per creator: sending the best lead first, the rest wait their turn in the pool.
    seen = set()
    queued = []
    slots = []
    for row in layers['ready']:
        if row['creatorId'] in seen:
            row['layer'] = 'queued'
            queued.append(row)
        else:
            seen.add(row['creatorId'])
            slots.append(row)
    layers['queued'] = sorted(queued, key=lambda r: (r['creatorId'],)+strength(r))
    layers['ready'] = slots
    layers['cooling'].sort(key=lambda r: (r['readyAt'] or 0,)+strength(r))
    layers['awaiting_reply'].sort(key=lambda r: (r['caseUpdatedAt'] or 0, r['creatorId']))
    layers['excluded'].sort(key=lambda r: (r['creatorId'], r['pid']))
    layers['product_inactive'].sort(key=strength)
    layers['sent'].sort(key=lambda r: -(r['sentAt'] or 0))

    current_sent = len(layers['sent']);sent = len(sent_pairs)
    merged = outcomes.get('completed', 0)
    counts = {'leads': leads,
              'merged': merged,
              'unresolved': outcomes.get('unresolved', 0),
              'identityQueued': outcomes.get('queued', 0) + outcomes.get('blocked', 0),
              'positions': len(positions),
              'creators': len(unique_creators),
              'handles': handles[0][0] if handles else 0,
              'sent': sent,
              'unsent': max(0, len(positions) - current_sent),
              'ready': len(layers['ready']),
              'readyCreators': len({row['creatorId'] for row in layers['ready']}),
              'queued': len(layers['queued']),
              'candidateQueued': len(layers['queued']),
              'aPositions':sum(row['sourceClass']=='A' for row in positions),
              'bPositions':sum(row['sourceClass']=='B' for row in positions),
              'videoUnresolved':video_unresolved,
              'cooling': len(layers['cooling']),
              'awaitingReply': len(layers['awaiting_reply']),
              'excluded': len(layers['excluded']),
              'creatorsWithRelationship': len(creators)}
    business={'sendable':len(layers['ready']),
              'waiting':len(layers['queued'])+len(layers['cooling'])+len(layers['awaiting_reply']),
              'inactive':len(layers['excluded'])+len(layers['product_inactive'])}
    business['total']=business['sendable']+business['waiting']+business['inactive']
    return {'schema':'bdhub.lead-pool.v3','available': True, 'now': now, 'counts': counts,
            'cooldown': {'unlocked': MARKETING_COOLDOWN_SECONDS, 'locked': MARKETING_COOLDOWN_SECONDS},
            'layers': {name: len(rows) for name, rows in layers.items()},
            'pools': {name: rows[:limit] for name, rows in layers.items()},
            'business':business,'reasons':{name:len(rows) for name,rows in layers.items() if name not in ('ready','sent')},
            'history':{'sent':sent,'currentPositions':current_sent}}
