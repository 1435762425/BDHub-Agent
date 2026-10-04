"""Read-only business reports from the existing ledgers; no platform or model calls."""
from __future__ import annotations

from collections import Counter
from contextlib import closing, contextmanager
from datetime import datetime, timedelta
from decimal import Decimal
from html import escape
from pathlib import Path
from types import SimpleNamespace
import json
import sqlite3
import time

from lib.catalog_binding import offer_fingerprint
from lib.cycle_stats import BEIJING, day_bounds
from lib.market_registry import load_registry
from lib.second_cycle import assess_offer

EXCLUDED = frozenset({'it', 'uk'})
FIELDS = ('fullProducts', 'campaignProducts', 'availableProducts', 'taplinkReady',
          'newA', 'newB', 'consumed', 'remaining', 'sendable', 'waiting', 'inactive',
          'sentCreators', 'sentCards', 'showcaseToday', 'receivedCreators', 'handledCreators',
          'pending', 'over24Hours', 'humanCases', 'technicalUnknown',
          'totalCreators', 'totalCards', 'totalReplied', 'totalShowcase')
TECHNICAL_CASES = frozenset({'card_result_unknown', 'conversation_business_rejected',
                            'conversation_create_unknown'})


@contextmanager
def read_db(path):
    path = Path(path)
    if not path.is_file():
        raise ValueError('ledger_missing')
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=10)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        deadline = time.monotonic() + 90
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
        yield db


def tables(db):
    return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def ratio(numerator, denominator):
    if numerator is None or denominator is None or not denominator:
        return None
    return round(100 * numerator / denominator, 2)


def _chosen_offers(db, plan, at):
    chosen = {}
    rows = db.execute('SELECT c.payload,c.observed FROM catalog_head h JOIN catalog c ON c.id=h.snapshot_id '
                      'AND c.plan_id=h.plan_id WHERE h.plan_id=?', (plan,)).fetchall()
    if not rows:
        raise ValueError('catalog_not_published')
    for row in rows:
        offers = json.loads(row['payload'])
        if not isinstance(offers, list):
            raise ValueError('catalog_payload_invalid')
        for offer in offers:
            if not isinstance(offer, dict) or not assess_offer(offer, at)['eligible']:
                continue
            pid = str(offer['pid'])
            # Same deterministic offer choice as cycle_materials.select_offers, without initializing a store.
            end = datetime.fromisoformat(str(offer['endAt']).replace('Z', '+00:00')).timestamp()
            key = (-Decimal(str(offer['creatorPercent'])), -end,
                   0 if offer.get('catalogSource') == 'selected' else 1,
                   str(offer.get('campaignId') or ''), str(offer.get('offerKey') or ''))
            if pid not in chosen or key < chosen[pid][0]:
                chosen[pid] = (key, offer)
    return [item[1] for item in chosen.values()], min(row['observed'] for row in rows)


def _inventory(root, db, plan, market, meta, at):
    offers, observed = _chosen_offers(db, plan, at)
    counts = Counter(offer.get('catalogSource') for offer in offers)
    if set(counts) - {'selected', 'campaign'}:
        raise ValueError('catalog_source_unrecognized')
    result = {'fullProducts': counts['selected'], 'campaignProducts': counts['campaign'],
              'availableProducts': len(offers), 'taplinkReady': None, 'catalogObservedAt': observed}
    path = Path(root) / 'var/catalog-links.sqlite'
    try:
        from lib.link_naming import load as load_naming
        naming = load_naming(root, market)['version']
        with read_db(path) as links:
            bindings = {(r['catalog_source'], r['pid'], r['campaign_id']): r for r in links.execute(
                'SELECT * FROM catalog_current_binding WHERE market=?', (market,))}
            ready = 0
            for offer in offers:
                binding = bindings.get((offer['catalogSource'], str(offer['pid']), str(offer['campaignId'])))
                if binding and binding['state'] == 'active' and binding['list_id'] and \
                        binding['offer_fingerprint'] == offer_fingerprint(offer) and \
                        binding['commission_rule_version'] == 'commission-1-to-2-v1' and \
                        binding['naming_rule_version'] == naming and binding['verified_at'] > 0:
                    ready += 1
            result['taplinkReady'] = ready
    except (OSError, sqlite3.Error, ValueError, KeyError):
        pass
    return result


