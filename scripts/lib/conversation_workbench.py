"""Read model and local controls for the Italy conversation workbench."""
import json,sqlite3,time
from contextlib import closing
from pathlib import Path
from lib.second_cycle import CycleError,digest
from lib.template_library import agent_setting,manual_templates

HUMAN_REASONS={'human':'需要人工判断','paid_or_budget':'付费或预算','catalog_request':'更多商品目录','whatsapp':'WhatsApp',
 'boost':'Boost','complaint':'投诉','do_not_contact':'明确停联','commission_issue':'佣金异常',
 'link_issue':'链接打不开','multiple_pid':'多个 PID 不明确','multiple_requests':'多个诉求',
 'unsupported_attachment':'图片或附件','unclassified':'无法理解','unsupported_input':'图片或暂不支持的输入',
 'refund_sample':'退款或样品费用','product_context_needs_review':'商品上下文需要核对',
 'refusal_review':'拒绝合作，需人工确认'}
INTENT_REASONS={'paid_collaboration':'paid_or_budget','commission_anomaly':'commission_issue',
 'refusal':'refusal_review','refusal_or_stop_contact':'refusal_review',
 'collaboration_product_request':'catalog_request','ambiguous_request':'unclassified',
 'other':'multiple_requests'}

def _handles(root):
 path=Path(root)/'var/creator-identities.sqlite'
 if not path.exists():return {}
 with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
  return {r[0]:r[1] for r in db.execute("SELECT creator_id,current_handle FROM creator_identity WHERE market='it' AND handle_conflict=0")}

def _latest_decision(db,turn_id):
 review=db.execute('SELECT correct_action FROM turn_review WHERE turn_id=? ORDER BY revision DESC LIMIT 1',(turn_id,)).fetchone()
 row=db.execute("SELECT decision_json FROM reply_classification WHERE state='ready' AND json_extract(input_json,'$.turn.turnId')=? ORDER BY CASE provider WHEN 'deepseek' THEN 0 ELSE 1 END,created_at DESC LIMIT 1",(turn_id,)).fetchone()
 value=json.loads(row[0]) if row else {}
 if review:value['action']=review[0];value['reviewed']=True
 return value if value.get('action') else None

def _human_reason(decision):
 if not decision:return 'human'
 return INTENT_REASONS.get(decision.get('intentCode'),decision.get('intentCode') if decision.get('intentCode') in HUMAN_REASONS else 'human')

