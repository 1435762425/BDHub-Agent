#!/usr/bin/env python3
"""Verify the vendored legacy layer boots on the new project's own config (I3 step 1).

Proves that vendor/bdhub can load, resolve a market identity and report capability
status without the sibling 01-BDSystem-V2 repository and without any copied
credential. Read-only: writes only a temporary config under the system temp dir.

    python scripts/check-vendored-runtime.py
"""
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / 'vendor'
EXAMPLE = VENDOR / 'config.example.yaml'


def check(label, condition, detail=''):
    mark = 'PASS' if condition else 'FAIL'
    print(f'  [{mark}] {label}' + (f' — {detail}' if detail else ''))
    return bool(condition)


def main():
    print('vendored layer runtime check')
    print(f'  vendor : {VENDOR}')
    print(f'  example: {EXAMPLE}')
    if not EXAMPLE.exists():
        print('  [FAIL] config.example.yaml missing')
        return 1

    sys.path.insert(0, str(VENDOR))
    import bdhub  # noqa: E402

    ok = True
    ok &= check('bdhub resolves inside vendor', Path(bdhub.__file__).is_relative_to(VENDOR),
                str(bdhub.__file__))

    tmp = Path(tempfile.mkdtemp(prefix='bdhub-vendor-check-'))
    try:
        cfg_path = tmp / 'config.yaml'
        shutil.copy2(EXAMPLE, cfg_path)

        from bdhub import config as cfgmod  # noqa: E402
        cfg = cfgmod.load(cfg_path)
        ok &= check('config loads from new-project template', cfg.market == 'it', f'market={cfg.market}')
        ok &= check('relative paths resolve against the config dir',
                    Path(cfg.headers_json).resolve().is_relative_to(tmp.resolve()), str(cfg.headers_json))
        ok &= check('AI auto-reply stays disabled', cfg.reply.api_key == '', 'reply.api_key empty')

        from bdhub.hub.markets import MARKETS, capability_status, identity_for  # noqa: E402
        expected = {'br', 'de', 'it', 'jp', 'mx', 'my', 'uk', 'us'}
        ok &= check('registered markets carried over', set(MARKETS) == expected, ','.join(sorted(MARKETS)))

        ident = identity_for('it', cfg=cfg)
        ok &= check('IT identity resolves', bool(ident.host and ident.aid and ident.im_market),
                    f'host={ident.host} aid={ident.aid} im_market={ident.im_market}')
        ok &= check('IT uses the EU portal host', 'partner.eu' in ident.host, ident.host)

        print('  capability matrix (legacy evidence, not new-project verification):')
        for market in sorted(MARKETS):
            caps = MARKETS[market].capabilities.public_dict()
            interesting = {k: caps[k] for k in ('find', 'profile', 'send', 'campaign', 'share_link', 'tap_link') if k in caps}
            print(f'    {market}: ' + ', '.join(f'{k}={v}' for k, v in interesting.items()))
        it_caps = MARKETS['it'].capabilities.public_dict()
        ok &= check('IT send is only canary in the legacy table (new project has real evidence)',
                    it_caps.get('send') == 'canary', f"send={it_caps.get('send')}")
        ok &= check('capability lookups work', capability_status('it', 'find') == 'enabled')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print('result:', 'OK' if ok else 'FAILED')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
