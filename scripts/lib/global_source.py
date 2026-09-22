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
    # Account and collection mechanics are compatibility slots, not the executing actor or a
    # second business source.  Category-partitioned runs therefore replace the old 10k-window head
    # only after they are complete, while preserving the exact v1 lineage hash.
    return digest({'market':scope['market'],'account':'acc6' if scope['market']=='it' else 'supply',
      'institutionFingerprint':scope['institutionFingerprint'],'source':scope.get('source',SOURCE),
      'filter':scope.get('filter',FILTER),'pageSize':scope.get('pageSize',15)})

def list_request(page,total=None,category_id=None):
    if type(page) is not int or not 1<=page<=2000:raise GlobalSourceError('invalid_page')
    if category_id is not None and (not isinstance(category_id,str) or not re.fullmatch(r'[0-9]{1,20}',category_id)):
        raise GlobalSourceError('invalid_category')
    # Live-verified tail: 9990 offset + 10 rows stays inside the 10000-result window.
    native_page,size=(1000,10) if page==667 and total==10000 else (page,15)
    request={'filter':deepcopy(FILTER),'page':native_page,'page_size':size}
    if category_id is not None:request['filter']['category_id']=[category_id]
    return request

def clean_categories(rows):
    if not isinstance(rows,list) or not 1<=len(rows)<=100:raise GlobalSourceError('category_tree_invalid')
    result=[];seen=set()
    for row in rows:
        category_id=str(row.get('category_id') or '') if isinstance(row,dict) else ''
        name=row.get('name') if isinstance(row,dict) else None;leaf=row.get('is_leaf') if isinstance(row,dict) else None
        if not re.fullmatch(r'[0-9]{1,20}',category_id) or category_id in seen or \
           not isinstance(name,str) or not name.strip() or len(name)>200 or type(leaf) is not bool:
            raise GlobalSourceError('category_tree_invalid')
        seen.add(category_id);result.append({'categoryId':category_id,'name':name.strip(),'isLeaf':leaf})
    return result

def normalize_page(data):
    """The category endpoint omits products for an explicit zero-result terminal page."""
    if isinstance(data,dict) and data.get('total')==0 and data.get('has_more') is False and data.get('products') is None:
        return {**data,'products':[]}
    return data

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
CREATE TABLE IF NOT EXISTS global_source_partition(
 run_id TEXT NOT NULL,category_id TEXT NOT NULL,category_name TEXT NOT NULL,position INTEGER NOT NULL,
 state TEXT NOT NULL,next_page INTEGER NOT NULL DEFAULT 1,reported_total INTEGER,page_count INTEGER NOT NULL DEFAULT 0,
 unique_count INTEGER NOT NULL DEFAULT 0,created REAL NOT NULL,updated REAL NOT NULL,terminal_reason TEXT,
 PRIMARY KEY(run_id,category_id),UNIQUE(run_id,position));
CREATE TABLE IF NOT EXISTS global_source_partition_page(
 run_id TEXT NOT NULL,category_id TEXT NOT NULL,page INTEGER NOT NULL,count INTEGER NOT NULL,
 category_new_count INTEGER NOT NULL,global_new_count INTEGER NOT NULL,has_more INTEGER NOT NULL,
 reported_total INTEGER NOT NULL,response_hash TEXT NOT NULL,pids_hash TEXT NOT NULL,observed REAL NOT NULL,
 request_payload TEXT NOT NULL,PRIMARY KEY(run_id,category_id,page));
CREATE TABLE IF NOT EXISTS global_source_product_category(
 run_id TEXT NOT NULL,pid TEXT NOT NULL,category_id TEXT NOT NULL,category_name TEXT NOT NULL,
 first_page INTEGER NOT NULL,last_page INTEGER NOT NULL,observed REAL NOT NULL,
 PRIMARY KEY(run_id,pid,category_id));
CREATE INDEX IF NOT EXISTS global_partition_state ON global_source_partition(run_id,state,position);
CREATE TABLE IF NOT EXISTS global_source_partition_repair_page(
 run_id TEXT NOT NULL,category_id TEXT NOT NULL,attempt INTEGER NOT NULL,page INTEGER NOT NULL,
 count INTEGER NOT NULL,new_count INTEGER NOT NULL,reported_total INTEGER NOT NULL,response_hash TEXT NOT NULL,
 pids_hash TEXT NOT NULL,observed REAL NOT NULL,request_payload TEXT NOT NULL,
 PRIMARY KEY(run_id,category_id,attempt,page));
