"""已选商品存量同步与全球销售批量选入；选入和建链为两个独立动作。"""
from copy import deepcopy
from collections import Counter
from contextlib import contextmanager
from decimal import Decimal
import hashlib
import json
import os
import subprocess
import time
from uuid import uuid4

from bdhub import config
from bdhub.hub.file_intents import FileIntentStore, now
from bdhub.hub.markets import require_capability
from bdhub.send.sharelink.candidates import campaign_time_status
from .commerce_pool import PoolStore, safe_raw, safe_product, normalize_offer, launch_scan, worker_lost
from .commerce_transport import commerce_transport, require_market
from .commerce_fast_scan import recent
from .campaign_catalog import project_inventory_offer


def remember(job, row):
    raw=safe_raw(row)
    offer=project_inventory_offer(raw,job['market'])
    if not offer:
        raise ValueError('commerce_selected_response_invalid')
    offer.update(selected=True,joined=False,global_source=offer['campaign_type'] in {'8','9'},observed_at=now(),
        stock=raw['campaign_product'].get('stock'),has_sample=raw.get('has_free_sample') is True or (offer.get('sample_quota') or 0)>0)
    pid=offer['pid']
    product=job['products'].setdefault(pid,{'product_id':pid,'title':offer['product_name'],'shop_name':offer['shop_name'],
        'price':{'floor_price':offer['price_text']},'sales':str(offer['sales']) if offer['sales'] is not None else '',
        'product_rating':offer['rating'],'stock':offer['stock'],'observed_at':now(),'source_kind':'selected_products','offers':[]})
    product['offers']=[e for e in product['offers'] if e['offer']['offer_key']!=offer['offer_key']]+[{'offer':offer,'raw':raw}]


def new_snapshot(market,account,*,delta=False):
    return {'job_id':'cp_'+uuid4().hex,'market':market,'account':account,'source_kind':'selected_products',
        'snapshot_mode':'delta' if delta else 'full','products':{},'created_at':now(),'state':'draft',
        'pages':[],'next_page':1,'has_more':True,'error':'','global_only':False}


def start_sync(market,account,*,store=None,dispatch=True):
    require_market(market)
    from bdhub.send.taplink.transport import account_for
    account_for(market,account,check_maintenance=False)
    repository=store or PoolStore();job=new_snapshot(market,account)
    with repository.lock():repository.save(job)
    return launch_scan(job['job_id'],store=repository) if dispatch else job


def selected_rows(transport, pids=None):
    rows=[];seen=set();total=None
    for page in range(1,2001):
        data=transport.selected_page(page,pids=pids)
        if total is not None and data['total']!=total:
            raise ValueError('commerce_selected_list_changed')
        total=data['total']
        for row in data['items']:
            key=(str((row.get('campaign_product') or {}).get('product_id')),str((row.get('campaign_info') or {}).get('campaign_id')))
            if key in seen or (pids is not None and key[0] not in pids):
                raise ValueError('commerce_selected_response_invalid')
            seen.add(key);rows.append(row)
        if len(rows)==total:return rows
        if not data['items'] or len(rows)>total:raise ValueError('commerce_selected_response_invalid')
    raise ValueError('commerce_selected_page_limit')


def run_sync(job_id,*,store=None,factory=commerce_transport):
    repository=store or PoolStore()
    with repository.lock():
        job=repository.read(job_id);job.update(state='running',error='');repository.save(job)
        total=None;seen=set();read=0
        try:
            with factory(job) as t:
                for page in range(1,2001):
                    if (repository.root/f'{job_id}.stop').exists():job['state']='stopped';break
                    data=t.selected_page(page)
                    if total is not None and data['total']!=total:raise ValueError('commerce_selected_list_changed')
                    total=data['total']
                    for row in data['items']:
                        key=(str((row.get('campaign_product') or {}).get('product_id')),str((row.get('campaign_info') or {}).get('campaign_id')))
                        if key in seen:raise ValueError('commerce_selected_response_invalid')
                        seen.add(key);remember(job,row)
                    read+=len(data['items'])
                    job['pages'].append({'page':page,'count':len(data['items'])})
                    job.update(next_page=page+1,read_verified_at=now(),row_total=total)
                    repository.save(job)
                    if read==total:job.update(state='completed',has_more=False);break
                    if not data['items'] or read>total:raise ValueError('commerce_selected_response_invalid')
                else:raise ValueError('commerce_selected_page_limit')
        except Exception as exc:
            job.update(state='blocked',error=str(exc) if isinstance(exc,ValueError) else 'commerce_remote_read_failed')
        repository.save(job)
    return job


class IntakeStore(FileIntentStore):
    def __init__(self,root=None):
        super().__init__(root or config.ROOT/'data/research/commerce-intake',prefix='ci',error_prefix='commerce')


