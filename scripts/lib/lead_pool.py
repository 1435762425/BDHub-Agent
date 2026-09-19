"""The creator-lead sending pool.

One position is a pair of *one creator and one product*: that is what a message is about. The
cooldown, however, belongs to the creator, because the operator's rule is "at most one message per
creator per day" — so a creator in cooldown makes every one of their positions unavailable, and when
the cooldown ends the best remaining position takes the slot.

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
import sqlite3
import time
from contextlib import closing
from pathlib import Path

UNLOCKED_COOLDOWN = 86400   # one message per creator per day
LOCKED_COOLDOWN = 172800    # four weeks without a reply: at least 48 hours between products
# ``ready`` holds one position per creator -- the slot that would be sent next. A creator's other
# positions stay in ``queued``: they are not lost, they simply are not the next thing to do.
LAYER_ORDER = ('ready', 'queued', 'cooling', 'awaiting_reply', 'excluded', 'sent')


def root_of(module_file=__file__):
    return Path(module_file).resolve().parents[2]


def _rows(conn, sql, args=()):
    return conn.execute(sql, args).fetchall()


def pool(root, *, now=None, limit=20):
    """Build the pool. ``limit`` caps how many rows each layer returns, not the counts."""
    root = Path(root)
    db = root / 'var/second-cycle.sqlite'
    if not db.exists():
        return {'available': False, 'counts': {}, 'layers': {}, 'pools': {}}
    now = time.time() if now is None else now
    with closing(sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('BEGIN')
        return _build(conn, now, limit)


def _build(conn, now, limit):
    leads = _rows(conn, "SELECT count(*) FROM source_edge WHERE json_extract(payload,'$.sourceKind')='kalodata_http'")[0][0]
    outcomes = {row[0]: row[1] for row in _rows(conn, 'SELECT status,count(*) FROM cycle_identity_outcome GROUP BY status')}
    handles = _rows(conn, "SELECT count(DISTINCT json_extract(payload,'$.sourceHandle')) FROM source_edge "
                          "WHERE json_extract(payload,'$.sourceKind')='kalodata_http'")
    # A position only exists once the lead has been matched to a real creator.
    #
    # 达人级一次（2026-09-15 用户确认）：平台回答的是"这个 handle 是谁"，**与商品无关**；商品卡也只按
    # 商品核验（`fresh_card` 读的就是 offer）。所以一位达人有了 OECID，他名下的**所有**线索商品就都是
    # 可发位置——不需要每个商品各自再查一次身份。原来按"每条线索各自一行解析"建位置，等于让同一位达人
    # 的其它线索永远进不了池子，还逼着后台把它们反复重查。
    # 位置：**达人 × 商品**。先取"已就位达人 → 他的 handle"（2,000 多行），再按 handle 取他名下的
    # 全部线索商品；两段都是单次扫描 + 一次 IN 查找。**不要**写成两张大表按 JSON 字段连接：那会对
    # 每行都调 json_extract，2,000×13,000 次，实测把接口拖到 60 秒超时。
    resolved = _rows(conn, """
        SELECT DISTINCT r.creator_id AS creator_id, json_extract(e.payload,'$.sourceHandle') AS handle
        FROM cycle_identity_resolution r
        JOIN source_edge e ON e.plan_id = r.plan_id AND e.source_id = r.source_id
        WHERE json_extract(e.payload,'$.sourceKind')='kalodata_http'""")
    owner = {row['handle']: row['creator_id'] for row in resolved}
    if owner:
        marks = ','.join('?' * len(owner))
        edges = _rows(conn, f"""
            SELECT json_extract(payload,'$.sourceHandle') AS handle, json_extract(payload,'$.pid') AS pid,
                   min(CAST(json_extract(payload,'$.sourceRank') AS INTEGER)) AS rank,
                   max(CAST(json_extract(payload,'$.units') AS INTEGER)) AS units
            FROM source_edge
            WHERE json_extract(payload,'$.sourceKind')='kalodata_http'
              AND json_extract(payload,'$.sourceHandle') IN ({marks})
            GROUP BY handle, pid""", tuple(owner))
        positions = [{'creator_id': owner[row['handle']], 'handle': row['handle'], 'pid': row['pid'],
                      'rank': row['rank'], 'units': row['units']} for row in edges]
    else:
        positions = []
    relationships = {row['creator_id']: row for row in _rows(
        conn, 'SELECT creator_id,unlocked,mode,rejected FROM relationship')}
    last_sent = {row[0]: row[1] for row in _rows(
        conn, "SELECT d.creator_id,max(p.started) FROM cycle_delivery d "
              "JOIN cycle_delivery_part p ON p.delivery_id=d.id "
              "WHERE d.state IN ('confirmed','partial_delivery') AND p.kind='card' GROUP BY d.creator_id")}
    sent_pairs = {(row[0], str(row[1])): row[2] for row in _rows(
        conn, "SELECT d.creator_id,d.pid,max(p.started) FROM cycle_delivery d "
              "JOIN cycle_delivery_part p ON p.delivery_id=d.id "
              "WHERE d.state IN ('confirmed','partial_delivery') GROUP BY d.creator_id,d.pid")}
    open_cases = {row[0]: row[1] for row in _rows(
        conn, "SELECT creator_id,updated FROM service_case WHERE state='open'")}
    creators = {row['creator_id'] for row in _rows(conn, 'SELECT DISTINCT creator_id FROM relationship')}

    layers = {name: [] for name in LAYER_ORDER}
    unique_creators = set()
    for row in positions:
        creator = row['creator_id']
        unique_creators.add(creator)
        relationship = relationships.get(creator)
        if relationship is None:
            continue
        unlocked = bool(relationship['unlocked'])
        excluded = bool(relationship['rejected'])
        blocked = creator in open_cases or relationship['mode'] == 'human'
        previously = last_sent.get(creator)
        ready_at = None if previously is None else previously + (UNLOCKED_COOLDOWN if unlocked else LOCKED_COOLDOWN)
        pair_sent = sent_pairs.get((creator, str(row['pid'])))
        if pair_sent is not None:
            layer = 'sent'
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
                              'unlocked': unlocked, 'sentAt': pair_sent,
                              'readyAt': ready_at, 'layer': layer,
                              'caseUpdatedAt': open_cases.get(creator)})

    # Ready positions: strongest lead first, one slot per creator so a single creator cannot fill
    # the whole page while others never surface. Cooling follows the clock, not the lead strength.
    layers['ready'].sort(key=lambda r: (r['rank'] if r['rank'] is not None else 10 ** 9, r['creatorId'], r['pid']))
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
    layers['queued'] = sorted(queued, key=lambda r: (r['creatorId'], r['rank'] if r['rank'] is not None else 10 ** 9))
    layers['ready'] = slots
    layers['cooling'].sort(key=lambda r: (r['readyAt'] or 0, r['rank'] if r['rank'] is not None else 10 ** 9))
    layers['awaiting_reply'].sort(key=lambda r: (r['caseUpdatedAt'] or 0, r['creatorId']))
    layers['excluded'].sort(key=lambda r: (r['creatorId'], r['pid']))
    layers['sent'].sort(key=lambda r: -(r['sentAt'] or 0))

    sent = len(layers['sent'])
    merged = outcomes.get('completed', 0)
    counts = {'leads': leads,
              'merged': merged,
              'unresolved': outcomes.get('unresolved', 0),
              'queued': outcomes.get('queued', 0) + outcomes.get('blocked', 0),
              'positions': len(positions),
              'creators': len(unique_creators),
              'handles': handles[0][0] if handles else 0,
              'sent': sent,
              'unsent': max(0, len(positions) - sent),
              'ready': len(layers['ready']),
              'readyCreators': len({row['creatorId'] for row in layers['ready']}),
              'queued': len(layers['queued']),
              'cooling': len(layers['cooling']),
              'awaitingReply': len(layers['awaiting_reply']),
              'excluded': len(layers['excluded']),
              'creatorsWithRelationship': len(creators)}
    return {'available': True, 'now': now, 'counts': counts,
            'cooldown': {'unlocked': UNLOCKED_COOLDOWN, 'locked': LOCKED_COOLDOWN},
            'layers': {name: len(rows) for name, rows in layers.items()},
            'pools': {name: rows[:limit] for name, rows in layers.items()}}
