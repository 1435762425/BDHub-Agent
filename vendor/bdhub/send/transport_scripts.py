"""Shared browser scripts used by the active send adapters.

The retired dispatch pipeline used to own these scripts even though the
live worker and the ad-hoc sender still depended on them.  Keeping the
protocol blobs here lets those active paths share one implementation while
leaving ``dispatch`` as a compatibility re-export for now.
"""
from __future__ import annotations

from ..imbase.transport import _FIND_API


_CARD_TITLE_KEY = "ttspc_im_communication_list_preview_message_title3"


_JS_RESOLVE_SERIAL_LEGACY = "async ({oecs, probe}) => {" + _FIND_API + r"""
  if(!api||!api.sdkInstance) return {results:[], err:'no api'};
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  const cs=api.sdkInstance.conversationService;
  const findCid=oec=>{ const c=(cs.getAllConversations()||[]).find(x=>(x.originExt||{}).creator_oec_id===String(oec)); return c?String(c.id||c.shortId):null; };
  const out=[], timing=[];
  for(const oec of oecs){
    let cid=findCid(oec);                       // 已在 store(热达人/上一次拉过)→ 直接命中,免 changeContact
    if(!cid){
      const t0=Date.now(); let settled=false, tSettle=null;
      // 【修复 Codex#1】不再 Promise.race-abandon:发起 changeContact 后保留其 promise,标记 settle
      const cc=Promise.resolve(api.changeContact(String(oec)))
        .then(()=>{},()=>{}).finally(()=>{ settled=true; tSettle=Date.now()-t0; });
      // 精确等:一旦 store 里出现该 oec 会话就停;否则等到 settle 再确认一次;硬顶 8s 防挂死
      while(Date.now()-t0<8000){
        await sleep(120);
        cid=findCid(oec);
        if(cid) break;
        if(settled){ cid=findCid(oec); break; }   // changeContact 已 settle 仍无 → 本次真没拉到
      }
      await cc;                                    // 【核心修复】进下一个 oec 前,确保本次 changeContact 彻底 settle,杜绝后台重叠污染
      if(probe) timing.push({oec:String(oec), cid, ms:Date.now()-t0, ms_settle:tSettle, cid_before_settle: !!cid && (tSettle===null || (Date.now()-t0)<tSettle)});
    }
    if(cid) out.push({oec:String(oec), cid});
  }
  return {results:out, timing};
}"""


_JS_RESOLVE_SERIAL = "async ({oecs, probe}) => {" + _FIND_API + r"""
  if(!api||!api.sdkInstance) {
    return {results:(oecs||[]).map(oec=>({
      oec:String(oec), cid:null, method:'none',
      classification:'sdk_unavailable', business_code:null,
      change_contact:{attempted:false,settled:false,resolved:false},
      network_observed:null, elapsed_ms:0
    })), err:'no api'};
  }
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  const cs=api.sdkInstance.conversationService;
  const findCid=oec=>{
    const c=(cs.getAllConversations()||[]).find(
      x=>(x.originExt||{}).creator_oec_id===String(oec)
    );
    return c?String(c.id||c.shortId):null;
  };
  const out=[], timing=[];
  for(const oec of oecs){
    const cleanOec=String(oec), startedAt=Date.now();
    let cid=findCid(cleanOec);
    let method='sdk_store', classification='resolved', businessCode=null;
    let changeContact={
      attempted:false, settled:false, resolved:!!cid, rejected:false
    };
    if(!cid){
      method='change_contact';
      let settled=false, rejected=false, response=null;
      let detail=null, tSettle=null, request;
      try { request=api.changeContact(cleanOec); }
      catch(e){
        rejected=true;
        detail=String((e&&e.message)||e).slice(0,200);
      }
      const cc=Promise.resolve(request)
        .then(value=>{ response=value; }, error=>{
          rejected=true;
          detail=String((error&&error.message)||error).slice(0,200);
        })
        .finally(()=>{
          settled=true;
          tSettle=Date.now()-startedAt;
        });
      while(Date.now()-startedAt<8000){
        await sleep(120);
        cid=findCid(cleanOec);
        if(cid) break;
        if(settled){ cid=findCid(cleanOec); break; }
      }
      await cc;
      cid=cid||findCid(cleanOec);
      if(response&&typeof response==='object'){
        businessCode=(
          response.code ?? response.status_code ?? response.statusCode ?? null
        );
      }
      if(cid) classification='resolved';
      else if(rejected) classification='change_contact_rejected';
      else if(businessCode!==null && Number(businessCode)!==0) {
        classification='business_rejected';
      }
      else if(!settled) classification='change_contact_timeout';
      else classification='settled_without_conversation';
      changeContact={
        attempted:true, settled, resolved:!!cid, rejected,
        ms_settle:tSettle, detail
      };
    }
    const row={
      oec:cleanOec, cid:cid||null, method, classification,
      business_code:businessCode, change_contact:changeContact,
      network_observed:null, elapsed_ms:Date.now()-startedAt
    };
    out.push(row);
    if(probe) timing.push(row);
  }
  return {results:out, timing};
}"""


