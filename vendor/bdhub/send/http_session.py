"""实验性HTTP IM会话；直接鉴权/会话读取已实证，尚未接入批量发送入口。"""
import time
import asyncio
import re
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import requests

from bdhub.send.http_protocol import (
    CONVERSATION_PATH,HISTORY_PATH,build_conversation_read,decode_conversation,
    build_fresh_send,decode_send_receipt,build_history_readback,history_checks,
    send_receipt_diagnostics,
)


async def bootstrap_http(account, report):
    """只截取本账号原生初始化读取的鉴权Envelope，不生成任何消息请求。"""
    from playwright.async_api import async_playwright
    from bdhub.imbase.account_binding import resolve_bound_profile
    from bdhub.hub.markets import identity_for
    from bdhub import config
    from urllib.parse import urlencode
    from bdhub.imbase.http_readonly_probe import READ_HOST,READ_PATH,validate_init_read,wire_fields
    from bdhub.send.http_protocol import Packet,SEND_PATH,one
    captured=asyncio.get_running_loop().create_future()
    # Chromium不继承HTTP_PROXY；仅本初始化进程沿用HTTP客户端已配置的出口。
    proxies=requests.utils.get_environ_proxies('https://partner.tiktokshop.com')
    proxy_url=proxies.get('https') or proxies.get('all')
    browser_options={}
    if proxy_url:
        from urllib.parse import unquote
        proxy=urlsplit(proxy_url)
        settings={'server':f'{proxy.scheme}://{proxy.hostname}:{proxy.port}'}
        if proxy.username:settings['username']=unquote(proxy.username)
        if proxy.password:settings['password']=unquote(proxy.password)
        browser_options['proxy']=settings
    report['bootstrap_uses_configured_proxy']=bool(proxy_url)
    async with async_playwright() as p:
        context=await p.chromium.launch_persistent_context(str(resolve_bound_profile(account)),headless=True,service_workers='block',**browser_options)
        try:
            def response_seen(response):
                u=urlsplit(response.url)
                if response.status>=400:
                    rows=report.setdefault('bootstrap_http_errors',[])
                    if len(rows)<8:rows.append({'host':u.hostname,'path':u.path,'status':response.status})
            context.on('response',response_seen)
            def request_done(req):
                if req.resource_type=='script':report['scripts_loaded']=report.get('scripts_loaded',0)+1
            def request_failed(req):
                if req.resource_type in ('script','document'):
                    rows=report.setdefault('bootstrap_network_errors',[])
                    if len(rows)<8:rows.append({'host':urlsplit(req.url).hostname,'path':urlsplit(req.url).path,'error':req.failure})
            context.on('requestfinished',request_done)
            context.on('requestfailed',request_failed)
            async def block_ws(ws):await ws.close()
            await context.route_web_socket('**/*',block_ws)
            async def guard(route):
                req=route.request;u=urlsplit(req.url)
                report['routed_requests']=report.get('routed_requests',0)+1
                if u.path.endswith(('/im/token/get','/im/id/get')):
                    report.setdefault('auth_request_paths',[]).append(u.path)
                if u.hostname==READ_HOST:
                    report.setdefault('im_request_paths',[]).append(u.path)
                    if u.path=='/v1/config/get' and req.method=='POST':
                        fields=wire_fields(req.post_data_buffer)
                        if one(fields,1)==2017 and set(wire_fields(one(fields,8,b'')))=={2017}:
                            await route.continue_();return
                    if u.path==READ_PATH and req.method=='POST':
                        try:
                            seq=validate_init_read(req.post_data_buffer)
                            env=wire_fields(req.post_data_buffer)
                            if not one(env,4) or int(one(env,9))<=0:raise ValueError('http_auth_envelope_invalid')
                            packet=Packet(urlunsplit(u._replace(path=SEND_PATH)),await req.all_headers(),
                                          req.post_data_buffer,seq,b'',0,2,b'',b'',int(one(env,9)))
                            if not captured.done():captured.set_result(packet)
                        except (ValueError,TypeError):
                            if not captured.done():captured.set_exception(ValueError('http_auth_envelope_invalid'))
                    await route.abort();return
                if any(x in u.path.lower() for x in ('/send','/create','/approve','/reject','/delete','/mark_read','/recall')):
                    await route.abort();return
                await route.continue_()
            await context.route('**/*',guard)
            page=await context.new_page()
            for restored in list(context.pages):
                if restored!=page:await restored.close()
            def page_error(error):
                # 不记录带查询串的URL或页面数据，只保留脚本错误摘要。
                value=re.sub(r'https?://\S+','[url]',str(error))[:240]
                report.setdefault('bootstrap_script_errors',[]).append(value)
            page.on('pageerror',page_error)
            report['bootstrap_stage']='navigation'
            identity=identity_for('mx',account=account,cfg=config.load()).require_im()
            await page.goto('https://partner.tiktokshop.com/partner/im?'+urlencode({
                'shop_id':identity.shop_id,'market':identity.im_market}),wait_until='commit',timeout=30000)
            report['bootstrap_stage']='waiting_for_init_envelope'
            packet=await asyncio.wait_for(captured,35)
            report['browser_initializations']=report.get('browser_initializations',0)+1
            report['browser_message_posts']=0
            return packet
        finally:
            if context.pages:
                report['final_page_path']=urlsplit(context.pages[0].url).path
                try:
                    report['page_diagnostics']=await context.pages[0].evaluate("()=>({scripts:document.scripts.length,bodyChars:(document.body?.innerText||'').length,hasLogin:!!document.querySelector('input[type=password]')})")
                except Exception:pass
            await context.close()
            report['browser_closed_before_http']=True


