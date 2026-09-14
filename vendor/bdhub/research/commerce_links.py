"""统一选品到商品卡的编排；实际建链事实继续保存在 TapLinkStore。"""
from copy import deepcopy
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import subprocess
from uuid import uuid4

from bdhub import config
from bdhub.hub.file_intents import FileIntentStore, now
from bdhub.hub.markets import require_capability
from bdhub.research.catalog_rules import filters_for, matches_filters, validate_link_rule, validate_filters
from bdhub.research.commerce_pool import PoolStore, normalize_offer, safe_product, safe_raw, worker_lost
from bdhub.research.commerce_transport import commerce_transport, require_market
from bdhub.send.taplink.service import TapLinkStore, prepare_locked, validate_live
from .commerce_build_selection import MAX_BUILD_ITEMS

# 用户确认的意大利验证样本；CLI 的一次性有限验证不开放普通 API 写入能力。
CANARY_PIDS = frozenset({'1729739918820874407', '1729779362302171335'})
UNKNOWN = {'joining', 'join_unknown', 'selecting', 'selection_unknown', 'creating', 'result_unknown', 'created_unverified'}


class CommerceStore(FileIntentStore):
    def __init__(self, root=None):
        super().__init__(root or config.ROOT / 'data/research/commerce-links', prefix='cw', error_prefix='commerce')


def fresh(stamp, hours=24):
    try:
        return timedelta(0) <= datetime.now(timezone.utc) - datetime.fromisoformat(stamp) <= timedelta(hours=hours)
    except (TypeError, ValueError):
        return False


def validate_canary(job):
    if (job.get('market') != 'it' or job.get('account') != 'acc6' or not job.get('validation_scope')
            or not fresh(job['validation_scope'], 2) or not 1 <= len(job['items']) <= 2
            or not {i['pid'] for i in job['items']} <= CANARY_PIDS):
        raise ValueError('commerce_validation_scope_invalid')


def prepare_batch(body, *, store=None, pool=None, validation=False):
    market = body.get('market'); require_market(market)
    source=body.get('catalog_source')
    if source is not None:
        from .commerce_sources import require_source
        require_source(source)
    from bdhub.send.taplink.transport import account_for
    account_for(market, body.get('account'), check_maintenance=False)
    choices = body.get('choices')
    if not isinstance(choices, list) or not 1 <= len(choices) <= MAX_BUILD_ITEMS:
        raise ValueError('commerce_selection_required')
    rule = validate_link_rule(body.get('rule', {}))
    filters = validate_filters(body.get('filters', filters_for(market)))
    items = []; seen = set(); scans = {}; pool = pool or PoolStore()
    for choice in choices:
        if choice['scan_id'] not in scans:scans[choice['scan_id']]=pool.read(choice['scan_id'])
        scan = scans[choice['scan_id']]
        if scan['market'] != market or scan['account'] != body['account']:
            raise ValueError('commerce_scope_invalid')
        product = scan['products'].get(choice['pid'])
        if not product or not fresh(product['observed_at'], 36):
            raise ValueError('commerce_offer_stale')
        if source:
            if (source=='campaign') != (scan.get('source_kind')=='joined_campaign'):
                raise ValueError('commerce_source_route_mismatch')
            product=deepcopy(product)
            if source=='selected' and scan.get('source_kind')!='selected_products':
                raise ValueError('commerce_selected_source_required')
            product['offers']=[e for e in product.get('offers',[]) if (
                str(e['offer'].get('campaign_type')) in {'8','9'} if source=='global' else e['offer'].get('selected') if source=='selected' else e['offer'].get('joined') and str(e['offer'].get('campaign_type')) in {'1','5','7'})]
        from .commerce_selection import best_offer
        recommended = best_offer(product, filters)
        selected_key = choice.get('offer_key') or (recommended or {}).get('offer_key')
        entry = next((e for e in product.get('offers', []) if e['offer']['offer_key'] == selected_key), None)
        if not entry or not fresh(entry['offer'].get('observed_at') or product['observed_at'],36) or not matches_filters(entry['offer'], filters):
            raise ValueError('commerce_offer_ineligible')
        if choice['pid'] in seen:
            raise ValueError('commerce_duplicate_pid')
        seen.add(choice['pid'])
        offer = entry['offer']
        route = 'campaign' if scan.get('source_kind') == 'joined_campaign' and offer['joined'] and offer['campaign_type'] in {'1', '5', '7'} else 'selected'
        items.append({'pid': choice['pid'], 'offer': offer, 'raw': entry['raw'], 'route': route,
                      'parent_campaign_id': offer['campaign_id'], 'campaign_id': offer['campaign_id'],
                      'state': 'pending', 'error': '', 'taplink_job_id': None})
    repository = store or CommerceStore()
    with repository.lock():
        fingerprint = hashlib.sha256(json.dumps({'choices': choices, 'rule': rule, 'filters': filters, 'account': body['account'],**({'catalog_source':source} if source else {})}, sort_keys=True).encode()).hexdigest()
        key = body.get('request_key')
        if not isinstance(key, str) or not 1 <= len(key) <= 100:
            raise ValueError('commerce_request_key_required')
        for previous in repository.list(market):
            if previous['request_key'] == key:
                if previous['fingerprint'] != fingerprint:
                    raise ValueError('commerce_request_key_conflict')
                return previous
            if previous['account'] == body['account'] and any(i['pid'] in seen and i['state'] in UNKNOWN for i in previous['items']):
                raise ValueError('commerce_existing_unknown')
        job = {'job_id': 'cw_' + uuid4().hex, 'market': market, 'account': body['account'], 'items': items,
               'state': 'draft', 'rule': rule, 'filters': filters, 'request_key': key, 'fingerprint': fingerprint,
               'created_at': now(), 'contact_email': body.get('contact_email', ''), 'error': ''}
        if source:
            job['catalog_source']=source
        if validation:
            job['validation_scope'] = now(); validate_canary(job)
        repository.save(job)
    return job


