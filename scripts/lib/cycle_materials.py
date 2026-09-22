"""Product-level names and fixed v4 templates. No per-creator model calls."""
import json,re,time
from decimal import Decimal
from pathlib import Path
from lib.second_cycle import CycleError,digest,encoded,assess_offer,epoch
ROOT=Path(__file__).resolve().parents[2]
SCHEMA='''
CREATE TABLE IF NOT EXISTS cycle_name_job(id TEXT PRIMARY KEY,state TEXT NOT NULL,inputs TEXT NOT NULL,response TEXT,error TEXT);
CREATE TABLE IF NOT EXISTS cycle_name_reservation(id TEXT PRIMARY KEY,job_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cycle_product_name(id TEXT PRIMARY KEY,pid TEXT NOT NULL,locale TEXT NOT NULL,source_title TEXT NOT NULL,payload TEXT NOT NULL,job_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cycle_card_check(plan_id TEXT NOT NULL,offer_key TEXT NOT NULL,offer_fingerprint TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(plan_id,offer_key,offer_fingerprint));
'''
def _italian_templates():
 from lib.market_content import send_template_map
 return {key:{'label':value['label'],'description':value['description'],'textIt':value['text'],
              'translationZh':value['translationZh']}
         for key,value in send_template_map(ROOT,'it').items()}


TEMPLATES=_italian_templates()
LEGACY_TEMPLATES={
 'video_live':{
  'label':'旧版视频或直播','description':'只用于读取历史材料，不再作为新批次选项。',
  'textIt':"Ciao{recipient}! Stai preparando un nuovo video o LIVE? Per {mention} abbiamo una commissione migliorata al {rate}% per te 😊",
  'translationZh':"你好！如果你正在准备新的短视频或直播，这款{shortZh}可以为你提供 {rate}% 佣金。"},
}
# The exact instruction the model receives. Kept here as one string so the workbench can show the
# operator what is actually being asked, instead of a paraphrase that could drift from the code.
NAMES_SYSTEM_PROMPT='将意大利商品标题缩成真实商品类型，不添加营销、疗效、品质或销量主张。去掉品牌、促销、颜色、包装等非必要词，但不可改品类。输出JSON items数组，逐项ref原样，shortNameIt为简短意大利商品名，mentionIt为可接在su/per之后的意大利短语（例如questo cuscino cervicale、questi leggings），shortNameZh为中文短名。每个名称最好2至5词。'

def localized_templates(market='it'):
 from lib.market_content import send_template_map
 rows=send_template_map(ROOT,market)
 return {key:{'label':value['label'],'description':value['description'],'textIt':value['text'],
              'translationZh':value['translationZh']} for key,value in rows.items()}

def names_prompt(market='it'):
 if market=='it':return NAMES_SYSTEM_PROMPT
 from lib.market_content import name_prompt
 return name_prompt(ROOT,market)

def market_locale(market='it'):
 from lib.market_content import market_content
 return market_content(ROOT,market)['locale']

def name_key(offer,market='it'):
 if market=='it':return digest(['product-short-name-v1','it-IT',offer['pid'],offer['title']])
 return digest(['product-short-name-v2',market,market_locale(market),offer['pid'],offer['title']])

def checked_names(body,inputs,market='it'):
 if not isinstance(body,dict) or set(body)!={'items'} or not isinstance(body['items'],list) or len(body['items'])!=len(inputs):raise CycleError('names_invalid')
 result={}
 required={'ref','shortNameIt','mentionIt','shortNameZh'} if market=='it' else {'ref','shortName','mention','shortNameZh'}
 for row in body['items']:
  # The four keys must all be present, but extra ones are ignored rather than fatal: the model
  # sometimes echoes the input title back, which is harmless and was rejecting whole valid batches.
  if not isinstance(row,dict) or not required<=set(row) or row['ref'] not in {str(i) for i in range(len(inputs))} or row['ref'] in result:raise CycleError('names_invalid')
  row={k:row[k] for k in required}
  for key in required-{'ref'}:
   v=row[key]
   word_limit=14 if market!='it' and key=='mention' else 8
   length_limit=100 if market!='it' and key=='mention' else 60
   if not isinstance(v,str) or not v.strip() or len(v)>length_limit or len(v.split())>word_limit or re.search(r'[\n\r<>%{}]|https?://|BJN|gratis|gratuito|commission|comissão|komisen|sconto|desconto|discount|diskaun|best.?sell|爆款|热销|保证',v,re.I):raise CycleError('names_invalid')
  if market!='it':
   from lib.market_content import market_content
   content=market_content(ROOT,market);row={**row,'language':content['language'],'locale':content['locale']}
  result[row['ref']]=row
 return result

