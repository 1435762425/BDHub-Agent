"""Bounded Kalodata page reader and durable local worker; no TikTok writes."""
from datetime import date,timedelta
from decimal import Decimal,InvalidOperation
import hashlib,json,re,time
from lib.second_cycle import CycleError,digest,encoded
PATH='/product/detail/creator/queryList'
HANDLE=re.compile(r'[a-z0-9_.]{1,24}\Z')

def quota_exhausted(body):
 if not isinstance(body,dict) or body.get('success') is True:return False
 message=body.get('message')
 if isinstance(message,str):
  try:message=json.loads(message)
  except ValueError:return False
 return isinstance(message,dict) and message.get('cause')=='DETAIL.ACCESS_TIMES'

def sales(v):
 if v is None or isinstance(v,bool):return None
 s=str(v).strip().replace(',','');m=re.fullmatch(r'(\d+(?:\.\d+)?)\s*([kKmM万]?)',s)
 if not m:return None
 d=Decimal(m[1])*{'':1,'k':1000,'K':1000,'m':1000000,'M':1000000,'万':10000}[m[2]]
 return int(d) if d==d.to_integral_value() and 0<=d<=9007199254740991 else None

def parse_page(body,claim,at,*,max_pages=2):
 if quota_exhausted(body):raise CycleError('kalodata_daily_quota_exhausted')
 if not isinstance(body,dict) or body.get('success') is not True:raise CycleError('kalodata_business_rejected')
 rows=body.get('data')
 if isinstance(rows,dict):rows=next((rows[k] for k in ('items','list','records','rows') if isinstance(rows.get(k),list)),None)
 if not isinstance(rows,list) or len(rows)>50 or any(not isinstance(r,dict) for r in rows):raise CycleError('kalodata_rows_invalid')
 page=int(claim['cursor'] or '1')
 if not 1<=page<=max_pages:raise CycleError('kalodata_page_bound')
 edges=[];seen=set();skipped={}
 for rank,row in enumerate(rows,start=(page-1)*50+1):
  handle=str(row.get('handle') or '').strip().lstrip('@').lower();units=sales(row.get('sale'));kid=str(row.get('id') or '')
  reason='invalid_handle' if not HANDLE.fullmatch(handle) else 'sales_missing' if units is None else 'no_sales' if units<=0 else None
  if reason:skipped[reason]=skipped.get(reason,0)+1;continue
  if not kid or len(kid)>128 or not re.fullmatch(r'[A-Za-z0-9_.:-]+',kid):kid='handle:'+handle
  if kid in seen:continue
  seen.add(kid)
  sid=digest(['kalodata',claim['id'],claim['pid'],claim['window_start'],claim['window_end'],kid])
  edges.append({'sourceId':sid,'pid':claim['pid'],'offerKey':claim['offer_key'],'creatorId':None,'oec':None,
                'units':units,'evidenceRef':'kalodata:'+digest([claim['pid'],claim['window_start'],claim['window_end'],page,row]),
                'observedAt':at,'windowStart':claim['window_start'],'windowEnd':claim['window_end'],
                'sourceHandle':handle,'kalodataCreatorId':kid,'sourceRank':rank,'currency':'EUR',
                'revenueRaw':str(row['revenue'])[:80] if isinstance(row.get('revenue'),(str,int,float)) else None,
                'liveRevenueRaw':str(row['live_revenue'])[:80] if isinstance(row.get('live_revenue'),(str,int,float)) else None,
                'videoRevenueRaw':str(row['video_revenue'])[:80] if isinstance(row.get('video_revenue'),(str,int,float)) else None,'historicalOwnership':'unverified','sourceKind':'kalodata_http'})
 done=len(rows)<50 or page>=max_pages
 source_rows=[{k:row[k] for k in ('id','handle','nickname','sale','revenue','video_revenue','live_revenue','followers') if k in row} for row in rows]
 return {'sourceRows':source_rows,'edges':edges,'nextCursor':'' if done else str(page+1),'done':done,'coverage':'page_cap' if len(rows)==50 and page>=max_pages else 'short_page' if len(rows)<50 else 'more_possible','rowsReceived':len(rows),'rowsFingerprint':digest(rows),'skipped':skipped,'page':page,'maxPages':max_pages}

