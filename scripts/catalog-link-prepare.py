#!/usr/bin/env python3
"""Catalog-wide PID link preparation: read existing links, reuse, create only confirmed gaps."""
import argparse,importlib.util,json,sqlite3,sys,time
from contextlib import closing
from decimal import Decimal
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT/'scripts'))
from lib.catalog_links import CatalogLinks,new_commission
from lib.catalog_prepare import (CatalogPreparation,READ_EXTRA,MEMBERS,list_rows,new_offer,classify_pid,
                                 search_cards,scan_lists,read_members,member_facts,TaplinkInventory,
                                 reconcile_from_inventory,reconcile_unknown_batch,reused_card,
                                 needs_standard_reread)
from lib.global_selection import selected_rows,assess
from lib.global_source import clean_product
from lib.global_source_transport import opportunity_reader,opportunity_card_creator_batch,CREATE
from lib.product_stock_policy import full_managed,mark_full_managed,require_stock,unavailable_allowed
from lib.second_cycle import digest
spec=importlib.util.spec_from_file_location('catalog_card_inspection',ROOT/'scripts/prepare-cycle-materials.py');inspection=importlib.util.module_from_spec(spec);spec.loader.exec_module(inspection)
SCOPE={'market':'it','account':'acc9','institution':'bjn-local-research','sourceRun':'it-global-20260914','route':'selected'}
ROUTES=('selected','campaign')
# 非全托读卡用的渠道号：卡清单是 `source=1, campaign_id=<活动>`，成员读也要带 source=1。
CAMPAIGN_SOURCE='1'

def retire_or_block(prep,run_id,pid,cid,src,intent_id,code,*,attempted,retired,blocked,seconds):
    """Retire a local-only frozen intent, or record the block. Never lets a retire failure abort the batch."""
    error=code
    if not attempted and (code=='product_no_longer_eligible' or code.startswith('commercial_facts_changed:')):
        try:
            prep.retire_live_change(run_id,pid,cid,src,intent_id,code)
            retired.append({'pid':pid,'reason':code,'intentId':intent_id,'platformCreateAttempts':0})
            return 'retired'
        except Exception as failure:
            error=code+';retire_failed:'+str(failure)[:200]
    try:prep.mark_progress(run_id,pid,cid,src,'unknown' if attempted else 'missing',error=error)
    except Exception:pass
    blocked.append({'pid':pid,'error':error,'seconds':seconds})
    return 'blocked'

def submit_create_with_verification(transport,payload,pid,report):
    """Submit once; when challenged, solve on the same session and replay this exact request once."""
    def request():
        result=transport._xhr(method='POST',path=CREATE,params=transport._params(),payload=payload,write=True)
        report.setdefault('nativeReceipts',[]).append({'pid':pid,'http':result.http_status,
          'code':result.code if type(result.code) is int else None,'verification':result.has_turing,
          'ambiguous':result.ambiguous})
        return result
    response=request()
    if not response.has_turing:return response
    header=getattr(transport,'_verification_header','')
    if not header:raise ValueError('verification_failed')
    transport._solve_verification(header);transport.verification_successes+=1
    replay=request();report.setdefault('verificationReplays',[]).append({'pid':pid,'replayed':True,
      'http':replay.http_status,'code':replay.code if type(replay.code) is int else None,
      'verification':replay.has_turing})
    if replay.has_turing:raise ValueError('verification_failed')
    return replay

def scope():
    from lib.catalog_links import policy_fingerprint
    policy=json.loads((ROOT/'config/catalog-link-policy.json').read_text())
    return SCOPE|{'policyVersion':policy['version'],'policyFingerprint':policy_fingerprint(policy)}

def plan_bounds(offer):
    """Full binding used by creation, card inspection and the material consumer."""
    return {'pid':offer['pid'],'campaignId':offer['campaignId'],'catalogSource':offer['catalogSource'],'creatorPercent':offer['creatorPercent'],
            'publicPercent':offer['publicPercent'],'totalPercent':offer['totalPercent'],'agencyPercent':offer['agencyPercent'],'title':offer['title'],
            # Carry the full-managed evidence: without it a verification would wrongly re-apply
            # the stock quantity gate that the confirmed policy cancelled for full-managed items.
            'managementType':offer.get('managementType'),'managementEvidenceRef':offer.get('managementEvidenceRef'),
            'planFingerprint':digest({k:offer.get(k) for k in ('pid','campaignId','catalogSource','creatorPercent','publicPercent','totalPercent')})}

def read_selected_pool(transport,max_pages=120):
    """The live selected pool, page by page. No silent partial view: the running total must agree."""
    rows=[];total=None;page=1
    while page<=max_pages:
        data=transport.selected_page(page)
        if total is None:total=data['total']
        elif total!=data['total']:raise ValueError('selected_pool_total_changed')
        rows+=data['items']
        if len(rows)>=total:break
        if not data['items']:raise ValueError('selected_pool_incomplete')
        page+=1
    if total is None or len(rows)!=total:raise ValueError('selected_pool_incomplete')
    return {'total':total,'pages':page,'rows':rows}

def live_plans(transport,prep,pids,pool,diag=None):
    """One eligible live selected plan per PID; campaign binding comes from the platform, not the ledger.

    ``diag`` (optional) receives a precise per-PID reason, so "no plan" is never reported
    as one undifferentiated read failure.
    """
    listing={}
    for start in range(0,len(pids),15):
        group=pids[start:start+15]
        page=transport.opportunity_page(1,global_only=True,pids=group)
        if page['has_more']:raise ValueError('listing_scope_invalid')
        for p in page['products']:
            row=clean_product(p)
            # Official high-opportunity "global only" request is this queue's full-managed evidence;
            # the listing itself returns no campaign_type or stock field.
            row['managementType']='full_managed'
            row['managementEvidenceRef']='opportunity_global_only:'+SCOPE['sourceRun']
            if row['product_id'] in pids:listing[row['product_id']]=row
    if diag is not None:
        for pid in pids:diag[pid]='selected_plan_missing'
    plans={}
    for raw in pool['rows']:
        cp=raw.get('campaign_product') or {};ci=raw.get('campaign_info') or {}
        pid=str(cp.get('product_id'))
        if pid not in pids or str(ci.get('crs_campaign_type')) not in ('8','9'):continue
        if diag is not None:diag[pid]='listing_read_unresolved'
        row=listing.get(pid)
        if not row:continue
        if diag is not None:diag[pid]='product_no_longer_eligible'
        if not assess(row)['eligible']:continue
        if diag is not None:diag[pid]='plan_terms_unresolved'
        try:offer=new_offer(row,pid,str(ci.get('campaign_id')),'selected',policy=prep.policy)
        except ValueError:continue
        plans.setdefault(pid,[]).append(offer)
        if diag is not None:diag[pid]=None
    # One live plan per PID: highest creator share, then later expiry, then campaign id. Bands never mix.
    return {pid:max(rows,key=lambda o:(Decimal(o['creatorPercent']),o['campaignId'])) for pid,rows in plans.items()}

POOL_CACHE_TTL_SECONDS=7200

def selected_pool_cache_path():
    suffix='' if SCOPE['market']=='it' else '-'+SCOPE['market']
    return ROOT/f'var/catalog-selected-pool-cache{suffix}.json'

def selection_db_path():
    return ROOT/('var/global-selection.sqlite' if SCOPE['market']=='it' else f"var/global-selection-{SCOPE['market']}.sqlite")

def pool_cache(report,path=None,*,force_refresh=False):
    """Reuse a recent complete selected-pool read instead of re-paging it for every run."""
    path=Path(path or selected_pool_cache_path())
    if force_refresh:
        report['selectedPoolCache']={'bypassed':'full_pool_seed'}
        return None
    if path.exists():
        cached=json.loads(path.read_text())
        if time.time()-cached.get('readAt',0)<=POOL_CACHE_TTL_SECONDS and type(cached.get('total')) is int and len(cached.get('rows') or [])==cached['total']:
            report['selectedPoolCache']={'readAt':cached['readAt'],'total':cached['total'],'pages':cached.get('pages')}
            return cached
    return None

