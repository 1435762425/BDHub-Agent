"""Market-scoped read model and local controls for the conversation workbench."""
import json,re,sqlite3,time
from datetime import datetime
from zoneinfo import ZoneInfo
from contextlib import closing
from pathlib import Path
from lib.second_cycle import CycleError,digest,encoded
from lib.template_library import agent_setting,manual_templates
from lib.cycle_service import Service

HUMAN_REASONS={'human':'需要人工判断','card_result_unknown':'卡片结果未知，已隔离，不再自动发送',
 'conversation_create_unknown':'建会话结果未知，已隔离，不再自动发送','paid_or_budget':'付费或预算','fixed_fee_negotiation_requires_human':'付费合作条件',
 'product_card_broken':'商品卡打不开','card_commission_mismatch':'商品卡佣金不一致','stop_contact':'要求停止联系',
 'catalog_request':'更多商品目录','whatsapp':'WhatsApp',
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
# Pending states that still wait for us.  The list's unread flag and the ops alert backlog share this rule.
UNREAD_PENDING=('awaiting_content','awaiting_classification','review_partial','template_ready','policy_review',
 'facts_ready_for_review','needs_facts','human')

def _human_label(db,plan,creator,reason):
 if db.execute("SELECT 1 FROM sqlite_master WHERE name='agent_reply_decision_v2'").fetchone():
  row=db.execute("SELECT output_json FROM agent_reply_decision_v2 WHERE plan_id=? AND creator_id=? AND mode='production' AND state='ready' ORDER BY created_at DESC LIMIT 1",(plan,creator)).fetchone()
  if row:
   result=json.loads(row[0])
   if result.get('route')=='handoff' and isinstance(result.get('handoffReason'),str):
    return result['handoffReason'][:120]
 return HUMAN_REASONS.get(reason,reason) if reason else None

def _plan_id(store,market):
 row=store.db.execute("SELECT id FROM plan WHERE market=? AND institution='bjn-local-research'",(market,)).fetchone()
 if not row:raise CycleError('plan_missing')
 return row[0]

def _latest_turn(db,plan,creator):
 return db.execute('SELECT * FROM inbound_turn WHERE plan_id=? AND creator_id=? ORDER BY coalesce(occurred_ms,observed_at*1000) DESC,message_id DESC LIMIT 1',(plan,creator)).fetchone()

def _latest_external(db,plan,rel,latest):
 """Conversation id and the newest agency-backend outbound message for this creator."""
 from lib.observed_messages import outbound_messages
 cid=latest['cid'] if latest else None
 if cid is None:
  observed=db.execute("SELECT cid FROM inbox_event WHERE plan_id=? AND oec=? AND kind='ourMessages' "
                       "ORDER BY coalesce(occurred_ms,observed_at*1000) DESC LIMIT 1",(plan,rel['oec'])).fetchone()
  cid=observed[0] if observed else None
 external=outbound_messages(db,plan,cid,rel['oec'],limit=1) if cid else []
 return cid,(external[-1] if external else None)

def _turn_at(latest):
 return latest['occurred_ms']/1000 if latest and latest['occurred_ms'] else latest['observed_at'] if latest else 0

def unread_backlog(store,market='it'):
 """Count and oldest creator-message time of the conversations the list marks unread, without building the list."""
 db=store.db;plan=_plan_id(store,market);count,oldest=0,None
 marks=','.join('?'*len(UNREAD_PENDING))
 for rel in db.execute("SELECT r.* FROM relationship r JOIN inbox_pending p ON p.plan_id=r.plan_id AND p.creator_id=r.creator_id "
                       f"WHERE r.plan_id=? AND p.state IN ({marks})",(plan,*UNREAD_PENDING)):
  latest=_latest_turn(db,plan,rel['creator_id']);_,external=_latest_external(db,plan,rel,latest);inbound_at=_turn_at(latest)
  if external and external['occurredAt']>inbound_at:continue
  count+=1
  if inbound_at and (oldest is None or inbound_at<oldest):oldest=inbound_at
 return {'unread':count,'oldestAt':oldest}

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

def _creator_metrics(root,db,plan,creator,oec,market):
 result={'gmv':None,'videoGmv':None,'liveGmv':None,'followers':None,'unitsSold':None,
  'avgVideoViews':None,'observedAt':None,'replyCount':0,'showcaseCount':0}
 path=Path(root)/'var/creator-identities.sqlite'
 if path.exists():
  with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as identities:
   row=identities.execute("""SELECT payload_json,observed_at FROM identity_observation
    WHERE market=? AND creator_id=? AND kind='profile' AND json_type(payload_json,'$.fields')='object'
    ORDER BY observed_us DESC,length(payload_json) DESC,event_id LIMIT 1""",(market,creator)).fetchone() if identities.execute("SELECT 1 FROM sqlite_master WHERE name='identity_observation'").fetchone() else None
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
 if view not in ('human','agent','waiting','completed','all') or type(limit) is not int or not 1<=limit<=100 or type(offset) is not int or offset<0 or offset>5000:raise CycleError('conversation_query_invalid')
 handles=_handles(root,market);db=store.db;plan=db.execute("SELECT id FROM plan WHERE market=? AND institution='bjn-local-research'",(market,)).fetchone()
 if not plan:raise CycleError('plan_missing')
 plan=plan[0]
 setting=agent_setting(store,plan);clock=datetime.fromtimestamp(store.clock(),ZoneInfo('Asia/Shanghai')).strftime('%H:%M')
 try:
  status_file='agent-reply-status.json' if market=='it' else f'agent-reply-status-{market}.json'
  runtime=json.loads((Path(root)/'var'/status_file).read_text(encoding='utf-8'))
  agent_failed=runtime.get('state')=='failed'
 except (OSError,ValueError,TypeError):agent_failed=False
 rows=[]
 for rel in db.execute('SELECT * FROM relationship WHERE plan_id=?',(plan,)):
  latest=_latest_turn(db,plan,rel['creator_id']);cid,external=_latest_external(db,plan,rel,latest);inbound_at=_turn_at(latest)
  externally_answered=bool(external and external['occurredAt']>inbound_at)
  case=db.execute("SELECT * FROM service_case WHERE plan_id=? AND creator_id=? AND state='open' ORDER BY updated DESC LIMIT 1",(plan,rel['creator_id'])).fetchone()
  pending=db.execute('SELECT * FROM inbox_pending WHERE plan_id=? AND creator_id=?',(plan,rel['creator_id'])).fetchone()
  reply=db.execute('SELECT state,kind,started,text FROM service_reply WHERE plan_id=? AND creator_id=? ORDER BY created DESC LIMIT 1',(plan,rel['creator_id'])).fetchone() if db.execute("SELECT 1 FROM sqlite_master WHERE name='service_reply'").fetchone() else None
  showcase=_latest_showcase(db,plan,rel['oec'])
  decision=_latest_decision(db,latest['turn_id']) if latest else None;action=decision.get('action') if decision else None
  v2=db.execute("SELECT output_json FROM agent_reply_decision_v2 WHERE plan_id=? AND turn_id=? AND mode='production' AND state='ready' ORDER BY created_at DESC LIMIT 1",(plan,latest['turn_id'])).fetchone() if latest and db.execute("SELECT 1 FROM sqlite_master WHERE name='agent_reply_decision_v2'").fetchone() else None
  failed_model=False
  if latest and not v2 and db.execute("SELECT 1 FROM sqlite_master WHERE name='agent_reply_decision_v2'").fetchone():
   failed_model=db.execute("SELECT count(*) FROM agent_reply_decision_v2 WHERE plan_id=? AND turn_id=? "
                           "AND mode='production' AND state='unknown'",(plan,latest['turn_id'])).fetchone()[0]>=3
  meaning=(json.loads(v2[0]).get('meaningZh') if v2 else decision.get('meaningZh') if decision else None)
  if case or rel['mode']=='human' or (action=='human' and pending and pending['state']!='resolved_by_human') or (pending and pending['state']=='human'):state='human'
  elif externally_answered:state='waiting'
  elif pending and pending['state'] in ('waiting_contact','waiting_clarification'):state='waiting'
  elif pending and pending['state'] in ('awaiting_content','awaiting_classification','review_partial','template_ready','policy_review','facts_ready_for_review','needs_facts'):state='agent'
  elif reply and reply['state']=='confirmed' or pending and pending['state'] in ('answered','no_reply','resolved_by_human'):state='completed'
  elif latest:state='completed'
  else:continue
  handle=handles.get(rel['creator_id'])
  if query and query.casefold() not in f"{handle or ''} {rel['oec']} {(latest['text'] if latest else '')} {(external['text'] if external else '')}".casefold():continue
  reason=(case['reason'] if case else _human_reason(decision) if action=='human' else None)
  status_label=('待人工处理' if state=='human' else
                '机构后台已回复，等待达人' if state=='waiting' and externally_answered else
                '等待达人提供联系方式' if state=='waiting' and pending and pending['state']=='waiting_contact' else
                '等待达人说明' if state=='waiting' else
                'AI 已关闭' if state=='agent' and not setting['enabled'] else
                'AI 模型连续失败，需检查' if state=='agent' and failed_model else
                'AI 运行异常' if state=='agent' and agent_failed else
                '等待回复窗口' if state=='agent' and not(setting['replyStart']<=clock<setting['replyEnd']) else
                '等待 AI 处理' if state=='agent' else '本轮已结束')
  occurred=(latest['occurred_ms']/1000 if latest and latest['occurred_ms'] else latest['observed_at'] if latest else 0)
  showcase_at=(showcase['occurred_ms']/1000 if showcase and showcase['occurred_ms'] else showcase['observed_at'] if showcase else 0)
  display_at=max(occurred,showcase_at);display_text=SHOWCASE_TEXT if showcase_at>occurred else latest['text'] if latest else None
  if external and external['occurredAt']>display_at:display_at=external['occurredAt'];display_text=external['text']
  rows.append({'conversationId':cid,'creatorId':rel['creator_id'],'oec':rel['oec'],'handle':handle,
   'state':state,'queueStatusLabel':status_label,'humanReason':reason,
   'humanReasonLabel':_human_label(db,plan,rel['creator_id'],reason),
   'latestText':display_text,'latestMeaningZh':meaning if latest and display_text==latest['text'] else None,
   'latestAt':display_at,'waitingSeconds':max(0,int(store.clock()-display_at)) if display_at and state in ('human','agent') else 0,
   'unread':bool(not externally_answered and pending and pending['state'] in UNREAD_PENDING),
   'action':action,'caseId':case['id'] if case else None})
 order={'human':0,'agent':1,'waiting':2,'completed':3}
 rows.sort(key=lambda r:(order[r['state']],-r['waitingSeconds'],r['creatorId']))
 filtered=rows if view=='all' else [r for r in rows if r['state']==view]
 counts={key:sum(r['state']==key for r in rows) for key in ('human','agent','waiting','completed')};counts['all']=len(rows)
 return {'available':True,'view':view,'query':query,'counts':counts,'total':len(filtered),'offset':offset,'limit':limit,
  'nextOffset':offset+limit if offset+limit<len(filtered) else None,'items':filtered[offset:offset+limit],
  'platformWrites':0,'realSends':0}

def conversation_detail(root,store,cid,market='it'):
 if not isinstance(cid,str) or not cid.isdigit():raise CycleError('conversation_query_invalid')
 db=store.db;plan=db.execute("SELECT id FROM plan WHERE market=? AND institution='bjn-local-research'",(market,)).fetchone()
 if not plan:raise CycleError('plan_missing')
 plan=plan[0]
 turn=db.execute('SELECT * FROM inbound_turn WHERE plan_id=? AND cid=? ORDER BY coalesce(occurred_ms,observed_at*1000) DESC LIMIT 1',(plan,cid)).fetchone()
 if turn:creator=turn['creator_id']
 else:
  observed=db.execute("SELECT r.creator_id FROM inbox_event e JOIN relationship r ON r.plan_id=e.plan_id AND r.oec=e.oec "
                      "WHERE e.plan_id=? AND e.cid=? AND e.kind='ourMessages' LIMIT 1",(plan,cid)).fetchone()
  if not observed:raise CycleError('conversation_missing')
  creator=observed[0]
 rel=db.execute('SELECT * FROM relationship WHERE plan_id=? AND creator_id=?',(plan,creator)).fetchone();handles=_handles(root,market)
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
  parts={part['kind']:part for part in db.execute('SELECT kind,state,started FROM cycle_delivery_part '
                                                 'WHERE delivery_id=?',(row['delivery_id'],))}
  card=parts.get('card');sent_text=parts.get('text')
  if card and card['state']=='confirmed':
   timeline.append({'id':row['episode_id']+':card','direction':'outbound','kind':'product_card',
                    'text':f"[商品卡 PID {row['pid']}]",'occurredAt':card['started'] or row['sent_at'],
                    'status':'confirmed','source':'batch','pid':row['pid'],'listId':row['list_id']})
  if sent_text and sent_text['state']=='confirmed':
   timeline.append({'id':row['episode_id']+':text','direction':'outbound','kind':'text',
                    'text':message.get('textIt') or message.get('text') or message.get('body'),
                    'occurredAt':sent_text['started'] or row['sent_at'],
                    'status':'confirmed','source':'batch','pid':row['pid'],'listId':row['list_id']})
 if db.execute("SELECT 1 FROM sqlite_master WHERE name='service_reply'").fetchone():
  for row in db.execute('SELECT * FROM service_reply WHERE plan_id=? AND creator_id=? ORDER BY created',(plan,creator)):
   card=json.loads(row['text']) if row['kind']=='manual_card' else None
   timeline.append({'id':row['id'],'direction':'outbound','kind':'product_card' if card else 'text','text':f"[商品卡 PID {card['pid']}]" if card else row['text'],'occurredAt':row['started'] or row['created'],'status':row['state'],'source':'human' if row['kind'] in ('manual','manual_card') else 'agent',**({'pid':card['pid'],'listId':card['listId']} if card else {})})
 from lib.observed_messages import outbound_messages
 timeline.extend(outbound_messages(db,plan,cid,rel['oec']))
 timeline.sort(key=lambda r:(r['occurredAt'],r['id']))
 episodes=[{'episodeId':r['episode_id'],'pid':r['pid'],'listId':r['list_id'],'sentAt':r['sent_at']} for r in db.execute('SELECT * FROM outbound_episode WHERE plan_id=? AND creator_id=? ORDER BY sent_at DESC LIMIT 10',(plan,creator))]
 case=db.execute("SELECT * FROM service_case WHERE plan_id=? AND creator_id=? AND state='open' ORDER BY updated DESC LIMIT 1",(plan,creator)).fetchone()
 pending=db.execute('SELECT * FROM inbox_pending WHERE plan_id=? AND creator_id=?',(plan,creator)).fetchone()
 decision=_latest_decision(db,turn['turn_id']) if turn else None;reason=_human_reason(decision)
 turn_at=(turn['occurred_ms']/1000 if turn['occurred_ms'] else turn['observed_at']) if turn else 0
 manual=_latest_manual_reply(db,plan,creator,turn_at)
 pending_manual=[]
 if db.execute("SELECT 1 FROM sqlite_master WHERE name='service_reply'").fetchone():
  pending_manual=[{'id':row['id'],'kind':row['kind'],'requestId':row['request_ref'],'state':row['state']}
                  for row in db.execute("SELECT id,kind,request_ref,state FROM service_reply "
                                        "WHERE plan_id=? AND creator_id=? AND cid=? "
                                        "AND kind IN ('manual','manual_card') "
                                        "AND state IN ('ready','inflight','accepted','unknown') "
                                        "ORDER BY created DESC LIMIT 100",(plan,creator,cid))]
 draft=db.execute('SELECT text,revision,updated_at FROM conversation_draft WHERE plan_id=? AND cid=?',(plan,cid)).fetchone()
 if case:
  case_payload={'id':case['id'],'reason':case['reason'],'reasonLabel':_human_label(db,plan,creator,case['reason']),
   'createdAt':case['created'],'revision':case['assessment_revision'],'virtual':False,'turnId':None,
   'pendingRevision':pending['revision'] if pending else case['assessment_revision']}
 elif decision and decision.get('action')=='human' and pending and pending['state']!='resolved_by_human':
  case_payload={'id':'review-'+turn['turn_id'][5:],'reason':reason,'reasonLabel':HUMAN_REASONS.get(reason,reason),
   'createdAt':(turn['occurred_ms']/1000 if turn['occurred_ms'] else turn['observed_at']),
   'revision':pending['revision'],'virtual':True,'turnId':turn['turn_id'],'pendingRevision':pending['revision']}
 else:case_payload=None
 from lib.collaboration_status import current as collaboration_current
 collaboration=collaboration_current(store,creator,plan)
 agent_decisions=[]
 if db.execute("SELECT 1 FROM sqlite_master WHERE name='agent_reply_decision_v2'").fetchone():
  for row in db.execute("SELECT decision_id,guide_revision,output_json,state,service_reply_id,created_at FROM agent_reply_decision_v2 WHERE plan_id=? AND creator_id=? AND mode='production' ORDER BY created_at DESC LIMIT 12",(plan,creator)):
   result=json.loads(row['output_json']) if row['state']=='ready' and row['output_json'] else None
   agent_decisions.append({'decisionId':row['decision_id'],'guideRevision':row['guide_revision'],
                           'route':result['route'] if result else None,
                           'reasonZh':result['reasonSummaryZh'] if result else None,
                           'state':row['state'],'serviceReplyId':row['service_reply_id'],
                           'createdAt':row['created_at']})
 return {'available':True,'conversationId':cid,'latestTurnId':turn['turn_id'] if turn else None,'creator':{'creatorId':creator,'oec':rel['oec'],'handle':handles.get(creator),'mode':rel['mode'],'rejected':bool(rel['rejected']),'unlocked':bool(rel['unlocked']),'revision':rel['revision'],'collaboration':collaboration},
  'timeline':timeline,'episodes':episodes,'case':case_payload,'agentDecisions':agent_decisions,
  'metrics':_creator_metrics(root,db,plan,creator,rel['oec'],market),
  'manualReply':({'id':manual['id'],'kind':manual['kind'],'confirmedAt':manual['confirmed_at']} if manual else None),
  'pendingManualReplies':pending_manual,
  'draft':{'text':draft['text'],'revision':draft['revision'],'updatedAt':draft['updated_at']} if draft else {'text':'','revision':0,'updatedAt':0},
  'manualTemplates':manual_templates(store,market=market),'platformWrites':0,'realSends':0}

def reconcile_manual_reply(store,cid,request_id,market='it',*,verify=None):
 """Read back a previously submitted manual intent without creating a new one."""
 if market!='it' or not isinstance(cid,str) or not cid.isdigit() or \
    not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{7,119}',request_id):
  raise CycleError('manual_reconcile_scope_invalid')
 plan=_plan_id(store,market)
 row=store.db.execute("SELECT * FROM service_reply WHERE plan_id=? AND cid=? AND request_ref=? "
                      "AND kind IN ('manual','manual_card')",(plan,cid,request_id)).fetchone()
 if not row:raise CycleError('manual_reconcile_intent_missing')
 from lib.cycle_auto_reply import AutoReplies
 replies=AutoReplies(store);reply=dict(row)
 if reply['state'] in ('inflight','accepted','unknown'):
  if verify is None:
   from lib.reply_transport import run_reply
   verify=lambda store,replies,reply:run_reply(store,replies,reply)
  verify(store,replies,reply)
 current=replies.get(reply['id'])
 return {'state':current['state'],'replyId':reply['id'],'requestRef':request_id,
         'platformWrites':0,'realSends':0}

def save_draft(store,cid,text,expected_revision,market='it'):
 if not isinstance(cid,str) or not cid.isdigit() or not isinstance(text,str) or len(text)>4000 or type(expected_revision) is not int or expected_revision<0:raise CycleError('conversation_draft_invalid')
 plan=_plan_id(store,market)
 with store.tx():
  row=store.db.execute('SELECT revision FROM conversation_draft WHERE plan_id=? AND cid=?',(plan,cid)).fetchone();current=row[0] if row else 0
  if current!=expected_revision:raise CycleError('conversation_draft_conflict')
  revision=current+1
  store.db.execute('INSERT INTO conversation_draft VALUES(?,?,?,?,?) ON CONFLICT(plan_id,cid) DO UPDATE SET text=excluded.text,revision=excluded.revision,updated_at=excluded.updated_at',(plan,cid,text,revision,store.clock()))
 return {'conversationId':cid,'text':text,'revision':revision,'updatedAt':store.clock(),'platformWrites':0,'realSends':0}

def complete_reviewed_human(store,cid,turn_id,expected_control_revision,expected_pending_revision,note,market='it'):
 if not isinstance(cid,str) or not cid.isdigit() or not isinstance(turn_id,str) or not turn_id.startswith('turn-') or \
    type(expected_control_revision) is not int or expected_control_revision<1 or \
    type(expected_pending_revision) is not int or expected_pending_revision<1 or \
    not isinstance(note,str) or not note.strip() or len(note)>4000:
  raise CycleError('conversation_resolution_invalid')
 plan=_plan_id(store,market)
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

def confirm_manual_reply(store,cid,case_id,turn_id,virtual,expected_control_revision,expected_pending_revision,market='it'):
 if not isinstance(cid,str) or not cid.isdigit() or type(virtual) is not bool or \
    type(expected_control_revision) is not int or expected_control_revision<1 or \
    type(expected_pending_revision) is not int or expected_pending_revision<1:
  raise CycleError('manual_confirmation_invalid')
 plan=_plan_id(store,market)
 turn=store.db.execute('SELECT * FROM inbound_turn WHERE plan_id=? AND cid=? ORDER BY coalesce(occurred_ms,observed_at*1000) DESC LIMIT 1',(plan,cid)).fetchone()
 if not turn:raise CycleError('conversation_missing')
 occurred=turn['occurred_ms']/1000 if turn['occurred_ms'] else turn['observed_at']
 manual=_latest_manual_reply(store.db,plan,turn['creator_id'],occurred)
 note='manual_reply_confirmed:'+manual['id'] if manual else 'human_case_confirmed_without_send'
 if virtual:
  if turn_id!=turn['turn_id']:raise CycleError('conversation_resolution_changed')
  result=complete_reviewed_human(store,cid,turn_id,expected_control_revision,expected_pending_revision,note,market)
 else:
  if not isinstance(case_id,str) or not re.fullmatch(r'case-[a-f0-9]{24}',case_id):raise CycleError('manual_confirmation_invalid')
  result=Service(store).resolve_case(plan,case_id,expected_pending_revision,expected_control_revision,note)
 return {**result,'manualReplyId':manual['id'] if manual else None,'platformWrites':0,'realSends':0}

def resolve_manual(store,cid,case_id,latest_turn_id,outcome,expected_control_revision,
                   expected_pending_revision,expected_status_revision,request_id,market='it'):
 """One explicit operator action closes the case and decides future outreach eligibility."""
 if not isinstance(cid,str) or not cid.isdigit() or outcome not in ('normal','paid','rejected') or \
    not isinstance(case_id,str) or not re.fullmatch(r'(?:case|review)-[a-f0-9]{24}',case_id) or \
    not isinstance(latest_turn_id,str) or not re.fullmatch(r'turn-[a-f0-9]{24}',latest_turn_id) or \
    not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{7,119}',request_id) or \
    any(type(value) is not int or value<0 for value in (expected_control_revision,
       expected_pending_revision,expected_status_revision)):
  raise CycleError('manual_resolution_invalid')
 plan=_plan_id(store,market);payload=encoded(['manual_resolution_v2',cid,case_id,latest_turn_id,outcome])
 from lib.collaboration_status import current as collaboration_current,_insert as insert_collaboration
 with store.tx():
  prior=store.db.execute('SELECT payload FROM control_event WHERE plan_id=? AND event_id=?',(plan,request_id)).fetchone()
  if prior:
   if prior[0]!=payload:raise CycleError('manual_resolution_conflict')
   return {'state':'resolved','duplicate':True,'outcome':outcome,'platformWrites':0,'realSends':0}
  turn=store.db.execute('SELECT * FROM inbound_turn WHERE plan_id=? AND cid=? '
   'ORDER BY coalesce(occurred_ms,observed_at*1000) DESC,message_id DESC LIMIT 1',(plan,cid)).fetchone()
  if not turn or turn['turn_id']!=latest_turn_id:raise CycleError('manual_resolution_changed')
  rel=store.db.execute('SELECT * FROM relationship WHERE plan_id=? AND creator_id=?',(plan,turn['creator_id'])).fetchone()
  pending=store.db.execute('SELECT * FROM inbox_pending WHERE plan_id=? AND creator_id=?',(plan,turn['creator_id'])).fetchone()
  if not rel or rel['mode']!='human' or rel['revision']!=expected_control_revision or \
     not pending or pending['revision']!=expected_pending_revision:
   raise CycleError('manual_resolution_changed')
  status=collaboration_current(store,turn['creator_id'],plan)
  if status['revision']!=expected_status_revision or outcome=='normal' and \
     (status['status'] in ('paid','rejected') or rel['rejected']):
   raise CycleError('manual_resolution_status_changed')
  case=store.db.execute('SELECT * FROM service_case WHERE id=? AND plan_id=? AND creator_id=? AND state=\'open\'',
                        (case_id,plan,turn['creator_id'])).fetchone()
  if not case:
   decision=_latest_decision(store.db,turn['turn_id'])
   if case_id!='review-'+turn['turn_id'][5:] or not decision or decision.get('action')!='human' or \
      store.db.execute("SELECT 1 FROM service_case WHERE plan_id=? AND creator_id=? AND state='open'",(plan,turn['creator_id'])).fetchone():
    raise CycleError('manual_resolution_changed')
   case_id='case-'+digest([plan,turn['creator_id'],turn['turn_id'],'manual-resolution-v2'])[:24]
   now=store.clock()
   store.db.execute("INSERT INTO service_case VALUES(?,?,?,'open',?,?,?,?, 'not_sent')",
                    (case_id,plan,turn['creator_id'],pending['revision'],'manual_completion',now,now))
   store.db.execute('INSERT OR IGNORE INTO service_case_turn VALUES(?,?)',(case_id,turn['turn_id']))
  elif case['assessment_revision']!=pending['revision']:
   store.db.execute('UPDATE service_case SET assessment_revision=?,updated=? WHERE id=? AND state=\'open\'',
                    (pending['revision'],store.clock(),case_id))
   store.db.execute('INSERT OR IGNORE INTO service_case_turn VALUES(?,?)',(case_id,turn['turn_id']))
  now=store.clock();note='manual_resolution_v2:'+outcome
  store.db.execute('INSERT INTO service_resolution VALUES(?,?,?,?)',(case_id,pending['revision'],note,now))
  store.db.execute("UPDATE service_case SET state='resolved',updated=? WHERE id=?",(now,case_id))
  store.db.execute("UPDATE inbox_pending SET state='resolved_by_human' WHERE plan_id=? AND creator_id=? AND revision=?",
                   (plan,turn['creator_id'],pending['revision']))
  watermark=store.db.execute("SELECT coalesce(max(rowid),0) FROM inbox_event WHERE plan_id=? AND oec=? AND kind='creatorReplies'",
                             (plan,rel['oec'])).fetchone()[0]
  store.db.execute('INSERT INTO service_cursor VALUES(?,?,?) ON CONFLICT(plan_id,creator_id) '
                   'DO UPDATE SET event_rowid=max(event_rowid,excluded.event_rowid)',
                   (plan,turn['creator_id'],watermark))
  if outcome in ('paid','rejected'):
   insert_collaboration(store,plan,turn['creator_id'],'manual',status['status'],outcome,
                        {'reason':'manual_resolution_v2','caseId':case_id},request_id=None,
                        revision=status['revision']+1)
  store.db.execute('UPDATE relationship SET mode=\'auto\',rejected=?,inbox_until=0,revision=revision+1 '
                   'WHERE plan_id=? AND creator_id=? AND revision=?',
                   (int(outcome=='rejected' or bool(rel['rejected'])),plan,turn['creator_id'],rel['revision']))
  store.db.execute('INSERT INTO control_event VALUES(?,?,?)',(plan,request_id,payload))
 return {'state':'resolved','duplicate':False,'outcome':outcome,'caseId':case_id,
         'relationshipRevision':expected_control_revision+1,'platformWrites':0,'realSends':0}

def set_collaboration(store,cid,status,expected_status_revision,expected_control_revision,request_id,market='it'):
 if not isinstance(cid,str) or not cid.isdigit():raise CycleError('collaboration_request_invalid')
 plan=_plan_id(store,market)
 turn=store.db.execute('SELECT creator_id FROM inbound_turn WHERE plan_id=? AND cid=? ORDER BY coalesce(occurred_ms,observed_at*1000) DESC LIMIT 1',(plan,cid)).fetchone()
 if not turn:raise CycleError('conversation_missing')
 from lib.collaboration_status import set_manual
 return set_manual(store,turn['creator_id'],status,request_id,expected_status_revision,expected_control_revision,plan_id=plan)

def reject_creator(store,cid,expected_control_revision,request_id,market='it'):
 if not isinstance(cid,str) or not cid.isdigit() or type(expected_control_revision) is not int or expected_control_revision<1 or \
    not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{7,119}',request_id):
  raise CycleError('creator_rejection_invalid')
 Service(store)
 plan=_plan_id(store,market)
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
