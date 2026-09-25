"""非全托（Campaign）商品的筛分：按渠道各算一份，单位是 **offer（PID × 活动）**。

全托的筛分账本（`global_screen`）是按 PID 记的、字段是销量/评分/佣金差——那套门槛不适用于 Campaign
（已确认：非全托**不得**套全托的 300 件门槛）。这里复用真正与渠道无关的那部分：

* 商品级可推资格 = `second_cycle.assess_offer`（达人佣金必须高于公开、非全托库存>100、available、
  剩余>45天；评分>4 只是偏好）；
* 佣金 = `cycle_catalog.commission_calculator`（已确认规则：机构 min(2, 总−公开−1)、达人 = 总−机构、差≥2）。

**为什么筛分时要重算佣金而不是读快照里的 `creatorPercent`**：快照可能是旧规则算的（历史快照就是这么来的）。
把「原始总/公开佣金 + 当前规则」当输入，筛分结果才与规则一致，也不会因为快照新旧而给出两套答案。
"""
import json
import sqlite3
import time
from datetime import datetime
from contextlib import closing
from decimal import Decimal, InvalidOperation
from pathlib import Path

from lib.cycle_catalog import commission_calculator
from lib.second_cycle import assess_offer, digest

INSTITUTION = 'bjn-local-research'
SOURCES = ('campaign', 'selected')
SCHEMA = '''
CREATE TABLE IF NOT EXISTS campaign_screen_run(
  run_id TEXT PRIMARY KEY, source TEXT NOT NULL, source_snapshot TEXT NOT NULL,
  rule TEXT NOT NULL, rule_fingerprint TEXT NOT NULL, state TEXT NOT NULL,
  counts TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS campaign_screen(
  run_id TEXT NOT NULL, pid TEXT NOT NULL, campaign_id TEXT NOT NULL, state TEXT NOT NULL,
  reasons TEXT NOT NULL, creator_percent TEXT, total_percent TEXT, public_percent TEXT,
  stock TEXT, end_at TEXT, available INTEGER NOT NULL, observed REAL NOT NULL,
  PRIMARY KEY(run_id, pid, campaign_id));
CREATE INDEX IF NOT EXISTS campaign_screen_state ON campaign_screen(run_id,state);
-- 入池是筛分的投影：一个 PID 一条，只带被选中的那个活动；落选活动留在 alternatives 里不删除。
CREATE TABLE IF NOT EXISTS campaign_pool_run(
  run_id TEXT PRIMARY KEY, source TEXT NOT NULL, source_snapshot TEXT NOT NULL,
  created REAL NOT NULL, counts TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS campaign_pool_item(
  run_id TEXT NOT NULL, pid TEXT NOT NULL, state TEXT NOT NULL, campaign_id TEXT,
  creator_percent TEXT, total_percent TEXT, public_percent TEXT, end_at TEXT, stock TEXT,
  alternatives TEXT NOT NULL, reasons TEXT NOT NULL, updated REAL NOT NULL,
  PRIMARY KEY(run_id,pid));
CREATE INDEX IF NOT EXISTS campaign_pool_state ON campaign_pool_item(run_id,state);
'''


def root_of(module_file=__file__):
    return Path(module_file).resolve().parents[2]


def db_path(root, market='it'):
    return Path(root) / ('var/campaign-screen.sqlite' if market == 'it' else f'var/campaign-screen-{market}.sqlite')


def snapshot_for(root, source, market='it'):
    """The head snapshot and its offers for one channel, read-only."""
    db = Path(root) / 'var/second-cycle.sqlite'
    if not db.exists():
        return None, []
    with closing(sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)) as conn:
        conn.execute('BEGIN')
        row = conn.execute(
            'SELECT h.snapshot_id,c.payload FROM catalog_head h JOIN catalog c ON c.id=h.snapshot_id '
            'JOIN plan p ON p.id=h.plan_id WHERE p.institution=? AND p.market=? AND h.source=?',
            (INSTITUTION, market, f'live-{market}-{source}')).fetchone()
    if not row:
        return None, []
    return row[0], json.loads(row[1])


