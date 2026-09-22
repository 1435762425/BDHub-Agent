"""Market-scoped read model and local controls for the conversation workbench."""
import json,re,sqlite3,time
from contextlib import closing
from pathlib import Path
from lib.second_cycle import CycleError,digest,encoded
from lib.template_library import agent_setting,manual_templates
from lib.cycle_service import Service

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
SHOWCASE_TEXT='达人已将商品添加到橱窗'

def _handles(root,market='it'):
 path=Path(root)/'var/creator-identities.sqlite'
 if not path.exists():return {}
 with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
  return {r[0]:r[1] for r in db.execute("SELECT creator_id,current_handle FROM creator_identity WHERE market=? AND handle_conflict=0",(market,))}

def _metric_value(fields,name):
 row=fields.get(name) if isinstance(fields,dict) else None
 if not isinstance(row,dict) or row.get('status') not in ('value','zero'):return None
 value=row.get('value')
 if isinstance(value,(int,float)) and not isinstance(value,bool):return value
 if isinstance(value,str):return value[:128]
 if isinstance(value,dict):
  formatted=value.get('format')
  if isinstance(formatted,str) and formatted.strip():return formatted[:128]
  decimal=value.get('decimal');symbol=value.get('rawSymbol')
  if isinstance(decimal,str) and re.fullmatch(r'\d+(?:\.\d+)?',decimal):return f"{symbol if isinstance(symbol,str) else ''}{decimal}"[:128]
 return None

def _creator_metrics(root,db,plan,creator,oec):
 result={'gmv':None,'videoGmv':None,'liveGmv':None,'followers':None,'unitsSold':None,
  'avgVideoViews':None,'observedAt':None,'replyCount':0,'showcaseCount':0}
 path=Path(root)/'var/creator-identities.sqlite'
 if path.exists():
  with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as identities:
   row=identities.execute("""SELECT payload_json,observed_at FROM identity_observation
    WHERE creator_id=? AND kind='profile' AND json_type(payload_json,'$.fields')='object'
    ORDER BY observed_us DESC,length(payload_json) DESC,event_id LIMIT 1""",(creator,)).fetchone() if identities.execute("SELECT 1 FROM sqlite_master WHERE name='identity_observation'").fetchone() else None
   if row:
    try:fields=json.loads(row[0]).get('fields') or {}
    except (TypeError,ValueError):fields={}
    result.update(gmv=_metric_value(fields,'med_gmv_revenue'),videoGmv=_metric_value(fields,'video_gmv'),
     liveGmv=_metric_value(fields,'live_gmv'),followers=_metric_value(fields,'follower_cnt'),
     unitsSold=_metric_value(fields,'units_sold'),avgVideoViews=_metric_value(fields,'video_avg_view_cnt'),
     observedAt=row[1])
 if db.execute("SELECT 1 FROM sqlite_master WHERE name='inbox_event'").fetchone():
  counts=dict(db.execute("SELECT kind,count(*) FROM inbox_event WHERE plan_id=? AND oec=? AND historical=0 AND kind IN ('creatorReplies','showcaseNotifications') GROUP BY kind",(plan,oec)))
  result['replyCount']=int(counts.get('creatorReplies',0));result['showcaseCount']=int(counts.get('showcaseNotifications',0))
 return result

def _latest_manual_reply(db,plan,creator,after):
 if not db.execute("SELECT 1 FROM sqlite_master WHERE name='service_reply'").fetchone():return None
 return db.execute("""SELECT id,kind,coalesce(started,created) confirmed_at FROM service_reply
  WHERE plan_id=? AND creator_id=? AND kind IN ('manual','manual_card') AND state='confirmed'
  AND coalesce(started,created)>=? ORDER BY coalesce(started,created) DESC,id DESC LIMIT 1""",(plan,creator,after)).fetchone()