# Generic IM pages cannot create a cold creator context with ``changeContact``
# alone: the promise often settles in a few milliseconds without issuing a
# network request.  The native Creator Marketplace contact route first opens a
# creator-targeted IM context; only then can the SDK expose a CID.  Keep this
# resolver separate from the main page so navigation never destroys the
# resident worker's hot conversation store.
_JS_RESOLVE_TARGETED = "async ({oec}) => {" + _FIND_API + r"""
  if(!api||!api.sdkInstance) {
    return {ok:0, cid:null, classification:'sdk_unavailable', attempts:0, elapsed_ms:0};
  }
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  const startedAt=Date.now();
  const cs=api.sdkInstance.conversationService;
  const cleanOec=String(oec);
  const findCid=()=>{
    const row=(cs.getAllConversations()||[]).find(
      item=>String((item.originExt||{}).creator_oec_id||'')===cleanOec
    );
    return row?String(row.id||row.shortId||''):null;
  };
  let cid=findCid(), attempts=0;
  while(!cid && attempts<10){
    attempts+=1;
    try{
      await Promise.race([
        Promise.resolve(api.changeContact(cleanOec)),
        new Promise(resolve=>setTimeout(()=>resolve('__timeout__'),2500))
      ]);
    }catch(_error){}
    cid=findCid();
    if(cid) break;
    await sleep(800);
    cid=findCid();
  }
  return {
    ok:!!cid,
    cid:cid||null,
    classification:cid?'resolved':'targeted_context_unresolved',
    attempts,
    elapsed_ms:Date.now()-startedAt
  };
}"""


