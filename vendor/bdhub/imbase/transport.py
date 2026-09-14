# -*- coding: utf-8 -*-
"""IM 浏览器 SDK 传输底座:page 内 chatApi 定位 + SDK 就绪等待 + 全量会话拉取 + 日志。

从 `im/monitor.py` 抽出的共享符号(依赖闭包,一起搬):`_wait_sdk` 依赖 `_JS_SDK_READY`,
`_load_all` 依赖 `_JS_PULL_CONVS` + `_log`。`im/monitor.py`、`im/send.py`、`imbase/login_helper.py`
均从这里导入,不再互相耦合。

imbase 层不 import 任何业务模块(enrich/discover/pid/outreach/im),只做纯传输底座。
"""
from __future__ import annotations

from ..hub.markets import im_page   # hub 是数据中台(非业务模块),不违反"底座不依赖业务模块"

__all__ = [
    "_IM_PAGE",
    "im_page",
    "_FIND_API",
    "_JS_SDK_READY",
    "_wait_sdk",
    "_JS_PULL_CONVS",
    "_JS_SEND_TEXT",
    "_JS_SEND_IMAGE",
    "_JS_ENSURE_CONV",
    "_log",
    "_load_all",
]

# MX IM 页(与旧硬编码 ".../partner/im?market=19" 逐字节一致,markets.selftest 已证);
# 多市场调用方改用 im_page(market)——send/monitor 传各自市场 key。
_IM_PAGE = im_page("mx")

# 在 React 树里认出 IM 的 chatApi(memoizedProps.value 上挂 sendTextMessage)。各 JS 段共用。
_FIND_API = r"""
  function getFiber(n){for(const k of Object.keys(n||{})){if(k.startsWith('__reactFiber$')||k.startsWith('__reactContainer$'))return n[k];}return null;}
  const root=document.getElementById('root')||document.body;
  let f=getFiber(root); if(f&&f.current)f=f.current;
  const seen=new Set(); let api=null;
  (function walk(n){if(!n||seen.has(n)||api)return;seen.add(n);try{
    const direct=n.memoizedProps&&n.memoizedProps.value;
    const nested=n.memoizedProps&&n.memoizedProps.children&&n.memoizedProps.children.props&&n.memoizedProps.children.props.value;
    for(const v of [direct,nested]){if(v&&typeof v.sendTextMessage==='function'){api=v;return;}}
  }catch(e){}walk(n.child);walk(n.sibling);})(f);
  const withTimeout=(p,ms)=>Promise.race([Promise.resolve(p).catch(()=>'__err__'), new Promise(r=>setTimeout(()=>r('__timeout__'),ms))]);
"""

# SDK 对象早于账号/agency identity 挂载；必须等待业务状态完成初始化。
_JS_SDK_READY = "() => {" + _FIND_API + r"""
  return (
    api &&
    api.sdkInstance &&
    api.sdkStatus===1 &&
    api.isSDKLoading===false
  ) ? 1 : 0;
}"""

_JS_PULL_CONVS = "async ({n}) => {" + _FIND_API + r"""
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  const sdk=api.sdkInstance, cs=sdk.conversationService;
  for(let k=0;k<n;k++){
    let r; try{ r=await withTimeout(sdk.pullConversations(), 4000); }catch(e){ return {count:(cs.getAllConversations()||[]).length, err:String(e).slice(0,80)}; }
    if(r==='__timeout__'){ return {count:(cs.getAllConversations()||[]).length, timedout:1}; }
    await sleep(500);
  }
  return {count:(cs.getAllConversations()||[]).length};
}"""

