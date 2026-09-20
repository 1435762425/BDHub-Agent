#!/usr/bin/env python3
"""Manage three separate template domains and Agent reply settings."""
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT/'scripts'))
from lib.second_cycle import CycleError,CycleStore
from lib.template_library import (agent_setting,archive_send_template,create_send_template,manual_templates,
 send_templates,save_agent_setting,update_send_template,upsert_manual_template)
from lib.reply_events import load_policy

def snapshot(store):
 plan=store.db.execute("SELECT id FROM plan WHERE market='it' AND institution='bjn-local-research'").fetchone()[0]
 policy=load_policy()
 setting=agent_setting(store,plan)
 try:
  send_config=json.loads((ROOT/'config/send-batch.json').read_text(encoding='utf-8'))
  window=send_config.get('window') if send_config.get('windowEnabled') else None
  if isinstance(window,list) and len(window)==2:
   setting={**setting,'sendStart':window[0],'sendEnd':window[1]}
 except (OSError,ValueError,TypeError):
  pass
 return {'sendTemplates':send_templates(store),'manualTemplates':manual_templates(store),
  'agentTemplates':[{'id':key,'action':value['action'],'language':value['language'],'text':value['text']} for key,value in policy['templates'].items()],
  'agentSetting':setting,'platformWrites':0,'realSends':0}

def main():
 try:
  raw=sys.stdin.read(20001)
  if len(raw.encode())>20000:raise CycleError('input_too_large')
  req=json.loads(raw or '{}');action=req.get('action');readonly=action=='status'
  if action not in ('status','create_send','update_send','archive_send','upsert_manual','save_agent'):
   raise CycleError('invalid_action')
  with CycleStore(ROOT/'var/second-cycle.sqlite',readonly=readonly) as store:
   plan=store.db.execute("SELECT id FROM plan WHERE market='it' AND institution='bjn-local-research'").fetchone()[0]
   if action=='status':
    if set(req)!={'action'}:raise CycleError('invalid_input')
   elif action=='create_send':
    if set(req)!={'action','requestId','name','bodyIt'}:raise CycleError('invalid_input')
    create_send_template(store,req['requestId'],req['name'],req['bodyIt'])
   elif action=='update_send':
    if set(req)!={'action','templateId','expectedRevision','name','bodyIt'}:raise CycleError('invalid_input')
    update_send_template(store,req['templateId'],req['expectedRevision'],req['name'],req['bodyIt'])
   elif action=='archive_send':
    if set(req)!={'action','templateId','expectedRevision'}:raise CycleError('invalid_input')
    try:send_config=json.loads((ROOT/'config/send-batch.json').read_text(encoding='utf-8'))
    except (OSError,ValueError,TypeError):send_config={}
    if send_config.get('template')==req['templateId']:raise CycleError('template_in_use')
    archive_send_template(store,req['templateId'],req['expectedRevision'])
   elif action=='upsert_manual':
    if set(req)!={'action','requestId','templateId','expectedRevision','name','category','body'}:raise CycleError('invalid_input')
    upsert_manual_template(store,req['requestId'],req['templateId'],req['expectedRevision'],req['name'],req['category'],req['body'])
   else:
    if set(req)!={'action','expectedRevision','setting'}:raise CycleError('invalid_input')
    saved=save_agent_setting(store,plan,req['expectedRevision'],req['setting'])
    from lib.send_batch import save_config
    save_config(ROOT,{'windowEnabled':True,'window':[saved['sendStart'],saved['sendEnd']]})
   result=snapshot(store)
  print(json.dumps(result,ensure_ascii=False));return 0
 except Exception as error:
  print(json.dumps({'error':str(error) if isinstance(error,CycleError) else 'template_library_unavailable'},ensure_ascii=False));return 2

if __name__=='__main__':raise SystemExit(main())