def public_batch(job):
    result = {k: v for k, v in job.items() if k not in {'fingerprint', 'worker_pid', 'contact_email', 'validation_scope'}} | {
        'items': [{k: v for k, v in i.items() if k != 'raw'} for i in job['items']],
        'finished': sum(i['state'] not in {'pending'} | UNKNOWN for i in job['items']), 'total': len(job['items']),
        'counts':dict(Counter(i['state'] for i in job['items']))}
    if worker_lost(job):
        result.update(state='needs_verification', error='commerce_worker_interrupted')
    return result


def _live_entry(t, job, item):
    pid = item['pid']
    if job.get('catalog_source')=='selected':
        from .campaign_catalog import project_inventory_offer
        selected=t.selected(pid,item['campaign_id'])
        if not selected:raise ValueError('commerce_selected_product_missing')
        raw=safe_raw(selected);offer=project_inventory_offer(raw,job['market'])
        if not offer or not matches_filters(offer,job['filters']) or any(offer[k]!=item['offer'][k] for k in ('total_commission','public_commission','end_date')):
            raise ValueError('commerce_offer_changed')
        return {'raw':raw,'offer':item['offer']|offer}
    if item['route'] == 'campaign':
        from bdhub.research.campaign_catalog import project_inventory_offer
        found = t.revalidate_joined_campaign(pid=pid, campaign_id=item['campaign_id'])
        raw = safe_raw(t.exact(found.get('items', []), pid, item['campaign_id']))
        offer = project_inventory_offer(raw, job['market'])
        if not offer or not matches_filters(offer, job['filters']) or any(offer[k] != item['offer'][k] for k in ('total_commission', 'public_commission', 'end_date')):
            raise ValueError('commerce_offer_changed')
        return {'raw': raw, 'offer': {**item['offer'], **offer}}
    rows = [p for p in t.opportunity_page(1, pids=[pid])['products'] if str(p.get('product_id')) == pid]
    if len(rows) != 1:
        raise ValueError('commerce_product_unavailable')
    candidate = next((c for c in t.offers(pid) if str((c.get('campaign') or {}).get('campaign_id')) == item['campaign_id']), None)
    if candidate is None:
        raise ValueError('commerce_fixed_offer_missing')
    entry = normalize_offer(safe_product(rows[0]), candidate, job['market'], t.joined_ids())
    offer = entry['offer']
    if not matches_filters(offer, job['filters']) or any(offer[k] != item['offer'][k] for k in ('total_commission', 'public_commission', 'end_date')):
        raise ValueError('commerce_offer_changed')
    return entry


