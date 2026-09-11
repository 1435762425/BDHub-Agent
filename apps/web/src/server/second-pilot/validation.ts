import {InputError} from "../runtime/validation.ts";
export type SecondPilotCommand=
  |{type:"freeze";caseId:string;expectedRevision:number}
  |{type:"queue";snapshotId:string;scenario:"accepted"|"receipt_lost"|"before_submit_crash"}
  |{type:"control";caseId:string;expectedRevision:number;mode:"running"|"paused"}
  |{type:"verify";actionId:string};
function obj(value:unknown):Record<string,unknown>{if(!value||typeof value!=="object"||Array.isArray(value))throw new InputError("请求应为JSON对象。");return value as Record<string,unknown>;}
function only(v:Record<string,unknown>,keys:string[]){if(Object.keys(v).some(k=>!keys.includes(k)))throw new InputError("包含不支持的字段，预演不能切换为真实发送。");}
function id(value:unknown){if(typeof value!=="string"||! /^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$/.test(value))throw new InputError("记录编号不正确。");return value;}
function revision(value:unknown){if(typeof value!=="number"||!Number.isSafeInteger(value)||value<1)throw new InputError("请提供当前版本。");return value;}
function member<T extends string>(value:unknown,choices:readonly T[]):T{if(typeof value!=="string"||!choices.includes(value as T))throw new InputError("不支持此预演选项。");return value as T;}
export function parseSecondPilotCommand(value:unknown):{requestId:string;command:SecondPilotCommand}{
  const envelope=obj(value);only(envelope,["requestId","command"]);const requestId=id(envelope.requestId),c=obj(envelope.command);
  if(c.type==="freeze"){only(c,["type","caseId","expectedRevision"]);return {requestId,command:{type:c.type,caseId:id(c.caseId),expectedRevision:revision(c.expectedRevision)}};}
  if(c.type==="queue"){only(c,["type","snapshotId","scenario"]);return {requestId,command:{type:c.type,snapshotId:id(c.snapshotId),scenario:member(c.scenario,["accepted","receipt_lost","before_submit_crash"] as const)}};}
  if(c.type==="control"){only(c,["type","caseId","expectedRevision","mode"]);return {requestId,command:{type:c.type,caseId:id(c.caseId),expectedRevision:revision(c.expectedRevision),mode:member(c.mode,["running","paused"] as const)}};}
  if(c.type==="verify"){only(c,["type","actionId"]);return {requestId,command:{type:c.type,actionId:id(c.actionId)}};}
  throw new InputError("本入口只支持二发本地预演，不支持真实发送。");
}
export function parseSecondPilotQuery(url:string){
  const p=new URL(url).searchParams;
  for(const key of p.keys())if(!["view","offset","limit","caseId"].includes(key)||p.getAll(key).length!==1)throw new InputError("不支持或重复的查询参数。");
  const view=member(p.get("view")||"overview",["overview","cases","case"] as const);
  const offset=Number(p.get("offset")||0),limit=Number(p.get("limit")||20);
  if(!Number.isSafeInteger(offset)||offset<0||offset>100000||!Number.isSafeInteger(limit)||limit<1||limit>50)throw new InputError("分页超出允许范围。");
  if(view!=="case"&&p.has("caseId"))throw new InputError("此查询不接受caseId。");
  return {view,offset,limit,caseId:view==="case"?id(p.get("caseId")):undefined};
}
