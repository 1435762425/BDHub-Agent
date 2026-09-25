#!/usr/bin/env python3
"""加入 Seller Campaign：先预览，再显式确认提交，最后回查。

    python scripts/campaign-join.py preview
    python scripts/campaign-join.py status
    python scripts/campaign-join.py apply --confirm --email you@example.com --campaigns 111,222
    python scripts/campaign-join.py join-all --confirm --email you@example.com   # 一键：先重新预览，再全部加入
    python scripts/campaign-join.py verify

``preview`` / ``status`` / ``verify`` 都只读。``apply`` 与 ``join-all`` 是**平台写入**，没有 ``--confirm``
一律拒绝；它们只接受预览里判定为 eligible 的活动，每个活动一次会话最多写一次，遇到"提了但不知道结果"
跳过该项并有限只读回查——**不重发未知申请**。``join-all``＝重新预览一遍再把全部合格的加进去（页面上的
「一键加入」就是它），不拿页面上的旧列表去写平台。
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'scripts'))
from lib.campaign_join import apply, default_email, join_all, preview, status, verify, migrate, safe_error  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['preview', 'status', 'apply', 'join-all', 'verify', 'migrate'])
    parser.add_argument('--market', required=True)
    parser.add_argument('--campaigns', help='逗号分隔的活动 id（apply 必填）')
    parser.add_argument('--email', help='平台要求的联系邮箱（apply 必填）')
    parser.add_argument('--confirm', action='store_true', help='显式确认这次是平台写入')
    parser.add_argument('--canary', action='store_true', help='仅允许一个活动的接入验证')
    parser.add_argument('--bounded', action='store_true', help='消耗原申请剩余的有限只读核验预算')
    args = parser.parse_args()
    market_args = {} if args.market == 'it' else {'market': args.market}
    try:
        if args.action == 'preview':
            result = preview(ROOT, **market_args)
        elif args.action == 'status':
            result = status(ROOT, **market_args)
        elif args.action == 'verify':
            result = verify(ROOT, bounded=args.bounded, **market_args)
        elif args.action == 'migrate':
            if not args.confirm:
                raise ValueError('campaign_join_confirmation_required')
            result = migrate(ROOT, **market_args)
        elif args.action == 'join-all':
            result = join_all(ROOT, email=args.email or default_email(ROOT), confirm=args.confirm, **market_args)
        else:
            campaigns = [c.strip() for c in (args.campaigns or '').split(',') if c.strip()]
            result = apply(ROOT, campaign_ids=campaigns, email=args.email or '', confirm=args.confirm, canary=args.canary, **market_args)
    except ValueError as error:
        print(json.dumps({'error': safe_error(error)}, ensure_ascii=False))
        return 2
    except Exception as error:  # noqa: BLE001 - 任何异常都必须留下可读的原因
        # 非预期异常也要以 **JSON** 回答。崩在 stderr 上时 stdout 是空的，页面只会拿到
        # campaign_unavailable——真正的名字级错误（比如某个未定义的常量）就永远传不到操作者眼前，
        # 而这类错误恰恰只在真正写入的那一刻才会出现。
        print(json.dumps({'error': f'campaign_join_internal:{type(error).__name__}'}, ensure_ascii=False))
        return 2
    print(json.dumps({'market':args.market,**result}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
