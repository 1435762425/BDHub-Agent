import {InputError} from "../runtime/validation.ts";
import type {MatchingCommand,MatchMarket} from "../../features/matching/contracts.ts";

function object(value:unknown):Record<string,unknown>{
  if(!value||typeof value!=="object"||Array.isArray(value))throw new InputError("请求必须是 JSON 对象。");
  return value as Record<string,unknown>;
}
function only(value:Record<string,unknown>,keys:string[]){if(Object.keys(value).some(k=>!keys.includes(k)))throw new InputError("包含不支持的字段。");}
function id(value:unknown,label:string){if(typeof value!=="string"||!value||value.length>160||! /^[A-Za-z0-9][A-Za-z0-9:._-]*$/.test(value))throw new InputError(`${label}格式不正确。`);return value;}
function member<T extends string>(value:unknown,values:readonly T[],label:string):T{if(typeof value!=="string"||!values.includes(value as T))throw new InputError(`${label}不受支持。`);return value as T;}
function integer(value:unknown,min:number,max:number,label:string){if(typeof value!=="number"||!Number.isSafeInteger(value)||value<min||value>max)throw new InputError(`${label}应为${min}–${max}之间的整数。`);return value;}

export function parseMatchingQuery(url:string){
  const params=new URL(url).searchParams;
  for(const key of params.keys())if(!["view","market","q","offset","limit","dataset","runId"].includes(key))throw new InputError("不支持此查询参数。");
  if([...params.keys()].some(key=>params.getAll(key).length!==1))throw new InputError("查询参数不能重复。");
  const dataset=member(params.get("dataset")||"demo",["demo","italy","italy-profiles"] as const,"数据集");
  const view=member(params.get("view")||"stats",["stats","products","creators","assessments","run"] as const,"查询类型");
  const runId=view==="assessments"||view==="run"?id(params.get("runId"),"召回编号"):undefined;
  if(view!=="assessments"&&view!=="run"&&params.has("runId"))throw new InputError("此查询不接受召回编号。");
  const rawMarket=params.get("market");
  const market=rawMarket&&rawMarket!=="all"?member(rawMarket,["mx","br","it"] as const,"市场"):undefined;
  const q=params.get("q")||"";if(q.length>120)throw new InputError("搜索关键词过长。");
  const offset=integer(Number(params.get("offset")??0),0,1000000,"分页位置");
  const limit=integer(Number(params.get("limit")??20),1,50,"每页条数");
  return {view,dataset,runId,options:{market:market as MatchMarket|undefined,q,offset,limit}};
}

export function parseMatchingCommand(value:unknown):{requestId:string;command:MatchingCommand}{
  const root=object(value);only(root,["requestId","command"]);
  const requestId=id(root.requestId,"请求编号");const c=object(root.command);
  if(c.type==="recall"){
    only(c,["type","query"]);const q=object(c.query);only(q,["direction","subjectId","source","limit"]);
    return {requestId,command:{type:"recall",query:{direction:member(q.direction,["product","creator"] as const,"匹配方向"),subjectId:id(q.subjectId,"主体编号"),source:member(q.source,["all","first","second"] as const,"候选来源"),limit:integer(q.limit,1,50,"候选数量")}}};
  }
  if(c.type==="prepare_review"){
    only(c,["type","runId","creatorId"]);
    return {requestId,command:{type:"prepare_review",runId:id(c.runId,"召回编号"),creatorId:id(c.creatorId,"达人编号")}};
  }
  if(c.type==="assess_candidate") {
    only(c,["type","runId","creatorId","productId","label","note","expectedRevision"]);
    const label=c.label===null?null:member(c.label,["suitable","unsuitable","insufficient"] as const,"人工判断");
    if(typeof c.note!=="string"||c.note.length>1000)throw new InputError("评审理由应为最多 1000 字的文本。");
    return {requestId,command:{type:"assess_candidate",runId:id(c.runId,"召回编号"),creatorId:id(c.creatorId,"达人编号"),productId:id(c.productId,"商品编号"),label,note:c.note.trim(),expectedRevision:integer(c.expectedRevision,0,Number.MAX_SAFE_INTEGER,"评审版本")}};
  }
  if(c.type==="demo_change"){
    only(c,["type","productId","expectedRevision","change"]);
    return {requestId,command:{type:"demo_change",productId:id(c.productId,"商品编号"),expectedRevision:integer(c.expectedRevision,1,Number.MAX_SAFE_INTEGER,"当前商业版本"),change:member(c.change,["raise_price","lower_price","offer_unavailable","offer_available"] as const,"演示变化")}};
  }
  throw new InputError("不支持此匹配操作。");
}