def _leads(db, market, start, end):
    if 'lead_publication_contribution' not in tables(db):
        raise ValueError('lead_contribution_not_recorded')
    counts = dict(db.execute('SELECT kind,coalesce(sum(new_pairs),0) FROM lead_publication_contribution '
                             'WHERE market=? AND published_at>=? AND published_at<? GROUP BY kind',
                             (market, start, end)))
    return {'newA': counts.get('A', 0), 'newB': counts.get('B', 0)}


def _activity(db, plan, start, end):
    names = tables(db)
    required = {'cycle_delivery', 'cycle_delivery_part', 'inbox_event'}
    if not required <= names:
        raise ValueError('activity_schema_missing')
    cid_join = 'LEFT JOIN cycle_conversation_intent i ON i.delivery_id=d.id' if 'cycle_conversation_intent' in names else ''
    cid = "coalesce(json_extract(d.snapshot,'$.conversationId'),i.cid)" if cid_join else "json_extract(d.snapshot,'$.conversationId')"
    rows = db.execute(f'''SELECT d.id,d.oec,d.pid,d.state,{cid} cid,
        coalesce(json_extract(d.snapshot,'$.card.listId'),json_extract(d.snapshot,'$.nativeCard.list_id')) list_id,
        p.started,EXISTS(SELECT 1 FROM cycle_delivery_part t WHERE t.delivery_id=d.id AND t.kind='text'
            AND t.state='confirmed' AND t.started<?) text_confirmed
        FROM cycle_delivery d JOIN cycle_delivery_part p ON p.delivery_id=d.id {cid_join}
        WHERE d.plan_id=? AND p.kind='card' AND p.state='confirmed' AND p.started<?''', (end, plan, end)).fetchall()
    today = [r for r in rows if r['started'] >= start]
    full = [r for r in rows if r['state'] == 'confirmed' and r['text_confirmed']]
    reached = {r['oec'] for r in full}
    card_keys = {(r['oec'], str(r['list_id'])) for r in rows if r['list_id']}
    missing_ids = sum(not r['list_id'] for r in rows)
    first_card = {}
    for r in rows:
        if r['cid']:
            key = (r['oec'], r['cid'])
            first_card[key] = min(first_card.get(key, end), r['started'])
    replied = set()
    showcase = showcase_today = 0
    received = set()
    missing_times = 0
    for r in db.execute("SELECT oec,cid,kind,occurred_ms FROM inbox_event WHERE plan_id=? AND historical=0 "
                        "AND kind IN ('creatorReplies','showcaseNotifications')", (plan,)):
        if not r['occurred_ms']:
            missing_times += 1
            continue
        stamp = r['occurred_ms'] / 1000
        if stamp >= end:
            continue
        if r['kind'] == 'creatorReplies' and stamp >= start:
            received.add(r['oec'])
        if stamp <= first_card.get((r['oec'], r['cid']), end):
            continue
        if r['kind'] == 'creatorReplies' and r['oec'] in reached:
            replied.add(r['oec'])
        elif r['kind'] == 'showcaseNotifications':
            showcase += 1
            showcase_today += stamp >= start
    result = {'sentCreators': len({r['oec'] for r in full if r['started'] >= start}),
              'sentCards': len(today), 'consumed': len({(r['oec'], r['pid']) for r in today}),
              'showcaseToday': showcase_today, 'receivedCreators': len(received),
              'totalCreators': len(reached), 'totalCards': None if missing_ids else len(card_keys),
              'totalReplied': len(replied), 'totalShowcase': showcase,
              'missingCardIds': missing_ids, 'missingEventTimes': missing_times,
              'cumulativeSince': min((r['started'] for r in rows), default=None)}
    if 'service_reply' in names:
        result['handledCreators'] = db.execute("SELECT count(DISTINCT oec) FROM service_reply WHERE plan_id=? "
                                               "AND state='confirmed' AND started>=? AND started<?",
                                               (plan, start, end)).fetchone()[0]
    if 'service_case' in names:
        cases = list(db.execute("SELECT reason,count(*) n FROM service_case WHERE plan_id=? AND state='open' GROUP BY reason", (plan,)))
        result['humanCases'] = sum(r['n'] for r in cases if r['reason'] not in TECHNICAL_CASES)
    result['technicalUnknown'] = db.execute("SELECT count(*) FROM cycle_delivery WHERE plan_id=? AND state IN "
                                            "('unknown','quarantined_unknown')", (plan,)).fetchone()[0]
    return result


