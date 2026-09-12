from contextlib import contextmanager
from dataclasses import asdict
import tempfile
from types import SimpleNamespace
import unittest
from test_second_live_trial import Clock,source_item
from test_italy_im_delivery import card
from lib.italy_im_delivery import ItalyImDeliveryError
from lib.second_live_trial import LiveTrialStore
from lib.second_live_runner import run_trial


class FakeRuntime:
    def __init__(self):
        self.opened=0;self.events=[];self.messages={};self.unconfirmed=False;self.fail_conversation=False;self.fail_text=False;self.fail_material=False
    def conversation(self,cid,oec):
        if self.fail_conversation:raise ValueError('read failed')
        return SimpleNamespace(conversation_id=cid,oec_id=oec,evidence_ref='fixture:conversation-proof')
    def create_once(self,oec,ref,*,before_dispatch):
        permit=before_dispatch({'market':'it','account':'acc6','oecId':oec,'stage':'create_conversation','requestRef':ref})
        assert permit['dispatchAllowed'];self.events.append('create')
        return {'conversationId':'12345','requestRef':ref,'evidenceRef':'fixture:create-response'}
    def send(self,conversation,text,ref,scope,before_dispatch):
        permit=before_dispatch({'market':'it','account':'acc6','oecId':conversation.oec_id,'conversationId':conversation.conversation_id,'stage':'send_message','requestRef':ref,**scope})
        assert permit['dispatchAllowed']
        if scope['componentKind']=='text' and self.fail_text:raise ItalyImDeliveryError('it_delivery_transport_error',outcome='result_unknown')
        self.events.append(scope['componentKind']);message={'conversationId':conversation.conversation_id,'requestRef':ref,'messageId':str(100+len(self.messages)),'accepted':True,'evidenceRef':'fixture:message-response'}
        self.messages[ref]=message;return message
    def send_card_once(self,conversation,card,ref,*,before_dispatch):
        return self.send(conversation,'[商品列表]',ref,{'componentKind':'card','productId':card.product_id,'listId':card.list_id,'campaignId':card.campaign_id,'bindingSha256':card.binding_sha256},before_dispatch)
    def send_once(self,conversation,text,ref,*,before_dispatch):return self.send(conversation,text,ref,{'componentKind':'text'},before_dispatch)
    def readback_card(self,conversation,card,ref,*,message_id=None):return self.readback(conversation,'[商品列表]',ref,message_id=message_id)
    def readback(self,conversation,text,ref,*,message_id=None):
        self.events.append('verify-card' if text=='[商品列表]' else 'verify-text')
        if self.unconfirmed:return {'status':'result_unknown'}
        return {'status':'confirmed','messageId':self.messages[ref]['messageId'],'evidenceRef':'fixture:readback-proof'}
    @contextmanager
    def gate(self):yield lambda:None
    @contextmanager
    def factory(self,item,*,read_only=False):
        self.opened+=1
        def validate_text():
            if self.fail_material:
                from lib.second_card_binding import SecondCardBindingError
                raise SecondCardBindingError('promotion_condition_changed')
        yield {'adapter':self,'reads':self,'write_gate':self.gate,'validate_card':lambda card:card,'validate_text':validate_text}


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.clock=Clock()
        self.store=LiveTrialStore(self.tmp.name,now=self.clock);self.addCleanup(self.store.close);self.runtime=FakeRuntime()
    def trial(self,cards=1):
        values=[asdict(card(product_id=str(123+i),list_id=str(456+i),campaign_id=str(789+i))) for i in range(cards)]
        return self.store.create_trial('run-fixture',[source_item(cards=values)],'d'*64)
    def run_trial(self,trial,**kwargs):return run_trial(self.store,trial['trialId'],runtime_factory=self.runtime.factory,validate_item=lambda item:None,**kwargs)
    def test_unapproved_opens_no_runtime_or_network(self):
        result=self.run_trial(self.trial());self.assertEqual(result['status'],'awaiting_approval');self.assertEqual(self.runtime.opened,0)
    def test_changed_benefit_after_card_confirmation_blocks_fixed_text(self):
        trial=self.trial();self.store.approve(trial['trialId'],trial['snapshotHash']);self.runtime.fail_material=True
        result=self.run_trial(trial)
        self.assertEqual(result['status'],'blocked');self.assertEqual(result['errorCode'],'promotion_condition_changed')
        self.assertEqual(self.runtime.events,['create','card','verify-card'])
        self.assertTrue(result['trial']['items'][0]['partialDelivery'])
    def test_all_cards_confirm_before_one_fixed_text(self):
        trial=self.trial(2);self.store.approve(trial['trialId'],trial['snapshotHash']);result=self.run_trial(trial)
        self.assertEqual(result['status'],'confirmed');self.assertEqual(self.runtime.events,['create','card','verify-card','card','verify-card','text','verify-text'])
        self.assertEqual([r['state'] for r in result['trial']['items'][0]['components']],['confirmed']*3)
    def test_missing_card_readback_blocks_text_and_retry_is_read_only(self):
        trial=self.trial();self.store.approve(trial['trialId'],trial['snapshotHash']);self.runtime.unconfirmed=True
        result=self.run_trial(trial,owner='first');self.assertEqual(result['status'],'result_unknown')
        self.assertEqual(result['trial']['items'][0]['state'],'result_unknown');writes=list(self.runtime.messages)
        self.clock.advance(301);self.runtime.unconfirmed=False;result=self.run_trial(trial,owner='recovery')
        self.assertEqual(result['status'],'confirmed');self.assertEqual(self.runtime.events.count('card'),1);self.assertEqual(len(self.runtime.messages),len(writes)+1)
    def test_create_candidate_cid_is_saved_before_identity_read_failure(self):
        trial=self.trial();self.store.approve(trial['trialId'],trial['snapshotHash']);self.runtime.fail_conversation=True
        result=self.run_trial(trial,owner='first');item=result['trial']['items'][0]
        self.assertEqual(item['conversationId'],'12345');self.assertEqual(item['state'],'result_unknown');self.assertEqual(self.runtime.events,['create'])
    def test_confirmed_card_not_resent_after_restart_before_text(self):
        trial=self.trial();self.store.approve(trial['trialId'],trial['snapshotHash']);first=self.run_trial(trial,owner='first',max_steps=2)
        self.assertEqual(first['status'],'step_limit');self.assertEqual(self.runtime.events,['create','card','verify-card'])
        self.clock.advance(301);result=self.run_trial(trial,owner='second')
        self.assertEqual(result['status'],'confirmed');self.assertEqual(self.runtime.events.count('card'),1);self.assertEqual(self.runtime.events.count('text'),1)
    def test_failed_text_preserves_confirmed_card_and_never_reposts(self):
        trial=self.trial();self.store.approve(trial['trialId'],trial['snapshotHash']);self.runtime.fail_text=True
        result=self.run_trial(trial,owner='first');self.assertTrue(result['trial']['items'][0]['partialDelivery']);self.assertEqual(result['trial']['items'][0]['components'][0]['state'],'confirmed')
        self.assertEqual(result['trial']['items'][0]['state'],'result_unknown');self.assertEqual(self.runtime.events.count('card'),1)
    def test_recovery_exception_marks_old_candidate_unknown_without_new_dispatch(self):
        trial=self.trial();self.store.approve(trial['trialId'],trial['snapshotHash']);self.run_trial(trial,owner='first',max_steps=1)
        claim=self.store.claim(trial['trialId'],'first');component=claim['component']
        attempt=self.store.begin_attempt(claim['itemId'],'first',claim['fence'],'send_card','fixture-before-crash',component_id=component['componentId'])
        self.store.record_send_receipt(claim['itemId'],'first',claim['fence'],attempt['attemptId'],{'conversationId':'12345','requestRef':'fixture-before-crash','messageId':'321','accepted':True,'evidenceRef':'fixture:old-candidate'})
        self.clock.advance(301);self.runtime.fail_conversation=True
        result=self.run_trial(trial,owner='recovery')
        self.assertEqual(result['trial']['items'][0]['state'],'result_unknown');self.assertTrue(result['trial']['blockedByUnknown'])
        self.assertEqual(self.runtime.events,['create'])
        self.assertEqual(result['trial']['items'][0]['components'][0]['sendReceipt']['messageId'],'321')

if __name__=='__main__':unittest.main()
