#!/usr/bin/env python3
"""Bounded read-only IM HTTP canary for one assigned communications account."""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
sys.dont_write_bytecode=True
sys.path.insert(0,str(ROOT/'scripts'))

from lib.legacy_runtime import configure_vendored_bdhub  # noqa:E402
from lib.market_accounts import load_config  # noqa:E402

INFO='/api/v1/affiliate/partner/info'
IM_ID='/api/v1/affiliate/partner/im/id/get'
IM_TOKEN='/api/v1/affiliate/partner/im/token/get'


def data_of(payload):
 nested=payload.get('data') if isinstance(payload,dict) else None
 return nested if isinstance(nested,dict) else payload


def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--market',required=True);args=parser.parse_args()
 pair=load_config(ROOT)['markets'].get(args.market)
 if not pair:parser.error('market assignment missing')
 account_name=pair['roles']['communications'];report={'market':args.market,'account':account_name,
  'platformWrites':0,'realSends':0,'conversationCount':None,'imHttpReady':False}
 configure_vendored_bdhub(root=ROOT,legacy_root=ROOT.parent/'01-BDSystem-V2')
 from bdhub.enrich.profile_lease import ProfileLease
 from bdhub.hub.markets import identity_for
 from bdhub.research.commerce_transport import CommerceTransport
 from bdhub.send.taplink.transport import account_for
 from lib.italy_im_session import ItalyImReadSession
 cfg,account=account_for(args.market,account_name,check_maintenance=False)
 identity=identity_for(args.market,account=account,cfg=cfg).require_product_search()
 headers_path=Path(account.headers_json);before=hashlib.sha256(headers_path.read_bytes()).hexdigest()

 class Reader(CommerceTransport):
  READ_ENDPOINTS=frozenset({(INFO,'GET'),(IM_ID,'GET'),(IM_TOKEN,'GET')});WRITE_ENDPOINTS=frozenset()

 reader=Reader(identity,account,allow_write=False);reader.session.trust_env=True
 lease=ProfileLease(account.profile_dir,account=account.name,market=args.market,operation='agent-im-read-canary')
 try:
  with lease:
   params={'aid':identity.aid,'partner_id':str(identity.partner_id)}
   info=data_of(reader.require_read(reader._xhr(method='GET',path=INFO,params=params|{'partner_type':1},payload=None,write=False)))
   rows=(info.get('partner_biz_role_info') or {}).get('market_list',[])
   matches=[row for row in rows if str(row.get('market_region'))==str(identity.im_market) and
            any(str(item.get('partner_id')) in {str(identity.partner_id),str(identity.im_market_partner_id)}
                for item in (row.get('type_list') or []))]
   if len(matches)!=1:raise ValueError('market_institution_not_verified')
   market_id=str(matches[0].get('market_id') or '')
   im=data_of(reader.require_read(reader._xhr(method='GET',path=IM_ID,params=params|{'user_id':market_id,'type':0},payload=None,write=False)))
   im_id=str(im.get('im_id') or '')
   token=data_of(reader.require_read(reader._xhr(method='GET',path=IM_TOKEN,params=params|{'im_id':im_id},payload=None,write=False)))
   endpoint=urlsplit(str(token.get('api_url') or ''))
   if not im_id.isdigit() or not token.get('token') or not endpoint.hostname:raise ValueError('im_auth_invalid')
   report['apiHost']=endpoint.hostname
   safe_headers={key:value for key,value in reader.headers.items() if key.lower() in {'user-agent','accept-language'}}
   portal=urlsplit(identity.home);partner_host=f'{portal.scheme}://{portal.netloc}'
   auth=SimpleNamespace(account_name=account.name,im_id=im_id,token=token,
    native_context={'market_region':str(identity.im_market),'partner_host':partner_host},
    im_headers=safe_headers,next_request_at=0)
   with ItalyImReadSession(auth,report,use_environment_proxy=True) as session:
    initial=session.initialize(0)
   report.update(imHttpReady=True,conversationCount=len(initial['conversations']),hasMore=initial['hasMore'],
                 invalidConversations=initial['invalidConversations'],otherMarketConversations=initial['otherMarketConversations'],
                 identityFileUnchanged=hashlib.sha256(headers_path.read_bytes()).hexdigest()==before,checkedAt=time.time())
   if report['identityFileUnchanged'] is not True:raise ValueError('identity_changed')
 except Exception as error:
  report.update(error=getattr(error,'code',None) or (str(error) if isinstance(error,ValueError) else type(error).__name__))
 finally:reader.session.close()
 print(json.dumps(report,ensure_ascii=False));return 0 if report['imHttpReady'] else 2


if __name__=='__main__':raise SystemExit(main())