_JS_SEND_CARD = "async ({cid, card, dry}) => {" + _FIND_API + r"""
  if(!api||!api.sdkInstance) return {ok:0, err:'no api'};
  const sdk=api.sdkInstance, ms=sdk.messageService, rcid=String(cid);
  if(!card||!card.product_id||!card.list_id) return {ok:0, err:'缺商品卡数据(需 product_id + list_id)'};
  const ext={type:'product_list', starling_content_key: card.title_key||'""" + _CARD_TITLE_KEY + r"""',
    list_id:String(card.list_id), campaign_id:String(card.campaign_id||'0'),
    campaign_name:String(card.campaign_name||''), list_name:String(card.list_name||''),
    product_id:String(card.product_id),
    'b:oec_im_search_context':JSON.stringify({sender_name:String(card.handle||''), search_content_map:{name:String(card.handle||'')}})};
  if(dry) return {ok:1, dry:1, cid:rcid, would_pid:String(card.product_id)};
  // 新本地消息不代表平台已受理，必须有新增服务端ID和成功flight。
  const cardMsgs=()=>(ms.getConversationMessages(rcid)||[]).filter(m=>m.isFromMe&&(m.content||'').indexOf('商品列表')>=0);
  const initial=cardMsgs(), before=initial.length;
  const beforeIds=new Set(initial.flatMap(m=>[String(m.serverId||''),String(m.clientId||'')]).filter(Boolean));
  let r; try{ r=await Promise.race([Promise.resolve(ms.sendMessage(rcid,{content:'[商品列表]', ext})), new Promise(res=>setTimeout(()=>res('__to'),9000))]); }
  catch(e){ return {ok:0, err:String((e&&e.message)||e).slice(0,150)}; }
  if(r==='__to') return {ok:0, err:'card send timeout'};
  let after=[], fresh=null;
  for(let i=0;i<37;i++){
    if(i) await new Promise(z=>setTimeout(z,200));
    after=cardMsgs();
    fresh=[...after].reverse().find(m=>{
      const sid=String(m.serverId||''), clientId=String(m.clientId||'');
      let x=m.ext||{}; if(typeof x==='string'){try{x=JSON.parse(x);}catch(_){return false;}}
      return (sid||clientId)&&!beforeIds.has(sid)&&!beforeIds.has(clientId)
        &&String(x.product_id||'')===String(card.product_id);
    });
    if(fresh&&(fresh.flightStatus<0||(fresh.serverId&&(fresh.flightStatus===3||fresh.flightStatus===4)))) break;
  }
  const flight=fresh?fresh.flightStatus:null, sid=fresh?String(fresh.serverId||''):null;
  const ok=!!sid&&(flight===3||flight===4);
  return {ok, serverId:sid, flight, cid:rcid, before, after:after.length, err:ok?null:'card receipt unknown'};
}"""


_JS_PRODUCT_SEARCH_CANARY = r"""async ({pid, host, partnerId, aid, userLanguage}) => {
  const clean = value => String(value == null ? '' : value);
  const safeKeys = value => (
    value && typeof value === 'object' && !Array.isArray(value)
      ? Object.keys(value).sort().slice(0, 40)
      : []
  );
  const result = {
    kind: 'transport_error', http_status: null, response_code: null,
    result_count: null, product_count: null, card_metadata_count: null,
    pid_field_observed: false, exact_pid_match: false,
    top_level_keys: [], data_keys: [], list_item_keys: [], product_item_keys: []
  };
  try {
    const query = new URLSearchParams({
      cur_page: '1', page_size: '10', version: '1', search_type: '2',
      key_word: clean(pid), user_language: clean(userLanguage || 'zh-CN'),
      partner_id: clean(partnerId), aid: clean(aid),
      app_name: 'i18n_ecom_alliance', device_platform: 'web'
    });
    const response = await fetch(
      clean(host) + '/api/v1/affiliate/partner/im/product_list/list?' + query,
      {credentials: 'include', headers: {accept: 'application/json'}}
    );
    result.http_status = Number(response.status);
    const text = await response.text();
    let payload = null;
    try { payload = JSON.parse(text); }
    catch (_) { result.kind = 'non_json'; return result; }
    result.top_level_keys = safeKeys(payload);
    result.response_code = payload && payload.code != null ? payload.code : null;
    const data = payload && payload.data;
    result.data_keys = safeKeys(data);
    if (!payload || Number(payload.code) !== 0) {
      result.kind = 'business_error';
      return result;
    }
    if (!data || !Array.isArray(data.list)) {
      result.kind = 'malformed_result';
      return result;
    }
    const lists = data.list.filter(item => item && typeof item === 'object');
    result.result_count = lists.length;
    if (!lists.length) {
      result.kind = 'empty_result';
      return result;
    }
    result.list_item_keys = safeKeys(lists[0]);
    result.card_metadata_count = lists.filter(
      item => clean(item.product_list_id).length > 0
    ).length;
    const products = lists.flatMap(item => (
      Array.isArray(item.campaign_products)
        ? item.campaign_products.filter(row => row && typeof row === 'object')
        : []
    ));
    result.product_count = products.length;
    if (products.length) result.product_item_keys = safeKeys(products[0]);
    const productIds = [];
    for (const product of products) {
      for (const key of ['product_id', 'productId']) {
        if (product[key] != null && clean(product[key]).length > 0) {
          result.pid_field_observed = true;
          productIds.push(clean(product[key]));
        }
      }
    }
    result.exact_pid_match = productIds.some(value => value === clean(pid));
    result.kind = (
      result.card_metadata_count > 0 &&
      products.length > 0 &&
      result.pid_field_observed &&
      result.exact_pid_match
    ) ? 'valid_result' : 'malformed_result';
    return result;
  } catch (_) {
    result.kind = 'transport_error';
    return result;
  }
}"""


