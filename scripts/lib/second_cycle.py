"""Local-only second outreach control and capacity-driven source queue.

No network, model, credential, send or link creation code belongs in this module.
Existing trial/simulator stores are not controlled by this staging slice.
"""
from contextlib import contextmanager
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
from lib.outreach_policy import MARKETING_COOLDOWN_SECONDS,last_contact_by_creator
import sqlite3
import time
from lib.product_stock_policy import require_stock,full_managed,mark_full_managed

class CycleError(ValueError):
    pass

def encoded(v):
    return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)

def digest(v):
    return hashlib.sha256(encoded(v).encode()).hexdigest()

def identifier(v):
    if not isinstance(v,str) or not v.strip() or len(v)>180 or any(ord(c)<32 for c in v):raise CycleError('invalid_identifier')
    return v

def integer(v,maximum=100000):
    if type(v) is not int or not 0<=v<=maximum:raise CycleError('invalid_integer')
    return v

def decimal(v):
    if v is None:return None
    if isinstance(v,bool):raise ValueError()
    d=Decimal(str(v))
    if not d.is_finite():raise ValueError()
    return d

def epoch(v):
    if isinstance(v,bool):raise ValueError()
    if isinstance(v,(int,float)):
        if not 0<float(v)<1e12:raise ValueError()
        return float(v)
    d=datetime.fromisoformat(v.replace('Z','+00:00'))
    if d.tzinfo is None:raise ValueError()
    return d.timestamp()

def assess_offer(offer,at):
    reasons=[]
    if offer.get('executionHold'):reasons.append('current_offer_on_hold')
    stock_needed=require_stock(offer)
    for key in (('stock',) if stock_needed else ())+('creatorPercent','publicPercent','endAt','available'):
        if offer.get(key) is None:reasons.append('missing_'+key)
    try:
        stock=decimal(offer.get('stock')) if stock_needed else None; creator=decimal(offer.get('creatorPercent')); public=decimal(offer.get('publicPercent'))
        if stock is not None and (stock<0 or stock!=stock.to_integral_value()):reasons.append('invalid_stock')
        elif stock is not None and stock<=100:reasons.append('stock_not_over_100')
        if any(v is not None and not 0<=v<=100 for v in (creator,public)):reasons.append('invalid_commission')
        elif creator is not None and public is not None and creator<=public:reasons.append('no_creator_commission_advantage')
        if offer.get('endAt') is not None and epoch(offer['endAt'])<=at+45*86400:reasons.append('expiry_not_over_45_days')
    except (ValueError,TypeError,InvalidOperation,OverflowError):reasons.append('invalid_commercial_fact')
    if offer.get('available') is not None and type(offer['available']) is not bool:reasons.append('invalid_available')
    elif offer.get('available') is False:reasons.append('unavailable')
    try:
        rating=decimal(offer.get('rating')); preferred=rating is not None and 4<rating<=5
    except (ValueError,InvalidOperation):preferred=False
    return {'eligible':not reasons,'reasons':reasons,'ratingPreferred':preferred,'stockRequired':stock_needed,'executionAllowed':False}

