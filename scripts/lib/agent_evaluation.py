"""Read-only V2 synthetic evaluation; no business ledger or transport writes.

References are authored from PROJECT/guide policy before model execution. Results
are contract/heuristic checks, never production accuracy or an LLM self-grade.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from types import SimpleNamespace

from lib.agent_reply_v2 import prompt, validate_decision
from lib.draft_provider import MODEL, DraftProviderError
from lib.second_cycle import CycleError, digest, encoded

MARKETS = ('it', 'br', 'my', 'uk')
LANGUAGE_HINTS = {
    'it': r'\b(grazie|puoi|per|il|la|non|che|tuo|ricevuto|certo|perfetto|va bene)\b',
    'br': r'\b(obrigad[oa]|você|pode|para|não|com|seu|sua|recebido|claro|perfeito)\b',
    'my': r'\b(terima kasih|anda|boleh|untuk|yang|kami|sila|saya|baik|akan|sudah)\b',
    'uk': r'\b(thank|thanks|you|your|can|the|we|will|please|great|sure)\b',
}
REMINDER_HINTS = {
    'it': r'(aggiung\w*|inser\w*).{0,80}(vetrina|showcase)',
    'br': r'(adicion\w*|coloc\w*).{0,80}(vitrine|showcase)',
    'my': r'(tambah\w*|masuk\w*).{0,80}(pameran|showcase)',
    'uk': r'add\w*.{0,80}showcase',
}

NEAR_TERM_HINTS = {
    'it': r'\b(a breve|al più presto)\b',
    'br': r'\b(em breve|o quanto antes|já já)\b',
    'my': r'\b(sebentar lagi|tidak lama lagi|secepat mungkin)\b',
    'uk': r'\b(shortly|soon|as soon as possible)\b',
}


def has_showcase_reminder(market, body):
    """Screen addition suggestions without counting thanks for a completed add.

    Remove only the acknowledged action through its showcase noun, retaining
    any following request, including requests in the same sentence. This is
    still a lexical screen, not a substitute for contextual semantic review.
    """
    acknowledgements = {
        'uk': r'\b(?:thanks|thank you)\s+for\s+(?:confirming\s*[-—,]?\s*and\s+for\s+)?(?:already\s+)?adding\b[^.!?;\n]{0,80}?\bshowcase\b',
        'br': r'\bobrigad[oa]\s+por\s+(?:ter\s+)?(?:adicionar|adicionado)\b[^.!?;\n]{0,80}?\b(?:vitrine|showcase)\b',
    }
    pattern = acknowledgements.get(market)
    if pattern:
        request_markers = (r'\b(?:please|add|you can|could you)\b' if market == 'uk'
                           else r'\b(?:pode|adicione|coloque)\b')
        body = re.sub(pattern, lambda match: match.group(0) if re.search(
            request_markers, match.group(0), re.I) else '', body, flags=re.I)
    return bool(re.search(REMINDER_HINTS[market], body, re.I | re.S))


def load_cases(path):
    suite = json.loads(Path(path).read_text(encoding='utf-8'))
    if suite.get('schemaVersion') != 'agent-v2-eval-cases-1' or suite.get('dataKind') != 'synthetic':
        raise ValueError('evaluation_suite_invalid')
    cases = suite.get('cases', [])
    ids = set()
    for case in cases:
        if case['id'] in ids or case['market'] not in MARKETS:
            raise ValueError('evaluation_case_invalid')
        ids.add(case['id'])
        if case['context']['market'] != case['market'] or not case['reference']['reviewChecklistZh']:
            raise ValueError('evaluation_reference_invalid')
        if not case['reference']['routes'] or set(case['reference']['routes']) - {'reply','no_reply','request_detail','handoff'}:
            raise ValueError('evaluation_reference_invalid')
    if not cases:
        raise ValueError('evaluation_suite_empty')
    return suite


def prompt_spec(root, market, guide_db=None):
    """Use the production prompt verbatim, with optional active guide read-only."""
    if guide_db is not None:
        uri = Path(guide_db).resolve().as_uri() + '?mode=ro'
        with closing(sqlite3.connect(uri, uri=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA query_only=ON')
            plans = db.execute('SELECT id FROM plan WHERE market=?', (market,)).fetchall()
            if len(plans) != 1:
                raise ValueError('evaluation_market_plan_ambiguous')
            return {**prompt(root, SimpleNamespace(db=db), plans[0]['id'], market),
                    'guideSource': 'active_readonly'}
    # Only the prompt schema/guide lookup is needed; never initialize CycleStore.
    with closing(sqlite3.connect(':memory:')) as db:
        db.row_factory = sqlite3.Row
        db.execute('CREATE TABLE agent_reply_guide_revision(plan_id,revision,body,content_hash,created_at)')
        db.execute('CREATE TABLE agent_reply_decision_v2(decision_id)')
        return {**prompt(root, SimpleNamespace(db=db), 'synthetic-evaluation', market),
                'guideSource': 'repository_file'}


def assess(case, raw):
    checks = []
    def check(name, passed, kind='contract'):
        checks.append({'name': name, 'passed': bool(passed), 'kind': kind})
    try:
        decision = validate_decision(raw, case['context'])
    except (CycleError, TypeError, ValueError, KeyError) as error:
        check('production_schema_and_evidence', False)
        checks[-1]['errorCode'] = str(error) if isinstance(error, CycleError) else type(error).__name__
        return checks
    check('production_schema_and_evidence', True)
    reference = case['reference']
    check('reference_route', decision['route'] in reference['routes'])
    check('reference_wait_for', decision['waitFor'] in reference['waitFor'])
    latest = next(row['id'] for row in reversed(case['context']['messages']) if row['direction'] == 'inbound')
    check('current_inbound_evidence', latest in decision['evidenceMessageIds'])
    body = decision['replyText'] or ''
    check('no_chinese_in_recipient_reply', not re.search(r'[\u3400-\u9fff]', body))
    # Language and business meanings need human review even when hints pass.
    check('market_language_hint', not body or re.search(LANGUAGE_HINTS[case['market']], body, re.I), 'heuristic')
    if reference.get('forbidNearTermCommitment') and body:
        check('unsupported_near_term_hint', not re.search(
            NEAR_TERM_HINTS[case['market']], body, re.I), 'heuristic')
    reminder = reference.get('showcaseReminder')
    if reminder in ('required', 'forbidden') and body:
        found = has_showcase_reminder(case['market'], body)
        check('showcase_reminder_hint', found if reminder == 'required' else not found, 'heuristic')
    return checks


def evaluate_case(case, spec, call):
    request = [{'role': 'system', 'content': spec['system']},
               {'role': 'user', 'content': encoded(case['context'])}]
    row = {'caseId': case['id'], 'scenario': case['scenario'], 'market': case['market'],
           'guideRevision': spec['guideRevision'], 'guideHash': spec['guideHash'],
           'guideSource': spec['guideSource'], 'provider': spec['provider'], 'model': MODEL,
           'endpoint': spec['endpoint'], 'inputHash': digest(request), 'input': request,
           'reference': case['reference'], 'semanticReview': 'pending_human_review',
           'platformWrites': 0}
    try:
        response = call(request, max_output_tokens=1200)
        row['receipt'] = {key: value for key, value in response.items() if key != 'content'}
        row['rawOutput'] = response['content']
        row['output'] = json.loads(response['content'])
        row['checks'] = assess(case, row['output'])
        row['status'] = 'checks_passed' if all(item['passed'] for item in row['checks']) else 'checks_failed'
    except (DraftProviderError, ValueError, KeyError, TypeError) as error:
        row.update(status='model_error', errorCode=getattr(error, 'code', type(error).__name__), checks=[])
        if isinstance(error, DraftProviderError):
            row['receipt'] = error.receipt
    return row


def summarize(results):
    return {'cases': len(results),
            'checksPassed': sum(row['status'] == 'checks_passed' for row in results),
            'checksFailed': sum(row['status'] == 'checks_failed' for row in results),
            'modelErrors': sum(row['status'] == 'model_error' for row in results),
            'pendingHumanReview': len(results), 'platformWrites': 0,
            'productionAccuracy': None,
            'interpretationZh': '合成案例合同及启发式筛查结果；语义人工复核未完成，不代表真实准确率。'}


def write_report(directory, suite, results, planned_case_ids=None):
    directory = Path(directory)
    planned = planned_case_ids if planned_case_ids is not None else [case['id'] for case in suite['cases']]
    completed = {row['caseId'] for row in results}
    summary = summarize(results)
    summary.update(plannedCases=len(planned), unexecutedCaseIds=[key for key in planned if key not in completed])
    summary['complete'] = not summary['unexecutedCaseIds']
    report = {'schemaVersion': 'agent-v2-eval-report-1', 'createdAt': datetime.now(timezone.utc).isoformat(),
              'suiteHash': digest(suite), 'dataKind': 'synthetic', 'summary': summary, 'results': results}
    (directory / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    lines = ['# V2 合成评测', '', report['summary']['interpretationZh'], '',
             f"计划 {len(planned)}，已执行 {len(results)}，未执行 {len(summary['unexecutedCaseIds'])}；筛查通过 {report['summary']['checksPassed']}；筛查异常 {report['summary']['checksFailed']}；模型错误 {report['summary']['modelErrors']}。",
             '全部案例仍需人工复核业务含义、语气、完整性和市场语言；未执行任何业务平台写入。', '',
             '| 案例 | 市场 | route | 筛查异常 | 人工复核 |', '|---|---|---|---|---|']
    for row in results:
        failures = ', '.join(item['name'] for item in row['checks'] if not item['passed']) or row.get('errorCode', '无')
        lines.append(f"| {row['caseId']} | {row['market']} | {row.get('output',{}).get('route','—')} | {failures} | 待复核 |")
    lines.extend(['', '逐项参考行为、完整输入输出、指南 hash、模型回执见 report.json。'])
    (directory / 'report.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    return report
