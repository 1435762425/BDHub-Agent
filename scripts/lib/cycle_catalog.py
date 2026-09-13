"""Resumable catalog read normalization; no remote mutations or credentials."""
from datetime import datetime,timezone
from decimal import Decimal, InvalidOperation
from lib.second_cycle import CycleError,digest
from lib.product_stock_policy import mark_full_managed,require_stock,unavailable_allowed

CAMPAIGNS='/api/v1/affiliate/partner/campaign/list'
PRODUCTS='/api/v1/affiliate/partner/campaign/product/list'
SELECTED='/api/v1/affiliate/partner/product/pick_up/list'

def number(v):
 try:
  if v is None or isinstance(v,bool):return None
  d=Decimal(str(v));return d if d.is_finite() else None
 except InvalidOperation:return None

def percent(v):
 d=number(v);return str(d/100) if d is not None and 0<=d<=10000 else None

def timestamp(v):
 d=number(v)
 if d is None or d<=0:return None
 try:return datetime.fromtimestamp(float(d)/(1000 if d>100000000000 else 1),timezone.utc).isoformat()
 except (ValueError,OverflowError,OSError):return None

def normalize(product,campaign,source,rule,calculator,evidence,at):
 pid=str(product.get('product_id') or '');cid=str(campaign.get('campaign_id') or '')
 if not pid.isdigit() or not cid.isdigit():raise CycleError('catalog_identity_missing')
 management=mark_full_managed({},evidence) if str(campaign.get('crs_campaign_type')) in ('8','9') else {}
 total=product.get('total_commission_percent')
 if total is None:total=product.get('partner_commission_percent')
 public=product.get('plan_commission_percent');result=calculator(total,public)
 creator=str(result.creator_pct) if result.valid else None
 status=product.get('product_status');governed=product.get('is_under_governed');unavailable=product.get('unavailable_type')
 available=None
 if governed is True:available=False
 elif status is not None:
  if str(status)!='2':available=False
  elif unavailable_allowed(unavailable,management):available=True
  else:available=False
 start=timestamp(campaign.get('promotion_start_time'));end=timestamp(campaign.get('promotion_end_time'))
 if start and datetime.fromisoformat(start).timestamp()>at:available=False
 if end and datetime.fromisoformat(end).timestamp()<=at:available=False
 title=product.get('product_name') or product.get('title') or pid
 return {**management,'pid':pid,'offerKey':source+':'+pid+':'+cid,'campaignId':cid,'title':str(title)[:500],'catalogSource':source,
 'stock':str(number(product.get('stock'))) if require_stock(management) and number(product.get('stock')) is not None else None,'creatorPercent':creator,
 'publicPercent':percent(public),'totalPercent':percent(total),'endAt':end,'startAt':start,'available':available,
 'rating':str(number(product.get('product_rating'))) if number(product.get('product_rating')) is not None else None,
 'evidenceRef':evidence,'observedAt':at,'commissionState':'proposed_not_applied','commissionRuleId':rule['id'],
 'commissionRuleFingerprint':digest(rule),'cardBindingVerified':False}

def new_state(source,rule,scope,at):
 if source not in ('campaign','selected'):raise CycleError('catalog_source_invalid')
 return {'schema':'bdhub.cycle-catalog.v1','source':source,'scope':scope,'rule':rule,'startedAt':at,'updatedAt':at,
 'state':'running','phase':'campaigns' if source=='campaign' else 'selected','page':1,'total':None,'seen':[],
 'campaigns':[],'campaignIndex':0,'offers':[],'requests':0,'pages':[],'platformWrites':0}

def _total(data,key):
 v=data.get(key)
 if isinstance(v,str) and v.isascii() and v.isdigit():v=int(v)
 if type(v) is not int or v<0:raise CycleError('catalog_total_missing')
 return v

