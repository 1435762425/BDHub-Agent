"""Versioned outbound, manual-reply templates and Agent reply schedule settings."""
import json,re,time
from pathlib import Path
from lib.second_cycle import CycleError,digest,encoded
from lib.cycle_materials import TEMPLATES
from lib.market_content import SEND_IDS

SEND_PLACEHOLDERS=('creator_handle','product_name','creator_commission')
CUSTOM_ID=re.compile(r'custom-[a-f0-9]{24}')
MANUAL_ID=re.compile(r'manual-[a-f0-9]{24}')
AGENT_TEMPLATE_KEYS={'sample_self_service_v1':'sample_self_service',
 'collaboration_ack_v1':'collaboration_ack','link_usage_v1':'link_usage'}
TIME=re.compile(r'(?:[01]\d|2[0-3]):[0-5]\d|24:00')
DEFAULT_AGENT_SETTING={'enabled':False,'timezone':'Asia/Shanghai','replyStart':'15:00','replyEnd':'16:00',
 'sendStart':'16:30','sendEnd':'24:00','bufferMinutes':30,
 'actions':{'no_reply':True,'sample_self_service':True,'collaboration_ack':True,'link_usage':True,'human':False},
 'revision':0,'updatedAt':0}

def _body(value,limit=600):
 if not isinstance(value,str) or not value.strip() or len(value)>limit or any(c in value for c in '\r<>'):
  raise CycleError('template_body_invalid')
 fields=set(re.findall(r'\{([a-z_]+)\}',value))
 if not {'product_name','creator_commission'}<=fields or fields-set(SEND_PLACEHOLDERS):
  raise CycleError('template_parameters_invalid')
 return value.strip()

def _name(value):
 if not isinstance(value,str) or not 1<=len(value.strip())<=60 or any(ord(c)<32 for c in value):
  raise CycleError('template_name_invalid')
 return value.strip()

def _public_builtin(key,value,market='it'):
 body=(value.get('textIt') or value.get('text')).replace('{recipient}',' @{creator_handle}').replace('{rate}','{creator_commission}').replace('{mention}','{product_name}')
 from lib.market_content import market_content
 content=market_content(Path(__file__).resolve().parents[2],market)
 return {'id':key,'semanticId':key,'name':value['label'],'description':value['description'],
         'bodyIt':body,'body':body,'market':market,'language':content['language'],'locale':content['locale'],
         'translationZh':value.get('translationZh') or '',
         'revision':1,'builtIn':True,'state':'active','parameters':list(SEND_PLACEHOLDERS)}

def send_templates(store,include_archived=False,market='it'):
 if market!='it':
  from lib.market_content import send_template_map
  return [_public_builtin(key,value,market) for key,value in send_template_map(Path(__file__).resolve().parents[2],market).items()]
 defaults={key:_public_builtin(key,value) for key,value in TEMPLATES.items()}
 if not store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='send_message_template'").fetchone():return list(defaults.values())
 saved=list(store.db.execute('''SELECT t.*,r.body_it FROM send_message_template t
   JOIN send_message_template_revision r ON r.template_id=t.template_id AND r.revision=t.current_revision
   ORDER BY t.updated_at DESC,t.template_id'''))
 indexed={row['template_id']:row for row in saved};rows=[]
 for key,default in defaults.items():
  row=indexed.get(key)
  if row:
   if row['state']=='archived' and not include_archived:continue
   rows.append({**default,'name':row['name'],'bodyIt':row['body_it'],'revision':row['current_revision'],'state':row['state']})
  else:rows.append(default)
 for row in saved:
  if row['template_id'] in TEMPLATES or (row['state']=='archived' and not include_archived):continue
  rows.append({'id':row['template_id'],'name':row['name'],'description':'自定义二发批量话术',
   'bodyIt':row['body_it'],'revision':row['current_revision'],'builtIn':False,'state':row['state'],
   'parameters':list(SEND_PLACEHOLDERS)})
 return rows


