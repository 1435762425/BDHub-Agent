"""Batch template freezing uses fixture sources, never models or IM transport."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from lib.second_outreach import SecondOutreachStore,SecondOutreachError,fingerprint


def opportunity(name,oec='100',*,identified=True):
    value={'id':'second-it-'+name,'sourceCreatorId':'source-'+name,'sourceHandle':'source_'+name,
        'sourceNamespace':'kalodata','sourceExternalId':name,'recipientStatus':'verified' if identified else 'unresolved',
        'currentRecipient':{'creatorId':'creator_'+hashlib.sha256(oec.encode()).hexdigest()[:32],'oecId':oec,'handle':'recipient_'+oec,'observedAt':'2026-09-12T12:00:00Z'} if identified else None,
        'products':[{'id':'source-product','pid':'1729480019490150432','title':'来源颈枕','nameIt':'cuscino cervicale','units':123,
                     'windowStart':1,'windowEnd':2,'sourceRef':'fixture:exact-pid'}],'historicalOwnership':'unverified'}
    value['fingerprint']=fingerprint(value);return value


class SecondTemplateBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.rows=[opportunity('one','100'),opportunity('two','200'),opportunity('three','300')]
        self.reads=0
        self.store=SecondOutreachStore(self.temp.name,source=self.source,clock=lambda:'2026-09-12T12:00:00Z',promotion_facts=lambda:{'1729480019490150432':{'creatorPercent':'12','publicPercent':'10','sourceRef':'fixture:current-card','observedAt':'2026-09-12T12:00:00Z'}})
        self.addCleanup(self.store.close);self.store.sync()

    def source(self):
        self.reads+=1
        return {'datasetId':'italy-fixture','sourceFingerprint':fingerprint(self.rows),'opportunities':deepcopy(self.rows)}

    def command(self,ids=None,**changes):
        return {'type':'render_batch','opportunityIds':ids or [row['id'] for row in self.rows],
                'templateId':'it-second-brief','expectedSourceFingerprint':self.store.status()['sourceFingerprint'],**changes}

    def test_only_explicit_unique_ids_are_prepared_with_zero_models(self):
        command=self.command([self.rows[1]['id'],self.rows[1]['id']])
        with patch('socket.socket',side_effect=AssertionError('no network')),patch('lib.draft_provider.call_model',side_effect=AssertionError('no model')) as model:
            result=self.store.command('batch-one',command)
        self.assertEqual(result['modelCalls'],0);model.assert_not_called()
        self.assertEqual([row['opportunityId'] for row in result['prepared']],[self.rows[1]['id']]);self.assertEqual(result['skipped'],[])
        self.assertTrue(result['batchId'].startswith('template-batch_'));self.assertEqual(result['sourceFingerprint'],command['expectedSourceFingerprint'])
        self.assertTrue(result['prepared'][0]['executionBlocked']);self.assertEqual(self.store.db.execute('SELECT count(*) FROM second_template_draft').fetchone()[0],1)
        self.assertIsNone(self.store.get(self.rows[0]['id'])['templateDraft'])

    def test_paused_unverified_removed_missing_and_duplicate_recipients_are_skipped(self):
        active=opportunity('active','100');duplicate=opportunity('duplicate','100');paused=opportunity('paused','200');unverified=opportunity('unverified','300',identified=False);removed=opportunity('removed','400')
        self.rows=[active,duplicate,paused,unverified,removed];self.store.sync()
        self.store.command('pause',{'type':'control','opportunityId':paused['id'],'expectedRevision':1,'control':'paused'})
        self.rows.remove(removed);self.store.sync()
        selected=[active['id'],duplicate['id'],paused['id'],unverified['id'],removed['id'],'second-it-missing']
        result=self.store.command('batch',self.command(selected))
        self.assertEqual(len(result['prepared']),1)
        reasons={row['opportunityId']:row['reason'] for row in result['skipped']}
        self.assertEqual(reasons,{duplicate['id']:'duplicate_recipient',paused['id']:'relationship_suppressed',unverified['id']:'identity_unverified',removed['id']:'source_inactive','second-it-missing':'opportunity_not_found'})
        self.assertEqual(self.store.get(paused['id'])['control'],'paused')

    def test_unrenderable_row_does_not_reserve_recipient_or_block_other_rows(self):
        invalid=opportunity('bad','100');invalid['products'][0]['nameIt']='unverified quote 20%';invalid['fingerprint']=fingerprint(invalid)
        valid=opportunity('good','100');self.rows=[invalid,valid];self.store.sync()
        result=self.store.command('batch',self.command())
        self.assertEqual([row['opportunityId'] for row in result['prepared']],[valid['id']])
        self.assertEqual(result['skipped'],[{'opportunityId':invalid['id'],'reason':'template_product_name_unverified'}])

    def test_whole_source_fingerprint_is_checked_after_sync_before_any_freeze(self):
        stale=self.command();self.rows.append(opportunity('new','400'))
        with self.assertRaises(SecondOutreachError) as found:self.store.command('stale',stale)
        self.assertEqual(found.exception.code,'source_conflict');self.assertEqual(found.exception.status,409)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM second_template_draft').fetchone()[0],0)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM second_command WHERE id=?',('stale',)).fetchone()[0],0)

    def test_source_change_between_sync_and_command_transaction_is_rejected(self):
        command=self.command();original_sync=self.store.sync
        def intervening_sync():
            value=original_sync();new_rows=deepcopy(self.rows);new_rows.append(opportunity('intervening','700'))
            with SecondOutreachStore(self.temp.name,source=lambda:{'sourceFingerprint':fingerprint(new_rows),'opportunities':new_rows}) as other:other.sync()
            return value
        with patch.object(self.store,'sync',side_effect=intervening_sync),self.assertRaises(SecondOutreachError) as found:self.store.command('intervening',command)
        self.assertEqual(found.exception.code,'source_conflict');self.assertEqual(self.store.db.execute('SELECT count(*) FROM second_template_draft').fetchone()[0],0)

    def test_request_recovery_returns_original_batch_without_sync_or_overwriting_new_selection(self):
        command=self.command();first=self.store.command('same-request',command)
        selected=self.store.get(self.rows[0]['id']);newer=self.store.command('choose-another',{'type':'render_template','opportunityId':selected['id'],'expectedRevision':selected['revision'],'templateId':'it-second-choice'})
        before=self.reads;self.rows[0]['currentRecipient']['handle']='renamed';self.rows[0]['fingerprint']=fingerprint(self.rows[0])
        recovered=self.store.command('same-request',command)
        self.assertEqual(recovered,first);self.assertEqual(self.reads,before)
        self.assertEqual(self.store.get(selected['id'])['templateDraft']['draftId'],newer['draftId'])
        with self.assertRaises(SecondOutreachError) as found:self.store.command('same-request',{**command,'templateId':'it-second-choice'})
        self.assertEqual(found.exception.code,'request_conflict')

    def test_new_request_for_same_content_reuses_drafts_and_whole_batch_id(self):
        command=self.command();first=self.store.command('first',command);count=self.store.db.execute('SELECT count(*) FROM second_template_draft').fetchone()[0]
        again=self.store.command('second',command)
        self.assertEqual(first,again);self.assertEqual(self.store.db.execute('SELECT count(*) FROM second_template_draft').fetchone()[0],count)
        with SecondOutreachStore(self.temp.name,source=self.source) as reopened:self.assertEqual(reopened.command('first',command),first)

    def test_single_and_batch_render_share_frozen_draft_and_template_selection(self):
        row=self.store.get(self.rows[0]['id']);single=self.store.command('single',{'type':'render_template','opportunityId':row['id'],'expectedRevision':row['revision'],'templateId':'it-second-brief'})
        result=self.store.command('batch',self.command([row['id']]))
        self.assertEqual(result['prepared'],[single]);self.assertEqual(self.store.get(row['id'])['templateDraft']['draftId'],single['draftId'])

    def test_fatal_store_failure_rolls_back_all_drafts_selections_and_batch_receipt(self):
        original=self.store._render_template;count=0
        def fail_second(*args):
            nonlocal count;count+=1
            if count==2:raise RuntimeError('injected local store failure')
            return original(*args)
        with patch.object(self.store,'_render_template',side_effect=fail_second),self.assertRaises(RuntimeError):self.store.command('fatal',self.command())
        for table in ('second_template_draft','second_template_selection','second_command'):
            self.assertEqual(self.store.db.execute('SELECT count(*) FROM '+table).fetchone()[0],0)

    def test_batch_request_is_bounded_and_cannot_inject_source_or_recipient(self):
        for changes in ({'opportunityIds':[]},{'opportunityIds':['x']*101},{'opportunityIds':'x'},{'opportunityIds':[{}]},{'expectedSourceFingerprint':'old'},{'oecId':'999'},{'source':{}},{'templateId':None}):
            before=self.reads
            with self.assertRaises(SecondOutreachError):self.store.command('invalid',self.command(**changes))
            self.assertEqual(self.reads,before)
        with self.assertRaises(SecondOutreachError) as found:self.store.command('unknown-template',self.command(templateId='not-a-template'))
        self.assertEqual(found.exception.code,'template_unknown')

    def test_duplicate_id_normalization_is_idempotent_but_recipient_order_is_explicit(self):
        first=self.rows[0]['id'];result=self.store.command('dedup',self.command([first,first]))
        self.assertEqual(self.store.command('dedup',self.command([first])),result)
        duplicate=opportunity('same-person','100');self.rows.append(duplicate);self.store.sync()
        result=self.store.command('ordered',self.command([duplicate['id'],first]))
        self.assertEqual(result['prepared'][0]['opportunityId'],duplicate['id']);self.assertEqual(result['skipped'][0]['opportunityId'],first)

    def test_maximum_hundred_explicit_fixture_targets_are_frozen_once(self):
        self.rows=[opportunity('fixture-'+str(index),str(1000+index)) for index in range(100)];self.store.sync()
        with patch('lib.draft_provider.call_model',side_effect=AssertionError('no model')) as model:
            result=self.store.command('hundred',self.command())
        self.assertEqual(len(result['prepared']),100);self.assertEqual(result['skipped'],[]);model.assert_not_called()
        self.assertEqual(len({row['recipient']['oecId'] for row in result['prepared']}),100)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM second_template_draft').fetchone()[0],100)


if __name__=='__main__':unittest.main()
