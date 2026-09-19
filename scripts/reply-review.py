#!/usr/bin/env python3
"""Backfill, classify and review creator replies in shadow mode; never sends messages."""
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.dont_write_bytecode=True
sys.path.insert(0,str(ROOT/'scripts'))

from lib.reply_events import (DeepSeekClassifier,JevClassifier,backfill,classify,review,status)  # noqa:E402
from lib.second_cycle import CycleError,CycleStore  # noqa:E402


def main():
    try:
        raw=sys.stdin.read(20001)
        if len(raw.encode())>20000:raise CycleError('input_too_large')
        request=json.loads(raw or '{}')
        action=request.get('action')
        if action not in ('status','backfill','classify','batch_classify','review'):raise CycleError('invalid_action')
        readonly=action=='status'
        with CycleStore(ROOT/'var/second-cycle.sqlite',readonly=readonly) as store:
            if action=='status':
                if set(request)-{'action','limit'}:raise CycleError('invalid_input')
                result=status(store,request.get('limit',12))
            elif action=='backfill':
                if set(request)!={'action'}:raise CycleError('invalid_input')
                result=backfill(store)
            elif action=='classify':
                if set(request)!={'action','turnId','requestId','provider'}:raise CycleError('invalid_input')
                provider=request['provider']
                classifier=DeepSeekClassifier() if provider=='deepseek' else JevClassifier(ROOT) if provider=='jev' else None
                if classifier is None:raise CycleError('reply_provider_invalid')
                result=classify(store,request['turnId'],request['requestId'],classifier)
            elif action=='batch_classify':
                if set(request)!={'action','providers','limit'}:raise CycleError('invalid_input')
                from lib.reply_events import batch_classify
                result=batch_classify(store,request['providers'],request['limit'],root=ROOT)
            else:
                if set(request)!={'action','classificationId','expectedRevision','verdict','correctAction','note'}:
                    raise CycleError('invalid_input')
                result=review(store,request['classificationId'],request['expectedRevision'],request['verdict'],
                              request['correctAction'],request['note'])
        print(json.dumps(result,ensure_ascii=False))
        return 0
    except Exception as error:
        code=str(error) if isinstance(error,CycleError) else 'reply_review_unavailable'
        print(json.dumps({'error':code},ensure_ascii=False));return 2


if __name__=='__main__':raise SystemExit(main())
