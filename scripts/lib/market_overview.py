"""Read-only operational facts. No store initialization, platform calls or state projection."""
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import json
import sqlite3
import time

from lib.cycle_stats import daily
from lib.lead_pool import pool
from lib.market_registry import require_operational

IDENTITY_STATES = ('resolved', 'notFound', 'queued', 'blocked', 'isolated', 'noRecord', 'conflict')
TECHNICAL_CASES = ('card_result_unknown', 'conversation_business_rejected', 'conversation_create_unknown')


@contextmanager
def read_db(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=2)
    try:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        deadline = time.monotonic() + 8
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
        yield db
    finally:
        db.close()


def tables(db):
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def require_tables(db, *names):
    if not set(names) <= tables(db):
        raise ValueError('schema_unavailable')


def metric(key, label, value, unit='项', note=''):
    if value is not None and (type(value) is not int or value < 0):
        raise ValueError('count_invalid')
    return {'key': key, 'label': label, 'value': value, 'unit': unit, 'note': note}


def panel(key, title, source, reader, observed_at=None):
    try:
        result = reader()
        return {'id': key, 'title': title, 'available': True, 'reason': None,
                'source': source, 'observedAt': observed_at, **result}
    except (sqlite3.Error, OSError, ValueError, KeyError, TypeError):
        return {'id': key, 'title': title, 'available': False, 'reason': '数据缺失或读取失败',
                'source': source, 'observedAt': None, 'metrics': []}


def identity_counts(root, db, plan, market):
    require_tables(db, 'lead_query_head', 'lead_query_selection', 'source_edge_index',
                   'cycle_identity_resolution', 'cycle_identity_outcome')
    if not db.execute('SELECT 1 FROM lead_query_head WHERE plan_id=? LIMIT 1', (plan,)).fetchone():
        raise ValueError('head_missing')
    rows = db.execute('''SELECT x.source_handle handle,x.source_id,o.status
        FROM lead_query_head h CROSS JOIN lead_query_selection s CROSS JOIN source_edge_index x
        LEFT JOIN cycle_identity_outcome o ON o.plan_id=x.plan_id AND o.source_id=x.source_id
        WHERE h.plan_id=? AND s.query_id=h.query_id AND x.plan_id=h.plan_id AND x.source_id=s.source_id''', (plan,)).fetchall()
    grouped = defaultdict(list)
    for row in rows:
        if not row['handle']:
            raise ValueError('identity_scope_invalid')
        grouped[row['handle']].append(row['status'])
    owners = {r['handle']: r['owners'] for r in db.execute('''SELECT x.source_handle handle,
        count(DISTINCT r.creator_id) owners FROM cycle_identity_resolution r
        JOIN source_edge_index x ON x.plan_id=r.plan_id AND x.source_id=r.source_id
        WHERE r.plan_id=? AND x.source_kind='kalodata_http' GROUP BY x.source_handle''', (plan,))}
    judgments = defaultdict(set)
    discovery = Path(root) / 'var/creator-discovery.sqlite'
    discovery_read = False
    if discovery.exists():
        with read_db(discovery) as found:
            require_tables(found, 'discovery_item', 'discovery_batch')
            for row in found.execute('''SELECT i.handle,i.status FROM discovery_item i
                JOIN discovery_batch b ON b.id=i.batch_id WHERE b.market=?
                AND i.handle IN (SELECT value FROM json_each(?))
                AND i.status IN ('completed','unresolved','queued','running','blocked')''',
                (market, json.dumps(list(grouped)))):
                judgments[row['handle']].add(row['status'])
            discovery_read = True
    from lib.identity_retry import snapshot
    isolated=snapshot(root,market)['isolated']
    for handle in isolated:judgments[handle].add('technical_isolated')
    row_counts = dict.fromkeys(IDENTITY_STATES, 0)
    handle_counts = dict.fromkeys(IDENTITY_STATES, 0)
    for handle, outcomes in grouped.items():
        states = set(outcomes) | judgments[handle]
        owner_count = owners.get(handle, 0)
        if owner_count > 1:
            state = 'conflict'
        elif owner_count == 1:
            state = 'resolved'
        elif 'completed' in states:
            state = 'conflict'  # A terminal status without a usable binding is not a usable identity.
        elif 'unresolved' in states:
            state = 'notFound'
        elif states & {'queued', 'running'}:
            state = 'queued'
        elif 'technical_isolated' in states:
            state = 'isolated'
        elif 'blocked' in states:
            state = 'blocked'
        elif states - {None}:
            state = 'conflict'
        else:
            state = 'noRecord'
        row_counts[state] += len(outcomes)
        handle_counts[state] += 1
    if sum(row_counts.values()) != len(rows) or sum(handle_counts.values()) != len(grouped):
        raise ValueError('identity_counts_unreconciled')
    labels = {'resolved': '已解析', 'notFound': '已判定搜索不到', 'queued': '解析排队/处理中',
              'blocked': '技术退避', 'isolated':'技术隔离（停止重试）', 'noRecord': '未有解析记录', 'conflict': '绑定或状态待核对'}
    return {'metrics': [metric('rows', '当前 A 类线索', len(rows), '条'),
                        metric('handles', '去重账号名', len(grouped), '个')],
            'rows': row_counts, 'handles': handle_counts, 'labels': labels,
            'discoveryRead': discovery_read,
            'note': '按当前 A 类查询范围计数；同一账号名可关联多个 PID。无记录不等于已证明从未请求；已判定搜索不到与技术失败分开。'}