def step_read(prep,run_id,limit,report,lanes=1,qps=3):
    claimed=prep.claim_read(run_id,limit=limit)
    if not claimed:return {'claimed':0,'states':{}}
    by_pid={}
    for it in claimed:by_pid.setdefault(it['pid'],[]).append(it)
    states={}
    with opportunity_reader(report,market=SCOPE['market'],extra_read_endpoints=READ_EXTRA,wait_seconds=60,account_name=SCOPE['account']) as transport:
        def reader(on,pace=None):
            # One read function per HTTP lane. CommerceTransport never calls _pace, so the
            # shared pacer has to be applied here; without it the lanes would burst unprotected.
            def read(path,extra):
                if pace is not None:pace()
                r=on._xhr(method='GET',path=path,params=on._params()|extra,payload=None,write=False)
                body=on.require_read(r);return body,digest(body)
            return read
        read=reader(transport)
        try:
            pool=pool_cache(report)
            if pool is None:
                pool=read_selected_pool(transport);pool['readAt']=time.time()
                selected_pool_cache_path().write_text(json.dumps(pool,ensure_ascii=False))
            report['selectedPoolTotal']=pool['total'];report['selectedPoolPages']=pool['pages']
        except Exception as error:
            code=str(error) if isinstance(error,ValueError) else 'selected_pool_unreadable'
            for pid in by_pid:
                for it in by_pid[pid]:prep.apply_read(run_id,it['pid'],it['campaign_id'],it['catalog_source'],{'state':'read_incomplete','error':code})
            return {'claimed':len(claimed),'states':{'read_incomplete':len(claimed)},'error':code}
        group_offers={};plan_diag={};failed_pids=set()
        for start in range(0,len(by_pid),15):
            group=list(by_pid)[start:start+15]
            try:group_offers|=live_plans(transport,prep,group,pool,plan_diag)
            except Exception as error:
                code=str(error) if isinstance(error,ValueError) else 'listing_read_unresolved'
                failed_pids.update(group)
                for pid in group:
                    for it in by_pid[pid]:prep.apply_read(run_id,it['pid'],it['campaign_id'],it['catalog_source'],{'state':'read_incomplete','error':code})
                    states['read_incomplete']=states.get('read_incomplete',0)+len(by_pid[pid])
        # Every claimed PID must settle.  A product absent/ineligible in the complete current pool is
        # retired from this run; leaving it as `reading` made the driver loop forever and later looked
        # like an unprocessed link gap (the old run accumulated 1,154 such rows).
        with prep.db:
            for pid,items in by_pid.items():
                if pid in group_offers or pid in failed_pids:continue
                reason=plan_diag.get(pid) or 'selected_plan_missing'
                for item in items:
                    prep.db.execute("UPDATE catalog_prepare_item SET state='retired',blocker=?,error=NULL,"
                                    "lease_until=0,updated=? WHERE run_id=? AND pid=? AND campaign_id=? "
                                    "AND catalog_source=?",(reason,time.time(),run_id,item['pid'],
                                    item['campaign_id'],item['catalog_source']))
                    states['retired']=states.get('retired',0)+1
        targets=[(pid,items) for pid,items in by_pid.items() if pid in group_offers]
        from lib.link_naming import load as load_naming
        naming=load_naming(ROOT,SCOPE['market'])
        standard_specs={pid:create_spec(prep,run_id,offer,short_name_for(pid,offer.get('title','')),naming)
                        for pid,offer in group_offers.items()}
        # Classification is network-bound and dominates the run. Fan it across same-account
        # lanes, but apply every ledger write on this thread: one connection, one writer.
        outcomes=[];lane_transports=[]
        try:
            if lanes>1 and len(targets)>1:
                from concurrent.futures import ThreadPoolExecutor
                from lib.cohort_find import SharedPacer
                pacer=SharedPacer(qps)
                lane_transports=[transport]+[transport.fork_lane(pacer.acquire) for _ in range(lanes-1)]
                lane_reads=[reader(transport,pacer.acquire)]+[reader(on,pacer.acquire) for on in lane_transports[1:]]
                def classify(index,pid):
                    try:return classify_pid(pid,group_offers[pid],lane_reads[index%lanes],prep.policy,standard_specs[pid]),None
                    except Exception as error:return None,error
                with ThreadPoolExecutor(max_workers=lanes) as executor:
                    outcomes=[f.result() for f in [executor.submit(classify,i,pid) for i,(pid,_) in enumerate(targets)]]
            else:
                for pid,_ in targets:
                    try:outcomes.append((classify_pid(pid,group_offers[pid],read,prep.policy,standard_specs[pid]),None))
                    except Exception as error:outcomes.append((None,error))
        finally:
            for on in lane_transports[1:]:
                try:on.session.close()
                except Exception:pass
        report['readLanes']=lanes if lanes>1 else 1
        for (pid,items),(outcome,error) in zip(targets,outcomes):
            offer=group_offers[pid]
            for it in items:
                if offer['campaignId']!=it['campaign_id']:
                    # The live platform binding is authoritative; the stale row is retired explicitly.
                    prep.apply_read(run_id,it['pid'],it['campaign_id'],it['catalog_source'],{'state':'read_incomplete','error':'selected_campaign_changed'})
                    states['read_incomplete']=states.get('read_incomplete',0)+1;continue
                if error is not None:
                    code=str(error) if isinstance(error,ValueError) else 'card_read_unresolved'
                    prep.apply_read(run_id,it['pid'],it['campaign_id'],it['catalog_source'],{'state':'read_incomplete','error':code,'listing':offer['listing']})
                    states['read_incomplete']=states.get('read_incomplete',0)+1;continue
                outcome=dict(outcome)
                outcome['listing']={'product_id':pid,'title':offer['title'],'creatorPercent':offer['creatorPercent'],'publicPercent':offer['publicPercent'],'totalPercent':offer['totalPercent'],'agencyPercent':offer['agencyPercent'],'planFingerprint':plan_bounds(offer)['planFingerprint'],'managementType':offer.get('managementType'),'managementEvidenceRef':offer.get('managementEvidenceRef')}
                prep.apply_read(run_id,it['pid'],it['campaign_id'],it['catalog_source'],outcome)
                states[outcome['state']]=states.get(outcome['state'],0)+1
    return {'claimed':len(claimed),'states':states}

def fresh_offers(transport,prep,pids,now=None):
    """Current global-only plan rows for the given PIDs; the campaign binding comes from the intent.

    Used to re-validate a frozen plan immediately before creating, so a platform-side change is
    caught instead of silently creating a link on stale commercial terms.
    """
    pids=list(pids);listing={}
    for start in range(0,len(pids),15):
        page=transport.opportunity_page(1,global_only=True,pids=pids[start:start+15])
        if page['has_more']:raise ValueError('listing_scope_invalid')
        for p in page['products']:
            row=clean_product(p)
            row['managementType']='full_managed'
            row['managementEvidenceRef']='opportunity_global_only:'+SCOPE['sourceRun']
            if row['product_id'] in pids:listing[row['product_id']]=row
    return {pid:listing[pid] for pid in pids if pid in listing and assess(listing[pid])['eligible']}

def step_inventory(report,lanes=1,qps=3,limit=None):
    """Enumerate every TapLink once (lists + members). Read-only: no create, no delete.

    This is the shared base for both link reconciliation and link maintenance: the per-PID
    search is replaced by one account-wide inventory that is reused for every later join.
    """
    inv=TaplinkInventory(ROOT);states={}
    try:
        with opportunity_reader(report,market=SCOPE['market'],extra_read_endpoints=READ_EXTRA,wait_seconds=60,account_name=SCOPE['account']) as transport:
            def reader(on,pace=None):
                def read(path,extra):
                    if pace is not None:pace()
                    r=on._xhr(method='GET',path=path,params=on._params()|extra,payload=None,write=False)
                    body=on.require_read(r);return body,digest(body)
                return read
            total,rows=scan_lists(reader(transport))
            report['inventoryLists']=total
            for row in rows:inv.save_list(row)
            todo=inv.lists_pending_members()
            if limit is not None:todo=todo[:limit]
            report['inventoryPending']=len(todo)
            lane_transports=[]
            try:
                if lanes>1 and len(todo)>1:
                    from concurrent.futures import ThreadPoolExecutor,as_completed
                    from lib.cohort_find import SharedPacer
                    pacer=SharedPacer(qps)
                    lane_transports=[transport]+[transport.fork_lane(pacer.acquire) for _ in range(lanes-1)]
                    lane_reads=[reader(transport,pacer.acquire)]+[reader(on,pacer.acquire) for on in lane_transports[1:]]
                    def fetch(index,row):
                        try:return row,read_members(lane_reads[index%lanes],row['list_id']),None
                        except Exception as error:return row,None,error
                    with ThreadPoolExecutor(max_workers=lanes) as executor:
                        # Persist as each list completes: a crash must not lose finished work.
                        for future in as_completed([executor.submit(fetch,i,row) for i,row in enumerate(todo)]):
                            row,members,error=future.result()
                            if error is not None:
                                code=str(error)[:40] if isinstance(error,ValueError) else 'taplink_members_unresolved'
                                states[code]=states.get(code,0)+1;continue
                            inv.save_members(row['list_id'],row['name'],members)
                            states['read']=states.get('read',0)+1
                else:
                    base=reader(transport)
                    for row in todo:
                        try:members=read_members(base,row['list_id'])
                        except Exception as error:
                            code=str(error)[:40] if isinstance(error,ValueError) else 'taplink_members_unresolved'
                            states[code]=states.get(code,0)+1;continue
                        inv.save_members(row['list_id'],row['name'],members)
                        states['read']=states.get('read',0)+1
            finally:
                for on in lane_transports[1:]:
                    try:on.session.close()
                    except Exception:pass
        report['inventory']=inv.summary();report['inventoryStates']=states
    finally:inv.close()
    return {'lists':report.get('inventoryLists'),'states':states}