class HttpImSession:
    def __init__(self, packet, *, use_environment_proxy=True):
        self.packet=packet
        self.sequence=packet.sequence+1000000
        self.session=requests.Session()
        self.session.trust_env=use_environment_proxy
        self.headers={k:v for k,v in packet.headers.items()
                      if k.lower() not in {'host','content-length','connection'} and not k.startswith(':')}
        self.expires_at=time.monotonic()+15*60
        self.next_request_at=0.0

    def close(self):
        self.session.close()

    def next_sequence(self):
        self.sequence+=1
        return self.sequence

    def post(self,path,data,*,before_request=None):
        if time.monotonic()>=self.expires_at:
            raise ValueError('http_auth_refresh_required')
        time.sleep(max(0,self.next_request_at-time.monotonic()))
        self.next_request_at=time.monotonic()+1.0
        if time.monotonic()>=self.expires_at:raise ValueError('http_auth_refresh_required')
        if before_request is not None:before_request()
        url=urlunsplit(urlsplit(self.packet.url)._replace(path=path))
        return self.session.post(url,headers=self.headers,data=data,timeout=(5,15),allow_redirects=False)

    def conversation(self,cid,oec):
        seq=self.next_sequence()
        response=self.post(CONVERSATION_PATH,build_conversation_read(self.packet,cid,sequence=seq))
        if response.status_code!=200:
            raise ValueError('http_conversation_status')
        return decode_conversation(response.content,sequence=seq,cid=cid,oec=oec)

    def prepare_send(self,conversation,text,*,handle=None):
        import json
        ext=dict(self.packet.message_ext)
        if handle:
            ext['b:oec_im_search_context']=json.dumps({'sender_name':handle,'search_content_map':{'name':handle}},separators=(',',':'))
        return build_fresh_send(self.packet,conversation,text=text,
                                sequence=self.next_sequence(),client_id=str(uuid4()),ext=ext)

    def dispatch_once(self,packet,report,*,before_dispatch=None):
        # 调用前必须已完成持久claim、暂停/拒收复核。之后任何异常均unknown。
        start=time.monotonic()
        dispatch_start=None
        if before_dispatch is None:
            report['http_send_posts']=1
            response=self.post(urlsplit(packet.url).path,packet.data)
        else:
            def dispatch():
                nonlocal dispatch_start
                before_dispatch()
                report['http_send_posts']=1
                report['http_dispatch_epoch']=time.time()
                dispatch_start=time.monotonic()
            response=self.post(urlsplit(packet.url).path,packet.data,before_request=dispatch)
        report['http_status']=response.status_code
        report['send_http_seconds']=round(time.monotonic()-start,3)
        if dispatch_start is not None:
            report['platform_send_seconds']=round(time.monotonic()-dispatch_start,3)
            report['local_pre_dispatch_wait_seconds']=round(dispatch_start-start,3)
        if response.status_code!=200:
            raise ValueError('http_send_status')
        report['send_receipt']=send_receipt_diagnostics(response.content,packet)
        server_id=decode_send_receipt(response.content,packet)
        report['candidate_platform_message_id']=str(server_id)
        report['server_receipt_verified']=True
        return str(server_id)

    def verify_sent(self,packet,server_id,report):
        server_id=int(server_id)
        started=time.monotonic()
        for _ in range(3):
            seq=self.next_sequence()
            response=self.post(HISTORY_PATH,build_history_readback(packet,sequence=seq))
            if response.status_code!=200:
                continue
            if packet.message_ext.get('type')=='product_list':
                from bdhub.send.http_card import verify_card_readback
                checks=verify_card_readback(response.content,packet,server_id,sequence=seq)
            else:
                checks=history_checks(response.content,packet,server_id,sequence=seq)
            report.setdefault('readback_checks',[]).append(checks)
            if all(checks.get(k) is True for k in ('server_id_matches','full_cid_match','short_cid_match',
                    'content_match','sender_match','client_id_match','text_type_match')):
                report['readback_verified']=True
                report['http_readback_seconds']=round(time.monotonic()-started,3)
                return str(server_id)
        raise ValueError('http_readback_unconfirmed')

    def send_once(self,packet,report,*,before_dispatch=None):
        server_id=self.dispatch_once(packet,report,before_dispatch=before_dispatch)
        return self.verify_sent(packet,server_id,report)

    def recover_outer_error(self,packet,report):
        """仅对已发POST且IM外层500做只读核验；绝不再次调用发送接口。"""
        from bdhub.send.http_protocol import wire_fields,one,decode_envelope
        if report.get('http_send_posts')!=1 or (report.get('send_receipt') or {}).get('outer_status')!=500:
            return None
        for _ in range(3):
            seq=self.next_sequence()
            response=self.post(HISTORY_PATH,build_history_readback(packet,sequence=seq))
            if response.status_code!=200:return None
            body=wire_fields(one(decode_envelope(response.content,cmd=301,sequence=seq),301,b''))
            for raw in body.get(1,[]):
                msg=wire_fields(raw)
                ext={one(wire_fields(v),1):one(wire_fields(v),2) for v in msg.get(9,[])}
                if ext.get(b's:client_message_id')!=packet.client_id:continue
                if ext.get(b's:visible') or ext.get(b'visibility_type') not in (None,b'',b'0'):return None
                server_id=one(msg,3,0)
                if not isinstance(server_id,int) or server_id<=0:return None
                checks=history_checks(response.content,packet,server_id,sequence=seq)
                if packet.message_ext.get('type')=='product_list':
                    from bdhub.send.http_card import verify_card_readback
                    checks=verify_card_readback(response.content,packet,server_id,sequence=seq)
                report.setdefault('recovery_readback_checks',[]).append(checks)
                if all(checks.get(k) is True for k in ('server_id_matches','full_cid_match','short_cid_match',
                    'content_match','sender_match','client_id_match','text_type_match')):
                    report.update(candidate_platform_message_id=str(server_id),readback_verified=True,
                                  recovered_after_outer_error=True)
                    return str(server_id)
                return None
        return None
