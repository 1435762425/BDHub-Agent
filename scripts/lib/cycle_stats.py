"""按天统计：发送、触达、回复、橱窗、自动回复。只读，每次读取重算，不落库。

口径（用户 2026-09-15 确认）：

- 按**北京时间**分天（固定 +08:00；中国没有夏令时，所以不需要时区库）。
- "触达"只算**回查确认**（`cycle_delivery_part.state='confirmed'`）：发出去了但还没确认的另列
  `unconfirmed`，绝不混进触达数——那正是这个系统一直在防的"把未知说成成功"。
- 回复与橱窗**排除历史补录**（`inbox_event.historical=1`）：补录进来的是很久以前的旧消息，
  算成"今天收到的回复"会让日历整片说谎。
- 消息时间用 `occurred_ms`（平台给的创建时间）。这一列在入库时已经校验过合法区间，空值表示
  平台没给可用时间，那种行不计入任何一天，而不是硬塞给今天。

这份口径只写一次：收信卡片要的"今日回复/加橱窗"和日历页要的整月数字来自同一个函数。
"""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import time
from pathlib import Path

BEIJING = timezone(timedelta(hours=8))
DAY_SECONDS = 86400
DETAIL_LIMIT_MAX = 100
DETAIL_OFFSET_MAX = 5000


def beijing_day(stamp):
    """Epoch 秒 → 北京时间的 ``YYYY-MM-DD``。"""
    return datetime.fromtimestamp(stamp, BEIJING).strftime('%Y-%m-%d')


def day_bounds(day):
    """``YYYY-MM-DD`` → (起, 止) 两个 epoch 秒，左闭右开。"""
    start = datetime.strptime(day, '%Y-%m-%d').replace(tzinfo=BEIJING)
    return start.timestamp(), (start + timedelta(days=1)).timestamp()


def _partition(now, count):
    """最近 ``count`` 个北京日，最老的在前，最后一个是今天。"""
    today = datetime.fromtimestamp(now, BEIJING).replace(hour=0, minute=0, second=0, microsecond=0)
    return [(today - timedelta(days=offset)).strftime('%Y-%m-%d') for offset in range(count - 1, -1, -1)]


def _one(conn, sql, args):
    row = conn.execute(sql, args).fetchone()
    return int(row[0] or 0) if row else 0


