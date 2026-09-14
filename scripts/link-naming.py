#!/usr/bin/env python3
"""Read, preview and save the frontend-controllable TapLink naming config.

    python scripts/link-naming.py show
    python scripts/link-naming.py preview --json '{"template":"BJN {short_name} {creator_percent}% {tail}"}'
    python scripts/link-naming.py save    --json '{"template":"..."}'

Preview uses real products that are actually waiting for a link, so the operator sees the
name a card would really get. Errors come back as JSON codes, never as a traceback.
"""
import argparse
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'scripts'))
from lib.link_naming import DEFAULTS, PLACEHOLDERS, fingerprint, load, render_full, save, short_name_for, tail_for, validate  # noqa: E402


def samples(limit=5):
    """Real products currently waiting for a link, newest first."""
    db = ROOT / 'var/catalog-links.sqlite'
    if not db.exists():
        return []
    with closing(sqlite3.connect(db.as_uri() + '?mode=ro', uri=True)) as conn:
        conn.execute('BEGIN')
        rows = conn.execute("SELECT pid,campaign_id,listing FROM catalog_prepare_item WHERE state='missing' ORDER BY updated DESC,pid LIMIT ?", (limit,)).fetchall()
    out = []
    for pid, campaign, listing in rows:
        data = json.loads(listing) if listing else {}
        title = data.get('title') or str(pid)
        out.append({'pid': str(pid), 'campaignId': str(campaign), 'title': title,
                    'creatorPercent': data.get('creatorPercent') or '0',
                    'publicPercent': data.get('publicPercent'), 'totalPercent': data.get('totalPercent'),
                    'shortName': short_name_for(ROOT, pid, title)})
    return out


def preview(config):
    rows = []
    for item in samples():
        tail = tail_for(item['pid'], item['campaignId'], item['creatorPercent'], config)
        try:
            rendered = render_full(config, short_name=item['shortName'], creator_percent=item['creatorPercent'],
                                   tail=tail, pid=item['pid'], campaign=item['campaignId'],
                                   public_percent=item['publicPercent'], total_percent=item['totalPercent'])
            rows.append(item | rendered | {'length': len(rendered['name']), 'limit': config['maxLength'], 'error': None})
        except ValueError as error:
            rows.append(item | {'name': None, 'shortName': None, 'tail': tail, 'length': 0,
                                'limit': config['maxLength'], 'error': str(error)})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['show', 'preview', 'save'])
    parser.add_argument('--json', help='config JSON, inline or @path')
    args = parser.parse_args()

    if args.action == 'show':
        config = load(ROOT)
        print(json.dumps({'config': config, 'fingerprint': fingerprint(config),
                          'placeholders': sorted(PLACEHOLDERS), 'defaults': DEFAULTS,
                          'preview': preview(config)}, ensure_ascii=False))
        return 0

    raw = {}
    if args.json:
        raw = json.loads(Path(args.json[1:]).read_text(encoding='utf-8')) if args.json.startswith('@') else json.loads(args.json)
    try:
        if args.action == 'preview':
            config = validate(raw)
            print(json.dumps({'config': config, 'fingerprint': fingerprint(config), 'preview': preview(config)}, ensure_ascii=False))
            return 0
        config = save(ROOT, raw)
        print(json.dumps({'saved': True, 'config': config, 'fingerprint': fingerprint(config), 'preview': preview(config)}, ensure_ascii=False))
        return 0
    except ValueError as error:
        print(json.dumps({'saved': False, 'error': str(error), 'config': load(ROOT) if (ROOT / 'config/link-naming.json').exists() else DEFAULTS}, ensure_ascii=False))
        return 2
    except json.JSONDecodeError:
        print(json.dumps({'saved': False, 'error': 'link_naming_json_invalid'}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
