"""货盘的两种独立来源：已加入 Campaign、全球销售高机会商品。"""
from copy import deepcopy
from uuid import uuid4

from bdhub.hub.file_intents import now
from bdhub.research.campaign_catalog import project_inventory_offer
from bdhub.research.commerce_pool import PoolStore, safe_raw, launch_scan, worker_lost
from bdhub.research.commerce_transport import commerce_transport, require_market
from bdhub.send.sharelink.transport import JOINED_CAMPAIGN_PRODUCT_PATH
from bdhub.send.taplink.transport import account_for

SOURCES = {'campaign','global','selected'}


def require_source(source):
    if source not in SOURCES:
        raise ValueError('commerce_source_invalid')


def source_products(market,account,source,*,store=None):
    require_market(market);require_source(source)
    jobs=[j for j in (store or PoolStore()).list(market) if j['account']==account]
    if source=='selected':
        candidates=[j for j in jobs if j.get('source_kind')=='selected_products' and j['state']=='completed']
        full=next((j for j in candidates if j.get('snapshot_mode')!='delta'),None)
        jobs=[j for j in candidates if j.get('snapshot_mode')=='delta' and (not full or j['created_at']>full['created_at'])]+([full] if full else [])
    elif source=='campaign':
        # 一个完整版本；新采集中不将部分商品覆盖上次完整货盘。
        jobs=[j for j in jobs if j.get('source_kind')=='joined_campaign' and j['state']=='completed'][:1]
    else:
        jobs=[j for j in jobs if j.get('source_kind') not in {'joined_campaign','selected_products'}]
        explicit=[j for j in jobs if j.get('global_only') is True and (j['products'] or j['state']=='completed')
                  and (not j.get('fast_listing') or j.get('incremental') or j['state']=='completed' or (j['state']=='paused' and j.get('pause_reason')=='target_reached'))]
        if explicit:
            jobs=explicit
        else:
            jobs=[j for j in jobs if not j.get('fast_listing')]
    products={}
    for job in jobs:
        for pid,p in job['products'].items():
            p=deepcopy(p)
            if source=='global':
                p['offers']=[e for e in p.get('offers',[]) if str(e['offer'].get('campaign_type')) in {'8','9'}]
                if not p['offers'] and not p.get('global_source'):
                    continue
            elif source=='campaign':
                p['offers']=[e for e in p.get('offers',[]) if e['offer'].get('joined') and str(e['offer'].get('campaign_type')) in {'1','5','7'}]
            if pid not in products or p['observed_at']>products[pid]['observed_at']:
                products[pid]=p|{'scan_id':job['job_id'],'catalog_source':source,'market':market}
    return list(products.values())


def source_jobs(market,account,source,*,store=None):
    require_market(market);require_source(source)
    return [j for j in (store or PoolStore()).list(market) if j['account']==account and (
        j.get('source_kind')=='joined_campaign' if source=='campaign' else j.get('source_kind')=='selected_products' if source=='selected' else j.get('global_only') is True and j.get('source_kind') not in {'joined_campaign','selected_products'})]


def create_campaign_scan(market,account,*,store=None,dispatch=True):
    require_market(market);account_for(market,account,check_maintenance=False)
    repository=store or PoolStore()
    with repository.lock():
        job={'job_id':'cp_'+uuid4().hex,'market':market,'account':account,'source_kind':'joined_campaign','global_only':False,
             'created_at':now(),'state':'draft','products':{},'campaigns':None,'campaign_index':0,'campaign_page':1,
             'campaign_scanned':0,'campaign_total':None,'pages':[],'next_page':1,'has_more':True,'error':'','page_limit':5000}
        repository.save(job)
    return launch_scan(job['job_id'],store=repository) if dispatch else job