def _latest_decision(db,turn_id):
 review=db.execute('SELECT correct_action FROM turn_review WHERE turn_id=? ORDER BY revision DESC LIMIT 1',(turn_id,)).fetchone()
 row=db.execute("SELECT decision_json FROM reply_classification WHERE state='ready' AND json_extract(input_json,'$.turn.turnId')=? ORDER BY CASE provider WHEN 'deepseek' THEN 0 ELSE 1 END,created_at DESC LIMIT 1",(turn_id,)).fetchone()
 value=json.loads(row[0]) if row else {}
 if review:value['action']=review[0];value['reviewed']=True
 return value if value.get('action') else None

def _latest_showcase(db,plan,oec):
 if not db.execute("SELECT 1 FROM sqlite_master WHERE name='inbox_event'").fetchone():return None
 return db.execute("""SELECT cid,message_id,occurred_ms,historical,observed_at FROM inbox_event
  WHERE plan_id=? AND oec=? AND kind='showcaseNotifications'
  ORDER BY coalesce(occurred_ms,observed_at*1000) DESC,message_id DESC LIMIT 1""",(plan,oec)).fetchone()

def _human_reason(decision):
 if not decision:return 'human'
 return INTENT_REASONS.get(decision.get('intentCode'),decision.get('intentCode') if decision.get('intentCode') in HUMAN_REASONS else 'human')

def list_conversations(root,store,view='human',query='',limit=30,offset=0,market='it'):
 if view not in ('human','agent','completed','all') or type(limit) is not int or not 1<=limit<=100 or type(offset) is not int or offset<0 or offset>5000:raise CycleError('conversation_query_invalid')
 handles=_handles(root,market);db=store.db;plan=db.execute("SELECT id FROM plan WHERE market=? AND institution='bjn-local-research'",(market,)).fetchone()
 if not plan:raise CycleError('plan_missing')
 plan=plan[0]
 rows=[]
 for rel in db.execute('SELECT * FROM relationship WHERE plan_id=?',(plan,)):
  latest=db.execute('SELECT * FROM inbound_turn WHERE plan_id=? AND creator_id=? ORDER BY coalesce(occurred_ms,observed_at*1000) DESC,message_id DESC LIMIT 1',(plan,rel['creator_id'])).fetchone()
  case=db.execute("SELECT * FROM service_case WHERE plan_id=? AND creator_id=? AND state='open' ORDER BY updated DESC LIMIT 1",(plan,rel['creator_id'])).fetchone()
  pending=db.execute('SELECT * FROM inbox_pending WHERE plan_id=? AND creator_id=?',(plan,rel['creator_id'])).fetchone()
  reply=db.execute('SELECT state,kind,started,text FROM service_reply WHERE plan_id=? AND creator_id=? ORDER BY created DESC LIMIT 1',(plan,rel['creator_id'])).fetchone() if db.execute("SELECT 1 FROM sqlite_master WHERE name='service_reply'").fetchone() else None
  showcase=_latest_showcase(db,plan,rel['oec'])
  decision=_latest_decision(db,latest['turn_id']) if latest else None;action=decision.get('action') if decision else None
  if case or rel['mode']=='human' or (action=='human' and pending and pending['state']!='resolved_by_human') or (pending and pending['state']=='human'):state='human'
  elif pending and pending['state'] in ('awaiting_classification','review_partial','template_ready','policy_review','facts_ready_for_review','needs_facts'):state='agent'
  elif reply and reply['state']=='confirmed' or pending and pending['state'] in ('answered','no_reply'):state='agent'
  elif latest:state='completed'
  else:continue
  handle=handles.get(rel['creator_id'])
  if query and query.casefold() not in f"{handle or ''} {rel['oec']} {(latest['text'] if latest else '')}".casefold():continue
  reason=(case['reason'] if case else _human_reason(decision) if action=='human' else None)
  occurred=(latest['occurred_ms']/1000 if latest and latest['occurred_ms'] else latest['observed_at'] if latest else 0)
  showcase_at=(showcase['occurred_ms']/1000 if showcase and showcase['occurred_ms'] else showcase['observed_at'] if showcase else 0)
  display_at=max(occurred,showcase_at);display_text=SHOWCASE_TEXT if showcase_at>occurred else latest['text'] if latest else None
  rows.append({'conversationId':latest['cid'] if latest else None,'creatorId':rel['creator_id'],'oec':rel['oec'],'handle':handle,
   'state':state,'humanReason':reason,'humanReasonLabel':HUMAN_REASONS.get(reason,reason) if reason else None,
   'latestText':display_text,'latestAt':display_at,'waitingSeconds':max(0,int(store.clock()-display_at)) if display_at else 0,
   'unread':bool(pending and pending['state'] in ('awaiting_classification','review_partial','template_ready','policy_review','facts_ready_for_review','needs_facts','human')),
   'action':action,'caseId':case['id'] if case else None})
 order={'human':0,'agent':1,'completed':2}
 rows.sort(key=lambda r:(order[r['state']],-r['waitingSeconds'],r['creatorId']))
 filtered=rows if view=='all' else [r for r in rows if r['state']==view]
 counts={key:sum(r['state']==key for r in rows) for key in ('human','agent','completed')};counts['all']=len(rows)
 return {'available':True,'view':view,'query':query,'counts':counts,'total':len(filtered),'offset':offset,'limit':limit,
  'nextOffset':offset+limit if offset+limit<len(filtered) else None,'items':filtered[offset:offset+limit],
  'platformWrites':0,'realSends':0}

