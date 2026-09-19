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
import sqlite3
import time
from pathlib import Path

BEIJING = timezone(timedelta(hours=8))
DAY_SECONDS = 86400


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


def _day_row(conn, day):
    start, end = day_bounds(day)
    # 毫秒边界：inbox_event 的 occurred_ms 是平台给的毫秒时间戳。
    start_ms, end_ms = int(start * 1000), int(end * 1000)
    return {
        'date': day,
        'cards': _one(conn, "SELECT count(*) FROM cycle_delivery_part WHERE kind='card' "
                            'AND state=\'confirmed\' AND started>=? AND started<?', (start, end)),
        'texts': _one(conn, "SELECT count(*) FROM cycle_delivery_part WHERE kind='text' "
                            'AND state=\'confirmed\' AND started>=? AND started<?', (start, end)),
        # 触达按**达人**去重：一个达人一个商品发成一次，跟"发了多少条组件"是两件事。
        'creators': _one(conn, 'SELECT count(DISTINCT d.creator_id) FROM cycle_delivery d '
                               'JOIN cycle_delivery_part p ON p.delivery_id=d.id '
                               "WHERE p.kind='card' AND p.state='confirmed' AND p.started>=? AND p.started<?",
                         (start, end)),
        # 发出去了但没确认：不是成功，也不是失败，单独一列。
        'unconfirmed': _one(conn, "SELECT count(*) FROM cycle_delivery_part WHERE kind='card' "
                                  "AND state<>'confirmed' AND started>=? AND started<?", (start, end)),
        'replies': _one(conn, "SELECT count(*) FROM inbox_event WHERE kind='creatorReplies' "
                              'AND historical=0 AND occurred_ms>=? AND occurred_ms<?', (start_ms, end_ms)),
        'showcase': _one(conn, "SELECT count(*) FROM inbox_event WHERE kind='showcaseNotifications' "
                               'AND historical=0 AND occurred_ms>=? AND occurred_ms<?', (start_ms, end_ms)),
        'ourMessages': _one(conn, "SELECT count(*) FROM inbox_event WHERE kind='ourMessages' "
                                  'AND historical=0 AND occurred_ms>=? AND occurred_ms<?', (start_ms, end_ms)),
        'autoReplies': _one(conn, "SELECT count(*) FROM service_reply WHERE state='confirmed' "
                                  'AND started>=? AND started<?', (start, end)),
        'casesOpened': _one(conn, "SELECT count(*) FROM service_case WHERE created>=? AND created<?",
                            (start, end)),
    }


def daily(root, *, count=14, now=None):
    """最近 ``count`` 天的按天统计 + 当前未结人工事项。只读；库不存在就说不可用。"""
    root = Path(root)
    path = root / 'var/second-cycle.sqlite'
    if not path.exists():
        return {'available': False, 'timezone': 'Asia/Shanghai', 'days': [], 'totals': {}, 'openCases': 0}
    now = time.time() if now is None else now
    count = max(1, min(int(count), 92))
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        rows = [_day_row(conn, day) for day in _partition(now, count)]
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        open_cases = (_one(conn, "SELECT count(*) FROM service_case WHERE state='open'", ())
                      if 'service_case' in tables else 0)
    totals = {}
    for key in ('cards', 'texts', 'creators', 'unconfirmed', 'replies', 'showcase', 'ourMessages',
                'autoReplies', 'casesOpened'):
        totals[key] = sum(row[key] for row in rows)
    return {'available': True, 'timezone': 'Asia/Shanghai', 'now': now, 'days': rows,
            'totals': totals, 'openCases': open_cases}
