#!/usr/bin/env python3
"""Inspect Agent contract, version its guide and simulate without platform writes."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'scripts'))

from lib.agent_reply_v2 import (authorize_rollout,generate, guide, prompt, provider_status,
                                production_context, save_guide, simulation_context)
from lib.market_content import market_content
from lib.second_cycle import CycleError, CycleStore


def main():
    p = argparse.ArgumentParser()
    p.add_argument('action', choices=('status', 'save-guide', 'simulate', 'replay', 'trace','start-first','resume-full'))
    p.add_argument('--market', required=True)
    p.add_argument('--turn-id')
    p.add_argument('--decision-id')
    args = p.parse_args()
    try:
        content = market_content(ROOT, args.market)
        readonly = args.action in ('status', 'trace')
        with CycleStore(ROOT/'var/second-cycle.sqlite', readonly=readonly) as store:
            plan = store.db.execute("SELECT id FROM plan WHERE market=? AND institution='bjn-local-research'",
                                    (args.market,)).fetchone()
            if not plan:
                raise CycleError('plan_missing')
            plan = plan[0]
            if args.action == 'status':
                if args.turn_id or args.decision_id:
                    raise CycleError('agent_query_invalid')
                result = {'guide': guide(ROOT, store, plan), 'connection': provider_status(ROOT, store, plan, args.market),
                          'compiledPrompt': prompt(ROOT, store, plan, args.market)['system']}
            elif args.action == 'trace':
                if not args.decision_id or args.turn_id:
                    raise CycleError('agent_query_invalid')
                row = store.db.execute('SELECT * FROM agent_reply_decision_v2 WHERE decision_id=? AND plan_id=?',
                                       (args.decision_id, plan)).fetchone()
                if not row:
                    raise CycleError('agent_trace_missing')
                result = {'decisionId': row['decision_id'], 'state': row['state'], 'market': row['market'],
                          'mode': row['mode'], 'model': row['model'], 'provider': row['provider'],
                          'guideRevision': row['guide_revision'], 'input': json.loads(row['input_json']),
                          'decision': json.loads(row['output_json']) if row['output_json'] else None,
                          'serviceReplyId': row['service_reply_id'], 'createdAt': row['created_at']}
            elif args.action == 'replay':
                if not args.turn_id or args.decision_id:
                    raise CycleError('agent_query_invalid')
                context = production_context(ROOT, store, plan, args.market, args.turn_id)
                result = generate(ROOT, store, plan, args.market, context, mode='simulation')
            else:
                raw = sys.stdin.read(50001)
                if len(raw.encode()) > 50000:
                    raise CycleError('agent_input_too_large')
                request = json.loads(raw)
                if args.action in ('start-first','resume-full'):
                    if set(request) != {'requestId'}:
                        raise CycleError('agent_input_invalid')
                    result = authorize_rollout(store,plan,args.market,
                                               'pilot' if args.action=='start-first' else 'full',
                                               request['requestId'])
                elif args.action == 'save-guide':
                    if set(request) != {'expectedRevision', 'body'}:
                        raise CycleError('agent_input_invalid')
                    result = save_guide(ROOT, store, plan, request['expectedRevision'], request['body'])
                else:
                    if set(request) != {'history'} or args.turn_id or args.decision_id:
                        raise CycleError('agent_input_invalid')
                    context = simulation_context(args.market, content['locale'], request['history'])
                    result = generate(ROOT, store, plan, args.market, context)
            result = {**result, 'platformWrites': 0, 'realSends': 0}
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as error:
        print(json.dumps({'error': str(error) if isinstance(error, CycleError)
                          else 'agent_reply_unavailable'}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
