#!/usr/bin/env python3
"""Kalodata scraper identity: status, save, test, activate, refresh.

    python scripts/kalodata-identity.py status
    python scripts/kalodata-identity.py save     --json '{"activationCode":"...","canaryPid":"..."}'
    python scripts/kalodata-identity.py probe
    python scripts/kalodata-identity.py activate
    python scripts/kalodata-identity.py refresh

``status`` only reads files. ``probe`` makes one real creator-list request on the same provider and
endpoint the batch worker uses, so its verdict reflects the path that actually scrapes; an
exhausted daily quota is reported as a healthy identity. Activation and refresh open the grabber's
dedicated Chrome and return immediately; the page follows ``login.running`` until the operator
closes that window. Errors come back as JSON codes, never as a traceback, and the card is never
printed.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'scripts'))
from lib.kalodata_identity import (load, probe, save, start_login, status)  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['status', 'save', 'probe', 'activate', 'refresh'])
    parser.add_argument('--json', help='config JSON, inline or @path')
    parser.add_argument('--pid', help='product id to probe with')
    args = parser.parse_args()

    try:
        if args.action == 'status':
            print(json.dumps(status(ROOT), ensure_ascii=False))
            return 0
        if args.action == 'save':
            raw = {}
            if args.json:
                raw = (json.loads(Path(args.json[1:]).read_text(encoding='utf-8'))
                       if args.json.startswith('@') else json.loads(args.json))
            saved = save(ROOT, {**load(ROOT), **raw})
            print(json.dumps(status(ROOT) | {'saved': True, 'config': saved}, ensure_ascii=False))
            return 0
        if args.action == 'probe':
            result = probe(ROOT, pid=args.pid or None)
            print(json.dumps(status(ROOT) | {'lastProbe': result}, ensure_ascii=False))
            return 0
        # Activation needs the card; refresh reuses the card the extension already saved.
        code = load(ROOT)['activationCode']
        if args.action == 'activate' and not code:
            print(json.dumps({'error': 'kalodata_activation_code_missing'}, ensure_ascii=False))
            return 2
        state = start_login(ROOT, args.action, code=code)
        print(json.dumps(status(ROOT) | {'started': state}, ensure_ascii=False))
        return 0
    except ValueError as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False))
        return 2
    except json.JSONDecodeError:
        print(json.dumps({'error': 'kalodata_identity_json_invalid'}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