# 发文字:messageService.sendMessage(cid,{content, ext:text}) + 回读 flight/serverId。dry 只报不发。
# (2026-07-18 从 send/dispatch.py 逐字下沉:reply 自动回复与 dispatch/adhoc 共用,收发同底座)
_JS_SEND_TEXT = "async ({cid, text, handle, dry}) => {" + _FIND_API + r"""
  if(!api||!api.sdkInstance) return {ok:0, err:'no api'};
  const sdk=api.sdkInstance, ms=sdk.messageService, rcid=String(cid);
  if(dry) return {ok:1, dry:1, cid:rcid, would:(text||'').slice(0,50)};
  const ext={type:'text', original_content:'', 'b:oec_im_search_context':JSON.stringify({sender_name:String(handle||''), search_content_map:{name:String(handle||'')}})};
  const before=new Set((ms.getConversationMessages(rcid)||[])
    .filter(m=>m.isFromMe)
    .flatMap(m=>[String(m.serverId||''),String(m.clientId||'')])
    .filter(Boolean));
  let r; try{ r=await Promise.race([Promise.resolve(ms.sendMessage(rcid,{content:String(text), ext})), new Promise(res=>setTimeout(()=>res('__to'),9000))]); }
  catch(e){ return {ok:0, err:String((e&&e.message)||e).slice(0,150)}; }
  const sendTimedOut=r==='__to';
  // 轮询等 flight 稳定:3=已读 4=已发未读(成功);负数(如 -2)=发送失败/被拒。
  // 即使sendMessage Promise超时也继续回读：平台可能已经受理，只是Promise晚到。
  // 先立即回读一次，随后 200ms 一次；总等待预算仍保持 7.2s，不降低成功门禁。
  let flight=null, sid=null;
  for(let i=0;i<37;i++){ if(i) await new Promise(z=>setTimeout(z,200));
    const mine=(ms.getConversationMessages(rcid)||[]).filter(m=>m.isFromMe);
    const fresh=[...mine].reverse().find(m=>{
      const id=String(m.serverId||m.clientId||'');
      return id&&!before.has(id)&&!before.has(String(m.clientId||''))&&String(m.content||'')===String(text);
    });
    if(fresh){ flight=fresh.flightStatus; sid=String(fresh.serverId||''); }
    if(sid && (flight===3||flight===4)) break;
  }
  const ok=!!sid && (flight===3||flight===4);
  return {
    ok, flight, serverId:sid, cid:rcid,
    send_timed_out:sendTimedOut,
    signal:ok?(sendTimedOut?'late_readback':'normal_readback'):null,
    err:!ok?(sendTimedOut?'send timeout':'send receipt unknown'):null
  };
}"""


# 发图片:页面 SDK 自带上传器完成文件上传和图片消息发送；Python 仅传已校验的小文件 base64。
_JS_SEND_IMAGE = "async ({cid, name, mime, data}) => {" + _FIND_API + r"""
  if(!api||!api.sdkInstance||typeof api.sendImageMessageWithFiles!=='function') return {ok:0, err:'image sender unavailable'};
  const sdk=api.sdkInstance, ms=sdk.messageService, rcid=String(cid);
  let bytes; try{ const raw=atob(String(data||'')); bytes=new Uint8Array(raw.length); for(let i=0;i<raw.length;i++) bytes[i]=raw.charCodeAt(i); }
  catch(e){ return {ok:0, err:'invalid image payload'}; }
  const before=new Set((ms.getConversationMessages(rcid)||[]).map(m=>String(m.serverId||m.clientId||'')));
  const file=new File([bytes], String(name||'image'), {type:String(mime||'application/octet-stream')});
  let sent; try{ sent=await Promise.race([Promise.resolve(api.sendImageMessageWithFiles(rcid,[file])), new Promise(res=>setTimeout(()=>res('__to'),20000))]); }
  catch(e){ return {ok:0, err:String((e&&e.message)||e).slice(0,150)}; }
  if(sent==='__to') return {ok:0, err:'send timeout'};
  const extOf=(m)=>{ let x=m&&m.ext; if(typeof x==='string'){try{x=JSON.parse(x);}catch(e){x={};}} if(!x||typeof x!=='object')x={}; return x; };
  let flight=null, sid=null, media=null;
  for(let i=0;i<18;i++){ await new Promise(z=>setTimeout(z,600));
    const mine=(ms.getConversationMessages(rcid)||[]).filter(m=>m.isFromMe);
    const fresh=[...mine].reverse().find(m=>{const id=String(m.serverId||m.clientId||''); const x=extOf(m); return id&&!before.has(id)&&(x.type==='file_image'||x.imageUrl||x.imageUri);});
    if(fresh){ const x=extOf(fresh); flight=fresh.flightStatus; sid=String(fresh.serverId||''); media={type:'image',url:String(x.imageUrl||''),uri:String(x.imageUri||''),width:Number(x.imageWidth||0)||null,height:Number(x.imageHeight||0)||null}; }
    if(sid && (flight===3||flight===4)) break;
  }
  // 图片消息在平台分配 serverId 后可能不暴露 flightStatus；serverId 本身就是服务端已接收信号。
  const accepted=!!sid&&(flight===3||flight===4||flight==null);
  return {ok:accepted,signal:(accepted?(flight==null?'server_id':'flight'):null),flight,serverId:sid,cid:rcid,media};
}"""


