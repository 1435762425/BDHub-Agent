import tempfile,unittest,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from lib.schema_migrations import apply_database
from lib.second_cycle import CycleError,CycleStore
from lib.template_library import (agent_setting,agent_templates,archive_send_template,create_send_template,
 manual_templates,next_approved_send_template,render_send_template,resolve_send_template,review_send_template,
 save_agent_setting,selected_send_template,send_template_reviews,send_templates,update_agent_template,
 update_send_template,upsert_manual_template)
from lib.reply_events import load_policy
from test_second_cycle import NOW,offer

class TemplateLibraryTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);(self.root/'var').mkdir();
  with CycleStore(self.root/'var/second-cycle.sqlite') as store:self.plan=store.plan('bjn-local-research','it')
  apply_database(self.root,'second-cycle',clock=lambda:NOW)
  self.store=CycleStore(self.root/'var/second-cycle.sqlite',lambda:NOW)
 def tearDown(self):self.store.close();self.tmp.cleanup()
 def test_custom_send_template_is_versioned_and_renders_only_allowed_parameters(self):
  body='Ciao @{creator_handle}! {product_name}: {creator_commission}%.'
  created=create_send_template(self.store,'request-0001','自定义',body)
  self.assertFalse(created['builtIn']);self.assertEqual(created['revision'],1)
  updated=update_send_template(self.store,created['id'],1,'自定义二版',body+' Grazie!')
  self.assertEqual(updated['revision'],2)
  spec=resolve_send_template(self.store,created['id'])
  message=render_send_template(spec,{'mentionIt':'questo prodotto','shortNameZh':'商品'},offer(endAt=NOW+90*86400),'alice')
  self.assertIn('alice',message['textIt']);self.assertEqual(message['templateRevision'],2)
  with self.assertRaises(CycleError):create_send_template(self.store,'request-0002','坏模板','Ciao {unknown}')
 def test_builtin_send_template_can_be_edited_and_deleted(self):
  body='Ciao @{creator_handle}! {product_name} {creator_commission}% — versione BJN.'
  updated=update_send_template(self.store,'standard',1,'佣金提升新版',body)
  self.assertTrue(updated['builtIn']);self.assertEqual(updated['revision'],2);self.assertEqual(updated['bodyIt'],body)
  spec=resolve_send_template(self.store,'standard');self.assertFalse(spec['builtIn']);self.assertEqual(spec['revision'],2)
  archive_send_template(self.store,'standard',2)
  self.assertNotIn('standard',{row['id'] for row in send_templates(self.store)})
  self.assertEqual(next(row for row in send_templates(self.store,True) if row['id']=='standard')['state'],'archived')
  with self.assertRaisesRegex(CycleError,'template_missing'):resolve_send_template(self.store,'standard')
 def test_manual_templates_are_separate(self):
  saved=upsert_manual_template(self.store,'manual-request',None,None,'确认','常用','Grazie!')
  self.assertEqual(saved['body'],'Grazie!');self.assertEqual(len(manual_templates(self.store)),1)
  self.assertTrue(all(row['id']!=saved['id'] for row in send_templates(self.store)))
  duplicate=upsert_manual_template(self.store,'manual-request',None,None,'确认','常用','Grazie!')
  self.assertEqual(duplicate['id'],saved['id'])
  with self.assertRaisesRegex(CycleError,'template_request_conflict'):
   upsert_manual_template(self.store,'manual-request',None,None,'确认','常用','正文不同')
 def test_agent_schedule_rejects_overlap_and_human_auto(self):
  base=agent_setting(self.store,self.plan);self.assertFalse(base['enabled'])
  value={k:v for k,v in base.items() if k not in ('revision','updatedAt')}
  value['enabled']=True
  saved=save_agent_setting(self.store,self.plan,0,value);self.assertEqual(saved['sendStart'],'16:30')
  bad={**value,'replyEnd':'17:00'}
  with self.assertRaisesRegex(CycleError,'overlap'):save_agent_setting(self.store,self.plan,1,bad)
  with self.assertRaises(CycleError):save_agent_setting(self.store,self.plan,1,{**value,'actions':{**value['actions'],'human':True}})
  after=save_agent_setting(self.store,self.plan,1,{**value,'replyStart':'00:30','replyEnd':'08:00'})
  self.assertEqual((after['replyStart'],after['replyEnd']),('00:30','08:00'))
  with self.assertRaisesRegex(CycleError,'overlap'):
   save_agent_setting(self.store,self.plan,2,{**value,'replyStart':'00:00','replyEnd':'08:00'})
 def test_agent_action_name_is_fixed_but_body_is_revisioned(self):
  policy=load_policy();rows=agent_templates(self.store,policy)
  current=next(row for row in rows if row['action']=='collaboration_ack')
  changed=update_agent_template(self.store,policy,current['id'],1,'Perfetto, grazie!')
  self.assertEqual(changed['action'],'collaboration_ack');self.assertEqual(changed['revision'],2)
  self.assertEqual(changed['text'],'Perfetto, grazie!')
  with self.assertRaisesRegex(CycleError,'revision_conflict'):
   update_agent_template(self.store,policy,current['id'],1,'Versione vecchia')
  with self.assertRaisesRegex(CycleError,'request_invalid'):
   update_agent_template(self.store,policy,'human',1,'No')

 def test_manual_and_agent_templates_are_isolated_by_market_plan(self):
  self.store.plan('bjn-local-research','br')
  it=upsert_manual_template(self.store,'manual-shared-request',None,None,'IT','常用','Grazie!',market='it')
  br=upsert_manual_template(self.store,'manual-shared-request',None,None,'BR','常用','Obrigado!',market='br')
  self.assertEqual(it['id'],br['id'])
  self.assertEqual(manual_templates(self.store,market='it')[0]['body'],'Grazie!')
  self.assertEqual(manual_templates(self.store,market='br')[0]['body'],'Obrigado!')
  policy=load_policy();before=next(row for row in agent_templates(self.store,policy,'it') if row['action']=='collaboration_ack')
  br_current=next(row for row in agent_templates(self.store,policy,'br') if row['action']=='collaboration_ack')
  changed=update_agent_template(self.store,policy,br_current['id'],1,'Perfeito, obrigado!',market='br')
  self.assertEqual(changed['text'],'Perfeito, obrigado!')
  after=next(row for row in agent_templates(self.store,policy,'it') if row['action']=='collaboration_ack')
  self.assertEqual(after['text'],before['text'])
 def test_continuous_control_is_the_current_template_delete_gate(self):
  self.store.db.execute("INSERT INTO continuous_send_control VALUES(?,0,0,0,'16:30','24:00','reconnect',1,?)",(self.plan,NOW))
  self.assertEqual(selected_send_template(self.store,self.root),'reconnect')

 def test_ten_semantic_approvals_unlock_rotation_and_each_creator_uses_a_template_once(self):
  from lib.cycle_delivery import Deliveries
  status=send_template_reviews(self.store,ROOT)
  self.assertEqual((status['approved'],status['total'],status['ready']),(0,16,False))
  for row in status['items'][:10]:
   status=review_send_template(self.store,ROOT,'review-'+row['templateId'],row['templateId'],
                               'approved',row['revision'])
  self.assertEqual((status['approved'],status['ready']),(10,True))
  first=next_approved_send_template(self.store,ROOT,self.plan,'creator-1')
  self.assertEqual(first['id'],'standard')
  Deliveries(self.store)
  snapshot={'message':{'template':'standard'}}
  self.store.db.execute("INSERT INTO cycle_delivery VALUES('d1',?,?,?,?,?,?,?,?,'ready')",
                        (self.plan,'creator-1','100','1','source-1',__import__('json').dumps(snapshot),NOW,NOW+60))
  second=next_approved_send_template(self.store,ROOT,self.plan,'creator-1')
  self.assertEqual(second['id'],'brief')

 def test_editing_an_approved_meaning_invalidates_that_approval(self):
  status=send_template_reviews(self.store,ROOT);row=status['items'][0]
  review_send_template(self.store,ROOT,'review-standard','standard','approved',row['revision'])
  update_send_template(self.store,'standard',1,'新版','Ciao @{creator_handle}! {product_name} {creator_commission}%.')
  changed=send_template_reviews(self.store,ROOT)
  self.assertEqual(changed['items'][0]['state'],'pending')
  self.assertEqual(changed['approved'],0)

 def test_each_market_approves_only_its_own_copy(self):
  import json,shutil
  shutil.copytree(ROOT/'config',self.root/'config')
  row=send_template_reviews(self.store,self.root,'br')['items'][0]
  review_send_template(self.store,self.root,'review-standard-br','standard','approved',row['revision'],'br')
  self.assertEqual(send_template_reviews(self.store,self.root,'de')['items'][0]['state'],'pending')
  registry=json.loads((self.root/'config/markets.json').read_text())
  content=json.loads((self.root/'config/market-content.json').read_text())
  content['markets']['de']['sendTemplates']['standard']['text']+=' Bis bald!'
  registry['markets']['de']['runtimeState']='ready'
  (self.root/'config/market-content.json').write_text(json.dumps(content,ensure_ascii=False))
  (self.root/'config/markets.json').write_text(json.dumps(registry,ensure_ascii=False))
  self.assertEqual(send_template_reviews(self.store,self.root,'br')['items'][0]['state'],'approved')
  review_send_template(self.store,self.root,'review-standard-de','standard','approved',0,'de')
  self.assertEqual(send_template_reviews(self.store,self.root,'de')['items'][0]['state'],'approved')
  content['markets']['br']['sendTemplates']['standard']['text']+=' Até logo!'
  (self.root/'config/market-content.json').write_text(json.dumps(content,ensure_ascii=False))
  self.assertEqual(send_template_reviews(self.store,self.root,'br')['items'][0]['state'],'pending')
  self.assertEqual(send_template_reviews(self.store,self.root,'de')['items'][0]['state'],'approved')

 def test_shared_approvals_carry_over_to_the_legacy_markets_once(self):
  import shutil
  from lib.market_content import load_content
  from lib.second_cycle import digest
  from lib.template_library import LEGACY_REVIEW_MARKETS,_market_entry,backfill_market_reviews
  shutil.copytree(ROOT/'config',self.root/'config')
  content=load_content(self.root);db=self.store.db
  current={market:_market_entry(db,content,market,'standard') for market in LEGACY_REVIEW_MARKETS}
  stale={**current,'br':{**current['br'],'text':'outdated'}}
  with self.store.tx():
   db.execute('INSERT INTO send_template_review VALUES(?,?,?,?,?)',
              ('standard',digest({'templateId':'standard','markets':current}),'approved',3,NOW))
   db.execute('INSERT INTO send_template_review VALUES(?,?,?,?,?)',
              ('brief',digest({'templateId':'brief','markets':stale}),'approved',1,NOW))
   carried=backfill_market_reviews(db,self.root,NOW)
   self.assertEqual(backfill_market_reviews(db,self.root,NOW),carried)
  self.assertEqual(carried,len(LEGACY_REVIEW_MARKETS))
  for market in LEGACY_REVIEW_MARKETS:
   states={row['templateId']:row['state'] for row in send_template_reviews(self.store,self.root,market)['items']}
   self.assertEqual((states.get('standard'),states.get('brief')),('approved','pending'))
  self.assertEqual(send_template_reviews(self.store,self.root,'de')['items'][0]['state'],'pending')

if __name__=='__main__':unittest.main()
