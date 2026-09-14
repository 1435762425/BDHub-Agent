#!/usr/bin/env python3
"""Catalog-wide PID link preparation: read existing links, reuse, create only confirmed gaps."""
import argparse,importlib.util,json,sqlite3,sys,time
from contextlib import closing
from decimal import Decimal
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT/'scripts'))
from lib.catalog_links import CatalogLinks,new_commission
from lib.catalog_prepare import CatalogPreparation,READ_EXTRA,new_offer,classify_pid,search_cards,scan_lists,read_members,member_facts,TaplinkInventory,reconcile_from_inventory
from lib.global_selection import selected_rows,assess
from lib.global_source import clean_product
from lib.global_source_transport import opportunity_reader,opportunity_card_creator,CREATE
from lib.second_cycle import digest
spec=importlib.util.spec_from_file_location('catalog_card_inspection',ROOT/'scripts/prepare-cycle-materials.py');inspection=importlib.util.module_from_spec(spec);spec.loader.exec_module(inspection)
SCOPE={'market':'it','account':'acc9','institution':'bjn-local-research','sourceRun':'it-global-20260914','route':'selected'}

def scope():
    policy=json.loads((ROOT/'config/catalog-link-policy.json').read_text())
    return SCOPE|{'policyVersion':policy['version'],'policyFingerprint':digest(policy)}

