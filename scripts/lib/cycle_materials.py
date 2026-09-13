"""Product-level names and fixed v4 templates. No per-creator model calls."""
import json,re,time
from decimal import Decimal
from lib.second_cycle import CycleError,digest,encoded,assess_offer
SCHEMA='''
CREATE TABLE IF NOT EXISTS cycle_name_job(id TEXT PRIMARY KEY,state TEXT NOT NULL,inputs TEXT NOT NULL,response TEXT,error TEXT);
CREATE TABLE IF NOT EXISTS cycle_name_reservation(id TEXT PRIMARY KEY,job_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cycle_product_name(id TEXT PRIMARY KEY,pid TEXT NOT NULL,locale TEXT NOT NULL,source_title TEXT NOT NULL,payload TEXT NOT NULL,job_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cycle_card_check(plan_id TEXT NOT NULL,offer_key TEXT NOT NULL,offer_fingerprint TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(plan_id,offer_key,offer_fingerprint));
'''
TEMPLATES={
 'standard':"Ciao{recipient}! Abbiamo una commissione migliorata al {rate}% per te su {mention} 👏 Ti va di dedicarci un nuovo video o LIVE?",
 'brief':"Ciao{recipient}! Per {mention}, commissione del {rate}% per te 👏 Ci fai un nuovo video o LIVE? 😊",
 'video_live':"Ciao{recipient}! Stai preparando un nuovo video o LIVE? Per {mention} abbiamo una commissione migliorata al {rate}% per te 😊",
}
def name_key(offer):return digest(['product-short-name-v1','it-IT',offer['pid'],offer['title']])
def checked_names(body,inputs):
 if not isinstance(body,dict) or set(body)!={'items'} or not isinstance(body['items'],list) or len(body['items'])!=len(inputs):raise CycleError('names_invalid')
 result={}
 for row in body['items']:
  if not isinstance(row,dict) or set(row)!={'ref','shortNameIt','mentionIt','shortNameZh'} or row['ref'] not in {str(i) for i in range(len(inputs))} or row['ref'] in result:raise CycleError('names_invalid')
  for key in ('shortNameIt','mentionIt','shortNameZh'):
   v=row[key]
   if not isinstance(v,str) or not v.strip() or len(v)>60 or len(v.split())>8 or re.search(r'[\n\r<>%{}]|https?://|BJN|gratis|commission|sconto|best.?sell|爆款|热销|保证',v,re.I):raise CycleError('names_invalid')
  result[row['ref']]=row
 return result

def render(name,offer,kind='standard',handle=None):
 if kind not in TEMPLATES:raise CycleError('template_missing')
 if not assess_offer(offer,time.time())['eligible']:raise CycleError('offer_not_eligible')
 if handle is not None and not re.fullmatch(r'[a-zA-Z0-9_.]{1,100}',handle):raise CycleError('invalid_handle')
 rate=format(Decimal(offer['creatorPercent']).normalize(),'f')
 return {'version':4,'template':kind,'textIt':TEMPLATES[kind].format(recipient=' @'+handle if handle else '',rate=rate,mention=name['mentionIt']),
 'translationZh':f"你好！这款{name['shortNameZh']}可以为你提供更高的 {rate}% 佣金，下一条视频或直播可以再推一轮。",
 'deliveryOrder':'card_then_text','pid':offer['pid'],'executionAllowed':False,'requiresVerifiedCard':True,'commissionState':'proposed_not_applied'}

def select_offers(store,plan,limit=5,require_demand=False):
 if store._plan(plan)['market']!='it':return []
 pids={r[0] for r in store.db.execute('SELECT DISTINCT pid FROM opportunity WHERE plan_id=?',(plan,))}
 chosen={}
 for _,offer in store._offers(plan):
  if offer['pid'] not in pids or not assess_offer(offer,store.clock())['eligible']:continue
  old=chosen.get(offer['pid'])
  if old is None or (Decimal(offer['creatorPercent']),offer['endAt'],offer['offerKey'])>(Decimal(old['creatorPercent']),old['endAt'],old['offerKey']):chosen[offer['pid']]=offer
 people=store._eligible_people(plan);demand={}
 for row in store.db.execute('SELECT creator_id,pid,units FROM opportunity WHERE plan_id=?',(plan,)):
  if row['creator_id'] in people:demand.setdefault(row['pid'],set()).add(row['creator_id'])
 return sorted((o for o in chosen.values() if not require_demand or demand.get(o['pid'])),key=lambda o:(-len(demand.get(o['pid'],set())),o['pid']))[:limit]