def render(name,offer,kind='standard',handle=None,market='it'):
 from lib.market_content import market_content
 template=(localized_templates(market)|LEGACY_TEMPLATES).get(kind)
 if template is None:raise CycleError('template_missing')
 if not assess_offer(offer,time.time())['eligible']:raise CycleError('offer_not_eligible')
 if handle is not None and not re.fullmatch(r'[a-zA-Z0-9_.]{1,100}',handle):raise CycleError('invalid_handle')
 rate=format(Decimal(offer['creatorPercent']).normalize(),'f')
 mention=name.get('mentionIt') if market=='it' else name.get('mention')
 if not isinstance(mention,str) or not mention:raise CycleError('localized_name_missing')
 text=template['textIt'].format(recipient=' @'+handle if handle else '',rate=rate,mention=mention)
 return {'version':4,'template':kind,'market':market,'language':market_content(ROOT,market)['language'],'text':text,'textIt':text,
 'translationZh':template['translationZh'].format(rate=rate,shortZh=name['shortNameZh']),
 'deliveryOrder':'card_then_text','pid':offer['pid'],'executionAllowed':False,'requiresVerifiedCard':True,'commissionState':'proposed_not_applied'}

def template_catalog(market='it'):
 return [{'id':key,'label':value['label'],'description':value['description']} for key,value in localized_templates(market).items()]

def select_offers(store,plan,limit=5,require_demand=False,scoped_pids=None):
 """当前合格货盘里的 offer，按 pid 取最好的一条。

 `scoped_pids` 给了就**只在这些 pid 里挑**，并且**不再要求这个 pid 在 `opportunity` 表里有行**。
 那道过滤是给**准备/估算**用的（"这个商品有没有人点"），发送时不该有它：发送位置来自发送池，
 池位本身就是需求。少了这个区分，一大批**商品明明在货盘里、也明明有卡**的位置会被报成
 "商品不在当前合格货盘"（实测 182 条）。
 """
 market=store._plan(plan)['market']
 if scoped_pids is None:
  pids={r[0] for r in store.db.execute('SELECT DISTINCT pid FROM opportunity WHERE plan_id=?',(plan,))}
 else:
  pids={str(p) for p in scoped_pids}
 chosen={}
 def order(offer):
  # Offer choice must match the Campaign pool contract: creator share highest, expiry later,
  # selected route wins an exact cross-channel tie, then campaign id smallest.
  try:end=epoch(offer.get('endAt'))
  except (ValueError,TypeError):end=-1
  return (-Decimal(str(offer['creatorPercent'])),-end,
          0 if offer.get('catalogSource')=='selected' else 1,str(offer.get('campaignId') or ''),
          str(offer.get('offerKey') or ''))
 for _,offer in store._offers(plan):
  if offer['pid'] not in pids or not assess_offer(offer,store.clock())['eligible']:continue
  old=chosen.get(offer['pid'])
  if old is None or order(offer)<order(old):chosen[offer['pid']]=offer
 people=store._eligible_people(plan);demand={}
 for row in store.db.execute('SELECT creator_id,pid,units FROM opportunity WHERE plan_id=?',(plan,)):
  if row['creator_id'] in people:demand.setdefault(row['pid'],set()).add(row['creator_id'])
 result=sorted((o for o in chosen.values() if not require_demand or demand.get(o['pid'])),
               key=lambda o:(-len(demand.get(o['pid'],set())),o['pid']))
 return result if limit is None else result[:limit]

