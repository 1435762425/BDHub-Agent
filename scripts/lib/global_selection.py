"""Frozen full-managed intake rules and durable single-submit selection receipts.

The intake thresholds themselves are no longer frozen here: they are the operator's knobs and
live in ``config/catalog-screen.json`` (see ``lib/global_screen``). What stays frozen is the
receipt discipline -- one submit per product, an explicit verification for every unknown, and a
run id that changes whenever the thresholds change, so two rule sets can never share one batch.
"""
import json,re,sqlite3,time
from contextlib import closing
from decimal import Decimal,InvalidOperation
from pathlib import Path
from lib.global_screen import decimal, evaluate, fingerprint, load, sales, promotion_assessment
from lib.second_cycle import encoded,digest

READBACK_DELAYS=(0,1,3,30,120)
OTHER_CAMPAIGN_MIN_DELAY=300

def rules(root=None):
    """The thresholds currently in force, read from the operator-controlled config."""
    return load(root)

def assess(product,config=None):
    return evaluate(product,config)

def choose_campaign(entries,now):
    choices=[]
    for e in entries:
        c=e.get('campaign') or {};start=decimal(c.get('promotion_start_time'));end=decimal(c.get('promotion_end_time'));total=decimal(c.get('commission'));public=decimal(e.get('open_collab_rate'))
        if str(c.get('crs_campaign_type')) not in ('8','9') or not str(c.get('campaign_id','')).isdigit():continue
        if None in (start,end,total,public) or not start<=Decimal(str(now))*1000<end:continue
        if not 0<=public<=total<=10000 or total-public<200:continue
        choices.append(e)
    return max(choices,key=lambda e:(decimal(e['campaign']['commission']),decimal(e['campaign']['promotion_end_time']))) if choices else None

def selection_campaign(transport,product,now,*,native_listing=False):
    """Use the listing's frozen full-managed campaign without one detail request per PID."""
    if not native_listing:return choose_campaign(transport.offers(product['product_id']),now)
    cid=str(product.get('campaign_id') or '')
    if not cid.isdigit() or not 10<=len(cid)<=32:raise ValueError('native_listing_campaign_missing')
    return {'campaign':{'campaign_id':cid},'selectionSource':'global_listing','freshProduct':product}

def prioritized_selection_batch(items,retried,limit):
    retried_pids={item['pid'] for item in retried}
    regular=[item for item in items if item['state']=='pending' and item['pid'] not in retried_pids]
    return list(retried)+regular[:max(0,limit-len(retried))]