def _review_fingerprint(store,root,template_id):
 from lib.market_content import load_content
 content=load_content(root);bundle={}
 for market,row in content['markets'].items():
  template=row['sendTemplates'][template_id]
  bundle[market]={'text':template['text'],'translationZh':template['translationZh'],'state':'active'}
 saved=store.db.execute('''SELECT t.state,r.body_it FROM send_message_template t
   JOIN send_message_template_revision r ON r.template_id=t.template_id AND r.revision=t.current_revision
   WHERE t.template_id=?''',(template_id,)).fetchone() if store.db.execute(
    "SELECT 1 FROM sqlite_master WHERE name='send_message_template'").fetchone() else None
 if saved:
  bundle['it']['text']=saved['body_it'];bundle['it']['state']=saved['state']
 return digest({'templateId':template_id,'markets':bundle})


def send_template_reviews(store,root,market='it'):
 if not store.db.execute("SELECT 1 FROM sqlite_master WHERE name='send_template_review'").fetchone():
  raise CycleError('send_template_review_schema_required')
 rows={row['template_id']:row for row in store.db.execute('SELECT * FROM send_template_review')}
 active_ids=({row['id'] for row in send_templates(store,market='it') if row['builtIn']}
             if market=='it' else set(SEND_IDS))
 items=[]
 for template_id in SEND_IDS:
  if template_id not in active_ids:continue
  fingerprint=_review_fingerprint(store,root,template_id);row=rows.get(template_id)
  current=bool(row and row['content_fingerprint']==fingerprint)
  state=row['state'] if current else 'pending'
  items.append({'templateId':template_id,'state':state,'revision':row['revision'] if row else 0,
                'contentFingerprint':fingerprint,'updatedAt':row['updated_at'] if current else 0})
 minimum=__import__('lib.operations_policy',fromlist=['load_policy']).load_policy(root)['sendTemplateApprovalMinimum']
 approved=sum(row['state']=='approved' for row in items)
 return {'minimumApproved':minimum,'approved':approved,'total':len(items),'ready':approved>=minimum,
         'items':items}


def review_send_template(store,root,request_id,template_id,state,expected_revision):
 if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{7,119}',request_id) or \
    template_id not in SEND_IDS or state not in ('approved','rejected') or \
    type(expected_revision) is not int or expected_revision<0:
  raise CycleError('send_template_review_invalid')
 fingerprint=_review_fingerprint(store,root,template_id);payload=encoded([template_id,fingerprint,state])
 with store.tx():
  prior=store.db.execute('SELECT * FROM send_template_review_event WHERE request_id=?',(request_id,)).fetchone()
  if prior:
   if encoded([prior['template_id'],prior['content_fingerprint'],prior['state']])!=payload:
    raise CycleError('send_template_review_request_conflict')
   return send_template_reviews(store,root)
  current=store.db.execute('SELECT * FROM send_template_review WHERE template_id=?',(template_id,)).fetchone()
  revision=current['revision'] if current else 0
  if revision!=expected_revision:raise CycleError('send_template_review_conflict')
  revision+=1;now=store.clock()
  store.db.execute('''INSERT INTO send_template_review VALUES(?,?,?,?,?)
   ON CONFLICT(template_id) DO UPDATE SET content_fingerprint=excluded.content_fingerprint,
   state=excluded.state,revision=excluded.revision,updated_at=excluded.updated_at''',
   (template_id,fingerprint,state,revision,now))
  store.db.execute('INSERT INTO send_template_review_event VALUES(?,?,?,?,?,?,?)',
   (request_id,template_id,fingerprint,state,expected_revision,revision,now))
 return send_template_reviews(store,root)


def require_send_template_approval(store,root,market='it'):
 status=send_template_reviews(store,root,market)
 if not status['ready']:raise CycleError('send_template_approval_required')
 return status


def used_send_template_ids(store,plan,creator_id):
 if not store.db.execute("SELECT 1 FROM sqlite_master WHERE name='cycle_delivery'").fetchone():return set()
 return {str(row[0]) for row in store.db.execute('''SELECT DISTINCT json_extract(d.snapshot,'$.message.template')
  FROM cycle_delivery d WHERE d.plan_id=? AND d.creator_id=?
    AND json_extract(d.snapshot,'$.message.template') IS NOT NULL
    AND (d.state IN ('ready','running','unknown') OR EXISTS(
      SELECT 1 FROM cycle_delivery_part p WHERE p.delivery_id=d.id AND p.kind='text' AND p.started IS NOT NULL))''',
  (plan,creator_id))}


