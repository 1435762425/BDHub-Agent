import {execFile} from "node:child_process";
import {existsSync} from "node:fs";
import {dirname,join,resolve} from "node:path";
import {InputError} from "../runtime/validation.ts";
import type {ProfileRefreshRequest} from "../../features/creator-identities/refresh-contracts.ts";
import {enabledMarket} from "../markets/registry.ts";

export class ProfileRefreshError extends Error {readonly code:string;readonly status:number;constructor(code:string,status:number,message:string){super(message);this.code=code;this.status=status;}}
export type RefreshCommand="enqueue"|"status"|"list";
const messages:Record<string,[number,string]>={
  idempotency_conflict:[409,"此请求编号已用于另一位达人，请恢复原请求或重新发起。"],
  creator_not_found:[404,"没有找到已核验的达人档案。"],
  identity_not_found:[404,"没有找到已核验的达人档案。"],
  job_not_found:[404,"没有找到此刷新任务。"],
  unsupported_market:[409,"此市场尚未接通页面画像刷新。"],
  invalid_input:[400,"刷新请求格式不正确。"],
  invalid_request:[400,"刷新请求格式不正确。"],
  identity_changed:[409,"达人身份与任务不一致，请重新读取档案。"],
};
export function parseRefreshRequest(value:unknown):ProfileRefreshRequest {
  if(!value||typeof value!=="object"||Array.isArray(value))throw new InputError("刷新请求必须为对象。");
  const row=value as Record<string,unknown>;
  if(Object.keys(row).some(k=>!["market","creatorId","requestId"].includes(k))||typeof row.market!=="string"||!enabledMarket(row.market)||typeof row.creatorId!=="string"||!/^creator_[a-f0-9]{32}$/.test(row.creatorId)||typeof row.requestId!=="string"||!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$/.test(row.requestId))throw new InputError("缺少有效的市场、达人或请求编号。");
  return {market:row.market,creatorId:row.creatorId,requestId:row.requestId};
}
export function parseRefreshQuery(url:string):{command:"list"|"status";input:Record<string,string>} {
  const q=new URL(url).searchParams;
  if([...q.keys()].some(k=>!["market","creatorId","jobId"].includes(k))||q.getAll("market").length!==1||[...q.keys()].length!==2)throw new InputError("请指定市场和一位达人或一个刷新任务。");
  const market=q.get("market"),creator=q.get("creatorId"),job=q.get("jobId");
  if(!market||!enabledMarket(market))throw new InputError("市场无效。");
  if(creator&&/^creator_[a-f0-9]{32}$/.test(creator))return {command:"list",input:{market,creatorId:creator}};
  if(job&&/^[A-Za-z0-9][A-Za-z0-9_-]{0,119}$/.test(job))return {command:"status",input:{market,jobId:job}};
  throw new InputError("达人或刷新任务编号无效。");
}
export function projectRoot(start=process.cwd()):string {
  for(let dir=resolve(start);;dir=dirname(dir)){
    if(existsSync(join(dir,"scripts/lib/creator_identity.py"))&&existsSync(join(dir,"apps/web/package.json")))return dir;
    if(dirname(dir)===dir)break;
  }
  throw new ProfileRefreshError("refresh_unavailable",503,"未定位到本机画像工作目录。");
}
export function decodeRefreshOutput(output:string):unknown {
  let parsed:unknown;try{parsed=JSON.parse(output);}catch{throw new ProfileRefreshError("refresh_unavailable",503,"画像服务响应不完整，任务编号已保留。");}
  if(!parsed||typeof parsed!=="object")throw new ProfileRefreshError("refresh_unavailable",503,"画像服务响应不完整。");
  if("error" in parsed){const error=(parsed as {error?:{code?:unknown}}).error;const code=typeof error?.code==="string"?error.code:"refresh_unavailable";const definition=messages[code];throw new ProfileRefreshError(definition?code:"refresh_unavailable",definition?.[0]??503,definition?.[1]??"画像服务暂不可用，原有档案保持不变。");}
  return parsed;
}
export function callRefreshCommand(command:RefreshCommand,input:Record<string,string>|ProfileRefreshRequest):Promise<unknown> {
  if(input.market!=="it")throw new ProfileRefreshError("unsupported_market",409,"此市场尚未接通页面画像刷新。");
  const root=projectRoot();
  return new Promise((resolveResult,reject)=>{
    const child=execFile(join(root,".venv/bin/python"),[join(root,"scripts/creator-profile-refresh.py"),command],
      {cwd:root,timeout:10000,maxBuffer:128*1024,env:{...process.env,PYTHONDONTWRITEBYTECODE:"1"}},(error,stdout)=>{
        try{if(error&&!stdout.trim())throw new ProfileRefreshError("refresh_unavailable",503,"暂未确认刷新请求，请保留原编号后重试。");resolveResult(decodeRefreshOutput(stdout));}catch(failure){reject(failure);}
      });
    const {market:_,...payload}=input;child.stdin?.on("error",()=>{});child.stdin?.end(JSON.stringify(payload));
  });
}