def run_batch(job_id, *, action='create', store=None, links=None, factory=commerce_transport, validation=False, known=None):
    repository = store or CommerceStore(); link_store = links or TapLinkStore()
    with repository.lock():
        job = repository.read(job_id)
        if worker_lost(job):
            job['state'] = 'needs_verification'
            repository.save(job)
        if action not in {'select', 'create', 'verify'} or job['state'] not in {'draft', 'queued', 'selected', 'stopped', 'needs_verification', 'completed'}:
            raise ValueError('commerce_action_invalid')
        if action in {'select','create'} and (job['state'] in {'needs_verification', 'completed'} or any(i['state'] in UNKNOWN for i in job['items'])):
            raise ValueError('commerce_verify_required')
        if validation:
            validate_canary(job)
        job.update(state='running', error='', action=action); repository.save(job)
        def save_item(item, state, **fields):
            item.update(state=state, **fields); repository.save(job)
        try:
            from bdhub.send.taplink.rule_batches import existing_index
            existing_links=existing_index(job['market'],job['account'],links=link_store) if known is None else known
            with factory(job, allow_write=action in {'select','create'}, validation=validation) as transport:
                for index, item in enumerate(job['items']):
                    if (repository.root / f'{job_id}.stop').exists():
                        job['state'] = 'stopped'; break
                    if action in {'select','create'} and item['state'] not in {'pending','selected','prepared'}:
                        continue
                    if action == 'select' and item['state'] in {'selected','prepared'}:
                        continue
                    try:
                        # 原来存在未知建链意图时只对原绑定查卡。
                        if action == 'verify' and item.get('taplink_job_id'):
                            with link_store.lock():
                                link_job = link_store.read(item['taplink_job_id'])
                                card = transport.card(link_job)
                                if card:
                                    link_job.update(state='card_verified', card=card, verified_at=now(), error='')
                                    link_store.save(link_job); save_item(item, 'ready', error='')
                                else:
                                    save_item(item, 'created_unverified', error='taplink_card_not_visible')
                            continue
                        if action == 'verify':
                            if item['state'] in {'selection_unknown', 'selecting'}:
                                selected = transport.selected_after(item)
                                if selected:
                                    save_item(item, 'pending', campaign_id=str(selected['campaign_info']['campaign_id']), error='', selected_at=now())
                            # 加入回执未知没有最终活动 ID，保留原意图，不猜测映射。
                            continue
                        existing = existing_links.get(item['pid']) or transport.find_existing(item['pid'])
                        if existing:
                            # 现有 URL 不等于可用绑定；先明确展示并交只读核验。
                            save_item(item, 'existing_link', existing_list_id=existing['list_id'], error='已有链接，跳过重复创建；在已建链接中核验。')
                            continue
                        entry = _live_entry(transport, job, item)
                        offer = entry['offer']
                        cid = item['campaign_id']
                        if item['route'] == 'selected':
                            selected = transport.selected(item['pid'], cid)
                            if not selected:
                                if job.get('catalog_source')=='selected':
                                    raise ValueError('commerce_selected_product_missing')
                                if not offer['joined'] and offer['campaign_type'] in {'4', '6'} and cid == item['parent_campaign_id']:
                                    if not validation:
                                        require_capability(job['market'], 'campaign_join')
                                    save_item(item, 'joining')
                                    cid = transport.join_offer(cid, offer['campaign_type'], job.get('contact_email', ''))
                                    save_item(item, 'pending', campaign_id=cid, joined_at=now())
                                save_item(item, 'selecting')
                                transport.require_read(transport.select_product(item['pid'], cid))
                                selected = transport.selected_after(item)
                                if not selected:
                                    raise ValueError('commerce_selection_unverified')
                            raw = safe_raw(selected)
                            cid = str(raw['campaign_info']['campaign_id'])
                        else:
                            found = transport.revalidate_joined_campaign(pid=item['pid'], campaign_id=cid)
                            raw = safe_raw(transport.exact(found.get('items', []), item['pid'], cid))
                        from bdhub.research.campaign_catalog import project_inventory_offer
                        actual = project_inventory_offer(raw, job['market'])
                        if not actual or not matches_filters(actual, job['filters']) or any(actual[k] != offer[k] for k in ('total_commission', 'public_commission')):
                            raise ValueError('commerce_selected_offer_changed')
                        # 回读已选值缺少展示字段时只补展示，不覆盖佣金/样品/库存。
                        actual = enrich_display(actual, offer)
                        save_item(item, 'pending', campaign_id=cid, raw=raw, offer=actual, selected_at=now())
                        if action == 'select':
                            save_item(item, 'prepared' if item['route']=='campaign' else 'selected', error='')
                            continue
                        with link_store.lock():
                            # 精确 PID 跨活动/来源去重，包含结果未知的已提交意图。
                            # 持锁再检查，覆盖前面平台读取期间出现的其它创建记录。
                            previous_links=[j for j in link_store.list(job['market']) if j['account']==job['account']]
                            from bdhub.send.taplink.cleanup import CleanupStore
                            prior=None
                            for child in previous_links:
                                if child['offer']['pid']!=item['pid'] or not (child.get('creation_attempted') or child.get('receipt',{}).get('url') or child['state'] in {'ready','card_verified','creating','result_unknown','created_unverified'}):
                                    continue
                                lid=(child.get('card') or {}).get('list_id') or child.get('receipt',{}).get('list_id')
                                if not lid or CleanupStore().retirement_state(job['market'],lid)!='deleted':
                                    prior=child;break
                            if prior:
                                save_item(item,'existing_link',existing_list_id=lid,error='已有创建记录，跳过重复创建。')
                                continue
                            body = {'market': job['market'], 'account': job['account'], 'purpose': 'catalog', 'route': item['route'],
                                    'offer_key': actual['offer_key'], 'snapshot_id': job_id, 'request_key': job_id + ':' + item['pid'],
                                    'link_rule': job['rule'], 'filter_rules': job['filters'], 'index': index + 1}
                            link_job = prepare_locked(body, actual, link_store, previous_links)
                            link_job.update(unified=True, source_raw=raw, has_sample=offer['has_sample'], commerce_job_id=job_id)
                            link_store.save(link_job)
                            save_item(item, 'creating', taplink_job_id=link_job['job_id'])
                            if link_job['state'] in {'draft', 'selected'}:
                                link_job.update(state='creating', stage='create', creation_attempted=True); link_store.save(link_job)
                                try:
                                    receipt = transport.create(link_job)
                                    link_job.update(receipt=receipt, state='created_unverified', stage='created'); link_store.save(link_job)
                                except Exception:
                                    link_job.update(state='result_unknown', error='taplink_create_result_unknown'); link_store.save(link_job)
                                    raise
                            card = transport.card(link_job)
                            if card:
                                link_job.update(state='ready', card=card, verified_at=now(), error=''); link_store.save(link_job)
                                save_item(item, 'ready', error='')
                            else:
                                save_item(item, 'created_unverified', error='taplink_card_not_visible')
                                break
                    except Exception as exc:
                        state = {'joining': 'join_unknown', 'selecting': 'selection_unknown', 'creating': 'result_unknown'}.get(item['state'], 'failed')
                        code = str(exc) if isinstance(exc, ValueError) else 'commerce_operation_interrupted'
                        save_item(item, state, error=code)
                        # 验证/认证/网络异常不继续其它商品，以免扩大未知状态。
                        if state in UNKNOWN or 'read_failed' in code or 'capability' in code:
                            break
        except Exception as exc:
            job['error'] = str(exc) if isinstance(exc, ValueError) else 'commerce_operation_interrupted'
        if job['state'] == 'running':
            job['state'] = 'needs_verification' if any(i['state'] in UNKNOWN for i in job['items']) else 'stopped' if any(i['state'] == 'pending' for i in job['items']) else 'completed'
            if job['state']=='completed' and any(i['state'] in {'selected','prepared'} for i in job['items']):
                job['state']='selected'
        repository.save(job)
        return job