def stored_offer(item):
    """Rebuild the plan facts already stored on the ledger row, if any.

    A mass re-read of every plan is both slow and unreliable, so the reconcile reuses
    what it already knows and only reads plans it has never seen. Creation still
    re-validates the live facts before writing anything.
    """
    data=json.loads(item['listing']) if item.get('listing') else None
    if not data or not data.get('creatorPercent'):return None
    def raw(value):
        if value in (None,''):return None
        try:return str(int(Decimal(str(value))*100))
        except Exception:return None
    return {'pid':str(item['pid']),'campaignId':str(item['campaign_id']),'catalogSource':item['catalog_source'],
            'title':data.get('title'),'creatorPercent':data.get('creatorPercent'),
            'publicPercent':data.get('publicPercent'),'totalPercent':data.get('totalPercent'),
            'agencyPercent':data.get('agencyPercent'),'managementType':data.get('managementType'),
            'managementEvidenceRef':data.get('managementEvidenceRef'),'planSource':'stored',
            'stats':{'totalRaw':raw(data.get('totalPercent')),'publicRaw':raw(data.get('publicPercent')),
                     'creatorRaw':raw(data.get('creatorPercent')),'agencyRaw':raw(data.get('agencyPercent'))}}

def step_reconcile(prep,inv,run_id,report,limit=None):
    """Judge every queued plan from the cached inventory plus the plan facts.

    Live plans are read only for rows that never had one: re-reading all of them is slow
    and the platform starts returning empty pages under a long uninterrupted run.
    """
    rows=[dict(r) for r in prep.db.execute("SELECT * FROM catalog_prepare_item WHERE run_id=? AND state NOT IN ('retired','ready')",(run_id,))]
    if limit:rows=rows[:limit]
    offers={}
    for item in rows:
        cached=stored_offer(item)
        if cached:offers[str(item['pid'])]=cached
    todo=sorted({str(r['pid']) for r in rows if str(r['pid']) not in offers})
    report['reconcileItems']=len(rows);report['reconcileFromStored']=len(offers);report['reconcilePlansToRead']=len(todo)
    diag={}
    if todo:
        with opportunity_reader(report,market=SCOPE['market'],wait_seconds=60,account_name=SCOPE['account']) as transport:
            pool=pool_cache(report)
            if pool is None:
                pool=read_selected_pool(transport);pool['readAt']=time.time()
                selected_pool_cache_path().write_text(json.dumps(pool,ensure_ascii=False))
            report['selectedPoolTotal']=pool['total']
            for start in range(0,len(todo),15):
                try:offers|=live_plans(transport,prep,todo[start:start+15],pool,diag)
                except Exception as error:
                    report.setdefault('reconcilePlanErrors',[]).append(str(error)[:60])
    return reconcile_from_inventory(prep,inv,rows,offers,diag)

def requeue_cardless(prep,route):
    """Requeue every legacy reuse/review decision under the standard-card policy."""
    src='campaign' if route=='campaign' else 'selected'
    with prep.db:
        rows=prep.db.execute("SELECT run_id,pid,campaign_id,catalog_source,state,card FROM catalog_prepare_item "
                             "WHERE catalog_source=? AND state IN ('review','reuse','missing')",(src,)).fetchall()
        n=0
        for row in rows:
            if not needs_standard_reread(row['state'],row['card']):continue
            n+=prep.db.execute("UPDATE catalog_prepare_item SET state='pending',blocker=NULL,error=NULL,lease_until=0,"
                               "updated=? WHERE run_id=? AND pid=? AND campaign_id=? AND catalog_source=?",
                               (time.time(),row['run_id'],row['pid'],row['campaign_id'],row['catalog_source'])).rowcount
    return n


def requeue_binding_mismatches(prep,run_id):
    """A ready row whose current offer fingerprint moved must be re-read, not shown as complete."""
    from lib.catalog_binding import CatalogBindings
    bindings=CatalogBindings(ROOT,connection=prep.db);changed=0
    with prep.db:
        rows=prep.db.execute("SELECT * FROM catalog_prepare_item WHERE run_id=? AND state='ready'",(run_id,)).fetchall()
        for row in rows:
            try:listing=json.loads(row['listing']) if row['listing'] else {};offer=listing|{'pid':row['pid'],'campaignId':row['campaign_id'],'catalogSource':row['catalog_source']}
            except (TypeError,ValueError):continue
            raw=bindings.get(SCOPE['market'],row['catalog_source'],row['pid'],row['campaign_id'])
            if not raw or bindings.active_for_offer(offer,market=SCOPE['market']):continue
            changed+=prep.db.execute("UPDATE catalog_prepare_item SET state='pending',blocker='catalog_link_terms_changed',"
                                     "error=NULL,lease_until=0,updated=? WHERE run_id=? AND pid=? AND campaign_id=? "
                                     "AND catalog_source=? AND state='ready'",(time.time(),run_id,row['pid'],
                                     row['campaign_id'],row['catalog_source'])).rowcount
    return changed


def step_read_campaign(prep,run_id,limit,report):
    """非全托读卡：按活动读一次卡清单（`source=1, campaign_id=<活动>`），再用池子的事实判定。

    非全托**没有账号级卡清单**可读——卡片就是挂在活动上的，所以先按活动分组、每个活动读一次。
    判定用的商业事实取自池子（快照 + 当前规则重算的佣金），不另读一遍商品：建链之前还会再实时重验一次，
    所以这里快一点的代价是可控的。
    """
    claimed=prep.claim_read(run_id,limit=limit)
    block=campaign_block()
    offers={t['pid']:t['offer'] for t in block['targets']}
    report['campaignTargets']=len(block['targets']);report['campaignSkipped']=block.get('skipped')
    campaigns=sorted({str(it['campaign_id']) for it in claimed})
    inv=TaplinkInventory(ROOT);failed={}
    try:
        if campaigns:
            with opportunity_reader(report,market=SCOPE['market'],extra_read_endpoints=READ_EXTRA,wait_seconds=60,account_name=SCOPE['account']) as transport:
                def read(path,extra):
                    r=transport._xhr(method='GET',path=path,params=transport._params()|extra,payload=None,write=False)
                    body=transport.require_read(r);return body,digest(body)
                cards={}
                run_created=prep.db.execute('SELECT created FROM catalog_prepare_run WHERE id=?',(run_id,)).fetchone()[0]
                for cid in campaigns:
                    try:
                        cached=inv.scan(SCOPE['market'],CAMPAIGN_SOURCE,cid)
                        if cached and cached['observed']>=run_created:
                            cards[cid]=cached['total'];continue
                        total,rows=scan_lists(read,source=CAMPAIGN_SOURCE,campaign_id=cid)
                        for row in rows:
                            inv.save_list(row,source=CAMPAIGN_SOURCE,campaign_id=cid)
                            inv.save_members(row['list_id'],row.get('name'),
                                             read_members(read,row['list_id'],source=CAMPAIGN_SOURCE))
                        inv.save_scan(SCOPE['market'],CAMPAIGN_SOURCE,cid,total);cards[cid]=total
                    except Exception as error:
                        # 一个活动读不到不能把别的活动也拖下水：只给它自己的行记原因。
                        failed[cid]=str(error) if isinstance(error,ValueError) else f'{type(error).__name__}:{str(error)[:60]}'
            report['campaignCards']=cards
        report['campaignReadFailures']=failed
        # 即使这次没有领到新行也照常复判：卡清单是缓存的，判定读的是缓存 + 池子事实，
        # 所以"修好判定再跑一次"必须能生效，而不是因为没有 pending 就什么都不做。
        rows=[dict(r) for r in prep.db.execute("SELECT * FROM catalog_prepare_item WHERE run_id=? AND state NOT IN ('retired','ready')",(run_id,))]
        for item in rows:
            code=failed.get(str(item['campaign_id']))
            if code:
                prep.apply_read(run_id,item['pid'],item['campaign_id'],item['catalog_source'],
                                {'state':'read_incomplete','error':'campaign_cards_unreadable:'+code[:120]})
        readable=[item for item in rows if str(item['campaign_id']) not in failed]
        states=reconcile_from_inventory(prep,inv,readable,offers)
        for cid in failed:states['read_incomplete']=states.get('read_incomplete',0)+1
        return {'claimed':len(claimed),'states':states,'campaigns':len(campaigns)}
    finally:inv.close()