def conversation_detail(root,store,cid,market='it'):
 if not isinstance(cid,str) or not cid.isdigit():raise CycleError('conversation_query_invalid')
 db=store.db;plan=db.execute("SELECT id FROM plan WHERE market=? AND institution='bjn-local-research'",(market,)).fetchone()
 if not plan:raise CycleError('plan_missing')
 plan=plan[0]
 turn=db.execute('SELECT * FROM inbound_turn WHERE plan_id=? AND cid=? ORDER BY coalesce(occurred_ms,observed_at*1000) DESC LIMIT 1',(plan,cid)).fetchone()
 if not turn:raise CycleError('conversation_missing')
 creator=turn['creator_id'];rel=db.execute('SELECT * FROM relationship WHERE plan_id=? AND creator_id=?',(plan,creator)).fetchone();handles=_handles(root,market)
 timeline=[]
 for row in db.execute('SELECT * FROM inbound_turn WHERE plan_id=? AND cid=? ORDER BY coalesce(occurred_ms,observed_at*1000),message_id',(plan,cid)):
  timeline.append({'id':row['turn_id'],'direction':'inbound','kind':row['format'],'text':row['text'],'occurredAt':(row['occurred_ms']/1000 if row['occurred_ms'] else row['observed_at']),'status':'received','source':'creator'})
 for row in db.execute("""SELECT message_id,occurred_ms,historical,observed_at FROM inbox_event
  WHERE plan_id=? AND oec=? AND kind='showcaseNotifications'
  ORDER BY coalesce(occurred_ms,observed_at*1000),message_id""",(plan,rel['oec'])):
  timeline.append({'id':'showcase-'+row['message_id'],'direction':'inbound','kind':'showcase','text':SHOWCASE_TEXT,
   'occurredAt':row['occurred_ms']/1000 if row['occurred_ms'] else row['observed_at'],
   'status':'historical' if row['historical'] else 'received','source':'showcase'})
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
 turn_at=(turn['occurred_ms']/1000 if turn['occurred_ms'] else turn['observed_at'])
 manual=_latest_manual_reply(db,plan,creator,turn_at)
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
 from lib.collaboration_status import current as collaboration_current
 collaboration=collaboration_current(store,creator,plan)
 return {'available':True,'conversationId':cid,'creator':{'creatorId':creator,'oec':rel['oec'],'handle':handles.get(creator),'mode':rel['mode'],'rejected':bool(rel['rejected']),'unlocked':bool(rel['unlocked']),'revision':rel['revision'],'collaboration':collaboration},
  'timeline':timeline,'episodes':episodes,'case':case_payload,
  'metrics':_creator_metrics(root,db,plan,creator,rel['oec']),
  'manualReply':({'id':manual['id'],'kind':manual['kind'],'confirmedAt':manual['confirmed_at']} if manual else None),
  'draft':{'text':draft['text'],'revision':draft['revision'],'updatedAt':draft['updated_at']} if draft else {'text':'','revision':0,'updatedAt':0},
  'manualTemplates':manual_templates(store,market=market),'platformWrites':0,'realSends':0}

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

