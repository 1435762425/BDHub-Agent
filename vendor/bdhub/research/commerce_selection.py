"""先用确定规则选合作方案，再由 AI 评估商品；不选入、不建链、不找达人。"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import os
import subprocess
from uuid import uuid4

from bdhub import config
from bdhub.hub.file_intents import FileIntentStore, now
from .catalog_rules import filters_for, matches_filters, version
from .commerce_transport import require_market
from .commerce_pool import pool_products,sales_value,money_value,CURRENCIES
from .outreach_ai import OutreachStore, model_call, product_facts

PRIORITY = '总佣金高 → 活动截止晚 → 有样品'


def listing_facts(product,market):
    price=product.get('price') or {}
    return {'pid':product['product_id'],'product_name':product.get('title',''),'sales':sales_value(product.get('sales')),
        'rating':product.get('product_rating'),'price_min':money_value(price.get('floor_price')),
        'price_max':money_value(price.get('celling_price') or price.get('floor_price')),'currency':CURRENCIES[market]}


def offer_rank(offer):
    return (-Decimal(str(offer['total_commission'])), -date.fromisoformat(offer['end_date']).toordinal() if offer.get('end_date') else 0,
            not offer.get('has_sample', False), not offer.get('selected', False), not offer.get('joined', False), offer['campaign_id'])


def best_offer(product, rules):
    try:
        if not timedelta(0) <= datetime.now(timezone.utc)-datetime.fromisoformat(product['observed_at']) <= timedelta(hours=36):
            return None
    except (KeyError,TypeError,ValueError):
        return None
    from .commerce_fast_scan import recent
    eligible = [e['offer'] for e in product.get('offers', []) if recent(e['offer'].get('observed_at') or product['observed_at']) and e['offer'].get('platform_available') and matches_filters(e['offer'], rules) and e['offer'].get('total_commission') is not None]
    return min(eligible, key=offer_rank) if eligible else None


def decorated_product(product, rules, *, cache=None, model=None):
    best = best_offer(product, rules)
    result = {k:v for k,v in product.items() if k != 'offers'}
    if result.get('stock') is None and best:
        entry=next(e for e in product['offers'] if e['offer']['offer_key']==best['offer_key'])
        result['stock']=(entry.get('raw',{}).get('campaign_product') or {}).get('stock')
    result['offers'] = [e['offer'] | {'eligible': matches_filters(e['offer'], rules)} for e in product.get('offers', [])]
    result['recommended_offer'] = best
    result['choice'] = {'pid':product['product_id'], 'scan_id':product['scan_id'], 'offer_key':best['offer_key']} if best else None
    result['selection_reason'] = f"按总佣金优先选择 {best['total_commission']}%，截止 {best['end_date'] or '未知'}" if best else '没有符合筛选条件的合作方案'
    fact=listing_facts(product,product.get('market','it')) if product.get('catalog_source')=='global' else best
    result['ai'] = (cache or OutreachStore()).cache_get(fact, model or config.load().reply.model) if fact else None
    return result


class SelectionStore(FileIntentStore):
    def __init__(self, root=None):
        super().__init__(root or config.ROOT / 'data/research/commerce-selection', prefix='cs', error_prefix='selection')


def source_version(rows, rules):
    return version({'rules':rules, 'products':sorted([p['product_id'], p['observed_at'], [e['offer']['offer_key'] for e in p.get('offers', [])]] for p in rows)})


def scoped_products(market,account,global_only=False,keyword='',catalog_source=None):
    from .commerce_sources import source_products
    rows=source_products(market,account,catalog_source) if catalog_source else pool_products(market,account)
    return [p for p in rows
            if (not global_only or p.get('global_source') or any(e['offer'].get('global_source') for e in p.get('offers',[])))
            and keyword.casefold() in f"{p['product_id']} {p.get('title','')} {p.get('shop_name','')}".casefold()]


def start(body, *, store=None, dispatch=True):
    market=body.get('market'); require_market(market)
    from bdhub.send.taplink.transport import account_for
    account_for(market, body.get('account'), check_maintenance=False)
    limit=body.get('limit',100)
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError('selection_limit_invalid')
    global_only=body.get('global_only',False);keyword=body.get('keyword','')
    catalog_source=body.get('catalog_source')
    if catalog_source:
        from .commerce_sources import require_source
        require_source(catalog_source)
    if type(global_only) is not bool or not isinstance(keyword,str) or len(keyword)>200:
        raise ValueError('selection_scope_invalid')
    rules=filters_for(market); products=scoped_products(market,body['account'],global_only,keyword,catalog_source)
    rows=[listing_facts(p,market) for p in products] if catalog_source=='global' else [best for p in products if (best:=best_offer(p,rules))]
    # 大池有明确分析范围：先按已有销量/评分初筛，再让模型评价。不能称为全市场最优。
    rows.sort(key=lambda p:(-(p.get('sales') or 0), -(p.get('rating') or 0), p['pid']))
    rows=rows[:limit]
    if not rows:
        raise ValueError('selection_no_eligible_products')
    repository=store or SelectionStore()
    with repository.lock():
        for job in repository.list(market):
            if job['state'] in {'queued','running'}:
                from .commerce_pool import worker_lost
                if not worker_lost(job):
                    raise ValueError('selection_running')
        cfg=config.load()
        job={'job_id':'cs_'+uuid4().hex,'market':market,'account':body['account'],'state':'queued','created_at':now(),
             'model':cfg.reply.model,'products':rows,'total':len(rows),'processed':0,'error':'','rules':rules,
             'source_version':source_version(products,rules),'global_only':global_only,'keyword':keyword,
             'eligible_total':sum(best_offer(p,rules) is not None for p in products)}
        if catalog_source:
            job['catalog_source']=catalog_source
        repository.save(job)
        if dispatch:
            try:
                proc=subprocess.Popen([str(config.ROOT/'.venv/bin/python'),'-m','bdhub.research.commerce_worker','assess',job['job_id']],
                                      cwd=config.ROOT,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
                job['worker_pid']=proc.pid; repository.save(job)
            except OSError:
                job.update(state='failed',error='selection_worker_start_failed');repository.save(job)
    return job


def public(job):
    from .commerce_pool import worker_lost
    result={k:v for k,v in job.items() if k not in {'products','worker_pid'}}
    if worker_lost(job):
        result.update(state='interrupted',error='selection_worker_interrupted')
    return result


def run(job_id, *, store=None, cache=None, ask=model_call):
    repository=store or SelectionStore(); assessments=cache or OutreachStore()
    with repository.lock():
        job=repository.read(job_id)
        if job['state']!='queued':
            return job
        job.update(state='running',worker_pid=os.getpid());repository.save(job)
    def check():
        if (repository.root/f'{job_id}.stop').exists():
            raise ValueError('selection_stopped')
        rows=scoped_products(job['market'],job['account'],job.get('global_only',False),job.get('keyword',''),job.get('catalog_source'))
        if source_version(rows,filters_for(job['market']))!=job['source_version']:
            raise ValueError('selection_source_changed')
    try:
        for offset in range(0,len(job['products']),40):
            check(); batch=job['products'][offset:offset+40]
            pending=[p for p in batch if not assessments.cache_get(p,job['model'])]
            if pending:
                result=ask({'mode':'assess','products':[product_facts(p) for p in pending]})
                by_id={p['pid']:p for p in pending}
                items=result.get('items',[])
                if len(items)!=len(by_id) or {i['pid'] for i in items}!=set(by_id):
                    raise ValueError('selection_ai_scope_invalid')
                check()
                for item in items:
                    if type(item.get('quality_score')) is not int or not 0 <= item['quality_score'] <= 100:
                        raise ValueError('selection_ai_score_invalid')
                    assessments.cache_put(by_id[item['pid']],job['model'],item)
            job['processed']+=len(batch);repository.save(job)
        job['state']='completed'
    except Exception as exc:
        code=str(exc) if isinstance(exc,ValueError) else 'selection_ai_failed'
        job.update(state='stopped' if code=='selection_stopped' else 'failed',error=code)
    repository.save(job)
    return job
