"""官方高机会商品池：按 has_more 续采，账号/市场/筛选范围各自保存证据。"""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import re
import hashlib
import subprocess
from uuid import uuid4

from bdhub import config
from bdhub.hub.file_intents import FileIntentStore, now
from bdhub.research.campaign_catalog import project_inventory_offer, money_value
from bdhub.research.commerce_transport import commerce_transport, require_market
from bdhub.send.sharelink.commission import decimal_value

CURRENCIES = {'br': 'BRL', 'it': 'EUR', 'uk': 'GBP', 'us': 'USD', 'jp': 'JPY', 'de': 'EUR'}


class PoolStore(FileIntentStore):
    def __init__(self, root=None):
        super().__init__(root or config.ROOT / 'data/research/commerce-pool', prefix='cp', error_prefix='commerce')


def pids(value):
    values = value if isinstance(value, list) else re.split(r'[\s,，;；]+', str(value).strip())
    values = list(dict.fromkeys(v for v in values if v))
    if not 1 <= len(values) <= 500 or any(not isinstance(p, str) or not re.fullmatch(r'[0-9]{19}', p) for p in values):
        raise ValueError('commerce_pid_invalid')
    return values


def sales_value(value):
    # 显示仍保留平台文本；K/M 属近似数，不冒充精确销量。
    match = re.match(r'^\s*([\d,.]+)\s*([KM万]?)', str(value or ''), re.I)
    if not match:
        return None
    try:
        number=match[1]
        if not match[2] and re.fullmatch(r'\d{1,3}(?:[,.]\d{3})+',number):
            number=re.sub(r'[,.]','',number)
        elif ',' in number and '.' in number:
            number=number.replace('.','').replace(',','.') if number.rfind(',')>number.rfind('.') else number.replace(',','')
        else:
            number=number.replace(',','.')
        count=Decimal(number) * {'': 1, 'K': 1000, 'M': 1000000, '万': 10000}[match[2].upper()]
        return int(count) if count>=0 and count==count.to_integral_value() else None
    except (ValueError, ArithmeticError):
        return None


def safe_product(product, *, global_only=False):
    pid = str(product.get('product_id') or '')
    if not re.fullmatch(r'[0-9]{19}', pid):
        raise ValueError('commerce_product_id_invalid')
    # 只保留商品字段；远端 contact_info 不进入账本和 API。
    keys = ('title', 'price', 'commission_rate', 'open_collab_rate', 'sales', 'product_rating',
            'is_free_sample', 'product_image', 'campaign_id', 'fs_is_selected', 'stock',
            'is_under_governed', 'unavailable_type', 'product_status', 'sample_quota')
    result = {k: product[k] for k in keys if k in product}
    result.update(product_id=pid, shop_name=str((product.get('shop_info') or {}).get('shop_name') or product.get('shop_name') or ''),
                  global_source=global_only, observed_at=now())
    return result


def safe_raw(raw):
    """已选响应也可能携带商家联系人；仅保存商品和活动事实。"""
    product = raw.get('campaign_product') or {}
    campaign = raw.get('campaign_info') or {}
    fields = ('product_id', 'product_status', 'product_name', 'shop_name', 'product_sales', 'stock', 'product_price',
              'product_thumbnail', 'product_rating', 'product_review_count', 'sample_quota', 'total_commission_percent',
              'partner_commission_percent', 'plan_commission_percent', 'is_under_governed', 'unavailable_type')
    return {'campaign_product': {k: product[k] for k in fields if k in product},
            'campaign_info': {k: campaign[k] for k in ('campaign_id', 'name', 'campaign_name', 'promotion_start_time', 'promotion_end_time', 'crs_campaign_type', 'commission', 'status') if k in campaign},
            'has_free_sample': raw.get('has_free_sample') is True, '_source_pool': raw.get('_source_pool', 'selected')}


