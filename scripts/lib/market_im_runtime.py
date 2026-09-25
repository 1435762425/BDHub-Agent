"""Authenticated market IM context for explicit canary delivery."""
from __future__ import annotations

import hashlib
import json
import time
from contextlib import ExitStack,contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from lib.italy_im_auth import ItalyImAuthContext
from lib.italy_im_delivery import ItalyImDeliveryAdapter
from lib.italy_im_session import ItalyImReadSession
from lib.request_budget import RequestBudget
from lib.legacy_runtime import configure_vendored_bdhub
from lib.market_accounts import load_config

INFO='/api/v1/affiliate/partner/info';IM_ID='/api/v1/affiliate/partner/im/id/get';IM_TOKEN='/api/v1/affiliate/partner/im/token/get'
_AUTH_CACHE={}
_AUTH_CACHE_SECONDS=60


def _read(reader,**request):
 """One auth read.  A failed read keeps its error code and also carries the platform business code, so a caller
 can tell a lapsed login (16201010) from any other refusal."""
 result=reader._xhr(**request)
 try:return reader.require_read(result)
 except ValueError as error:
  code=getattr(result,'code',None)
  error.platform_code=code if type(code) is int else None
  raise


def _data(payload):
 value=payload.get('data') if isinstance(payload,dict) else None
 return value if isinstance(value,dict) else payload