def launch_batch(job_id, action, *, store=None):
    repository = store or CommerceStore()
    with repository.lock():
        job = repository.read(job_id)
        if worker_lost(job):
            job['state'] = 'needs_verification'
            repository.save(job)
        if action in {'select','create'}:
            for capability in (('product_select',) if action=='select' else ('tap_link',) + (('product_select',) if any(i['route']=='selected' for i in job['items']) else ())):
                require_capability(job['market'], capability)
        elif action != 'verify':
            raise ValueError('commerce_action_invalid')
        if job['state'] in {'running', 'queued'} or (action in {'select','create'} and job['state'] not in {'draft','selected', 'stopped'}):
            raise ValueError('commerce_verify_required')
        previous = job['state']; job.update(state='queued'); repository.save(job)
        (repository.root / f'{job_id}.stop').unlink(missing_ok=True)
        try:
            p = subprocess.Popen([str(config.ROOT / '.venv/bin/python'), '-m', 'bdhub.research.commerce_worker', action, job_id],
                cwd=config.ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            job['worker_pid'] = p.pid
        except OSError:
            job.update(state=previous, error='commerce_worker_start_failed')
        repository.save(job)
    return job


def ready_products(market, *, account=None, purpose='second', store=None):
    require_market(market)
    from bdhub.send.taplink.cleanup import CleanupStore
    clean = CleanupStore(); rows = []; seen = set()
    for job in (store or TapLinkStore()).list(market):
        if not job.get('unified') or account and job['account'] != account:
            continue
        key = (job['account'], job['offer']['pid'])
        if key in seen:
            continue
        seen.add(key)
        card = job.get('card') or {}
        if (job['state'] not in {'ready', 'card_verified'} or not fresh(job.get('verified_at')) or not card.get('list_id')
                or clean.is_blocked(market, card['list_id']) or not matches_filters(job['offer'], filters_for(market))
                or purpose == 'first' and not job.get('has_sample')):
            continue
        rows.append({**job['offer'], 'account': job['account'], 'binding_id': job['job_id'], 'list_id': card['list_id'],
                     'verified_at': job['verified_at'], 'has_sample': job.get('has_sample', False)})
    return rows


def recruitment_snapshot(market):
    if market == 'mx':
        from bdhub.research.campaign_catalog import CatalogStore
        return CatalogStore().snapshot(market)
    rows = ready_products(market)
    if not rows:
        return None
    index = {}; identities = []
    for row in rows:
        job = TapLinkStore().read(row['binding_id'])
        raw = deepcopy(job['source_raw']); raw.update(_ready_binding=row['binding_id'], _ready_account=row['account'], _ready_list_id=row['list_id'])
        for field, source in (('product_rating', 'rating'), ('product_sales', 'sales')):
            if raw['campaign_product'].get(field) is None:
                raw['campaign_product'][field] = job['offer'].get(source)
        # 招募只把平台明确可提供样品视为资格，不虚构样品数值。
        raw['_recruitment_has_sample'] = job.get('has_sample', False)
        index.setdefault(row['pid'], []).append(raw)
        identities.append([row['binding_id'], row['verified_at']])
    version = hashlib.sha256(json.dumps(sorted(identities)).encode()).hexdigest()[:24]
    return {'snapshot_id': 'ready:' + version, 'market': market, 'index': index, 'generated_at': min(r['verified_at'] for r in rows)}


def recruitment_view(snapshot, rules, purpose):
    from bdhub.research.campaign_catalog import inventory_view
    # 有样品是平台事实；额度缺失保留 None，不伪造数字。
    view = inventory_view(snapshot, {**rules, 'samples_only': False})
    if purpose == 'first' and snapshot:
        samples = {f"{p['campaign_product']['product_id']}:{p['campaign_info']['campaign_id']}"
                   for rows in snapshot['index'].values() for p in rows if p.get('_recruitment_has_sample')}
        view['items'] = [p for p in view['items'] if p['offer_key'] in samples]
    return view


def enrich_display(actual, original):
    result = {**original, **actual}
    for key in ('product_name', 'rating', 'sales', 'price_min', 'price_max', 'price_text', 'image_url', 'shop_name', 'review_count'):
        if result.get(key) in (None, ''):
            result[key] = original.get(key)
    return result