def _remember(job,product,campaign):
    raw=safe_raw({'campaign_product':product,'campaign_info':campaign,'has_free_sample':product.get('has_free_sample'), '_source_pool':'joined_campaign'})
    offer=project_inventory_offer(raw,job['market'])
    if not offer:
        raise ValueError('commerce_campaign_product_invalid')
    offer['product_status']=str(product['product_status']) if product.get('product_status') is not None else ''
    offer.update(joined=True,selected=False,global_source=False,has_sample=(offer.get('sample_quota') or 0)>0 or raw.get('has_free_sample') is True,observed_at=now())
    pid=offer['pid'];entry={'offer':offer,'raw':raw}
    if pid not in job['products']:
        job['products'][pid]={'product_id':pid,'title':offer['product_name'],'shop_name':offer['shop_name'],
            'price':{'floor_price':offer['price_text']},'sales':str(offer['sales']) if offer['sales'] is not None else '',
            'product_rating':offer['rating'],'product_image':raw['campaign_product'].get('product_thumbnail'),
            'observed_at':now(),'source_kind':'joined_campaign','offers':[]}
        job['products'][pid]['stock']=raw['campaign_product'].get('stock')
    offer['stock']=raw['campaign_product'].get('stock')
    rows=job['products'][pid]['offers']
    if not any(e['offer']['offer_key']==offer['offer_key'] for e in rows):
        rows.append(entry)


def run_campaign_scan(job_id,*,store=None,factory=commerce_transport):
    repository=store or PoolStore()
    with repository.lock():
        job=repository.read(job_id)
        if job['state'] not in {'queued','draft'} or job.get('source_kind')!='joined_campaign':
            raise ValueError('commerce_scan_not_resumable')
        job.update(state='running',error='');repository.save(job)
        try:
            with factory(job) as t:
                if job['campaigns'] is None:
                    campaigns=t.campaigns({'campaign_join_status_category':'1','crs_campaign_types':''})
                    job['campaigns']=[t._joined_campaign_summary(c) for c in campaigns if t._joined_campaign_type(c) in {'1','5','7'}]
                    job['excluded_campaigns']=len(campaigns)-len(job['campaigns'])
                    repository.save(job)
                for _ in range(job['page_limit']):
                    if (repository.root/f'{job_id}.stop').exists():
                        job['state']='stopped';break
                    if job['campaign_index']>=len(job['campaigns']):
                        job.update(state='completed',has_more=False);break
                    campaign=job['campaigns'][job['campaign_index']]
                    data=t.require_read(t._xhr(method='GET',path=JOINED_CAMPAIGN_PRODUCT_PATH,params=t._params()|{
                        'campaign_id':campaign['campaign_id'],'cur_page':job['campaign_page'],'page_size':100,
                        'marked':t._joined_campaign_marked(campaign)},payload=None,write=False)).get('data')
                    job['read_verified_at']=now()
                    if not isinstance(data,dict) or not isinstance(data.get('total_num'),(str,int)) or isinstance(data['total_num'],bool):
                        raise ValueError('commerce_campaign_page_incomplete')
                    total=int(data['total_num']);rows=data.get('campaign_product',[]) if total==0 else data.get('campaign_product')
                    if total<0 or not isinstance(rows,list) or (job['campaign_total'] is not None and job['campaign_total']!=total):
                        raise ValueError('commerce_campaign_page_changed')
                    scanned=job['campaign_scanned']+len(rows)
                    if scanned>total or (not rows and scanned<total):
                        raise ValueError('commerce_campaign_page_incomplete')
                    prior={pid for pid,p in job['products'].items() if any(e['offer']['campaign_id']==campaign['campaign_id'] for e in p['offers'])}
                    batch_ids=[str(p.get('product_id') or '') for p in rows]
                    if len(batch_ids)!=len(set(batch_ids)) or prior.intersection(batch_ids):
                        raise ValueError('commerce_campaign_page_repeated')
                    for p in rows:
                        _remember(job,p,campaign)
                    job['pages'].append({'campaign_id':campaign['campaign_id'],'page':job['campaign_page'],'count':len(rows)})
                    job['next_page']+=1
                    if scanned==total:
                        job.update(campaign_index=job['campaign_index']+1,campaign_page=1,campaign_scanned=0,campaign_total=None)
                    else:
                        job.update(campaign_page=job['campaign_page']+1,campaign_scanned=scanned,campaign_total=total)
                    repository.save(job)
                if job['state']=='running':
                    job['state']='completed' if job['campaign_index']>=len(job['campaigns']) else 'paused'
                    job['has_more']=job['state']!='completed'
        except Exception as exc:
            job.update(state='blocked',error=str(exc) if isinstance(exc,ValueError) else 'commerce_remote_read_failed')
        repository.save(job)
    return job
