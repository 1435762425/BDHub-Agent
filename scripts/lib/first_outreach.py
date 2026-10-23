"""Brazil sample outreach policy and local candidate projection.

The shared relationship and cycle_delivery tables remain the only relationship
and send ledgers. Preparing matches never authorizes an external operation.
"""
from __future__ import annotations

from contextlib import closing
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
import sqlite3
import uuid

from lib.second_cycle import CycleError, CycleStore, digest, encoded, epoch
from lib.outreach_policy import MARKETING_COOLDOWN_SECONDS, last_contact_by_creator, marketing_isolated

MODE = 'market-first-v1'
PAGE_SIZE = 500
SCHEMA = '''
CREATE TABLE IF NOT EXISTS first_outreach_control(
 plan_id TEXT PRIMARY KEY,mode TEXT NOT NULL DEFAULT 'stopped',approvals_enabled INTEGER NOT NULL DEFAULT 0,
 revision INTEGER NOT NULL DEFAULT 0,updated_at REAL NOT NULL DEFAULT 0,
 start_request TEXT,start_revision INTEGER,canary_delivery TEXT);
CREATE TABLE IF NOT EXISTS first_outreach_request(
 request_id TEXT PRIMARY KEY,plan_id TEXT NOT NULL,payload TEXT NOT NULL,result_revision INTEGER NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS first_outreach_generation(
 id TEXT PRIMARY KEY,plan_id TEXT NOT NULL,created REAL NOT NULL,completed REAL,state TEXT NOT NULL,
 catalog_fingerprint TEXT NOT NULL,products INTEGER NOT NULL DEFAULT 0,scanned INTEGER NOT NULL DEFAULT 0,
 matched INTEGER NOT NULL DEFAULT 0,waiting TEXT NOT NULL DEFAULT '{}',last_oec TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS first_outreach_head(plan_id TEXT PRIMARY KEY,generation_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS first_outreach_candidate(
 generation_id TEXT NOT NULL,creator_id TEXT NOT NULL,oec TEXT NOT NULL,pid TEXT NOT NULL,
 offer_key TEXT NOT NULL,gmv TEXT,payload TEXT NOT NULL,gmv_window REAL,gmv_key TEXT NOT NULL DEFAULT '',
 content_gmv_key TEXT NOT NULL DEFAULT '',
 PRIMARY KEY(generation_id,creator_id));
'''


def ensure_schema(store):
    store.db.executescript(SCHEMA)
    if 'content_gmv_key' not in {row['name'] for row in store.db.execute('PRAGMA table_info(first_outreach_candidate)')}:
        store.db.execute("ALTER TABLE first_outreach_candidate ADD COLUMN content_gmv_key TEXT NOT NULL DEFAULT ''")
    store.db.execute('CREATE INDEX IF NOT EXISTS first_outreach_rank_v2 ON first_outreach_candidate(generation_id,gmv_window DESC,content_gmv_key DESC,gmv_key DESC,creator_id)')