def _day_row(conn, day, plan, reply_kind=True):
    start, end = day_bounds(day)
    # 毫秒边界：inbox_event 的 occurred_ms 是平台给的毫秒时间戳。
    start_ms, end_ms = int(start * 1000), int(end * 1000)
    return {
        'date': day,
        'cards': _one(conn, "SELECT count(*) FROM cycle_delivery_part p JOIN cycle_delivery d ON d.id=p.delivery_id WHERE d.plan_id=? AND p.kind='card' "
                            'AND p.state=\'confirmed\' AND p.started>=? AND p.started<?', (plan,start, end)),
        'texts': _one(conn, "SELECT count(*) FROM cycle_delivery_part p JOIN cycle_delivery d ON d.id=p.delivery_id WHERE d.plan_id=? AND p.kind='text' "
                            'AND p.state=\'confirmed\' AND p.started>=? AND p.started<?', (plan,start, end)),
        # 触达按**达人**去重：一个达人一个商品发成一次，跟"发了多少条组件"是两件事。
        'creators': _one(conn, 'SELECT count(DISTINCT d.creator_id) FROM cycle_delivery d '
                               'JOIN cycle_delivery_part p ON p.delivery_id=d.id '
                               "WHERE d.plan_id=? AND d.state='confirmed' AND p.kind='card' AND p.state='confirmed' AND p.started>=? AND p.started<?",
                         (plan,start, end)),
        # 发出去了但没确认：不是成功，也不是失败，单独一列。
        'unconfirmed': _one(conn, "SELECT count(*) FROM cycle_delivery_part p JOIN cycle_delivery d ON d.id=p.delivery_id WHERE d.plan_id=? AND p.kind='card' "
                                  "AND p.state<>'confirmed' AND p.started>=? AND p.started<?", (plan,start, end)),
        'replies': _one(conn, "SELECT count(*) FROM inbox_event WHERE plan_id=? AND kind='creatorReplies' "
                              'AND historical=0 AND occurred_ms>=? AND occurred_ms<?', (plan,start_ms, end_ms)),
        'showcase': _one(conn, "SELECT count(*) FROM inbox_event WHERE plan_id=? AND kind='showcaseNotifications' "
                               'AND historical=0 AND occurred_ms>=? AND occurred_ms<?', (plan,start_ms, end_ms)),
        'ourMessages': _one(conn, "SELECT count(*) FROM inbox_event WHERE plan_id=? AND kind='ourMessages' "
                                  'AND historical=0 AND occurred_ms>=? AND occurred_ms<?', (plan,start_ms, end_ms)),
        # Historical name: every confirmed service reply, manual ones included. The two fields below split it.
        'autoReplies': _one(conn, "SELECT count(*) FROM service_reply WHERE plan_id=? AND state='confirmed' "
                                  'AND started>=? AND started<?', (plan,start, end)),
        'serviceRepliesManual': _one(conn, "SELECT count(*) FROM service_reply WHERE plan_id=? AND state='confirmed' "
                                           "AND kind IN ('manual','manual_card') AND started>=? AND started<?", (plan,start, end)) if reply_kind else None,
        'serviceRepliesAi': _one(conn, "SELECT count(*) FROM service_reply WHERE plan_id=? AND state='confirmed' "
                                       "AND kind NOT IN ('manual','manual_card') AND started>=? AND started<?", (plan,start, end)) if reply_kind else None,
        # People, not messages: creators (by OECID) who sent at least one new message that day.
        'replyCreators': _one(conn, "SELECT count(DISTINCT oec) FROM inbox_event WHERE plan_id=? AND kind='creatorReplies' "
                                    'AND historical=0 AND occurred_ms>=? AND occurred_ms<?', (plan,start_ms, end_ms)),
        'casesOpened': _one(conn, "SELECT count(*) FROM service_case WHERE plan_id=? AND created>=? AND created<?",
                            (plan,start, end)),
    }


def _clean_text(value, limit):
    return value[:limit] if isinstance(value, str) and value else None