def confirm_manual_reply(store,cid,case_id,turn_id,virtual,expected_control_revision,expected_pending_revision):
 if not isinstance(cid,str) or not cid.isdigit() or type(virtual) is not bool or \
    type(expected_control_revision) is not int or expected_control_revision<1 or \
    type(expected_pending_revision) is not int or expected_pending_revision<1:
  raise CycleError('manual_confirmation_invalid')
 plan=store.db.execute("SELECT id FROM plan WHERE market='it' AND institution='bjn-local-research'").fetchone()[0]
 turn=store.db.execute('SELECT * FROM inbound_turn WHERE plan_id=? AND cid=? ORDER BY coalesce(occurred_ms,observed_at*1000) DESC LIMIT 1',(plan,cid)).fetchone()
 if not turn:raise CycleError('conversation_missing')
 occurred=turn['occurred_ms']/1000 if turn['occurred_ms'] else turn['observed_at']
 manual=_latest_manual_reply(store.db,plan,turn['creator_id'],occurred)
 note='manual_reply_confirmed:'+manual['id'] if manual else 'human_case_confirmed_without_send'
 if virtual:
  if turn_id!=turn['turn_id']:raise CycleError('conversation_resolution_changed')
  result=complete_reviewed_human(store,cid,turn_id,expected_control_revision,expected_pending_revision,note)
 else:
  if not isinstance(case_id,str) or not re.fullmatch(r'case-[a-f0-9]{24}',case_id):raise CycleError('manual_confirmation_invalid')
  result=Service(store).resolve_case(plan,case_id,expected_pending_revision,expected_control_revision,note)
 return {**result,'manualReplyId':manual['id'] if manual else None,'platformWrites':0,'realSends':0}

def set_collaboration(store,cid,status,expected_status_revision,expected_control_revision,request_id):
 if not isinstance(cid,str) or not cid.isdigit():raise CycleError('collaboration_request_invalid')
 plan=store.db.execute("SELECT id FROM plan WHERE market='it' AND institution='bjn-local-research'").fetchone()[0]
 turn=store.db.execute('SELECT creator_id FROM inbound_turn WHERE plan_id=? AND cid=? ORDER BY coalesce(occurred_ms,observed_at*1000) DESC LIMIT 1',(plan,cid)).fetchone()
 if not turn:raise CycleError('conversation_missing')
 from lib.collaboration_status import set_manual
 return set_manual(store,turn['creator_id'],status,request_id,expected_status_revision,expected_control_revision)

