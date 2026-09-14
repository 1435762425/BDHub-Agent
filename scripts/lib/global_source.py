"""Durable listing snapshots for opportunity products filtered to global sales.

Lists are source observations, never proof of stock, selection, or send eligibility.
"""
from contextlib import contextmanager
from collections import Counter
from copy import deepcopy
import json,re,sqlite3,time
from lib.second_cycle import encoded,digest,assess_offer
from lib.product_stock_policy import mark_full_managed

SOURCE='opportunity_global_only'
FILTER={'product_source':[],'campaign_type':[8],'label_type':[],'product_status':1}
class GlobalSourceError(ValueError):pass

def source_lineage_key(scope):
    # Keep the original v1 source hash stable while actors move between the IT pair.
    # Account is a reserved compatibility slot in this hash, not the executing actor;
    # each immutable run.scope still records its real account.
    return digest({**scope,'account':'acc6'})

def list_request(page,total=None):
    if type(page) is not int or not 1<=page<=2000:raise GlobalSourceError('invalid_page')
    # Live-verified tail: 9990 offset + 10 rows stays inside the 10000-result window.
    native_page,size=(1000,10) if page==667 and total==10000 else (page,15)
    return {'filter':deepcopy(FILTER),'page':native_page,'page_size':size}

def clean_product(row):
    if not isinstance(row,dict) or not isinstance(row.get('product_id'),str) or not re.fullmatch(r'[0-9]{19}',row['product_id']):raise GlobalSourceError('product_identity_invalid')
    # Contact fields never enter persistence, even when the platform returns them.
    scalar=('title','commission_rate','open_collab_rate','open_collab_ads_rate','sales','product_rating','is_free_sample','campaign_id','fs_is_selected','stock','product_status','is_under_governed','unavailable_type')
    safe={'product_id':row['product_id']}
    for key in scalar:
        if key in row and type(row[key]) in (str,int,float,bool,type(None)):safe[key]=row[key]
    price=row.get('price')
    if isinstance(price,dict):safe['price']={k:v for k,v in price.items() if k in ('floor_price','celling_price','currency') and type(v) in (str,int,float,type(None))}
    shop=row.get('shop_info') or {};safe['shop_name']=str(shop.get('shop_name') or row.get('shop_name') or '')[:500] if isinstance(shop,dict) else ''
    # The official request filter is the provenance; missing per-row type is not guessed.
    safe['source']=SOURCE
    return safe

def clean_details(rows):
    if not isinstance(rows,list):raise GlobalSourceError('detail_shape_invalid')
    result=[];seen=set()
    for row in rows:
        if not isinstance(row,dict) or not isinstance(row.get('campaign'),dict):raise GlobalSourceError('detail_shape_invalid')
        c=row['campaign'];cid=str(c.get('campaign_id') or '')
        if not cid.isdigit() or cid in seen:raise GlobalSourceError('detail_identity_invalid')
        seen.add(cid)
        if str(c.get('crs_campaign_type')) not in ('8','9'):continue
        result.append({'campaign':{k:v for k,v in c.items() if k in ('campaign_id','name','promotion_start_time','promotion_end_time','commission','crs_campaign_type') and type(v) in (str,int,float,type(None))},
            **{k:row[k] for k in ('open_collab_rate','open_collab_ads_rate','is_selected','is_free_sample') if k in row and type(row[k]) in (str,int,float,bool,type(None))}})
    return result