def _detail_item(row, handles):
    try:
        payload = json.loads(row['data_json']) if row['data_json'] else {}
    except (TypeError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    snapshot_handle = _clean_text(payload.get('handle'), 100) if row['item_kind'] == 'delivery' else None
    current_handle = handles.get((row['creator_id'], row['oec'])) if row['creator_id'] else None
    item = {
        'kind': row['item_kind'],
        'occurredAt': int(row['occurred_ms']),
        'ref': _clean_text(row['ref'], 200),
        'creatorId': _clean_text(row['creator_id'], 200),
        'oec': _clean_text(row['oec'], 40),
        'handle': current_handle or snapshot_handle,
        'handleAtEvent': snapshot_handle,
        'pid': _clean_text(row['pid'], 40),
        'status': _clean_text(row['status'], 80),
        'product': None,
        'creatorPercent': None,
        'catalogSource': None,
        'text': None,
        'format': None,
        'textState': _clean_text(row['aux_status'], 80),
    }
    if row['item_kind'] == 'delivery':
        name = payload.get('name') if isinstance(payload.get('name'), dict) else {}
        offer = payload.get('offer') if isinstance(payload.get('offer'), dict) else {}
        message = payload.get('message') if isinstance(payload.get('message'), dict) else {}
        item['product'] = _clean_text(name.get('shortName') or name.get('mention') or name.get('shortNameIt') or name.get('mentionIt') or offer.get('title'), 300)
        item['creatorPercent'] = _clean_text(offer.get('creatorPercent'), 32)
        item['catalogSource'] = _clean_text(offer.get('catalogSource'), 40)
        item['text'] = _clean_text(message.get('textIt'), 4000)
        item['format'] = 'text' if item['text'] else None
    elif row['item_kind'] in ('reply', 'auto_reply'):
        item['format'] = payload.get('format') if payload.get('format') in ('text', 'attachment_or_unsupported') else 'not_fetched'
        item['text'] = _clean_text(payload.get('text'), 4000) if item['format'] == 'text' else None
    elif row['item_kind'] == 'case':
        item['status'] = _clean_text(payload.get('reason') or row['status'], 80)
    return item


def _current_handles(root, rows, market):
    path = Path(root) / 'var/creator-identities.sqlite'
    pairs = {(row['creator_id'], row['oec']) for row in rows if row['creator_id'] and row['oec']}
    if not path.exists() or not pairs:
        return {}
    creators = sorted({creator for creator, _ in pairs})
    placeholders = ','.join('?' for _ in creators)
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        found = conn.execute('SELECT creator_id,oec_id,current_handle FROM creator_identity '
                             f"WHERE market=? AND creator_id IN ({placeholders})", (market,*creators)).fetchall()
    return {(creator, oec): handle for creator, oec, handle in found
            if (creator, oec) in pairs and isinstance(handle, str) and handle}


def day_detail(root, day, *, market='it', offset=0, limit=50):
    """一个北京自然日的有界核对明细。只读；不返回原始 snapshot、回执或平台 payload。"""
    root = Path(root)
    try:
        parsed = datetime.strptime(day, '%Y-%m-%d')
    except (TypeError, ValueError):
        raise ValueError('invalid_inbox_day')
    if parsed.strftime('%Y-%m-%d') != day:
        raise ValueError('invalid_inbox_day')
    if type(offset) is not int or not 0 <= offset <= DETAIL_OFFSET_MAX:
        raise ValueError('invalid_inbox_offset')
    if type(limit) is not int or not 1 <= limit <= DETAIL_LIMIT_MAX:
        raise ValueError('invalid_inbox_limit')
    path = root / 'var/second-cycle.sqlite'
    if not path.exists():
        return {'available': False, 'market':market,'date': day, 'timezone': 'Asia/Shanghai', 'summary': None,
                'total': 0, 'offset': offset, 'limit': limit, 'nextOffset': None,
                'items': [], 'platformWrites': False}
    start, end = day_bounds(day)
    start_ms, end_ms = int(start * 1000), int(end * 1000)
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as lookup:
        plan=lookup.execute("SELECT id FROM plan WHERE institution='bjn-local-research' AND market=?",(market,)).fetchone()
    if not plan:
        return {'available':False,'market':market,'date':day,'timezone':'Asia/Shanghai','summary':None,
                'total':0,'offset':offset,'limit':limit,'nextOffset':None,'items':[],'platformWrites':False}
    plan=plan[0]
    sql = '''WITH detail AS (
      SELECT 'delivery' item_kind,CAST(p.started*1000 AS INTEGER) occurred_ms,d.id ref,
             d.creator_id,d.oec,d.pid,p.state status,d.snapshot data_json,
             (SELECT t.state FROM cycle_delivery_part t WHERE t.delivery_id=d.id AND t.kind='text') aux_status
        FROM cycle_delivery_part p JOIN cycle_delivery d ON d.id=p.delivery_id
       WHERE d.plan_id=? AND p.kind='card' AND p.started>=? AND p.started<?
      UNION ALL
      SELECT CASE e.kind WHEN 'creatorReplies' THEN 'reply' ELSE 'showcase' END,
             e.occurred_ms,e.message_id,r.creator_id,e.oec,NULL,e.kind,
             CASE WHEN e.kind='creatorReplies' THEN v.payload ELSE NULL END,NULL
        FROM inbox_event e
        LEFT JOIN relationship r ON r.plan_id=e.plan_id AND r.oec=e.oec
        LEFT JOIN inbox_content_head h ON h.plan_id=e.plan_id AND h.cid=e.cid AND h.message_id=e.message_id
        LEFT JOIN inbox_content_version v ON v.plan_id=h.plan_id AND v.cid=h.cid AND v.message_id=h.message_id AND v.hash=h.hash
       WHERE e.plan_id=? AND e.historical=0 AND e.kind IN ('creatorReplies','showcaseNotifications')
         AND e.occurred_ms>=? AND e.occurred_ms<?
      UNION ALL
      SELECT 'auto_reply',CAST(started*1000 AS INTEGER),id,creator_id,oec,NULL,state,
             json_object('format','text','text',text),NULL
        FROM service_reply WHERE plan_id=? AND state='confirmed' AND started>=? AND started<?
      UNION ALL
      SELECT 'case',CAST(c.created*1000 AS INTEGER),c.id,c.creator_id,r.oec,NULL,c.state,
             json_object('reason',c.reason),NULL
        FROM service_case c LEFT JOIN relationship r ON r.plan_id=c.plan_id AND r.creator_id=c.creator_id
       WHERE c.plan_id=? AND c.created>=? AND c.created<?
    ) SELECT * FROM detail ORDER BY occurred_ms DESC,ref LIMIT ? OFFSET ?'''
    args = (plan,start, end, plan,start_ms, end_ms, plan,start, end, plan,start, end, limit, offset)
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        summary = _day_row(conn, day, plan, 'kind' in {row[1] for row in conn.execute('PRAGMA table_info(service_reply)')})
        rows = conn.execute(sql, args).fetchall()
    handles = _current_handles(root, rows, market)
    total = sum(summary[key] for key in ('cards', 'unconfirmed', 'replies', 'showcase',
                                         'autoReplies', 'casesOpened'))
    items = [_detail_item(row, handles) for row in rows]
    next_offset = offset + len(items) if offset + len(items) < total else None
    return {'available': True, 'market':market,'date': day, 'timezone': 'Asia/Shanghai', 'summary': summary,
            'total': total, 'offset': offset, 'limit': limit, 'nextOffset': next_offset,
            'items': items, 'platformWrites': False}


def daily(root, *, market='it', count=14, now=None):
    """最近 ``count`` 天的按天统计 + 当前未结人工事项。只读；库不存在就说不可用。"""
    root = Path(root)
    path = root / 'var/second-cycle.sqlite'
    if not path.exists():
        return {'available': False, 'market':market,'timezone': 'Asia/Shanghai', 'days': [], 'totals': {}, 'openCases': 0}
    now = time.time() if now is None else now
    count = max(1, min(int(count), 92))
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        plan=conn.execute("SELECT id FROM plan WHERE institution='bjn-local-research' AND market=?",(market,)).fetchone()
        if not plan:return {'available':False,'market':market,'timezone':'Asia/Shanghai','days':[],'totals':{},'openCases':0}
        plan=plan[0]
        reply_kind = 'kind' in {row[1] for row in conn.execute('PRAGMA table_info(service_reply)')}
        rows = [_day_row(conn, day, plan, reply_kind) for day in _partition(now, count)]
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        open_cases = (_one(conn, "SELECT count(*) FROM service_case WHERE plan_id=? AND state='open'", (plan,))
                      if 'service_case' in tables else 0)
    totals = {}
    for key in ('cards', 'texts', 'creators', 'unconfirmed', 'replies', 'showcase', 'ourMessages',
                'autoReplies', 'serviceRepliesManual', 'serviceRepliesAi', 'replyCreators', 'casesOpened'):
        values = [row[key] for row in rows]
        totals[key] = None if any(value is None for value in values) else sum(values)
    return {'available': True, 'market':market,'timezone': 'Asia/Shanghai', 'now': now, 'days': rows,
            'totals': totals, 'openCases': open_cases}