class Selection:
    def __init__(self,root,market='it'):
        self.root=Path(root);self.market=market
        self.path=self.root/('var/global-selection.sqlite' if market=='it' else f'var/global-selection-{market}.sqlite')
        self.db=sqlite3.connect(self.path,timeout=15);self.db.row_factory=sqlite3.Row
        self.db.executescript('''CREATE TABLE IF NOT EXISTS intake_run(id TEXT PRIMARY KEY,rules TEXT NOT NULL,source_run TEXT NOT NULL,created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS intake_item(run_id TEXT NOT NULL,pid TEXT NOT NULL,state TEXT NOT NULL,payload TEXT NOT NULL,updated REAL NOT NULL,PRIMARY KEY(run_id,pid));''')
        from lib.fullmanaged_candidates import SELECTION_SCHEMA
        self.db.executescript(SELECTION_SCHEMA)
    def prepare(self):
        source_path=self.root/('var/global-source.sqlite' if self.market=='it' else f'var/global-source-{self.market}.sqlite')
        with closing(sqlite3.connect(source_path.as_uri()+'?mode=ro',uri=True)) as c:
            source=c.execute("SELECT r.id,r.scope FROM global_source_head h JOIN global_source_run r ON r.id=h.run_id WHERE json_extract(r.scope,'$.market')=?",(self.market,)).fetchall()
            if len(source)!=1:raise ValueError('source_scope_ambiguous')
            rid,scope=source[0]
            from lib.market_accounts import load_config
            try:supply=load_config(self.root)['markets'][self.market]['roles']['supply']
            except FileNotFoundError:
                if self.market!='it':raise
                supply='acc9'
            allowed={supply}|({'acc6'} if self.market=='it' else set())
            if json.loads(scope)['account'] not in allowed:
                raise ValueError('source_account_changed')
        from lib.global_screen import screen_source
        from lib.fullmanaged_candidates import candidate_rows,backfill_owners
        screen_source(source_path,rid,root=self.root)
        candidates=candidate_rows(self.root,self.market)
        config=rules(self.root);stamp=fingerprint(config)
        id='select-'+digest([self.market,rid,config,stamp,'cumulative-v1'])[:24]
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            backfill_owners(self.db)
            self.db.execute('INSERT OR IGNORE INTO intake_run VALUES(?,?,?,?)',(id,encoded(config),rid,time.time()))
            for candidate in candidates:
                pid=candidate['pid'];p=candidate['evidence']['product']
                owner=self.db.execute('SELECT owner_run_id FROM intake_candidate_owner WHERE pid=?',(pid,)).fetchone()
                if owner is None:
                    state='already_selected' if p.get('fs_is_selected') is True else 'pending'
                    facts={'snapshot':p,'candidateEvidence':{'market':self.market,'sourceRun':candidate['source_run'],
                      'screenRun':candidate['screen_run'],'observedAt':candidate['observed_at']},
                      'selectionObservation':'qualified_source_listing' if state=='already_selected' else None}
                    self.db.execute('INSERT INTO intake_item VALUES(?,?,?,?,?)',(id,pid,state,encoded(facts),time.time()))
                    self.db.execute('INSERT INTO intake_candidate_owner VALUES(?,?)',(pid,id))
            # The scope references original intents, including older pending/unknown and failed
            # rows, so changing discovery snapshots cannot reset their disposition.
            self.db.execute("""INSERT OR IGNORE INTO intake_run_member
              SELECT ?,pid,owner_run_id FROM intake_candidate_owner""",(id,))
        return id
    def items(self,id):
        from lib.fullmanaged_candidates import selection_rows
        return [dict(r)|{'payload':json.loads(r['payload'])} for r in selection_rows(self.db,id)]
    def update(self,item,state,**facts):
        payload=item['payload']|facts
        with self.db:self.db.execute('UPDATE intake_item SET state=?,payload=?,updated=? WHERE run_id=? AND pid=?',(state,encoded(payload),time.time(),item['run_id'],item['pid']))
        item.update(state=state,payload=payload)
    def begin(self,item,campaign):
        facts=item['payload']|{'campaign':campaign,'attemptedAt':time.time(),'notDispatched':False}
        with self.db:
            changed=self.db.execute("UPDATE intake_item SET state='submitting',payload=?,updated=? WHERE run_id=? AND pid=? AND state='pending'",(encoded(facts),time.time(),item['run_id'],item['pid'])).rowcount
            if changed!=1:raise ValueError('selection_already_attempted')
        item.update(state='submitting',payload=facts)
    def record_other_campaign(self,item,observed,*,at=None):
        if item['state'] not in ('submitting','awaiting_verification','result_unknown'):return False
        stamp=time.time() if at is None else float(at)
        reads=list(item['payload'].get('otherCampaignReads') or [])+[{'at':stamp,'campaignIds':sorted({row['campaignId'] for row in observed})}]
        reads=reads[-10:]
        if len(reads)>=2 and reads[-1]['at']-reads[0]['at']>=OTHER_CAMPAIGN_MIN_DELAY:
            self.update(item,'isolated_unverified',otherCampaignObserved=observed,otherCampaignReads=reads,
                        isolation={'reason':'selection_campaign_mismatch','at':stamp})
            return True
        self.update(item,item['state'],otherCampaignObserved=observed,otherCampaignReads=reads)
        return False
    def record_readback_absence(self,item,*,at=None):
        if item['state'] not in ('submitting','awaiting_verification','result_unknown'):return item
        stamp=time.time() if at is None else float(at);history=list(item['payload'].get('readbackAbsences') or [])
        history.append({'at':stamp,'selectedPoolAbsent':True})
        self.update(item,item['state'],readbackAbsences=history[-10:])
        return item
    def skip_unknown(self,id,*,at=None,min_delay=30):
        """Terminally skip, but never resend, an ambiguous write after two delayed absences."""
        stamp=time.time() if at is None else float(at);skipped=[]
        for item in self.items(id):
            receipt=item['payload'].get('receipt') or {};reads=item['payload'].get('readbackAbsences') or []
            uncertain=(receipt.get('ambiguous') is True or
                       receipt.get('http')==200 and receipt.get('code')==0 and receipt.get('verification') is False or
                       receipt.get('http')==200 and receipt.get('code')==10000 and
                       receipt.get('verification') is True and receipt.get('ambiguous') is False and
                       item['payload'].get('platformVerification')=='passed' and
                       len(item['payload'].get('priorAttempts') or [])>=2)
            if item['state'] not in ('submitting','awaiting_verification','result_unknown') or not uncertain or len(reads)<2:
                continue
            if not all(isinstance(row,dict) and row.get('selectedPoolAbsent') is True and isinstance(row.get('at'),(int,float)) for row in reads):
                continue
            if reads[-1]['at']-reads[0]['at']<min_delay:continue
            self.update(item,'skipped_unknown',reason='unresolved_after_two_delayed_readbacks',skippedAt=stamp)
            skipped.append(item['pid'])
        return skipped
    def status(self,id):
        from collections import Counter
        return dict(Counter(r['state'] for r in self.items(id)))