CREATE TABLE IF NOT EXISTS global_source_operator_acceptance(
 run_id TEXT PRIMARY KEY,action TEXT NOT NULL,accepted_at REAL NOT NULL,products INTEGER NOT NULL,
 pages INTEGER NOT NULL,categories_completed INTEGER NOT NULL,category_count INTEGER NOT NULL,
 note TEXT NOT NULL);
'''

def validate_source_scope(scope):
    if not isinstance(scope,dict) or set(scope)!={'market','account','institutionFingerprint'} or \
       not isinstance(scope.get('account'),str) or not re.fullmatch(r'acc[1-9][0-9]*',scope['account']) or \
       not re.fullmatch(r'[a-f0-9]{64}',scope.get('institutionFingerprint','')):
        raise GlobalSourceError('unsupported_source_scope')
    from lib.market_registry import supports
    try:available=supports(None,scope['market'],'fullManagedCatalog')
    except (OSError,ValueError,KeyError):available=False
    if not available:raise GlobalSourceError('unsupported_source_scope')
    return scope

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
        validate_source_scope(scope)
        frozen={**scope,'source':SOURCE,'filter':FILTER,'pageSize':15}
        with self.tx():
            previous=self.db.execute('SELECT scope FROM global_source_run WHERE id=?',(id,)).fetchone()
            if previous:
                if previous[0]!=encoded(frozen):raise GlobalSourceError('run_scope_changed')
            elif self.db.execute("SELECT 1 FROM global_source_run WHERE scope_hash=? AND state='collecting'",(source_lineage_key(frozen),)).fetchone():raise GlobalSourceError('scan_already_collecting')
            else:self.db.execute("INSERT INTO global_source_run(id,scope,scope_hash,state,created,updated) VALUES(?,?,?,'collecting',?,?)",(id,encoded(frozen),source_lineage_key(frozen),self.clock(),self.clock()))
        return self.get(id)
    def start_partitioned(self,id,scope,categories):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',id):raise GlobalSourceError('invalid_run_id')
        validate_source_scope(scope)
        categories=clean_categories(categories)
        frozen={**scope,'source':SOURCE,'filter':FILTER,'pageSize':15,
                'partitionMode':'category_l1_v1','categories':categories}
        with self.tx():
            previous=self.db.execute('SELECT scope FROM global_source_run WHERE id=?',(id,)).fetchone()
            if previous:
                if previous[0]!=encoded(frozen):raise GlobalSourceError('run_scope_changed')
            elif self.db.execute("SELECT 1 FROM global_source_run WHERE scope_hash=? AND state='collecting'",(source_lineage_key(frozen),)).fetchone():
                raise GlobalSourceError('scan_already_collecting')
            else:
                now=self.clock();self.db.execute("INSERT INTO global_source_run(id,scope,scope_hash,state,created,updated) VALUES(?,?,?,'collecting',?,?)",(id,encoded(frozen),source_lineage_key(frozen),now,now))
                for position,row in enumerate(categories):
                    self.db.execute("INSERT INTO global_source_partition VALUES(?,?,?,?, 'queued',1,NULL,0,0,?,?,NULL)",
                      (id,row['categoryId'],row['name'],position,now,now))
        return self.get(id)
    def next_partition(self,id):
        with self.tx():
            run=self.get(id)
            if run['state']!='collecting' or run['scope'].get('partitionMode')!='category_l1_v1':
                return None
            row=self.db.execute("SELECT * FROM global_source_partition WHERE run_id=? AND state='collecting' ORDER BY position LIMIT 1",(id,)).fetchone()
            if not row:
                row=self.db.execute("SELECT * FROM global_source_partition WHERE run_id=? AND state='queued' ORDER BY position LIMIT 1",(id,)).fetchone()
                if row:
                    self.db.execute("UPDATE global_source_partition SET state='collecting',updated=? WHERE run_id=? AND category_id=?",(self.clock(),id,row['category_id']))
                    row=self.db.execute("SELECT * FROM global_source_partition WHERE run_id=? AND category_id=?",(id,row['category_id'])).fetchone()
        return dict(row) if row else None
    def partition_page(self,id,category_id,page,data,*,request_payload):
        data=normalize_page(data)
        if not isinstance(data,dict) or type(data.get('has_more')) is not bool or type(data.get('total')) is not int or data['total']<0 or not isinstance(data.get('products'),list):raise GlobalSourceError('page_shape_invalid')
        cleaned=[clean_product(row) for row in data['products']];pids=[row['product_id'] for row in cleaned]
        if len(set(pids))!=len(pids):raise GlobalSourceError('duplicate_pid_in_page')
        if len(cleaned)>15 or (not cleaned and data['has_more']):raise GlobalSourceError('page_no_progress')
        fingerprint=digest(data);pid_hash=digest(pids)
        with self.tx():
            run=self.get(id);scope=run['scope']
            if run['state']!='collecting' or scope.get('partitionMode')!='category_l1_v1':raise GlobalSourceError('unexpected_page')
            part=self.db.execute('SELECT * FROM global_source_partition WHERE run_id=? AND category_id=?',(id,str(category_id))).fetchone()
            old=self.db.execute('SELECT response_hash FROM global_source_partition_page WHERE run_id=? AND category_id=? AND page=?',(id,str(category_id),page)).fetchone()
            if old:
                if old[0]!=fingerprint:raise GlobalSourceError('page_replay_changed')
                return self.status(id)
            if not part or part['state']!='collecting' or page!=part['next_page']:raise GlobalSourceError('unexpected_page')
            expected=list_request(page,part['reported_total'],str(category_id))
            if request_payload!=expected:raise GlobalSourceError('page_request_scope_changed')
            if len(cleaned)>expected['page_size']:raise GlobalSourceError('page_size_mismatch')
            if pids and self.db.execute('SELECT 1 FROM global_source_partition_page WHERE run_id=? AND category_id=? AND pids_hash=?',(id,str(category_id),pid_hash)).fetchone():raise GlobalSourceError('repeated_page')
            global_added=category_added=0;sequence=part['position']*2000+page;now=self.clock()
            for product in cleaned:
                pid=product['product_id']
                exists=self.db.execute('SELECT 1 FROM global_source_product WHERE run_id=? AND pid=?',(id,pid)).fetchone();global_added+=not bool(exists)
                member=self.db.execute('SELECT 1 FROM global_source_product_category WHERE run_id=? AND pid=? AND category_id=?',(id,pid,str(category_id))).fetchone();category_added+=not bool(member)
                self.db.execute('''INSERT INTO global_source_product VALUES(?,?,?,?,?,?,?)
                  ON CONFLICT(run_id,pid) DO UPDATE SET payload=excluded.payload,fingerprint=excluded.fingerprint,
                    last_page=excluded.last_page,observed=excluded.observed''',
                  (id,pid,encoded(product),digest(product),sequence,sequence,now))
                self.db.execute('''INSERT INTO global_source_product_category VALUES(?,?,?,?,?,?,?)
                  ON CONFLICT(run_id,pid,category_id) DO UPDATE SET last_page=excluded.last_page,observed=excluded.observed''',
                  (id,pid,str(category_id),part['category_name'],page,page,now))
            if not category_added and data['has_more']:raise GlobalSourceError('page_no_progress')
            self.db.execute('INSERT INTO global_source_partition_page VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
              (id,str(category_id),page,len(cleaned),category_added,global_added,int(data['has_more']),data['total'],fingerprint,pid_hash,now,encoded(expected)))
            unique=self.db.execute('SELECT count(*) FROM global_source_product_category WHERE run_id=? AND category_id=?',(id,str(category_id))).fetchone()[0]
            totals={row[0] for row in self.db.execute('SELECT DISTINCT reported_total FROM global_source_partition_page WHERE run_id=? AND category_id=?',(id,str(category_id)))}
            state='collecting' if data['has_more'] else 'completed';reason=None if data['has_more'] else 'endpoint_end'
            if state=='completed' and (len(totals)>1 or unique!=data['total']):state='partial';reason='category_endpoint_total_mismatch'
            self.db.execute('UPDATE global_source_partition SET next_page=?,reported_total=?,page_count=page_count+1,unique_count=?,state=?,updated=?,terminal_reason=? WHERE run_id=? AND category_id=?',
              (page+1,data['total'],unique,state,now,reason,id,str(category_id)))
            pages=self.db.execute('SELECT coalesce(sum(page_count),0) FROM global_source_partition WHERE run_id=?',(id,)).fetchone()[0]
            if state=='partial':
                self.db.execute("UPDATE global_source_run SET state='partial',next_page=?,updated=?,terminal_reason='category_endpoint_total_mismatch' WHERE id=?",(pages+1,now,id))
            elif state=='completed' and not self.db.execute("SELECT 1 FROM global_source_partition WHERE run_id=? AND state<>'completed'",(id,)).fetchone():
                reported=self.db.execute('SELECT coalesce(sum(reported_total),0) FROM global_source_partition WHERE run_id=?',(id,)).fetchone()[0]
                self.db.execute("UPDATE global_source_run SET state='completed',next_page=?,reported_total=?,updated=?,terminal_reason='category_endpoints_complete' WHERE id=?",(pages+1,reported,now,id))
            else:self.db.execute('UPDATE global_source_run SET next_page=?,updated=? WHERE id=?',(pages+1,now,id))
        return self.status(id)
    def page(self,id,page,data,*,request_payload=None):
        data=normalize_page(data)
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
    def retry_blocked_partition(self,id,proof,max_attempts=3):
        """Retry the same category page in a fresh signed session after code 98001004."""
        with self.tx():
            run=self.get(id)
            partition=self.db.execute("SELECT * FROM global_source_partition WHERE run_id=? AND state='blocked' ORDER BY position LIMIT 1",(id,)).fetchone()
            if run['state']!='blocked' or not partition or proof.get('http')!=200 or proof.get('code')!=98001004 or \
                    proof.get('verification') is not False or proof.get('page')!=partition['next_page'] or \
                    str(proof.get('categoryId'))!=partition['category_id']:
                raise GlobalSourceError('partition_retry_evidence_missing')
            attempts=self.db.execute("SELECT count(*) FROM global_source_attempt WHERE run_id=? AND page=? AND code='partition_signature_retry'",(id,partition['next_page'])).fetchone()[0]
            if attempts>=max_attempts:raise GlobalSourceError('partition_retry_limit')
            self.db.execute("INSERT INTO global_source_attempt(run_id,page,at,code) VALUES(?,?,?,'partition_signature_retry')",(id,partition['next_page'],self.clock()))
            self.db.execute("UPDATE global_source_partition SET state='collecting',terminal_reason=NULL,updated=? WHERE run_id=? AND category_id=?",(self.clock(),id,partition['category_id']))
            self.db.execute("UPDATE global_source_run SET state='collecting',terminal_reason=NULL,updated=? WHERE id=?",(self.clock(),id))
    def resume_partition_after_relogin(self,id,proof,published_at):
        with self.tx():
            run=self.get(id);part=self.db.execute("SELECT * FROM global_source_partition WHERE run_id=? AND state='blocked' ORDER BY position LIMIT 1",(id,)).fetchone()
            if run['state']!='blocked' or not part or proof.get('http')!=200 or proof.get('code')!=16201010 or \
                    proof.get('verification') is not False or proof.get('page')!=part['next_page'] or \
                    str(proof.get('categoryId'))!=part['category_id'] or not isinstance(published_at,(int,float)) or published_at<=run['updated']:
                raise GlobalSourceError('relogin_resume_evidence_missing')
            self.db.execute("INSERT INTO global_source_attempt(run_id,page,at,code) VALUES(?,?,?,'relogin_verified_resume')",(id,part['next_page'],self.clock()))
            now=self.clock();self.db.execute("UPDATE global_source_partition SET state='collecting',terminal_reason=NULL,updated=? WHERE run_id=? AND category_id=?",(now,id,part['category_id']))
            self.db.execute("UPDATE global_source_run SET state='collecting',terminal_reason=NULL,updated=? WHERE id=?",(now,id))
    def retry_partial_partition(self,id):
        """Discard one unpublished inconsistent category and re-read it from page one."""
        with self.tx():
            run=self.get(id)
            if run['state']!='partial' or self.db.execute('SELECT 1 FROM global_source_head WHERE run_id=?',(id,)).fetchone():
                raise GlobalSourceError('partial_partition_retry_invalid')
            rows=self.db.execute("SELECT category_id FROM global_source_partition WHERE run_id=? AND state='partial' ORDER BY position",(id,)).fetchall()
            if len(rows)!=1:raise GlobalSourceError('partial_partition_retry_invalid')
            category_id=rows[0][0]
            self.db.execute('DELETE FROM global_source_partition_page WHERE run_id=? AND category_id=?',(id,category_id))
            self.db.execute('DELETE FROM global_source_product_category WHERE run_id=? AND category_id=?',(id,category_id))
            self.db.execute('DELETE FROM global_source_product WHERE run_id=? AND NOT EXISTS (SELECT 1 FROM global_source_product_category c WHERE c.run_id=global_source_product.run_id AND c.pid=global_source_product.pid)',(id,))
            now=self.clock();self.db.execute("UPDATE global_source_partition SET state='queued',next_page=1,reported_total=NULL,page_count=0,unique_count=0,updated=?,terminal_reason=NULL WHERE run_id=? AND category_id=?",(now,id,category_id))
            pages=self.db.execute('SELECT coalesce(sum(page_count),0) FROM global_source_partition WHERE run_id=?',(id,)).fetchone()[0]
            self.db.execute("UPDATE global_source_run SET state='collecting',next_page=?,reported_total=NULL,updated=?,terminal_reason=NULL WHERE id=?",(pages+1,now,id))
            return category_id
    def partial_repair_scope(self,id):
        run=self.get(id)
        if run['state']!='partial':raise GlobalSourceError('partial_partition_repair_invalid')
        part=self.db.execute("SELECT * FROM global_source_partition WHERE run_id=? AND state='partial' ORDER BY position LIMIT 1",(id,)).fetchone()
        if not part:raise GlobalSourceError('partial_partition_repair_invalid')
        duplicates=[row[0] for row in self.db.execute("SELECT page FROM global_source_partition_page WHERE run_id=? AND category_id=? AND category_new_count<count ORDER BY page",(id,part['category_id']))]
        attempt=self.db.execute("SELECT coalesce(max(attempt),0)+1 FROM global_source_partition_repair_page WHERE run_id=? AND category_id=?",(id,part['category_id'])).fetchone()[0]
        radius=10 if attempt<=2 else 25
        pages=(list(range(1,part['page_count']+1)) if attempt>=4 else
               sorted({max(1,page+delta) for page in duplicates for delta in range(-radius,radius+1)}))
        return {'categoryId':part['category_id'],'reportedTotal':part['reported_total'],'uniqueCount':part['unique_count'],
                'pages':pages,'attempt':attempt}
    def repair_partition_page(self,id,category_id,page,data,request_payload,attempt):
        data=normalize_page(data)
        if not isinstance(data,dict) or type(data.get('total')) is not int or not isinstance(data.get('products'),list):raise GlobalSourceError('page_shape_invalid')
        cleaned=[clean_product(row) for row in data['products']];pids=[row['product_id'] for row in cleaned]
        if len(set(pids))!=len(pids):raise GlobalSourceError('duplicate_pid_in_page')
        with self.tx():
            run=self.get(id);part=self.db.execute('SELECT * FROM global_source_partition WHERE run_id=? AND category_id=?',(id,str(category_id))).fetchone()
            if run['state']!='partial' or not part or part['state']!='partial' or data['total']!=part['reported_total'] or request_payload!=list_request(page,part['reported_total'],str(category_id)):
                raise GlobalSourceError('partial_partition_repair_invalid')
            added=0;now=self.clock();sequence=part['position']*2000+page
            for product in cleaned:
                pid=product['product_id'];member=self.db.execute('SELECT 1 FROM global_source_product_category WHERE run_id=? AND pid=? AND category_id=?',(id,pid,str(category_id))).fetchone()
                if member:continue
                added+=1
                self.db.execute('INSERT OR IGNORE INTO global_source_product VALUES(?,?,?,?,?,?,?)',(id,pid,encoded(product),digest(product),sequence,sequence,now))
                self.db.execute('INSERT INTO global_source_product_category VALUES(?,?,?,?,?,?,?)',(id,pid,str(category_id),part['category_name'],page,page,now))
            self.db.execute('INSERT INTO global_source_partition_repair_page VALUES(?,?,?,?,?,?,?,?,?,?,?)',(id,str(category_id),int(attempt),int(page),len(cleaned),added,data['total'],digest(data),digest(pids),now,encoded(request_payload)))
            unique=self.db.execute('SELECT count(*) FROM global_source_product_category WHERE run_id=? AND category_id=?',(id,str(category_id))).fetchone()[0]
            if unique>part['reported_total']:
                self.db.execute("UPDATE global_source_partition SET unique_count=?,terminal_reason='category_repair_union_exceeds_total' WHERE run_id=? AND category_id=?",(unique,id,str(category_id)))
                raise GlobalSourceError('category_repair_union_exceeds_total')
            if unique==part['reported_total']:
                self.db.execute("UPDATE global_source_partition SET state='completed',unique_count=?,terminal_reason='endpoint_end_reconciled',updated=? WHERE run_id=? AND category_id=?",(unique,now,id,str(category_id)))
                pages=self.db.execute('SELECT coalesce(sum(page_count),0) FROM global_source_partition WHERE run_id=?',(id,)).fetchone()[0]
                if not self.db.execute("SELECT 1 FROM global_source_partition WHERE run_id=? AND state<>'completed'",(id,)).fetchone():
                    reported=self.db.execute('SELECT coalesce(sum(reported_total),0) FROM global_source_partition WHERE run_id=?',(id,)).fetchone()[0]
                    self.db.execute("UPDATE global_source_run SET state='completed',next_page=?,reported_total=?,terminal_reason='category_endpoints_complete',updated=? WHERE id=?",(pages+1,reported,now,id))
                else:self.db.execute("UPDATE global_source_run SET state='collecting',next_page=?,terminal_reason=NULL,updated=? WHERE id=?",(pages+1,now,id))
            else:self.db.execute('UPDATE global_source_partition SET unique_count=?,updated=? WHERE run_id=? AND category_id=?',(unique,now,id,str(category_id)))
        return {'added':added,'uniqueCount':unique,'complete':unique==part['reported_total']}
    def accept_stable_duplicate_rows(self,id,max_duplicates=5):
        """Accept endpoint-complete rows when bounded duplicates replay identically."""
        with self.tx():
            run=self.get(id);part=self.db.execute("SELECT * FROM global_source_partition WHERE run_id=? AND state='partial' ORDER BY position LIMIT 1",(id,)).fetchone()
            if run['state']!='partial' or not part:raise GlobalSourceError('stable_duplicate_evidence_missing')
            rows=self.db.execute('SELECT coalesce(sum(count),0) FROM global_source_partition_page WHERE run_id=? AND category_id=?',(id,part['category_id'])).fetchone()[0]
            duplicates=rows-part['unique_count'];limit=max(max_duplicates,int(part['reported_total'])//1000)
            if rows!=part['reported_total'] or not 1<=duplicates<=limit:raise GlobalSourceError('stable_duplicate_evidence_missing')
            duplicate_pages=self.db.execute('SELECT page,pids_hash,count-category_new_count FROM global_source_partition_page WHERE run_id=? AND category_id=? AND category_new_count<count',(id,part['category_id'])).fetchall()
            if not duplicate_pages:raise GlobalSourceError('stable_duplicate_evidence_missing')
            latest=self.db.execute('SELECT max(attempt) FROM global_source_partition_repair_page WHERE run_id=? AND category_id=?',(id,part['category_id'])).fetchone()[0]
            repair_summary=self.db.execute('SELECT count(*),coalesce(sum(new_count),0) FROM global_source_partition_repair_page WHERE run_id=? AND category_id=? AND attempt=?',(id,part['category_id'],latest)).fetchone()
            stable=duplicates if repair_summary[0]==part['page_count'] and repair_summary[1]==0 else 0
            if not stable:
                for page,pids_hash,duplicate_rows in duplicate_pages:
                    repair=self.db.execute('SELECT pids_hash,new_count FROM global_source_partition_repair_page WHERE run_id=? AND category_id=? AND page=? ORDER BY attempt DESC LIMIT 1',(id,part['category_id'],page)).fetchone()
                    if repair and repair[0]==pids_hash and repair[1]==0:stable+=duplicate_rows
            if stable!=duplicates:raise GlobalSourceError('stable_duplicate_evidence_missing')
            now=self.clock();reason=f'endpoint_end_stable_duplicate_rows_{duplicates}'
            self.db.execute('UPDATE global_source_partition SET state=\'completed\',terminal_reason=?,updated=? WHERE run_id=? AND category_id=?',(reason,now,id,part['category_id']))
            pages=self.db.execute('SELECT coalesce(sum(page_count),0) FROM global_source_partition WHERE run_id=?',(id,)).fetchone()[0]
            if not self.db.execute("SELECT 1 FROM global_source_partition WHERE run_id=? AND state<>'completed'",(id,)).fetchone():
                reported=self.db.execute('SELECT coalesce(sum(reported_total),0) FROM global_source_partition WHERE run_id=?',(id,)).fetchone()[0]
                self.db.execute("UPDATE global_source_run SET state='completed',next_page=?,reported_total=?,terminal_reason='category_endpoints_complete',updated=? WHERE id=?",(pages+1,reported,now,id))
            else:self.db.execute("UPDATE global_source_run SET state='collecting',next_page=?,terminal_reason=NULL,updated=? WHERE id=?",(pages+1,now,id))
            return {'categoryId':part['category_id'],'duplicateRows':duplicates,'reason':reason}
    def accept_partial_snapshot(self,id):
        """Freeze and publish a user-approved partial category snapshot.

        This is deliberately distinct from ``completed``: downstream work may consume the exact
        frozen products, while status and UI retain the incomplete category coverage forever.
        It is only valid after the collector closed its authenticated session cleanly.
        """
        with self.tx():
            run=self.get(id)
            existing=self.db.execute('SELECT * FROM global_source_operator_acceptance WHERE run_id=?',(id,)).fetchone()
            if run['state']=='accepted_partial' and existing:
                return dict(existing)
            if run['state']!='collecting' or run['scope'].get('partitionMode')!='category_l1_v1' or not run['identity_unchanged']:
                raise GlobalSourceError('partial_snapshot_acceptance_invalid')
            partitions=self.db.execute('SELECT * FROM global_source_partition WHERE run_id=? ORDER BY position',(id,)).fetchall()
            completed=sum(row['state']=='completed' for row in partitions)
            active=[row for row in partitions if row['state']=='collecting']
            if not partitions or completed<1 or completed>=len(partitions) or len(active)>1 or \
                    any(row['state'] in ('partial','blocked') for row in partitions):
                raise GlobalSourceError('partial_snapshot_acceptance_invalid')
            products=self.db.execute('SELECT count(*) FROM global_source_product WHERE run_id=?',(id,)).fetchone()[0]
            pages=self.db.execute('SELECT coalesce(sum(page_count),0) FROM global_source_partition WHERE run_id=?',(id,)).fetchone()[0]
            if not products or not pages:raise GlobalSourceError('partial_snapshot_acceptance_invalid')
            old=self.db.execute('SELECT r.created,r.id FROM global_source_head h JOIN global_source_run r ON r.id=h.run_id WHERE h.scope_hash=?',(run['scope_hash'],)).fetchone()
            if old and old['created']>run['created']:raise GlobalSourceError('newer_source_head_exists')
            now=self.clock();note='operator_accepted_partial'
            if active:
                self.db.execute("UPDATE global_source_partition SET state='accepted_partial',terminal_reason=?,updated=? WHERE run_id=? AND category_id=?",
                                (note,now,id,active[0]['category_id']))
            self.db.execute("UPDATE global_source_run SET state='accepted_partial',terminal_reason=?,updated=? WHERE id=?",(note,now,id))
            self.db.execute('INSERT INTO global_source_operator_acceptance VALUES(?,?,?,?,?,?,?,?)',
                            (id,'accept_partial_snapshot',now,products,pages,completed,len(partitions),note))
            self.db.execute('INSERT INTO global_source_head VALUES(?,?) ON CONFLICT(scope_hash) DO UPDATE SET run_id=excluded.run_id',(run['scope_hash'],id))
            return dict(self.db.execute('SELECT * FROM global_source_operator_acceptance WHERE run_id=?',(id,)).fetchone())
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
            if row['scope'].get('partitionMode')=='category_l1_v1':
                self.db.execute("UPDATE global_source_partition SET state='blocked',terminal_reason=?,updated=? WHERE run_id=? AND state='collecting'",(code,self.clock(),id))
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
        intake,selection=observations(self.path.parent,id,status['market'])
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
        active=self.db.execute("SELECT r.id,r.updated,r.state,(SELECT count(*) FROM global_source_product p WHERE p.run_id=r.id) products FROM global_source_head h JOIN global_source_run r ON r.id=h.run_id WHERE h.scope_hash=? AND r.state IN ('completed','accepted_partial') AND r.identity_unchanged=1",(r['scope_hash'],)).fetchone()
        partitioned=r['scope'].get('partitionMode')=='category_l1_v1';partitions=[]
        if partitioned:partitions=[dict(row) for row in self.db.execute('SELECT * FROM global_source_partition WHERE run_id=? ORDER BY position',(id,))]
        pages=sum(row['page_count'] for row in partitions) if partitioned else r['next_page']-1
        reported=sum((row['reported_total'] or 0) for row in partitions) if partitioned else r['reported_total']
        memberships=self.db.execute('SELECT count(*) FROM global_source_product_category WHERE run_id=?',(id,)).fetchone()[0] if partitioned else count
        next_partition=next((row for row in partitions if row['state'] in ('collecting','queued','blocked')),None)
        stable_duplicates=sum(max(0,(row['reported_total'] or 0)-row['unique_count']) for row in partitions if row['state']=='completed') if partitioned else 0
        coverage=('operator_accepted_partial' if r['state']=='accepted_partial' else
                  ('category_l1_endpoint_and_totals' if partitioned else 'current_query_endpoint_and_total') if r['state']=='completed' else 'partial_query')
        acceptance=(self.db.execute('SELECT action,accepted_at,products,pages,categories_completed,category_count,note FROM global_source_operator_acceptance WHERE run_id=?',(id,)).fetchone()
                    if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='global_source_operator_acceptance'").fetchone() else None)
        return {'activePublished':dict(active) if active else None,'listedSelectedProducts':selected,'listedUnselectedProducts':unselected,'platformWrites':0,'modelCalls':0,'available':True,'id':id,'market':r['scope']['market'],'account':r['scope']['account'],'source':SOURCE,'state':r['state'],'products':count,'pages':pages,'reportedTotal':reported,'stableDuplicateRows':stable_duplicates,'nextPage':next_partition['next_page'] if next_partition else r['next_page'],'detailProducts':detail_count,'reason':r['terminal_reason'],
            'identityFileUnchanged':bool(r['identity_unchanged']),'published':bool(self.db.execute('SELECT 1 FROM global_source_head WHERE run_id=?',(id,)).fetchone()),'coverage':coverage,
            'partitionMode':r['scope'].get('partitionMode'),'categoryCount':len(partitions) if partitioned else None,
            'categoriesCompleted':sum(row['state']=='completed' for row in partitions) if partitioned else None,
            'categoryMemberships':memberships if partitioned else None,'categoryOverlap':memberships-count if partitioned else None,
            'nextCategory':({'categoryId':next_partition['category_id'],'name':next_partition['category_name'],'state':next_partition['state'],'nextPage':next_partition['next_page']} if next_partition else None),
            'updatedAt':r['updated'],'elapsedSeconds':max(0,r['updated']-r['created']),
            'operatorAcceptance':dict(acceptance) if acceptance else None,
            'listingOnly':True,'stockVerified':False,'stockRequired':False,'executionAllowed':False,'sample':[{'pid':x['pid'],'title':json.loads(x['payload']).get('title'),'listedSelected':json.loads(x['payload']).get('fs_is_selected')} for x in self.db.execute('SELECT pid,payload FROM global_source_product WHERE run_id=? ORDER BY first_page,pid LIMIT 6',(id,))]}
