#!/usr/bin/env python3
"""Evaluate synthetic V2 policy cases; default previews without a model call."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from lib.agent_evaluation import evaluate_case, load_cases, prompt_spec, summarize, write_report
from lib.draft_provider import call_model


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, default=root/'config/evals/agent-v2-multiturn.json')
    parser.add_argument('--guide-db', type=Path, help='Read active guide via SQLite mode=ro; default repository guide')
    parser.add_argument('--market', choices=('it','br','my','uk'))
    parser.add_argument('--call-model', action='store_true', help='One DeepSeek call per synthetic case; no automatic retries')
    parser.add_argument('--output', type=Path, help='New directory strictly under project outputs/')
    args = parser.parse_args()
    suite = load_cases(args.cases)
    cases = [case for case in suite['cases'] if not args.market or case['market'] == args.market]
    specs = {market: prompt_spec(root, market, args.guide_db) for market in sorted({case['market'] for case in cases})}
    if not args.call_model:
        print(json.dumps({'mode':'preview', 'cases':len(cases), 'markets':list(specs),
                          'guideHashes':{key:row['guideHash'] for key,row in specs.items()}, 'modelCalls':0,'platformWrites':0}, ensure_ascii=False))
        return 0
    directory = (args.output or root/'outputs'/('agent-v2-eval-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))).resolve()
    if not directory.is_relative_to((root/'outputs').resolve()) or directory == (root/'outputs').resolve():
        parser.error('output must be a new directory under project outputs/')
    directory.mkdir(parents=True, exist_ok=False)
    results = []
    try:
        with (directory/'results.jsonl').open('x', encoding='utf-8') as stream:
            for case in cases:
                result = evaluate_case(case, specs[case['market']], call_model)
                results.append(result)
                stream.write(json.dumps(result,ensure_ascii=False)+'\n'); stream.flush()
                print(json.dumps({'case':case['id'],'status':result['status']},ensure_ascii=False),flush=True)
    finally:
        write_report(directory, suite, results, [case['id'] for case in cases])
    print(json.dumps({'report':str(directory/'report.json'),**summarize(results)},ensure_ascii=False))
    return 1 if any(row['status'] != 'checks_passed' for row in results) else 0


if __name__ == '__main__':
    raise SystemExit(main())