def selected_rows(t,pids):
    rows=[];seen=set();total=None
    for page in range(1,101):
        data=t.selected_page(page,pids=pids)
        if total is not None and total!=data['total']:raise ValueError('selected_list_changed')
        total=data['total']
        for r in data['items']:
            pid=str((r.get('campaign_product') or {}).get('product_id'));cid=str((r.get('campaign_info') or {}).get('campaign_id'))
            if pid not in pids or (pid,cid) in seen:raise ValueError('selected_scope_or_duplicate')
            rows.append(r);seen.add((pid,cid))
        if len(rows)==total:return rows
        if not data['items'] or len(rows)>total:raise ValueError('selected_incomplete')
    raise ValueError('selected_page_limit')

def readback_row(r):
    """One full-managed row of the selected pool, with the total commission it carries.

    The selected pool reports commission in basis points (1500 = 15%), like the listing."""
    product=r.get('campaign_product') or {};campaign=r.get('campaign_info') or {}
    total=product.get('total_commission_percent')
    if total is None:total=product.get('partner_commission_percent')
    return {'pid':str(product.get('product_id')),'campaignId':str(campaign.get('campaign_id')),
            'type':campaign.get('crs_campaign_type'),'totalBasis':None if total is None else str(total)}

def frozen_total_basis(item):
    """Total commission the intent was submitted at, in basis points; None when not recorded."""
    frozen=(item.get('payload') or {}).get('campaign') or {}
    if not isinstance(frozen,dict):return None
    basis=(frozen.get('freshProduct') or {}).get('commission_rate')
    if basis is None:basis=(frozen.get('campaign') or {}).get('commission')
    try:return Decimal(str(basis)) if basis is not None else None
    except InvalidOperation:return None

def matching_selection_evidence(item, observed):
    """Readback rows that settle a submitted selection intent.

    The frozen campaign settles it. Full-managed selection is also re-homed by the platform under a
    campaign it creates (2026-09-28, verified on 8,900+ earlier selections): such a row settles the
    intent only when it carries the same total commission the intent was submitted at."""
    if item['state']=='pending':return observed
    frozen=(item.get('payload') or {}).get('campaign') or {}
    campaign=(frozen.get('campaign') or {}) if isinstance(frozen,dict) else {}
    cid=str(campaign.get('campaign_id') or '')
    exact=[row for row in observed if row['campaignId']==cid] if cid else []
    if exact:return exact
    total=frozen_total_basis(item)
    if total is None:return []
    matched=[]
    for row in observed:
        try:same=row.get('totalBasis') is not None and Decimal(str(row['totalBasis']))==total
        except InvalidOperation:same=False
        if same and str(row.get('type')) in ('8','9'):matched.append({**row,'platformAssignedCampaign':True})
    return matched

def settle_readback(ledger,item,observed,*,at=None):
    """One readback outcome for a submitted intent: settled, isolated, other_campaign, absent or unchanged."""
    exact=matching_selection_evidence(item,observed)
    if exact:
        ledger.update(item,'already_selected' if item['state']=='pending' else 'confirmed',selectionEvidence=exact,verifiedAt=time.time())
        return 'settled'
    if item['state'] in ('submitting','awaiting_verification','result_unknown'):
        if observed:return 'isolated' if ledger.record_other_campaign(item,observed,at=at) else 'other_campaign'
        ledger.record_readback_absence(item,at=at);return 'absent'
    return 'unchanged'

def settled_pids(root,market='it'):
    """Products any batch has proven to be in the selected pool.

    The collection snapshot only says what the pool held when we collected. Once a product is
    confirmed selected, the snapshot is out of date for it, and a page that keeps reading the
    snapshot will keep claiming work that is already done.
    """
    path=Path(root)/('var/global-selection.sqlite' if market=='it' else f'var/global-selection-{market}.sqlite')
    if not path.exists():return set()
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as conn:
        conn.execute('BEGIN')
        return {row[0] for row in conn.execute(
            "SELECT DISTINCT pid FROM intake_item WHERE state IN ('confirmed','already_selected')")}


def observations(folder,source_run,market='it'):
    """Read verified intake observations without rewriting source snapshots."""
    path=Path(folder)/('global-selection.sqlite' if market=='it' else f'global-selection-{market}.sqlite')
    if not path.exists():return None,{}
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as c:
        run=c.execute('SELECT id,rules FROM intake_run WHERE source_run=? ORDER BY created DESC LIMIT 1',(source_run,)).fetchone()
        if not run:return None,{}
        from lib.fullmanaged_candidates import selection_rows
        c.row_factory=sqlite3.Row
        rows=[(r['pid'],r['state'],r['updated']) for r in selection_rows(c,run[0])]
    from collections import Counter
    summary={'id':run[0],'rules':json.loads(run[1]),'total':len(rows),'states':dict(Counter(r[1] for r in rows)),'updatedAt':max((r[2] for r in rows),default=None)}
    status=Path(folder)/('global-selection-status.json' if market=='it' else f'global-selection-status-{market}.json')
    if status.exists():
        report=json.loads(status.read_text())
        if report.get('id')==run[0] and report.get('mode')=='same_account_parallel':summary['performance']={k:report.get(k) for k in ('confirmedPerMinute','configuredQps','lanes','confirmedThisRun','elapsedSeconds','stageMetrics')}
    return summary, {r[0]:{'state':r[1],'observedAt':r[2]} for r in rows}