def _backlog(db, market, at):
    from lib.conversation_workbench import unread_items
    rows = list(unread_items(SimpleNamespace(db=db, clock=lambda: at), market))
    return {'pending': len(rows), 'over24Hours': sum(bool(r['waitingAt']) and at - r['waitingAt'] > 86400 for r in rows)}


def collect(root, report_date, *, now=None, pool_reader=None):
    """Day events stop at midnight; inventory is explicitly stamped at its actual sample time."""
    root = Path(root)
    now = float(time.time() if now is None else now)
    start, end = day_bounds(report_date)
    if end > now:
        raise ValueError('report_day_not_finished')
    registry = load_registry(root)['markets']
    if pool_reader is None:
        from lib.lead_pool import pool as pool_reader
    markets, planned = [], []
    for market, meta in registry.items():
        if not meta['enabled'] or market in EXCLUDED:
            continue
        if meta['runtimeState'] == 'planned':
            planned.append({'market': market, 'label': meta['label']})
            continue
        row = dict.fromkeys(FIELDS)
        row.update(market=market, label=meta['label'], fullSupported=meta['capabilities']['fullManagedCatalog'], errors={})
        try:
            with read_db(root / 'var/second-cycle.sqlite') as db:
                plans = list(db.execute("SELECT id FROM plan WHERE market=? AND institution='bjn-local-research'", (market,)))
                if len(plans) != 1:
                    raise ValueError('market_plan_missing')
                plan = plans[0][0]
                for name, reader in (
                    ('inventory', lambda: _inventory(root, db, plan, market, meta, now)),
                    ('leads', lambda: _leads(db, market, start, end)),
                    ('activity', lambda: _activity(db, plan, start, end)),
                    ('backlog', lambda: _backlog(db, market, now)),
                ):
                    try:
                        row.update(reader())
                    except (sqlite3.Error, ValueError, KeyError, TypeError, OSError):
                        row['errors'][name] = '未接入或读取失败'
        except (sqlite3.Error, ValueError, OSError):
            row['errors']['ledger'] = '台账未接入或读取失败'
        try:
            value = pool_reader(root, market=market, now=now, limit=0)
            if not value.get('available'):
                raise ValueError('pool_unavailable')
            business = value['business']
            row.update(sendable=business['sendable'], waiting=business['waiting'],
                       remaining=business['sendable'] + business['waiting'], inactive=business['inactive'])
        except (sqlite3.Error, ValueError, KeyError, TypeError, OSError):
            row['errors']['pool'] = '发送池未接入或读取失败'
        row['taplinkRate'] = ratio(row['taplinkReady'], row['availableProducts'])
        row['replyRate'] = ratio(row['totalReplied'], row['totalCreators'])
        row['showcaseRate'] = ratio(row['totalShowcase'], row['totalCards'])
        markets.append(row)
    summary = {key: sum(r[key] for r in markets) if markets and all(r[key] is not None for r in markets) else None for key in FIELDS}
    summary.update(taplinkRate=ratio(summary['taplinkReady'], summary['availableProducts']),
                   replyRate=ratio(summary['totalReplied'], summary['totalCreators']),
                   showcaseRate=ratio(summary['totalShowcase'], summary['totalCards']))
    return {'schema': 'bdhub.business-daily.v1', 'date': report_date, 'timezone': 'Asia/Shanghai',
            'generatedAt': now, 'inventorySampledAt': now, 'eventCutoff': end,
            'markets': markets, 'plannedMarkets': planned, 'summary': summary,
            'readOnly': True, 'platformWrites': 0}


