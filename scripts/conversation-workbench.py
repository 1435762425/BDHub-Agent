#!/usr/bin/env python3
"""Conversation workbench read model and local draft controls."""
import argparse,importlib.util,json,re,sqlite3,sys
from contextlib import closing
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT/'scripts'))
from lib.conversation_workbench import complete_reviewed_human,conversation_detail,list_conversations,save_draft,workspace_status
from lib.cycle_auto_reply import AutoReplies
from lib.second_cycle import CycleError,CycleStore,digest

def main():
 p=argparse.ArgumentParser();p.add_argument('action',choices=('list','detail','status','save-draft','complete-human','send-text','send-card','translate'));p.add_argument('--view',default='human');p.add_argument('--query',default='');p.add_argument('--limit',type=int,default=30);p.add_argument('--offset',type=int,default=0);p.add_argument('--cid');a=p.parse_args()
 try:
  readonly=a.action not in ('save-draft','complete-human','send-text','send-card')
  with CycleStore(ROOT/'var/second-cycle.sqlite',readonly=readonly) as store:
   if a.action=='list':result=list_conversations(ROOT,store,a.view,a.query,a.limit,a.offset)
   elif a.action=='detail':result=conversation_detail(ROOT,store,a.cid)
   elif a.action=='status':result=workspace_status(ROOT,store)
   elif a.action=='save-draft':
    raw=sys.stdin.read(10001)
    if len(raw.encode())>10000:raise CycleError('input_too_large')
    req=json.loads(raw);result=save_draft(store,a.cid,req.get('text'),req.get('expectedRevision'))
   elif a.action=='complete-human':
    raw=sys.stdin.read(10001)
    if len(raw.encode())>10000:raise CycleError('input_too_large')
    req=json.loads(raw);result=complete_reviewed_human(store,a.cid,req.get('turnId'),req.get('expectedControlRevision'),req.get('expectedPendingRevision'),req.get('note'))
   elif a.action in ('send-text','send-card'):
    raw=sys.stdin.read(10001)
    if len(raw.encode())>10000:raise CycleError('input_too_large')
    req=json.loads(raw);turn=store.db.execute('SELECT plan_id,creator_id FROM inbound_turn WHERE cid=? ORDER BY coalesce(occurred_ms,observed_at*1000) DESC LIMIT 1',(a.cid,)).fetchone()
    if not turn:raise CycleError('conversation_missing')
    replies=AutoReplies(store)
    if a.action=='send-text':q=replies.prepare_manual(turn['plan_id'],turn['creator_id'],a.cid,req.get('text'),req.get('expectedControlRevision'),req.get('requestId'))
    else:
     request_id=req.get('requestId')
     if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{7,119}',request_id):raise CycleError('manual_reply_invalid')
     episode=store.db.execute('SELECT * FROM outbound_episode WHERE episode_id=? AND plan_id=? AND creator_id=?',(req.get('episodeId'),turn['plan_id'],turn['creator_id'])).fetchone()
     if not episode:raise CycleError('manual_card_missing')
     prior=replies.get('manual-card-'+digest([turn['plan_id'],turn['creator_id'],request_id])[:24])
     if prior:
      saved=json.loads(prior['text'])
      if prior['kind']!='manual_card' or prior['cid']!=a.cid or str(saved.get('pid'))!=str(episode['pid']) or str(saved.get('listId'))!=str(episode['list_id']):raise CycleError('manual_reply_request_conflict')
      q=prior
     else:
      with closing(sqlite3.connect((ROOT/'var/catalog-links.sqlite').resolve().as_uri()+'?mode=ro',uri=True)) as links:
       row=links.execute("SELECT card_payload FROM catalog_current_binding WHERE market='it' AND pid=? AND list_id=? AND state='active'",(episode['pid'],episode['list_id'])).fetchone()
      if not row:raise CycleError('manual_card_stale')
      q=replies.prepare_manual_card(turn['plan_id'],turn['creator_id'],a.cid,json.loads(row[0]),req.get('expectedControlRevision'),request_id)
    spec=importlib.util.spec_from_file_location('manual_reply_runtime',ROOT/'scripts/run-auto-replies.py');runtime=importlib.util.module_from_spec(spec);spec.loader.exec_module(runtime)
    if q['state']=='confirmed':state='confirmed';new_dispatch=False
    else:new_dispatch=q['state']=='ready';state=runtime.run_reply(store,replies,q)
    result={'state':state,'replyId':q['id'],'requestRef':q['request_ref'],'platformWrites':int(new_dispatch),'realSends':int(new_dispatch)}
   else:
    raw=sys.stdin.read(10001);req=json.loads(raw);text=req.get('text');target=req.get('target')
    if not isinstance(text,str) or not text.strip() or len(text)>4000 or target not in ('it','zh'):raise CycleError('translation_invalid')
    from lib.draft_provider import call_model
    language='自然、礼貌、简洁的意大利语' if target=='it' else '自然的简体中文'
    response=call_model([{'role':'system','content':f'你是翻译。把用户消息翻成{language}，保留数字、链接、emoji和专有名词，不添加原文没有的信息。只返回JSON：{{"translation":"..."}}'},{'role':'user','content':text}],max_output_tokens=1000)
    translated=json.loads(response['content']).get('translation')
    if not isinstance(translated,str) or not translated.strip():raise CycleError('translation_invalid')
    result={'translation':translated.strip(),'target':target,'platformWrites':0,'realSends':0}
  print(json.dumps(result,ensure_ascii=False));return 0
 except Exception as error:
  print(json.dumps({'error':str(error) if isinstance(error,CycleError) else 'conversation_workbench_unavailable'},ensure_ascii=False));return 2
if __name__=='__main__':raise SystemExit(main())