def next_approved_send_template(store,root,plan,creator_id,market='it'):
 review=send_template_reviews(store,root,market)
 approved={row['templateId'] for row in review['items'] if row['state']=='approved'}
 used=used_send_template_ids(store,plan,creator_id)
 for template_id in SEND_IDS:
  if template_id in approved and template_id not in used:
   return resolve_send_template(store,template_id,market=market)
 return None

def create_send_template(store,request_id,name,body):
 if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{7,119}',request_id):
  raise CycleError('template_request_invalid')
 template_id='custom-'+digest(['send-template',request_id])[:24];name=_name(name);body=_body(body);now=store.clock()
 with store.tx():
  prior=store.db.execute('SELECT * FROM send_message_template WHERE template_id=?',(template_id,)).fetchone()
  if prior:
   revision=store.db.execute('SELECT body_it FROM send_message_template_revision WHERE template_id=? AND revision=1',(template_id,)).fetchone()
   if prior['name']!=name or not revision or revision[0]!=body:raise CycleError('template_request_conflict')
  else:
   store.db.execute('INSERT INTO send_message_template VALUES(?,?,\'active\',1,?,?)',(template_id,name,now,now))
   store.db.execute('INSERT INTO send_message_template_revision VALUES(?,?,?,?)',(template_id,1,body,now))
 return next(row for row in send_templates(store,True) if row['id']==template_id)

def update_send_template(store,template_id,expected_revision,name,body):
 if not isinstance(template_id,str) or (template_id not in TEMPLATES and not CUSTOM_ID.fullmatch(template_id)) or type(expected_revision) is not int or expected_revision<1:
  raise CycleError('template_request_invalid')
 name=_name(name);body=_body(body);now=store.clock()
 with store.tx():
  row=store.db.execute('SELECT * FROM send_message_template WHERE template_id=?',(template_id,)).fetchone()
  if not row:
   if template_id not in TEMPLATES or expected_revision!=1:raise CycleError('template_revision_conflict')
   original=_public_builtin(template_id,TEMPLATES[template_id]);revision=2
   store.db.execute("INSERT INTO send_message_template VALUES(?,?,\'active\',?,?,?)",(template_id,name,revision,now,now))
   store.db.execute('INSERT INTO send_message_template_revision VALUES(?,?,?,?)',(template_id,1,original['bodyIt'],now))
  else:
   if row['current_revision']!=expected_revision:raise CycleError('template_revision_conflict')
   revision=expected_revision+1
  store.db.execute('INSERT INTO send_message_template_revision VALUES(?,?,?,?)',(template_id,revision,body,now))
  store.db.execute("UPDATE send_message_template SET name=?,state='active',current_revision=?,updated_at=? WHERE template_id=?",(name,revision,now,template_id))
 return next(value for value in send_templates(store,True) if value['id']==template_id)

def archive_send_template(store,template_id,expected_revision):
 if not isinstance(template_id,str) or (template_id not in TEMPLATES and not CUSTOM_ID.fullmatch(template_id)) or type(expected_revision) is not int or expected_revision<1:raise CycleError('template_request_invalid')
 with store.tx():
  row=store.db.execute('SELECT current_revision FROM send_message_template WHERE template_id=?',(template_id,)).fetchone()
  now=store.clock()
  if not row:
   if template_id not in TEMPLATES or expected_revision!=1:raise CycleError('template_revision_conflict')
   default=_public_builtin(template_id,TEMPLATES[template_id])
   store.db.execute("INSERT INTO send_message_template VALUES(?,?,\'archived\',1,?,?)",(template_id,default['name'],now,now))
   store.db.execute('INSERT INTO send_message_template_revision VALUES(?,?,?,?)',(template_id,1,default['bodyIt'],now))
  else:
   if row[0]!=expected_revision:raise CycleError('template_revision_conflict')
   store.db.execute("UPDATE send_message_template SET state='archived',updated_at=? WHERE template_id=?",(now,template_id))
 return {'id':template_id,'state':'archived','revision':expected_revision}