def retryable_verification_rejection(item,present,fresh):
    r=item['payload'].get('receipt') or {}
    return (item['state']=='result_unknown' and r.get('http')==200 and r.get('code')==10000
            and r.get('verification') is True and r.get('ambiguous') is False
            and item['pid'] not in present and fresh.get('product_id')==item['pid']
            and fresh.get('fs_is_selected') is False and promotion_assessment(fresh)['eligible']
            and len(item['payload'].get('priorAttempts',[]))<2)

def latest_relogin_after(ledger,item):
    """When the supply account last proved a working login after this attempt.

    A published relogin or refresh generation both re-establish and verify the saved login; the
    scheduler's own recovery for a selection auth rejection runs a refresh."""
    attempted=item['payload'].get('attemptedAt')
    if not isinstance(attempted,(int,float)):return None
    cycle=ledger.root/'var/second-cycle.sqlite'
    if not cycle.exists():return None
    from lib.market_accounts import load_config
    try:account=load_config(ledger.root)['markets'][ledger.market]['roles']['supply']
    except (FileNotFoundError,KeyError):return None
    try:
        with closing(sqlite3.connect(cycle.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            row=db.execute("SELECT max(published_at) FROM account_identity_generation WHERE market=? AND account=? AND state='published' AND reason IN ('relogin','refresh')",
                           (ledger.market,account)).fetchone()
    except sqlite3.Error:return None
    return row[0] if row and isinstance(row[0],(int,float)) and row[0]>attempted else None

def retryable_auth_rejection(item,present,fresh,relogin_at):
    receipt=item['payload'].get('receipt') or {}
    return (item['state']=='result_unknown' and receipt.get('http')==200 and
            receipt.get('code')==16201010 and receipt.get('verification') is False and
            receipt.get('ambiguous') is False and receipt.get('systemError') is False and
            isinstance(relogin_at,(int,float)) and item['pid'] not in present and
            fresh.get('product_id')==item['pid'] and fresh.get('fs_is_selected') is False and
            promotion_assessment(fresh)['eligible'] and len(item['payload'].get('priorAttempts',[]))<2)

def reconcile_verification_rejections(ledger,id,t,*,pids=None,limit=600):
    """Only explicit verification rejections with two current non-selection proofs can continue."""
    from lib.global_source import clean_product
    items=[i for i in ledger.items(id) if i['state']=='result_unknown' and
           i['payload'].get('receipt',{}).get('code') in (10000,16201010) and
           (pids is None or i['pid'] in pids)][:limit]
    if not items:return []
    pids=[i['pid'] for i in items];present={str(r['campaign_product']['product_id']) for r in selected_rows(t,pids)};fresh={}
    for start in range(0,len(pids),15):
        targets=pids[start:start+15];page=t.opportunity_page(1,global_only=True,pids=targets)
        if page['has_more'] or any(p['product_id'] not in targets for p in page['products']):raise ValueError('rejection_verification_scope_invalid')
        for p in page['products']:fresh[p['product_id']]=clean_product(p)
    accepted=[]
    for item in items:
        verification=retryable_verification_rejection(item,present,fresh.get(item['pid'],{}))
        relogin_at=latest_relogin_after(ledger,item) if not verification else None
        auth=retryable_auth_rejection(item,present,fresh.get(item['pid'],{}),relogin_at)
        if verification or auth:
            outcome='verified_not_selected' if verification else 'explicit_auth_rejection_after_relogin'
            if verification:
                t.allow_verified_nonselection(item['pid'],item['payload']['receipt'],fresh[item['pid']],True)
            prior=item['payload'].copy();history=prior.pop('priorAttempts',[])
            history=history+[{'payload':prior,'outcome':outcome,'verifiedAt':time.time(),
                              'evidence':{'selectedPoolAbsent':True,'freshListing':fresh[item['pid']],
                                          **({'reloginPublishedAt':relogin_at} if auth else {})}}]
            ledger.update(item,'pending',priorAttempts=history,receipt=None,platformVerification=None,
                          notDispatched=True,recoveredAfterReloginAt=relogin_at)
            accepted.append(item)
    return accepted