def inventory(root, db, plan, market):
    require_tables(db, 'catalog', 'catalog_head')
    heads = db.execute('''SELECT c.id,c.observed,c.payload FROM catalog_head h JOIN catalog c
        ON c.id=h.snapshot_id AND c.plan_id=h.plan_id WHERE h.plan_id=?''', (plan,)).fetchall()
    if not heads:
        raise ValueError('catalog_head_missing')
    pids = set()
    for row in heads:
        offers = json.loads(row['payload'])
        if not isinstance(offers, list):
            raise ValueError('catalog_payload_invalid')
        pids.update(str(offer['pid']) for offer in offers)
    links = None
    path = Path(root) / 'var/catalog-links.sqlite'
    if path.exists():
        try:
            with read_db(path) as cards:
                require_tables(cards, 'catalog_current_binding')
                links = cards.execute("SELECT count(*) FROM catalog_current_binding WHERE market=? AND state='active'", (market,)).fetchone()[0]
        except (sqlite3.Error, ValueError):
            pass
    return {'observedAt': min(r['observed'] for r in heads), 'headIds': [r['id'] for r in heads],
            'metrics': [metric('catalogPids', '当前货盘商品', len(pids), 'PID', '最新发布货盘的去重 PID，含 Campaign/全托；不等于历史累计候选。'),
                        metric('activeBindings', '当前有效链接绑定', links, '个', '已记为有效的商品链接；不代表此刻重新经过平台核验。'),
                        metric('cumulativePids', '累计合格候选', None, 'PID', '增量候选台账尚未实现，不以当前货盘替代历史累计。')]}


def video_counts(root, db, market):
    require_tables(db, 'video_lead_current')
    columns = {r[1] for r in db.execute('PRAGMA table_info(video_lead_current)')}
    # Legacy table belongs to IT. This is storage compatibility, not cross-market fallback.
    if 'market' not in columns and market != 'it':
        raise ValueError('video_market_not_implemented')
    rows = list(db.execute('SELECT handle FROM video_lead_current' + (' WHERE market=?' if 'market' in columns else ''),
                           (market,) if 'market' in columns else ()))
    with read_db(Path(root) / 'var/creator-identities.sqlite') as ids:
        require_tables(ids, 'creator_identity')
        owners = {str(r[0]).lower() for r in ids.execute('''SELECT current_handle FROM creator_identity
            WHERE market=? AND handle_conflict=0 AND current_handle IS NOT NULL''', (market,))}
    known = sum(str(r['handle']).lower() in owners for r in rows)
    # An unmatched author is either already judged "not found" by the OECID search (final, not
    # re-queried) or still waiting for resolution; the two were once shown together as "待匹配".
    not_found = set()
    if {'source_edge_index', 'cycle_identity_outcome', 'plan'} <= tables(db):
        not_found = {str(r[0]).lower() for r in db.execute(
            "SELECT DISTINCT s.source_handle FROM source_edge_index s "
            "JOIN plan p ON p.id=s.plan_id AND p.market=? "
            "JOIN cycle_identity_outcome o ON o.plan_id=s.plan_id AND o.source_id=s.source_id "
            "WHERE s.source_kind='kalodata_video' AND o.status='unresolved'", (market,))}
    unmatched = [str(r['handle']).lower() for r in rows if str(r['handle']).lower() not in owners]
    judged = sum(handle in not_found for handle in unmatched)
    return {'metrics': [metric('rows', '当前 B 类投影', len(rows), '条'),
                        metric('known', '已匹配作者', known, '条'),
                        metric('notFound', '已判定搜索不到', judged, '条', 'OECID 已查过、未找到的作者；按规则不再自动重查，不会进入发送池。'),
                        metric('unknown', '待解析作者', len(unmatched) - judged, '条', '尚未得到 OECID 结果，排队等身份阶段处理。')],
            'note': '计当前视频投影行，不等于去重达人或可发位置；未接通市场显示不可用。'}