SCHEMA='''
CREATE TABLE IF NOT EXISTS cycle_meta(version INTEGER PRIMARY KEY CHECK(version=1));
INSERT OR IGNORE INTO cycle_meta VALUES(1);
CREATE TABLE IF NOT EXISTS plan(id TEXT PRIMARY KEY,institution TEXT NOT NULL,market TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'active',revision INTEGER NOT NULL DEFAULT 1,capacity_new INTEGER NOT NULL DEFAULT 0,capacity_established INTEGER NOT NULL DEFAULT 0,UNIQUE(institution,market));
CREATE TABLE IF NOT EXISTS cycle_product_management(plan_id TEXT NOT NULL,pid TEXT NOT NULL,kind TEXT NOT NULL,evidence_ref TEXT NOT NULL,observed REAL NOT NULL,PRIMARY KEY(plan_id,pid));
CREATE TABLE IF NOT EXISTS relationship(plan_id TEXT NOT NULL REFERENCES plan(id),creator_id TEXT NOT NULL,oec TEXT NOT NULL,mode TEXT NOT NULL DEFAULT 'auto',rejected INTEGER NOT NULL DEFAULT 0,revision INTEGER NOT NULL DEFAULT 1,inbox_until REAL NOT NULL DEFAULT 0,unlocked INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(plan_id,creator_id),UNIQUE(plan_id,oec));
CREATE TABLE IF NOT EXISTS control_event(plan_id TEXT NOT NULL,event_id TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(plan_id,event_id));
CREATE TABLE IF NOT EXISTS catalog(id TEXT PRIMARY KEY,plan_id TEXT NOT NULL REFERENCES plan(id),source TEXT NOT NULL,observed REAL NOT NULL,state TEXT NOT NULL,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS catalog_head(plan_id TEXT NOT NULL,source TEXT NOT NULL,snapshot_id TEXT NOT NULL REFERENCES catalog(id),PRIMARY KEY(plan_id,source));
CREATE TABLE IF NOT EXISTS source_job(id TEXT PRIMARY KEY,plan_id TEXT NOT NULL REFERENCES plan(id),snapshot_id TEXT NOT NULL,offer_key TEXT NOT NULL,pid TEXT NOT NULL,window_start TEXT NOT NULL,window_end TEXT NOT NULL,estimated INTEGER NOT NULL,state TEXT NOT NULL DEFAULT 'queued',cursor TEXT NOT NULL DEFAULT '',owner TEXT,fence INTEGER NOT NULL DEFAULT 0,lease_until REAL NOT NULL DEFAULT 0,UNIQUE(plan_id,snapshot_id,offer_key,window_start,window_end));
CREATE TABLE IF NOT EXISTS source_page(job_id TEXT NOT NULL,cursor TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(job_id,cursor));
CREATE TABLE IF NOT EXISTS source_edge(plan_id TEXT NOT NULL,source_id TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(plan_id,source_id));
CREATE TABLE IF NOT EXISTS opportunity(plan_id TEXT NOT NULL,creator_id TEXT NOT NULL,pid TEXT NOT NULL,offer_key TEXT NOT NULL,units INTEGER NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(plan_id,creator_id,pid,offer_key),FOREIGN KEY(plan_id,creator_id) REFERENCES relationship(plan_id,creator_id));
CREATE TABLE IF NOT EXISTS cycle_identity_outcome(plan_id TEXT NOT NULL,source_id TEXT NOT NULL,status TEXT NOT NULL,PRIMARY KEY(plan_id,source_id));
CREATE TABLE IF NOT EXISTS cycle_identity_resolution(plan_id TEXT NOT NULL,source_id TEXT NOT NULL,creator_id TEXT NOT NULL,oec TEXT NOT NULL,evidence_ref TEXT NOT NULL,PRIMARY KEY(plan_id,source_id));
CREATE INDEX IF NOT EXISTS cycle_job_claim ON source_job(plan_id,state,lease_until);
'''