def plan_bounds(offer):
    """Full binding used by creation, card inspection and the material consumer."""
    return {'pid':offer['pid'],'campaignId':offer['campaignId'],'catalogSource':offer['catalogSource'],'creatorPercent':offer['creatorPercent'],
            'publicPercent':offer['publicPercent'],'totalPercent':offer['totalPercent'],'agencyPercent':offer['agencyPercent'],'title':offer['title'],
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

def pool_cache(report,path=None):
    """Reuse a recent complete selected-pool read instead of re-paging it for every run."""
    path=Path(path or (ROOT/'var/catalog-selected-pool-cache.json'))
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
    with opportunity_reader(report,extra_read_endpoints=READ_EXTRA,wait_seconds=60,account_name='acc9') as transport:
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
                (ROOT/'var/catalog-selected-pool-cache.json').write_text(json.dumps(pool,ensure_ascii=False))
            report['selectedPoolTotal']=pool['total'];report['selectedPoolPages']=pool['pages']
        except Exception as error:
            code=str(error) if isinstance(error,ValueError) else 'selected_pool_unreadable'
            for pid in by_pid:
                for it in by_pid[pid]:prep.apply_read(run_id,it['pid'],it['campaign_id'],it['catalog_source'],{'state':'read_incomplete','error':code})
            return {'claimed':len(claimed),'states':{'read_incomplete':len(claimed)},'error':code}
        group_offers={}
        for start in range(0,len(by_pid),15):
            group=list(by_pid)[start:start+15]
            try:group_offers|=live_plans(transport,prep,group,pool)
            except Exception as error:
                code=str(error) if isinstance(error,ValueError) else 'listing_read_unresolved'
                for pid in group:
                    for it in by_pid[pid]:prep.apply_read(run_id,it['pid'],it['campaign_id'],it['catalog_source'],{'state':'read_incomplete','error':code})
                    states['read_incomplete']=states.get('read_incomplete',0)+len(by_pid[pid])
        targets=[(pid,items) for pid,items in by_pid.items() if pid in group_offers]
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
                    try:return classify_pid(pid,group_offers[pid],lane_reads[index%lanes],prep.policy),None
                    except Exception as error:return None,error
                with ThreadPoolExecutor(max_workers=lanes) as executor:
                    outcomes=[f.result() for f in [executor.submit(classify,i,pid) for i,(pid,_) in enumerate(targets)]]
            else:
                for pid,_ in targets:
                    try:outcomes.append((classify_pid(pid,group_offers[pid],read,prep.policy),None))
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
                outcome['card']={'state':'existing_links_observed','total':outcome['total']}
                prep.apply_read(run_id,it['pid'],it['campaign_id'],it['catalog_source'],outcome)
                states[outcome['state']]=states.get(outcome['state'],0)+1
    return {'claimed':len(claimed),'states':states}

def step_inventory(report,lanes=1,qps=3,limit=None):
    """Enumerate every TapLink once (lists + members). Read-only: no create, no delete.

    This is the shared base for both link reconciliation and link maintenance: the per-PID
    search is replaced by one account-wide inventory that is reused for every later join.
    """
    inv=TaplinkInventory(ROOT);states={}
    try:
        with opportunity_reader(report,extra_read_endpoints=READ_EXTRA,wait_seconds=60,account_name='acc9') as transport:
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

def step_reconcile(prep,inv,run_id,report,limit=None):
    """Judge every queued plan from the cached inventory plus one live plan read.

    Replaces the per-PID card search: the inventory already holds which cards exist and
    their member facts, so only the product plan still needs a platform read.
    """
    rows=[dict(r) for r in prep.db.execute("SELECT * FROM catalog_prepare_item WHERE run_id=? AND state NOT IN ('retired','ready')",(run_id,))]
    if limit:rows=rows[:limit]
    # A product with no card can never be reused, so it needs no live plan read at all:
    # only card-bearing PIDs need current commercial facts. Cuts the plan reads by ~2/3.
    pids=sorted({r['pid'] for r in rows if inv.members_for_pid(r['pid'])})
    report['reconcileItems']=len(rows);report['reconcileWithCards']=len(pids)
    offers={};diag={}
    if pids:
        with opportunity_reader(report,wait_seconds=60,account_name='acc9') as transport:
            pool=pool_cache(report)
            if pool is None:
                pool=read_selected_pool(transport);pool['readAt']=time.time()
                (ROOT/'var/catalog-selected-pool-cache.json').write_text(json.dumps(pool,ensure_ascii=False))
            report['selectedPoolTotal']=pool['total']
            for start in range(0,len(pids),15):
                try:offers|=live_plans(transport,prep,pids[start:start+15],pool,diag)
                except Exception as error:
                    report.setdefault('reconcilePlanErrors',[]).append(str(error)[:60])
    return reconcile_from_inventory(prep,inv,rows,offers,diag)

def create_spec(prep,run_id,offer,short_name):
    campaign=offer['campaignId'];pid=offer['pid']
    tail=digest([pid,campaign,offer['creatorPercent']])[:6]
    while True:
        name=f"BJN {short_name} {offer['creatorPercent']}% "+tail
        if len(name)<=50:break
        short_name=short_name.rsplit(' ',1)[0] if ' ' in short_name else short_name[:-1]
        if not short_name:raise ValueError('catalog_prepare_name_invalid')
    from bdhub.send.taplink.protocol import create_payload
    payload=create_payload(pid=pid,campaign_id=campaign,creator_pct=offer['creatorPercent'],name=name,route='selected')
    return {'market':'it','account':'acc9','route':'selected','purpose':'catalog_batch_link','sourceRun':run_id,'pid':pid,'campaignId':campaign,
            'creatorPercent':offer['creatorPercent'],'listName':name,'shortName':short_name,'policyFingerprint':digest(prep.policy),'searchTotal':0,
            'offer':plan_bounds(offer)|{'observedAt':time.time()},'payload':payload,'preparedAt':time.time()}

def short_name_for(pid,title):
    with closing(sqlite3.connect((ROOT/'var/second-cycle.sqlite').as_uri()+'?mode=ro',uri=True)) as db:
        db.execute('BEGIN')
        row=db.execute("SELECT payload FROM cycle_product_name WHERE id=?",(digest(['product-short-name-v1','it-IT',str(pid),title]),)).fetchone()
    if row:
        value=json.loads(row[0]).get('shortNameIt')
        if isinstance(value,str) and 1<=len(value)<=30:return value
    cleaned=' '.join(str(title).split())
    return (cleaned[:30].rsplit(' ',1)[0] if len(cleaned)>30 and ' ' in cleaned[:31] else cleaned[:30]) or str(pid)

def step_create(prep,run_id,limit,report):
    created=[];blocked=[]
    for _ in range(limit):
        item=prep.claim_create(run_id)
        if not item:break
        pid,cid,src=item['pid'],item['campaign_id'],item['catalog_source']
        ledger=CatalogLinks(ROOT);intent=None
        try:
            listing=item['listing'] or {}
            if item['state']=='missing':
                offer=listing|{'pid':pid,'campaignId':cid,'catalogSource':src}
                if not offer.get('creatorPercent'):raise ValueError('catalog_prepare_offer_missing')
                planned=create_spec(prep,run_id,offer,short_name_for(pid,offer.get('title','')))
                intent=prep.freeze(run_id,pid,cid,src,planned)
            else:
                intent=ledger.get(item['intent_id'])
            if intent['state']=='verified':
                prep.mark_progress(run_id,pid,cid,src,'ready',card=intent['readback']);created.append({'pid':pid,'state':'already_verified'});continue
            if intent['state']!='prepared':raise ValueError('catalog_intent_requires_verification')
            with opportunity_card_creator(report,intent['spec']['payload'],stopped=lambda:False,wait_seconds=60) as transport:
                def read(path,extra):
                    r=transport._xhr(method='GET',path=path,params=transport._params()|extra,payload=None,write=False)
                    body=transport.require_read(r);return body,digest(body)
                fresh=fresh_offers(transport,prep,[pid],time.time()).get(pid)
                if not fresh:raise ValueError('product_no_longer_eligible')
                if any(str(fresh.get(k))!=str(intent['spec']['offer'].get(k)) for k in ('campaignId','creatorPercent','totalPercent','publicPercent')):raise ValueError('commercial_facts_changed')
                total,_=search_cards(read,pid)
                if total!=0:raise ValueError('existing_links_preserved_no_creation')
                ledger.begin(intent['id'],'acc9');prep.mark_progress(run_id,pid,cid,src,'submitted')
                r=transport._xhr(method='POST',path=CREATE,params=transport._params(),payload=intent['spec']['payload'],write=True)
                report.setdefault('nativeReceipts',[]).append({'pid':pid,'http':r.http_status,'code':r.code if type(r.code) is int else None,'verification':r.has_turing,'ambiguous':r.ambiguous})
                if r.has_turing and getattr(transport,'_verification_header',''):
                    transport._solve_verification(transport._verification_header);transport.verification_successes+=1
                    ledger.unknown(intent['id'],'verification_required_readback_pending')
                    prep.mark_progress(run_id,pid,cid,src,'unknown',error='verification_required_readback_pending')
                    created.append({'pid':pid,'state':'unknown_readback_pending'});continue
                from bdhub.send.taplink.protocol import creation_receipt
                body=transport.require_read(r);receipt=creation_receipt(body);receipt['responseHash']=digest(body);ledger.receipt(intent['id'],receipt)
                card=None
                for delay in (0,1,3):
                    if delay:time.sleep(delay)
                    card=inspection.inspect_card(intent['spec']['offer'],read,expected_list_id=receipt.get('list_id'),expected_name=intent['spec']['listName'])
                    if card['state']=='verified_read_only':break
                if not card or card['state']!='verified_read_only':raise ValueError('created_card_not_verified')
                card.update(readAccount='acc9')
                ledger.confirm(intent['id'],card);prep.mark_progress(run_id,pid,cid,src,'ready',card=card)
                created.append({'pid':pid,'state':'verified','listId':card['listId'],'creatorPercent':card['creatorPercent']})
        except Exception as error:
            code=str(error) if isinstance(error,ValueError) else type(error).__name__
            try:attempted=bool(intent) and ledger.get(intent['id'])['state'] in ('submitted','receipt_saved','unknown')
            except Exception:attempted=False
            # Never lose the failure: an attempted write stays for readback, an untouched plan returns to the queue.
            try:prep.mark_progress(run_id,pid,cid,src,'unknown' if attempted else 'missing',error=code)
            except Exception:pass
            blocked.append({'pid':pid,'error':code})
        finally:
            try:ledger.db.close()
            finally:prep.release(run_id,pid,cid,src)
    return {'created':created,'blocked':blocked}

def selection_items(pids=None):
    """Confirmed full-managed selections from the durable intake ledger."""
    with closing(sqlite3.connect((ROOT/'var/global-selection.sqlite').as_uri()+'?mode=ro',uri=True)) as db:
        db.execute('BEGIN')
        run=db.execute('SELECT id FROM intake_run ORDER BY created DESC LIMIT 1').fetchone()
        if not run:return []
        rows=db.execute('SELECT pid,state,payload FROM intake_item WHERE run_id=?',(run[0],)).fetchall()
    items=[]
    for pid,state,payload in rows:
        if state!='confirmed':continue
        campaign=str((json.loads(payload).get('snapshot') or {}).get('campaign_id') or '')
        if campaign.isdigit() and len(campaign)>1:items.append({'pid':str(pid),'campaignId':campaign,'catalogSource':'selected'})
    if pids:items=[i for i in items if i['pid'] in pids]
    return items

def status_view():
    """Read-only coverage view for the product page: links, covered PIDs and blocking reasons."""
    from lib.catalog_prepare import CatalogPreparation
    prep=CatalogPreparation(ROOT)
    try:
        run=prep.db.execute('SELECT * FROM catalog_prepare_run ORDER BY created DESC LIMIT 1').fetchone()
        if not run:return {'available':False,'executionAllowed':False,'reason':'catalog_prepare_not_started'}
        items=[dict(r) for r in prep.db.execute('''SELECT pid,campaign_id,state,error,blocker,creator_percent,public_percent,total_percent,title,intent_id,updated
            FROM catalog_prepare_item WHERE run_id=? ORDER BY CASE state WHEN 'ready' THEN 0 WHEN 'missing' THEN 1 WHEN 'reuse' THEN 2 WHEN 'review' THEN 3 ELSE 4 END,updated DESC LIMIT 40''',(run['id'],))]
        with closing(sqlite3.connect((ROOT/'var/global-selection.sqlite').as_uri()+'?mode=ro',uri=True)) as db:
            db.execute('BEGIN')
            row=db.execute('SELECT run_id,count(*) FROM intake_item GROUP BY run_id ORDER BY max(updated) DESC LIMIT 1').fetchone()
        return {'available':True,'executionAllowed':False,'runId':run['id'],'scope':json.loads(run['scope']),'created':run['created'],
                'selection':{'intakeRun':row[0],'confirmed':row[1]} if row else None,
                'summary':prep.summary(run['id']),
                'items':[{'pid':i['pid'],'campaignId':i['campaign_id'],'state':i['state'],'error':i['error'],'blocker':i['blocker'],
                          'creatorPercent':i['creator_percent'],'publicPercent':i['public_percent'],'totalPercent':i['total_percent'],
                          'title':i['title'],'intentId':i['intent_id'],'updated':i['updated']} for i in items]}
    finally:prep.close()

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',nargs='?',choices=['status','seed','read','create','inventory','reconcile'])
    p.add_argument('--status-links',action='store_true',help='read-only coverage view for the product page')
    p.add_argument('--report',type=Path);p.add_argument('--pids');p.add_argument('--limit',type=int,default=15);p.add_argument('--max-creates',type=int,default=0)
    p.add_argument('--lists',type=int,default=0,help='inventory: max lists to read members for (0=all pending)')
    p.add_argument('--items',type=int,default=0,help='reconcile: max queued plans to judge (0=all)')
    p.add_argument('--scope',choices=['intake','pool'],default='intake',
                   help='seed universe: intake=durable full-managed selection only; pool=every live selected plan')
    p.add_argument('--lanes',type=int,default=1,choices=[1,3,6,9],help='same-account read lanes')
    p.add_argument('--qps',type=int,default=3,choices=[3,5,8,12],help='aggregate request rate shared by all lanes')
    a=p.parse_args()
    if a.action=='status' or a.status_links:
        print(json.dumps(status_view(),ensure_ascii=False));return
    if not a.action:p.error('action required')
    if not a.report:p.error('--report required')
    output=a.report.resolve()
    if not output.is_relative_to(ROOT/'var') or output.exists():p.error('new report under var required')
    if a.limit<1 or a.limit>600 or a.max_creates<0 or a.max_creates>600:p.error('limit out of range')
    prep=CatalogPreparation(ROOT);s=scope();run_id=prep.open_run(s)
    report={'action':a.action,'realSends':0,'platformWrites':0,'startedAt':time.time(),'scope':s,'runId':run_id}
    if a.action=='seed':
        wanted=set(a.pids.split(',')) if a.pids else None
        # intake keeps the original narrow universe (the durable full-managed selection ledger).
        # pool covers every live selected plan, so already-verified links are reused instead of
        # only creating links for the newly selected PIDs.
        universe=None if a.scope=='pool' else {i['pid'] for i in selection_items(wanted)}
        with opportunity_reader(report,wait_seconds=60,account_name='acc9') as transport:
            pool=pool_cache(report)
            if pool is None:
                pool=read_selected_pool(transport);pool['readAt']=time.time()
                (ROOT/'var/catalog-selected-pool-cache.json').write_text(json.dumps(pool,ensure_ascii=False))
        items=[]
        for r in pool['rows']:
            cp=r.get('campaign_product') or {};ci=r.get('campaign_info') or {}
            pid=str(cp.get('product_id'))
            if not pid.isdigit() or str(ci.get('crs_campaign_type')) not in ('8','9'):continue
            if wanted is not None and pid not in wanted:continue
            if universe is not None and pid not in universe:continue
            items.append({'pid':pid,'campaignId':str(ci.get('campaign_id')),'catalogSource':'selected'})
        report['selectedPoolTotal']=pool['total'];report['candidates']=len(universe) if universe is not None else pool['total']
        report['scopeMode']=a.scope
        report['seeded']=prep.seed(run_id,items,s)
        report['retired']=prep.retire_mismatched_bindings(run_id)
    elif a.action=='read':
        if a.pids:
            # Reserve the exact live binding before classifying; a stale local snapshot is not a plan.
            with opportunity_reader(report,wait_seconds=60,account_name='acc9') as transport:
                pool=pool_cache(report)
                if pool is None:
                    pool=read_selected_pool(transport);pool['readAt']=time.time()
                    (ROOT/'var/catalog-selected-pool-cache.json').write_text(json.dumps(pool,ensure_ascii=False))
            wanted=set(a.pids.split(','))
            items=[{'pid':str((r.get('campaign_product') or {}).get('product_id')),'campaignId':str((r.get('campaign_info') or {}).get('campaign_id')),'catalogSource':'selected'}
                   for r in pool['rows'] if str((r.get('campaign_product') or {}).get('product_id')) in wanted and str((r.get('campaign_info') or {}).get('crs_campaign_type')) in ('8','9')]
            report['selectedPoolTotal']=pool['total'];report['seeded']=prep.seed(run_id,items,s)
        report['read']=step_read(prep,run_id,a.limit,report,lanes=a.lanes,qps=a.qps)
        report['retired']=prep.retire_mismatched_bindings(run_id)
    elif a.action=='inventory':
        report['inventoryResult']=step_inventory(report,lanes=a.lanes,qps=a.qps,limit=(a.lists or None))
    elif a.action=='reconcile':
        inv=TaplinkInventory(ROOT)
        try:report['reconcile']=step_reconcile(prep,inv,run_id,report,limit=(a.items or None))
        finally:inv.close()
    elif a.action=='create':
        report['create']=step_create(prep,run_id,a.max_creates,report)
    report['summary']=prep.summary(run_id);report['elapsedSeconds']=round(time.time()-report['startedAt'],2)
    report['state']=report.get('state') or 'completed'
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');prep.close()
    print(json.dumps({'action':a.action,'state':report['state'],'summary':report['summary'],'platformWrites':report['platformWrites']},ensure_ascii=False))
if __name__=='__main__':main()
