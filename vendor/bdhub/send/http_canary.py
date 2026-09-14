"""Dashboard显式确认后的单条HTTP实证；浏览器只准备包，关闭后HTTP发送一次。"""
import asyncio
import json
import os
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

import requests
from playwright.async_api import async_playwright
from sqlalchemy import select, text

from bdhub import config, scheduled_relogin
from bdhub.enrich.profile_lease import ProfileLease
from bdhub.hub.engine import get_engine, check_schema
from bdhub.hub.schema import send_task_account
from bdhub.hub.repo.send_tasks import SendTaskRepo
from bdhub.hub.repo.send_workers import SendWorkerRepo
from bdhub.hub.repo.im import OutboundMessageProjector
from bdhub.hub.repo.outreach import OutreachStore
from bdhub.hub.repo.send_attribution import SendAttributionRepo
from bdhub.hub.markets import identity_for, require_capability
from bdhub.imbase.account_binding import resolve_bound_profile
from bdhub.imbase.transport import _FIND_API, _JS_SDK_READY
from bdhub.send.transport_scripts import _JS_RESOLVE_TARGETED
from bdhub.send.component_sender import ComponentOutcome, ConversationResolutionError
from bdhub.send.runner import SendTaskRunner
from bdhub.send.http_protocol import (
    validate_packet, decode_send_receipt,
    READ_HOST, SEND_PATH,
    HISTORY_PATH, build_history_readback, history_checks,
)

_PREPARE = "({cid,text,handle})=>{" + _FIND_API + """
 if(!api||!api.sdkInstance)throw new Error('sdk_missing');
 const ext={type:'text',original_content:'','b:oec_im_search_context':JSON.stringify({sender_name:handle,search_content_map:{name:handle}})};
 window.__bdhubHttpPacketPromise=api.sdkInstance.messageService.sendMessage(String(cid),{content:String(text),ext}).then(()=>true,()=>false);
 return true;
}"""
_CHECK = "({cid,oec})=>{" + _FIND_API + """
 if(!api||!api.sdkInstance)return false;
 return (api.sdkInstance.conversationService.getAllConversations()||[]).some(c=>
   (String(c.id||'')===cid||String(c.shortId||'')===cid)&&String((c.originExt||{}).creator_oec_id||'')===oec);
}"""

async def prepare(account, identity, claim, report):
    from bdhub.send.worker import _targeted_im_gate
    lead=claim["lead"];oec=str(lead["oec_id"]);message=str(lead["rendered_text_snapshot"])
    captured=asyncio.get_running_loop().create_future()
    selected={"cid":None};captures=0
    async with async_playwright() as p:
        context=await p.chromium.launch_persistent_context(str(resolve_bound_profile(account)),headless=True,service_workers="block")
        try:
            async def block_ws(ws):await ws.close()
            await context.route_web_socket("**/*",block_ws)
            async def guard(route):
                nonlocal captures
                req=route.request;u=urlsplit(req.url);path=u.path.lower()
                if u.hostname==READ_HOST and path==SEND_PATH:
                    captures+=1
                    try:
                        if selected["cid"] is None:raise ValueError("packet_before_target_ready")
                        packet=validate_packet(req.url,await req.all_headers(),req.post_data_buffer,
                                               cid=selected["cid"],text=message)
                        if not captured.done():captured.set_result(packet)
                    except Exception as error:
                        if not captured.done():captured.set_exception(ValueError(type(error).__name__+":packet_rejected"))
                    await route.fulfill(status=503,content_type="application/json",body='{"code":-1,"message":"local_packet_capture_only"}')
                    return
                if any(x in path for x in ("/message/send","/send_message","/approve","/reject","/delete","/recall","/mark_read")):
                    await route.abort();return
                if "/conversation/create" in path:
                    try:body=req.post_data_json
                    except Exception:body=None
                    def contains(v):
                        if isinstance(v,dict):return any(contains(x) for x in v.values())
                        if isinstance(v,list):return any(contains(x) for x in v)
                        return str(v)==oec
                    if u.hostname!=READ_HOST or not contains(body):
                        await route.abort();return
                await route.continue_()
            await context.route("**/*",guard)
            page=context.pages[0] if context.pages else await context.new_page()
            from urllib.parse import urlencode
            url="https://partner.tiktokshop.com/partner/im?"+urlencode({
                "shop_id":identity.shop_id,"creator_id":oec,"market":identity.im_market,"enter_from":"find_creators",
            })
            with _targeted_im_gate(timeout_seconds=20):
                await page.goto(url,wait_until="commit",timeout=30000)
                await page.wait_for_function(_JS_SDK_READY,timeout=30000)
                resolution=await page.evaluate(_JS_RESOLVE_TARGETED,{"oec":oec})
            cid=str(resolution.get("cid") or "")
            if not resolution.get("ok") or not cid:raise ValueError("http_canary_target_unresolved")
            if not await page.evaluate(_CHECK,{"cid":cid,"oec":oec}):raise ValueError("http_canary_oec_mismatch")
            selected["cid"]=cid
            report["oec_verified"]=True
            await page.evaluate(_PREPARE,{"cid":cid,"text":message,"handle":str(lead["raw_handle"])})
            packet=await asyncio.wait_for(captured,25)
            report["browser_send_requests_blocked"]=captures
            return packet,cid
        finally:
            await context.close()
            report["browser_closed_before_http"]=True