def resolve_send_template(store,template_id,market='it'):
 if market!='it':
  from lib.market_content import send_template_map
  try:return _public_builtin(template_id,send_template_map(Path(__file__).resolve().parents[2],market)[template_id],market)
  except KeyError:raise CycleError('template_missing') from None
 if not store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='send_message_template'").fetchone():
  if template_id in TEMPLATES:return _public_builtin(template_id,TEMPLATES[template_id])
  raise CycleError('template_missing')
 row=store.db.execute('''SELECT t.current_revision,r.body_it FROM send_message_template t
   JOIN send_message_template_revision r ON r.template_id=t.template_id AND r.revision=t.current_revision
   WHERE t.template_id=? AND t.state='active' ''',(template_id,)).fetchone()
 if row:
  default=_public_builtin(template_id,TEMPLATES[template_id]) if template_id in TEMPLATES else {}
  return {**default,'id':template_id,'revision':row['current_revision'],'builtIn':False,'bodyIt':row['body_it']}
 if template_id in TEMPLATES and not store.db.execute('SELECT 1 FROM send_message_template WHERE template_id=?',(template_id,)).fetchone():return _public_builtin(template_id,TEMPLATES[template_id])
 raise CycleError('template_missing')

def render_send_template(spec,name,offer,handle,market='it'):
 rate=str(offer['creatorPercent']).rstrip('0').rstrip('.') if '.' in str(offer['creatorPercent']) else str(offer['creatorPercent'])
 mention=name.get('mentionIt') if market=='it' else name.get('mention')
 if not isinstance(mention,str) or not mention:raise CycleError('localized_name_missing')
 text=spec['bodyIt'].replace('{creator_handle}',handle).replace('{product_name}',mention).replace('{creator_commission}',rate)
 translation=spec.get('translationZh') or '自定义模板；请以最终正文为准。'
 translation=translation.replace('{shortZh}',str(name.get('shortNameZh') or '')).replace('{rate}',rate)
 from lib.market_content import market_content
 return {'version':4,'template':spec['id'],'templateRevision':spec['revision'],'market':market,
  'language':market_content(Path(__file__).resolve().parents[2],market)['language'],'text':text,'textIt':text,
  'translationZh':translation,'deliveryOrder':'card_then_text','pid':offer['pid'],
  'executionAllowed':False,'requiresVerifiedCard':True,'commissionState':'proposed_not_applied'}

def selected_send_template(store,root):
 try:value=json.loads((Path(root)/'config/send-batch.json').read_text(encoding='utf-8'));selected=value.get('template')
 except (OSError,ValueError,TypeError):selected=None
 if store.db.execute("SELECT 1 FROM sqlite_master WHERE name='continuous_send_control'").fetchone():
  current=store.db.execute('SELECT template_id FROM continuous_send_control ORDER BY updated_at DESC LIMIT 1').fetchone()
  if current:selected=current[0]
 return selected

def _template_plan(store,market):
 if not store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_manual_reply_template'").fetchone():
  raise CycleError('market_template_schema_required')
 row=store.db.execute("SELECT id FROM plan WHERE market=? AND institution='bjn-local-research'",(market,)).fetchone()
 if not row:raise CycleError('plan_missing')
 return row[0]

def manual_templates(store,include_archived=False,market='it'):
 plan=_template_plan(store,market);where='' if include_archived else "AND t.state='active'"
 return [{'id':r['template_id'],'name':r['name'],'category':r['category'],'body':r['body'],
          'revision':r['current_revision'],'state':r['state']} for r in store.db.execute(f'''SELECT t.*,v.body FROM market_manual_reply_template t
   JOIN market_manual_reply_template_revision v ON v.plan_id=t.plan_id AND v.template_id=t.template_id AND v.revision=t.current_revision
   WHERE t.plan_id=? {where} ORDER BY t.category,t.name''',(plan,))]

