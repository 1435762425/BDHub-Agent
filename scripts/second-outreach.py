#!/usr/bin/env python3
"""Local second-outreach API. No platform or model execution commands."""
import argparse
import json
import sys
from lib.second_outreach import SecondOutreachStore,SecondOutreachError
from lib.second_templates import SecondTemplateError

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['status','list','get','command','context','templates'])
    args=parser.parse_args()
    try:
        source=sys.stdin.read(16385)
        if len(source.encode())>16384:raise SecondOutreachError('invalid_request')
        value=json.loads(source)
        fields={'status':set(),'templates':set(),'list':{'offset','limit','q','filter'},'get':{'opportunityId'},'command':{'requestId','command'},'context':{'packetId','style','instructions'}}
        if not isinstance(value,dict) or set(value)!=fields[args.command]:raise SecondOutreachError('invalid_request')
        with SecondOutreachStore() as store:
            if args.command=='templates':
                from lib.second_templates import list_templates
                result={'templates':list_templates()}
            elif args.command=='context':raise SecondOutreachError('second_uses_templates',409)
            else:result=store.status() if args.command=='status' else store.list(**value) if args.command=='list' else store.get(value['opportunityId']) if args.command=='get' else store.command(value['requestId'],value['command'])
        print(json.dumps(result,ensure_ascii=False));return 0
    except (SecondOutreachError,ValueError) as error:
        code=error.code if isinstance(error,(SecondOutreachError,SecondTemplateError)) else 'source_unavailable'
        print(json.dumps({'error':{'code':code,'status':getattr(error,'status',422 if isinstance(error,SecondTemplateError) else 503),'message':code}}));return 1
    except Exception:
        print(json.dumps({'error':{'code':'second_unavailable','status':503,'message':'Second outreach unavailable'}}));return 1

if __name__=='__main__':raise SystemExit(main())
