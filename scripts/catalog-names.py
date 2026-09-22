#!/usr/bin/env python3
"""Show and fill the catalogue short-name gap used by TapLink card names.

    python scripts/catalog-names.py status
    python scripts/catalog-names.py prepare --limit 25

``status`` only reads. ``prepare`` spends real model calls (DeepSeek) to generate the missing
names, reusing the same validated provider path as the rest of the system. It never retries a
failed call; failures are reported so the operator decides. Errors come back as JSON codes.
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'scripts'))
from lib.catalog_names import discard_invalid_pid_names, gap, prepare  # noqa: E402
from lib.cycle_materials import names_prompt  # noqa: E402
from lib.draft_provider import provider_status  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['status', 'prepare', 'discard-invalid'])
    parser.add_argument('--market', default='it')
    parser.add_argument('--limit', type=int, default=25)
    parser.add_argument('--with-progress', action='store_true')
    parser.add_argument('--all', action='store_true', help='prepare every remaining product')
    parser.add_argument('--confirm-discard-invalid', action='store_true')
    args = parser.parse_args()
    from lib.market_registry import enabled_market_keys
    if args.market not in enabled_market_keys(ROOT):
        print(json.dumps({'error':'market_not_enabled'},ensure_ascii=False))
        return 2
    progress_path=ROOT/('var/catalog-names-progress.json' if args.market=='it' else f'var/catalog-names-progress-{args.market}.json')

    try:
        if args.action=='discard-invalid':
            if not args.confirm_discard_invalid:raise ValueError('catalog_names_discard_confirmation_required')
            print(json.dumps(discard_invalid_pid_names(ROOT,args.market),ensure_ascii=False));return 0
        if args.action == 'status':
            progress = {}
            if args.with_progress and progress_path.exists():
                try:
                    progress = {'run': json.loads(progress_path.read_text(encoding='utf-8'))}
                except (OSError, ValueError):
                    progress = {}
            status = provider_status()
            print(json.dumps(gap(ROOT,args.market) | {'market':args.market,'provider': {'model': status['model'], 'ready': status['ready'],
                                                       'endpointHost': status['endpointHost'],
                                                       'pricing': status['pricing']},
                                         'batchLimit': 5,
                                         'prompt': names_prompt(args.market),
                                         'promptRole': 'system'} | progress, ensure_ascii=False))
            return 0
        # Progress is written per batch so the page can show a real bar, not an optimistic spinner.
        def progress(state):
            progress_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = progress_path.with_suffix('.json.tmp')
            temporary.write_text(json.dumps({'running': True, 'startedAt': started} | state,
                                            ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            temporary.replace(progress_path)

        started = time.time()
        report = {'requested':0,'prepared':0,'modelCalls':0,'errors':[],'cost':None,'total':0}
        progress({'prepared': 0, 'total': args.limit, 'modelCalls': 0, 'errors': []})
        try:
            limit = gap(ROOT,args.market)['missing'] if args.all else args.limit
            progress({'prepared': 0, 'total': limit, 'modelCalls': 0, 'errors': []})
            if args.all and limit==0:
                report|={'stopped':'nothing_missing'}
            else:
                report = prepare(ROOT,limit,market=args.market,on_progress=progress,all_missing=args.all)
        finally:
            # The bar must never keep moving after the work stopped.
            progress_path.write_text(json.dumps({'running': False, 'finishedAt': time.time(),
                                            'prepared': report.get('prepared', 0),
                                            'total': report.get('total', 0),
                                            'modelCalls': report.get('modelCalls', 0),
                                            'cost': report.get('cost'),
                                            'errors': report.get('errors', [])},
                                           ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(gap(ROOT,args.market) | {'market':args.market} | report, ensure_ascii=False))
        return 0
    except ValueError as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