# 发送前确保会话已激活/载入:SDK 的 sendMessage 依赖 changeContact 建立的会话上下文,
# 即使会话已经在 store，首次发送也不能跳过(缺了会报 pigeonBizType，2026-07-19 实测)。
# 冷缓存先按已知CID读取，再changeContact激活；不通过全量扫描恢复已有会话。
# oec 优先用调用方从库带来的真值;没有则试 store 里会话自带的 originExt。
_JS_ENSURE_CONV = "async ({cid, oec, attempts}) => {" + _FIND_API + r"""
  if(!api||!api.sdkInstance) return {ok:0, err:'SDK未就绪'};
  const sdk=api.sdkInstance, cs=sdk.conversationService;
  const wantedCid=String(cid||'');
  if(!wantedCid) return {ok:0,err:'conversation_id_required'};
  const find=()=>(cs.getAllConversations()||[]).find(
    c=>String(c.id||'')===wantedCid||String(c.shortId||'')===wantedCid
  );
  const expectedOec=String(oec||'');
  const identityMatches=c=>!expectedOec||String((c.originExt||{}).creator_oec_id||'')===expectedOec;
  let conv=find();
  if(conv&&!identityMatches(conv)) return {ok:0,err:'conversation_identity_mismatch'};
  if(!conv&&typeof sdk.pullConversationById==='function'){
    // 只读恢复已有CID，不创建会话、不发送；Promise完成不等于恢复成功。
    const pulled=await withTimeout(Promise.resolve().then(()=>sdk.pullConversationById(wantedCid)),4000);
    if(pulled==='__timeout__') return {ok:0,err:'conversation_cache_pull_timeout'};
    conv=find();
    if(conv&&!identityMatches(conv)) return {ok:0,err:'conversation_identity_mismatch'};
  }
  const useOec=expectedOec||(conv&&conv.originExt&&conv.originExt.creator_oec_id)||'';
  if(!useOec) return conv?{ok:1,note:'conversation_loaded_without_oec'}:{ok:0,err:'conversation_not_loaded'};
  const maxAttempts=Math.max(1,Math.min(3,Number(attempts)||3));
  for(let i=0;i<maxAttempts;i++){
    const activated=await Promise.race([
      Promise.resolve().then(()=>api.changeContact(useOec)).then(
        value=>({kind:'settled',value}),()=>({kind:'rejected'})
      ),
      new Promise(resolve=>setTimeout(()=>resolve({kind:'timeout'}),4000))
    ]);
    if(activated.kind==='timeout') return {ok:0,err:'conversation_activation_timeout'};
    if(activated.kind==='rejected'||activated.value===false) return {ok:0,err:'conversation_activation_rejected'};
    await new Promise(r=>setTimeout(r,700));
    conv=find();
    if(conv&&!identityMatches(conv)) return {ok:0,err:'conversation_identity_mismatch'};
    if(conv) break;
  }
  if(!conv) return {ok:0, err:'conversation_not_reachable'};
  return {ok:1};
}"""


def _log(m):
    print(m, flush=True)


def _wait_sdk(page, tries=25, interval_ms=2000, *, stop_when=None):
    """有界等待 SDK 账号上下文完成初始化，不要求预载历史会话。"""
    for attempt in range(tries):
        if stop_when is not None and stop_when():
            return False
        try:
            if page.evaluate(_JS_SDK_READY):
                return not (stop_when is not None and stop_when())
        except Exception:
            pass
        if attempt + 1 < tries:
            page.wait_for_timeout(interval_ms)
    return False


def _load_all(
    page,
    rounds=60,
    settle=4,
    max_timeouts=4,
    progress_callback=None,
):
    """翻页把会话灌全,直到数量连续 settle 轮不增。
    单次 pullConversations 超时【不】立即截断(那只是这次慢,async 常仍在后台灌);
    连续 max_timeouts 次超时才放弃,避免一次慢就漏掉后面的未读会话。"""
    prev, stable, tmo = -1, 0, 0
    for _ in range(rounds):
        try:
            r = page.evaluate(_JS_PULL_CONVS, {"n": 3})
        except Exception as e:
            _log(f"  载会话异常 {repr(e)[:60]}"); break
        if progress_callback is not None:
            progress_callback()
        cnt = r.get("count", 0)
        if r.get("err"):
            return cnt
        if r.get("timedout"):
            tmo += 1
            if tmo >= max_timeouts:
                return cnt
            if cnt != prev:               # 超时但后台已灌进新会话 → 重置稳定计数继续
                stable = 0; prev = cnt; _log(f"  载入会话 {cnt} …(慢,继续)")
            continue
        tmo = 0
        if cnt == prev:
            stable += 1
            if stable >= settle:
                return cnt
        else:
            stable = 0; _log(f"  载入会话 {cnt} …")
        prev = cnt
    return prev