def list_conversations(root,store,view='human',query='',limit=30,offset=0):
 if view not in ('human','processing','agent','completed','all') or type(limit) is not int or not 1<=limit<=100 or type(offset) is not int or offset<0 or offset>5000:raise CycleError('conversation_query_invalid')
 handles=_handles(root);db=store.db;plan=db.execute("SELECT id FROM plan WHERE market='it' AND institution='bjn-local-research'").fetchone()[0]
 rows=[]
 for rel in db.execute('SELECT * FROM relationship WHERE plan_id=?',(plan,)):
  latest=db.execute('SELECT * FROM inbound_turn WHERE plan_id=? AND creator_id=? ORDER BY coalesce(occurred_ms,observed_at*1000) DESC,message_id DESC LIMIT 1',(plan,rel['creator_id'])).fetchone()
  case=db.execute("SELECT * FROM service_case WHERE plan_id=? AND creator_id=? AND state='open' ORDER BY updated DESC LIMIT 1",(plan,rel['creator_id'])).fetchone()
  pending=db.execute('SELECT * FROM inbox_pending WHERE plan_id=? AND creator_id=?',(plan,rel['creator_id'])).fetchone()
  reply=db.execute('SELECT state,kind,started,text FROM service_reply WHERE plan_id=? AND creator_id=? ORDER BY created DESC LIMIT 1',(plan,rel['creator_id'])).fetchone() if db.execute("SELECT 1 FROM sqlite_master WHERE name='service_reply'").fetchone() else None
  decision=_latest_decision(db,latest['turn_id']) if latest else None;action=decision.get('action') if decision else None
  if case or (action=='human' and pending and pending['state']!='resolved_by_human') or (pending and pending['state']=='human'):state='human'
  elif rel['mode']=='human' or (pending and pending['state'] in ('awaiting_classification','review_partial','template_ready','policy_review','facts_ready_for_review','needs_facts')):state='processing'
  elif reply and reply['state']=='confirmed' or pending and pending['state'] in ('answered','no_reply'):state='agent'
  elif latest:state='completed'
  else:continue
  handle=handles.get(rel['creator_id'])
  if query and query.casefold() not in f"{handle or ''} {rel['oec']} {(latest['text'] if latest else '')}".casefold():continue
  reason=(case['reason'] if case else _human_reason(decision) if action=='human' else None)
  occurred=(latest['occurred_ms']/1000 if latest and latest['occurred_ms'] else latest['observed_at'] if latest else 0)
  rows.append({'conversationId':latest['cid'] if latest else None,'creatorId':rel['creator_id'],'oec':rel['oec'],'handle':handle,
   'state':state,'humanReason':reason,'humanReasonLabel':HUMAN_REASONS.get(reason,reason) if reason else None,
   'latestText':latest['text'] if latest else None,'latestAt':occurred,'waitingSeconds':max(0,int(store.clock()-occurred)) if occurred else 0,
   'unread':bool(pending and pending['state'] in ('awaiting_classification','review_partial','template_ready','policy_review','facts_ready_for_review','needs_facts','human')),
   'action':action,'caseId':case['id'] if case else None})
 order={'human':0,'processing':1,'agent':2,'completed':3}
 rows.sort(key=lambda r:(order[r['state']],-r['waitingSeconds'],r['creatorId']))
 filtered=rows if view=='all' else [r for r in rows if r['state']==view]
 counts={key:sum(r['state']==key for r in rows) for key in ('human','processing','agent','completed')};counts['all']=len(rows)
 return {'available':True,'view':view,'query':query,'counts':counts,'total':len(filtered),'offset':offset,'limit':limit,
  'nextOffset':offset+limit if offset+limit<len(filtered) else None,'items':filtered[offset:offset+limit],
  'platformWrites':0,'realSends':0}