class FixedOutcomeSender:
    def __init__(self,outcome,cid):self.market="mx";self.outcome=outcome;self.cid=cid
    def ensure_conversation(self,**kwargs):return {"method":"http_canary","classification":"verified_prepared" if self.outcome.transport.get("oec_verified") else "prepare_failed"}
    def resolve_conversation(self,_oec):raise ConversationResolutionError(self.outcome.error_code or "http_canary_prepare_failed",self.outcome.transport)
    def send_text(self,**kwargs):return self.outcome
    def send_card(self,**kwargs):return self.outcome

def run_canary(*,account_name,market,task_id,revision):
    from bdhub.send.worker import _select_account,_claim_liveness
    if market!="mx" or not task_id or not isinstance(revision,int):raise ValueError("http_canary_invalid")
    require_capability(market,"send")
    cfg=config.load();account=_select_account(cfg,account_name)
    if scheduled_relogin.maintenance_due(account,initialize=False,ignore_retry_throttle=True):raise ValueError("maintenance_due")
    identity=identity_for(market,account=account,cfg=cfg).require_im()
    if not identity.partner_id_is_own:raise ValueError("http_canary_identity_mismatch")
    engine=get_engine(cfg);check_schema(engine)
    repo=SendTaskRepo(engine);workers=SendWorkerRepo(engine);wid=f"mx:{account_name}"
    task=repo.get_task(task_id)
    if not task or task["status"]!="running" or task["send_mode"]!="text_only" or task["revision"]!=revision or task["preflight_revision"]!=revision:
        raise ValueError("http_canary_task_changed")
    with engine.connect() as c:
        if c.execute(select(send_task_account.c.account_name).where(send_task_account.c.task_id==task_id,send_task_account.c.account_name==account_name)).scalar_one_or_none() is None:
            raise ValueError("http_canary_account_not_selected")
    lease=ProfileLease(resolve_bound_profile(account),account=account_name,market=market,operation="http_send_canary")
    owner=lease.acquire()
    report={"task_id":task_id,"account":account_name,"transport_kind":"http_canary","http_send_posts":0,
            "started_at":datetime.now(timezone.utc).isoformat()}
    claim=None;outcome=None;cid=None
    try:
        workers.register(wid,market,account_name,os.getpid())
        if repo.recover_orphaned_attempt_for_worker(wid) is not None:
            workers.pause(wid,code="orphaned_worker_attempt",detail=None);return 2
        claim=repo.claim_next_component_for_worker(market=market,account_name=account_name,worker_id=wid,task_id=task_id)
        if claim is None:raise ValueError("http_canary_no_pending_target")
        if claim["component"]["component_kind"]!="text":raise ValueError("http_canary_text_only")
        report["component_id"]=claim["component"]["component_id"]
        workers.heartbeat(wid,status="busy",task_id=task_id,lead_id=claim["lead"]["lead_id"])
        with _claim_liveness(workers,wid):
            try:
                async def bounded():return await asyncio.wait_for(prepare(account,identity,claim,report),75)
                packet,cid=asyncio.run(bounded())
                report["client_message_id"]=packet.client_id.decode()
                report["conversation_id"]=cid
                latest=repo.get_task(task_id);state=workers.get(wid)
                if latest["status"]!="running" or latest["revision"]!=revision or state["status"] in {"paused","stopped","unhealthy"}:
                    raise ValueError("http_canary_paused_before_dispatch")
                with engine.connect() as c:
                    allowed=c.execute(text("""select count(*) from send_task_component x join send_task_lead l using(lead_id)
                        join send_component_attempt a using(component_id)
                        where x.component_id=:component and a.attempt_id=:attempt and x.status='sending' and a.status='sending'
                        and not l.manual_excluded and l.oec_id=:oec and l.rendered_text_snapshot=:body
                        and not exists(select 1 from creator_im_state s where s.bd_market='mx' and s.creator_identity_key='oec:'||l.oec_id and s.status='rejected')"""),
                        {"component":claim["component"]["component_id"],"attempt":claim["attempt"]["attempt_id"],
                         "oec":claim["lead"]["oec_id"],"body":claim["lead"]["rendered_text_snapshot"]}).scalar_one()
                    if allowed!=1:raise ValueError("http_canary_claim_changed")
                headers={k:v for k,v in packet.headers.items() if k.lower() not in {"host","content-length","connection"} and not k.startswith(":")}
                with requests.Session() as session:
                    report["http_send_posts"]=1  # 后面任何异常均unknown，绝不重发POST。
                    t=time.monotonic()
                    response=session.post(packet.url,headers=headers,data=packet.data,timeout=(5,15),allow_redirects=False)
                    report["http_status"]=response.status_code
                    report["send_http_seconds"]=round(time.monotonic()-t,3)
                    if response.status_code!=200:raise ValueError("http_send_status")
                    server_id=decode_send_receipt(response.content,packet)
                    report["server_receipt_verified"]=True
                    report["candidate_platform_message_id"]=str(server_id)
                    seq=packet.sequence+1000000
                    read_url=urlunsplit(urlsplit(packet.url)._replace(path=HISTORY_PATH))
                    confirmed=False
                    for n in range(3):
                        time.sleep(1)
                        read=session.post(read_url,headers=headers,data=build_history_readback(packet,sequence=seq+n),timeout=(5,15),allow_redirects=False)
                        if read.status_code==200:
                            try:
                                checks=history_checks(read.content,packet,server_id,sequence=seq+n)
                                report.setdefault("readback_checks",[]).append(checks)
                                confirmed=all(checks.get(k) is True for k in ("server_id_matches","full_cid_match","short_cid_match","content_match","sender_match","client_id_match","text_type_match"))
                                if confirmed:break
                            except ValueError:pass
                    report["readback_verified"]=confirmed
                    if not confirmed:raise ValueError("http_readback_unconfirmed")
                    outcome=ComponentOutcome("sent",str(server_id),None,dict(report))
            except Exception as error:
                report["error_type"]=type(error).__name__
                report["error_code"]=str(error)[:100] if isinstance(error,ValueError) else "http_canary_transport_error"
                outcome=ComponentOutcome("unknown" if report["http_send_posts"] else "failed_retryable",None,
                    "http_canary_result_unknown" if report["http_send_posts"] else "http_canary_prepare_failed",dict(report))
        if cid is None:cid=str(claim["component"].get("conversation_id") or "")
        claim["component"]["conversation_id"]=cid
        # 共用正式runner的完成、消息投影和审计，不另建第二套成功事实。
        sender=FixedOutcomeSender(outcome,cid)
        runner=SendTaskRunner(task_repo=repo,sender=sender,attribution_repo=SendAttributionRepo(engine),
            outreach_store=OutreachStore(engine),message_projector=OutboundMessageProjector(engine))
        runner.process_claim(claim,task_id=task_id,market=market,send_account=account_name)
        report["outcome"]=outcome.status
        if outcome.status=="unknown":
            repo.pause_task(task_id,actor="http_canary")
            workers.pause(wid,code=outcome.error_code,detail=None)
        else:workers.stop(wid)
        return 0 if outcome.status=="sent" else 2
    finally:
        lease.release(owner.token)
        report["finished_at"]=datetime.now(timezone.utc).isoformat()
        output=config.ROOT/"outputs/im-http-send-canary-20260906";output.mkdir(parents=True,exist_ok=True)
        (output/"result.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        if report.get("component_id"):
            (output/(report["component_id"]+".json")).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps(report,ensure_ascii=False),flush=True)