def reject_creator(store,cid,expected_control_revision,request_id):
 if not isinstance(cid,str) or not cid.isdigit() or type(expected_control_revision) is not int or expected_control_revision<1 or \
    not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{7,119}',request_id):
  raise CycleError('creator_rejection_invalid')
 Service(store)
 plan=store.db.execute("SELECT id FROM plan WHERE market='it' AND institution='bjn-local-research'").fetchone()[0]
 with store.tx():
  turn=store.db.execute('SELECT * FROM inbound_turn WHERE plan_id=? AND cid=? ORDER BY coalesce(occurred_ms,observed_at*1000) DESC LIMIT 1',(plan,cid)).fetchone()
  if not turn:raise CycleError('conversation_missing')
  payload=encoded([turn['creator_id'],cid,'manual_reject'])
  old=store.db.execute('SELECT payload FROM control_event WHERE plan_id=? AND event_id=?',(plan,request_id)).fetchone()
  if old:
   if old[0]!=payload:raise CycleError('event_conflict')
   return {'state':'rejected','duplicate':True,'platformWrites':0,'realSends':0}
  rel=store.db.execute('SELECT * FROM relationship WHERE plan_id=? AND creator_id=?',(plan,turn['creator_id'])).fetchone()
  if not rel:raise CycleError('relationship_missing')
  if rel['rejected']:
   return {'state':'rejected','duplicate':True,'platformWrites':0,'realSends':0}
  if rel['revision']!=expected_control_revision:raise CycleError('revision_conflict')
  pending=store.db.execute('SELECT * FROM inbox_pending WHERE plan_id=? AND creator_id=?',(plan,turn['creator_id'])).fetchone()
  case=store.db.execute("SELECT * FROM service_case WHERE plan_id=? AND creator_id=? AND state='open'",(plan,turn['creator_id'])).fetchone()
  now=store.clock()
  if case:
   store.db.execute('INSERT OR IGNORE INTO service_resolution VALUES(?,?,?,?)',(case['id'],case['assessment_revision'],'creator_marked_rejected',now))
   store.db.execute("UPDATE service_case SET state='resolved',reason='manual_reject',updated=? WHERE id=?",(now,case['id']))
   case_id=case['id']
  else:
   case_id='case-'+digest([plan,turn['creator_id'],turn['turn_id'],'manual-reject'])[:24]
   revision=pending['revision'] if pending else 1
   store.db.execute("INSERT OR IGNORE INTO service_case VALUES(?,?,?,'resolved',?,'manual_reject',?,?, 'not_sent')",(case_id,plan,turn['creator_id'],revision,now,now))
   store.db.execute('INSERT OR IGNORE INTO service_resolution VALUES(?,?,?,?)',(case_id,revision,'creator_marked_rejected',now))
  store.db.execute('INSERT OR IGNORE INTO service_case_turn VALUES(?,?)',(case_id,turn['turn_id']))
  if store.db.execute("SELECT 1 FROM sqlite_master WHERE name='inbox_event'").fetchone():
   watermark=store.db.execute("SELECT coalesce(max(rowid),0) FROM inbox_event WHERE plan_id=? AND oec=? AND kind='creatorReplies'",(plan,rel['oec'])).fetchone()[0]
   store.db.execute('INSERT INTO service_cursor VALUES(?,?,?) ON CONFLICT(plan_id,creator_id) DO UPDATE SET event_rowid=max(event_rowid,excluded.event_rowid)',(plan,turn['creator_id'],watermark))
  if pending:store.db.execute("UPDATE inbox_pending SET state='suppressed_no_reply' WHERE plan_id=? AND creator_id=?",(plan,turn['creator_id']))
  store.db.execute("UPDATE relationship SET mode='auto',rejected=1,inbox_until=0,revision=revision+1 WHERE plan_id=? AND creator_id=?",(plan,turn['creator_id']))
  store.db.execute('INSERT INTO control_event VALUES(?,?,?)',(plan,request_id,payload))
 return {'state':'rejected','duplicate':False,'caseId':case_id,'revision':expected_control_revision+1,'platformWrites':0,'realSends':0}

def workspace_status(root,store,market='it'):
 plan=store.db.execute("SELECT id FROM plan WHERE market=? AND institution='bjn-local-research'",(market,)).fetchone()
 if not plan:raise CycleError('plan_missing')
 plan=plan[0]
 latest=store.db.execute('SELECT * FROM agent_reply_run WHERE plan_id=? ORDER BY started_at DESC LIMIT 1',(plan,)).fetchone()
 return {'agentSetting':agent_setting(store,plan),'latestAgentRun':dict(latest) if latest else None,
  'manualTemplates':manual_templates(store,market=market),'platformWrites':0,'realSends':0}