def start_intake(body,*,store=None,pool=None,dispatch=True):
    market,account=body.get('market'),body.get('account');require_market(market)
    require_capability(market,'product_select')
    from bdhub.send.taplink.transport import account_for
    account_for(market,account,check_maintenance=False)
    from .commerce_sources import source_products
    available={p['product_id']:p for p in source_products(market,account,'global',store=pool)}
    pids=body.get('pids')
    if not isinstance(pids,list) or not 1<=len(pids)<=10000 or len(set(pids))!=len(pids) or not set(pids)<=available.keys():
        raise ValueError('commerce_selection_required')
    key=body.get('request_key')
    if not isinstance(key,str) or not 1<=len(key)<=100:raise ValueError('commerce_request_key_required')
    fingerprint=hashlib.sha256(json.dumps([account,sorted(pids)]).encode()).hexdigest()
    repository=store or IntakeStore()
    with repository.lock():
        for prior in repository.list(market):
            if prior['request_key']==key:
                if prior['fingerprint']!=fingerprint:raise ValueError('commerce_request_key_conflict')
                return prior
            if prior['account']==account and any(i['pid'] in pids and i['state'] in {'selecting','awaiting_verification','result_unknown'} for i in prior['items']):
                raise ValueError('commerce_existing_unknown')
            if prior['state'] in {'queued','running','waiting_account'} and not worker_lost(prior):raise ValueError('commerce_scan_running')
        job={'job_id':'ci_'+uuid4().hex,'market':market,'account':account,'action':'select','state':'queued','created_at':now(),
             'request_key':key,'fingerprint':fingerprint,'error':'','fast_intake':True,'items':[{'pid':pid,'product':available[pid], 'state':'pending','error':''} for pid in pids]}
        repository.save(job)
        if dispatch:
            proc=subprocess.Popen([str(config.ROOT/'.venv/bin/python'),'-m','bdhub.research.commerce_worker','intake',job['job_id']],
                cwd=config.ROOT,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
            job['worker_pid']=proc.pid;repository.save(job)
    return job


def public_intake(job):
    result={k:v for k,v in job.items() if k not in {'fingerprint','request_key','worker_pid','items'}}|{
        'items':[{k:v for k,v in i.items() if k not in {'product','raw'}} for i in job['items']],
        'total':len(job['items']),'counts':dict(Counter(i['state'] for i in job['items'])),
        'processed':sum(i['state'] in {'selected','already_selected','skipped','result_unknown'} for i in job['items']),
        'finished':sum(i['state'] in {'selected','already_selected','skipped'} for i in job['items'])}
    if worker_lost(job):result.update(state='needs_verification',error='commerce_worker_interrupted')
    return result


def verify_intake(job_id,*,store=None,pool=None,factory=commerce_transport):
    repository=store or IntakeStore();pool=pool or PoolStore()
    with repository.lock():
        job=repository.read(job_id)
        snapshot=new_snapshot(job['market'],job['account'],delta=True)
        try:
            with factory(job) as t:
                for item in job['items']:
                    if item['state'] not in {'selecting','awaiting_verification','result_unknown'}:continue
                    rows=selected_rows(t,[item['pid']])
                    rows=[r for r in rows if str(r.get('campaign_info',{}).get('crs_campaign_type')) in {'8','9'}]
                    if rows:
                        for row in rows:remember(snapshot,row)
                        item.update(state='selected',error='',selected_at=now())
                    else:item.update(state='result_unknown',error='尚未确认选入，请核对平台，未重新提交')
            job['state']='needs_verification' if any(i['state']=='result_unknown' for i in job['items']) else 'stopped' if any(i['state']=='pending' for i in job['items']) else 'completed'
        except Exception:
            job.update(state='needs_verification',error='commerce_remote_read_failed')
        if snapshot['products']:
            snapshot.update(state='completed',has_more=False,read_verified_at=now())
            with pool.lock():pool.save(snapshot)
        repository.save(job)
    return job


def run_intake(job_id,*,store=None,pool=None,factory=commerce_transport):
    repository=store or IntakeStore();pool=pool or PoolStore()
    if repository.read(job_id).get('fast_intake'):
        from .commerce_intake_fast import run
        return run(job_id,store=repository,pool=pool,factory=factory)
    with repository.lock():
        job=repository.read(job_id)
        if job['state']!='queued':return job
        job.update(state='running',worker_pid=os.getpid());repository.save(job)
        snapshot=new_snapshot(job['market'],job['account'],delta=True)
        def save(item,state,**fields):
            item.update(state=state,**fields);repository.save(job)
        try:
            with factory(job,allow_write=True) as t:
                existing={}
                targets=[i['pid'] for i in job['items']]
                for offset in range(0,len(targets),100):
                    if (repository.root/f'{job_id}.stop').exists():raise ValueError('commerce_stopped')
                    for row in selected_rows(t,targets[offset:offset+100]):
                        existing.setdefault(str(row['campaign_product']['product_id']),[]).append(row)
                for item in job['items']:
                    if (repository.root/f'{job_id}.stop').exists():raise ValueError('commerce_stopped')
                    pid=item['pid']
                    if existing.get(pid):
                        for row in existing[pid]:remember(snapshot,row)
                        save(item,'already_selected');continue
                    product=item['product']
                    if not recent(product['observed_at']):
                        rows=[p for p in t.opportunity_page(1,global_only=True,pids=[pid])['products'] if str(p.get('product_id'))==pid]
                        if len(rows)!=1:save(item,'skipped',error='全球销售池已无此商品');continue
                        product=safe_product(rows[0],global_only=True)
                    offers=[c for c in t.offers(pid) if str((c.get('campaign') or {}).get('crs_campaign_type'))=='8'
                        and campaign_time_status(c['campaign'])[0]=='ACTIVE']
                    if not offers:save(item,'skipped',error='暂无可选的全球销售活动');continue
                    offers.sort(key=lambda c:(-Decimal(str(c['campaign'].get('commission') or 0)),
                        -int(c['campaign'].get('promotion_end_time') or 0)))
                    entry=normalize_offer(product,offers[0],job['market'],set())
                    cid=entry['offer']['campaign_id']
                    save(item,'selecting',campaign_id=cid,write_attempted=True)
                    result=t.select_product(pid,cid)
                    if result.code!=0 or result.http_status!=200 or result.ambiguous or result.has_turing:
                        from bdhub.send.sharelink.transport import _auth_error,_transient_error
                        if result.http_status==200 and type(result.code) is int and result.code!=0 and not result.ambiguous and not result.has_turing and not _auth_error(result) and not _transient_error(result):
                            save(item,'skipped',error=f'平台拒绝选入，code={result.code}');continue
                        # 无完整回执时先核验，绝不再次提交。
                        save(item,'result_unknown',error='选入结果待核验');break
                    rows=selected_rows(t,[pid])
                    for delay in (1,3,5):
                        if rows:break
                        if (repository.root/f'{job_id}.stop').exists():raise ValueError('commerce_stopped')
                        time.sleep(delay);rows=selected_rows(t,[pid])
                    matched=[row for row in rows if str((row.get('campaign_info') or {}).get('crs_campaign_type')) in {'8','9'}]
                    if not matched:save(item,'result_unknown',error='平台已选商品暂未回读到');break
                    for row in matched:remember(snapshot,row)
                    save(item,'selected',selected_at=now())
            job['state']='needs_verification' if any(i['state']=='result_unknown' for i in job['items']) else 'completed'
        except Exception as exc:
            for item in job['items']:
                if item['state']=='selecting':save(item,'result_unknown',error='写入后中断，需回查')
            job.update(state='needs_verification' if any(i['state']=='result_unknown' for i in job['items']) else 'stopped' if str(exc)=='commerce_stopped' else 'blocked',
                       error=str(exc) if isinstance(exc,ValueError) else 'commerce_operation_interrupted')
        if snapshot['products']:
            snapshot.update(state='completed',has_more=False,read_verified_at=now())
            with pool.lock():pool.save(snapshot)
            job['selected_snapshot_id']=snapshot['job_id']
        repository.save(job)
    return job


def resume_pending(job_id,*,store=None,dispatch=True):
    """仅继续原批次从未提交的商品；已提交、成功和未知记录不改写。"""
    repository=store or IntakeStore()
    with repository.lock():
        job=repository.read(job_id)
        require_capability(job['market'],'product_select')
        if job['state'] not in {'stopped','blocked','needs_verification'}:raise ValueError('commerce_action_invalid')
        if not any(i['state']=='pending' and not i.get('write_attempted') for i in job['items']):raise ValueError('commerce_no_pending_items')
        job.setdefault('resume_history',[]).append({'at':now(),'previous_state':job['state'],'pending':sum(i['state']=='pending' and not i.get('write_attempted') for i in job['items'])})
        (repository.root/f'{job_id}.stop').unlink(missing_ok=True)
        job.update(state='queued',fast_intake=True,error='');repository.save(job)
        if dispatch:
            proc=subprocess.Popen([str(config.ROOT/'.venv/bin/python'),'-m','bdhub.research.commerce_worker','intake',job_id],
                cwd=config.ROOT,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
            job['worker_pid']=proc.pid;repository.save(job)
    return job