def stamp(at):
    return datetime.fromtimestamp(at, BEIJING).strftime('%Y-%m-%d %H:%M:%S')


def fmt(value, percent=False):
    if value is None:
        return '待核实'
    return f'{value:.1f}%' if percent else f'{value:,}'


def sections(report):
    """One table description shared by email HTML and plain text."""
    rows = report['markets']
    yield '商品与 TapLink', ['市场', '全托', 'Campaign', '可用商品', 'TapLink 就绪', '准备率'], [
        [r['label'], '—' if r['fullSupported'] is False else fmt(r['fullProducts']), fmt(r['campaignProducts']),
         fmt(r['availableProducts']), fmt(r['taplinkReady']), fmt(r['taplinkRate'], True)] for r in rows]
    yield '当日新线索与剩余线索', ['市场', '新增 A', '新增 B', '发送消耗', '剩余线索', '可发送', '等待中'], [
        [r['label'], *(fmt(r[k]) for k in ('newA', 'newB', 'consumed', 'remaining', 'sendable', 'waiting'))] for r in rows]
    yield '当日发送与橱窗', ['市场', '完整发送达人', '确认商品卡', '新橱窗通知', '未知／隔离投递'], [
        [r['label'], *(fmt(r[k]) for k in ('sentCreators', 'sentCards', 'showcaseToday', 'technicalUnknown'))] for r in rows]
    yield '累计转化表现', ['市场', '发送达人', '商品卡', '回复达人', '回复率', '橱窗通知', '通知／卡比例'], [
        [r['label'], fmt(r['totalCreators']), fmt(r['totalCards']), fmt(r['totalReplied']), fmt(r['replyRate'], True),
         fmt(r['totalShowcase']), fmt(r['showcaseRate'], True)] for r in rows]
    yield '回复服务与待办', ['市场', '当日回复达人', '已回复达人', '当前未处理', '等待 >24h', '商业待决策'], [
        [r['label'], *(fmt(r[k]) for k in ('receivedCreators', 'handledCreators', 'pending', 'over24Hours', 'humanCases'))] for r in rows]
    s = report['summary']
    yield '汇总', ['指标', '结果'], [
        ['可用商品', f"{fmt(s['availableProducts'])}（全托 {fmt(s['fullProducts'])}，Campaign {fmt(s['campaignProducts'])}）"],
        ['TapLink 就绪／准备率', f"{fmt(s['taplinkReady'])}／{fmt(s['taplinkRate'], True)}"],
        ['当日新线索 A／B', f"{fmt(s['newA'])}／{fmt(s['newB'])}"],
        ['当日发送消耗线索', fmt(s['consumed'])],
        ['剩余／可发送／等待中', f"{fmt(s['remaining'])}／{fmt(s['sendable'])}／{fmt(s['waiting'])}"],
        ['当日完整发送达人／商品卡', f"{fmt(s['sentCreators'])}／{fmt(s['sentCards'])}"],
        ['当日新增橱窗通知', fmt(s['showcaseToday'])],
        ['累计发送达人／商品卡', f"{fmt(s['totalCreators'])}／{fmt(s['totalCards'])}"],
        ['累计回复达人／回复率', f"{fmt(s['totalReplied'])}／{fmt(s['replyRate'], True)}"],
        ['累计橱窗通知／通知卡比例', f"{fmt(s['totalShowcase'])}／{fmt(s['showcaseRate'], True)}"],
        ['当日已回复达人', fmt(s['handledCreators'])],
        ['当前未处理／等待超过 24 小时', f"{fmt(s['pending'])}／{fmt(s['over24Hours'])}"],
        ['商业待决策', fmt(s['humanCases'])],
    ]


