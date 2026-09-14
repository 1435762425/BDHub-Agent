"""Frozen full-managed intake rules and durable single-submit selection receipts."""
import json,re,sqlite3,time
from contextlib import closing
from decimal import Decimal,InvalidOperation
from pathlib import Path
from lib.second_cycle import encoded,digest
RULES={'minSalesInclusive':300,'minRating':4,'allowUnrated':True,'minCommissionGapPoints':2}

def sales(value):
    if not isinstance(value,str):return None
    if re.fullmatch(r'\d+(?:\.\d{3})* 已售',value):return int(value.replace('.','').replace(' 已售',''))
    if re.fullmatch(r'\d+,\d+K 已售',value):return int(Decimal(value.replace('K 已售','').replace(',','.'))*1000)
    return None

def decimal(value):
    try:
        if value is None or isinstance(value,bool):return None
        d=Decimal(str(value));return d if d.is_finite() else None
    except InvalidOperation:return None

def assess(product):
    units=sales(product.get('sales'));rating=decimal(product.get('product_rating'));total=decimal(product.get('commission_rate'));public=decimal(product.get('open_collab_rate'))
    reasons=[]
    if units is None:reasons.append('sales_missing')
    elif units<300:reasons.append('sales_below_300')
    if rating is None or rating==0:reasons.append('rating_unrated_unverified')
    elif not 4<=rating<=5:reasons.append('rating_below_4_or_invalid')
    if total is None or public is None:reasons.append('commission_missing')
    elif not 0<=public<=10000 or not 0<=total<=10000 or total-public<200:reasons.append('commission_gap_below_2_or_invalid')
    # No stock check. Rating 0 semantics remain unresolved; no >=300 snapshot candidates have 0.
    return {'eligible':not reasons,'reasons':reasons,'units':units}

def choose_campaign(entries,now):
    choices=[]
    for e in entries:
        c=e.get('campaign') or {};start=decimal(c.get('promotion_start_time'));end=decimal(c.get('promotion_end_time'));total=decimal(c.get('commission'));public=decimal(e.get('open_collab_rate'))
        if str(c.get('crs_campaign_type')) not in ('8','9') or not str(c.get('campaign_id','')).isdigit():continue
        if None in (start,end,total,public) or not start<=Decimal(str(now))*1000<end:continue
        if not 0<=public<=total<=10000 or total-public<200:continue
        choices.append(e)
    return max(choices,key=lambda e:(decimal(e['campaign']['commission']),decimal(e['campaign']['promotion_end_time']))) if choices else None

class Selection:
    def __init__(self,root):
        self.root=Path(root);self.path=self.root/'var/global-selection.sqlite'
        self.db=sqlite3.connect(self.path,timeout=15);self.db.row_factory=sqlite3.Row
        self.db.executescript('''CREATE TABLE IF NOT EXISTS intake_run(id TEXT PRIMARY KEY,rules TEXT NOT NULL,source_run TEXT NOT NULL,created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS intake_item(run_id TEXT NOT NULL,pid TEXT NOT NULL,state TEXT NOT NULL,payload TEXT NOT NULL,updated REAL NOT NULL,PRIMARY KEY(run_id,pid));''')
    def prepare(self):
        with closing(sqlite3.connect((self.root/'var/global-source.sqlite').as_uri()+'?mode=ro',uri=True)) as c:
            source=c.execute("SELECT r.id,r.scope FROM global_source_head h JOIN global_source_run r ON r.id=h.run_id WHERE json_extract(r.scope,'$.market')='it'").fetchall()
            if len(source)!=1:raise ValueError('source_scope_ambiguous')
            rid,scope=source[0]
            if json.loads(scope)['account']!='acc6':raise ValueError('source_account_changed')
            rows=[json.loads(r[0]) for r in c.execute('SELECT payload FROM global_source_product WHERE run_id=?',(rid,))]
        id='select-'+digest([rid,RULES,'user-300-inclusive'])[:24]
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO intake_run VALUES(?,?,?,?)',(id,encoded(RULES),rid,time.time()))
            for p in rows:
                if assess(p)['eligible'] and p.get('fs_is_selected') is False:
                    self.db.execute('INSERT OR IGNORE INTO intake_item VALUES(?,?,?,?,?)',(id,p['product_id'],'pending',encoded({'snapshot':p}),time.time()))
        return id
    def items(self,id):return [dict(r)|{'payload':json.loads(r['payload'])} for r in self.db.execute('SELECT * FROM intake_item WHERE run_id=? ORDER BY pid',(id,))]
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
    def status(self,id):return dict(self.db.execute('SELECT state,count(*) FROM intake_item WHERE run_id=? GROUP BY state',(id,)).fetchall())

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

