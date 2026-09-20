import tempfile,unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.schema_migrations import apply_database
from lib.second_cycle import CycleError,CycleStore
from lib.template_library import (agent_setting,archive_send_template,create_send_template,manual_templates,render_send_template,
 resolve_send_template,save_agent_setting,send_templates,update_send_template,upsert_manual_template)
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

if __name__=='__main__':unittest.main()