def pool_counts(root, market, now):
    value = pool(root, market=market, now=now, limit=0)
    if not value.get('available'):
        raise ValueError('pool_unavailable')
    counts = value['counts']; layers = value['layers']
    return {'metrics': [metric('ready', '当前可发达人', counts['readyCreators'], '位'),
                        metric('queued', '同达人候选排队', layers['queued'], '位置'),
                        metric('cooling', '冷却等待', layers['cooling'], '位置'),
                        metric('awaitingReply', '会话事项等待', layers['awaiting_reply'], '位置'),
                        metric('inactive', '商品暂不可用', layers['product_inactive'], '位置')],
            'note': '复用当前发送池资格投影；一个达人可有多个等待位置，不与身份排队混算。'}


def handling(db, plan, market, now):
    require_tables(db, 'cycle_delivery', 'service_reply', 'service_case')
    delivery = Counter({r[0]: r[1] for r in db.execute('SELECT state,count(*) FROM cycle_delivery WHERE plan_id=? GROUP BY state', (plan,))})
    replies = db.execute("SELECT count(*) FROM service_reply WHERE plan_id=? AND state IN ('inflight','accepted','unknown')", (plan,)).fetchone()[0]
    isolated_replies = db.execute("SELECT count(*) FROM service_reply WHERE plan_id=? AND state='isolated'", (plan,)).fetchone()[0]
    cases = list(db.execute("SELECT reason,count(*) n FROM service_case WHERE plan_id=? AND state='open' GROUP BY reason", (plan,)))
    technical = sum(r['n'] for r in cases if r['reason'] in TECHNICAL_CASES)
    business = sum(r['n'] for r in cases if r['reason'] not in TECHNICAL_CASES)
    accounts = None
    if 'account_maintenance_intent' in tables(db):
        # Same rule as the account queue: a needs_human intent is settled once a newer one exists for that account.
        accounts = db.execute("""SELECT count(*) FROM account_maintenance_intent i WHERE i.market=? AND i.state='needs_human'
            AND NOT EXISTS (SELECT 1 FROM account_maintenance_intent newer WHERE newer.market=i.market
            AND newer.account=i.account AND newer.created_at>i.created_at)""", (market,)).fetchone()[0]
    return {'metrics': [metric('deliveryUnknown', '投递结果未知', delivery['unknown'], '次'),
                        metric('quarantined', '已技术隔离投递', delivery['quarantined_unknown'], '次'),
                        metric('replyUnknown', '回复在途/待核验', replies, '条'),
                        metric('replyIsolated', '回复核验耗尽已隔离', isolated_replies, '条', '仅暂停该达人的自动回复与主动推品；保留真实未知，不补发。'),
                        metric('legacyTechnicalCases', '历史技术人工案件', technical, '件', '现有人工锁仍保留；本页只分类展示，不结案、不解除阻断。'),
                        metric('humanCases', '业务及其他人工事项', business, '件'),
                        metric('accountNeedsHuman', '账号需人工处理', accounts, '项')],
            'note': '按当前真实状态分类。技术隔离与原业务人工事项分列。'}