def upsert_manual_template(store,request_id,template_id,expected_revision,name,category,body,market='it'):
 name=_name(name);category=_name(category)
 if not isinstance(body,str) or not body.strip() or len(body)>2000:raise CycleError('template_body_invalid')
 plan=_template_plan(store,market);now=store.clock()
 if template_id is None:
  if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{7,119}',request_id):raise CycleError('template_request_invalid')
  template_id='manual-'+digest(['manual-template',request_id])[:24];revision=1
  with store.tx():
   prior=store.db.execute('''SELECT t.name,t.category,v.body FROM market_manual_reply_template t
    JOIN market_manual_reply_template_revision v ON v.plan_id=t.plan_id AND v.template_id=t.template_id AND v.revision=1
    WHERE t.plan_id=? AND t.template_id=?''',(plan,template_id)).fetchone()
   if prior:
    if prior['name']!=name or prior['category']!=category or prior['body']!=body.strip():
     raise CycleError('template_request_conflict')
   else:
    store.db.execute('INSERT INTO market_manual_reply_template VALUES(?,?,?,?,\'active\',1,?,?)',(plan,template_id,name,category,now,now))
    store.db.execute('INSERT INTO market_manual_reply_template_revision VALUES(?,?,?,?,?)',(plan,template_id,1,body.strip(),now))
 elif not isinstance(template_id,str) or not MANUAL_ID.fullmatch(template_id) or type(expected_revision) is not int:
  raise CycleError('template_request_invalid')
 else:
  with store.tx():
   row=store.db.execute('SELECT current_revision FROM market_manual_reply_template WHERE plan_id=? AND template_id=?',(plan,template_id)).fetchone()
   if not row or row[0]!=expected_revision:raise CycleError('template_revision_conflict')
   revision=expected_revision+1
   store.db.execute('INSERT INTO market_manual_reply_template_revision VALUES(?,?,?,?,?)',(plan,template_id,revision,body.strip(),now))
   store.db.execute("UPDATE market_manual_reply_template SET name=?,category=?,state='active',current_revision=?,updated_at=? WHERE plan_id=? AND template_id=?",(name,category,revision,now,plan,template_id))
 return next(value for value in manual_templates(store,True,market) if value['id']==template_id)

def agent_templates(store,policy,market='it'):
 plan=_template_plan(store,market)
 if market=='it':
  defaults=policy.get('templates') or {};language='it'
 else:
  from lib.market_content import agent_template_map as content_templates,market_content
  content=market_content(Path(__file__).resolve().parents[2],market);language=content['language']
  texts=content_templates(Path(__file__).resolve().parents[2],market)
  defaults={key:{'action':action,'text':texts[action]} for key,action in AGENT_TEMPLATE_KEYS.items()}
 rows=[]
 for key,action in AGENT_TEMPLATE_KEYS.items():
  original=defaults.get(key) or {}
  if original.get('action')!=action or not isinstance(original.get('text'),str):raise CycleError('agent_template_policy_invalid')
  saved=store.db.execute('''SELECT t.current_revision,r.body FROM market_agent_reply_template t
   JOIN market_agent_reply_template_revision r ON r.plan_id=t.plan_id AND r.template_key=t.template_key AND r.revision=t.current_revision
   WHERE t.plan_id=? AND t.template_key=? AND t.action=?''',(plan,key,action)).fetchone()
  rows.append({'id':key,'action':action,'language':language,'text':saved['body'] if saved else original['text'],
   'revision':saved['current_revision'] if saved else 1})
 return rows

def update_agent_template(store,policy,template_key,expected_revision,body,market='it'):
 if template_key not in AGENT_TEMPLATE_KEYS or type(expected_revision) is not int or expected_revision<1 or \
    not isinstance(body,str) or not body.strip() or len(body)>4000 or any(ord(c)<9 for c in body):
  raise CycleError('agent_template_request_invalid')
 plan=_template_plan(store,market);defaults={row['id']:row for row in agent_templates(store,policy,market)};default=defaults[template_key];now=store.clock()
 with store.tx():
  row=store.db.execute('SELECT current_revision,action FROM market_agent_reply_template WHERE plan_id=? AND template_key=?',(plan,template_key)).fetchone()
  if not row:
   if expected_revision!=1:raise CycleError('agent_template_revision_conflict')
   revision=2
   store.db.execute('INSERT INTO market_agent_reply_template VALUES(?,?,?,?,?)',(plan,template_key,AGENT_TEMPLATE_KEYS[template_key],revision,now))
   store.db.execute('INSERT INTO market_agent_reply_template_revision VALUES(?,?,?,?,?)',(plan,template_key,1,default['text'],now))
  else:
   if row['action']!=AGENT_TEMPLATE_KEYS[template_key] or row['current_revision']!=expected_revision:
    raise CycleError('agent_template_revision_conflict')
   revision=expected_revision+1
   store.db.execute('UPDATE market_agent_reply_template SET current_revision=?,updated_at=? WHERE plan_id=? AND template_key=?',(revision,now,plan,template_key))
  store.db.execute('INSERT INTO market_agent_reply_template_revision VALUES(?,?,?,?,?)',(plan,template_key,revision,body.strip(),now))
 return next(row for row in agent_templates(store,policy,market) if row['id']==template_key)