def _table(store, name):
    return bool(store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def _market(market):
    if market != 'br':
        raise CycleError('first_outreach_market_unsupported')


def _plan(store, market):
    _market(market)
    row = store.db.execute("SELECT id FROM plan WHERE institution='bjn-local-research' AND market=?", (market,)).fetchone()
    if not row:
        raise CycleError('plan_missing')
    return row[0]


def control(store, market='br'):
    plan = _plan(store, market)
    row = store.db.execute('SELECT * FROM first_outreach_control WHERE plan_id=?', (plan,)).fetchone() if _table(store, 'first_outreach_control') else None
    return {'planId': plan, 'mode': row['mode'] if row else 'stopped',
            'approvalsEnabled': bool(row['approvals_enabled']) if row else False,
            'revision': row['revision'] if row else 0, 'updatedAt': row['updated_at'] if row else 0,
            'firstStart': {'requestId': row['start_request'], 'revision': row['start_revision']} if row and row['start_request'] else None,
            'canaryDeliveryId': row['canary_delivery'] if row else None}


def _number(value):
    if isinstance(value, dict):
        value = value.get('decimal')
    if value is None or isinstance(value, bool):
        return None
    try:
        text = str(value)
        if len(text) > 100:
            return None
        result = Decimal(text)
        return result if result.is_finite() and result >= 0 and abs(result.as_tuple().exponent) <= 60 else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _field(payload, name):
    field = payload.get('fields', {}).get(name, {})
    return field.get('value') if isinstance(field, dict) and field.get('status') in {'value', 'zero'} else None


def main_category(payload):
    """Only the unique strongest actual industry group is a primary category."""
    groups = _field(payload, 'industry_groups')
    if not isinstance(groups, list) or not groups:
        return None
    parsed = []
    for group in groups:
        if not isinstance(group, dict):
            return None
        weight = _number(group.get('weight'))
        if weight is None:
            return None
        parsed.append((weight, group))
    maximum = max(weight for weight, _ in parsed)
    winners = [group for weight, group in parsed if weight == maximum]
    if maximum <= 0 or len(winners) != 1 or str(winners[0].get('id') or '') in {'', '-1', '0'}:
        return None
    return {'id': str(winners[0]['id']), 'name': winners[0].get('label') or str(winners[0]['id'])}


def assess_first_offer(offer, now):
    reasons = []
    if not isinstance(offer, dict):
        return {'eligible': False, 'reasons': ['sample_offer_missing'], 'executionAllowed': False}
    if offer.get('executionHold'):
        reasons.append('current_offer_on_hold')
    if offer.get('available') is not True:
        reasons.append('product_unavailable_or_unknown')
    try:
        if epoch(offer.get('endAt')) <= now:
            reasons.append('campaign_expired')
    except (ValueError, TypeError, AttributeError, OverflowError):
        reasons.append('campaign_expiry_unknown')
    for key in ('pid', 'offerKey', 'campaignId', 'categoryId'):
        if not isinstance(offer.get(key), str) or not offer[key] or offer[key] in {'0', '-1'}:
            reasons.append('missing_' + key)
    sample = offer.get('sample')
    if not isinstance(sample, dict):
        sample = {}
    if sample.get('supported') is not True:
        reasons.append('free_sample_unavailable_or_unknown')
    if type(sample.get('quota')) is not int or sample['quota'] <= 0:
        reasons.append('sample_quota_unavailable_or_unknown')
    try:
        if not 0 < epoch(sample.get('observedAt')) <= now + 60:
            reasons.append('sample_observation_invalid')
    except (ValueError, TypeError, AttributeError, OverflowError):
        reasons.append('sample_observation_unknown')
    if not isinstance(sample.get('evidenceRef'), str) or not sample['evidenceRef'].strip():
        reasons.append('sample_evidence_missing')
    return {'eligible': not reasons, 'reasons': reasons, 'executionAllowed': False}


def _identity_connection(root):
    connection = sqlite3.connect((Path(root) / 'var/creator-identities.sqlite').resolve().as_uri() + '?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    return connection


_LATEST = """SELECT * FROM identity_observation WHERE market=? AND oec_id=?
 AND kind='profile' AND outcome='observed' AND json_valid(payload_json)
 AND json_type(payload_json,'$.fields')='object'
 ORDER BY observed_us DESC,CASE WHEN evidence_ref LIKE '%:profile' THEN 2
 WHEN evidence_ref LIKE '%:find-profile' THEN 1 ELSE 0 END DESC,rowid DESC LIMIT 1"""


def _profile(connection, market, oec):
    person = connection.execute('SELECT * FROM creator_identity WHERE market=? AND oec_id=?', (market, str(oec))).fetchone()
    if not person or person['handle_conflict'] or not person['current_handle']:
        return None
    row = connection.execute(_LATEST, (market, str(oec))).fetchone()
    if not row or row['creator_id'] != person['creator_id']:
        return None
    return {'creatorId': person['creator_id'], 'oecId': str(oec), 'handle': person['current_handle'],
            'observedAt': row['observed_us'] / 1_000_000, 'evidenceRef': row['evidence_ref'],
            'fingerprint': row['fingerprint'], 'payload': json.loads(row['payload_json'])}


def latest_profile(root, market, oec):
    with closing(_identity_connection(root)) as connection:
        return _profile(connection, market, oec)


def _catalog_fingerprint(store, plan):
    return digest({'policy': 'first-category-content-v2',
                   'heads': [list(row) for row in store.db.execute('SELECT source,snapshot_id FROM catalog_head WHERE plan_id=? ORDER BY source', (plan,))],
                   'offers': sorted((offer['offerKey'], digest(offer)) for _, offer in _offers(store, plan))})


def _offers(store, plan):
    try:
        from lib.first_catalog import offers
    except ModuleNotFoundError as error:
        if error.name != 'lib.first_catalog':
            raise
        return store._offers(plan)
    return offers(store, plan)


def _amount(profile, field):
    """Amounts are comparable only within their actual BRL performance window."""
    raw = _field(profile['payload'], field)
    amount = _number(raw)
    symbol = raw.get('rawSymbol') if isinstance(raw, dict) else None
    currency = raw.get('currency') if isinstance(raw, dict) else None
    end = _field(profile['payload'], 'sales_performance_end_time')
    if end is None:
        candidate = profile['payload'].get('profileSource', {}).get('salesPerformanceEnd', {})
        end = candidate.get('value') if candidate.get('status') == 'value' else None
    try:
        window = epoch(end)
    except (ValueError, TypeError, AttributeError, OverflowError):
        window = None
    if amount is None or currency not in (None, 'BRL') or (currency != 'BRL' and symbol != 'R$') or window is None:
        return None, None, ''
    text = format(amount, 'f')
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    integer = text.split('.')[0]
    return text, window, str(len(integer)).zfill(3) + ':' + text


def _gmv(profile):
    return _amount(profile, 'med_gmv_revenue')


def _performance(profile):
    total, total_window, total_key = _gmv(profile)
    video, video_window, video_key = _amount(profile, 'video_gmv')
    live, live_window, live_key = _amount(profile, 'live_gmv')
    verified = [(Decimal(amount), amount, window, key) for amount, window, key in
                ((video, video_window, video_key), (live, live_window, live_key)) if amount is not None]
    content = max(verified) if verified else None
    return {'gmv': total, 'gmvKey': total_key, 'videoGmv': video, 'liveGmv': live,
            'contentGmv': content[1] if content else None,
            'window': content[2] if content else total_window,
            'contentKey': content[3] if content else total_key}


def _message(offer, name, handle):
    mention = name.get('mention') or offer.get('title')
    if not isinstance(mention, str) or not mention.strip():
        raise CycleError('localized_name_missing')
    text = (f'Olá @{handle}! Gostaríamos de convidar você para conhecer {mention}. '
            'Este produto oferece amostra grátis, sujeita à disponibilidade e à aprovação da solicitação. '
            'Se tiver interesse, veja o produto no cartão e solicite sua amostra. '
            'Podemos ajudar com as informações para criar seu conteúdo!')
    return {'version': 4, 'template': 'first-sample-invite-v1', 'templateRevision': 1,
            'market': 'br', 'language': 'pt-BR', 'text': text, 'textIt': text,
            'translationZh': f'邀请 @{handle} 了解 {mention}，可通过商品卡申请免费样品；以可用额度和申请审批为准。',
            'deliveryOrder': 'card_then_text', 'pid': offer['pid'], 'executionAllowed': False,
            'requiresVerifiedCard': True, 'commissionState': 'proposed_not_applied'}


def _offer_rank(offer):
    # No GMV threshold and no invented creator/product performance score.
    rate = _number(offer.get('creatorPercent')) or Decimal(0)
    return (0 if offer.get('catalogSource') == 'selected' else 1, -rate,
            -offer['sample']['quota'], offer['pid'], offer['campaignId'], offer['offerKey'])


def _localized_name(store, offer, market='br'):
    if not _table(store, 'cycle_product_name') or not isinstance(offer.get('title'), str):
        return None
    from lib.cycle_materials import market_locale, name_key
    row = store.db.execute('SELECT payload FROM cycle_product_name WHERE id=? AND pid=? AND locale=? AND source_title=?',
                           (name_key(offer, market), offer['pid'], market_locale(market), offer['title'])).fetchone()
    if not row:
        return None
    try:
        name = json.loads(row[0])
    except (ValueError, TypeError):
        return None
    return name if isinstance(name, dict) and isinstance(name.get('mention'), str) and name['mention'].strip() else None


def _materials(root, store, market, offers):
    """Read only exact current names and TapLink bindings; never create either."""
    from lib.catalog_binding import offer_fingerprint
    result = {'readyOfferKeys': set(), 'missingLinks': 0, 'missingNames': 0}
    eligible = [offer for offer in offers if assess_first_offer(offer, store.clock())['eligible']]
    path = Path(root) / 'var/catalog-links.sqlite'
    connection = None
    try:
        if path.exists():
            connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
            if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='catalog_current_binding'").fetchone():
                connection.close()
                connection = None
        for offer in eligible:
            row = connection.execute("SELECT offer_fingerprint,card_payload FROM catalog_current_binding WHERE market=? AND catalog_source=? AND pid=? AND campaign_id=? AND state='active'",
                                     (market, offer.get('catalogSource'), offer['pid'], offer['campaignId'])).fetchone() if connection else None
            linked = False
            try:
                card = json.loads(row[1]) if row else None
                linked = bool(row and row[0] == offer_fingerprint(offer) and isinstance(card, dict) and card.get('listId'))
            except (ValueError, TypeError):
                pass
            named = _localized_name(store, offer, market) is not None
            result['missingLinks'] += int(not linked)
            result['missingNames'] += int(not named)
            if linked and named:
                result['readyOfferKeys'].add(offer['offerKey'])
    finally:
        if connection:
            connection.close()
    return result


def prepare(root, market='br', *, store=None, should_stop=None, max_pages=None):
    _market(market)
    if store is None:
        with CycleStore(Path(root) / 'var/second-cycle.sqlite') as opened:
            return prepare(root, market, store=opened, should_stop=should_stop, max_pages=max_pages)
    ensure_schema(store)
    plan = _plan(store, market)
    fingerprint = _catalog_fingerprint(store, plan)
    offers = [offer for _, offer in _offers(store, plan)]
    available = [offer for offer in offers if assess_first_offer(offer, store.clock())['eligible']]
    by_category = {}
    for offer in sorted(available, key=_offer_rank):
        by_category.setdefault(offer['categoryId'], offer)
    resumed = store.db.execute("SELECT * FROM first_outreach_generation WHERE plan_id=? AND catalog_fingerprint=? AND state IN ('building','paused') ORDER BY created DESC LIMIT 1", (plan, fingerprint)).fetchone()
    generation = resumed['id'] if resumed else 'first-generation-' + uuid.uuid4().hex
    if not resumed:
        store.db.execute('INSERT INTO first_outreach_generation(id,plan_id,created,state,catalog_fingerprint,products) VALUES(?,?,?,?,?,?)',
                         (generation, plan, store.clock(), 'building', fingerprint, len(available)))
    else:
        store.db.execute("UPDATE first_outreach_generation SET state='building' WHERE id=?", (generation,))
    waiting = json.loads(resumed['waiting']) if resumed else {}
    if not resumed:
        for offer in offers:
            for reason in assess_first_offer(offer, store.clock())['reasons']:
                waiting[reason] = waiting.get(reason, 0) + 1
    scanned = resumed['scanned'] if resumed else 0
    matched = resumed['matched'] if resumed else 0
    pages = 0
    try:
        if by_category:
            with closing(_identity_connection(root)) as identities:
                cursor = resumed['last_oec'] if resumed else ''
                while True:
                    if (should_stop and should_stop()) or max_pages is not None and pages >= max_pages:
                        store.db.execute("UPDATE first_outreach_generation SET state='paused',waiting=? WHERE id=?", (encoded(waiting), generation))
                        return {'generationId': generation, 'products': len(available), 'scanned': scanned,
                                'matchedCreators': matched, 'waiting': waiting, 'state': 'paused', 'completed': False,
                                'platformWrites': 0, 'realSends': 0}
                    people = identities.execute('SELECT oec_id FROM creator_identity WHERE market=? AND oec_id>? ORDER BY oec_id LIMIT ?',
                                                 (market, cursor, PAGE_SIZE)).fetchall()
                    if not people:
                        break
                    identities.execute('BEGIN')
                    records = []
                    for person in people:
                        scanned += 1
                        profile = _profile(identities, market, person['oec_id'])
                        category = main_category(profile['payload']) if profile else None
                        if not category:
                            waiting['creator_primary_category_unknown'] = waiting.get('creator_primary_category_unknown', 0) + 1
                            continue
                        offer = by_category.get(category['id'])
                        if not offer:
                            continue
                        performance = _performance(profile)
                        payload = {key: profile[key] for key in ('creatorId', 'oecId', 'handle', 'observedAt', 'evidenceRef', 'fingerprint')}
                        payload.update(category=category, gmv=performance['gmv'],
                                       videoGmv=performance['videoGmv'], liveGmv=performance['liveGmv'],
                                       contentGmv=performance['contentGmv'], gmvWindowEnd=performance['window'],
                                       gmvCurrency='BRL' if performance['window'] is not None else None)
                        records.append((generation, profile['creatorId'], profile['oecId'], offer['pid'], offer['offerKey'],
                                        performance['gmv'], encoded(payload), performance['window'], performance['gmvKey'], performance['contentKey']))
                    identities.execute('COMMIT')
                    with store.tx():
                        store.db.executemany('INSERT OR IGNORE INTO first_outreach_candidate VALUES(?,?,?,?,?,?,?,?,?,?)', records)
                        matched += len(records)
                        store.db.execute('UPDATE first_outreach_generation SET scanned=?,matched=?,last_oec=?,waiting=? WHERE id=?', (scanned, matched, people[-1]['oec_id'], encoded(waiting), generation))
                    cursor = people[-1]['oec_id']
                    pages += 1
        with store.tx():
            if fingerprint != _catalog_fingerprint(store, plan):
                raise CycleError('first_outreach_catalog_changed')
            store.db.execute("UPDATE first_outreach_generation SET state='complete',completed=?,waiting=? WHERE id=?", (store.clock(), encoded(waiting), generation))
            store.db.execute('INSERT INTO first_outreach_head VALUES(?,?) ON CONFLICT(plan_id) DO UPDATE SET generation_id=excluded.generation_id', (plan, generation))
        return {'generationId': generation, 'products': len(available), 'scanned': scanned, 'matchedCreators': matched,
                'waiting': waiting, 'state': 'complete', 'completed': True, 'platformWrites': 0, 'realSends': 0}
    except BaseException:
        store.db.execute("UPDATE first_outreach_generation SET state='failed',completed=?,waiting=? WHERE id=?", (store.clock(), encoded(waiting), generation))
        raise


def _head(store, plan):
    if not _table(store, 'first_outreach_head'):
        return None
    return store.db.execute('SELECT g.* FROM first_outreach_head h JOIN first_outreach_generation g ON g.id=h.generation_id WHERE h.plan_id=? AND g.state=\'complete\'', (plan,)).fetchone()


def _unknown(store, plan):
    if not _table(store, 'cycle_delivery'):
        return 0
    return store.db.execute("""SELECT count(*) FROM cycle_delivery d WHERE d.plan_id=? AND
       json_extract(d.snapshot,'$.executionMode')=? AND d.state<>'quarantined_unknown' AND (d.state='unknown' OR EXISTS(
       SELECT 1 FROM cycle_delivery_part p WHERE p.delivery_id=d.id AND p.state IN ('inflight','accepted','unknown'))
       OR EXISTS(SELECT 1 FROM cycle_conversation_intent i WHERE i.delivery_id=d.id AND i.state IN ('inflight','received')))""", (plan, MODE)).fetchone()[0]


def change_control(store, root, market, request_id, action, expected_revision, *, approvals_enabled=None):
    _market(market)
    if action not in {'start', 'continue', 'stop', 'set-approvals'} or not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{7,119}', request_id) or type(expected_revision) is not int or expected_revision < 0:
        raise CycleError('first_outreach_request_invalid')
    if action == 'set-approvals' and type(approvals_enabled) is not bool or action != 'set-approvals' and approvals_enabled is not None:
        raise CycleError('first_outreach_request_invalid')
    ensure_schema(store)
    plan = _plan(store, market)
    payload = encoded([market, action, approvals_enabled])
    with store.tx():
        prior = store.db.execute('SELECT * FROM first_outreach_request WHERE request_id=?', (request_id,)).fetchone()
        if prior:
            if prior['plan_id'] != plan or prior['payload'] != payload:
                raise CycleError('first_outreach_request_conflict')
            return control(store, market) | {'duplicate': True}
        cfg = control(store, market)
        if expected_revision != cfg['revision']:
            raise CycleError('first_outreach_revision_conflict')
        if action in {'start', 'continue'}:
            if store._plan(plan)['state'] != 'active':
                raise CycleError('plan_paused')
            head = _head(store, plan)
            if not head or not head['matched']:
                raise CycleError('first_outreach_candidates_missing')
            if head['catalog_fingerprint'] != _catalog_fingerprint(store, plan):
                raise CycleError('first_outreach_catalog_changed')
            if _unknown(store, plan):
                raise CycleError('first_outreach_result_unknown')
            materials = _materials(root, store, market, [offer for _, offer in _offers(store, plan)])
            material_match = store.db.execute('SELECT 1 FROM first_outreach_candidate WHERE generation_id=? AND offer_key IN (SELECT value FROM json_each(?)) LIMIT 1',
                                              (head['id'], encoded(sorted(materials['readyOfferKeys'])))).fetchone()
            if not material_match:
                raise CycleError('first_outreach_materials_unavailable')
            if action == 'continue' and not cfg['canaryDeliveryId']:
                raise CycleError('first_outreach_canary_required')
            cfg['mode'] = 'continuous' if action == 'continue' else 'canary'
        elif action == 'stop':
            cfg['mode'] = 'stopped'
        else:
            cfg['approvalsEnabled'] = approvals_enabled
        revision = expected_revision + 1
        first_start = {'requestId': request_id, 'revision': revision} if action in {'start', 'continue'} else cfg['firstStart']
        store.db.execute('''INSERT INTO first_outreach_control VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(plan_id) DO UPDATE SET
         mode=excluded.mode,approvals_enabled=excluded.approvals_enabled,revision=excluded.revision,
         updated_at=excluded.updated_at,start_request=excluded.start_request,start_revision=excluded.start_revision''',
                         (plan, cfg['mode'], int(cfg['approvalsEnabled']), revision, store.clock(),
                          (first_start or {}).get('requestId'), (first_start or {}).get('revision'), cfg['canaryDeliveryId']))
        store.db.execute('INSERT INTO first_outreach_request VALUES(?,?,?,?,?)', (request_id, plan, payload, revision, store.clock()))
    return control(store, market) | {'duplicate': False}


def dispatch_allowed(store, market='br'):
    cfg = control(store, market)
    if cfg['mode'] not in {'canary', 'continuous'} or not cfg['firstStart']:
        raise CycleError('first_outreach_stopped')
    if store._plan(cfg['planId'])['state'] != 'active':
        raise CycleError('plan_paused')
    return cfg


def consume_canary(store, delivery):
    """Consume this confirmed canary grant inside the delivery confirmation transaction."""
    if not store.db.in_transaction:
        raise CycleError('first_outreach_confirmation_transaction_required')
    snapshot = delivery['snapshot']
    if snapshot.get('executionMode') != MODE or delivery['state'] != 'confirmed':
        return None
    cfg = control(store, snapshot['market'])
    if snapshot.get('firstStart') != cfg['firstStart'] or cfg['mode'] not in {'canary', 'stopped'} or cfg['canaryDeliveryId'] == delivery['id']:
        return None
    store.db.execute("UPDATE first_outreach_control SET mode='stopped',canary_delivery=?,revision=revision+1,updated_at=? WHERE plan_id=?",
                     (delivery['id'], store.clock(), delivery['plan_id']))
    return {'canaryCompleted': True, 'canaryStopRequested': True, 'deliveryId': delivery['id']}


def _root(store):
    return Path(store.db.execute('PRAGMA database_list').fetchone()[2]).resolve().parent.parent


def require_delivery(store, candidate, current_delivery_id=None, *, plan=None):
    source = candidate.get('source', {})
    first_marked = candidate.get('executionMode') == MODE or candidate.get('outreachPurpose') == 'first' or source.get('kind') == 'first_category'
    if not first_marked:
        return
    if candidate.get('executionMode') != MODE or candidate.get('outreachPurpose') != 'first' or source.get('kind') != 'first_category':
        raise CycleError('first_outreach_source_invalid')
    cfg = dispatch_allowed(store, candidate.get('market'))
    if plan is not None and cfg['planId'] != plan:
        raise CycleError('first_outreach_plan_changed')
    if candidate.get('firstStart') != cfg['firstStart']:
        raise CycleError('first_outreach_authorization_changed')
    if not assess_first_offer(candidate.get('offer'), store.clock())['eligible']:
        raise CycleError('first_outreach_offer_ineligible')
    current = {offer['offerKey']: offer for _, offer in _offers(store, cfg['planId'])}.get(candidate['offer']['offerKey'])
    if digest(current) != digest(candidate['offer']):
        raise CycleError('first_outreach_offer_changed')
    profile = latest_profile(_root(store), candidate['market'], candidate['oecId'])
    category = main_category(profile['payload']) if profile else None
    if not profile or profile['creatorId'] != candidate['creatorId'] or profile['handle'] != candidate['handle'] or not category or category['id'] != candidate['offer']['categoryId']:
        raise CycleError('first_outreach_match_changed')
    if _table(store, 'cycle_delivery'):
        if cfg['mode'] == 'canary' and store.db.execute("""SELECT 1 FROM cycle_delivery WHERE plan_id=? AND id<>coalesce(?,'')
          AND json_extract(snapshot,'$.executionMode')=? AND json_extract(snapshot,'$.firstStart.requestId')=?
          AND state NOT IN ('cancelled','failed_known','rejected') LIMIT 1""",
          (cfg['planId'], current_delivery_id, MODE, cfg['firstStart']['requestId'])).fetchone():
            raise CycleError('first_outreach_canary_in_progress')
        if store.db.execute("""SELECT 1 FROM cycle_delivery WHERE plan_id=? AND creator_id=? AND pid=? AND id<>coalesce(?,'')
          AND json_extract(snapshot,'$.offer.campaignId')=?
          AND state NOT IN ('cancelled','failed_known','rejected') LIMIT 1""",
          (cfg['planId'], candidate['creatorId'], candidate['pid'], current_delivery_id, candidate['offer']['campaignId'])).fetchone():
            raise CycleError('first_outreach_already_recommended')


def _relation_eligible(store, plan, profile, *, current_delivery_id=None):
    from lib.cycle_delivery import collaboration_blocked
    row = store.db.execute('SELECT * FROM relationship WHERE plan_id=? AND creator_id=?', (plan, profile['creatorId'])).fetchone()
    if row and (row['oec'] != profile['oecId'] or row['mode'] != 'auto' or row['rejected'] or row['inbox_until']):
        return False
    if marketing_isolated(store.db, plan, profile['creatorId'], profile['oecId']) or collaboration_blocked(store, plan, profile['creatorId']):
        return False
    prior = last_contact_by_creator(store.db, plan, creator_id=profile['creatorId'], current_delivery_id=current_delivery_id).get(profile['creatorId'])
    return prior is None or store.clock() - prior >= MARKETING_COOLDOWN_SECONDS


def candidate(root, market, store, plan, initial):
    cfg = dispatch_allowed(store, market)
    if cfg['planId'] != plan:
        raise CycleError('first_outreach_plan_changed')
    head = _head(store, plan)
    if not head or head['catalog_fingerprint'] != _catalog_fingerprint(store, plan):
        raise CycleError('first_outreach_prepare_required')
    offers = {offer['offerKey']: offer for _, offer in _offers(store, plan)}
    path = Path(root) / 'var/catalog-links.sqlite'
    if not path.exists() or not _table(store, 'cycle_product_name'):
        return None
    from lib.catalog_binding import offer_fingerprint
    from lib.cycle_delivery import capacity_for_candidate
    conversations = {str(row['oecId']): row for row in initial.get('conversations', [])}
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as links, closing(_identity_connection(root)) as identities:
        links.row_factory = sqlite3.Row
        # Stream the ranked index: earlier sent/cooling creators must not starve
        # the rest of a large batch. No all-creator list is materialized here.
        skip_sent = ''
        arguments = [plan, head['id']]
        if _table(store, 'cycle_delivery'):
            skip_sent = """ AND NOT EXISTS(SELECT 1 FROM cycle_delivery d WHERE d.plan_id=? AND d.creator_id=c.creator_id
                AND (d.state IN ('ready','running','unknown','quarantined_unknown')
                  OR d.pid=c.pid AND d.state NOT IN ('cancelled','failed_known','rejected')))"""
            arguments.append(plan)
        rows = store.db.execute('''SELECT c.* FROM first_outreach_candidate c
             LEFT JOIN relationship r ON r.plan_id=? AND r.creator_id=c.creator_id
             WHERE c.generation_id=? AND (r.creator_id IS NULL OR r.mode='auto' AND r.rejected=0 AND r.inbox_until=0)
             ''' + skip_sent + ' ORDER BY c.gmv_window DESC,c.content_gmv_key DESC,c.gmv_key DESC,c.creator_id', arguments)
        for row in rows:
            offer = offers.get(row['offer_key'])
            if not offer or not assess_first_offer(offer, store.clock())['eligible']:
                continue
            profile = _profile(identities, market, row['oec'])
            category = main_category(profile['payload']) if profile else None
            if not profile or not category or category['id'] != offer['categoryId'] or not _relation_eligible(store, plan, profile):
                continue
            binding = links.execute("SELECT * FROM catalog_current_binding WHERE market=? AND catalog_source=? AND pid=? AND campaign_id=? AND state='active'", (market, offer.get('catalogSource'), offer['pid'], offer['campaignId'])).fetchone()
            if not binding or binding['offer_fingerprint'] != offer_fingerprint(offer):
                continue
            name = _localized_name(store, offer, market)
            if not name:
                continue
            with store.tx():
                store.db.execute('INSERT OR IGNORE INTO relationship(plan_id,creator_id,oec) VALUES(?,?,?)', (plan, profile['creatorId'], profile['oecId']))
            relation = store.db.execute('SELECT * FROM relationship WHERE plan_id=? AND creator_id=?', (plan, profile['creatorId'])).fetchone()
            value = {'creatorId': profile['creatorId'], 'oecId': profile['oecId'], 'handle': profile['handle'], 'pid': offer['pid'],
                     'source': {'sourceId': 'first-' + digest([head['id'], profile['creatorId'], offer['offerKey']])[:32],
                                'kind': 'first_category', 'categoryId': category['id'], 'profileEvidenceRef': profile['evidenceRef'],
                                'profileFingerprint': profile['fingerprint'], 'generationId': head['id']},
                     'offer': offer, 'offerFingerprint': digest(offer), 'name': name, 'card': json.loads(binding['card_payload']),
                     'message': _message(offer, name, profile['handle']),
                     'planRevision': store._plan(plan)['revision'], 'controlRevision': relation['revision'],
                     'executionMode': MODE, 'outreachPurpose': 'first', 'market': market, 'firstStart': cfg['firstStart'],
                     'conversationId': (conversations.get(profile['oecId']) or {}).get('conversationId')}
            if not capacity_for_candidate(store, plan, value):
                continue
            require_delivery(store, value)
            return value
    return None


def assess_application(store, root, market, application):
    """Approval scope is exact confirmed outreach plus current sample/category facts."""
    _market(market)
    plan = _plan(store, market)
    for key in ('oecId', 'pid', 'campaignId'):
        if not isinstance(application.get(key), str) or not application[key]:
            return {'eligible': False, 'reason': 'sample_application_identity_missing'}
    if not _table(store, 'cycle_delivery'):
        return {'eligible': False, 'reason': 'sample_not_recommended_by_first_outreach'}
    row = store.db.execute("""SELECT * FROM cycle_delivery WHERE plan_id=? AND oec=? AND pid=? AND state='confirmed'
      AND json_extract(snapshot,'$.executionMode')=? AND json_extract(snapshot,'$.offer.campaignId')=? ORDER BY created DESC LIMIT 1""",
      (plan, application['oecId'], application['pid'], MODE, application['campaignId'])).fetchone()
    if not row or application.get('creatorId') not in (None, row['creator_id']):
        return {'eligible': False, 'reason': 'sample_not_recommended_by_first_outreach'}
    offers = [offer for _, offer in _offers(store, plan) if offer['pid'] == application['pid'] and offer.get('campaignId') == application['campaignId']]
    offers = [offer for offer in offers if assess_first_offer(offer, store.clock())['eligible']]
    if not offers:
        return {'eligible': False, 'reason': 'sample_current_offer_ineligible'}
    profile = latest_profile(root, market, application['oecId'])
    category = main_category(profile['payload']) if profile else None
    if not profile or profile['creatorId'] != row['creator_id'] or not category:
        return {'eligible': False, 'reason': 'sample_current_profile_unavailable'}
    offer = next((offer for offer in sorted(offers, key=_offer_rank) if offer['categoryId'] == category['id']), None)
    if not offer:
        return {'eligible': False, 'reason': 'sample_current_category_mismatch'}
    relation = store.db.execute('SELECT * FROM relationship WHERE plan_id=? AND creator_id=?', (plan, row['creator_id'])).fetchone()
    if not relation or relation['mode'] != 'auto' or relation['rejected'] or relation['inbox_until']:
        return {'eligible': False, 'reason': 'sample_relationship_requires_review'}
    return {'eligible': True, 'reason': None, 'deliveryId': row['id'], 'creatorId': row['creator_id'], 'offer': offer}


def status(root, market='br', limit=20):
    _market(market)
    if type(limit) is not int or not 0 <= limit <= 100:
        raise CycleError('first_outreach_limit_invalid')
    empty = {'schemaVersion': 'bdhub.first-outreach.v1', 'market': market,
             'control': {'revision': 0, 'mode': 'stopped', 'approvalsEnabled': False, 'updatedAt': 0},
             'readiness': {'ready': False, 'blockers': ['first_outreach_prepare_required']},
             'counts': dict.fromkeys(['products', 'matchedCreators', 'sent', 'pendingApplications', 'approved', 'needsReview', 'unknown'], 0),
             'candidates': [], 'applications': [], 'runtime': {'state': 'stopped', 'reason': None, 'updatedAt': None}, 'platformWrites': 0}
    path = Path(root) / 'var/second-cycle.sqlite'
    if not path.exists():
        return empty
    with CycleStore(path, readonly=True) as store:
        try:
            plan = _plan(store, market)
        except CycleError:
            return empty
        cfg = control(store, market)
        head = _head(store, plan)
        blockers = []
        offers = [offer for _, offer in _offers(store, plan)]
        empty['counts']['products'] = sum(assess_first_offer(offer, store.clock())['eligible'] for offer in offers)
        materials = _materials(root, store, market, offers)
        if not empty['counts']['products']:
            blockers.append('sample_products_unavailable')
        if not head:
            blockers.append('first_outreach_prepare_required')
        elif head['catalog_fingerprint'] != _catalog_fingerprint(store, plan):
            blockers.append('first_outreach_catalog_changed')
        else:
            empty['counts']['matchedCreators'] = head['matched']
            if not head['matched']:
                blockers.append('first_outreach_matches_unavailable')
            material_match = store.db.execute('SELECT 1 FROM first_outreach_candidate WHERE generation_id=? AND offer_key IN (SELECT value FROM json_each(?)) LIMIT 1',
                                              (head['id'], encoded(sorted(materials['readyOfferKeys'])))).fetchone()
            if head['matched'] and not material_match:
                blockers.append('first_outreach_materials_unavailable')
            by_key = {offer['offerKey']: offer for offer in offers}
            for row in store.db.execute('SELECT * FROM first_outreach_candidate WHERE generation_id=? ORDER BY gmv_window DESC,content_gmv_key DESC,gmv_key DESC,creator_id LIMIT ?', (head['id'], limit)):
                payload = json.loads(row['payload'])
                offer = by_key.get(row['offer_key'], {})
                empty['candidates'].append({'creatorId': row['creator_id'], 'handle': payload['handle'], 'pid': row['pid'],
                                            'productName': offer.get('title') or row['pid'], 'categoryName': payload['category']['name'],
                                            'gmv': payload['gmv'], 'message': _message(offer, _localized_name(store, offer, market) or {'mention': offer.get('title') or row['pid']}, payload['handle'])['text']})
        if _table(store, 'cycle_delivery'):
            empty['counts']['sent'] = store.db.execute("SELECT count(*) FROM cycle_delivery WHERE plan_id=? AND state='confirmed' AND json_extract(snapshot,'$.executionMode')=?", (plan, MODE)).fetchone()[0]
            empty['counts']['unknown'] = _unknown(store, plan)
        if empty['counts']['unknown']:
            blockers.append('first_outreach_result_unknown')
        empty.update(control=cfg, readiness={'ready': not blockers, 'blockers': blockers},
                     runtime={'state': 'waiting_reconciliation' if empty['counts']['unknown'] else cfg['mode'],
                              'reason': blockers[0] if blockers else None, 'updatedAt': cfg['updatedAt'] or None})
        if head:
            empty['generation'] = {'id': head['id'], 'createdAt': head['created'], 'completedAt': head['completed'],
                                   'scanned': head['scanned'], 'waiting': json.loads(head['waiting'])}
        empty['materials'] = {'readyProducts': len(materials['readyOfferKeys']),
                              'missingLinks': materials['missingLinks'], 'missingNames': materials['missingNames']}
        return empty