def observations(folder,source_run):
    """Read verified intake observations without rewriting source snapshots."""
    path=Path(folder)/'global-selection.sqlite'
    if not path.exists():return None,{}
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as c:
        run=c.execute('SELECT id,rules FROM intake_run WHERE source_run=? ORDER BY created DESC LIMIT 1',(source_run,)).fetchone()
        if not run:return None,{}
        rows=c.execute('SELECT pid,state,updated FROM intake_item WHERE run_id=?',(run[0],)).fetchall()
    from collections import Counter
    summary={'id':run[0],'rules':json.loads(run[1]),'total':len(rows),'states':dict(Counter(r[1] for r in rows)),'updatedAt':max((r[2] for r in rows),default=None)}
    status=Path(folder)/'global-selection-status.json'
    if status.exists():
        report=json.loads(status.read_text())
        if report.get('id')==run[0] and report.get('mode')=='same_account_parallel':summary['performance']={k:report.get(k) for k in ('confirmedPerMinute','configuredQps','lanes','confirmedThisRun','elapsedSeconds','stageMetrics')}
    return summary, {r[0]:{'state':r[1],'observedAt':r[2]} for r in rows}

def retryable_verification_rejection(item,present,fresh):
    r=item['payload'].get('receipt') or {}
    return (item['state']=='result_unknown' and r.get('http')==200 and r.get('code')==10000
            and r.get('verification') is True and r.get('ambiguous') is False
            and item['pid'] not in present and fresh.get('product_id')==item['pid']
            and fresh.get('fs_is_selected') is False and assess(fresh)['eligible']
            and len(item['payload'].get('priorAttempts',[]))<2)

def reconcile_verification_rejections(ledger,id,t,*,pids=None,limit=600):
    """Only explicit verification rejections with two current non-selection proofs can continue."""
    from lib.global_source import clean_product
    items=[i for i in ledger.items(id) if i['state']=='result_unknown' and i['payload'].get('receipt',{}).get('code')==10000 and (pids is None or i['pid'] in pids)][:limit]
    if not items:return []
    pids=[i['pid'] for i in items];present={str(r['campaign_product']['product_id']) for r in selected_rows(t,pids)};fresh={}
    for start in range(0,len(pids),15):
        targets=pids[start:start+15];page=t.opportunity_page(1,global_only=True,pids=targets)
        if page['has_more'] or any(p['product_id'] not in targets for p in page['products']):raise ValueError('rejection_verification_scope_invalid')
        for p in page['products']:fresh[p['product_id']]=clean_product(p)
    accepted=[]
    for item in items:
        if retryable_verification_rejection(item,present,fresh.get(item['pid'],{})):
            t.allow_verified_nonselection(item['pid'],item['payload']['receipt'],fresh[item['pid']],True)
            prior=item['payload'].copy();history=prior.pop('priorAttempts',[])
            history=history+[{'payload':prior,'outcome':'verified_not_selected','verifiedAt':time.time(),'evidence':{'selectedPoolAbsent':True,'freshListing':fresh[item['pid']]}}]
            ledger.update(item,'pending',priorAttempts=history,receipt=None,platformVerification=None,notDispatched=True)
            accepted.append(item)
    return accepted
