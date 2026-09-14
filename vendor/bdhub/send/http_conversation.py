"""原生Pigeon获取/创建会话；凭据仅在内存，写入前由正式worker持久记意图。"""
import time
from urllib.parse import urlsplit, urlunsplit

from bdhub.send.http_protocol import READ_HOST,one,wire_fields,decode_envelope,HISTORY_PATH,build_history_readback

CREATE_PATH='/api/v1/im/conversation/create'


def native_context(context,token):
    partner=context['partner']; row=context['market_row']
    def role_id(kind):
        values={str(r['partner_id']) for r in row.get('type_list',[]) if r.get('type')==kind and r.get('partner_id')}
        if len(values)>1:raise ValueError('http_create_ambiguous_partner_role')
        return next(iter(values),'')
    result={'market_id':context['market_id'],'market_region':context['market_region'],
            'partner_id':context['partner_id'],'tap_id':role_id(4),'cap_id':role_id(1),
            'company_region':str((partner.get('partner_biz_role_info') or {}).get('company_region') or ''),
            'agency_company_name':(partner.get('partner_info') or {}).get('company_name') or '',
            'agency_avatar':partner.get('avatar_url') or '',
            'app_id':token.get('app_id'),'api_url':token.get('api_url')}
    if (not result['company_region'] or not result['agency_company_name'] or not result['app_id']
            or result['partner_id'] not in (result['tap_id'],result['cap_id']) or result['market_region']!='19'):
        raise ValueError('http_create_context_incomplete')
    return result


def create_payload(context,oec):
    if not isinstance(oec,str) or not oec.isascii() or not oec.isdigit() or int(oec)<=0:
        raise ValueError('http_create_oec_invalid')
    c=context; agency=c['market_id']
    u=urlsplit(c['api_url'])
    if u.scheme!='https' or u.netloc!=READ_HOST or u.path not in ('','/') or u.query or u.fragment:
        raise ValueError('http_create_host_mismatch')
    return {'app_id':c['app_id'],'participants':[
        {'role':0,'uid':oec,'extra':{'sender_role':'1','creator_oec_id':oec}},
        {'role':1,'uid':agency,'extra':{'sender_role':'4','agency_market_id':agency,'tap_id':c['tap_id'],'cap_id':c['cap_id']}}],
        'options':{'api_url':c['api_url']},'biz_hook_ext':{
            '1':oec,'4':agency,'createConversationTime':str(int(time.time()*1000)),
            'agency_market_id':agency,'company_region':c['company_region'],
            'agency_company_name':c['agency_company_name'],'market_region':c['market_region'],
            'agency_avatar':c['agency_avatar'],'creator_oec_id':oec,'partner_id':c['partner_id'],
            'tap_id':c['tap_id'],'cap_id':c['cap_id']}}


def create_once(session,context,oec,report,*,before_request):
    payload=create_payload(context,oec)
    token=one(wire_fields(session.packet.data),4).decode()
    headers={**session.headers,'content-type':'application/json','x-im-paas-token':token}
    time.sleep(max(0,session.next_request_at-time.monotonic()))
    if time.monotonic()>=session.expires_at:raise ValueError('http_auth_refresh_required')
    session.next_request_at=time.monotonic()+1
    before_request()
    report['http_conversation_create_posts']=1
    response=session.session.post(urlunsplit(urlsplit(session.packet.url)._replace(path=CREATE_PATH)),
        headers=headers,json=payload,timeout=(5,15),allow_redirects=False)
    report['conversation_create_http_status']=response.status_code
    if response.status_code!=200:raise ValueError('http_create_result_unknown')
    try:body=response.json()
    except ValueError:raise ValueError('http_create_result_unknown') from None
    if not isinstance(body,dict):raise ValueError('http_create_result_unknown')
    report['conversation_response_fields']={k:type(v).__name__ for k,v in body.items()}
    report['conversation_create_code']=body.get('code') if isinstance(body.get('code'),(int,str)) else None
    data=body.get('data') or {}
    cid=data.get('conversation_short_id') if isinstance(data,dict) else None
    is_new=data.get('is_new') if isinstance(data,dict) else None
    report['conversation_result_fields']={k:type(v).__name__ for k,v in data.items()} if isinstance(data,dict) else {}
    # Python JSON整数不会丢失64位精度；浮点数和bool不能充当CID。
    if type(cid) is int:cid=str(cid)
    if isinstance(cid,str) and cid.isascii() and cid.isdigit() and int(cid)>0:
        report['candidate_conversation_id']=cid
    if type(is_new) is bool:report['candidate_conversation_is_new']=is_new
    # 原生get-or-create恢复旧会话时实证省略is_new。缺省保留None，不冒充新建证据。
    if type(body.get('code')) is not int or body['code']!=0 or not isinstance(cid,str) or not cid.isascii() or not cid.isdigit() or int(cid)<=0 or (is_new is not None and type(is_new) is not bool):
        raise ValueError('http_create_result_unknown')
    return {'conversation_id':cid,'is_new':is_new}


def history_before_send(session,packet,report):
    seq=session.next_sequence()
    response=session.post(HISTORY_PATH,build_history_readback(packet,sequence=seq))
    if response.status_code!=200:raise ValueError('http_create_history_unconfirmed')
    envelope=decode_envelope(response.content,cmd=301,sequence=seq)
    if 301 not in envelope:raise ValueError('http_create_history_unconfirmed')
    body=wire_fields(one(envelope,301))
    report['history_messages_before_send']=len(body.get(1,[]))
    report['history_has_more_before_send']=bool(one(body,3,0))
    return not body.get(1,[]) and not report['history_has_more_before_send']