def notes(report):
    result = [f"事件统计：{report['date']} 北京时间 00:00–24:00；累计截至该日末。",
              f"库存与待办采样：{stamp(report['inventorySampledAt'])}。该时间为实际读取时间，不倒推成历史日末库存。",
              '可用商品为当前已发布货盘中按既有规则合格的市场＋PID，按最优当前方案归类；有效 TapLink 必须精确匹配方案及命名、分佣版本。',
              '新线索来自 Kalodata 发布贡献台账；发送消耗按当天确认卡的达人×商品去重，不包含未记录的失效退出。剩余线索＝发送池可发送＋等待，不含暂不参与。',
              '当日完整发送须卡文均确认；累计商品卡按市场内达人×listId 去重，累计达人在市场内去重。缺失时间或卡标识单列，不补造。',
              '累计回复只计确认发卡后同达人、同会话的新回复；历史补录不计。橱窗按同范围的新通知计数，通知缺少商品卡 ID，通知／卡比例是观察口径，不是逐卡验证采用率。',
              '已回复达人只计平台回读确认的 AI／人工回复；无需回复与人工接管不计为已回复。汇总比例用合计分子÷合计分母。']
    if report['plannedMarkets']:
        result.append('未接入：' + '、'.join(r['label'] for r in report['plannedMarkets']) + '；不计经营汇总。英国、意大利不纳入。')
    for r in report['markets']:
        if r.get('catalogObservedAt'):
            result.append(r['label'] + '货盘最早来源更新时间：' + stamp(r['catalogObservedAt']))
        if r['errors']:
            result.append(r['label'] + '数据缺口：' + '、'.join(r['errors']))
        if r.get('missingEventTimes') or r.get('missingCardIds'):
            result.append(f"{r['label']}：{r.get('missingEventTimes', 0)} 条互动缺时间；{r.get('missingCardIds', 0)} 张确认卡缺标识。")
    return result


def render(report):
    s = report['summary']
    subject = f"[BDHub 日报] {report['date']}｜可用商品 {fmt(s['availableProducts'])} · TapLink {fmt(s['taplinkRate'], True)} · 发送 {fmt(s['sentCreators'])} 人"
    all_notes = notes(report)
    text = [subject, '', *all_notes[:2], '']
    body = []
    for title, headers, rows in sections(report):
        if title == '汇总':
            text += ['统计口径与数据状态', *all_notes[2:], '']
            body.append('<div style="margin-top:22px;padding:12px;background:#f7f9f4">' +
                        ''.join('<p style="margin:6px 0;font-size:11px;line-height:1.8;color:#6c756f">' + escape(n) + '</p>' for n in all_notes[2:]) + '</div>')
        text += [title, '| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join('---' for _ in headers) + ' |']
        text += ['| ' + ' | '.join(row) + ' |' for row in rows] + ['']
        table = '<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;font-size:12px;line-height:1.7">'
        table += '<tr>' + ''.join('<th style="padding:9px 6px;text-align:left;background:#edf4ed;border-bottom:1px solid #cddbcf">' + escape(h) + '</th>' for h in headers) + '</tr>'
        for row in rows:
            table += '<tr>' + ''.join('<td style="padding:10px 6px;border-bottom:1px solid #e2e9e1">' + escape(cell) + '</td>' for cell in row) + '</tr>'
        body.append('<h2 style="margin:25px 0 10px;font-size:18px;color:#173b2c">' + escape(title) + '</h2>' + table + '</table>')
    note_html = ''.join('<p style="margin:6px 0;font-size:11px;line-height:1.8;color:#6c756f">' + escape(n) + '</p>' for n in all_notes[:2])
    html = '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>' + escape(subject) + '</title></head><body style="margin:0;padding:18px 8px;background:#edf1ec;font-family:Arial,\'PingFang SC\',\'Microsoft YaHei\',sans-serif;color:#20322a"><table role="presentation" width="100%"><tr><td align="center"><table role="presentation" width="760" style="width:100%;max-width:760px;background:white"><tr><td style="padding:25px 20px;background:#173b2c;color:white"><h1 style="margin:0;font-size:28px">经营日报</h1><p style="margin:10px 0 0">' + escape(report['date']) + ' · 按市场明细与汇总</p></td></tr><tr><td style="padding:0 16px 24px">'
    html += '<div style="margin-top:18px;padding:12px;background:#f7f9f4">' + note_html + '</div>' + ''.join(body) + '</td></tr></table></td></tr></table></body></html>'
    return subject, '\n'.join(text), html
