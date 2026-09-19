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
PROGRESS = ROOT / 'var/catalog-names-progress.json'
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'scripts'))
from lib.catalog_names import gap, prepare  # noqa: E402
from lib.cycle_materials import NAMES_SYSTEM_PROMPT  # noqa: E402
from lib.draft_provider import provider_status  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['status', 'prepare'])
    parser.add_argument('--limit', type=int, default=25)
    parser.add_argument('--with-progress', action='store_true')
    parser.add_argument('--all', action='store_true', help='prepare every remaining product')
    args = parser.parse_args()

    try:
        if args.action == 'status':
            progress = {}
            if args.with_progress and PROGRESS.exists():
                try:
                    progress = {'run': json.loads(PROGRESS.read_text(encoding='utf-8'))}
                except (OSError, ValueError):
                    progress = {}
            status = provider_status()
            print(json.dumps(gap(ROOT) | {'provider': {'model': status['model'], 'ready': status['ready'],
                                                       'endpointHost': status['endpointHost'],
                                                       'pricing': status['pricing']},
                                         'batchLimit': 5,
                                         'prompt': NAMES_SYSTEM_PROMPT,
                                         'promptRole': 'system'} | progress, ensure_ascii=False))
            return 0
        # Progress is written per batch so the page can show a real bar, not an optimistic spinner.
        def progress(state):
            PROGRESS.parent.mkdir(parents=True, exist_ok=True)
            temporary = PROGRESS.with_suffix('.json.tmp')
            temporary.write_text(json.dumps({'running': True, 'startedAt': started} | state,
                                            ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            temporary.replace(PROGRESS)

        started = time.time()
        progress({'prepared': 0, 'total': args.limit, 'modelCalls': 0, 'errors': []})
        try:
            limit = gap(ROOT)['missing'] if args.all else args.limit
            progress({'prepared': 0, 'total': limit, 'modelCalls': 0, 'errors': []})
            report = prepare(ROOT, limit, on_progress=progress, all_missing=args.all)
        finally:
            # The bar must never keep moving after the work stopped.
            PROGRESS.write_text(json.dumps({'running': False, 'finishedAt': time.time(),
                                            'prepared': report.get('prepared', 0),
                                            'total': report.get('total', 0),
                                            'modelCalls': report.get('modelCalls', 0),
                                            'cost': report.get('cost'),
                                            'errors': report.get('errors', [])},
                                           ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(gap(ROOT) | report, ensure_ascii=False))
        return 0
    except ValueError as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