def agent_template_map(store,policy,market='it'):
 return {row['action']:(row['id'],row['text'],row['revision']) for row in agent_templates(store,policy,market)}

def _minutes(value,allow_24=False):
 if not isinstance(value,str) or not TIME.fullmatch(value) or (value=='24:00' and not allow_24):raise CycleError('reply_schedule_invalid')
 if value=='24:00':return 1440
 hour,minute=map(int,value.split(':'));return hour*60+minute

def validate_agent_setting(value):
 if not isinstance(value,dict):raise CycleError('reply_setting_invalid')
 merged={**DEFAULT_AGENT_SETTING,**value};allowed=set(DEFAULT_AGENT_SETTING)-{'revision','updatedAt'}
 if set(value)-allowed:raise CycleError('reply_setting_invalid')
 if type(merged['enabled']) is not bool or merged['timezone']!='Asia/Shanghai' or type(merged['bufferMinutes']) is not int or not 0<=merged['bufferMinutes']<=180:raise CycleError('reply_setting_invalid')
 rs,re=_minutes(merged['replyStart']),_minutes(merged['replyEnd'],True);ss,se=_minutes(merged['sendStart']),_minutes(merged['sendEnd'],True)
 if not rs<re<=ss<se or ss-re<merged['bufferMinutes']:raise CycleError('reply_schedule_overlap')
 actions=merged['actions'];expected=set(DEFAULT_AGENT_SETTING['actions'])
 if not isinstance(actions,dict) or set(actions)!=expected or any(type(v) is not bool for v in actions.values()) or actions['human']:
  raise CycleError('reply_setting_invalid')
 return {key:merged[key] for key in allowed}

def agent_setting(store,plan):
 row=store.db.execute('SELECT * FROM agent_reply_setting WHERE plan_id=?',(plan,)).fetchone()
 if not row:return dict(DEFAULT_AGENT_SETTING)
 return {'enabled':bool(row['enabled']),'timezone':row['timezone'],'replyStart':row['reply_start'],'replyEnd':row['reply_end'],
  'sendStart':row['send_start'],'sendEnd':row['send_end'],'bufferMinutes':row['buffer_minutes'],
  'actions':json.loads(row['actions_json']),'revision':row['revision'],'updatedAt':row['updated_at']}

def save_agent_setting(store,plan,expected_revision,value):
 setting=validate_agent_setting(value);now=store.clock()
 store.db.execute('CREATE TABLE IF NOT EXISTS service_reply_config(plan_id TEXT PRIMARY KEY,enabled INTEGER NOT NULL,authorization TEXT NOT NULL)')
 with store.tx():
  current=agent_setting(store,plan)
  if current['revision']!=expected_revision:raise CycleError('reply_setting_conflict')
  revision=expected_revision+1
  store.db.execute('''INSERT INTO agent_reply_setting VALUES(?,?,?,?,?,?,?,?,?,?,?)
   ON CONFLICT(plan_id) DO UPDATE SET enabled=excluded.enabled,timezone=excluded.timezone,
   reply_start=excluded.reply_start,reply_end=excluded.reply_end,send_start=excluded.send_start,
   send_end=excluded.send_end,buffer_minutes=excluded.buffer_minutes,actions_json=excluded.actions_json,
   revision=excluded.revision,updated_at=excluded.updated_at''',(plan,int(setting['enabled']),setting['timezone'],setting['replyStart'],setting['replyEnd'],setting['sendStart'],setting['sendEnd'],setting['bufferMinutes'],encoded(setting['actions']),revision,now))
  store.db.execute('INSERT INTO service_reply_config(plan_id,enabled,authorization) VALUES(?,?,?) ON CONFLICT(plan_id) DO UPDATE SET enabled=excluded.enabled,authorization=excluded.authorization',(plan,int(setting['enabled']),'conversation-workbench-agent-v1'))
 return agent_setting(store,plan)
