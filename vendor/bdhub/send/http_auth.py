"""显式HTTP IM鉴权探查；每账号自身身份包，不启动浏览器或持久化token。"""
import time
from urllib.parse import urlsplit

import requests

from bdhub import config,scheduled_relogin
from bdhub.enrich.identity_store import load_identity
from bdhub.hub.markets import identity_for


def get_http_im_auth(account,report,*,use_environment_proxy=True,context=None):
    identity=identity_for('mx',account=account,cfg=config.load()).require_im()
    if not identity.partner_id_is_own:raise ValueError('http_auth_identity_not_own')
    if scheduled_relogin.maintenance_due(account,initialize=False,ignore_retry_throttle=True):
        raise ValueError('http_auth_maintenance_due')
    bundle=load_identity(account.headers_json)
    headers={k:v for k,v in bundle.headers.items() if not k.startswith(':') and k.lower() not in ('host','content-length')}
    report['browser_initializations']=0
    report['auth_transport']='pure_http'
    with requests.Session() as session:
        session.trust_env=use_environment_proxy
        report['environment_proxy']=use_environment_proxy
        # 原生MX请求实证为区域API host，不能把Portal HTML host当IM API host。
        base=identity.host+'/api/v1/affiliate/partner/'
        common={'aid':identity.aid,'partner_id':identity.partner_id}
        def read(path,extra):
            for attempt in range(2):
                start=time.monotonic()
                try:
                    response=session.get(base+path,headers=headers,params={**common,**extra},
                                         timeout=(5,15),allow_redirects=False)
                except requests.RequestException:
                    report.setdefault('auth_reads',[]).append({'path':path,'transport_error':True})
                    if attempt==0:time.sleep(1);continue
                    raise ValueError('http_auth_transport_error') from None
                if response.status_code!=200:raise ValueError('http_auth_http_status')
                try:body=response.json()
                except ValueError:raise ValueError('http_auth_non_json') from None
                code=body.get('code') if isinstance(body,dict) else None
                report.setdefault('auth_reads',[]).append({'path':path,'code':code,'seconds':round(time.monotonic()-start,3)})
                if type(code) is not int or code!=0:raise ValueError('http_auth_business_rejected')
                if response.headers.get('bdturing-verify'):raise ValueError('http_auth_verification_required')
                time.sleep(1)
                return body['data'] if isinstance(body.get('data'),dict) else body
        partner=read('info',{'partner_type':1})
        markets=(partner.get('partner_biz_role_info') or {}).get('market_list',[])
        matches=[row for row in markets if str(row.get('market_region'))==str(identity.im_market)
                 and any(str(t.get('partner_id'))==str(identity.partner_id) for t in row.get('type_list',[]))]
        if len(matches)!=1:raise ValueError('http_auth_market_identity_mismatch')
        report['market_identity_verified']=True
        if context is not None:
            context['market_id']=str(matches[0]['market_id'])
            # 仅留在调用者内存，不能把公司身份或token写入report。
            context.update(partner=partner,market_row=matches[0],partner_id=str(identity.partner_id),
                           market_region=str(identity.im_market))
        im=read('im/id/get',{'user_id':matches[0]['market_id'],'type':0})
        im_id=str(im.get('im_id') or '')
        if not im_id.isdigit() or int(im_id)<=0:raise ValueError('http_auth_im_id_missing')
        token=read('im/token/get',{'im_id':im_id})
        report['token_field_names']=sorted(token)
        report['token_present']=bool(token.get('token'))
        report['im_id_verified']=True
        return im_id,token,headers


def bootstrap_direct_http(account,report,*,use_environment_proxy=False,conversation_context=None):
    """按已取证SDK字段构造本账号Envelope；只在内存中持有token。"""
    import secrets
    from bdhub.send.http_protocol import Packet,READ_HOST,SEND_PATH,vi,vb
    context={}
    im_id,token,source_headers=get_http_im_auth(account,report,use_environment_proxy=use_environment_proxy,context=context)
    u=urlsplit(str(token.get('api_url') or ''))
    if (u.scheme!='https' or u.hostname!=READ_HOST or u.port or u.username or u.password
            or u.path not in ('','/') or u.query or u.fragment or not token.get('token')):
        raise ValueError('http_auth_im_host_mismatch')
    if conversation_context is not None:
        from bdhub.send.http_conversation import native_context
        conversation_context.update(native_context(context,token))
    sequence=secrets.randbelow(1_000_000_000)+1
    envelope=(vi(1,203)+vi(2,sequence)+vb(3,'1.2.2')+vb(4,token['token'])
              +vi(5,3)+vi(6,0)+vb(7,'5dd76f3:master')+vb(8,vb(203,vi(1,0)))
              +vb(9,im_id)+vb(11,'web')+vi(18,2))
    headers={k:v for k,v in source_headers.items() if k.lower() in ('user-agent','accept-language')}
    headers.update({'content-type':'application/x-protobuf','origin':'https://partner.tiktokshop.com',
                    'referer':'https://partner.tiktokshop.com/'})
    report['fresh_auth_envelope']=True
    message_ext={'PIGEON_BIZ_TYPE':'1','sender_role':'4','sender_im_role':'4','sender_im_id':context['market_id'],
                 'shop_region':'MX','monitor_send_message_platform':'pc','type':'text','original_content':''}
    return Packet('https://'+READ_HOST+SEND_PATH,headers,envelope,sequence,b'',0,2,b'',b'',int(im_id),message_ext)