def _basis(percent):
    """百分数字符串 → 平台原始基点；缺失或非法返回 None（由 assess_offer 记 missing_*）。"""
    if percent is None:
        return None
    try:
        value = Decimal(str(percent))
    except (InvalidOperation, TypeError):
        return None
    if not value.is_finite() or not 0 <= value <= 100:
        return None
    return str(int(value * 100))


def evaluate(offer, *, calculate, at):
    """One offer's verdict under the confirmed rule. Never raises on bad data.

    The creator's percentage is always decided *here* (raw facts + current rule), so
    ``assess_offer``'s ``missing_creatorPercent`` is a consequence of our own decision, not a
    separate platform fact -- reporting both would count one product twice in the reason totals.
    """
    total, public = _basis(offer.get('totalPercent')), _basis(offer.get('publicPercent'))
    reasons = []
    commission_error = None
    creator = None
    if total is None:
        commission_error = 'missing_total_commission'
    elif public is None:
        # assess_offer names the missing field itself; no need to guess here.
        creator = None
    else:
        result = calculate(total, public)
        if result.valid:
            creator = str(result.creator_pct)
        else:
            commission_error = result.error or 'commission_invalid'
    if commission_error:
        reasons.append(commission_error)
    assessment = assess_offer({**offer, 'creatorPercent': creator}, at)
    for reason in assessment['reasons']:
        if reason == 'missing_creatorPercent':
            continue
        if reason not in reasons:
            reasons.append(reason)
    return {'pid': str(offer['pid']), 'campaignId': str(offer.get('campaignId') or ''),
            'state': 'eligible' if not reasons else 'ineligible', 'reasons': reasons,
            'creatorPercent': creator, 'totalPercent': offer.get('totalPercent'),
            'publicPercent': offer.get('publicPercent'), 'stock': offer.get('stock'),
            'endAt': offer.get('endAt'), 'available': offer.get('available') is True}


def end_timestamp(value):
    """ISO 时间 → epoch；缺失或非法排到最后（挑活动时"截止更晚"优先，缺失不能占先）。"""
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None
    return moment.timestamp()


def pick(offers):
    """One campaign per PID: 达人佣金最高 → 截止更晚 → 活动ID最小（确定性）。

    只用合格候选调用。规则固定是为了确定性——同一批商品每次算出来必须是同一个活动，
    否则快照、链接意图和消息内容会来回变。
    """
    def key(item):
        creator = item.get('creatorPercent')
        end = end_timestamp(item.get('endAt'))
        return (-float(creator) if creator not in (None, '') else 0.0,
                -(end if end is not None else -1), str(item.get('campaignId')))
    return sorted(offers, key=key)[0]


def pool(built):
    """Project a screening into per-PID pool entries: one chosen campaign, the rest as alternatives.

    **按 PID 分组后再看有没有合格活动**——不能按 offer 的状态分桶：同一个 PID 可能既有一个合格活动、
    又有一个不合格活动，那样它会在"已入池"和"未入池"里各出现一次，池子总数凭空多出来。
    一个都没有合格活动时它就是"未入池"，并如实带上原因；**不硬挑**。
    """
    by_pid = {}
    for item in built.get('items', []):
        by_pid.setdefault(item['pid'], []).append(item)
    items = []
    for pid, offers in by_pid.items():
        good = [o for o in offers if o['state'] == 'eligible']
        if not good:
            reasons = sorted({reason for o in offers for reason in o['reasons']})
            items.append({'pid': pid, 'state': 'held', 'campaignId': None, 'creatorPercent': None,
                          'totalPercent': None, 'publicPercent': None, 'endAt': None, 'stock': None,
                          'alternatives': [], 'reasons': reasons})
            continue
        chosen = pick(good)
        others = [o for o in good if o is not chosen]
        items.append({'pid': pid, 'state': 'chosen', 'campaignId': chosen['campaignId'],
                      'creatorPercent': chosen['creatorPercent'], 'totalPercent': chosen['totalPercent'],
                      'publicPercent': chosen['publicPercent'], 'endAt': chosen['endAt'],
                      'stock': chosen['stock'],
                      'alternatives': [{'campaignId': o['campaignId'],
                                        'creatorPercent': o['creatorPercent'],
                                        'endAt': o['endAt']} for o in sorted(others, key=lambda o: str(o['campaignId']))],
                      'reasons': []})
    items.sort(key=lambda item: (item['state'] != 'chosen', item['pid']))
    counts = {}
    for item in items:
        counts[item['state']] = counts.get(item['state'], 0) + 1
    return {'counts': counts, 'items': items,
            'withAlternatives': sum(1 for item in items if item['alternatives']),
            # 恒等式：一个 PID 一条。对不上就是分组错了，宁可让调用方看见。
            'reconciled': len(items) == len(by_pid)}


