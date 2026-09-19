"""The task's TapLink material must come from the product-level catalog ledger,
and must never wait for the creator roster to be complete."""
import json,sys,tempfile,unittest
import sqlite3
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'));sys.path.insert(0,str(ROOT/'vendor'))
from lib.batch_task_service import TaskService
from lib.batch_materials import BatchMaterials,apply_materials
from lib.second_cycle import CycleStore,digest
from lib.catalog_prepare import CatalogPreparation,new_offer
from lib.catalog_binding import CatalogBindings
from lib.schema_migrations import apply_database
from test_second_cycle import offer as cycle_offer,NOW
CLOCK=[NOW]

class MaterialLinkTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        (self.root/'var').mkdir();(self.root/'config').mkdir()
        (self.root/'config/catalog-link-policy.json').write_text((ROOT/'config/catalog-link-policy.json').read_text())
        self.policy=json.loads((self.root/'config/catalog-link-policy.json').read_text())
        self.s=TaskService(self.root/'var/batch-tasks.sqlite',lambda:CLOCK[0])
        card=self.s.preview({'institution':'bjn-local-research','market':'it','target':2,'startDate':'2026-09-15','startTime':'22:00','endTime':'01:00'})
        self.task=self.s.confirm(card['token'],'link-consumer')
        self.pid='1729480061238089885';self.cid='7685262119046498070'
        self.offer=cycle_offer(pid=self.pid,campaignId=self.cid,catalogSource='selected',title='Quaderno',creatorPercent='13',publicPercent='12',totalPercent='15')
        with CycleStore(self.root/'var/second-cycle.sqlite',lambda:CLOCK[0]) as store:
            plan=store.plan('bjn-local-research','it');store.publish(plan,'selected',NOW,[self.offer])
        sqlite3.connect(self.root/'var/catalog-links.sqlite').close();apply_database(self.root,'catalog-links')
        self.prep=CatalogPreparation(self.root)
        self.run=self.prep.open_run({'market':'it','account':'acc9','institution':'bjn-local-research','sourceRun':'r'})
        self.prep.seed(self.run,[{'pid':self.pid,'campaignId':self.cid,'catalogSource':'selected'}],{'market':'it'})
    def tearDown(self):self.prep.close();self.s.close();self.tmp.cleanup()
    def ready(self):
        self.prep.claim_read(self.run,limit=5)
        self.prep.apply_read(self.run,self.pid,self.cid,'selected',{'state':'missing','listing':{'product_id':self.pid,'title':'Quaderno','creatorPercent':'13','publicPercent':'12','totalPercent':'15'}})
        name='🔥 BJN Quaderno 13% abcdef'
        card={'state':'verified_read_only','pid':self.pid,'verifiedListName':name,'listName':name,'campaignName':'x',
              'stock':None,'stockRequired':False,'publicPercent':'12','listId':'8650756273145355030','wireCampaignId':'0','sourceCampaignId':self.cid,
              'creatorPercent':'13','checkedAt':NOW,'evidenceRefs':['a','b'],'executionAllowed':False}
        spec={'market':'it','route':'selected','pid':self.pid,'campaignId':self.cid,'creatorPercent':'13','listName':name,
              'policyVersion':'commission-1-to-2-v1','namingVersion':'link-naming-v1','offer':self.offer}
        bindings=CatalogBindings(self.root)
        try:bindings.promote(spec,card,now=NOW)
        finally:bindings.close()
        self.prep.mark_progress(self.run,self.pid,self.cid,'selected','ready',card=card)
        return card
    def advance(self):
        from lib.batch_material_runtime import advance_materials
        empty={'state':'waiting_adapters','target':2,'reserve':0,'required':2,'candidates':0,'candidateGap':2,'blockers':['pid_creator_supply_needed'],'stages':[]}
        with patch('lib.batch_material_runtime.read_local_preparation',return_value=(empty,[])):
            advance_materials(self.s,self.s.tasks.get(self.task['id']),self.root,lambda:NOW)
        if not self.s.db.execute("SELECT 1 FROM sqlite_master WHERE name='batch_material_link'").fetchone():return -1
        return self.s.db.execute('SELECT count(*) FROM batch_material_link').fetchone()[0]
    def test_links_advance_without_any_creators_in_the_roster(self):
        self.ready();self.advance()
        rows=[dict(r) for r in self.s.db.execute('SELECT * FROM batch_material_link WHERE task_id=?',(self.task['id'],))]
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['state'],'ready')
        self.assertEqual(json.loads(rows[0]['card'])['listId'],'8650756273145355030')
    def test_unprepared_plan_is_pending_and_blocks_with_a_reason(self):
        self.advance()
        rows=[dict(r) for r in self.s.db.execute('SELECT * FROM batch_material_link WHERE task_id=?',(self.task['id'],))]
        self.assertEqual(rows[0]['state'],'pending');self.assertEqual(rows[0]['reason'],'catalog_link_lookup_pending')
    def test_stale_verified_terms_never_satisfy_the_offer(self):
        self.advance()  # plans the product before any creator exists
        self.ready()
        with self.prep.db:self.prep.db.execute("UPDATE catalog_current_binding SET offer_fingerprint='changed' WHERE pid=?",(self.pid,))
        from lib.batch_material_runtime import advance_materials
        empty={'state':'waiting_adapters','target':2,'reserve':0,'required':2,'candidates':0,'candidateGap':2,'blockers':[],'stages':[]}
        with patch('lib.batch_material_runtime.read_local_preparation',return_value=(empty,[])):
            advance_materials(self.s,self.s.tasks.get(self.task['id']),self.root,lambda:NOW)
        rows=[dict(r) for r in self.s.db.execute('SELECT * FROM batch_material_link WHERE task_id=?',(self.task['id'],))]
        self.assertEqual(rows[0]['state'],'blocked');self.assertEqual(rows[0]['reason'],'catalog_link_terms_changed')
    def test_paused_task_never_registers_link_plans(self):
        self.s.control(self.task['id'],'pause',1)
        self.assertEqual(self.advance(),-1)
    def test_report_separates_product_coverage_from_creator_readiness(self):
        self.ready();self.advance()
        report={'state':'waiting_adapters','target':2,'candidates':3,'blockers':[],'stages':[{'key':'taplink','done':0,'required':3}]}
        members=[{'oec':'1','materialKey':'k1','checks':{'taplink':False}}]
        out,_=apply_materials(self.s,self.task['id'],report,members)
        self.assertEqual(out['linkCoverage'],{'plannedPids':1,'readyPids':1,'pendingPids':0,'reasons':{}})
        self.assertIn('task_materials_pending',out['blockers'])

if __name__=='__main__':unittest.main()