class Materials:
 def __init__(self,store,market='it'):self.store=store;self.market=market;store.db.executescript(SCHEMA)
 def candidates(self,plan,limit=5):
  result=[]
  for o in select_offers(self.store,plan,1000,require_demand=True):
   check=self.store.db.execute('SELECT payload FROM cycle_card_check WHERE plan_id=? AND offer_key=? AND offer_fingerprint=?',(plan,o['offerKey'],digest(o))).fetchone()
   if self.name(o) and check and json.loads(check[0]).get('state')=='verified_read_only':continue
   result.append(o)
   if len(result)>=limit:break
  return result
 def usable_cached(self,offer):
  """A stored name only counts if a reader would accept it.

  Row existence is not enough: a row whose shortNameIt is empty or over the length limit satisfies
  ``WHERE id=?`` and would block regeneration forever, while every reader still falls back to a
  truncated title. Those rows must be regenerated, not treated as done.
  """
  row=self.store.db.execute('SELECT payload FROM cycle_product_name WHERE id=?',(name_key(offer,self.market),)).fetchone()
  if not row:return False
  try:
   payload=json.loads(row[0]);value=payload.get('shortNameIt') if self.market=='it' else payload.get('shortName')
  except (TypeError,ValueError):return False
  return isinstance(value,str) and 1<=len(value)<=30

 def prepare_names(self,offers,call):
  missing=[o for o in offers if not self.usable_cached(o)]
  if not missing:return {'modelCalls':0,'cached':len(offers)}
  if len(missing)>5:raise CycleError('names_batch_limit')
  jid='names-'+digest([name_key(o,self.market) for o in missing]);row=self.store.db.execute('SELECT * FROM cycle_name_job WHERE id=?',(jid,)).fetchone()
  if row and row['state'] not in ('response_saved','ready'):raise CycleError('names_previous_request_unresolved')
  if not row:
   with self.store.tx():
    for o in missing:
     prior=self.store.db.execute('SELECT job_id FROM cycle_name_reservation WHERE id=?',(name_key(o,self.market),)).fetchone()
     if prior and prior[0]!=jid:raise CycleError('names_reserved_by_other_batch')
     self.store.db.execute('INSERT OR IGNORE INTO cycle_name_reservation VALUES(?,?)',(name_key(o,self.market),jid))
    self.store.db.execute('INSERT INTO cycle_name_job VALUES(?,?,?,?,?)',(jid,'request_started',encoded(missing),None,None))
   prompt={'items':[{'ref':str(i),'title':o['title']} for i,o in enumerate(missing)]}
   try:
    response=call([{'role':'system','content':names_prompt(self.market)}, {'role':'user','content':encoded(prompt)}],max_output_tokens=1000)
    with self.store.tx():self.store.db.execute("UPDATE cycle_name_job SET state='response_saved',response=? WHERE id=?",(encoded(response),jid))
   except Exception as e:
    with self.store.tx():self.store.db.execute("UPDATE cycle_name_job SET state='unknown',error=?,response=? WHERE id=?",(getattr(e,'code','provider_failed'),encoded(getattr(e,'receipt',None)),jid))
    raise CycleError('names_request_unresolved') from None
  else:response=json.loads(row['response'])
  parsed=checked_names(json.loads(response['content']),missing,self.market)
  with self.store.tx():
   for i,o in enumerate(missing):
    # Upsert: ``missing`` only holds products with no row or an unusable one, so replacing is the
    # intended outcome and a good row is never overwritten.
    self.store.db.execute('INSERT INTO cycle_product_name VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET pid=excluded.pid,locale=excluded.locale,source_title=excluded.source_title,payload=excluded.payload,job_id=excluded.job_id',(name_key(o,self.market),o['pid'],market_locale(self.market),o['title'],encoded(parsed[str(i)]),jid))
   self.store.db.execute("UPDATE cycle_name_job SET state='ready' WHERE id=?",(jid,))
  return {'modelCalls':0 if row else 1,'prepared':len(missing),'jobId':jid,'usage':response.get('usage'),'cost':response.get('cost')}
 def name(self,offer):
  r=self.store.db.execute('SELECT payload FROM cycle_product_name WHERE id=?',(name_key(offer,self.market),)).fetchone()
  return json.loads(r[0]) if r else None


def material_status(store,plan):
 if not store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cycle_product_name'").fetchone():return []
 # The same pure selection as the writer, without constructing/mutating stores.
 market=store._plan(plan)['market'];result=[]
 for o in select_offers(store,plan):
  saved=store.db.execute('SELECT payload FROM cycle_product_name WHERE id=?',(name_key(o,market),)).fetchone()
  name=json.loads(saved[0]) if saved else None
  r=store.db.execute('SELECT payload FROM cycle_card_check WHERE plan_id=? AND offer_key=? AND offer_fingerprint=?',(plan,o['offerKey'],digest(o))).fetchone()
  result.append({'pid':o['pid'],'name':name,'creatorPercent':o['creatorPercent'],'card':json.loads(r[0]) if r else {'state':'not_checked'},'templates':[render(name,o,k,market=market) for k in localized_templates(market)] if name else []})
 return result