def step_read_campaign_direct(prep,run_id,limit,report,lanes=1,qps=3):
    """Market-scoped PID search; reads only cards that can belong to the claimed products."""
    claimed=prep.claim_read(run_id,limit=limit)
    if not claimed:return {'claimed':0,'states':{}}
    block=campaign_block();offers={target['pid']:target['offer'] for target in block['targets']};states={}
    from concurrent.futures import ThreadPoolExecutor
    from lib.cohort_find import SharedPacer
    with opportunity_reader(report,market=SCOPE['market'],extra_read_endpoints=READ_EXTRA,wait_seconds=60,account_name=SCOPE['account']) as transport:
        pacer=SharedPacer(qps)
        lane_transports=[transport]+[transport.fork_lane(pacer.acquire) for _ in range(max(0,lanes-1))]
        def reader(on):
            def read(path,extra):
                pacer.acquire();response=on._xhr(method='GET',path=path,params=on._params()|extra,payload=None,write=False)
                body=on.require_read(response);return body,digest(body)
            return read
        reads=[reader(on) for on in lane_transports]
        def inspect(index,item):
            offer=offers.get(str(item['pid']))
            if not offer:return item,None,ValueError('campaign_plan_missing')
            try:return item,classify_pid(item['pid'],offer,reads[index%len(reads)],prep.policy),None
            except Exception as error:return item,None,error
        try:
            with ThreadPoolExecutor(max_workers=max(1,lanes)) as executor:
                outcomes=[future.result() for future in [executor.submit(inspect,index,item) for index,item in enumerate(claimed)]]
        finally:
            for lane in lane_transports[1:]:
                try:lane.session.close()
                except Exception:pass
    for item,outcome,error in outcomes:
        if error is not None:
            code=str(error) if isinstance(error,ValueError) else type(error).__name__
            prep.apply_read(run_id,item['pid'],item['campaign_id'],item['catalog_source'],{'state':'read_incomplete','error':code})
            states['read_incomplete']=states.get('read_incomplete',0)+1;continue
        outcome=dict(outcome);outcome['listing']=offers[str(item['pid'])]
        prep.apply_read(run_id,item['pid'],item['campaign_id'],item['catalog_source'],outcome)
        states[outcome['state']]=states.get(outcome['state'],0)+1
    return {'claimed':len(claimed),'states':states,'mode':'pid_search'}

def campaign_fresh_offers(transport,offers,at=None):
    """建链前实时重验非全托商业事实：按活动翻一遍商品列表，只挑我们关心的 PID。

    目的与全托侧完全一样——**不是为了再读一遍，而是让平台侧的改动在写入之前暴露出来**。
    按活动分组而不是按 PID：一个活动的商品列表翻一页就够（实测 3,622 offer / 43 个活动），
    逐个 PID 重读会把同一页读几十遍。
    """
    from lib.cycle_catalog import normalize,commission_rule,commission_calculator,CAMPAIGNS,PRODUCTS,_total
    from lib.second_cycle import assess_offer
    rule=commission_rule(ROOT);calculator=commission_calculator(rule)
    at=at if at is not None else time.time()
    wanted={}
    for offer in offers:wanted.setdefault(str(offer['campaignId']),set()).add(str(offer['pid']))
    def request(method,path,params,payload=None):
        r=transport._xhr(method=method,path=path,params=transport._params()|params,payload=payload,write=False)
        return transport.require_read(r),digest(r.payload)
    meta={}
    for page in range(1,6):
        body,_=request('GET',CAMPAIGNS,{'campaign_join_status_category':'1','crs_campaign_types':'','cur_page':page,'page_size':100})
        data=body.get('data') or {}
        for row in data.get('campaign') or []:
            cid=str(row.get('campaign_id'))
            if cid in wanted:meta[cid]=row
        if page*100>=_total(data,'total_num'):break
    fresh={}
    for cid,pids in wanted.items():
        campaign=meta.get(cid)
        if not campaign:continue
        found=set()
        for page in range(1,21):
            body,sha=request('GET',PRODUCTS,{'campaign_id':cid,'marked':'0' if str(campaign.get('crs_campaign_type'))=='7' else 'false','cur_page':page,'page_size':100})
            data=body.get('data') or {};rows=data.get('campaign_product') or []
            for row in rows:
                pid=str(row.get('product_id'))
                if pid in pids and pid not in found:
                    found.add(pid)
                    fresh[pid]=normalize(row,campaign,'campaign',rule,calculator,sha,at)
            if len(found)==len(pids) or page*100>=_total(data,'total_num'):break
    # 资格用与池子同一个闸门复判：平台把商品改下线/降佣/改期限，都在这里被挡下。
    return {pid:offer for pid,offer in fresh.items() if assess_offer(offer,at)['eligible']}

def step_verify(prep,report,limit=None):
    """Read back intents that already reached the platform. Never creates, never deletes.

    An intent whose POST returned but whose card could not be confirmed stays in the ledger
    with its receipt; reading it back is the only correct way to resolve it.
    """
    rows=[dict(r) for r in prep.db.execute("SELECT * FROM catalog_prepare_item WHERE state IN ('unknown','submitted') AND intent_id IS NOT NULL ORDER BY updated LIMIT ?",(limit or 500,))]
    report['verifyItems']=len(rows);states={}
    if not rows:return states
    ledger=CatalogLinks(ROOT)
    try:
        with opportunity_reader(report,market=SCOPE['market'],extra_read_endpoints=READ_EXTRA,wait_seconds=60,account_name=SCOPE['account']) as transport:
            def read(path,extra):
                r=transport._xhr(method='GET',path=path,params=transport._params()|extra,payload=None,write=False)
                body=transport.require_read(r);return body,digest(body)
            for item in rows:
                pid,cid,src=item['pid'],item['campaign_id'],item['catalog_source']
                intent=ledger.get(item['intent_id'])
                if intent['state']=='verified':
                    prep.mark_progress(item['run_id'],pid,cid,src,'ready',card=intent['readback'])
                    states['already_verified']=states.get('already_verified',0)+1;continue
                receipt=intent.get('receipt') or {}
                # Intents frozen before the marker was carried still verify correctly: take the
                # full-managed evidence from the ledger row so the stock gate stays cancelled.
                listing=json.loads(item['listing']) if item.get('listing') else {}
                offer=intent['spec']['offer']
                # 只有全托这条队列准备官方全托来源，才在这里补上数量门槛的取消证据；
                # 非全托不能被冒充成全托（那会连带把库存门槛也取消掉）。
                if (intent['spec'].get('route') or 'selected')=='selected' and not full_managed(offer) and listing.get('managementEvidenceRef'):
                    offer=mark_full_managed(offer,listing['managementEvidenceRef'])
                try:
                    card=None
                    for delay in (0,1,3):
                        if delay:time.sleep(delay)
                        card=inspection.inspect_card(offer,read,expected_list_id=receipt.get('list_id'),expected_name=intent['spec']['listName'])
                        if card['state']=='verified_read_only':break
                    if not card or card['state']!='verified_read_only':raise ValueError('created_card_not_verified')
                    card.update(readAccount=SCOPE['account'])
                    ledger.confirm(intent['id'],card)
                    prep.mark_progress(item['run_id'],pid,cid,src,'ready',card=card)
                    states['verified']=states.get('verified',0)+1
                except Exception as error:
                    # Still unresolved: stay unknown, keep the receipt, never re-create.
                    code=str(error) if isinstance(error,ValueError) else f'{type(error).__name__}:{str(error)[:60]}'
                    prep.mark_progress(item['run_id'],pid,cid,src,'unknown',error=code)
                    states['still_unknown']=states.get('still_unknown',0)+1
    finally:ledger.db.close()
    return states