def run_id(source, snapshot, rule, market='it'):
    return 'campaign-screen-' + digest([market, source, snapshot, rule])[:28]


def build(root=None, *, source='campaign', market='it', rule=None, at=None, clock=time.time):
    """Screen one channel's head snapshot. Pure: reads the snapshot, writes a run only if asked."""
    from lib.cycle_catalog import commission_rule
    root = Path(root or root_of())
    if source not in SOURCES:
        raise ValueError('campaign_screen_source_invalid')
    at = clock() if at is None else at
    # Order matters: nothing to screen without a snapshot, so that is reported first and a missing
    # policy file only shows up for a workspace that does have something to screen.
    snapshot, offers = snapshot_for(root, source, market)
    if snapshot is None:
        return {'available': False, 'market': market, 'source': source, 'reason': 'snapshot_missing'}
    try:
        rule = rule or commission_rule(root)
    except (OSError, ValueError, KeyError):
        return {'available': False, 'market': market, 'source': source, 'reason': 'policy_missing'}
    calculate = commission_calculator(rule)
    items = [evaluate(offer, calculate=calculate, at=at) for offer in offers]
    counts = {}
    reasons = {}
    for item in items:
        counts[item['state']] = counts.get(item['state'], 0) + 1
        for reason in item['reasons']:
            reasons[reason] = reasons.get(reason, 0) + 1
    by_pid = {}
    for item in items:
        if item['state'] == 'eligible':
            by_pid.setdefault(item['pid'], []).append(item['campaignId'])
    return {'available': True, 'market': market, 'source': source, 'snapshot': snapshot, 'rule': rule,
            'ruleFingerprint': digest(rule), 'runId': run_id(source, snapshot, rule, market), 'at': at,
            'offers': len(items), 'counts': counts, 'reasons': reasons,
            # 去重商品数：页面上要的是"我筛的是多少个商品"，不是 offer 条数。
            'distinctPids': len({item['pid'] for item in items}),
            'eligiblePids': len(by_pid), 'multiCampaignPids': sum(1 for v in by_pid.values() if len(v) > 1),
            'items': items,
            'pool': pool({'items': items})}