def step(state,read,calculator,at):
 if state['state']=='completed':return
 phase=state['phase'];page=state['page'];params={'cur_page':page,'page_size':100};body=None
 if phase=='campaigns':path=CAMPAIGNS;params.update(campaign_join_status_category='1',crs_campaign_types='');method='GET'
 elif phase=='products':
  path=PRODUCTS;method='GET';c=state['campaigns'][state['campaignIndex']];params.update(campaign_id=c['campaign_id'],marked='0' if str(c.get('crs_campaign_type'))=='7' else 'false')
 else:path=SELECTED;method='POST';body={'cur_page':page,'page_size':100,'filter':{'product_source':[],'campaign_type':[],'label_type':[],'product_status':1}};params={}
 response,sha=read(method,path,params,body);data=response.get('data')
 evidence='catalog-page:'+sha;items=[]
 if phase=='selected':
  # Native selected endpoint puts total_num at the response root.
  total=_total(response,'total_num')
  if state['total'] is not None and total!=state['total']:raise CycleError('catalog_total_changed')
  if data is None and total==0:data=[]
  if not isinstance(data,list):raise CycleError('selected_rows_invalid')
  rows=data;done=len(state['seen'])+len(rows)==total
  if len(state['seen'])+len(rows)>total:raise CycleError('catalog_page_incomplete')
  for row in rows:
   items.append(normalize(row.get('campaign_product') or {},row.get('campaign_info') or {},state['source'],state['rule'],calculator,evidence,at))
  keys=[o['offerKey'] for o in items]
 else:
  if not isinstance(data,dict):raise CycleError('catalog_data_invalid')
  total=_total(data,'total_num')
  if state['total'] is not None and total!=state['total']:raise CycleError('catalog_total_changed')
  key='campaign' if phase=='campaigns' else 'campaign_product'
  rows=data.get(key,[]) if total==0 else data.get(key)
  if not isinstance(rows,list):raise CycleError('catalog_rows_invalid')
  if phase=='campaigns':keys=[str(r.get('campaign_id','')) for r in rows]
  else:
   c=state['campaigns'][state['campaignIndex']]
   items=[normalize(r,c,state['source'],state['rule'],calculator,evidence,at) for r in rows];keys=[o['offerKey'] for o in items]
  done=len(state['seen'])+len(keys)==total
  if len(state['seen'])+len(keys)>total or not rows and not done:raise CycleError('catalog_page_incomplete')
 if len(set(keys))!=len(keys) or set(keys)&set(state['seen']) or any(not k for k in keys):raise CycleError('catalog_duplicate_page')
 if not rows and not done:raise CycleError('catalog_empty_more')
 state['total']=total
 state['seen']+=keys;state['offers']+=items
 state['requests']+=1;state['pages'].append({'phase':phase,'page':page,'rows':len(rows),'sha256':sha,'observedAt':at})
 state['updatedAt']=at
 if phase=='campaigns':
  state['campaigns'] += [{k:r.get(k) for k in ('campaign_id','name','crs_campaign_type','promotion_start_time','promotion_end_time')} for r in rows if str(r.get('crs_campaign_type')) in ('1','5','7')]
 if done:
  state.update(page=1,total=None,seen=[])
  if phase=='campaigns':
   if state['campaigns']:state['phase']='products'
   else:state['state']='completed'
  elif phase=='products':
   state['campaignIndex']+=1
   if state['campaignIndex']>=len(state['campaigns']):state['state']='completed'
  else:state['state']='completed'
 else:state['page']+=1

def read_current_offer(offer,rule,calculator,request,now):
 """Fresh exact PID/campaign facts for either selected or joined-campaign cards."""
 if offer['catalogSource']=='selected':
  body,sha=request('POST',SELECTED,{}, {'cur_page':1,'page_size':100,'product_ids':[offer['pid']],'filter':{'product_source':[],'campaign_type':[],'label_type':[],'product_status':1}})
  rows=body.get('data');matches=[r for r in rows if str((r.get('campaign_product') or {}).get('product_id'))==offer['pid'] and str((r.get('campaign_info') or {}).get('campaign_id'))==offer['campaignId']] if isinstance(rows,list) else []
  if len(matches)!=1 or str(body.get('total_num'))!=str(len(rows)):raise CycleError('current_offer_not_unique')
  return normalize(matches[0]['campaign_product'],matches[0]['campaign_info'],'selected',rule,calculator,sha,now())
 if offer['catalogSource']!='campaign':raise CycleError('creation_route_not_enabled')
 campaign=None
 for page in range(1,6):
  body,_=request('GET',CAMPAIGNS,{'campaign_join_status_category':'1','crs_campaign_types':'','cur_page':page,'page_size':100});data=body.get('data') or {};rows=data.get('campaign') or []
  matches=[r for r in rows if str(r.get('campaign_id'))==offer['campaignId']]
  if len(matches)>1:raise CycleError('current_campaign_not_unique')
  if matches:campaign=matches[0];break
  if page*100>=_total(data,'total_num'):break
 if not campaign:raise CycleError('current_campaign_missing')
 for page in range(1,21):
  body,sha=request('GET',PRODUCTS,{'campaign_id':offer['campaignId'],'marked':'0' if str(campaign.get('crs_campaign_type'))=='7' else 'false','cur_page':page,'page_size':100});data=body.get('data') or {};rows=data.get('campaign_product') or []
  matches=[r for r in rows if str(r.get('product_id'))==offer['pid']]
  if len(matches)>1:raise CycleError('current_offer_not_unique')
  if matches:return normalize(matches[0],campaign,'campaign',rule,calculator,sha,now())
  if page*100>=_total(data,'total_num'):break
 raise CycleError('current_campaign_product_not_located')