def conversation_detail(root,store,cid):
 if not isinstance(cid,str) or not cid.isdigit():raise CycleError('conversation_query_invalid')
 db=store.db;plan=db.execute("SELECT id FROM plan WHERE market='it' AND institution='bjn-local-research'").fetchone()[0]
 turn=db.execute('SELECT * FROM inbound_turn WHERE plan_id=? AND cid=? ORDER BY coalesce(occurred_ms,observed_at*1000) DESC LIMIT 1',(plan,cid)).fetchone()
 if not turn:raise CycleError('conversation_missing')
 creator=turn['creator_id'];rel=db.execute('SELECT * FROM relationship WHERE plan_id=? AND creator_id=?',(plan,creator)).fetchone();handles=_handles(root)
 timeline=[]
 for row in db.execute('SELECT * FROM inbound_turn WHERE plan_id=? AND cid=? ORDER BY coalesce(occurred_ms,observed_at*1000),message_id',(plan,cid)):
  timeline.append({'id':row['turn_id'],'direction':'inbound','kind':row['format'],'text':row['text'],'occurredAt':(row['occurred_ms']/1000 if row['occurred_ms'] else row['observed_at']),'status':'received','source':'creator'})
 for row in db.execute('SELECT * FROM outbound_episode WHERE plan_id=? AND creator_id=? ORDER BY sent_at',(plan,creator)):
  payload=json.loads(row['payload_json']);message=payload.get('message') or {}
  timeline.append({'id':row['episode_id'],'direction':'outbound','kind':'text','text':message.get('textIt'),'occurredAt':row['sent_at'],'status':'confirmed','source':'batch','pid':row['pid'],'listId':row['list_id']})
 if db.execute("SELECT 1 FROM sqlite_master WHERE name='service_reply'").fetchone():
  for row in db.execute('SELECT * FROM service_reply WHERE plan_id=? AND creator_id=? ORDER BY created',(plan,creator)):
   card=json.loads(row['text']) if row['kind']=='manual_card' else None
   timeline.append({'id':row['id'],'direction':'outbound','kind':'product_card' if card else 'text','text':f"[商品卡 PID {card['pid']}]" if card else row['text'],'occurredAt':row['started'] or row['created'],'status':row['state'],'source':'human' if row['kind'] in ('manual','manual_card') else 'agent',**({'pid':card['pid'],'listId':card['listId']} if card else {})})
 timeline.sort(key=lambda r:(r['occurredAt'],r['id']))
 episodes=[{'episodeId':r['episode_id'],'pid':r['pid'],'listId':r['list_id'],'sentAt':r['sent_at']} for r in db.execute('SELECT * FROM outbound_episode WHERE plan_id=? AND creator_id=? ORDER BY sent_at DESC LIMIT 10',(plan,creator))]
 case=db.execute("SELECT * FROM service_case WHERE plan_id=? AND creator_id=? AND state='open' ORDER BY updated DESC LIMIT 1",(plan,creator)).fetchone()
 pending=db.execute('SELECT * FROM inbox_pending WHERE plan_id=? AND creator_id=?',(plan,creator)).fetchone()
 decision=_latest_decision(db,turn['turn_id']);reason=_human_reason(decision)
 draft=db.execute('SELECT text,revision,updated_at FROM conversation_draft WHERE plan_id=? AND cid=?',(plan,cid)).fetchone()
 if case:
  case_payload={'id':case['id'],'reason':case['reason'],'reasonLabel':HUMAN_REASONS.get(case['reason'],case['reason']),
   'createdAt':case['created'],'revision':case['assessment_revision'],'virtual':False,'turnId':None,
   'pendingRevision':case['assessment_revision']}
 elif decision and decision.get('action')=='human' and pending and pending['state']!='resolved_by_human':
  case_payload={'id':'review-'+turn['turn_id'][5:],'reason':reason,'reasonLabel':HUMAN_REASONS.get(reason,reason),
   'createdAt':(turn['occurred_ms']/1000 if turn['occurred_ms'] else turn['observed_at']),
   'revision':pending['revision'],'virtual':True,'turnId':turn['turn_id'],'pendingRevision':pending['revision']}
 else:case_payload=None
 return {'available':True,'conversationId':cid,'creator':{'creatorId':creator,'oec':rel['oec'],'handle':handles.get(creator),'mode':rel['mode'],'rejected':bool(rel['rejected']),'unlocked':bool(rel['unlocked']),'revision':rel['revision']},
  'timeline':timeline,'episodes':episodes,'case':case_payload,
  'draft':{'text':draft['text'],'revision':draft['revision'],'updatedAt':draft['updated_at']} if draft else {'text':'','revision':0,'updatedAt':0},
  'manualTemplates':manual_templates(store),'platformWrites':0,'realSends':0}

def save_draft(store,cid,text,expected_revision):
 if not isinstance(cid,str) or not cid.isdigit() or not isinstance(text,str) or len(text)>4000 or type(expected_revision) is not int or expected_revision<0:raise CycleError('conversation_draft_invalid')
 plan=store.db.execute("SELECT id FROM plan WHERE market='it' AND institution='bjn-local-research'").fetchone()[0]
 with store.tx():
  row=store.db.execute('SELECT revision FROM conversation_draft WHERE plan_id=? AND cid=?',(plan,cid)).fetchone();current=row[0] if row else 0
  if current!=expected_revision:raise CycleError('conversation_draft_conflict')
  revision=current+1
  store.db.execute('INSERT INTO conversation_draft VALUES(?,?,?,?,?) ON CONFLICT(plan_id,cid) DO UPDATE SET text=excluded.text,revision=excluded.revision,updated_at=excluded.updated_at',(plan,cid,text,revision,store.clock()))
 return {'conversationId':cid,'text':text,'revision':revision,'updatedAt':store.clock(),'platformWrites':0,'realSends':0}