def record(root, built, *, market=None, clock=time.time):
    """Persist one screening run; the caller decides when that is worth doing."""
    if not built.get('available'):
        raise ValueError('campaign_screen_source_invalid')
    market = market or built.get('market') or 'it'
    path = db_path(root, market)
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = clock()
    with closing(sqlite3.connect(path, timeout=15)) as conn:
        conn.executescript(SCHEMA)
        with conn:
            conn.execute('DELETE FROM campaign_screen WHERE run_id=?', (built['runId'],))
            conn.executemany(
                'INSERT INTO campaign_screen(run_id,pid,campaign_id,state,reasons,creator_percent,'
                'total_percent,public_percent,stock,end_at,available,observed) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                [(built['runId'], item['pid'], item['campaignId'], item['state'],
                  json.dumps(item['reasons'], ensure_ascii=False), item['creatorPercent'],
                  item['totalPercent'], item['publicPercent'], item['stock'], item['endAt'],
                  int(item['available']), stamp) for item in built['items']])
            conn.execute(
                'INSERT INTO campaign_screen_run(run_id,source,source_snapshot,rule,rule_fingerprint,'
                'state,counts,created,updated) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET '
                'counts=excluded.counts,updated=excluded.updated',
                (built['runId'], built['source'], built['snapshot'],
                 json.dumps(built['rule'], ensure_ascii=False, sort_keys=True), built['ruleFingerprint'],
                 'recorded', json.dumps({'states': built['counts'], 'reasons': built['reasons'],
                                         'eligiblePids': built['eligiblePids'],
                                         'multiCampaignPids': built['multiCampaignPids']},
                                        ensure_ascii=False), stamp, stamp))
            pool = built.get('pool') or {'counts': {}, 'items': []}
            conn.execute('DELETE FROM campaign_pool_item WHERE run_id=?', (built['runId'],))
            conn.executemany(
                'INSERT INTO campaign_pool_item(run_id,pid,state,campaign_id,creator_percent,'
                'total_percent,public_percent,end_at,stock,alternatives,reasons,updated) '
                'VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                [(built['runId'], item['pid'], item['state'], item['campaignId'],
                  item['creatorPercent'], item['totalPercent'], item['publicPercent'], item['endAt'],
                  item['stock'], json.dumps(item['alternatives'], ensure_ascii=False),
                  json.dumps(item['reasons'], ensure_ascii=False), stamp) for item in pool['items']])
            conn.execute('INSERT INTO campaign_pool_run(run_id,source,source_snapshot,created,counts) '
                         'VALUES(?,?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET counts=excluded.counts',
                         (built['runId'], built['source'], built['snapshot'], stamp,
                          json.dumps(pool['counts'], ensure_ascii=False)))
    reconciliation = {'available': False, 'reason': 'catalog_binding_missing'}
    links = Path(root) / 'var/catalog-links.sqlite'
    if links.exists():
        targets = link_targets(root, source=built['source'], market=market)
        skipped = targets.get('skipped') or {}
        if targets.get('available') and not int(skipped.get('plan_missing') or 0) and \
                not int(skipped.get('commission_invalid') or 0):
            from lib.catalog_binding import CatalogBindings
            bindings = None
            try:
                bindings = CatalogBindings(root)
                preserve_missing = set()
                if built['source'] == 'campaign':
                    from lib.campaign_join import status as join_status
                    membership = join_status(root, market=market)
                    preserve_missing = {str(row[key]) for row in membership.get('items', [])
                        if row['state'] in ('writing', 'result_unknown')
                        for key in ('campaign_id', 'joined_campaign_id') if row.get(key)}
                reconciliation = {'available': True, **bindings.reconcile_current_offers(
                    market, built['source'], [row['offer'] for row in targets['targets']],
                    evidence_ref=built['runId'], now=stamp, preserve_missing_campaigns=preserve_missing,
                )}
            except ValueError as error:
                reconciliation = {'available': False, 'reason': str(error)}
            finally:
                if bindings is not None:
                    bindings.close()
        elif targets.get('available'):
            reconciliation = {'available': False, 'reason': 'campaign_binding_reconcile_incomplete',
                              'skipped': skipped}
        else:
            reconciliation = {'available': False, 'reason': targets.get('reason') or 'campaign_pool_missing'}
    built['bindingReconciliation'] = reconciliation
    return built


def recorded(root, source='campaign', market='it'):
    """The last recorded run for a channel, with a flag saying whether it still matches the snapshot."""
    path = db_path(root, market)
    if not path.exists():
        return None
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)) as conn:
        conn.execute('BEGIN')
        row = conn.execute('SELECT * FROM campaign_screen_run WHERE source=? ORDER BY created DESC LIMIT 1',
                           (source,)).fetchone()
        if not row:
            return None
        pool = conn.execute('SELECT counts FROM campaign_pool_run WHERE run_id=?', (row[0],)).fetchone()
    return {'runId': row[0], 'source': row[1], 'snapshot': row[2], 'ruleFingerprint': row[4],
            'counts': json.loads(row[6]), 'poolCounts': json.loads(pool[0]) if pool else None,
            'updated': row[8]}


def status(root=None, *, source='campaign', market='it', at=None):
    """What the page reads: the funnel plus whether a recorded run is still current."""
    root = Path(root or root_of())
    built = build(root, source=source, market=market, at=at)
    return built | {'recorded': recorded(root, source, market)}


