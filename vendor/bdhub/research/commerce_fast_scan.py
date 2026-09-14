"""全球销售快采：只翻列表，复用同账号的新鲜方案；未知方案不伪造。"""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from decimal import Decimal

from bdhub.hub.file_intents import now
from .commerce_pool import PoolStore,safe_product
from .commerce_transport import commerce_transport
from .commerce_queue import queued_transport


def recent(stamp):
    try:
        return timedelta(0)<=datetime.now(timezone.utc)-datetime.fromisoformat(stamp)<=timedelta(hours=36)
    except (ValueError,TypeError):
        return False


def reuse_offers(product,cached):
    """公开/总佣金和当前活动ID匹配才复用，保留方案原观测时间。"""
    if not cached or not recent(cached.get('observed_at')):
        return []
    entries=[e for e in cached.get('offers',[]) if str(e['offer'].get('campaign_type')) in {'8','9'}
             and recent(e['offer'].get('observed_at') or cached['observed_at'])]
    try:
        total=Decimal(str(product['commission_rate']))/100
        public=Decimal(str(product['open_collab_rate']))/100
        matches=[e for e in entries if e['offer']['campaign_id']==str(product.get('campaign_id'))
                 and Decimal(e['offer']['total_commission'])==total and Decimal(e['offer']['public_commission'])==public]
    except (ValueError,TypeError,KeyError,ArithmeticError):
        return []
    if not matches:
        return []
    result=deepcopy(entries)
    for e in result:
        e['offer'].setdefault('observed_at',cached['observed_at'])
        e['offer']['details_source']='cached'
    return result


def run_fast_scan(job_id,*,store=None,factory=commerce_transport):
    repository=store or PoolStore()
    with repository.lock():
        job=repository.read(job_id)
        if not job.get('fast_listing') or not job.get('global_only'):
            raise ValueError('commerce_fast_scope_invalid')
        cached={}
        for old in repository.list(job['market']):
            if old['account']!=job['account'] or old['job_id']==job_id or not old.get('global_only'):
                continue
            for pid,p in old['products'].items():
                if p.get('offers') and (pid not in cached or p['observed_at']>cached[pid]['observed_at']):
                    cached[pid]=p
        job.update(state='running',error='',started_at=now(),pause_reason='')
        known=set(job.get('known_pids',[]))
        progress=len(set(job['products'])-known) if job.get('incremental') else len(job['products'])
        def collected():
            return progress
        repository.save(job)
        transport=None
        try:
            with queued_transport(job,repository,factory) as transport:
                def check_stop():
                    if (repository.root/f'{job_id}.stop').exists():raise ValueError('commerce_stopped')
                transport.check_stop=check_stop
                for _ in range(job['page_limit']):
                    if (repository.root/f'{job_id}.stop').exists():
                        job['state']='stopped';break
                    if collected()>=job['product_limit']:
                        job.update(state='paused',pause_reason='target_reached');break
                    if not job['has_more']:
                        job['state']='completed';break
                    buffered=job.pop('page_buffer',[])
                    if buffered:
                        batch=buffered;remote_more=job.get('remote_has_more',False)
                    else:
                        kwargs={'global_only':True}
                        if job.get('category_id'):
                            kwargs['category_id']=job['category_id']
                        data=transport.opportunity_page(job['next_page'],**kwargs)
                        batch=[safe_product(p,global_only=True) for p in data['products']]
                        remote_more=data['has_more']
                        job['pages'].append({'page':job['next_page'],'count':len(batch),'has_more':remote_more})
                        job['next_page']+=1
                    added=0;overflow=[]
                    for product in batch:
                        pid=product['product_id']
                        if pid not in job['products'] and pid not in known and collected()>=job['product_limit']:
                            overflow.append(product);continue
                        if pid not in job['products']:
                            added+=1
                            if pid not in known:progress+=1
                        product['offers']=reuse_offers(product,cached.get(pid))
                        product['details_pending']=not product['offers']
                        product['capture_category_id']=job.get('category_id')
                        job['products'][pid]=product
                    if not added and remote_more and collected()<job['product_limit']:
                        raise ValueError('commerce_pagination_no_progress')
                    job.update(page_buffer=overflow,remote_has_more=remote_more,has_more=bool(overflow) or remote_more,
                               read_verified_at=now(),new_count=collected())
                    repository.save(job)
                if job['state']=='running':
                    if collected()>=job['product_limit']:
                        job.update(state='paused' if job['has_more'] else 'completed',pause_reason='target_reached')
                    else:
                        job.update(state='paused' if job['has_more'] else 'completed',pause_reason='page_budget')
        except Exception as exc:
            code=str(exc) if isinstance(exc,ValueError) else 'commerce_remote_read_failed'
            job.update(state='stopped' if code=='commerce_stopped' else 'blocked',error='' if code=='commerce_stopped' else code,
                       error_type=type(exc).__name__)
        job.update(finished_at=now(),cached_count=sum(bool(p.get('offers')) for p in job['products'].values()))
        job['verification_attempts']=getattr(transport,'verification_attempts',0)
        job['verification_successes']=getattr(transport,'verification_successes',0)
        job['read_retry_count']=getattr(transport,'read_retry_count',0)
        job['last_read']=getattr(transport,'last_read',None)
        job['last_retry']=getattr(transport,'last_retry',None)
        job['detail_pending_count']=len(job['products'])-job['cached_count']
        repository.save(job)
    return job