SCHEMA='''
CREATE TABLE IF NOT EXISTS global_source_run(id TEXT PRIMARY KEY,scope TEXT NOT NULL,scope_hash TEXT NOT NULL,state TEXT NOT NULL,next_page INTEGER NOT NULL DEFAULT 1,reported_total INTEGER,created REAL NOT NULL,updated REAL NOT NULL,terminal_reason TEXT,identity_unchanged INTEGER NOT NULL DEFAULT 0);
CREATE UNIQUE INDEX IF NOT EXISTS global_one_open_scope ON global_source_run(scope_hash) WHERE state='collecting';
CREATE TABLE IF NOT EXISTS global_source_page(run_id TEXT NOT NULL,page INTEGER NOT NULL,count INTEGER NOT NULL,new_count INTEGER NOT NULL,has_more INTEGER NOT NULL,reported_total INTEGER NOT NULL,response_hash TEXT NOT NULL,pids_hash TEXT NOT NULL,observed REAL NOT NULL,PRIMARY KEY(run_id,page));
CREATE TABLE IF NOT EXISTS global_source_product(run_id TEXT NOT NULL,pid TEXT NOT NULL,payload TEXT NOT NULL,fingerprint TEXT NOT NULL,first_page INTEGER NOT NULL,last_page INTEGER NOT NULL,observed REAL NOT NULL,PRIMARY KEY(run_id,pid));
CREATE INDEX IF NOT EXISTS global_product_page ON global_source_product(run_id,first_page,pid);
CREATE TABLE IF NOT EXISTS global_source_head(scope_hash TEXT PRIMARY KEY,run_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS global_source_detail(run_id TEXT NOT NULL,pid TEXT NOT NULL,listing_fingerprint TEXT NOT NULL,payload TEXT NOT NULL,response_hash TEXT NOT NULL,observed REAL NOT NULL,PRIMARY KEY(run_id,pid));
CREATE TABLE IF NOT EXISTS global_source_stock(run_id TEXT NOT NULL,pid TEXT NOT NULL,listing_fingerprint TEXT NOT NULL,payload TEXT NOT NULL,response_hash TEXT NOT NULL,observed REAL NOT NULL,PRIMARY KEY(run_id,pid));
CREATE TABLE IF NOT EXISTS global_source_attempt(id INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT NOT NULL,page INTEGER,at REAL NOT NULL,code TEXT NOT NULL);
'''
class GlobalSources:
    def __init__(self,path,*,clock=time.time,readonly=False):
        self.path=path
        self.clock=clock;self.db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro' if readonly else path,uri=readonly,timeout=10,isolation_level=None);self.db.row_factory=sqlite3.Row
        if not readonly:
            self.db.executescript(SCHEMA)
            if 'request_payload' not in {r[1] for r in self.db.execute('PRAGMA table_info(global_source_page)')}:
                self.db.execute('ALTER TABLE global_source_page ADD COLUMN request_payload TEXT')
    def close(self):self.db.close()
    @contextmanager
    def tx(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:yield;self.db.execute('COMMIT')
        except BaseException:self.db.execute('ROLLBACK');raise
    def get(self,id):
        row=self.db.execute('SELECT * FROM global_source_run WHERE id=?',(id,)).fetchone()
        if not row:raise GlobalSourceError('run_missing')
        return dict(row)|{'scope':json.loads(row['scope'])}
    def start(self,id,scope):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',id):raise GlobalSourceError('invalid_run_id')
        if not isinstance(scope,dict) or set(scope)!={'market','account','institutionFingerprint'} or scope.get('market')!='it' or scope.get('account') not in ('acc6','acc9') or not re.fullmatch(r'[a-f0-9]{64}',scope.get('institutionFingerprint','')):raise GlobalSourceError('unsupported_source_scope')
        frozen={**scope,'source':SOURCE,'filter':FILTER,'pageSize':15}
        with self.tx():
            previous=self.db.execute('SELECT scope FROM global_source_run WHERE id=?',(id,)).fetchone()
            if previous:
                if previous[0]!=encoded(frozen):raise GlobalSourceError('run_scope_changed')
            elif self.db.execute("SELECT 1 FROM global_source_run WHERE scope_hash=? AND state='collecting'",(source_lineage_key(frozen),)).fetchone():raise GlobalSourceError('scan_already_collecting')
            else:self.db.execute("INSERT INTO global_source_run(id,scope,scope_hash,state,created,updated) VALUES(?,?,?,'collecting',?,?)",(id,encoded(frozen),source_lineage_key(frozen),self.clock(),self.clock()))
        return self.get(id)
    def page(self,id,page,data,*,request_payload=None):
        if not isinstance(data,dict) or type(data.get('has_more')) is not bool or type(data.get('total')) is not int or data['total']<0 or not isinstance(data.get('products'),list):raise GlobalSourceError('page_shape_invalid')
        cleaned=[clean_product(row) for row in data['products']];pids=[r['product_id'] for r in cleaned]
        if len(set(pids))!=len(pids):raise GlobalSourceError('duplicate_pid_in_page')
        if len(cleaned)>15 or (not cleaned and data['has_more']):raise GlobalSourceError('page_no_progress')
        fingerprint=digest(data);pid_hash=digest(pids)
        with self.tx():
            run=self.get(id);old=self.db.execute('SELECT response_hash FROM global_source_page WHERE run_id=? AND page=?',(id,page)).fetchone()
            if old:
                if old[0]!=fingerprint:raise GlobalSourceError('page_replay_changed')
                return self.status(id)
            if run['state']!='collecting' or page!=run['next_page']:raise GlobalSourceError('unexpected_page')
            expected=list_request(page,run['reported_total'])
            if expected['page']!=page and request_payload is None:raise GlobalSourceError('page_request_evidence_required')
            if len(cleaned)>expected['page_size']:raise GlobalSourceError('page_size_mismatch')
            if request_payload is not None and request_payload!=expected:raise GlobalSourceError('page_request_scope_changed')
            if pids and self.db.execute('SELECT 1 FROM global_source_page WHERE run_id=? AND pids_hash=?',(id,pid_hash)).fetchone():raise GlobalSourceError('repeated_page')
            added=0
            for p in cleaned:
                exists=self.db.execute('SELECT 1 FROM global_source_product WHERE run_id=? AND pid=?',(id,p['product_id'])).fetchone()
                added+=not bool(exists)
                self.db.execute('INSERT INTO global_source_product VALUES(?,?,?,?,?,?,?) ON CONFLICT(run_id,pid) DO UPDATE SET payload=excluded.payload,fingerprint=excluded.fingerprint,last_page=excluded.last_page,observed=excluded.observed',(id,p['product_id'],encoded(p),digest(p),page,page,self.clock()))
            if not added and data['has_more']:raise GlobalSourceError('page_no_new_products')
            self.db.execute('INSERT INTO global_source_page(run_id,page,count,new_count,has_more,reported_total,response_hash,pids_hash,observed,request_payload) VALUES(?,?,?,?,?,?,?,?,?,?)',(id,page,len(pids),added,int(data['has_more']),data['total'],fingerprint,pid_hash,self.clock(),encoded(expected)))
            state='collecting' if data['has_more'] else 'completed'
            unique=self.db.execute('SELECT count(*) FROM global_source_product WHERE run_id=?',(id,)).fetchone()[0]
            totals={r[0] for r in self.db.execute('SELECT DISTINCT reported_total FROM global_source_page WHERE run_id=?',(id,))}
            reason='endpoint_end' if state=='completed' else None
            if state=='completed' and (len(totals)>1 or unique!=data['total']):state='partial';reason='endpoint_end_total_mismatch'
            self.db.execute('UPDATE global_source_run SET next_page=?,reported_total=?,state=?,updated=?,terminal_reason=? WHERE id=?',(page+1,data['total'],state,self.clock(),reason,id))
        return self.status(id)
    def retry_boundary_tail(self,id,proof):
        with self.tx():
            run=self.get(id)
            if run['state']!='blocked' or run['next_page']!=667 or run['reported_total']!=10000 or self.status(id)['products']!=9990:raise GlobalSourceError('not_boundary_retry')
            if proof.get('http')!=200 or proof.get('code')!=98001004 or proof.get('page')!=667 or proof.get('verification') is not False:raise GlobalSourceError('boundary_evidence_missing')
            self.db.execute("INSERT INTO global_source_attempt(run_id,page,at,code) VALUES(?,667,?,'aligned_tail_retry')",(id,self.clock()))
            self.db.execute("UPDATE global_source_run SET state='collecting',terminal_reason=NULL WHERE id=?",(id,))
    def finish_session(self,id,identity_unchanged):
        with self.tx():
            self.db.execute('UPDATE global_source_run SET identity_unchanged=? WHERE id=?',(int(identity_unchanged is True),id))
            run=self.get(id)
            if identity_unchanged is not True:
                self.db.execute("UPDATE global_source_run SET state='blocked',terminal_reason='identity_changed' WHERE id=?",(id,));return
            if run['state']=='completed':
                old=self.db.execute('SELECT r.created FROM global_source_head h JOIN global_source_run r ON r.id=h.run_id WHERE h.scope_hash=?',(run['scope_hash'],)).fetchone()
                if not old or old[0]<run['created']:self.db.execute('INSERT INTO global_source_head VALUES(?,?) ON CONFLICT(scope_hash) DO UPDATE SET run_id=excluded.run_id',(run['scope_hash'],id))
    def blocked(self,id,code):
        if not re.fullmatch(r'[A-Za-z0-9_]{1,100}',code):code='source_read_failed'
        with self.tx():
            row=self.get(id);self.db.execute('INSERT INTO global_source_attempt(run_id,page,at,code) VALUES(?,?,?,?)',(id,row['next_page'],self.clock(),code))
            self.db.execute("UPDATE global_source_run SET state='blocked',terminal_reason=?,updated=? WHERE id=?",(code,self.clock(),id))
    def detail(self,id,pid,listing_fingerprint,rows):
        clean=clean_details(rows)
        with self.tx():
            product=self.db.execute('SELECT fingerprint FROM global_source_product WHERE run_id=? AND pid=?',(id,pid)).fetchone()
            if not product or product[0]!=listing_fingerprint:raise GlobalSourceError('listing_changed')
            self.db.execute('INSERT INTO global_source_detail VALUES(?,?,?,?,?,?) ON CONFLICT(run_id,pid) DO UPDATE SET listing_fingerprint=excluded.listing_fingerprint,payload=excluded.payload,response_hash=excluded.response_hash,observed=excluded.observed',(id,pid,listing_fingerprint,encoded(clean),digest(rows),self.clock()))
    def stock(self,id,pid,listing_fingerprint,offers,response_hash):
        if not isinstance(offers,list) or any(o.get('pid')!=pid or o.get('catalogSource')!='selected' for o in offers):raise GlobalSourceError('stock_scope_mismatch')
        with self.tx():
            row=self.db.execute('SELECT fingerprint FROM global_source_product WHERE run_id=? AND pid=?',(id,pid)).fetchone()
            if not row or row[0]!=listing_fingerprint:raise GlobalSourceError('listing_changed')
            self.db.execute('INSERT INTO global_source_stock VALUES(?,?,?,?,?,?) ON CONFLICT(run_id,pid) DO UPDATE SET listing_fingerprint=excluded.listing_fingerprint,payload=excluded.payload,response_hash=excluded.response_hash,observed=excluded.observed',(id,pid,listing_fingerprint,encoded(offers),response_hash,self.clock()))
    def products(self,id=None,*,offset=0,limit=30,query=''):
        if type(offset) is not int or not 0<=offset<=1000000 or type(limit) is not int or not 1<=limit<=50 or not isinstance(query,str) or len(query)>100:raise GlobalSourceError('invalid_product_query')
        explicit=id is not None
        status=self.status(id)
        if not status.get('available'):return status|{'items':[],'totalMatches':0,'offset':offset,'limit':limit}
        id=status['id'] if explicit or not status.get('activePublished') else status['activePublished']['id']
        clause="p.run_id=? AND (p.pid LIKE ? OR json_extract(p.payload,'$.title') LIKE ?)";args=(id,'%'+query+'%','%'+query+'%')
        total=self.db.execute('SELECT count(*) FROM global_source_product p WHERE '+clause,args).fetchone()[0]
        items=[]
        for row in self.db.execute('SELECT p.*,d.payload details,s.payload stock FROM global_source_product p LEFT JOIN global_source_detail d ON d.run_id=p.run_id AND d.pid=p.pid AND d.listing_fingerprint=p.fingerprint LEFT JOIN global_source_stock s ON s.run_id=p.run_id AND s.pid=p.pid AND s.listing_fingerprint=p.fingerprint WHERE '+clause+' ORDER BY p.first_page,p.pid LIMIT ? OFFSET ?',(*args,limit,offset)):
            product=json.loads(row['payload'])
            offers=[]
            for old in json.loads(row['stock']) if row['stock'] else []:
                current=mark_full_managed(old,'global-source:'+id+':'+row['pid']+':'+row['fingerprint']);current['assessment']=assess_offer(current,self.clock());offers.append(current)
            items.append({'pid':row['pid'],'title':str(product.get('title') or row['pid']),'listedSelected':product.get('fs_is_selected'),'totalCommissionRaw':product.get('commission_rate'),'publicCommissionRaw':product.get('open_collab_rate'),'observedAt':row['observed'],'detailsChecked':row['details'] is not None,'stockChecked':row['stock'] is not None,'conditionsChecked':row['stock'] is not None,'stockRequired':False,'selectedOffers':offers})
        from lib.global_selection import observations
        intake,selection=observations(self.path.parent,id)
        for item in items:item['selectionObservation']=selection.get(item['pid'])
        return status|{'selectionBatch':intake,'displayRunId':id,'displayIsComplete':id==(status.get('activePublished') or {}).get('id'),'items':items,'totalMatches':total,'offset':offset,'limit':limit}
    def status(self,id=None):
        if id is None:
            row=self.db.execute('SELECT id FROM global_source_run ORDER BY created DESC LIMIT 1').fetchone()
            if not row:return {'available':False,'executionAllowed':False}
            id=row[0]
        r=self.get(id);count=self.db.execute('SELECT count(*) FROM global_source_product WHERE run_id=?',(id,)).fetchone()[0]
        detail_count=self.db.execute('SELECT count(*) FROM global_source_detail d JOIN global_source_product p ON p.run_id=d.run_id AND p.pid=d.pid AND p.fingerprint=d.listing_fingerprint WHERE d.run_id=?',(id,)).fetchone()[0]
        selected=self.db.execute("SELECT count(*) FROM global_source_product WHERE run_id=? AND json_extract(payload,'$.fs_is_selected')=1",(id,)).fetchone()[0]
        unselected=self.db.execute("SELECT count(*) FROM global_source_product WHERE run_id=? AND json_extract(payload,'$.fs_is_selected')=0",(id,)).fetchone()[0]
        active=self.db.execute("SELECT r.id,r.updated,(SELECT count(*) FROM global_source_product p WHERE p.run_id=r.id) products FROM global_source_head h JOIN global_source_run r ON r.id=h.run_id WHERE h.scope_hash=? AND r.state='completed' AND r.identity_unchanged=1",(r['scope_hash'],)).fetchone()
        return {'activePublished':dict(active) if active else None,'listedSelectedProducts':selected,'listedUnselectedProducts':unselected,'platformWrites':0,'modelCalls':0,'available':True,'id':id,'market':r['scope']['market'],'account':r['scope']['account'],'source':SOURCE,'state':r['state'],'products':count,'pages':r['next_page']-1,'reportedTotal':r['reported_total'],'nextPage':r['next_page'],'detailProducts':detail_count,'reason':r['terminal_reason'],
            'identityFileUnchanged':bool(r['identity_unchanged']),'published':bool(self.db.execute('SELECT 1 FROM global_source_head WHERE run_id=?',(id,)).fetchone()),'coverage':'current_query_endpoint_and_total' if r['state']=='completed' else 'partial_query',
            'updatedAt':r['updated'],'listingOnly':True,'stockVerified':False,'stockRequired':False,'executionAllowed':False,'sample':[{'pid':x['pid'],'title':json.loads(x['payload']).get('title'),'listedSelected':json.loads(x['payload']).get('fs_is_selected')} for x in self.db.execute('SELECT pid,payload FROM global_source_product WHERE run_id=? ORDER BY first_page,pid LIMIT 6',(id,))]}