def normalize_offer(product, candidate, market, joined):
    from bdhub.research.commerce_transport import CommerceTransport
    campaign = candidate.get('campaign') or {}
    clean = {k: campaign[k] for k in ('campaign_id', 'name', 'campaign_name', 'crs_campaign_type', 'commission', 'promotion_start_time', 'promotion_end_time', 'start_time', 'end_time', 'status') if k in campaign}
    raw = CommerceTransport._opportunity_as_pickup_item(pid=product['product_id'], product=product,
        campaign_item={k: v for k, v in candidate.items() if k in ('open_collab_rate', 'has_free_sample', 'is_selected')} | {'campaign': clean})
    price = product.get('price') or {}
    raw['campaign_product'].update(product_price={'min_price': price.get('floor_price'), 'max_price': price.get('celling_price')},
        product_sales=sales_value(product.get('sales')), product_thumbnail=product.get('product_image'), shop_name=product.get('shop_name', ''))
    # project_inventory_offer 原 MX/BR 合同不变；非 MX 只扩展币种查表。
    offer = project_inventory_offer(raw, market)
    if not offer:
        raise ValueError('commerce_offer_identity_invalid')
    offer.update(joined=offer['campaign_id'] in joined, selected=candidate.get('is_selected') is True,
                 global_source=offer['campaign_type'] in {'8', '9'}, observed_at=now(),
                 has_sample=product.get('is_free_sample') is True or candidate.get('is_free_sample') is True or candidate.get('has_free_sample') is True or (offer.get('sample_quota') or 0) > 0)
    return {'offer': offer, 'raw': raw}


def create_scan(body, *, store=None):
    market = body.get('market'); require_market(market)
    from bdhub.send.taplink.transport import account_for
    account_for(market, body.get('account'), check_maintenance=False)
    if type(body.get('global_only', False)) is not bool:
        raise ValueError('commerce_scope_invalid')
    targets = pids(body['pids']) if body.get('pids') else []
    limit = body.get('page_limit', 10)
    if type(limit) is not int or not 1 <= limit <= (2000 if body.get('fast_listing') else 100):
        raise ValueError('commerce_page_limit_invalid')
    fast=body.get('fast_listing',False)
    target=body.get('product_limit',300)
    category=body.get('category_id')
    if type(fast) is not bool or (fast and (not body.get('global_only') or type(target) is not int or not 1<=target<=10000)):
        raise ValueError('commerce_fast_scope_invalid')
    if category is not None and (not isinstance(category,str) or not re.fullmatch(r'[0-9]{1,20}',category)):
        raise ValueError('commerce_category_invalid')
    repository = store or PoolStore()
    with repository.lock():
        job = {'job_id': 'cp_' + uuid4().hex, 'market': market, 'account': body['account'],
               'global_only': body.get('global_only', False), 'targets': targets, 'page_limit': limit,
               'next_page': 1, 'pages': [], 'products': {}, 'state': 'draft', 'has_more': True,
               'expand_offers': True, 'error': '', 'created_at': now()}
        if fast:
            job.update(fast_listing=True,product_limit=target,batch_size=target,expand_offers=False,category_id=category)
        repository.save(job)
    return job