def pool_products(root=None, *, source='campaign', market='it'):
    """已入池商品（PID）＋累计销量与标题，供**线索队列**排序用。

    销量取自快照里的 `sales`（平台的活动商品行自带 `product_sales`，是 `normalize` 新接出来的）。
    老快照没有这个字段时返回 ``None``——**不编 0**：调用方要能区分"销量是 0"和"没有销量数据"。
    """
    root = Path(root or root_of())
    path = db_path(root, market)
    if not path.exists():
        return {}
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)) as conn:
        conn.execute('BEGIN')
        run = conn.execute('SELECT run_id FROM campaign_pool_run WHERE source=? ORDER BY created DESC LIMIT 1',
                           (source,)).fetchone()
        if not run:
            return {}
        chosen = {str(row[0]) for row in conn.execute(
            "SELECT pid FROM campaign_pool_item WHERE run_id=? AND state='chosen'", (run[0],))}
    _, offers = snapshot_for(root, source, market)
    out = {}
    for offer in offers:
        pid = str(offer.get('pid') or '')
        if pid in chosen and pid not in out:
            raw = offer.get('sales')
            units = None
            try:
                units = int(str(raw)) if raw not in (None, '') else None
            except (TypeError, ValueError):
                units = None
            out[pid] = {'units': units, 'title': str(offer.get('title') or '')[:80]}
    return out


def link_targets(root=None, *, source='campaign', market='it', exclude=()):
    """已入池（chosen）商品的建链目标：播种行 + 判定用的 offer（佣金按**当前**规则重算）。

    池子本身已经是「一个 PID 一条」（见 `pool`），所以这里天然不会给同一个商品建两条链。
    选中的活动号就是链接要挂的活动——非全托卡片读的是 `source=1, campaign_id=<活动>`，
    与全托的账号级卡片（`source=2, campaign_id=0`）是两个不同的平台对象。

    ``exclude`` 用来排除跨渠道重叠的 PID（那种商品已有全托卡、按「全托优先」只会出一条位置）。
    """
    root = Path(root or root_of())
    path = db_path(root, market)
    if not path.exists():
        return {'available': False, 'reason': 'campaign_screen_missing', 'targets': []}
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)) as conn:
        conn.execute('BEGIN')
        run = conn.execute('SELECT run_id FROM campaign_pool_run WHERE source=? ORDER BY created DESC LIMIT 1',
                           (source,)).fetchone()
        if not run:
            return {'available': False, 'reason': 'campaign_pool_missing', 'targets': []}
        rows = conn.execute(
            "SELECT pid,campaign_id,creator_percent,total_percent,public_percent,end_at,stock "
            "FROM campaign_pool_item WHERE run_id=? AND state='chosen' ORDER BY pid", (run[0],)).fetchall()
    snapshot, offers = snapshot_for(root, source, market)
    index = {(str(o.get('pid')), str(o.get('campaignId'))): o for o in offers}
    from lib.catalog_prepare import new_offer
    try:
        policy = json.loads((root / 'config/catalog-link-policy.json').read_text())
    except (OSError, ValueError):
        return {'available': False, 'reason': 'policy_missing', 'targets': []}
    excluded = {str(p) for p in exclude}
    targets = []
    skipped = {'excluded': 0, 'plan_missing': 0, 'commission_invalid': 0}
    for pid, cid, creator, total, public, end_at, stock in rows:
        pid, cid = str(pid), str(cid)
        if pid in excluded:
            skipped['excluded'] += 1
            continue
        raw = index.get((pid, cid))
        if raw is None:
            skipped['plan_missing'] += 1
            continue
        try:
            # 复用 new_offer：佣金一定按当前规则重算，快照里旧的 creator_percent 只作展示。
            offer = new_offer({'title': raw.get('title'), 'product_id': pid, 'campaign_id': cid,
                               'managementType': raw.get('managementType'),
                               'managementEvidenceRef': raw.get('managementEvidenceRef'),
                               'stock': raw.get('stock'), 'product_rating': raw.get('rating')},
                              pid, cid, source, total=_basis(total), public=_basis(public), policy=policy)
        except (ValueError, InvalidOperation):
            skipped['commission_invalid'] += 1
            continue
        targets.append({'pid': pid, 'campaignId': cid, 'catalogSource': source, 'title': offer['title'],
                        'creatorPercent': offer['creatorPercent'], 'totalPercent': offer['totalPercent'],
                        'publicPercent': offer['publicPercent'], 'agencyPercent': offer['agencyPercent'],
                        'endAt': end_at, 'stock': stock, 'offer': offer})
    return {'available': True, 'source': source, 'runId': run[0], 'snapshot': snapshot,
            'targets': targets, 'skipped': skipped}