def verify_created_card(read,intent,receipt):
    """Read back a just-created card by its receipt list_id. Returns the card, or None.

    Cheaper than a full search: the receipt already names the list to read, and the frozen
    intent says exactly what that card must contain.
    """
    spec=intent['spec'];offer=spec['offer'];route=spec.get('route') or 'selected'
    source=2 if route=='selected' else 1
    wire='0' if route=='selected' else str(spec['campaignId'])
    list_id=str(receipt.get('list_id') or '')
    if not list_id.isdigit():raise ValueError('catalog_receipt_list_missing')
    body,sha=read(MEMBERS,{'list_id':list_id,'source':source})
    data=body.get('data') if isinstance(body,dict) else None
    rows=list_rows(data,'total_num','campaign_products')
    if rows is None or data.get('total_num')!=len(rows):raise ValueError('card_members_incomplete')
    wanted=str(spec['campaignId']);pid=str(spec['pid'])
    # 非全托卡片的成员行不带活动号（活动号在卡本身），所以这里必须容忍 None。
    member=next((p for p in rows if str(p.get('product_id'))==pid
                 and (str(p.get('campaign_id'))==wanted or route!='selected' and p.get('campaign_id') is None)),None)
    if member is None:return None
    raw=member.get('creator_commission_percent')
    if raw is None:return None
    rate=Decimal(str(raw))/100
    if rate!=Decimal(str(spec['creatorPercent'])):return None
    if str(member.get('product_status'))!='2' or member.get('is_under_governed') is True:return None
    if route=='selected':
        # 这条队列准备的是官方全托来源，所以在这里取消数量门槛；非全托保留自己的库存规则。
        merged=offer|{'managementType':offer.get('managementType') or 'full_managed','managementEvidenceRef':offer.get('managementEvidenceRef') or 'catalog-prepare:full_managed_source'}
    else:
        merged=offer
    if not unavailable_allowed(member.get('unavailable_type'),merged):return None
    needs_stock=require_stock(merged)
    if needs_stock and (member.get('stock') is None or Decimal(str(member.get('stock')))<=100):return None
    public=member.get('plan_commission_percent')
    return {'state':'verified_read_only','pid':pid,'verifiedListName':spec['listName'],'listName':spec['listName'],
            'campaignName':'','stock':str(member.get('stock')) if needs_stock else None,'stockRequired':needs_stock,
            'publicPercent':format(Decimal(str(public))/100,'f') if public is not None else None,
            'listId':list_id,'wireCampaignId':wire,'sourceCampaignId':wanted,'creatorPercent':format(rate,'f'),
            'checkedAt':time.time(),'evidenceRefs':[sha],'executionAllowed':False,'readAccount':SCOPE['account']}

def create_spec(prep,run_id,offer,short_name,naming=None,route=None):
    """Freeze one creation intent. The name comes from the frontend-controllable naming config."""
    from lib.link_naming import load as load_naming,fingerprint as naming_fingerprint,name_for
    naming=naming or load_naming(ROOT,SCOPE['market'])
    route=route or SCOPE['route']
    campaign=offer['campaignId'];pid=offer['pid']
    rendered=name_for(ROOT,pid=pid,campaign=campaign,creator_percent=offer['creatorPercent'],
                      short_name=short_name,public_percent=offer.get('publicPercent'),
                      total_percent=offer.get('totalPercent'),market=SCOPE['market'],config=naming)
    name=rendered['name']
    # 载荷由生成器按渠道给出，不在这里手拼：两条渠道的形状不同（非全托没有 source、活动在顶层）。
    from bdhub.send.taplink.protocol import create_payload
    payload=create_payload(pid=pid,campaign_id=campaign,creator_pct=offer['creatorPercent'],name=name,route=route)
    from lib.catalog_links import policy_fingerprint
    return {'market':SCOPE['market'],'account':SCOPE['account'],'route':route,'purpose':'catalog_batch_link','sourceRun':run_id,'pid':pid,'campaignId':campaign,
            'creatorPercent':offer['creatorPercent'],'listName':name,'shortName':rendered['shortName'],
            'policyVersion':prep.policy['version'],'policyFingerprint':policy_fingerprint(prep.policy),'searchTotal':0,'standardSearchComplete':True,
            'namingVersion':naming['version'],'namingFingerprint':naming_fingerprint(naming),
            'offer':plan_bounds(offer)|{'observedAt':time.time()},'payload':payload,'preparedAt':time.time()}

def short_name_for(pid,title):
    # Shared with the naming preview so what the operator sees is what gets created.
    from lib.link_naming import short_name_for as cached_short_name
    return cached_short_name(ROOT,pid,title,SCOPE['market'])