def inbox(db, market, now):
    from lib.conversation_workbench import unread_backlog
    require_tables(db, 'inbox_pending', 'relationship', 'inbound_turn')
    value = unread_backlog(SimpleNamespace(db=db, clock=lambda: now), market)
    return {'metrics': [metric('unread', '未处理达人来信', value['unread'], '会话'),
                        metric('oldestMinutes', '最久等待', int(max(0, now-value['oldestAt']) / 60) if value['oldestAt'] else None, '分钟')],
            'note': '与会话页未回口径一致，排除机构已经回复的旧来信。'}


def waits(db, plan, market):
    result = []
    if 'continuous_send_runtime' in tables(db):
        row = db.execute('SELECT state,seen_at FROM continuous_send_runtime WHERE plan_id=?', (plan,)).fetchone()
        if row and row['state'] not in ('sending', 'completed'):
            labels = {'waiting_window': '等待发送窗口', 'waiting_capacity': '等待平台额度',
                      'waiting_pool': '等待可发候选', 'waiting_reconciliation': '等待原投递核验',
                      'stopped': '发送已停止', 'off': '发送未启用', 'paused': '发送暂停'}
            result.append({'id': 'sender', 'label': labels.get(row['state'], '发送状态待核对'),
                           'state': row['state'], 'observedAt': row['seen_at']})
    if {'workflow_run', 'workflow_stage_run'} <= tables(db):
        row = db.execute('SELECT run_id FROM workflow_run WHERE market=? ORDER BY started_at DESC,run_id DESC LIMIT 1', (market,)).fetchone()
        if row:
            labels = {'catalog': '货盘', 'taplink_prepare': '链接准备', 'kalodata': '达人线索', 'oecid': '身份解析', 'send_pool': '发送池', 'taplink_clean': '链接维护'}
            for stage in db.execute("SELECT stage,state,started_at FROM workflow_stage_run WHERE run_id=? AND state NOT IN ('completed','skipped') ORDER BY position", (row[0],)):
                result.append({'id': stage['stage'], 'label': labels.get(stage['stage'], '工作流阶段'),
                               'state': stage['state'], 'observedAt': stage['started_at']})
    return result


def overview(root, market, *, now=None):
    root = Path(root); now = float(time.time() if now is None else now)
    require_operational(root, market)
    result = {'schemaVersion': 'bdhub.market-overview.v1', 'market': market, 'checkedAt': now,
              'available': False, 'planState': None, 'panels': [], 'waits': [],
              'activity': {'available': False, 'days': [], 'totals': {}},
              'readOnly': True, 'platformWrites': 0, 'realSends': 0}
    path = root / 'var/second-cycle.sqlite'
    if not path.exists():
        return result
    try:
        with read_db(path) as db:
            require_tables(db, 'plan')
            plans = db.execute("SELECT id,state FROM plan WHERE institution='bjn-local-research' AND market=?", (market,)).fetchall()
            if len(plans) != 1:
                return result
            plan = plans[0]['id']; result.update(available=True, planState=plans[0]['state'])
            readers = [
                ('inventory', '当前供给', 'catalog_head / catalog_current_binding', lambda: inventory(root, db, plan, market)),
                ('identity', 'A 类身份分布', 'lead_query_head / cycle_identity_outcome / discovery_item', lambda: identity_counts(root, db, plan, market)),
                ('video', 'B 类作者', 'video_lead_current / creator_identity', lambda: video_counts(root, db, market)),
                ('pool', '发送池', 'lead_pool 当前资格投影', lambda: pool_counts(root, market, now)),
                ('handling', '技术状态与人工事项', 'cycle_delivery / service_reply / service_case / account_maintenance_intent', lambda: handling(db, plan, market, now)),
                ('inbox', '来信等待', 'conversation_workbench.unread_backlog', lambda: inbox(db, market, now)),
            ]
            result['panels'] = [panel(key, title, source, reader, now) for key, title, source, reader in readers]
            try:
                result['waits'] = waits(db, plan, market)
            except (sqlite3.Error, ValueError):
                result['waitsUnavailable'] = True
        try:
            value = daily(root, market=market, count=7, now=now)
            result['activity'] = {key: value[key] for key in ('available', 'days', 'totals')}
        except (sqlite3.Error, ValueError, OSError):
            pass
    except (sqlite3.Error, OSError, ValueError):
        result.update(available=False, panels=[], waits=[])
    return result
