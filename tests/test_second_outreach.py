import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.second_outreach import SecondOutreachStore,SecondOutreachError,fingerprint
from lib.outreach_drafts import validate_context,OutreachDraftError

def row(identity=True):
    r={'id':'second-it-example','sourceCreatorId':'kd-test','sourceHandle':'old_handle','sourceNamespace':'kalodata','sourceExternalId':'123',
       'currentRecipient':{'creatorId':'creator_'+'a'*32,'oecId':'987654321','handle':'current_handle','observedAt':'2026-09-12T12:00:00Z'} if identity else None,
       'products':[{'id':'source-product','pid':'1729480019490150432','title':'来源颈枕','nameIt':'cuscino cervicale','units':123,'windowStart':1787702400000,'windowEnd':1788825600000,'sourceRef':'fixture:exact-pid'}],
       'historicalOwnership':'unverified'}
    r['fingerprint']=fingerprint(r);return r

class SecondOutreachTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.rows=[row()]
        self.store=SecondOutreachStore(self.tmp.name,source=self.source,clock=lambda:'2026-09-12T12:00:00Z',promotion_facts=lambda:{'1729480019490150432':{'creatorPercent':'12','publicPercent':'10','sourceRef':'fixture:current-card','observedAt':'2026-09-12T12:00:00Z'}})
        self.addCleanup(self.store.close)
    def source(self):return {'datasetId':'italy-source','sourceFingerprint':fingerprint(self.rows),'opportunities':copy.deepcopy(self.rows)}
    def prepare(self,key='prepare-1'):
        self.store.sync();r=self.store.get(self.rows[0]['id'])
        return self.store.command(key,{'type':'prepare','opportunityId':r['id'],'expectedRevision':r['revision']})
    def request(self,p):return {'packetId':p['packetId'],'style':'friendly','instructions':'简短一些'}
    def test_import_replay_does_not_increase_revision_or_packets(self):
        self.store.sync();first=self.store.get('second-it-example');self.store.sync()
        self.assertEqual(first,self.store.get(first['id']));self.assertEqual(self.store.status()['identified'],1)
        self.assertFalse(self.store.status()['transport']['sendEnabled']);self.assertEqual(self.store.status()['realSends'],0)
    def test_missing_current_identity_is_retained_without_a_draft_packet(self):
        self.rows=[row(False)];self.store.sync()
        self.assertEqual(self.store.status()['unresolved'],1)
        self.assertEqual(self.store.list(filter='unresolved')['total'],1)
        with self.assertRaises(SecondOutreachError) as error:self.prepare()
        self.assertEqual(error.exception.code,'identity_unverified')
    def test_paid_draft_input_has_no_historical_sales_or_private_identity(self):
        packet=self.prepare();result=self.store.context(self.request(packet));facts=result['context']['modelFacts']
        validate_context(result['context'],result['contextRequest'])
        self.assertEqual(facts['recipient']['handle'],'current_handle')
        text=json.dumps(facts)
        for value in ['old_handle','987654321','1729480019490150432','units','windowStart','fixture:exact-pid']:
            self.assertNotIn(value,text)
        self.assertEqual(facts['products'][0]['sharedCategories'],[])
        self.assertEqual({f['kind'] for f in facts['facts']},{'recipient_handle','product_name'})
        self.assertEqual(result['fingerprint'],fingerprint(result['context']))
    def test_second_context_cannot_smuggle_claimed_sales_or_fake_category_alignment(self):
        packet=self.prepare();result=self.store.context(self.request(packet));context=result['context']
        context['modelFacts']['facts'].append({'id':'p1-fit','kind':'category_alignment','value':['运动']})
        with self.assertRaises(OutreachDraftError):validate_context(context,result['contextRequest'])
        result=self.store.context(self.request(packet));result['context']['binding']['historicalOwnership']='verified'
        with self.assertRaises(OutreachDraftError):validate_context(result['context'],result['contextRequest'])
    def test_source_change_preserves_old_packet_and_requires_a_new_revision(self):
        packet=self.prepare();before=self.store.db.execute('SELECT payload FROM second_packet WHERE id=?',(packet['packetId'],)).fetchone()[0]
        self.rows[0]['currentRecipient']['handle']='renamed';self.rows[0]['fingerprint']=fingerprint(self.rows[0])
        with self.assertRaises(SecondOutreachError) as error:self.store.context(self.request(packet))
        self.assertEqual(error.exception.code,'stale_context')
        self.assertEqual(before,self.store.db.execute('SELECT payload FROM second_packet WHERE id=?',(packet['packetId'],)).fetchone()[0])
        new=self.prepare('prepare-2');self.assertNotEqual(new['packetId'],packet['packetId'])
        self.assertEqual(self.store.context(self.request(new))['context']['modelFacts']['recipient']['handle'],'renamed')
    def test_pause_invalidates_prepared_packet_and_survives_source_sync(self):
        packet=self.prepare();r=self.store.get('second-it-example')
        self.store.command('pause-1',{'type':'control','opportunityId':r['id'],'expectedRevision':r['revision'],'control':'paused'})
        self.store.sync();self.assertEqual(self.store.get(r['id'])['control'],'paused')
        with self.assertRaises(SecondOutreachError):self.store.context(self.request(packet))
        with self.assertRaises(SecondOutreachError) as error:self.prepare('prepare-2')
        self.assertEqual(error.exception.code,'relationship_suppressed')
    def test_original_request_recovered_after_source_changes_without_refreezing(self):
        p=self.prepare();self.rows[0]['fingerprint']='b'*64
        same=self.store.command('prepare-1',{'type':'prepare','opportunityId':'second-it-example','expectedRevision':1})
        self.assertEqual(same,p);self.assertEqual(self.store.db.execute('SELECT count(*) FROM second_packet').fetchone()[0],1)
        with self.assertRaises(SecondOutreachError) as error:self.store.command('prepare-1',{'type':'refresh'})
        self.assertEqual(error.exception.code,'request_conflict')
    def test_two_tabs_prepare_same_version_reuses_the_original_packet(self):
        first=self.prepare('tab-one');second=self.prepare('tab-two')
        self.assertEqual(first,second)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM second_packet').fetchone()[0],1)
    def test_older_reader_cannot_commit_over_a_newer_source_observation(self):
        old=self.source();new=copy.deepcopy(old)
        new['opportunities'][0]['currentRecipient']['handle']='later_name'
        new['opportunities'][0]['fingerprint']=fingerprint(new['opportunities'][0]);new['sourceFingerprint']=fingerprint(new['opportunities'])
        older_read=threading.Event();release_older=threading.Event();newer_ready=threading.Event();start_newer=threading.Event();newer_done=threading.Event();errors=[]
        def older_source():
            older_read.set()
            if not release_older.wait(2):raise RuntimeError('test coordination timeout')
            return old
        def run_newer():
            try:
                with SecondOutreachStore(self.tmp.name,source=lambda:new) as s:
                    newer_ready.set();start_newer.wait(2);s.sync();newer_done.set()
            except Exception as error:errors.append(error)
        def run_older():
            try:
                with SecondOutreachStore(self.tmp.name,source=older_source) as s:s.sync()
            except Exception as error:errors.append(error)
        b=threading.Thread(target=run_newer);b.start();self.assertTrue(newer_ready.wait(2))
        a=threading.Thread(target=run_older);a.start();self.assertTrue(older_read.wait(2));start_newer.set()
        # In the former implementation B committed while A was still reading,
        # then A overwrote it. Serial source reads must preserve the newer state.
        newer_done.wait(.1);release_older.set();a.join(3);b.join(3)
        self.assertFalse(a.is_alive() or b.is_alive());self.assertEqual(errors,[])
        self.assertEqual(self.store.get('second-it-example')['currentRecipient']['handle'],'later_name')
    def test_removed_source_cannot_remain_actionable_and_keeps_history(self):
        p=self.prepare();other=row(False);other['id']='second-other';other['fingerprint']=fingerprint(other);self.rows=[other]
        self.store.sync()
        with self.assertRaises(SecondOutreachError):self.store.context(self.request(p))
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM second_packet').fetchone()[0],1)
    def test_reopen_and_pagination_read_original_saved_packet(self):
        p=self.prepare()
        with SecondOutreachStore(self.tmp.name,source=self.source) as reopened:
            self.assertEqual(reopened.get('second-it-example')['packetId'],p['packetId'])
            self.assertEqual(reopened.list(q='current_handle')['total'],1)
            self.assertEqual(reopened.list(offset=1)['items'],[])
            self.assertEqual(reopened.list(q='missing')['total'],0)
    def test_no_sending_or_client_fact_injection_command_exists(self):
        for command in [{'type':'send'},{'type':'prepare','opportunityId':'second-it-example','expectedRevision':1,'oecId':'wrong'},{'type':'refresh','source':{}}]:
            with self.assertRaises(SecondOutreachError):self.store.command('invalid',command)
    def test_fixed_templates_reuse_frozen_versions_without_model_calls(self):
        self.store.sync()
        def render(template,key):return self.store.command(key,{'type':'render_template','opportunityId':'second-it-example','expectedRevision':1,'templateId':template})
        with patch('lib.draft_provider.call_model',side_effect=AssertionError('No second model calls')) as model:
            one=render('it-second-brief','template-one');two=render('it-second-choice','template-two');again=render('it-second-brief','template-three')
        self.assertEqual(model.call_count,0);self.assertEqual(one,again);self.assertNotEqual(one['draftId'],two['draftId'])
        self.assertEqual(self.store.get('second-it-example')['templateDraft']['draftId'],one['draftId'])
        self.assertEqual(self.store.template_draft(one['draftId'],require_current=True)['textIt'],one['textIt'])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM second_template_draft').fetchone()[0],2)
    def test_template_snapshot_survives_rename_but_cannot_execute_as_current(self):
        self.store.sync();saved=self.store.command('template-one',{'type':'render_template','opportunityId':'second-it-example','expectedRevision':1,'templateId':'it-second-brief'})
        self.rows[0]['currentRecipient']['handle']='renamed';self.rows[0]['fingerprint']=fingerprint(self.rows[0])
        with self.assertRaises(SecondOutreachError):self.store.template_draft(saved['draftId'],require_current=True)
        self.assertEqual(self.store.template_draft(saved['draftId']),saved)

if __name__=='__main__':unittest.main()
