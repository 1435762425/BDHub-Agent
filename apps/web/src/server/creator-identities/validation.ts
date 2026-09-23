import type {CreatorIdentityQuery,IdentityMarket} from "../../features/creator-identities/contracts.ts";
import {InputError} from "../runtime/validation.ts";
import {enabledMarket} from "../markets/registry.ts";

export function parseCreatorIdentityQuery(url:string):CreatorIdentityQuery {
  if(url.length>4096)throw new InputError("请求地址过长。");
  const params=new URL(url).searchParams,view=params.get("view")||"overview";
  const allowed=view==="overview"?["view","market"]:view==="list"?["view","market","status","q","offset","limit"]:
    view==="detail"?["view","market","creatorId"]:view==="source"?["view","market","oecId","externalId"]:null;
  if(!allowed)throw new InputError("不支持此身份查询。");
  for(const key of params.keys())if(!allowed.includes(key)||params.getAll(key).length!==1)throw new InputError("查询包含不支持或重复的字段。");
  const market=params.get("market");
  if(!market||!enabledMarket(market))throw new InputError("此市场尚未启用。");
  if(view==="detail"){
    const creatorId=params.get("creatorId")||"";
    if(!/^creator_[a-f0-9]{32}$/.test(creatorId))throw new InputError("达人身份标识格式不正确。");
    return {view,market,creatorId};
  }
  if(view==="overview")return {view,market};
  if(view==="list"){
    const status=params.get("status")||"verified",q=(params.get("q")||"").trim().replace(/^@/,"");
    if(status!=="verified"&&status!=="pending")throw new InputError("不支持此身份状态。");
    if(q.length>80||/[\u0000-\u001f\u007f]/.test(q))throw new InputError("搜索内容格式不正确。");
    const integer=(key:string,fallback:number,max:number)=>{const value=params.get(key);if(value===null)return fallback;if(!/^\d{1,7}$/.test(value))throw new InputError("分页格式不正确。");const n=Number(value);if(n>max||(key==="limit"&&n<1))throw new InputError("分页超出允许范围。");return n;};
    return {view,market,status,q,offset:integer("offset",0,1_000_000),limit:integer("limit",20,100)};
  }
  const oecId=params.get("oecId")??undefined,externalId=params.get("externalId")??undefined;
  if(oecId!==undefined&&!/^[0-9]{1,64}$/.test(oecId))throw new InputError("OEC 格式不正确。");
  if(externalId!==undefined&&(!externalId.trim()||externalId.length>256||/[\u0000-\u001f\u007f]/.test(externalId)))throw new InputError("来源标识格式不正确。");
  if(oecId===undefined&&externalId===undefined)throw new InputError("请提供 OEC 或来源标识。");
  return {view:"source",market,...(oecId!==undefined?{oecId}:{}),...(externalId!==undefined?{externalId}:{})};
}
