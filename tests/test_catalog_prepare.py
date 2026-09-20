import json,sqlite3,sys,tempfile,unittest
from contextlib import closing
from decimal import Decimal
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'));sys.path.insert(0,str(ROOT/'vendor'))
from lib.catalog_prepare import (CatalogPreparation,assess_existing,choose_existing_batch,new_offer,
 classify_pid,search_cards,card_facts,rate_text,CARD,MEMBERS,TaplinkInventory,new_offer,
 reconcile_from_inventory,reconcile_unknown_batch,needs_standard_reread)
from lib.catalog_links import CatalogLinks,policy_fingerprint
from lib.catalog_binding import CatalogBindings
from lib.schema_migrations import apply_database
from lib.second_cycle import digest
from types import SimpleNamespace
POLICY=json.loads((ROOT/'config/catalog-link-policy.json').read_text())

class BatchLinkTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        (self.root/'var').mkdir();(self.root/'config').mkdir()
        (self.root/'config/catalog-link-policy.json').write_text(json.dumps(POLICY))
        sqlite3.connect(self.root/'var/catalog-links.sqlite').close();apply_database(self.root,'catalog-links')
        sqlite3.connect(self.root/'var/second-cycle.sqlite').close();apply_database(self.root,'second-cycle')
        self.prep=CatalogPreparation(self.root)
        self.scope={'market':'it','account':'acc9','institution':'bjn-local-research','sourceRun':'run-1','route':'selected'}
        self.run=self.prep.open_run(self.scope)
        self.offer=self.make_offer('1729480061238089885','7685262119046498070')
    def tearDown(self):self.prep.close();self.tmp.cleanup()
    def make_offer(self,pid,cid,total='1500',public='1200'):
        return new_offer({'product_id':pid,'title':'Quaderno','commission_rate':total,'open_collab_rate':public,'sales':'330 已售','product_rating':4.5,'product_status':2},pid,cid,'selected',policy=POLICY)
    def seed(self,pid,cid):
        self.prep.seed(self.run,[{'pid':pid,'campaignId':cid,'catalogSource':'selected'}],self.scope)
    def reader(self,cards,members=None,member_campaign=None):
        """Mirror the real response shape: selected-route member rows carry the real campaign id."""
        def read(path,extra):
            if path==CARD:
                pid=extra['key_word'];rows=[{'product_list_id':lid,'campaign_id':wire,'campaign_products':products} for lid,wire,products in cards.get(pid,[])]
                return {'code':0,'data':{'total':len(rows),'list':rows}},digest(rows)
            lid=extra['list_id'];rows=[dict(r) for r in (members or {}).get(lid,[])]
            if member_campaign is not None:
                for r in rows:r['campaign_id']=member_campaign
            return {'code':0,'data':{'total_num':len(rows),'campaign_products':rows}},digest(rows)
        return read
    def verified_card(self,pid,cid,list_id='8650756273145355030',creator='1300',public='1200'):
        pct=format(Decimal(creator)/100,'f');name=f'🔥 BJN Quaderno {pct}% abcdef'
        return {'state':'verified_read_only','pid':pid,'verifiedListName':name,'listName':name,'campaignName':'x',
                'stock':None,'stockRequired':False,'publicPercent':format(Decimal(public)/100,'f'),'listId':list_id,'wireCampaignId':'0','sourceCampaignId':cid,
                'creatorPercent':format(Decimal(creator)/100,'f'),'checkedAt':1.0,'evidenceRefs':['a','b'],'executionAllowed':False}
    def promote(self,spec,card):
        bindings=CatalogBindings(self.root)
        try:return bindings.promote(spec,card)
        finally:bindings.close()
    # ---- queue behaviour ---------------------------------------------------------
    def test_only_legacy_or_unproven_rows_are_requeued_for_the_standard_search(self):
        self.assertTrue(needs_standard_reread('reuse',{}));self.assertTrue(needs_standard_reread('review',{}))
        self.assertTrue(needs_standard_reread('missing',{'state':'existing_links_observed'}))
        self.assertFalse(needs_standard_reread('missing',{'state':'standard_missing','standardSearchComplete':True}))
        self.assertFalse(needs_standard_reread('ready',{}))
    def test_inventory_reconciled_missing_row_without_card_can_freeze_once(self):
        self.seed('1','2')
        self.prep.db.execute("UPDATE catalog_prepare_item SET listing=?,state='missing' WHERE run_id=? AND pid='1'",
                             (json.dumps(self.plan_listing()),self.run))
        intent=self.prep.freeze(self.run,'1','2','selected',self.spec('1','2','13'))
        self.assertEqual(intent['state'],'prepared')
        self.assertTrue(self.prep.item(self.run,'1','2')['card']['standardSearchComplete'])
    def test_seed_is_idempotent_and_read_requires_complete_search(self):
        self.seed('1','2');self.seed('1','2');self.assertEqual(self.prep.summary(self.run)['total'],1)
        claimed=self.prep.claim_read(self.run,limit=5)
        self.prep.release(self.run,'1','2','selected')
        self.assertEqual(len(claimed),1)
        def incomplete(path,extra):
            if path==CARD:return {'code':0,'data':{'total':3,'list':[{'product_list_id':'9','campaign_id':'0','campaign_products':[]}]}}, 'sha'
            raise AssertionError('members must not be read')
        with self.assertRaises(ValueError):search_cards(incomplete,'1')
        self.prep.apply_read(self.run,'1','2','selected',{'state':'read_incomplete','error':'card_search_incomplete'})
        self.assertEqual(self.prep.item(self.run,'1','2')['state'],'read_incomplete')
        self.assertEqual(self.prep.summary(self.run)['incompleteCount'],1)
    def test_expired_lease_is_reclaimed_without_losing_the_row(self):
        self.seed('1','2');self.prep.claim_read(self.run,now=1000,lease=10)
        self.assertEqual(self.prep.claim_read(self.run,now=1005,lease=10),[])
        again=self.prep.claim_read(self.run,now=1011,lease=10)
        self.assertEqual([i['pid'] for i in again],['1'])
    # ---- reuse rules -------------------------------------------------------------
    def test_existing_valid_old_link_is_historical_and_standard_is_still_missing(self):
        self.seed('1','2')
        members={'8650756273145355030':[{'product_id':'1','campaign_id':'2','creator_commission_percent':'1335','plan_commission_percent':'1200','product_status':2,'unavailable_type':None,'stock':500}]}
        cards={'1':[('8650756273145355030','0',[{'product_id':'1','creator_commission_percent':'1335'}])]}
        out=classify_pid('1',self.make_offer('1','2'),self.reader(cards,members),POLICY)
        self.assertEqual(out['state'],'missing');self.assertEqual(out['reuse'][0]['listId'],'8650756273145355030')
    def test_exact_standard_link_is_promoted_without_creating_another(self):
        offer=self.make_offer('1','2');spec=self.spec('1','2',offer['creatorPercent'])
        member={'product_id':'1','campaign_id':'2','creator_commission_percent':'1300',
                'plan_commission_percent':'1200','product_status':2,'unavailable_type':None,'stock':500}
        def read(path,extra):
            if path==CARD:
                row={'product_list_id':'99','campaign_id':'0','product_list_name':spec['listName'],
                     'campaign_products':[{'product_id':'1','creator_commission_percent':'1300'}]}
                return {'code':0,'data':{'total':1,'list':[row]}},'card-proof'
            return {'code':0,'data':{'total_num':1,'campaign_products':[member]}},'member-proof'
        outcome=classify_pid('1',offer,read,POLICY,spec)
        self.assertEqual(outcome['state'],'standard')
        self.seed('1','2');self.prep.claim_read(self.run,limit=1)
        outcome['listing']=self.plan_listing();self.prep.apply_read(self.run,'1','2','selected',outcome,now=10)
        self.assertEqual(self.prep.item(self.run,'1','2')['state'],'ready')
        self.assertEqual(self.prep.verified_link('1','2','selected')['listId'],'99')
    def test_creator_not_above_public_or_agency_below_one_point_is_not_reused(self):
        # Plan is 17%/12%: creator must exceed 12 and the agency must keep at least one point.
        cases=[({'creator_commission_percent':'1200'},'link_creator_not_above_public'),
               ({'creator_commission_percent':'1680'},'link_agency_below_minimum')]
        for member,reason in cases:
            base={'product_id':'1','campaign_id':'2','plan_commission_percent':'1200','product_status':2,'unavailable_type':None,'stock':500}|member
            out=classify_pid('1',self.make_offer('1','2',total='1700',public='1200'),self.reader({'1':[('99','0',[{'product_id':'1','creator_commission_percent':'1200'}])]},{'99':[base]}),POLICY)
            self.assertEqual(out['state'],'missing');self.assertEqual(out['reuse'][0]['reason'],reason)
    def test_unavailable_or_non_two_status_link_is_not_reused(self):
        for member,reason in [({'product_status':3},'link_not_platform_valid'),({'unavailable_type':5,'product_status':2},'link_product_not_eligible')]:
            base={'product_id':'1','campaign_id':'2','creator_commission_percent':'1335','plan_commission_percent':'1200','product_status':2,'unavailable_type':None,'stock':500}|member
            out=classify_pid('1',self.make_offer('1','2',total='1700',public='1200'),self.reader({'1':[('99','0',[{'product_id':'1','creator_commission_percent':'1200'}])]},{'99':[base]}),POLICY)
            self.assertEqual(out['reuse'][0]['reason'],reason)
    def test_highest_creator_rate_then_reuse_preference(self):
        cards=[{'listId':'a','creatorRaw':'1335','publicRaw':'1200','totalRaw':'1500','platformValid':True,'productEligible':True,'previouslyUsed':False},
               {'listId':'b','creatorRaw':'1340','publicRaw':'1200','totalRaw':'1500','platformValid':True,'productEligible':True,'previouslyUsed':False},
               {'listId':'c','creatorRaw':'1340','publicRaw':'1200','totalRaw':'1500','platformValid':True,'productEligible':True,'previouslyUsed':True}]
        self.assertEqual(choose_existing_batch(cards,POLICY)['listId'],'c')
        self.assertIsNone(choose_existing_batch([cards[2]|{'platformValid':False}],POLICY))
    # ---- campaign isolation ------------------------------------------------------
    def test_cards_from_another_campaign_never_authorize_a_new_link(self):
        self.seed('1','2')
        out=classify_pid('1',self.make_offer('1','2'),self.reader({'1':[('77','999',[{'product_id':'1'}])]}),POLICY)
        self.assertEqual(out['state'],'missing')
    def test_multi_campaign_pid_binds_one_plan_and_keeps_bands_apart(self):
        a=self.make_offer('1','2',total='1500',public='1200');b=self.make_offer('1','3',total='1700',public='1200')
        self.assertEqual((a['creatorPercent'],b['creatorPercent']),('13','15'))
        members={'50':[{'product_id':'1','campaign_id':'3','creator_commission_percent':'1500','plan_commission_percent':'1200','product_status':2,'unavailable_type':None,'stock':500}]}
        out=classify_pid('1',b,self.reader({'1':[('50','0',[{'product_id':'1'}])]},members),POLICY)
        self.assertEqual(out['state'],'missing')
        other=classify_pid('1',a,self.reader({'1':[('50','0',[{'product_id':'1'}])]},members),POLICY)
        self.assertEqual(other['state'],'missing')
    # ---- frozen intent and durability -------------------------------------------
    def test_freeze_creates_one_intent_and_replay_is_idempotent(self):
        self.seed('1','2');self.prep.claim_read(self.run,limit=5)
        self.prep.apply_read(self.run,'1','2','selected',{'state':'missing','listing':{'product_id':'1','creatorPercent':'13','publicPercent':'12','totalPercent':'15'}})
        spec=self.spec('1','2','13')
        first=self.prep.freeze(self.run,'1','2','selected',spec)
        again=self.prep.freeze(self.run,'1','2','selected',spec|{'preparedAt':999})
        self.assertEqual(first['id'],again['id']);self.assertEqual(first['state'],'prepared')
        ledger=CatalogLinks(self.root);self.assertEqual(len(ledger.db.execute('SELECT 1 FROM catalog_link_intent').fetchall()),1);ledger.db.close()
    def test_ready_state_exposes_exact_binding_and_rejects_changed_terms(self):
        pid,cid=self.offer['pid'],self.offer['campaignId']
        self.seed(pid,cid);self.prep.claim_read(self.run,limit=5)
        self.prep.apply_read(self.run,pid,cid,'selected',{'state':'missing','listing':self.plan_listing()})
        card=self.verified_card(pid,cid)
        self.promote(self.spec(pid,cid,self.offer['creatorPercent']),card)
        self.prep.mark_progress(self.run,pid,cid,'selected','ready',card=card)
        self.assertEqual(self.prep.offer_status(self.offer)['state'],'ready')
        self.assertEqual(self.prep.verified_link(pid,cid,'selected')['listId'],card['listId'])
        self.assertEqual(self.prep.offer_status(self.make_offer(pid,cid,total='1700'))['reason'],'catalog_link_terms_changed')
        self.assertEqual(self.prep.verified_link(pid,'9','selected'),None)
        self.assertEqual(self.prep.offer_status(self.make_offer('7','2'))['reason'],'catalog_link_not_prepared')
    def test_submitted_intent_goes_to_unknown_and_is_never_recreated(self):
        pid,cid=self.offer['pid'],self.offer['campaignId']
        self.seed(pid,cid);self.prep.claim_read(self.run,limit=5)
        self.prep.apply_read(self.run,pid,cid,'selected',{'state':'missing','listing':self.plan_listing()})
        intent=self.prep.freeze(self.run,pid,cid,'selected',self.spec(pid,cid,self.offer['creatorPercent']))
        item=self.prep.claim_create(self.run)
        self.assertEqual(item['intent_id'],intent['id']);self.assertIn(item['state'],('missing','prepared'))
        self.prep.mark_progress(self.run,pid,cid,'selected','submitted')
        self.prep.mark_progress(self.run,pid,cid,'selected','unknown',error='card_read_unresolved')
        self.prep.close();self.prep=CatalogPreparation(self.root)  # restart
        self.assertIsNone(self.prep.claim_create(self.run))
        self.assertEqual(self.prep.offer_status(self.offer)['reason'],'card_read_unresolved')
    def test_unknown_is_reconciled_after_batch_without_a_second_post(self):
        pid,cid=self.offer['pid'],self.offer['campaignId']
        self.seed(pid,cid);self.prep.claim_read(self.run,limit=5)
        self.prep.apply_read(self.run,pid,cid,'selected',{'state':'missing','listing':self.plan_listing()})
        intent=self.prep.freeze(self.run,pid,cid,'selected',self.spec(pid,cid,self.offer['creatorPercent']))
        ledger=CatalogLinks(self.root);ledger.begin(intent['id'],'acc9');ledger.unknown(intent['id'],'created_card_not_verified');ledger.db.close()
        self.prep.mark_progress(self.run,pid,cid,'selected','unknown',error='created_card_not_verified')
        sleeps=[];calls=[]
        def lookup(row,frozen):
            calls.append((row['pid'],frozen['id']))
            return self.verified_card(pid,cid) if len(calls)==2 else None
        result=reconcile_unknown_batch(self.root,self.prep,self.run,lookup,delays=(30,120),
                                       sleeper=sleeps.append,clock=lambda:123)
        self.assertEqual(len(calls),2);self.assertEqual(sleeps,[30])
        self.assertEqual(result['settled'][0]['pid'],pid)
        self.assertEqual(self.prep.item(self.run,pid,cid)['state'],'ready')
        ledger=CatalogLinks(self.root);self.assertEqual(ledger.get(intent['id'])['state'],'verified');ledger.db.close()
        with closing(sqlite3.connect(self.root/'var/second-cycle.sqlite')) as db,db:
            self.assertEqual(db.execute('SELECT count(*) FROM taplink_reconcile_attempt').fetchone()[0],2)
    def test_unknown_finally_becomes_skipped_and_keeps_original_intent(self):
        pid,cid=self.offer['pid'],self.offer['campaignId']
        self.seed(pid,cid);self.prep.claim_read(self.run,limit=5)
        self.prep.apply_read(self.run,pid,cid,'selected',{'state':'missing','listing':self.plan_listing()})
        intent=self.prep.freeze(self.run,pid,cid,'selected',self.spec(pid,cid,self.offer['creatorPercent']))
        ledger=CatalogLinks(self.root);ledger.begin(intent['id'],'acc9');ledger.unknown(intent['id'],'created_card_not_verified');ledger.db.close()
        self.prep.mark_progress(self.run,pid,cid,'selected','unknown',error='created_card_not_verified')
        calls=[]
        result=reconcile_unknown_batch(self.root,self.prep,self.run,
          lambda *_:(calls.append(1) and None),delays=(30,120),sleeper=lambda _:None,clock=lambda:123)
        self.assertEqual(len(calls),3);self.assertEqual(len(result['skippedUnknown']),1)
        self.assertEqual(self.prep.item(self.run,pid,cid)['state'],'skipped_unknown')
        ledger=CatalogLinks(self.root);self.assertEqual(ledger.get(intent['id'])['state'],'unknown');ledger.db.close()

    def test_verification_replays_only_the_same_frozen_create_request(self):
        from importlib.util import spec_from_file_location,module_from_spec
        spec=spec_from_file_location('catalog_link_prepare_script',ROOT/'scripts/catalog-link-prepare.py');module=module_from_spec(spec);spec.loader.exec_module(module)
        class Transport:
            _verification_header='challenge';verification_successes=0
            def __init__(self):self.calls=[];self.solved=[]
            def _params(self):return {'fixed':'params'}
            def _xhr(self,**kwargs):
                self.calls.append(kwargs)
                return SimpleNamespace(http_status=200,code=0,has_turing=len(self.calls)==1,ambiguous=False)
            def _solve_verification(self,header):self.solved.append(header)
        transport=Transport();report={};payload={'frozen':'request'}
        result=module.submit_create_with_verification(transport,payload,'1',report)
        self.assertFalse(result.has_turing);self.assertEqual(transport.solved,['challenge'])
        self.assertEqual(len(transport.calls),2);self.assertEqual(transport.calls[0],transport.calls[1])
        self.assertIs(transport.calls[0]['payload'],payload);self.assertEqual(transport.verification_successes,1)
    def test_summary_separates_links_from_covered_pids(self):
        for pid in ('1','2'):self.seed(pid,'2')
        self.prep.claim_read(self.run,limit=5)
        for pid in ('1','2'):self.prep.apply_read(self.run,pid,'2','selected',{'state':'missing','listing':{'product_id':pid}})
        card=self.verified_card('1','2');self.promote(self.spec('1','2','13'),card)
        self.prep.mark_progress(self.run,'1','2','selected','ready',card=card)
        s=self.prep.summary(self.run)
        self.assertEqual((s['verifiedLinkCount'],s['verifiedPidCount'],s['pendingCount']),(1,1,1))
    def test_legacy_creator_cannot_create_for_a_catalog_owned_pid(self):
        from lib.second_cycle import CycleStore
        from lib.cycle_card_creation import CardCreation
        self.seed('1','2');self.prep.claim_read(self.run,limit=5)
        self.prep.apply_read(self.run,'1','2','selected',{'state':'missing','listing':{'product_id':'1','creatorPercent':'13'}})
        self.prep.freeze(self.run,'1','2','selected',self.spec('1','2','13'))
        with CycleStore(self.root/'var/second-cycle.sqlite') as store:
            plan=store.plan('bjn-local-research','it')
            offer={'pid':'1','offerKey':'selected:1:2','campaignId':'2','catalogSource':'selected','creatorPercent':'13','publicPercent':'12','totalPercent':'15',
                   'endAt':None,'available':True,'rating':'4.5','title':'x','stock':None}
            from lib.second_cycle import CycleError
            with self.assertRaises(CycleError):CardCreation(store).prepare(plan,offer,'nome')
    def plan_listing(self):
        return {'product_id':self.offer['pid'],'title':self.offer['title'],'creatorPercent':self.offer['creatorPercent'],
                'publicPercent':self.offer['publicPercent'],'totalPercent':self.offer['totalPercent'],'agencyPercent':self.offer['agencyPercent']}
    def spec(self,pid,cid,pct):
        from lib.catalog_prepare import rate_text
        from bdhub.send.taplink.protocol import create_payload
        name='🔥 BJN Quaderno '+pct+'% abcdef'
        return {'market':'it','account':'acc9','route':'selected','purpose':'catalog_batch_link','sourceRun':self.run,'pid':pid,'campaignId':cid,
                'creatorPercent':pct,'listName':name,'shortName':'Quaderno','policyVersion':POLICY['version'],'policyFingerprint':policy_fingerprint(POLICY),'searchTotal':0,'standardSearchComplete':True,
                'namingVersion':'link-naming-v1','namingFingerprint':'a'*64,
                'offer':{'pid':pid,'campaignId':cid,'catalogSource':'selected','creatorPercent':pct,'publicPercent':'12','totalPercent':'15','agencyPercent':'2','title':'Quaderno'},
                'payload':create_payload(pid=pid,campaign_id=cid,creator_pct=pct,name=name,route='selected')}

