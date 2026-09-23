import copy
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from lib.agent_evaluation import assess, has_showcase_reminder, evaluate_case, load_cases, prompt_spec, summarize, write_report
from lib.draft_provider import DraftProviderError
from lib.second_cycle import digest


class AgentEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.suite = load_cases(ROOT/'config/evals/agent-v2-multiturn.json')
        self.case = next(case for case in self.suite['cases'] if case['id']=='uk-affirmative_without_showcase')

    def decision(self):
        return {'schemaVersion':'reply-decision-v2', 'route':'reply', 'intentCodes':['collaboration_ack'],
                'evidenceMessageIds':['sim-3'],
                'replyText':'Thanks! You can add the card above to your showcase; commission applies to your next video or live stream.',
                'meaningZh':'确认并提醒加橱窗', 'reasonCode':'collaboration_ack', 'reasonSummaryZh':'有真实商品卡，明确同意',
                'waitFor':'none', 'handoffReason':None}

    def test_four_markets_have_same_multiturn_scenarios_and_explicit_references(self):
        grouped = {market:{case['scenario'] for case in self.suite['cases'] if case['market']==market}
                   for market in ('it','br','my','uk')}
        self.assertGreaterEqual(len(self.suite['cases']), 28)
        self.assertTrue(all(value == grouped['it'] for value in grouped.values()))
        for name in ('already_reminded','institution_answered','waiting_contact_thanks','waiting_clarification_thanks'):
            self.assertIn(name, grouped['it'])
        self.assertEqual(self.suite['referenceStatus'],'policy_derived_requires_human_review')

    def test_unseen_or_stale_evidence_cannot_pass(self):
        for evidence in (['invented-message'], ['sim-2']):
            raw = self.decision(); raw['evidenceMessageIds']=evidence
            self.assertFalse(all(check['passed'] for check in assess(self.case, raw)))
        raw=self.decision(); raw['route']='no_reply'; raw['replyText']=None
        self.assertFalse(next(check['passed'] for check in assess(self.case,raw) if check['name']=='reference_route'))

    def test_language_and_reminder_are_only_heuristics_not_semantic_truth(self):
        checks=assess(self.case,self.decision())
        self.assertTrue(all(check['passed'] for check in checks))
        self.assertEqual(next(check['kind'] for check in checks if check['name']=='market_language_hint'),'heuristic')
        raw=self.decision();raw['replyText']='谢谢你。'
        self.assertFalse(next(check['passed'] for check in assess(self.case,raw) if check['name']=='no_chinese_in_recipient_reply'))
        raw=self.decision();raw['replyText']='Thanks, looking forward to it!'
        self.assertFalse(next(check['passed'] for check in assess(self.case,raw) if check['name']=='showcase_reminder_hint'))

    def test_thanks_for_completed_showcase_add_is_not_a_reminder(self):
        for market, body in (
            ('br', 'Que ótimo, obrigada por adicionar o produto à vitrine!'),
            ('uk', "That's great, thank you for adding it to your showcase."),
            ('uk', "Thank you for confirming — and for adding it to your showcase, that's brilliant."),
        ):
            with self.subTest(market=market):
                self.assertFalse(has_showcase_reminder(market, body))

    def test_thanks_does_not_hide_a_following_showcase_request(self):
        for market, body in (
            ('uk', 'Thanks! Please add this card to your showcase.'),
            ('it', 'Grazie! Puoi aggiungere la card al tuo showcase.'),
            ('uk', 'Thank you for adding that and please add this card to your showcase.'),
            ('uk', 'Thank you for adding it to your showcase and please add this card to your showcase too.'),
            ('br', 'Obrigada! Pode adicionar o card à vitrine.'),
            ('br', 'Obrigada por adicionar o produto à vitrine e pode adicionar o próximo ao seu showcase.'),
            ('br', 'Assim que enviarmos a ficha do produto, é só adicionar ao seu showcase.'),
        ):
            with self.subTest(body=body):
                self.assertTrue(has_showcase_reminder(market, body))

    def test_unconfirmed_near_term_promises_are_review_findings_in_each_market(self):
        for market, body in (
            ('uk', "Thanks! I'll send product details shortly."),
            ('br', 'Obrigado, vamos retornar em breve.'),
            ('it', 'Grazie, risponderemo a breve.'),
            ('my', 'Terima kasih, kami akan balas sebentar lagi.'),
        ):
            case=copy.deepcopy(self.case);case['market']=market;case['context']['market']=market
            raw=self.decision();raw['replyText']=body
            check=next(item for item in assess(case,raw) if item['name']=='unsupported_near_term_hint')
            self.assertFalse(check['passed']);self.assertEqual(check['kind'],'heuristic')
        checks=assess(self.case,self.decision())
        self.assertTrue(next(item['passed'] for item in checks if item['name']=='unsupported_near_term_hint'))

    def test_future_card_condition_does_not_bypass_no_card_reference(self):
        case=next(item for item in self.suite['cases'] if item['id']=='br-no_real_card')
        raw=self.decision();raw['evidenceMessageIds']=['sim-1']
        raw['replyText']='Obrigado! Assim que enviarmos a ficha do produto, é só adicionar ao seu showcase.'
        checks=assess(case,raw)
        self.assertFalse(next(item['passed'] for item in checks if item['name']=='showcase_reminder_hint'))

    def test_active_guide_read_does_not_mutate_database_and_requires_exact_market_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'real-state.sqlite'
            body=(ROOT/'config/agent-reply-guide-v2.txt').read_text().strip()+'\n测试生效指南。'
            db=sqlite3.connect(path)
            db.execute('CREATE TABLE plan(id,market)')
            db.execute('INSERT INTO plan VALUES(?,?)',('plan-it','it'))
            db.execute('CREATE TABLE agent_reply_guide_revision(plan_id,revision,body,content_hash,created_at)')
            db.execute('CREATE TABLE agent_reply_decision_v2(decision_id)')
            db.execute('INSERT INTO agent_reply_guide_revision VALUES(?,?,?,?,?)',('plan-it',7,body,digest(body),123))
            db.commit();db.close()
            before=path.read_bytes()
            spec=prompt_spec(ROOT,'it',path)
            self.assertEqual(spec['guideRevision'],7)
            self.assertEqual(spec['guideHash'],digest(body))
            self.assertIn(body,spec['system'])
            self.assertEqual(before,path.read_bytes())
            with self.assertRaisesRegex(ValueError,'plan_ambiguous'):
                prompt_spec(ROOT,'uk',path)
            with self.assertRaises(sqlite3.OperationalError):
                prompt_spec(ROOT,'it',Path(tmp)/'does-not-exist.sqlite')
            self.assertFalse((Path(tmp)/'does-not-exist.sqlite').exists())

    def test_model_output_cannot_rewrite_reference_or_complete_human_review(self):
        case=copy.deepcopy(self.case);before=copy.deepcopy(case)
        calls=[]
        def call(messages,**kwargs):
            calls.append(messages)
            return {'content':json.dumps(self.decision()),'model':'deepseek-flash','responseId':'synthetic-test'}
        spec=prompt_spec(ROOT,'uk')
        with patch('lib.agent_reply_v2.generate',side_effect=AssertionError('must not write model ledger')):
            row=evaluate_case(case,spec,call)
        self.assertEqual(len(calls),1)
        self.assertEqual(case,before)
        self.assertEqual(row['status'],'checks_passed')
        self.assertEqual(row['semanticReview'],'pending_human_review')
        self.assertEqual(row['inputHash'],digest(calls[0]))
        self.assertIsNone(summarize([row])['productionAccuracy'])
        self.assertEqual(summarize([row])['pendingHumanReview'],1)
        with tempfile.TemporaryDirectory() as tmp:
            report=write_report(tmp,self.suite,[row])
            self.assertEqual(json.loads((Path(tmp)/'report.json').read_text())['results'][0]['output'],self.decision())
            self.assertEqual(report['summary']['platformWrites'],0)
            self.assertFalse(report['summary']['complete'])
            self.assertEqual(len(report['summary']['unexecutedCaseIds']),47)

    def test_provider_failure_is_recorded_without_retry(self):
        calls=[]
        def failed(*args,**kwargs):
            calls.append(1)
            raise DraftProviderError('provider_timeout',outcome='outcome_unknown',receipt={'responseId':None})
        row=evaluate_case(self.case,prompt_spec(ROOT,'uk'),failed)
        self.assertEqual(len(calls),1)
        self.assertEqual(row['status'],'model_error')
        self.assertEqual(row['errorCode'],'provider_timeout')
        self.assertEqual(summarize([row])['checksPassed'],0)
        self.assertEqual(summarize([row])['modelErrors'],1)


if __name__=='__main__':
    unittest.main()