class KalodataWorker:
 def __init__(self,store,provider,owner='kalodata-local-worker',max_pages=2):
  if type(max_pages) is not int or not 1<=max_pages<=20:raise CycleError('invalid_page_cap')
  self.store=store;self.provider=provider;self.owner=owner;self.max_pages=max_pages
  store.db.executescript('''CREATE TABLE IF NOT EXISTS cycle_source_receipt(job_id TEXT NOT NULL,cursor TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(job_id,cursor));
  CREATE TABLE IF NOT EXISTS cycle_source_issue(job_id TEXT PRIMARY KEY,code TEXT NOT NULL,created REAL NOT NULL);''')
 def once(self,plan):
  if self.store._plan(plan)['market']!='it':raise CycleError('kalodata_market_not_enabled')
  claim=self.store.claim(plan,self.owner,lease_seconds=120)
  if not claim:return {'status':'idle','networkRequests':0}
  calls=0
  try:
   old=self.store.db.execute('SELECT payload FROM cycle_source_receipt WHERE job_id=? AND cursor=?',(claim['id'],claim['cursor'])).fetchone()
   if old:
    receipt=json.loads(old[0])
    if receipt['maxPages']!=self.max_pages:raise CycleError('kalodata_scope_changed')
   else:
    payload={'startDate':claim['window_start'],'endDate':claim['window_end'],'authority':True,'pageSize':50,'pageNo':int(claim['cursor'] or '1'),'sort':[{'field':'revenue','type':'DESC'}],'id':claim['pid']}
    calls=1;body=self.provider.request(PATH,payload)
    receipt=parse_page(body,claim,self.store.clock(),max_pages=self.max_pages)
    with self.store.tx():
     r=self.store.db.execute('SELECT * FROM source_job WHERE id=?',(claim['id'],)).fetchone()
     if r['owner']!=claim['owner'] or r['fence']!=claim['fence'] or r['lease_until']<=self.store.clock():raise CycleError('lease_lost')
     self.store.db.execute('INSERT INTO cycle_source_receipt VALUES(?,?,?)',(claim['id'],claim['cursor'],encoded(receipt)))
   # Deduplicate a repeated creator across pages while retaining page-level coverage.
   edges=[]
   for e in receipt['edges']:
    if not self.store.db.execute('SELECT 1 FROM source_edge WHERE plan_id=? AND source_id=?',(plan,e['sourceId'])).fetchone():edges.append(e)
   if receipt['rowsReceived'] and any(json.loads(r[0]).get('rowsFingerprint')==receipt['rowsFingerprint'] for r in self.store.db.execute('SELECT payload FROM cycle_source_receipt WHERE job_id=? AND cursor<>?',(claim['id'],claim['cursor']))):raise CycleError('kalodata_repeated_page')
   self.store.page(claim,edges,receipt['nextCursor'],receipt['done'])
   return {'status':'completed' if receipt['done'] else 'checkpointed','jobId':claim['id'],'page':receipt['page'],'addedEdges':len(edges),'coverage':receipt['coverage'],'networkRequests':calls}
  except Exception as error:
   code=str(error) if isinstance(error,CycleError) else 'kalodata_read_failed'
   if code not in {'kalodata_daily_quota_exhausted','kalodata_scope_changed','kalodata_auth_required','kalodata_repeated_page','kalodata_business_rejected','kalodata_rows_invalid','kalodata_page_bound','lease_lost','plan_paused','catalog_changed','cursor_changed','page_scope_mismatch'}:code='kalodata_read_failed'
   with self.store.tx():
    r=self.store.db.execute('SELECT * FROM source_job WHERE id=?',(claim['id'],)).fetchone()
    if r['owner']==claim['owner'] and r['fence']==claim['fence'] and r['state']=='running':
     self.store.db.execute("UPDATE source_job SET state='blocked',owner=NULL,lease_until=0 WHERE id=?",(claim['id'],))
     self.store.db.execute('INSERT OR REPLACE INTO cycle_source_issue VALUES(?,?,?)',(claim['id'],code,self.store.clock()))
   return {'status':'blocked','jobId':claim['id'],'error':code,'networkRequests':calls}