_JS_CARD_SEARCH = r"""async ({pid, host, partnerId, aid, listId, sourceCampaignId, campaignId, listName}) => {
  const matches = new Map();
  try {
    for (let page = 1; page <= 20; page++) {
      const query = new URLSearchParams({cur_page:String(page),page_size:'20',version:'1',search_type:'2',
        key_word:String(pid),user_language:'zh-CN',partner_id:partnerId,aid,app_name:'i18n_ecom_alliance',device_platform:'web'});
      const r=await fetch(host+'/api/v1/affiliate/partner/im/product_list/list?'+query,{credentials:'include',headers:{accept:'application/json'}});
      if(r.status!==200||r.headers.get('bdturing-verify')) return {ok:0,err:'商品卡查询未通过'};
      const j=await r.json();
      if(j.code!==0) return {ok:0,err:'code '+j.code};
      const rows=j.data&&j.data.list;
      if(!Array.isArray(rows)) return {ok:0,err:'商品列表响应不完整'};
      for(const row of rows) {
        const lid=String(row.product_list_id||'');
        if(!/^[1-9][0-9]*$/.test(lid)||(listId&&lid!==listId)) continue;
        const products=(Array.isArray(row.campaign_products)?row.campaign_products:[]).filter(p=>String(p.product_id||'')===String(pid));
        if(products.length!==1) continue;
        const product=products[0], topCampaign=String(row.campaign_id||'0');
        if(product.stock!=null&&(!Number.isFinite(Number(product.stock))||Number(product.stock)<=0)) continue;
        if(product.is_under_governed===true||![null,undefined,'',0,'0'].includes(product.unavailable_type)) continue;
        if(listId&&(String(row.product_list_name||'')!==listName||topCampaign!==campaignId)) continue;
        const source=String(product.campaign_id||(product.campaign_info||{}).campaign_id||topCampaign);
        if(sourceCampaignId&&source!==sourceCampaignId) continue;
        matches.set(lid,{ok:1,list_id:lid,campaign_id:topCampaign,campaign_name:String(row.campaign_name||''),list_name:String(row.product_list_name||'')});
      }
      if(listId&&matches.size===1) return matches.values().next().value;
      if(rows.length<20) {
        if(matches.size===1) return matches.values().next().value;
        return {ok:0,err:matches.size?'PID 对应多个商品列表，请指定已核验的 TapLink':'PID 无货盘(该 partner 搜不到)'};
      }
    }
    return {ok:0,err:'商品列表查询超过分页范围，不能确认唯一绑定'};
  } catch(e){ return {ok:0, err:'商品卡查询异常'}; }
}"""


__all__ = [
    "_CARD_TITLE_KEY",
    "_JS_CARD_SEARCH",
    "_JS_PRODUCT_SEARCH_CANARY",
    "_JS_RESOLVE_SERIAL",
    "_JS_RESOLVE_TARGETED",
    "_JS_SEND_CARD",
]
