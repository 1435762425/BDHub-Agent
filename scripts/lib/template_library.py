"""Versioned outbound, manual-reply templates and Agent reply schedule settings."""
import json,re,time
from lib.second_cycle import CycleError,digest,encoded
from lib.cycle_materials import TEMPLATES,render as render_builtin

SEND_PLACEHOLDERS=('creator_handle','product_name','creator_commission')
CUSTOM_ID=re.compile(r'custom-[a-f0-9]{24}')
MANUAL_ID=re.compile(r'manual-[a-f0-9]{24}')
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

def _public_builtin(key,value):
 body=value['textIt'].replace('{recipient}',' @{creator_handle}').replace('{rate}','{creator_commission}').replace('{mention}','{product_name}')
 return {'id':key,'name':value['label'],'description':value['description'],'bodyIt':body,'revision':1,
         'builtIn':True,'state':'active','parameters':list(SEND_PLACEHOLDERS)}

def send_templates(store,include_archived=False):
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

def resolve_send_template(store,template_id):
 if not store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='send_message_template'").fetchone():
  if template_id in TEMPLATES:return {'id':template_id,'revision':1,'builtIn':True}
  raise CycleError('template_missing')
 row=store.db.execute('''SELECT t.current_revision,r.body_it FROM send_message_template t
   JOIN send_message_template_revision r ON r.template_id=t.template_id AND r.revision=t.current_revision
   WHERE t.template_id=? AND t.state='active' ''',(template_id,)).fetchone()
 if row:return {'id':template_id,'revision':row['current_revision'],'builtIn':False,'bodyIt':row['body_it']}
 if template_id in TEMPLATES and not store.db.execute('SELECT 1 FROM send_message_template WHERE template_id=?',(template_id,)).fetchone():return {'id':template_id,'revision':1,'builtIn':True}
 raise CycleError('template_missing')

def render_send_template(spec,name,offer,handle):
 if spec['builtIn']:return render_builtin(name,offer,spec['id'],handle)
 rate=str(offer['creatorPercent']).rstrip('0').rstrip('.') if '.' in str(offer['creatorPercent']) else str(offer['creatorPercent'])
 text=spec['bodyIt'].replace('{creator_handle}',handle).replace('{product_name}',name['mentionIt']).replace('{creator_commission}',rate)
 return {'version':4,'template':spec['id'],'templateRevision':spec['revision'],'textIt':text,
  'translationZh':'自定义模板；请以意大利语最终正文为准。','deliveryOrder':'card_then_text','pid':offer['pid'],
  'executionAllowed':False,'requiresVerifiedCard':True,'commissionState':'proposed_not_applied'}

def manual_templates(store,include_archived=False):
 if not store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='manual_reply_template'").fetchone():return []
 where='' if include_archived else "WHERE t.state='active'"
 return [{'id':r['template_id'],'name':r['name'],'category':r['category'],'body':r['body'],
          'revision':r['current_revision'],'state':r['state']} for r in store.db.execute(f'''SELECT t.*,v.body FROM manual_reply_template t
   JOIN manual_reply_template_revision v ON v.template_id=t.template_id AND v.revision=t.current_revision
   {where} ORDER BY t.category,t.name''')]

def upsert_manual_template(store,request_id,template_id,expected_revision,name,category,body):
 name=_name(name);category=_name(category)
 if not isinstance(body,str) or not body.strip() or len(body)>2000:raise CycleError('template_body_invalid')
 now=store.clock()
 if template_id is None:
  if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{7,119}',request_id):raise CycleError('template_request_invalid')
  template_id='manual-'+digest(['manual-template',request_id])[:24];revision=1
  with store.tx():
   prior=store.db.execute('''SELECT t.name,t.category,v.body FROM manual_reply_template t
    JOIN manual_reply_template_revision v ON v.template_id=t.template_id AND v.revision=1
    WHERE t.template_id=?''',(template_id,)).fetchone()
   if prior:
    if prior['name']!=name or prior['category']!=category or prior['body']!=body.strip():
     raise CycleError('template_request_conflict')
   else:
    store.db.execute('INSERT INTO manual_reply_template VALUES(?,?,?,\'active\',1,?,?)',(template_id,name,category,now,now))
    store.db.execute('INSERT INTO manual_reply_template_revision VALUES(?,?,?,?)',(template_id,1,body.strip(),now))
 elif not isinstance(template_id,str) or not MANUAL_ID.fullmatch(template_id) or type(expected_revision) is not int:
  raise CycleError('template_request_invalid')
 else:
  with store.tx():
   row=store.db.execute('SELECT current_revision FROM manual_reply_template WHERE template_id=?',(template_id,)).fetchone()
   if not row or row[0]!=expected_revision:raise CycleError('template_revision_conflict')
   revision=expected_revision+1
   store.db.execute('INSERT INTO manual_reply_template_revision VALUES(?,?,?,?)',(template_id,revision,body.strip(),now))
   store.db.execute("UPDATE manual_reply_template SET name=?,category=?,state='active',current_revision=?,updated_at=? WHERE template_id=?",(name,category,revision,now,template_id))
 return next(value for value in manual_templates(store,True) if value['id']==template_id)

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