def step_create(prep,run_id,limit,report,pace=0.0,lanes=1,qps=5,canary=False,pids=None):
    """Freeze a batch locally, then create it under ONE account session.

    Establishing the ACC9 session (profile lock, cookies, signer) is the dominant fixed cost of
    a creation, so a batch shares one session. Safety is unchanged: every write must equal the
    frozen request of its own product, and each product is written at most once per session.
    """
    created=[];blocked=[];retired=[];timings=[]
    # Pin the naming config once per run: a template edited mid-run must not split the batch.
    from lib.link_naming import load as load_naming
    naming=load_naming(ROOT,SCOPE['market'])
    report['namingVersion']=naming['version']
    ledger=CatalogLinks(ROOT)
    work=[]
    try:
        # Phase 1: local only. Claim and freeze; nothing here touches the platform.
        for _ in range(limit):
            item=prep.claim_create(run_id,pids=pids)
            if not item:break
            pid,cid,src=item['pid'],item['campaign_id'],item['catalog_source']
            try:
                listing=item['listing'] or {}
                if item['state']=='missing':
                    offer=listing|{'pid':pid,'campaignId':cid,'catalogSource':src}
                    if not offer.get('creatorPercent'):raise ValueError('catalog_prepare_offer_missing')
                    from lib.catalog_binding import CatalogBindings
                    current=CatalogBindings(ROOT,connection=prep.db).active_for_offer(offer,market=SCOPE['market'])
                    if current:
                        ledger.supersede_redundant_prepared()
                        prep.mark_progress(run_id,pid,cid,src,'ready',card=current['card'])
                        created.append({'pid':pid,'state':'existing_current_binding','seconds':0.0});continue
                    planned=create_spec(prep,run_id,offer,short_name_for(pid,offer.get('title','')),naming)
                    observed=item.get('card') or {}
                    if observed.get('state')=='verified_read_only' and \
                            str(observed.get('verifiedListName') or observed.get('listName'))==planned['listName'] and \
                            str(observed.get('creatorPercent'))==str(planned['creatorPercent']) and \
                            str(observed.get('pid'))==str(pid) and str(observed.get('sourceCampaignId'))==str(cid):
                        from lib.catalog_binding import CatalogBindings
                        CatalogBindings(ROOT,connection=prep.db).promote(planned,observed,None)
                        prep.mark_progress(run_id,pid,cid,src,'ready',card=observed)
                        created.append({'pid':pid,'state':'existing_standard','listId':observed['listId'],
                                        'creatorPercent':observed['creatorPercent'],'seconds':0.0});continue
                    intent=prep.freeze(run_id,pid,cid,src,planned)
                else:
                    intent=ledger.get(item['intent_id'])
                if intent['state']=='verified':
                    prep.mark_progress(run_id,pid,cid,src,'ready',card=intent['readback'])
                    created.append({'pid':pid,'state':'already_verified','seconds':0.0});continue
                if intent['state']!='prepared':raise ValueError('catalog_intent_requires_verification')
                work.append((item,intent))
            except Exception as error:
                code=str(error) if isinstance(error,ValueError) else f'{type(error).__name__}:{str(error)[:80]}'
                try:prep.mark_progress(run_id,pid,cid,src,'missing',error=code)
                except Exception:pass
                blocked.append({'pid':pid,'error':code,'seconds':0.0})
                prep.release(run_id,pid,cid,src)
        report['createFrozen']=len(work)
        if not work:return {'created':created,'blocked':blocked,'retired':retired,'timings':timings,'paceSeconds':pace}
        # Phase 2: one session. Every read is done up front in batch/parallel, so the write
        # loop is left with exactly two serial requests per product (write + member readback).
        payloads={str(intent['spec']['pid']):intent['spec']['payload'] for _,intent in work}
        # 一条 run 只有一条渠道，所以整批共用。（意图本身各自带 route，写载荷仍逐条核对。）
        route=SCOPE['route']
        # 非全托在写之前要按活动重验商业事实，那两次只读必须在这里声明——
        # 少了它，整条创建路径会在发出任何请求之前就被 TapLinkError('taplink_endpoint_not_allowed') 挡下。
        from lib.global_source_transport import CAMPAIGN_OFFER_READS
        extra_reads=CAMPAIGN_OFFER_READS if route=='campaign' else frozenset()
        with opportunity_card_creator_batch(report,payloads,market=SCOPE['market'],canary=canary,
                                             stopped=lambda:False,wait_seconds=60,extra_reads=extra_reads) as transport:
            def reader(on):
                def read(path,extra):
                    r=on._xhr(method='GET',path=path,params=on._params()|extra,payload=None,write=False)
                    body=on.require_read(r);return body,digest(body)
                return read
            read=reader(transport)
            pids=[str(intent['spec']['pid']) for _,intent in work]
            intents_by_pid={str(intent['spec']['pid']):intent for _,intent in work}
            t=time.time()
            if route=='campaign':
                fresh_all=campaign_fresh_offers(transport,[intent['spec']['offer'] for _,intent in work],time.time())
            else:
                fresh_all=fresh_offers(transport,prep,pids,time.time())
            report.setdefault('phaseSeconds',{})['plans']=round(time.time()-t,2)
            # Card pre-search for the whole batch across read lanes: a product that already has a
            # card is blocked before any write, and the write loop keeps no search of its own.
            t=time.time();preflight={}
            from concurrent.futures import ThreadPoolExecutor
            from lib.cohort_find import SharedPacer
            pacer=SharedPacer(qps)
            lane_transports=[transport]+[transport.fork_lane(pacer.acquire) for _ in range(max(0,lanes-1))]
            try:
                lane_reads=[read]+[reader(on) for on in lane_transports[1:]]
                def look(index,pid):
                    try:
                        intent=intents_by_pid[pid];source=intent['spec'].get('route') or 'selected'
                        raw=fresh_all.get(pid)
                        if not raw:raise ValueError('product_no_longer_eligible')
                        current=(raw if source=='campaign' else new_offer(raw,pid,intent['spec']['campaignId'],'selected',policy=prep.policy))
                        outcome=classify_pid(pid,current,lane_reads[index%max(1,lanes)],prep.policy,intent['spec'])
                        return pid,outcome,None
                    except Exception as error:return pid,None,error
                with ThreadPoolExecutor(max_workers=max(1,lanes)) as executor:
                    for pid,outcome,error in (f.result() for f in [executor.submit(look,i,p) for i,p in enumerate(pids)]):
                        preflight[pid]=(outcome,error)
            finally:
                for on in lane_transports[1:]:
                    try:on.session.close()
                    except Exception:pass
            report.setdefault('phaseSeconds',{})['searches']=round(time.time()-t,2)
            # Readback runs on its own lane so it can overlap the next creation.
            rb_lane=transport.fork_lane(lambda:None)
            rb_read=reader(rb_lane)
            rb_pool=ThreadPoolExecutor(max_workers=1)
            def rb_lane_close():
                try:rb_lane.session.close()
                except Exception:pass
            def readback_once(target_intent,target_receipt):
                for delay in (0,1,2):
                    if delay:time.sleep(delay)
                    try:card=verify_created_card(rb_read,target_intent,target_receipt)
                    except Exception:card=None
                    if card:return card
                return None
            pending=None
            def finalize(entry):
                pitem,pintent,preceipt,fut,pt0=entry
                ppid,pcid,psrc=pitem['pid'],pitem['campaign_id'],pitem['catalog_source']
                try:
                    card=fut.result()
                    if not card:raise ValueError('created_card_not_verified')
                    ledger.confirm(pintent['id'],card)
                    prep.mark_progress(pitem['run_id'],ppid,pcid,psrc,'ready',card=card)
                    created.append({'pid':ppid,'state':'verified','listId':card['listId'],'creatorPercent':card['creatorPercent'],'seconds':round(time.time()-pt0,2)})
                except Exception as error:
                    code=str(error) if isinstance(error,ValueError) else f'{type(error).__name__}:{str(error)[:80]}'
                    try:prep.mark_progress(pitem['run_id'],ppid,pcid,psrc,'unknown',error=code)
                    except Exception:pass
                    blocked.append({'pid':ppid,'error':code,'seconds':round(time.time()-pt0,2)})
            remaining=[]
            for item,intent in work:
                pid=item['pid'];outcome,error=preflight.get(pid,(None,ValueError('card_search_unresolved')))
                if error is not None:
                    code=str(error) if isinstance(error,ValueError) else f'{type(error).__name__}:{str(error)[:80]}'
                    if retire_or_block(prep,run_id,pid,item['campaign_id'],item['catalog_source'],intent['id'],code,
                                       attempted=False,retired=retired,blocked=blocked,seconds=0.0)=='retired':continue
                    prep.release(run_id,pid,item['campaign_id'],item['catalog_source']);continue
                if outcome.get('state')=='standard':
                    ledger.confirm_existing_standard(intent['id'],outcome['card'])
                    prep.mark_progress(run_id,pid,item['campaign_id'],item['catalog_source'],'ready',card=outcome['card'])
                    created.append({'pid':pid,'state':'existing_standard','listId':outcome['card']['listId'],
                                    'creatorPercent':outcome['card']['creatorPercent'],'seconds':0.0})
                    prep.release(run_id,pid,item['campaign_id'],item['catalog_source']);continue
                if outcome.get('state')!='missing':
                    prep.mark_progress(run_id,pid,item['campaign_id'],item['catalog_source'],'missing',error='catalog_standard_search_unresolved')
                    blocked.append({'pid':pid,'error':'catalog_standard_search_unresolved','seconds':0.0});prep.release(run_id,pid,item['campaign_id'],item['catalog_source']);continue
                remaining.append((item,intent))
            t=time.time()
            for item,intent in remaining:
                t0=time.time()
                pid,cid,src=item['pid'],item['campaign_id'],item['catalog_source']
                try:
                    fresh=fresh_all.get(pid)
                    if not fresh:raise ValueError('product_no_longer_eligible')
                    # The campaign binding comes from the frozen intent; only the commercial facts
                    # are re-read, so a plan edited on the platform can never be created silently.
                    # 非全托的实时事实已经是同名字段（normalize 的输出），不必再折算一次。
                    if route=='campaign':
                        current=fresh
                    else:
                        current=new_offer(fresh,pid,intent['spec']['campaignId'],'selected',policy=prep.policy)
                    # Record which field moved, not just that something did. Without the diff the
                    # only way to learn what happened was to read the platform again by hand, and
                    # a transient platform answer looks exactly like a real change.
                    moved={k:(str(intent['spec']['offer'].get(k)),str(current.get(k))) for k in ('campaignId','creatorPercent','totalPercent','publicPercent') if str(current.get(k))!=str(intent['spec']['offer'].get(k))}
                    if moved:raise ValueError('commercial_facts_changed:'+(';'.join(f'{k} {a}->{b}' for k,(a,b) in moved.items()))[:180])
                    ledger.begin(intent['id'],SCOPE['account']);prep.mark_progress(run_id,pid,cid,src,'submitted')
                    r=submit_create_with_verification(transport,intent['spec']['payload'],pid,report)
                    from bdhub.send.taplink.protocol import creation_receipt
                    body=transport.require_read(r);receipt=creation_receipt(body);receipt['responseHash']=digest(body);ledger.receipt(intent['id'],receipt)
                    # The receipt already carries list_id. Read that list back on its own lane and
                    # do NOT wait here: the readback of this product overlaps the next write, so the
                    # write loop is limited by one write per product instead of write+readback.
                    previous=pending
                    pending=(item,intent,receipt,rb_pool.submit(readback_once,intent,receipt),t0)
                    if previous is not None:finalize(previous)
                except Exception as error:
                    code=str(error) if isinstance(error,ValueError) else f'{type(error).__name__}:{str(error)[:80]}'
                    try:attempted=ledger.get(intent['id'])['state'] in ('submitted','receipt_saved','unknown')
                    except Exception:attempted=False
                    # Never lose the failure: an attempted write stays for readback, an untouched plan returns to the queue.
                    retire_or_block(prep,run_id,pid,cid,src,intent['id'],code,attempted=attempted,
                                    retired=retired,blocked=blocked,seconds=round(time.time()-t0,2))
                    if code in ('verification_failed','source_maintenance_due','taplink_account_maintenance_due',
                                'login_required','account_disabled','transport_unavailable'):
                        raise
                finally:
                    prep.release(run_id,pid,cid,src)
                timings.append(round(time.time()-t0,2))
                if pace:time.sleep(pace)
            if pending is not None:finalize(pending)
            # Unknown is isolated per PID.  Once every create request has been attempted, perform
            # one common readback and the two configured delayed polls.  This never calls the POST
            # path again and therefore cannot create a replacement intent.
            def lookup_unknown(row,intent):
                pid=str(row['pid']);raw=fresh_all.get(pid)
                if not raw:raise ValueError('product_no_longer_eligible')
                current=raw if route=='campaign' else new_offer(
                    raw,pid,intent['spec']['campaignId'],'selected',policy=prep.policy)
                outcome=classify_pid(pid,current,rb_read,prep.policy,intent['spec'])
                return outcome.get('card') if outcome.get('state')=='standard' else None
            delays=tuple(prep.policy.get('unknownReadbackDelaysSeconds') or (30,120))
            report['unknownReconcile']=reconcile_unknown_batch(
                ROOT,prep,run_id,lookup_unknown,delays=delays)
            rb_pool.shutdown(wait=True)
            rb_lane_close()
            report.setdefault('phaseSeconds',{})['writesAndReadback']=round(time.time()-t,2)
    finally:
        try:ledger.db.close()
        except Exception:pass
    return {'created':created,'blocked':blocked,'retired':retired,'timings':timings,'paceSeconds':pace}