class CycleStore:
    def __init__(self,path,clock=time.time,readonly=False):
        self.clock=clock
        if not readonly:Path(path).parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro' if readonly else path,uri=readonly,timeout=10,isolation_level=None);self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        if not readonly:
            self.db.execute('PRAGMA journal_mode=WAL');self.db.executescript(SCHEMA)
            for table,column in [('plan','capacity_new'),('plan','capacity_established'),('relationship','unlocked')]:
                if column not in {r['name'] for r in self.db.execute(f'PRAGMA table_info({table})')}:
                    self.db.execute(f'ALTER TABLE {table} ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0')
    def close(self):self.db.close()
    def __enter__(self):return self
    def __exit__(self,*_):self.close()
    @contextmanager
    def tx(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:yield;self.db.execute('COMMIT')
        except BaseException:self.db.execute('ROLLBACK');raise
    def plan(self,institution,market):
        identifier(institution)
        from lib.market_registry import enabled_market_keys
        if market not in enabled_market_keys():raise CycleError('invalid_market')
        pid='cycle-'+digest([institution,market])[:24]
        with self.tx():self.db.execute('INSERT OR IGNORE INTO plan(id,institution,market) VALUES(?,?,?)',(pid,institution,market))
        return pid
    def _plan(self,p):
        r=self.db.execute('SELECT * FROM plan WHERE id=?',(p,)).fetchone()
        if r is None:raise CycleError('plan_missing')
        return r
    def control(self,p,event_id,expected_revision,mode,creator_id=None,reject=False):
        identifier(event_id)
        if type(expected_revision) is not int or expected_revision<1:raise CycleError('invalid_revision')
        if mode not in ('auto','human','paused','active') or type(reject) is not bool:raise CycleError('invalid_control')
        if creator_id is None and mode not in ('active','paused'):raise CycleError('invalid_plan_control')
        if creator_id is not None and mode=='active':raise CycleError('invalid_relationship_control')
        payload=encoded([creator_id,expected_revision,mode,reject])
        with self.tx():
            self._plan(p);old=self.db.execute('SELECT payload FROM control_event WHERE plan_id=? AND event_id=?',(p,event_id)).fetchone()
            if old:
                if old[0]!=payload:raise CycleError('event_conflict')
                return {'duplicate':True}
            table='relationship' if creator_id else 'plan';where='plan_id=? AND creator_id=?' if creator_id else 'id=?';args=(p,creator_id) if creator_id else (p,)
            row=self.db.execute(f'SELECT * FROM {table} WHERE {where}',args).fetchone()
            if row is None:raise CycleError('control_missing')
            if row['revision']!=expected_revision:raise CycleError('revision_conflict')
            if creator_id:
                self.db.execute(f'UPDATE relationship SET mode=?,rejected=?,revision=revision+1 WHERE {where}',(mode,int(reject or row['rejected']),*args))
            else:self.db.execute('UPDATE plan SET state=?,revision=revision+1 WHERE id=?',(mode,p))
            self.db.execute('INSERT INTO control_event VALUES(?,?,?)',(p,event_id,payload))
        return {'duplicate':False,'revision':expected_revision+1}
    def inbound(self,p,creator,event_id):
        identifier(event_id)
        with self.tx():
            row=self.db.execute('SELECT * FROM relationship WHERE plan_id=? AND creator_id=?',(p,creator)).fetchone()
            if not row:raise CycleError('relationship_missing')
            payload=encoded(['inbound',creator]);prior=self.db.execute('SELECT payload FROM control_event WHERE plan_id=? AND event_id=?',(p,event_id)).fetchone()
            if prior:
                if prior[0]!=payload:raise CycleError('event_conflict')
                return
            self.db.execute('INSERT INTO control_event VALUES(?,?,?)',(p,event_id,payload))
            self.db.execute('UPDATE relationship SET inbox_until=?,unlocked=1,revision=revision+1 WHERE plan_id=? AND creator_id=?',(max(row['inbox_until'],self.clock()+60),p,creator))
            # Nonzero inbox_until also means pending service after debounce expires.
    def resolve_inbound(self,p,creator,event_id,expected_revision):
        identifier(event_id)
        with self.tx():
            row=self.db.execute('SELECT * FROM relationship WHERE plan_id=? AND creator_id=?',(p,creator)).fetchone()
            payload=encoded(['resolve_inbound',creator,expected_revision])
            prior=self.db.execute('SELECT payload FROM control_event WHERE plan_id=? AND event_id=?',(p,event_id)).fetchone()
            if prior:
                if prior[0]!=payload:raise CycleError('event_conflict')
                return
            if not row or row['revision']!=expected_revision:raise CycleError('revision_conflict')
            self.db.execute('INSERT INTO control_event VALUES(?,?,?)',(p,event_id,payload))
            self.db.execute('UPDATE relationship SET inbox_until=0,revision=revision+1 WHERE plan_id=? AND creator_id=?',(p,creator))

    def publish(self,p,source,observed,offers,complete=True):
        identifier(source);at=epoch(observed)
        if type(complete) is not bool or not isinstance(offers,list) or len(offers)>200000:raise CycleError('invalid_catalog')
        seen=set()
        for o in offers:
            identifier(o['pid']);identifier(o['offerKey']);identifier(o['evidenceRef'])
            if o['offerKey'] in seen:raise CycleError('duplicate_offer')
            seen.add(o['offerKey'])
        payload=encoded(offers);sid='catalog-'+digest([p,source,at,offers,complete])[:28]
        with self.tx():
            self._plan(p)
            self.db.execute('INSERT OR IGNORE INTO catalog VALUES(?,?,?,?,?,?)',(sid,p,source,at,'complete' if complete else 'partial',payload))
            head=self.db.execute('SELECT c.observed,c.id FROM catalog_head h JOIN catalog c ON c.id=h.snapshot_id WHERE h.plan_id=? AND h.source=?',(p,source)).fetchone()
            if complete and head and at==head['observed'] and sid!=head['id']:raise CycleError('catalog_time_conflict')
            if complete and (not head or at>head['observed']):self.db.execute('INSERT INTO catalog_head VALUES(?,?,?) ON CONFLICT(plan_id,source) DO UPDATE SET snapshot_id=excluded.snapshot_id',(p,source,sid))
        return sid
    def project_current_offers(self,p,source_ids=None):
        """Project changed identity edges in short transactions; historical raw evidence stays immutable."""
        self._plan(p)
        if source_ids is not None and not source_ids:return
        heads=tuple(self.db.execute('SELECT source,snapshot_id FROM catalog_head WHERE plan_id=? ORDER BY source',(p,)).fetchall())
        heads=tuple(tuple(row) for row in heads)
        by_pid={}
        for sid,offer in self._offers(p):by_pid.setdefault(offer['pid'],[]).append((sid,offer))
        query='SELECT payload FROM source_edge WHERE plan_id=?'
        args=(p,)
        if source_ids is not None:
            query+=' AND source_id IN (SELECT value FROM json_each(?))';args+=(encoded(list(source_ids)),)
        changes={}
        for row in self.db.execute(query,args).fetchall():
            edge=json.loads(row[0])
            # B uses its explicit video projection; do not replace legacy positive-sale opportunities with it.
            if edge.get('sourceKind')=='kalodata_video':continue
            if not edge.get('creatorId'):
                binding=self.db.execute('SELECT * FROM cycle_identity_resolution WHERE plan_id=? AND source_id=?',(p,edge['sourceId'])).fetchone()
                if not binding:continue
                edge={**edge,'creatorId':binding['creator_id'],'oec':binding['oec'],'identityEvidenceRef':binding['evidence_ref']}
            for sid,offer in by_pid.get(edge['pid'],[]):
                if offer['offerKey']==edge['offerKey'] and not edge.get('identityEvidenceRef'):continue
                payload=encoded({**edge,'offerKey':offer['offerKey'],'originalOfferKey':edge['offerKey'],'offerSnapshot':sid,'offerEvidenceRef':offer['evidenceRef']})
                key=(edge['creatorId'],edge['pid'],offer['offerKey'])
                prior=changes.get(key)
                if prior is None or (epoch(edge['observedAt']),edge['sourceId'])>=(epoch(prior[0]['observedAt']),prior[0]['sourceId']):
                    changes[key]=(edge,offer['offerKey'],payload)
        changes=list(changes.values())
        for offset in range(0,len(changes),100):
            with self.tx():
                current=tuple(tuple(row) for row in self.db.execute('SELECT source,snapshot_id FROM catalog_head WHERE plan_id=? ORDER BY source',(p,)))
                if current!=heads:raise CycleError('catalog_projection_scope_changed')
                for edge,offer_key,payload in changes[offset:offset+100]:
                    previous=self.db.execute('SELECT payload FROM opportunity WHERE plan_id=? AND creator_id=? AND pid=? AND offer_key=?',(p,edge['creatorId'],edge['pid'],offer_key)).fetchone()
                    if previous and previous[0]==payload:continue
                    if not previous or epoch(edge['observedAt'])>=epoch(json.loads(previous[0])['observedAt']):
                        self.db.execute('INSERT INTO opportunity VALUES(?,?,?,?,?,?) ON CONFLICT(plan_id,creator_id,pid,offer_key) DO UPDATE SET units=excluded.units,payload=excluded.payload',
                                        (p,edge['creatorId'],edge['pid'],offer_key,edge['units'],payload))

    def _offers(self,p):
        rows=list(self.db.execute('SELECT c.* FROM catalog_head h JOIN catalog c ON c.id=h.snapshot_id WHERE h.plan_id=?',(p,)))
        # Initial historical imports remain evidence, not a second current catalog.
        if any(r['source'].startswith('live-it-') for r in rows):
            rows=[r for r in rows if r['source']!='italy-historical-source']
        management={}
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='cycle_product_management'").fetchone():
            management={r['pid']:r['evidence_ref'] for r in self.db.execute("SELECT * FROM cycle_product_management WHERE plan_id=? AND kind='full_managed'",(p,))}
        held={}
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='cycle_card_creation'").fetchone():
            held={r['offer_json']:dict(r) for r in self.db.execute("SELECT id,offer_json,readback FROM cycle_card_creation WHERE plan_id=? AND state='invalidated'",(p,))}
        result=[]
        for row in rows:
            for original in json.loads(row['payload']):
                o=mark_full_managed(original,management[original['pid']]) if original['pid'] in management else original
                hold=held.get(encoded(original))
                if hold:
                    reasons=set((json.loads(hold['readback'] or '{}').get('assessment') or {}).get('reasons',[]))
                    stock_only=bool(reasons) and reasons<={'stock_not_over_100','missing_stock','invalid_stock'}
                    if not (full_managed(o) and stock_only):o=o|{'executionHold':{'reason':'current_offer_changed','evidenceRef':'card-preflight:'+hold['id']}}
                result.append((row['id'],o))
        return result
    def import_edges(self,p,edges):
        with self.tx():
            self._plan(p);self._edges(p,edges)
    def _edges(self,p,edges):
        if not isinstance(edges,list) or len(edges)>500:raise CycleError('page_limit')
        for e in edges:
            identifier(e['sourceId']);identifier(e['pid']);identifier(e['offerKey']);identifier(e['evidenceRef']);integer(e['units'],9007199254740991)
            epoch(e['observedAt'])
            if (date.fromisoformat(e['windowEnd'])-date.fromisoformat(e['windowStart'])).days!=13:raise CycleError('source_window_not_14_days')
            data=encoded(e);old=self.db.execute('SELECT payload FROM source_edge WHERE plan_id=? AND source_id=?',(p,e['sourceId'])).fetchone()
            if old:
                if old[0]!=data:raise CycleError('edge_conflict')
            else:self.db.execute('INSERT INTO source_edge VALUES(?,?,?)',(p,e['sourceId'],data))
            if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_edge_index'").fetchone() and e.get('sourceKind')=='kalodata_http':
                rank=e.get('sourceRank');handle=e.get('sourceHandle')
                if type(rank) is not int or rank<1 or not isinstance(handle,str) or not handle:raise CycleError('lead_edge_invalid')
                self.db.execute("""INSERT INTO source_edge_index(
                  plan_id,source_id,pid,source_handle,source_rank,units,window_start,window_end,source_kind,
                  revenue_value,revenue_currency) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                  ON CONFLICT(plan_id,source_id) DO UPDATE SET pid=excluded.pid,source_handle=excluded.source_handle,
                  source_rank=excluded.source_rank,units=excluded.units,window_start=excluded.window_start,
                  window_end=excluded.window_end,source_kind=excluded.source_kind,
                  revenue_value=excluded.revenue_value,revenue_currency=excluded.revenue_currency""",
                  (p,e['sourceId'],e['pid'],handle,rank,e['units'],e['windowStart'],e['windowEnd'],e['sourceKind'],
                   e.get('revenueValue'),e.get('currency')))
            if old:continue
            person=e.get('creatorId');oec=e.get('oec')
            if bool(person)!=bool(oec):raise CycleError('identity_incomplete')
            if person:
                identifier(person);identifier(oec)
                row=self.db.execute('SELECT * FROM relationship WHERE plan_id=? AND (creator_id=? OR oec=?)',(p,person,oec)).fetchone()
                if row and (row['creator_id']!=person or row['oec']!=oec):raise CycleError('identity_conflict')
                self.db.execute('INSERT OR IGNORE INTO relationship(plan_id,creator_id,oec) VALUES(?,?,?)',(p,person,oec))
                previous=self.db.execute('SELECT payload FROM opportunity WHERE plan_id=? AND creator_id=? AND pid=? AND offer_key=?',(p,person,e['pid'],e['offerKey'])).fetchone()
                if not previous or epoch(e['observedAt'])>epoch(json.loads(previous[0])['observedAt']):
                    self.db.execute('INSERT INTO opportunity VALUES(?,?,?,?,?,?) ON CONFLICT(plan_id,creator_id,pid,offer_key) DO UPDATE SET units=excluded.units,payload=excluded.payload',(p,person,e['pid'],e['offerKey'],e['units'],data))
    def _eligible_people(self,p,window_end=None):
        eligible={(o['pid'],o['offerKey']) for _,o in self._offers(p) if assess_offer(o,self.clock())['eligible']}
        consumed=set();cooldown=set()
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='cycle_delivery'").fetchone():
            consumed={(r[0],r[1],r[2]) for r in self.db.execute("SELECT creator_id,pid,source_id FROM cycle_delivery WHERE plan_id=? AND state IN ('ready','running','unknown','confirmed','partial_delivery')",(p,))}
        cooldown={creator for creator,stamp in last_contact_by_creator(self.db,p).items() if self.clock()-stamp<MARKETING_COOLDOWN_SECONDS}
        return {r['creator_id'] for r in self.db.execute('SELECT o.*,r.mode,r.rejected,r.inbox_until FROM opportunity o JOIN relationship r USING(plan_id,creator_id) WHERE o.plan_id=?',(p,)) if r['creator_id'] not in cooldown and (r['pid'],r['offer_key']) in eligible and r['mode']=='auto' and not r['rejected'] and not r['inbox_until'] and (r['creator_id'],r['pid'],json.loads(r['payload'])['sourceId']) not in consumed and (window_end is None or json.loads(r['payload'])['windowEnd']==window_end)}
    def _prepared(self,p,new,established,window_end=None):
        people=self._eligible_people(p,window_end)
        unlocked={r[0] for r in self.db.execute('SELECT creator_id FROM relationship WHERE plan_id=? AND unlocked=1',(p,))}
        return min(new,len(people-unlocked))+min(established,len(people&unlocked))

    def pending_identity_count(self,p,window_end):
        return self.db.execute("SELECT count(DISTINCT coalesce(json_extract(payload,'$.kalodataCreatorId'),json_extract(payload,'$.sourceHandle'),source_id)) FROM source_edge e WHERE plan_id=? AND json_extract(payload,'$.creatorId') IS NULL AND json_extract(payload,'$.windowEnd')=? AND NOT EXISTS (SELECT 1 FROM cycle_identity_resolution r WHERE r.plan_id=e.plan_id AND r.source_id=e.source_id) AND NOT EXISTS (SELECT 1 FROM cycle_identity_outcome o WHERE o.plan_id=e.plan_id AND o.source_id=e.source_id AND o.status IN ('unresolved','blocked'))",(p,window_end)).fetchone()[0]

    def replenish(self,p,*,new_remaining,established_capacity,expected_per_pid=10,max_pids=5,window_end=None):
        integer(new_remaining,500);integer(established_capacity,10000);integer(expected_per_pid,500);integer(max_pids,100)
        if not expected_per_pid or not max_pids:raise CycleError('invalid_capacity')
        end=date.fromisoformat(window_end) if window_end else datetime.fromtimestamp(self.clock(),timezone.utc).date()
        start=end-timedelta(days=13)
        with self.tx():
            plan=self._plan(p)
            if plan['state']!='active':return {'created':[],'reason':'plan_paused'}
            valid={(sid,o['offerKey']) for sid,o in self._offers(p) if assess_offer(o,self.clock())['eligible']}
            for job in self.db.execute("SELECT * FROM source_job WHERE plan_id=? AND state IN ('queued','running')",(p,)).fetchall():
                if (job['snapshot_id'],job['offer_key']) not in valid or job['window_end']!=str(end):
                    self.db.execute("UPDATE source_job SET state='obsolete' WHERE id=?",(job['id'],))
            self.db.execute('UPDATE plan SET capacity_new=?,capacity_established=? WHERE id=?',(new_remaining,established_capacity,p))
            target=new_remaining+established_capacity;prepared=self._prepared(p,new_remaining,established_capacity,str(end))
            pending=self.db.execute("SELECT coalesce(sum(estimated),0) FROM source_job WHERE plan_id=? AND state IN ('queued','running')",(p,)).fetchone()[0]
            pending_identity=self.pending_identity_count(p,str(end))
            deficit=max(0,target-prepared-pending-pending_identity);created=[]
            prior={r[0]:r[1] for r in self.db.execute("SELECT json_extract(payload,'$.pid'),count(*) FROM source_edge WHERE plan_id=? GROUP BY json_extract(payload,'$.pid')",(p,))}
            offers=sorted(self._offers(p),key=lambda pair:(-prior.get(pair[1]['pid'],0),not assess_offer(pair[1],self.clock())['ratingPreferred'],pair[1]['pid'],pair[1]['offerKey']))
            seen=set()
            for sid,o in offers:
                if deficit<=0 or len(created)>=max_pids:break
                if o['pid'] in seen or not assess_offer(o,self.clock())['eligible']:continue
                seen.add(o['pid']);jid='supply-'+digest([p,sid,o['offerKey'],str(start),str(end)])[:28]
                estimate=min(deficit,expected_per_pid)
                cur=self.db.execute('INSERT OR IGNORE INTO source_job(id,plan_id,snapshot_id,offer_key,pid,window_start,window_end,estimated) VALUES(?,?,?,?,?,?,?,?)',(jid,p,sid,o['offerKey'],o['pid'],str(start),str(end),estimate))
                if cur.rowcount:created.append(jid);deficit-=estimate
            return {'created':created,'target':target,'preparedUniqueCreators':prepared,'pendingEstimatedCreators':pending,'pendingIdentityCreators':pending_identity,'unfilled':deficit,'windowDays':14,'mode':'local_preparation_only','quotaWindowVerified':False}
    def claim(self,p,owner,lease_seconds=30):
        identifier(owner);integer(lease_seconds,300)
        if not lease_seconds:raise CycleError('invalid_lease')
        with self.tx():
            plan=self._plan(p)
            if plan['state']!='active':return None
            target=plan['capacity_new']+plan['capacity_established']
            running=self.db.execute("SELECT coalesce(sum(estimated),0) FROM source_job WHERE plan_id=? AND state='running' AND lease_until>?",(p,self.clock())).fetchone()[0]
            window=self.db.execute("SELECT window_end FROM source_job WHERE plan_id=? AND state IN ('queued','running') ORDER BY rowid DESC LIMIT 1",(p,)).fetchone()
            if target<=self._prepared(p,plan['capacity_new'],plan['capacity_established'],window[0] if window else None)+running+(self.pending_identity_count(p,window[0]) if window else 0):return None
            valid={(sid,o['offerKey']) for sid,o in self._offers(p) if assess_offer(o,self.clock())['eligible']}
            for r in self.db.execute("SELECT * FROM source_job WHERE plan_id=? AND (state='queued' OR (state='running' AND lease_until<=?)) ORDER BY rowid",(p,self.clock())).fetchall():
                if (r['snapshot_id'],r['offer_key']) not in valid:
                    self.db.execute("UPDATE source_job SET state='obsolete' WHERE id=?",(r['id'],));continue
                self.db.execute("UPDATE source_job SET state='running',owner=?,fence=fence+1,lease_until=? WHERE id=?",(owner,self.clock()+lease_seconds,r['id']))
                return dict(self.db.execute('SELECT * FROM source_job WHERE id=?',(r['id'],)).fetchone())
            return None
    def page(self,claim,edges,next_cursor,done):
        if type(done) is not bool or not isinstance(next_cursor,str) or len(next_cursor)>100:raise CycleError('invalid_cursor')
        with self.tx():
            r=self.db.execute('SELECT * FROM source_job WHERE id=?',(claim['id'],)).fetchone()
            if not r or r['owner']!=claim['owner'] or r['fence']!=claim['fence'] or r['state']!='running' or r['lease_until']<=self.clock():raise CycleError('lease_lost')
            if self._plan(r['plan_id'])['state']!='active':raise CycleError('plan_paused')
            if r['cursor']!=claim['cursor']:raise CycleError('cursor_changed')
            if (r['snapshot_id'],r['offer_key']) not in {(s,o['offerKey']) for s,o in self._offers(r['plan_id']) if assess_offer(o,self.clock())['eligible']}:raise CycleError('catalog_changed')
            if not done and (not next_cursor or next_cursor==r['cursor']):raise CycleError('cursor_not_advancing')
            if next_cursor and self.db.execute('SELECT 1 FROM source_page WHERE job_id=? AND cursor=?',(r['id'],next_cursor)).fetchone():raise CycleError('cursor_cycle')
            if any(e['pid']!=r['pid'] or e['offerKey']!=r['offer_key'] or e['windowStart']!=r['window_start'] or e['windowEnd']!=r['window_end'] for e in edges):raise CycleError('page_scope_mismatch')
            self._edges(r['plan_id'],edges)
            self.db.execute('INSERT INTO source_page VALUES(?,?,?)',(r['id'],r['cursor'],encoded({'edges':edges,'next':next_cursor,'done':done})))
            self.db.execute('UPDATE source_job SET cursor=?,state=?,owner=NULL,lease_until=0 WHERE id=?',(next_cursor,'completed' if done else 'queued',r['id']))
    def status(self,p):
        plan=dict(self._plan(p));offers=[dict(o,assessment=assess_offer(o,self.clock())) for _,o in self._offers(p)]
        known={r[0] for r in self.db.execute('SELECT DISTINCT pid FROM opportunity WHERE plan_id=?',(p,))}
        offers.sort(key=lambda o:(o['pid'] not in known,not o['assessment']['eligible'],o['pid'],o['offerKey']))
        counts=lambda table:self.db.execute(f'SELECT count(*) FROM {table} WHERE plan_id=?',(p,)).fetchone()[0]
        jobs={r[0]:r[1] for r in self.db.execute('SELECT state,count(*) FROM source_job WHERE plan_id=? GROUP BY state',(p,))}
        issues=[]
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cycle_source_issue'").fetchone():
            issues=[dict(r) for r in self.db.execute("SELECT i.code,j.window_start,j.window_end FROM cycle_source_issue i JOIN source_job j ON i.job_id=j.id WHERE j.plan_id=? AND j.state='blocked' ORDER BY i.created DESC LIMIT 5",(p,))]
        identity_total=self.db.execute("SELECT count(*) FROM source_edge WHERE plan_id=? AND json_extract(payload,'$.sourceKind')='kalodata_http'",(p,)).fetchone()[0]
        identity_resolved=self.db.execute('SELECT count(*) FROM cycle_identity_resolution WHERE plan_id=?',(p,)).fetchone()[0]
        from lib.cycle_materials import material_status
        materials=material_status(self,p)
        from lib.cycle_review import latest_review
        review=latest_review(self,p)
        from lib.cycle_inbox import inbox_status
        inbox=inbox_status(self,p)
        from lib.cycle_delivery import delivery_status
        from lib.cycle_scheduler import schedule_status
        delivery=delivery_status(self,p);schedule=schedule_status(self,p)
        from lib.cycle_speed import speed_status
        speed=speed_status(self,p)
        model_calls=unknown_model_requests=0
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cycle_name_job'").fetchone():
            model_calls=self.db.execute("SELECT count(*) FROM cycle_name_job WHERE state IN ('ready','response_saved')").fetchone()[0]
            unknown_model_requests=self.db.execute("SELECT count(*) FROM cycle_name_job WHERE state IN ('unknown','request_started')").fetchone()[0]
        return {'schema':'bdhub.second-cycle.v1','plan':plan,'mode':'local_preparation_only','offers':offers[:40],'offerCount':len(offers),'eligibleOfferCount':sum(o['assessment']['eligible'] for o in offers),'relationships':counts('relationship'),'sourceEdges':counts('source_edge'),'opportunities':self.db.execute('SELECT count(*) FROM (SELECT DISTINCT creator_id,pid FROM opportunity WHERE plan_id=?)',(p,)).fetchone()[0],'eligibleUniqueCreators':len(self._eligible_people(p)),'jobs':jobs,'sourceIssues':issues,'materials':materials,'reviewBatch':review,'inbox':inbox,'delivery':delivery,'schedule':schedule,'speed':speed,'identitySourceEdges':identity_total,'identityResolvedEdges':identity_resolved,'identityUnmatchedEdges':self.db.execute("SELECT count(*) FROM cycle_identity_outcome WHERE plan_id=? AND status='unresolved'",(p,)).fetchone()[0],'executionAllowed':False,'legacyTrialControlIntegrated':False,'modelCalls':model_calls,'modelRequestsUnresolved':unknown_model_requests,'realSends':delivery['confirmedParts'] if delivery else 0}