class Materials:
 def __init__(self,store):self.store=store;store.db.executescript(SCHEMA)
 def candidates(self,plan,limit=5):
  result=[]
  for o in select_offers(self.store,plan,1000,require_demand=True):
   check=self.store.db.execute('SELECT payload FROM cycle_card_check WHERE plan_id=? AND offer_key=? AND offer_fingerprint=?',(plan,o['offerKey'],digest(o))).fetchone()
   if self.name(o) and check and json.loads(check[0]).get('state')=='verified_read_only':continue
   result.append(o)
   if len(result)>=limit:break
  return result
 def prepare_names(self,offers,call):
  missing=[o for o in offers if not self.store.db.execute('SELECT 1 FROM cycle_product_name WHERE id=?',(name_key(o),)).fetchone()]
  if not missing:return {'modelCalls':0,'cached':len(offers)}
  if len(missing)>5:raise CycleError('names_batch_limit')
  jid='names-'+digest([name_key(o) for o in missing]);row=self.store.db.execute('SELECT * FROM cycle_name_job WHERE id=?',(jid,)).fetchone()
  if row and row['state'] not in ('response_saved','ready'):raise CycleError('names_previous_request_unresolved')
  if not row:
   with self.store.tx():
    for o in missing:
     prior=self.store.db.execute('SELECT job_id FROM cycle_name_reservation WHERE id=?',(name_key(o),)).fetchone()
     if prior and prior[0]!=jid:raise CycleError('names_reserved_by_other_batch')
     self.store.db.execute('INSERT OR IGNORE INTO cycle_name_reservation VALUES(?,?)',(name_key(o),jid))
    self.store.db.execute('INSERT INTO cycle_name_job VALUES(?,?,?,?,?)',(jid,'request_started',encoded(missing),None,None))
   prompt={'items':[{'ref':str(i),'title':o['title']} for i,o in enumerate(missing)]}
   try:
    response=call([{'role':'system','content':'将意大利商品标题缩成真实商品类型，不添加营销、疗效、品质或销量主张。去掉品牌、促销、颜色、包装等非必要词，但不可改品类。输出JSON items数组，逐项ref原样，shortNameIt为简短意大利商品名，mentionIt为可接在su/per之后的意大利短语（例如questo cuscino cervicale、questi leggings），shortNameZh为中文短名。每个名称最好2至5词。'}, {'role':'user','content':encoded(prompt)}],max_output_tokens=1000)
    with self.store.tx():self.store.db.execute("UPDATE cycle_name_job SET state='response_saved',response=? WHERE id=?",(encoded(response),jid))
   except Exception as e:
    with self.store.tx():self.store.db.execute("UPDATE cycle_name_job SET state='unknown',error=?,response=? WHERE id=?",(getattr(e,'code','provider_failed'),encoded(getattr(e,'receipt',None)),jid))
    raise CycleError('names_request_unresolved') from None
  else:response=json.loads(row['response'])
  parsed=checked_names(json.loads(response['content']),missing)
  with self.store.tx():
   for i,o in enumerate(missing):self.store.db.execute('INSERT OR IGNORE INTO cycle_product_name VALUES(?,?,?,?,?,?)',(name_key(o),o['pid'],'it-IT',o['title'],encoded(parsed[str(i)]),jid))
   self.store.db.execute("UPDATE cycle_name_job SET state='ready' WHERE id=?",(jid,))
  return {'modelCalls':0 if row else 1,'prepared':len(missing),'jobId':jid,'usage':response.get('usage'),'cost':response.get('cost')}
 def name(self,offer):
  r=self.store.db.execute('SELECT payload FROM cycle_product_name WHERE id=?',(name_key(offer),)).fetchone()
  return json.loads(r[0]) if r else None


def material_status(store,plan):
 if not store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cycle_product_name'").fetchone():return []
 # The same pure selection as the writer, without constructing/mutating stores.
 result=[]
 for o in select_offers(store,plan):
  saved=store.db.execute('SELECT payload FROM cycle_product_name WHERE id=?',(name_key(o),)).fetchone()
  name=json.loads(saved[0]) if saved else None
  r=store.db.execute('SELECT payload FROM cycle_card_check WHERE plan_id=? AND offer_key=? AND offer_fingerprint=?',(plan,o['offerKey'],digest(o))).fetchone()
  result.append({'pid':o['pid'],'name':name,'creatorPercent':o['creatorPercent'],'card':json.loads(r[0]) if r else {'state':'not_checked'},'templates':[render(name,o,k) for k in TEMPLATES] if name else []})
 return result