def selection_items(pids=None):
    """Confirmed full-managed selections from the durable intake ledger."""
    with closing(sqlite3.connect(selection_db_path().as_uri()+'?mode=ro',uri=True)) as db:
        db.execute('BEGIN')
        run=db.execute('SELECT id FROM intake_run ORDER BY created DESC LIMIT 1').fetchone()
        if not run:return []
        rows=db.execute('SELECT pid,state,payload FROM intake_item WHERE run_id=?',(run[0],)).fetchall()
    items=[]
    for pid,state,payload in rows:
        if state!='confirmed':continue
        facts=json.loads(payload);snapshot=facts.get('snapshot') or {};fresh=facts.get('freshProduct') or (facts.get('campaign') or {}).get('freshProduct') or {}
        campaign=str(snapshot.get('campaign_id') or fresh.get('campaign_id') or '')
        title=str(snapshot.get('title') or fresh.get('title') or '').strip()
        if campaign.isdigit() and len(campaign)>1:items.append({'pid':str(pid),'campaignId':campaign,'catalogSource':'selected','title':title})
    if pids:items=[i for i in items if i['pid'] in pids]
    return items

def channel_overlap(route=None):
    """跨渠道重叠的商品：**另一条渠道**已有卡、或已有链接意图的 PID。

    按已确认口径「位置只出一条、全托优先」，这些商品只出一条位置，另一条渠道不另建链——
    一个商品去建第二条链既不会被发出去，也会占用意图账本里那个**按 PID** 的唯一键。

    **只算另一条渠道**：本渠道自己的意图（比如刚建好或已冻结的那几条）不是"重叠"，
    把它们也算进来会让"待备链商品"凭空少掉几条，与池子对不上。名单实时算，实测重叠只有 1 个 PID。
    """
    route=route or SCOPE['route']
    other_source='2' if route=='campaign' else '1'
    covered=set()
    # Current standard bindings are market-scoped.  Only the IT compatibility path also consults
    # the old account-wide inventory, whose historical schema predates the market column.
    if SCOPE['market']=='it':
        inv=TaplinkInventory(ROOT)
        try:
            covered={str(r[0]) for r in inv.db.execute(
                "SELECT DISTINCT m.pid FROM catalog_tap_member m JOIN catalog_tap_list l ON l.list_id=m.list_id WHERE l.source=?",
                (other_source,))}
        finally:inv.close()
    path=ROOT/'var/catalog-links.sqlite'
    if path.exists():
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=5)) as conn:
            conn.execute('BEGIN')
            # 意图里存了 route：只排另一条渠道的，本渠道的已经在这条 run 里了。
            covered|={str(r[0]) for r in conn.execute(
                "SELECT DISTINCT pid FROM catalog_current_binding WHERE market=? AND catalog_source<>? AND state='active'",
                (SCOPE['market'],route))}
            covered|={str(r[0]) for r in conn.execute(
                "SELECT DISTINCT pid FROM catalog_link_intent WHERE json_extract(spec,'$.market')=? "
                "AND json_extract(spec,'$.route') IS NOT ?",(SCOPE['market'],route))}
    return covered

def campaign_block():
    """非全托的建链目标：已入池（chosen）商品 + 按**当前**规则重算的商业事实。"""
    from lib.campaign_screen import link_targets
    return link_targets(ROOT,market=SCOPE['market'],exclude=channel_overlap(SCOPE['route']))