def complete_reviewed_human(store,cid,turn_id,expected_control_revision,expected_pending_revision,note):
 if not isinstance(cid,str) or not cid.isdigit() or not isinstance(turn_id,str) or not turn_id.startswith('turn-') or \
    type(expected_control_revision) is not int or expected_control_revision<1 or \
    type(expected_pending_revision) is not int or expected_pending_revision<1 or \
    not isinstance(note,str) or not note.strip() or len(note)>4000:
  raise CycleError('conversation_resolution_invalid')
 plan=store.db.execute("SELECT id FROM plan WHERE market='it' AND institution='bjn-local-research'").fetchone()[0]
 with store.tx():
  turn=store.db.execute('SELECT * FROM inbound_turn WHERE plan_id=? AND cid=? ORDER BY coalesce(occurred_ms,observed_at*1000) DESC LIMIT 1',(plan,cid)).fetchone()
  if not turn or turn['turn_id']!=turn_id or (_latest_decision(store.db,turn_id) or {}).get('action')!='human':
   raise CycleError('conversation_resolution_changed')
  case_id='case-'+digest([plan,turn['creator_id'],turn_id,'conversation-workbench'])[:24]
  prior=store.db.execute('SELECT * FROM service_case WHERE id=?',(case_id,)).fetchone()
  if prior and prior['state']=='resolved':
   resolution=store.db.execute('SELECT note FROM service_resolution WHERE case_id=? AND revision=?',(case_id,prior['assessment_revision'])).fetchone()
   if resolution and resolution[0]==note.strip():
    return {'state':'resolved','caseId':case_id,'duplicate':True,'platformWrites':0,'realSends':0}
   raise CycleError('conversation_resolution_conflict')
  if store.db.execute("SELECT 1 FROM service_case WHERE plan_id=? AND creator_id=? AND state='open'",(plan,turn['creator_id'])).fetchone():
   raise CycleError('conversation_resolution_changed')
  rel=store.db.execute('SELECT * FROM relationship WHERE plan_id=? AND creator_id=?',(plan,turn['creator_id'])).fetchone()
  pending=store.db.execute('SELECT * FROM inbox_pending WHERE plan_id=? AND creator_id=?',(plan,turn['creator_id'])).fetchone()
  if not rel or rel['revision']!=expected_control_revision or not pending or pending['revision']!=expected_pending_revision:
   raise CycleError('conversation_resolution_changed')
  now=store.clock();reason=_human_reason(_latest_decision(store.db,turn_id))
  store.db.execute("INSERT INTO service_case VALUES(?,?,?,'resolved',?,?,?,?, 'not_sent')",(case_id,plan,turn['creator_id'],pending['revision'],reason,now,now))
  store.db.execute('INSERT INTO service_case_turn VALUES(?,?)',(case_id,turn_id))
  store.db.execute('INSERT INTO service_resolution VALUES(?,?,?,?)',(case_id,pending['revision'],note.strip(),now))
  watermark=store.db.execute("SELECT coalesce(max(rowid),0) FROM inbox_event WHERE plan_id=? AND oec=? AND kind='creatorReplies'",(plan,rel['oec'])).fetchone()[0]
  store.db.execute('INSERT INTO service_cursor VALUES(?,?,?) ON CONFLICT(plan_id,creator_id) DO UPDATE SET event_rowid=max(event_rowid,excluded.event_rowid)',(plan,turn['creator_id'],watermark))
  store.db.execute("UPDATE inbox_pending SET state='resolved_by_human' WHERE plan_id=? AND creator_id=? AND revision=?",(plan,turn['creator_id'],pending['revision']))
  store.db.execute("UPDATE relationship SET mode='auto',inbox_until=0,revision=revision+1 WHERE plan_id=? AND creator_id=? AND revision=?",(plan,turn['creator_id'],rel['revision']))
 return {'state':'resolved','caseId':case_id,'duplicate':False,'platformWrites':0,'realSends':0}

def workspace_status(root,store):
 plan=store.db.execute("SELECT id FROM plan WHERE market='it' AND institution='bjn-local-research'").fetchone()[0]
 latest=store.db.execute('SELECT * FROM agent_reply_run WHERE plan_id=? ORDER BY started_at DESC LIMIT 1',(plan,)).fetchone()
 return {'agentSetting':agent_setting(store,plan),'latestAgentRun':dict(latest) if latest else None,
  'manualTemplates':manual_templates(store),'platformWrites':0,'realSends':0}