class ScanListsShapeTests(unittest.TestCase):
    """平台在 total=0 时**整个省略 lists 键**：那是「一张卡都没有」，不是响应格式错误。

    实测来源：IT 的 43 个 campaign 里 41 个只返回 ``{"total": 0}``，旧写法把它们全部
    报成 ``taplink_inventory_malformed``；但 total>0 却缺键必须继续报错。
    """
    def reader(self,pages):
        def read(path,extra):
            body=pages[int(extra['cur_page'])]
            return body,digest(body)
        return read
    def test_zero_total_with_omitted_lists_is_empty(self):
        from lib.catalog_prepare import scan_lists
        total,rows=scan_lists(self.reader({1:{'code':0,'data':{'total':0}}}),source='1',campaign_id='9')
        self.assertEqual((total,rows),(0,[]))
    def test_missing_lists_with_nonzero_total_is_still_malformed(self):
        from lib.catalog_prepare import scan_lists
        with self.assertRaises(ValueError) as ctx:
            scan_lists(self.reader({1:{'code':0,'data':{'total':3}}}),source='1',campaign_id='9')
        self.assertEqual(str(ctx.exception),'taplink_inventory_malformed')

class CampaignChannelTests(unittest.TestCase):
    """非全托读卡：活动号在**卡**上，成员行没有活动号（实测 24/24 全是 None）。

    只按成员行匹配会把每一张非全托卡判成"卡在别的活动"，于是已有的非全托链接全部被挡住——
    实测就是这么把 24 个可复用商品报成 review 的。
    """
    CID='7666370360582260502'
    LID='8650756273145355030'
    PID='1729474628908391280'
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        (self.root/'var').mkdir();(self.root/'config').mkdir()
        (self.root/'config/catalog-link-policy.json').write_text(json.dumps(POLICY))
        self.prep=CatalogPreparation(self.root)
        self.scope={'market':'it','account':'acc9','institution':'bjn-local-research','sourceRun':'pool-1','route':'campaign'}
        self.run=self.prep.open_run(self.scope)
        self.prep.seed(self.run,[{'pid':self.PID,'campaignId':self.CID,'catalogSource':'campaign'}],self.scope)
        self.offer=new_offer({'title':'x','stock':321,'product_status':2},self.PID,self.CID,'campaign',
                             total=1300,public=1000,policy=POLICY)
    def tearDown(self):self.prep.close();self.tmp.cleanup()
    def member(self,list_id,campaign_id):
        return {'product_id':self.PID,'campaign_id':campaign_id,'creator_commission_percent':1100,
                'plan_commission_percent':1000,'product_status':2,'is_under_governed':False,
                'unavailable_type':None,'stock':321}
    def judge(self):
        inv=TaplinkInventory(self.root)
        try:
            items=[dict(r) for r in self.prep.db.execute('SELECT * FROM catalog_prepare_item WHERE run_id=?',(self.run,))]
            return reconcile_from_inventory(self.prep,inv,items,{self.PID:self.offer})
        finally:inv.close()
    def test_member_without_campaign_id_is_matched_by_the_card_itself(self):
        inv=TaplinkInventory(self.root)
        try:
            inv.save_list({'list_id':self.LID,'name':'BJN x 11% abcdef','url':'','product_total':1,'platform_updated_at':None},
                          source='1',campaign_id=self.CID)
            inv.save_members(self.LID,'BJN x 11% abcdef',[self.member(self.LID,None)])
        finally:inv.close()
        self.assertEqual(self.judge(),{'missing':1})
        self.assertIsNone(self.prep.db.execute(
            "SELECT payload FROM catalog_prepare_readback WHERE run_id=? AND kind='verifiedLink'",
            (self.run,)).fetchone())
    def test_a_card_from_another_campaign_is_never_reused(self):
        inv=TaplinkInventory(self.root)
        try:
            inv.save_list({'list_id':self.LID,'name':'BJN x 11% abcdef','url':'','product_total':1,'platform_updated_at':None},
                          source='1',campaign_id='7683821197913540374')
            inv.save_members(self.LID,'BJN x 11% abcdef',[self.member(self.LID,None)])
        finally:inv.close()
        self.assertEqual(self.judge(),{'missing':1})
    def test_a_selected_card_does_not_satisfy_a_campaign_plan(self):
        """全托卡是非全托的**另一个平台对象**，不能拿来当非全托的链接。"""
        inv=TaplinkInventory(self.root)
        try:
            inv.save_list({'list_id':self.LID,'name':'BJN x 11% abcdef','url':'','product_total':1,'platform_updated_at':None},
                          source='2',campaign_id='0')
            inv.save_members(self.LID,'BJN x 11% abcdef',[self.member(self.LID,self.CID)])
        finally:inv.close()
        self.assertEqual(self.judge(),{'missing':1})
    def test_campaign_preflight_accepts_normalized_offer_without_nested_listing(self):
        normalized={key:value for key,value in self.offer.items() if key!='listing'}
        def read(path,extra):
            if path==CARD:return {'code':0,'data':{'total':0}},'sha'
            raise AssertionError('no member read for an empty search')
        result=classify_pid(self.PID,normalized,read,POLICY,standard_spec={'listName':'x','creatorPercent':'11'})
        self.assertEqual(result['state'],'missing');self.assertTrue(result['standardSearchComplete'])

if __name__=='__main__':unittest.main()