@contextmanager
def authenticated(root,market,report,*,canary=False,read_only=False,capability='message_send',stopped=lambda:False,owner=False):
 root=Path(root);pair=load_config(root)['markets'][market];account_name=pair['roles']['communications']
 if capability not in ('message_send','agent_reply'):raise ValueError('market_im_capability_invalid')
 if canary:
  if market not in {'br','my','uk'}:raise ValueError('market_send_canary_unavailable')
 else:
  from lib.account_identity import current_generation
  from lib.second_cycle import CycleStore
  with CycleStore(root/'var/second-cycle.sqlite',readonly=True) as store:generation=current_generation(store,market,account_name)
  required='inbox_read' if read_only else capability
  if (generation or {}).get('capabilities',{}).get(required,{}).get('state')!='verified':
   raise ValueError('market_send_capability_unverified')
 configure_vendored_bdhub(root=root,legacy_root=root.parent/'01-BDSystem-V2')
 from bdhub.enrich.profile_lease import ProfileBusyError,ProfileLease
 from bdhub.hub.markets import identity_for
 from bdhub.research.commerce_transport import CommerceTransport
 from bdhub.send.taplink.transport import account_for
 from bdhub import scheduled_relogin
 cfg,account=account_for(market,account_name,check_maintenance=False)
 identity=identity_for(market,account=account,cfg=cfg).require_product_search()
 headers_path=Path(account.headers_json);before=hashlib.sha256(headers_path.read_bytes()).hexdigest()
 class Reader(CommerceTransport):
  READ_ENDPOINTS=frozenset({(INFO,'GET'),(IM_ID,'GET'),(IM_TOKEN,'GET')});WRITE_ENDPOINTS=frozenset()
 reader=None
 lease=ProfileLease(account.profile_dir,account=account.name,market=market,
                    operation='agent-im-read' if read_only else 'agent-im-send-canary')
 auth_started=time.monotonic()
 from lib.im_session_owner import enabled,borrow,OwnerUnavailable
 if not owner and enabled(root,market):
  from contextlib import contextmanager as _contextmanager
  @_contextmanager
  def borrowed():
   try:
    with borrow(root,market,account_name,stopped=stopped) as value:yield value
   except OwnerUnavailable:raise ProfileBusyError('im_session_unavailable') from None
  with borrowed() as (auth,owner_check):
   def available():
    owner_check()
    if hashlib.sha256(headers_path.read_bytes()).hexdigest()!=before:raise ValueError('market_send_identity_changed')
    return scheduled_relogin.maintenance_due(account,initialize=False,ignore_retry_throttle=True)
   report.update(market=market,account=account.name,sendCapability='read_only' if read_only else 'enabled',authSource='sdk_http_owner',platformWrites=0,realSends=0)
   with ItalyImReadSession(auth,report,use_environment_proxy=True,stopped=stopped,maintenance_due=available,request_budget=RequestBudget(qps=2 if read_only else 3)) as session:
    yield {'account':account,'identity':identity,'auth':auth,'session':session,'adapter':None if read_only else ItalyImDeliveryAdapter(auth,session),'partnerHost':auth.native_context['partner_host'],'headersPath':headers_path,'beforeHash':before}
   report['identityFileUnchanged']=hashlib.sha256(headers_path.read_bytes()).hexdigest()==before
  return
 try:
  with ExitStack() as guard:
   deadline=time.monotonic()+5 if read_only else 0
   while True:
    try:
     guard.enter_context(lease)
     break
    except ProfileBusyError:
     if not read_only or stopped() or time.monotonic()>=deadline:raise
     time.sleep(0.1)
   if stopped() or scheduled_relogin.maintenance_due(account,initialize=False,ignore_retry_throttle=True):raise ValueError('market_send_account_unavailable')
   if hashlib.sha256(headers_path.read_bytes()).hexdigest()!=before:raise ValueError('market_send_identity_changed')
   cache_key=(market,account.name,(generation or {}).get('generationId') if not canary else None,before)
   cached=_AUTH_CACHE.get(cache_key) if not read_only and not canary else None
   cache_hit=bool(cached and time.monotonic()-cached['at']<_AUTH_CACHE_SECONDS)
   if cache_hit:
    auth=cached['auth'];partner_host=cached['partnerHost']
   else:
    reader=Reader(identity,account,allow_write=False);reader.session.trust_env=True
    params={'aid':identity.aid,'partner_id':str(identity.partner_id)}
    partner=_data(_read(reader,method='GET',path=INFO,params=params|{'partner_type':1},payload=None,write=False))
    rows=(partner.get('partner_biz_role_info') or {}).get('market_list',[])
    matches=[row for row in rows if str(row.get('market_region'))==str(identity.im_market) and
             any(str(item.get('partner_id')) in {str(identity.partner_id),str(identity.im_market_partner_id)} for item in (row.get('type_list') or []))]
    if len(matches)!=1:raise ValueError('market_institution_not_verified')
    market_row=matches[0];market_id=str(market_row.get('market_id') or '')
    im=_data(_read(reader,method='GET',path=IM_ID,params=params|{'user_id':market_id,'type':0},payload=None,write=False))
    im_id=str(im.get('im_id') or '')
    token=_data(_read(reader,method='GET',path=IM_TOKEN,params=params|{'im_id':im_id},payload=None,write=False))
    endpoint=urlsplit(str(token.get('api_url') or ''));portal=urlsplit(identity.home);partner_host=f'{portal.scheme}://{portal.netloc}'
    if not im_id.isdigit() or not token.get('token') or not endpoint.hostname:raise ValueError('market_im_auth_invalid')
    safe_headers={key:value for key,value in reader.headers.items() if key.lower() in {'user-agent','accept-language'}}
    auth=ItalyImAuthContext(account.name,im_id,token,{'market':market,'account':account.name,
     'market_region':str(identity.im_market),'partner_host':partner_host,'im_host':endpoint.hostname,
     'partner':partner,'market_row':market_row,'market_id':market_id,'partner_id':str(identity.partner_id)},safe_headers,0)
   report.update(market=market,account=account.name,sendCapability='read_only' if read_only else 'canary' if canary else 'enabled',
                 identityFileUnchanged=True,platformWrites=0,realSends=0,
                 authDurationMs=round((time.monotonic()-auth_started)*1000,1),authCacheHit=cache_hit)
   if hashlib.sha256(headers_path.read_bytes()).hexdigest()!=before:raise ValueError('market_send_identity_changed')
   if not read_only and not canary and not cache_hit:
    _AUTH_CACHE.clear()
    _AUTH_CACHE[cache_key]={'auth':auth,'partnerHost':partner_host,'at':time.monotonic()}
   # The IM token is an immutable snapshot. Only authentication needs the profile
   # lease; read-only polling must not monopolize it while scanning conversations.
   if read_only and not owner:guard.close()
   def session_unavailable():
    if hashlib.sha256(headers_path.read_bytes()).hexdigest()!=before:raise ValueError('market_send_identity_changed')
    return scheduled_relogin.maintenance_due(account,initialize=False,ignore_retry_throttle=True)
   with ItalyImReadSession(auth,report,use_environment_proxy=True,stopped=stopped,
                           maintenance_due=session_unavailable,
                           request_budget=RequestBudget(qps=2 if read_only else 3)) as session:
    yield {'account':account,'identity':identity,'auth':auth,'session':session,
           'adapter':None if read_only else ItalyImDeliveryAdapter(auth,session),
           'partnerHost':partner_host,'headersPath':headers_path,'beforeHash':before}
   if hashlib.sha256(headers_path.read_bytes()).hexdigest()!=before:raise ValueError('market_send_identity_changed')
  report['identityFileUnchanged']=True
 finally:
  if reader is not None:reader.session.close()


@contextmanager
def write_gate(root,auth,interval=0.75,stopped=lambda:False):
 configure_vendored_bdhub(root=root,legacy_root=Path(root).parent/'01-BDSystem-V2')
 from bdhub.send.http_write_gate import shared_write_gate
 directory=Path(root)/'var/im-http-write-gates';directory.mkdir(parents=True,exist_ok=True)
 manager=shared_write_gate(auth.im_id,interval=interval,stopped=stopped,directory=directory)
 mark=manager.__enter__()
 try:yield mark
 finally:manager.__exit__(None,None,None)
