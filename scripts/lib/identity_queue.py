"""The creator-identity (OECID) queue that stands between a Kalodata lead and the sending pool.

A Kalodata lead is only a ``handle``. Sending needs the platform identity -- the OECID -- and the
whole system is built on that: the sending pool's positions come from ``cycle_identity_resolution``,
and that table only gets a row once a Find returned both the internal creatorId *and* the OECID.
So a lead whose handle cannot be found never becomes a position, by construction, not by a filter
someone has to remember to apply.

That makes the identity step a real stage of the funnel with its own queue, its own budget and its
own failure mode, which is what this module measures:

* ``resolved``   -- leads already carrying an OECID; these are in the sending pool
* ``pending``    -- leads not decided yet: never submitted, queued for a Find, or blocked by the
                    account; this is the work the backfill button consumes
* ``unresolved`` -- the Find ran and found no identity for that handle. The lead is kept and shown,
                    never given a fabricated OECID, and it does not enter the pool.

Counts are reported in both units on purpose. The operator counts **leads** (a handle can appear in
several leads), while the platform work is per **handle**, because one handle resolves once.
"""
import sqlite3
import sys
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


def _iso(seconds):
    """与 discovery_item.finished_at 同一种写法：UTC、微秒、Z 结尾——这样字符串比较就是时间比较。"""
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")

SCOPE = {'institution': 'bjn-local-research', 'market': 'it'}
KALODATA = "json_extract(e.payload,'$.sourceKind')='kalodata_http'"
HANDLE = "json_extract(e.payload,'$.sourceHandle')"


def root_of(module_file=__file__):
    return Path(module_file).resolve().parents[2]


def _read(db, sql, args=()):
    path = Path(db)
    if not path.exists():
        return []
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        conn.execute('BEGIN')
        return list(conn.execute(sql, args))


def _tables(db, *names):
    found = {row[0] for row in _read(db, "SELECT name FROM sqlite_master WHERE type='table'")}
    return all(name in found for name in names)


def counts(root):
    """Lead/handle counts for the identity stage, or ``None`` when the plan has no identity work.

    ``second-cycle.sqlite`` is written continuously by the resident preparation worker, so a read can
    hit a momentary ``database is locked``. That is expected traffic on a shared file, not a broken
    stage: it is retried before the page is ever told the identity numbers are unavailable.
    """
    for attempt in range(3):
        try:
            return _counts_once(root)
        except sqlite3.OperationalError:
            if attempt == 2:
                raise
            time.sleep(0.25)
    return None


def _counts_once(root):
    db = Path(root) / 'var/second-cycle.sqlite'
    if not db.exists():
        return None
    # One connection for every number. Seven separate connects cost about as much as the queries
    # themselves, and this read is on the page's critical path.
    with closing(sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)) as conn:
        conn.execute('BEGIN')
        found = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'plan', 'source_edge', 'cycle_identity_resolution', 'cycle_identity_outcome'} <= found:
            return None
        plan = conn.execute('SELECT id FROM plan WHERE institution=? AND market=? AND state=?',
                            (SCOPE['institution'], SCOPE['market'], 'active')).fetchone()
        if not plan:
            return None
        plan_id = plan[0]
        leads = conn.execute(f'SELECT count(*) FROM source_edge e WHERE {KALODATA} AND e.plan_id=?',
                             (plan_id,)).fetchone()[0]
        resolved_leads, resolved_creators = conn.execute(
            'SELECT count(*),count(DISTINCT creator_id) FROM cycle_identity_resolution WHERE plan_id=?',
            (plan_id,)).fetchone()
        outcomes = {row[0]: row[1] for row in conn.execute(
            'SELECT status,count(*) FROM cycle_identity_outcome WHERE plan_id=? GROUP BY status', (plan_id,))}
        unresolved_leads = outcomes.get('unresolved', 0)
        # The three ways a lead can still be undecided, kept apart because the next action differs:
        # nothing submitted yet, waiting in the Find queue, and blocked by the account.
        breakdown = {'unhanded': max(0, leads - resolved_leads - unresolved_leads - outcomes.get('queued', 0)
                                     - outcomes.get('blocked', 0)),
                     'queued': outcomes.get('queued', 0), 'blocked': outcomes.get('blocked', 0)}
        pending_leads = sum(breakdown.values())
        unresolved_creators = conn.execute(f"""SELECT count(DISTINCT {HANDLE}) FROM source_edge e
            JOIN cycle_identity_outcome o ON o.plan_id=e.plan_id AND o.source_id=e.source_id
            WHERE {KALODATA} AND e.plan_id=? AND o.status='unresolved'""", (plan_id,)).fetchone()[0]
        pending_creators = conn.execute(f"""SELECT count(DISTINCT {HANDLE}) FROM source_edge e
            LEFT JOIN cycle_identity_outcome o ON o.plan_id=e.plan_id AND o.source_id=e.source_id
            WHERE {KALODATA} AND e.plan_id=? AND (o.status IS NULL OR o.status IN ('queued','blocked'))""",
            (plan_id,)).fetchone()[0]
    return {'plan': plan_id, 'leads': leads, 'resolvedLeads': resolved_leads, 'resolvedCreators': resolved_creators,
            'pendingLeads': pending_leads, 'pendingCreators': pending_creators,
            'unresolvedLeads': unresolved_leads, 'unresolvedCreators': unresolved_creators,
            'pendingBreakdown': breakdown,
            # If these ever disagree the stage is showing numbers it cannot account for, and the
            # page says so instead of quietly presenting a total that does not add up.
            'reconciled': leads == resolved_leads + unresolved_leads + pending_leads}