def import_campaign_snapshot(market, account, *, store=None):
    require_market(market)
    # BR 现有全量快照直接并入工作区，MX 原来源池保持原路径。
    from bdhub.research.campaign_catalog import CatalogStore
    if market != 'br':
        raise ValueError('commerce_campaign_snapshot_pending')
    catalog = CatalogStore(); snapshot = catalog.joined_snapshot(market)
    if not snapshot or catalog.read_run(snapshot['snapshot_id'])['account'] != account:
        raise ValueError('commerce_campaign_account_mismatch')
    job_id = 'cp_' + hashlib.sha256(f"{market}:{account}:{snapshot['snapshot_id']}".encode()).hexdigest()[:32]
    repository = store or PoolStore()
    with repository.lock():
        if repository.path(job_id).exists():
            return repository.read(job_id)
        products = {}
        for pid, rows in snapshot['index'].items():
            offers = []
            for raw in rows:
                clean = safe_raw(raw); offer = project_inventory_offer(clean, market)
                if not offer:
                    continue
                offer.update(joined=True, selected=False, global_source=False, observed_at=snapshot['generated_at'],
                    has_sample=clean.get('has_free_sample') is True or (offer.get('sample_quota') or 0) > 0)
                offers.append({'offer': offer, 'raw': clean})
            if not offers:
                continue
            p = offers[0]['offer']
            products[pid] = {'product_id': pid, 'title': p['product_name'], 'sales': str(p['sales']) if p['sales'] is not None else '',
                'price': {'floor_price': p['price_text']}, 'product_rating': p['rating'], 'shop_name': p['shop_name'],
                'global_source': False, 'observed_at': snapshot['generated_at'], 'offers': offers, 'source_kind': 'joined_campaign'}
        job = {'job_id': job_id, 'market': market, 'account': account, 'created_at': now(), 'state': 'completed',
               'products': products, 'source_kind': 'joined_campaign', 'source_snapshot_id': snapshot['snapshot_id'],
               'global_only': False, 'pages': [], 'next_page': 1, 'has_more': False, 'error': ''}
        repository.save(job)
    return job


def public_scan(job):
    result = {k: v for k, v in job.items() if k not in {'products', 'worker_pid', 'page_buffer', 'known_pids'}} | {'count': len(job['products'])}
    if worker_lost(job):
        result.update(state='blocked', error='commerce_worker_interrupted')
    messages={'commerce_account_wait_timeout':'账号长时间被占用，断点已保存，可以继续采集。',
        'commerce_worker_interrupted':'采集进程已结束，断点已保存，可以继续采集。',
        'commerce_verification_required':'平台验证未通过，断点已保存。',
        'commerce_pagination_no_progress':'平台返回重复页，断点已保存。',
        'taplink_remote_read_failed':'平台读取失败，断点已保存。'}
    result['error_message']=messages.get(result.get('error'),'读取未完成，断点已保存。' if result.get('error') else '')
    return result


def worker_lost(job):
    if job.get('state') not in {'queued', 'running', 'waiting_account'}:
        return False
    if (datetime.now(timezone.utc) - datetime.fromisoformat(job['updated_at'])).total_seconds() < 30:
        return False
    process = subprocess.run(['ps', '-p', str(int(job.get('worker_pid') or 0)), '-o', 'command='], capture_output=True, text=True, timeout=3)
    return process.returncode != 0 or 'bdhub.research.commerce_worker' not in process.stdout or job['job_id'] not in process.stdout


def run_scan(job_id, *, store=None, factory=commerce_transport):
    repository = store or PoolStore()
    if repository.read(job_id).get('source_kind')=='selected_products':
        from .commerce_selected import run_sync
        return run_sync(job_id,store=repository,factory=factory)
    if repository.read(job_id).get('fast_listing'):
        from .commerce_fast_scan import run_fast_scan
        return run_fast_scan(job_id,store=repository,factory=factory)
    if repository.read(job_id).get('source_kind')=='joined_campaign':
        from .commerce_sources import run_campaign_scan
        return run_campaign_scan(job_id,store=repository,factory=factory)
    with repository.lock():
        job = repository.read(job_id)
        job.update(state='running', error=''); repository.save(job)
        try:
            with factory(job) as transport:
                joined = transport.joined_ids() if job.get('expand_offers') else set()
                for _ in range(job['page_limit']):
                    if (repository.root / f'{job_id}.stop').exists():
                        job['state'] = 'stopped'; break
                    if not job['has_more']:
                        job['state'] = 'completed'; break
                    data = transport.opportunity_page(job['next_page'], global_only=job['global_only'], pids=job['targets'])
                    batch = [safe_product(p, global_only=job['global_only']) for p in data['products']]
                    if job['targets']:
                        batch = [p for p in batch if p['product_id'] in job['targets']]
                    new = set(p['product_id'] for p in batch) - set(job['products'])
                    if data['has_more'] and not new:
                        raise ValueError('commerce_pagination_no_progress')
                    job['products'].update({p['product_id']: p for p in batch})
                    job['pages'].append({'page': job['next_page'], 'count': len(batch), 'new': len(new), 'has_more': data['has_more']})
                    job.update(next_page=job['next_page'] + 1, has_more=data['has_more'])
                    repository.save(job)
                    if job.get('expand_offers'):
                        for product in batch:
                            if (repository.root / f'{job_id}.stop').exists():
                                job['state'] = 'stopped'; break
                            product['offers'] = [normalize_offer(product, c, job['market'], joined) for c in transport.offers(product['product_id'])]
                            job['products'][product['product_id']] = product
                            repository.save(job)
                        if job['state'] == 'stopped':
                            break
                if job['state'] == 'running':
                    job['state'] = 'paused' if job['has_more'] else 'completed'
        except Exception as exc:
            job.update(state='blocked', error=str(exc) if isinstance(exc, ValueError) else 'commerce_remote_read_failed')
        repository.save(job)
        return job


