"""GS原生快速选入：列表活动ID直接提交，20个一组回查；未知项永不重发。"""
from collections import Counter
from contextlib import contextmanager
from decimal import Decimal
import os
import re
import time

from bdhub.hub.file_intents import now
from bdhub.send.sharelink.candidates import campaign_time_status
from bdhub.send.sharelink.transport import _auth_error, _transient_error
from .commerce_fast_scan import recent
from .commerce_pool import PoolStore,safe_product
from .commerce_queue import queued_transport
from .commerce_transport import commerce_transport
from .commerce_selected import IntakeStore,new_snapshot,remember,selected_rows
from .commerce_selection_lanes import SelectionLanes

BATCH_SIZE=20
UNCERTAIN={'selecting','awaiting_verification','result_unknown'}


def readback_group_failed(items):
    """单项缺失只隔离；整组成功回执均未得到确认才暂停，防止系统性回读异常。"""
    confirmed=any(i['state'] in {'selected','already_selected'} for i in items)
    accepted_missing=any(i['state']=='result_unknown' and (i.get('receipt') or {}).get('code')==0 for i in items)
    return accepted_missing and not confirmed


def receipt(result):
    return {'http':result.http_status,'code':result.code if type(result.code) is int else None,
            'kind':result.kind,'verification':result.has_turing,'system_error':result.system_error_3}