def policy(root):
    """The published identity channel: this stage spends the collection account, not Kalodata.

    The policy table is read-only and shared with the batch preparation worker, which writes to the
    same database, so a momentary ``database is locked`` is possible. The read itself waits out a busy
    database -- but only briefly here, because this one answers a page: a status card that hangs for
    minutes is worse than saying "the channel could not be read". Reporting the conservative 3 QPS
    default as if it were the published channel would be a wrong number dressed as a fact, so an
    unreadable policy is returned as exactly that.
    """
    scripts = str(Path(root) / 'scripts')
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    try:
        from lib.identity_acceptance import production_policy
        published = production_policy(root, wait_seconds=6.0)
        return {**published, 'published': published.get('acceptanceId') is not None, 'readable': True}
    except Exception:
        return {'qps': 3, 'lanes': 3, 'acceptanceId': None, 'published': False, 'readable': False}


def walk(root, since):
    """本次运行在名单库里**结算了什么**，用来解释"已领为什么大于找到＋搜索不到"。

    ``claimed`` 数的是**领取的线索条数**，而 ``found``/``notFound`` 数的是**达人个数**（按
    handle/creator 去重）。一个达人名下可以挂很多条线索（实测有 13 条的），所以那两个数本来
    就不该相等。这里把差额量出来，而不是让页面自己猜：

    * ``settled``     本次运行内结算的线索条数
    * ``newHandles``  其中 handle 从没被判过的部分（≈ 找到 ＋ 搜索不到）
    * ``repeats``     同一位达人的其它线索（判定已存在，不再计入增量）
    * ``inflight``    已领但还没结算（被账号挡住、延后重试）

    只读；``since`` 是运行开始时刻的 UTC ISO 串（与 ``finished_at`` 同格式，可直接比较）。
    """
    db = Path(root) / 'var/creator-discovery.sqlite'
    if not db.exists():
        return None
    if isinstance(since, (int, float)) and not isinstance(since, bool):
        since = _iso(since)
    if not isinstance(since, str) or not since:
        return None
    with closing(sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'discovery_item' not in tables:
            return None
        settled = conn.execute('SELECT count(*) FROM discovery_item WHERE finished_at>=?',
                               (since,)).fetchone()[0]
        # "全新" = 这位达人的**第一条**已结算线索就落在本次运行里。先按 handle 取最早时间再数，
        # 比逐行 EXISTS 快两个数量级——后者 1,700 行 × 13,000 行会把页面读卡住 20 秒（实测）。
        new_handles = conn.execute(
            'SELECT count(*) FROM (SELECT handle, min(finished_at) AS first_at FROM discovery_item '
            'WHERE finished_at IS NOT NULL AND handle IS NOT NULL GROUP BY handle HAVING first_at>=?)',
            (since,)).fetchone()[0]
    settled, new_handles = int(settled), int(new_handles)
    return {'since': since, 'settled': settled, 'newHandles': new_handles,
            'repeats': max(0, settled - new_handles)}


def by_creator(root):
    """达人身份的三个分类，**单位是去重 handle**，而且**一次互斥**。

    为什么必须互斥：一个达人名下可以挂很多条线索（实测有 13 条的）。它的某条线索拿到 OECID，
    这个达人就进"已就位"；另一条线索还没判定，同一个达人又会被算进"待补"。三个数各自去重再相加
    实测是 4,430，而全部达人只有 3,069——多出来的一千多是同一个达人在两处各算一次。
    所以这里按优先级 **已就位 > 搜索不到 > 还没判过** 分类一次，三项相加恒等于 handle 总数。

    另外给出 ``positions``：**可达位置数 = 达人 × 商品**。一位达人有了 OECID，他名下的所有线索商品就
    都是可发位置——平台回答的是"这个 handle 是谁"，与商品无关（见 `lib/lead_pool.py` 的达人级口径）。
    所以这张卡不需要"还差多少条线索要补判定"这种数字：那件事本来就不该按线索做。
    """
    db = Path(root) / 'var/second-cycle.sqlite'
    if not db.exists():
        return None
    with closing(sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)) as conn:
        conn.execute('BEGIN')
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'plan', 'source_edge', 'cycle_identity_resolution', 'cycle_identity_outcome'} <= tables:
            return None
        plan = conn.execute('SELECT id FROM plan WHERE institution=? AND market=? AND state=?',
                            (SCOPE['institution'], SCOPE['market'], 'active')).fetchone()
        if not plan:
            return None
        sql = f"""
        WITH K AS (SELECT {HANDLE} AS h, e.source_id FROM source_edge e
                   WHERE {KALODATA} AND e.plan_id=:plan),
        R AS (SELECT DISTINCT k.h FROM cycle_identity_resolution r
              JOIN K k ON k.source_id=r.source_id),
        U AS (SELECT DISTINCT k.h FROM K k JOIN cycle_identity_outcome o ON o.source_id=k.source_id
              WHERE o.status='unresolved'),
        -- 「被挡住」＝问过但没拿到平台的真实回答（请求/签名失败、账号起不来、被远端挡回）。它不是
        -- "没问过"，也不是"找不到"；这一批必须能重试，否则这些达人永远停在待补、补 OECID 一直空转。
        B AS (SELECT DISTINCT k.h FROM K k JOIN cycle_identity_outcome o ON o.source_id=k.source_id
              WHERE o.status='blocked')
        SELECT
          (SELECT count(DISTINCT h) FROM K) AS handles,
          (SELECT count(*) FROM R) AS resolved,
          (SELECT count(*) FROM U WHERE h NOT IN (SELECT h FROM R)) AS unresolved,
          (SELECT count(*) FROM B WHERE h NOT IN (SELECT h FROM R) AND h NOT IN (SELECT h FROM U)) AS blocked,
          (SELECT group_concat(h, '|') FROM B WHERE h NOT IN (SELECT h FROM R) AND h NOT IN (SELECT h FROM U)) AS blocked_handles,
          (SELECT count(DISTINCT h) FROM K WHERE h NOT IN (SELECT h FROM R)
             AND h NOT IN (SELECT h FROM U) AND h NOT IN (SELECT h FROM B)) AS unknown,
          (SELECT count(*) FROM K) AS leads,
          -- 可达位置：已就位达人的**全部**线索商品，去重到 达人×商品。
          -- `json_extract` 必须先抽成列再连接：直接在 ON 里对每一行调它，3000×13000 次会把读卡住。
          (SELECT count(*) FROM (SELECT DISTINCT e2.h, e2.pid FROM
             (SELECT json_extract(payload,'$.sourceHandle') AS h, json_extract(payload,'$.pid') AS pid
              FROM source_edge WHERE plan_id=:plan
                AND json_extract(payload,'$.sourceKind')='kalodata_http') e2
             WHERE e2.h IN (SELECT h FROM R))) AS positions
        """
        row = conn.execute(sql, {'plan': plan[0]}).fetchone()
    if row is None:
        return None
    # 列顺序：handles / resolved / unresolved / blocked / blocked_handles / unknown / leads / positions
    values = {'handles': int(row[0]), 'resolved': int(row[1]), 'unresolved': int(row[2]),
              'blocked': int(row[3]), 'unknown': int(row[5]), 'leads': int(row[6]),
              'positions': int(row[7])}
    blocked_handles = str(row[4] or '').split('|') if row[4] else []
    # 四项互斥、相加必须等于 handle 总数：这是由查询本身保证的，对不上就说明这一读不可信。
    values['reconciled'] = (values['resolved'] + values['unresolved'] + values['blocked']
                            + values['unknown']) == values['handles']
    # 被挡住的原因分布：只说"被挡住"不够，要说清为什么没问成。**只统计真的落在这一桶里的达人**，
    # 否则会看到"被挡住 19"旁边写着 56（那 56 里有 52 位的身份其实已经从别的线索拿到了）。
    reasons = []
    if blocked_handles:
        db2 = Path(root) / 'var/creator-discovery.sqlite'
        if db2.exists():
            with closing(sqlite3.connect(db2.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)) as items:
                if items.execute("SELECT 1 FROM sqlite_master WHERE name='discovery_item'").fetchone():
                    marks = ','.join('?' * len(blocked_handles))
                    reasons = [{'reason': str(r[0] or 'unknown'), 'count': int(r[1])} for r in items.execute(
                        f"SELECT reason,count(DISTINCT handle) FROM discovery_item WHERE status='blocked' "
                        f"AND handle IN ({marks}) GROUP BY reason ORDER BY 2 DESC LIMIT 4", tuple(blocked_handles))]
    values['blockedReasons'] = reasons
    return values


def status(root=None):
    """Everything the identity card shows. Read-only: no platform contact, no enrollment."""
    from lib import job_run
    root = Path(root or root_of())
    measured = counts(root)
    job = job_run.status(root, 'identity')['identity']
    # 对账只在这里算：每次读都按当前运行重新数一遍，页面永远是对的，也不需要驱动多写一份。
    run = job['run']
    walked = walk(root, run['startedAt']) if run and isinstance(run.get('startedAt'), (int, float)) else None
    return {'available': measured is not None,
            **(measured or {}),
            # 达人（去重 handle）是这张卡的唯一单位；线索级数字只用来解释"本次在干什么"。
            'byCreator': by_creator(root),
            'policy': policy(root),
            'walk': ({key: walked[key] for key in ('settled', 'newHandles', 'repeats')}
                     if walked else None),
            'config': job['config'], 'run': run}