def launch_scan(job_id, *, store=None):
    repository = store or PoolStore()
    with repository.lock():
        job = repository.read(job_id)
        if job.get('fast_listing') and worker_lost(job):
            job.update(state='blocked',error='commerce_worker_interrupted')
        from .commerce_transport import MARKETS
        if any(j['job_id'] != job_id and j['state'] in {'queued', 'running', 'waiting_account'} and not worker_lost(j) for market in MARKETS for j in repository.list(market)):
            raise ValueError('commerce_scan_running')
        if job['state'] not in ({'draft','paused','stopped','blocked'} if job.get('fast_listing') else {'draft','paused','stopped'}):
            raise ValueError('commerce_scan_not_resumable')
        (repository.root / f'{job_id}.stop').unlink(missing_ok=True)
        previous = job['state']
        if previous=='blocked':
            job.setdefault('recovery_history',[]).append({'at':now(),'error':job.get('error',''),'next_page':job['next_page'],'new_count':job.get('new_count',0)})
        job.update(state='queued',error='');repository.save(job)
        if job.get('fast_listing') and job.get('pause_reason')=='target_reached':
            job['product_limit']+=job['batch_size']
            repository.save(job)
        try:
            process = subprocess.Popen([str(config.ROOT / '.venv/bin/python'), '-m', 'bdhub.research.commerce_worker', 'scan', job_id],
                cwd=config.ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            job['worker_pid'] = process.pid
        except OSError:
            job['state'] = previous; job['error'] = 'commerce_worker_start_failed'
        repository.save(job)
    return job


def inspect_product(job_id, pid, *, store=None, factory=commerce_transport):
    repository = store or PoolStore()
    with repository.lock():
        job = repository.read(job_id)
        if pid not in job['products']:
            raise ValueError('commerce_product_not_in_scan')
        with factory(job) as transport:
            data = transport.opportunity_page(1, global_only=job['global_only'],pids=[pid])
            rows = [p for p in data['products'] if str(p.get('product_id')) == pid]
            if len(rows) != 1:
                raise ValueError('commerce_product_unavailable')
            product = safe_product(rows[0], global_only=job['global_only'])
            joined = set() if job['global_only'] else transport.joined_ids()
            product['offers'] = [normalize_offer(product, candidate, job['market'], joined) for candidate in transport.offers(pid)]
            job['products'][pid] = product
            repository.save(job)
        return product


def pool_products(market, account, *, store=None):
    require_market(market)
    by_pid = {}
    for job in (store or PoolStore()).list(market):
        if job['account'] != account:
            continue
        for pid, product in job['products'].items():
            # 观测时间决定新鲜度，较新的扫描不能被旧扫描详情覆盖。
            if pid not in by_pid or product['observed_at'] > by_pid[pid]['observed_at']:
                by_pid[pid] = deepcopy(product) | {'scan_id': job['job_id']}
    return list(by_pid.values())