def run(job_id,*,store=None,pool=None,factory=commerce_transport,max_batches=None,concurrency=2,qps=2):
    repository=store or IntakeStore();pool=pool or PoolStore()
    with repository.lock():
        job=repository.read(job_id)
        if job['state']!='queued':return job
        if any(i['state']=='pending' and i.get('write_attempted') for i in job['items']):
            raise ValueError('commerce_existing_unknown')
        job.update(state='running',worker_pid=os.getpid(),phase='precheck',error='',fast_intake=True)
        started=time.monotonic()
        initial_selected=sum(i['state']=='selected' for i in job['items'])
        initial_unknown=sum(i['state']=='result_unknown' for i in job['items'])
        metrics={'started_at':now(),'stages':{},'verified_new':0,'elapsed_seconds':0,'platform_verifications':0,'platform_verification_successes':0}
        job['metrics']=metrics
        repository.save(job)
        snapshot=new_snapshot(job['market'],job['account'],delta=True)
        published_count=0

        def save():
            metrics.update(elapsed_seconds=round(time.monotonic()-started,3),verified_new=sum(i['state']=='selected' for i in job['items'])-initial_selected,
                           isolated_new=sum(i['state']=='result_unknown' for i in job['items'])-initial_unknown)
            repository.save(job)

        def timed(stage,fn,*args,**kwargs):
            before=time.monotonic()
            try:return fn(*args,**kwargs)
            finally:
                metric=metrics['stages'].setdefault(stage,{'calls':0,'seconds':0})
                metric['calls']+=1;metric['seconds']=round(metric['seconds']+time.monotonic()-before,3)

        def stop_requested():return (repository.root/f'{job_id}.stop').exists()

        def publish():
            nonlocal published_count
            if len(snapshot['products'])>published_count:
                snapshot.update(state='completed',has_more=False,read_verified_at=now())
                with pool.lock():pool.save(snapshot)
                job['selected_snapshot_id']=snapshot['job_id']
                published_count=len(snapshot['products'])

        def verify(t,items):
            """仅查询本组已经提交的PID，哪怕用户停止也完成这组的只读核验。"""
            if not items:return
            job['phase']='verifying';save()
            remaining={i['pid']:i for i in items};isolated={}
            for delay in (0,1,3,5):
                if delay:time.sleep(delay)
                rows=timed('verify',selected_rows,t,list(remaining))
                matched={}
                for row in rows:
                    if str((row.get('campaign_info') or {}).get('crs_campaign_type')) in {'8','9'}:
                        pid=str(row['campaign_product']['product_id']);matched.setdefault(pid,[]).append(row)
                for pid,rows in matched.items():
                    item=remaining.pop(pid)
                    for row in rows:remember(snapshot,row)
                    item.update(state='selected',error='',selected_at=now())
                for pid,item in list(remaining.items()):
                    if (item.get('receipt') or {}).get('code')!=0:
                        isolated[pid]=remaining.pop(pid)
                if not remaining:break
            remaining.update(isolated)
            for item in remaining.values():item.update(state='result_unknown',error='选入未回读确认，已隔离，未重发')
            for item in remaining.values():
                if item.get('platform_verification',{}).get('state')=='passed':
                    item['error']='平台验证已通过；原选入仍未回读，已隔离，未重发'
            publish();save()

        @contextmanager
        def write_factory(value):
            with factory(value,allow_write=True) as transport:yield transport

        pending=[i for i in job['items'] if i['state']=='pending' and not i.get('write_attempted')]
        job['run_scope']={'pending_total':len(pending),'group_size':BATCH_SIZE,'max_batches':max_batches}
        save()
        outstanding=[]
        try:
            with queued_transport(job,repository,write_factory) as t, SelectionLanes(t,stop_requested,concurrency=concurrency,qps=qps) as selector:
                metrics['concurrency']=selector.width;metrics['request_qps']=selector.qps
                for offset in range(0,len(pending),BATCH_SIZE):
                    if max_batches is not None and offset//BATCH_SIZE>=max_batches:break
                    if stop_requested():break
                    chunk=pending[offset:offset+BATCH_SIZE];chunk_started=time.monotonic();outstanding=[]
                    job['phase']='precheck';save()
                    existing={}
                    for row in timed('precheck',selected_rows,t,[i['pid'] for i in chunk]):
                        existing.setdefault(str(row['campaign_product']['product_id']),[]).append(row)
                    uncertain=False;plans=[]
                    for item in chunk:
                        if stop_requested():break
                        pid=item['pid']
                        if pid in existing:
                            for row in existing[pid]:remember(snapshot,row)
                            item.update(state='already_selected',error='');save();continue
                        product=item['product']
                        if not recent(product.get('observed_at')):
                            rows=timed('refresh',t.opportunity_page,1,global_only=True,pids=[pid])['products']
                            exact=[p for p in rows if str(p.get('product_id'))==pid]
                            if len(exact)!=1:item.update(state='skipped',error='全球销售池已无此商品');save();continue
                            product=safe_product(exact[0],global_only=True)
                        cid=str(product.get('campaign_id') or '')
                        if product.get('fs_is_selected') is True:
                            item.update(state='skipped',error='平台列表标记已选，但已选明细未回读到，未重复提交');save();continue
                        cid_source='global_listing'
                        if product.get('global_source') is not True or not re.fullmatch(r'[0-9]{10,32}',cid):
                            offers=[c for c in timed('offers',t.offers,pid) if str((c.get('campaign') or {}).get('crs_campaign_type'))=='8' and campaign_time_status(c['campaign'])[0]=='ACTIVE']
                            if not offers:item.update(state='skipped',error='暂无可选的全球销售活动');save();continue
                            offers.sort(key=lambda c:(-Decimal(str(c['campaign'].get('commission') or 0)),-int(c['campaign'].get('promotion_end_time') or 0)))
                            cid=str(offers[0]['campaign']['campaign_id']);cid_source='campaign_detail'
                        item['selection_source']=cid_source
                        plans.append((item,cid))

                    def begin(pair):
                        if stop_requested():return False
                        job['phase']='selecting'
                        for item,cid in pair:
                            item.update(state='selecting',write_attempted=True,campaign_id=cid,attempted_at=now())
                        save() # 同组每个PID的意图全部落盘后，才发出任何写请求。
                        if stop_requested():
                            for item,_ in pair:item.update(state='pending',write_attempted=False)
                            save();return False
                        return True

                    for outcomes in selector.pairs(plans,begin):
                        challenges=[]
                        for item,cid,lane,result,seconds in outcomes:
                            if not result.dispatched and result.kind=='not_dispatched':
                                item.update(state='pending',write_attempted=False);continue
                            metric=metrics['stages'].setdefault('select',{'calls':0,'seconds':0})
                            metric['calls']+=1;metric['seconds']=round(metric['seconds']+seconds,3)
                            item.update(receipt=receipt(result),select_seconds=seconds)
                            if result.http_status==200 and type(result.code) is int and result.code==0 and not result.ambiguous and not result.has_turing and not result.system_error_3:
                                item.update(state='awaiting_verification',error='');outstanding.append(item)
                            elif result.http_status==200 and type(result.code) is int and result.code!=0 and not result.ambiguous and not result.has_turing and not _auth_error(result) and not _transient_error(result):
                                item.update(state='skipped',error=f'平台拒绝选入，code={result.code}')
                            else:
                                item.update(state='result_unknown',error='选入回执待核验');outstanding.append(item)
                                resolver=getattr(lane,'resolve_selection_verification',None)
                                if result.http_status==200 and type(result.code) is int and result.code==10000 and result.has_turing and not result.ambiguous and callable(resolver):
                                    challenges.append((item,cid,lane))
                                else:uncertain=True
                        save()
                        if challenges and not uncertain and not stop_requested():
                            # pairs在返回前已收齐全部在途结果，验证期间不会再发新的选入。
                            item,cid,lane=challenges[0];job['phase']='platform_verification'
                            item['platform_verification']={'state':'running','started_at':now()}
                            metrics['platform_verifications']+=1;save();auth_start=time.monotonic()
                            try:
                                if timed('platform_verification',lane.resolve_selection_verification,item['pid'],cid) is not True:
                                    raise ValueError('commerce_verification_required')
                                selector.adopt_verified_session(lane)
                            except Exception as exc:
                                item['platform_verification'].update(state='failed',seconds=round(time.monotonic()-auth_start,3))
                                item['platform_verification'].update(getattr(lane,'last_verification',{}))
                                t._verification_failed=True
                                job['error']=str(exc) if isinstance(exc,ValueError) else 'commerce_verification_required'
                                uncertain=True;save()
                            else:
                                item['platform_verification'].update(state='passed',seconds=round(time.monotonic()-auth_start,3),finished_at=now())
                                item['platform_verification'].update(getattr(lane,'last_verification',{}))
                                for other,_,_ in challenges[1:]:
                                    other['platform_verification']={'state':'passed','shared_account_verification':True,'finished_at':now()}
                                metrics['platform_verification_successes']+=1;save()
                                verify(t,outstanding);outstanding=[]
                                uncertain=readback_group_failed(chunk)
                                if uncertain:job['error']='commerce_batch_readback_empty'
                        elif challenges:uncertain=True
                        if uncertain:break
                    verify(t,outstanding)
                    if readback_group_failed(chunk):
                        uncertain=True;job['error']='commerce_batch_readback_empty'
                    publish()
                    job.setdefault('batch_log',[]).append({'at':now(),'count':len(chunk),'seconds':round(time.monotonic()-chunk_started,3),'states':dict(Counter(i['state'] for i in chunk))})
                    save()
                    if uncertain:break
        except Exception as exc:
            for item in outstanding:
                if item['state'] in UNCERTAIN:item.update(state='result_unknown',error='回查中断，未重发')
            for item in job['items']:
                if item['state']=='selecting':item.update(state='result_unknown',error='写入后中断，需回查')
            job['error']=str(exc) if isinstance(exc,ValueError) else 'commerce_operation_interrupted'
        finally:
            for item in job['items']:
                if item['state']=='awaiting_verification':item.update(state='result_unknown',error='选入回查未完成，未重发')
            try:publish()
            except Exception:job['error']='commerce_selected_snapshot_save_failed'
            job['state']='needs_verification' if any(i['state'] in UNCERTAIN for i in job['items']) else 'blocked' if job.get('error') else 'stopped' if any(i['state']=='pending' for i in job['items']) else 'completed'
            job['phase']='finished';metrics['finished_at']=now();save()
    return job