def status_view(route='selected'):
    """Read-only coverage view for the product page: links, covered PIDs and blocking reasons."""
    from lib.catalog_prepare import CatalogPreparation
    prep=CatalogPreparation(ROOT,SCOPE['market'])
    try:
        # 按渠道各取自己最近的一次准备 run：两条渠道的账本同表，最新一条不一定是这条渠道的。
        run=prep.db.execute("SELECT * FROM catalog_prepare_run WHERE market=? AND json_extract(scope,'$.route')=? ORDER BY created DESC LIMIT 1",(SCOPE['market'],route)).fetchone()
        if not run:return {'available':False,'executionAllowed':False,'route':route,'reason':'catalog_prepare_not_started'}
        items=[dict(r) for r in prep.db.execute('''SELECT pid,campaign_id,state,error,blocker,creator_percent,public_percent,total_percent,title,intent_id,updated
            FROM catalog_prepare_item WHERE run_id=? ORDER BY CASE state WHEN 'ready' THEN 0 WHEN 'missing' THEN 1 WHEN 'reuse' THEN 2 WHEN 'review' THEN 3 ELSE 4 END,updated DESC LIMIT 40''',(run['id'],))]
        selection=None
        if route=='selected':
            with closing(sqlite3.connect(selection_db_path().as_uri()+'?mode=ro',uri=True)) as db:
                db.execute('BEGIN')
                row=db.execute('SELECT run_id,count(*) FROM intake_item GROUP BY run_id ORDER BY max(updated) DESC LIMIT 1').fetchone()
            selection={'intakeRun':row[0],'confirmed':row[1]} if row else None
        else:
            # 非全托的"宇宙"是池子不是选入台账，所以这里报池子本身，让页面对得上数。
            block=campaign_block()
            selection={'poolRun':block.get('runId'),'snapshot':block.get('snapshot'),
                       'targets':len(block.get('targets') or []),'skipped':block.get('skipped')}
        return {'available':True,'executionAllowed':False,'route':route,'runId':run['id'],'scope':json.loads(run['scope']),'created':run['created'],
                'selection':selection,
                'summary':prep.summary(run['id']),
                'items':[{'pid':i['pid'],'campaignId':i['campaign_id'],'state':i['state'],'error':i['error'],'blocker':i['blocker'],
                          'creatorPercent':i['creator_percent'],'publicPercent':i['public_percent'],'totalPercent':i['total_percent'],
                          'title':i['title'],'intentId':i['intent_id'],'updated':i['updated']} for i in items]}
    finally:prep.close()

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',nargs='?',choices=['status','seed','read','reread','create','inventory','reconcile','verify'])
    p.add_argument('--status-links',action='store_true',help='read-only coverage view for the product page')
    p.add_argument('--market',default='it');p.add_argument('--canary',action='store_true')
    p.add_argument('--report',type=Path);p.add_argument('--pids');p.add_argument('--limit',type=int,default=15);p.add_argument('--max-creates',type=int,default=0)
    p.add_argument('--lists',type=int,default=0,help='inventory: max lists to read members for (0=all pending)')
    p.add_argument('--items',type=int,default=0,help='reconcile: max queued plans to judge (0=all)')
    p.add_argument('--scope',choices=['intake','pool'],default='intake',
                   help='seed universe: intake=durable full-managed selection only; pool=every live selected plan')
    p.add_argument('--route',choices=list(ROUTES),default='selected',
                   help='selected=全托（账号级卡 source=2）；campaign=非全托（按活动的卡 source=1）')
    p.add_argument('--lanes',type=int,default=1,choices=[1,3,6,9],help='same-account read lanes')
    p.add_argument('--qps',type=int,default=3,choices=[3,5,8,12],help='aggregate request rate shared by all lanes')
    a=p.parse_args()
    from lib.market_accounts import load_config
    from lib.market_registry import enabled_market_keys,supports
    if a.market not in enabled_market_keys(ROOT):p.error('market not enabled')
    if a.route=='selected' and not supports(ROOT,a.market,'fullManagedCatalog'):p.error('market has no full-managed catalog')
    if a.canary and (a.market not in {'br','my','uk'} or a.action!='create' or a.max_creates!=1):p.error('canary requires BR/MY/UK create --max-creates 1')
    SCOPE.update(market=a.market,account=load_config(ROOT)['markets'][a.market]['roles']['supply'],route=a.route)
    if a.route=='campaign':
        # 非全托的源跑号用**池子的 run**：快照或规则一变就换新 run，旧 run 的结论不会被沿用。
        block=campaign_block()
        if not block.get('available'):p.error('campaign pool unavailable: '+str(block.get('reason')))
        SCOPE['sourceRun']=block['runId']
    else:
        source_path=ROOT/('var/global-source.sqlite' if a.market=='it' else f'var/global-source-{a.market}.sqlite')
        if source_path.exists():
            with closing(sqlite3.connect(source_path.resolve().as_uri()+'?mode=ro',uri=True)) as source_db:
                row=source_db.execute('SELECT run_id FROM global_source_head ORDER BY rowid DESC LIMIT 1').fetchone()
            if row:SCOPE['sourceRun']=row[0]
    if a.action=='status' or a.status_links:
        print(json.dumps(status_view(a.route),ensure_ascii=False));return
    if not a.action:p.error('action required')
    if a.route=='campaign' and a.action in ('inventory','reconcile'):
        p.error('campaign route reads cards per campaign inside `read`; use campaign-inventory.py for a plain scan')
    if not a.report:p.error('--report required')
    output=a.report.resolve()
    if not output.is_relative_to(ROOT/'var') or output.exists():p.error('new report under var required')
    if a.limit<1 or a.limit>600 or a.max_creates<0 or a.max_creates>600:p.error('limit out of range')
    prep=CatalogPreparation(ROOT,SCOPE['market']);s=scope();run_id=prep.open_run(s)
    report={'action':a.action,'route':a.route,'realSends':0,'platformWrites':0,'startedAt':time.time(),'scope':s,'runId':run_id}
    if a.action=='seed':
        wanted=set(a.pids.split(',')) if a.pids else None
        if a.route=='campaign':
            # 非全托的播种宇宙＝已入池商品（一个 PID 一条），不需要联网。
            block=campaign_block()
            items=[{'pid':t['pid'],'campaignId':t['campaignId'],'catalogSource':'campaign','title':t['title']}
                   for t in block['targets'] if wanted is None or t['pid'] in wanted]
            report['campaignPoolRun']=block.get('runId');report['campaignSkipped']=block.get('skipped')
            report['candidates']=len(block['targets'])
        else:
            # intake keeps the original narrow universe (the durable full-managed selection ledger).
            # pool covers every live selected plan, so already-verified links are reused instead of
            # only creating links for the newly selected PIDs.
            intake=selection_items(wanted);universe=None if a.scope=='pool' else {i['pid']:i for i in intake}
            with opportunity_reader(report,market=SCOPE['market'],wait_seconds=60,account_name=SCOPE['account']) as transport:
                # A successful selection can change the platform pool seconds before this stage.
                # A two-hour cache is useful for subsequent read passes, but never authoritative
                # for the full-pool seed that defines their universe.
                pool=pool_cache(report,force_refresh=a.scope=='pool')
                if pool is None:
                    pool=read_selected_pool(transport);pool['readAt']=time.time()
                    selected_pool_cache_path().write_text(json.dumps(pool,ensure_ascii=False))
            items=[]
            for r in pool['rows']:
                cp=r.get('campaign_product') or {};ci=r.get('campaign_info') or {}
                pid=str(cp.get('product_id'))
                if not pid.isdigit() or str(ci.get('crs_campaign_type')) not in ('8','9'):continue
                if wanted is not None and pid not in wanted:continue
                if universe is not None and pid not in universe:continue
                title=(universe.get(pid,{}).get('title') if universe is not None else None) or cp.get('product_name') or cp.get('title') or ''
                items.append({'pid':pid,'campaignId':str(ci.get('campaign_id')),'catalogSource':'selected','title':str(title)[:500]})
            report['selectedPoolTotal']=pool['total'];report['candidates']=len(universe) if universe is not None else pool['total']
            report['scopeMode']=a.scope
        report['seeded']=prep.seed(run_id,items,s)
        report['requeuedTermsChanged']=requeue_binding_mismatches(prep,run_id)
        report['retired']=prep.retire_mismatched_bindings(run_id)
    elif a.action=='read':
        if a.route=='campaign':
            report['read']=(step_read_campaign(prep,run_id,a.limit,report) if a.market=='it' else
                            step_read_campaign_direct(prep,run_id,a.limit,report,lanes=a.lanes,qps=a.qps))
        else:
            if a.pids:
                # Reserve the exact live binding before classifying; a stale local snapshot is not a plan.
                with opportunity_reader(report,market=SCOPE['market'],wait_seconds=60,account_name=SCOPE['account']) as transport:
                    pool=pool_cache(report)
                    if pool is None:
                        pool=read_selected_pool(transport);pool['readAt']=time.time()
                        selected_pool_cache_path().write_text(json.dumps(pool,ensure_ascii=False))
                wanted=set(a.pids.split(','))
                items=[{'pid':str((r.get('campaign_product') or {}).get('product_id')),'campaignId':str((r.get('campaign_info') or {}).get('campaign_id')),'catalogSource':'selected'}
                       for r in pool['rows'] if str((r.get('campaign_product') or {}).get('product_id')) in wanted and str((r.get('campaign_info') or {}).get('crs_campaign_type')) in ('8','9')]
                report['selectedPoolTotal']=pool['total'];report['seeded']=prep.seed(run_id,items,s)
            report['read']=step_read(prep,run_id,a.limit,report,lanes=a.lanes,qps=a.qps)
            report['retired']=prep.retire_mismatched_bindings(run_id)
    elif a.action=='reread':
        # 只读：把没卡的行重新排一次队，再用既有的读卡路径读一遍。
        report['requeued']=requeue_cardless(prep,a.route)
        if a.route=='campaign':
            # 非全托的 run 是**按活动来源**建的（`sourceRun` 进 id），每次调用都会算出新 id，
            # 所以不能用本次的 run_id 去读——要读**真正持有这些行的那些 run**。
            src='campaign'
            runs=[r[0] for r in prep.db.execute(
                "SELECT DISTINCT run_id FROM catalog_prepare_item WHERE catalog_source=? AND "
                "(card IS NULL OR coalesce(json_extract(card,'$.listId'),'')='')",(src,))]
            report['readRuns']=len(runs)
            report['read']={run:step_read_campaign(prep,run,a.limit,report) for run in runs}
        else:
            report['read']=step_read(prep,run_id,a.limit,report,lanes=a.lanes,qps=a.qps)
    elif a.action=='inventory':
        report['inventoryResult']=step_inventory(report,lanes=a.lanes,qps=a.qps,limit=(a.lists or None))
    elif a.action=='reconcile':
        inv=TaplinkInventory(ROOT)
        try:report['reconcile']=step_reconcile(prep,inv,run_id,report,limit=(a.items or None))
        finally:inv.close()
    elif a.action=='create':
        try:
            report['create']=step_create(prep,run_id,a.max_creates,report,lanes=a.lanes,qps=a.qps,canary=a.canary,
                                         pids=set(a.pids.split(',')) if a.pids else None)
        except Exception as error:                                     # noqa: BLE001 - 报告必须先落盘
            # 写不出去的时候，**这份报告就是唯一的证据**：异常抛出会让 stdout 空白、
            # 报告文件根本不生成，于是"为什么没写成"就查不出来了（踩过一次）。
            report['create']={'error':f'{type(error).__name__}:{str(error)[:200]}','created':[],'blocked':[]}
            report['state']='blocked'
        report['platformWrites']=report.get('createWrites',0)
    elif a.action=='verify':
        report['verify']=step_verify(prep,report,limit=(a.items or None))
    report['summary']=prep.summary(run_id);report['elapsedSeconds']=round(time.time()-report['startedAt'],2)
    report['state']=report.get('state') or 'completed'
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');prep.close()
    # The driver loops creation while anything is still being created, so the count has to be on
    # stdout. Without it the loop's exit condition was always true and one run meant one batch.
    print(json.dumps({'action':a.action,'state':report['state'],'summary':report['summary'],'platformWrites':report['platformWrites'],'created':len((report.get('create') or {}).get('created') or [])},ensure_ascii=False))
if __name__=='__main__':main()
