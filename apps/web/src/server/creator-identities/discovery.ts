import {execFile} from "node:child_process";
import {existsSync} from "node:fs";
import {dirname,join,resolve} from "node:path";
import {InputError,isLocalRequest} from "../runtime/validation.ts";
import type {CreatorDiscoveryCommand,CreatorDiscoveryResponse,DiscoveryBatch,DiscoveryBatchCounts,DiscoveryBatchItem,DiscoveryDetail,DiscoveryList,DiscoveryPreview,DiscoveryPreviewItem} from "../../features/creator-identities/discovery-contracts.ts";

type ObjectValue=Record<string,unknown>;
type DiscoveryCall={command:CreatorDiscoveryCommand;input:ObjectValue};
const BATCH=/^discovery_[a-f0-9]{32}$/,ITEM=/^discovery_item_[a-f0-9]{32}$/,CREATOR=/^creator_[a-f0-9]{32}$/;
const REQUEST=/^[A-Za-z0-9_.:-]{1,160}$/,HASH=/^[a-f0-9]{64}$/;
const LABEL_MAX=120,TEXT_BYTES=64*1024,BODY_BYTES=128*1024;
const messages:Record<string,[number,string]>={
  invalid_request:[400,"名单请求格式不正确。"],invalid_input:[400,"名单请求格式不正确。"],
  unsupported_market:[409,"批量名单发现目前仅接通意大利。"],
  preview_mismatch:[409,"名单与预览已不一致，请重新预览后提交。"],
  empty_batch:[400,"没有可提交的有效 handle，请先修正名单。"],
  input_limit_exceeded:[400,"每次最多 500 条、64 KiB 名单。"],
  batch_not_found:[404,"没有找到此名单批次。"],
  idempotency_conflict:[409,"此请求编号已用于不同内容，请恢复原请求或使用新编号。"],
  batch_not_resumable:[409,"此批次当前不能恢复，请刷新批次状态。"],
  schema_mismatch:[503,"名单任务库格式不兼容，请检查本地运行底座。"],
  invalid_schema:[503,"名单任务库格式不兼容，请检查本地运行底座。"],
  runtime_unavailable:[503,"名单运行环境暂不可用，请检查本地运行底座。"],
  internal_error:[503,"名单服务暂不可用，请保留当前请求编号后重试。"],
  payload_too_large:[413,"请求内容超过 128 KiB。"],
};
const reasonCodes=new Set(["invalid_handle","unsupported_url","duplicate_handle","looks_like_id","no_exact_handle","find_identity_invalid","find_market_mismatch","profile_identity_mismatch","profile_market_mismatch","supplement_identity_mismatch","supplement_market_mismatch","merged_identity_not_confirmed","verification_required","remote_error","whole_probe_deadline","request_or_signer_error","account_not_startable","account_not_prepared","identity_changed_before_guard","maintenance_due","shared_backoff","account_maintenance_or_shared_backoff","probe_initialization_or_validation_error","child_no_report","worker_interrupted","worker_interrupted_inflight","identity_mismatch","find_failed","profile_failed","profile_incomplete","find_remote_error","profile_remote_error","request_timeout","request_error","profile_wall_verification_failed","profile_wall_verification_required","identity_changed","identity_not_found","creator_not_found","unknown","timeout","not_found","blocked","error","probe_timeout","probe_failed","probe_report_missing","probe_report_invalid","identity_import_failed","lease_expired_no_report","attempt_exists_without_final","stale_lease","job_not_found","identity_policy_unreadable","published_identity_policy_invalid"]);
export class CreatorDiscoveryError extends Error {
  readonly code:string;readonly status:number;
  constructor(code:string,status:number,message:string){super(message);this.name="CreatorDiscoveryError";this.code=code;this.status=status;}
}
function unavailable(){return new CreatorDiscoveryError("discovery_unavailable",503,"名单服务暂不可用，请保留当前请求编号后重试。");}
function object(value:unknown):ObjectValue {if(!value||typeof value!=="object"||Array.isArray(value))throw new InputError("请求必须为 JSON 对象。");return value as ObjectValue;}
function only(row:ObjectValue,keys:string[]){if(Object.keys(row).some(key=>!keys.includes(key)))throw new InputError("请求包含不支持的字段。");}
function match(value:unknown,pattern:RegExp,label:string):string {if(typeof value!=="string"||!pattern.test(value))throw new InputError(`${label} 格式不正确。`);return value;}
function source(row:ObjectValue):ObjectValue{
  if(row.market!=="it")throw new CreatorDiscoveryError("unsupported_market",409,messages.unsupported_market[1]);
  if(typeof row.sourceLabel!=="string"||!row.sourceLabel.trim()||row.sourceLabel.trim().length>LABEL_MAX||/[\u0000-\u001f\u007f]/.test(row.sourceLabel))throw new InputError("请填写 1–120 字的来源名称。");
  if(typeof row.text!=="string")throw new InputError("请提供文本名单。");
  if(new TextEncoder().encode(row.text).byteLength>TEXT_BYTES||row.text.split(/\r\n|\r|\n/).filter(line=>line.trim()).length>500)throw new CreatorDiscoveryError("input_limit_exceeded",400,messages.input_limit_exceeded[1]);
  return {market:"it",sourceLabel:row.sourceLabel.trim(),text:row.text};
}
export function parseDiscoveryPost(value:unknown):DiscoveryCall{
  const row=object(value);
  if(row.command==="preview"||row.command==="submit"){
    only(row,["command","market","sourceLabel","text",...(row.command==="submit"?["previewHash","requestId"]:[])]);
    const input=source(row);
    if(row.command==="submit"){
      if(!(input.text as string).trim())throw new CreatorDiscoveryError("empty_batch",400,messages.empty_batch[1]);
      input.previewHash=match(row.previewHash,HASH,"预览编号");input.requestId=match(row.requestId,REQUEST,"请求编号");
    }
    return {command:row.command,input};
  }
  if(row.command==="control"){
    only(row,["command","batchId","action","requestId"]);
    if(row.action!=="pause"&&row.action!=="resume")throw new InputError("不支持此批次操作。");
    return {command:"control",input:{batchId:match(row.batchId,BATCH,"批次编号"),action:row.action,requestId:match(row.requestId,REQUEST,"请求编号")}};
  }
  throw new InputError("不支持此名单操作。");
}
export function parseDiscoveryQuery(url:string):DiscoveryCall{
  if(url.length>4096)throw new InputError("请求地址过长。");
  const params=new URL(url).searchParams,view=params.get("view")||"list",allowed=view==="list"?["view"]:view==="detail"?["view","batchId"]:null;
  if(!allowed)throw new InputError("不支持此名单查询。");
  for(const key of params.keys())if(!allowed.includes(key)||params.getAll(key).length!==1)throw new InputError("查询包含不支持或重复的字段。");
  return view==="list"?{command:"list",input:{}}:{command:"detail",input:{batchId:match(params.get("batchId"),BATCH,"批次编号")}};
}
function normalizedCall(command:CreatorDiscoveryCommand,input:ObjectValue):DiscoveryCall{
  if(command==="list"){only(object(input),[]);return {command,input:{}};}
  if(command==="detail"){only(object(input),["batchId"]);return {command,input:{batchId:match(input.batchId,BATCH,"批次编号")}};}
  return parseDiscoveryPost({...object(input),command});
}
function rootPath(start=process.cwd()):string{
  for(let dir=resolve(start);;dir=dirname(dir)){
    if(existsSync(join(dir,"scripts/lib/creator_identity.py"))&&existsSync(join(dir,"apps/web/package.json")))return dir;
    if(dirname(dir)===dir)break;
  }
  throw unavailable();
}
const outputObject=(value:unknown):ObjectValue=>{if(!value||typeof value!=="object"||Array.isArray(value))throw unavailable();return value as ObjectValue;};
function outputString(value:unknown,max=256):string{if(typeof value!=="string"||value.length>max)throw unavailable();return value;}
function outputMatch(value:unknown,pattern:RegExp):string{const text=outputString(value);if(!pattern.test(text))throw unavailable();return text;}
function number(value:unknown,max=500):number{if(typeof value!=="number"||!Number.isSafeInteger(value)||value<0||value>max)throw unavailable();return value;}
function flag(value:unknown):boolean{if(typeof value!=="boolean")throw unavailable();return value;}
function timestamp(value:unknown,nullable=true):string|null {if(value===null&&nullable)return null;const text=outputString(value,40);if(!/^\d{4}-\d{2}-\d{2}T.+(?:Z|[+-]\d{2}:\d{2})$/.test(text)||!Number.isFinite(Date.parse(text)))throw unavailable();return text;}
function choice<T extends string>(value:unknown,allowed:readonly T[]):T{if(typeof value!=="string"||!allowed.includes(value as T))throw unavailable();return value as T;}
function nullableMatch(value:unknown,pattern:RegExp):string|null{return value===null?null:outputMatch(value,pattern);}
function safeReason(value:unknown):string|null{if(value===null)return null;return typeof value==="string"&&(reasonCodes.has(value)||Object.hasOwn(messages,value))?value:"unknown_error";}
function handle(value:unknown):string|null{return nullableMatch(value,/^[a-z0-9._]{1,64}$/);}
function batch(value:unknown):DiscoveryBatch{
  const row=outputObject(value),raw=outputObject(row.counts),counts={} as DiscoveryBatchCounts;
  for(const key of ["total","queued","running","completed","unresolved","blocked","invalid","duplicate","created","existing","identityOnly"] as const)counts[key]=number(raw[key]);
  if(counts.queued+counts.running+counts.completed+counts.unresolved+counts.blocked+counts.invalid+counts.duplicate!==counts.total)throw unavailable();
  const sourceLabel=outputString(row.sourceLabel,120);if(!sourceLabel.trim()||/[\u0000-\u001f\u007f]/.test(sourceLabel))throw unavailable();
  return {id:outputMatch(row.id,BATCH),market:choice(row.market,["it"]),sourceLabel,status:choice(row.status,["queued","running","paused","completed","blocked"]),createdAt:timestamp(row.createdAt,false)!,startedAt:timestamp(row.startedAt),finishedAt:timestamp(row.finishedAt),errorCode:safeReason(row.errorCode),counts,workerOnline:flag(row.workerOnline)};
}
function preview(value:unknown):DiscoveryPreview{
  const row=outputObject(value),rawCounts=outputObject(row.counts);
  if(!Array.isArray(row.items)||row.items.length>500)throw unavailable();
  const items:DiscoveryPreviewItem[]=row.items.map(value=>{const item=outputObject(value);const index=number(item.index),duplicateOf=item.duplicateOf===null?null:number(item.duplicateOf);if(index<1||(duplicateOf!==null&&(duplicateOf<1||duplicateOf>=index)))throw unavailable();return {index,raw:outputString(item.raw,TEXT_BYTES),handle:handle(item.handle),status:choice(item.status,["valid","duplicate","invalid"]),reason:safeReason(item.reason),duplicateOf};});
  const counts={total:number(rawCounts.total),valid:number(rawCounts.valid),duplicate:number(rawCounts.duplicate),invalid:number(rawCounts.invalid)};
  if(counts.total!==items.length||counts.valid+counts.duplicate+counts.invalid!==counts.total||items.some((item,index)=>item.index!==index+1)||["valid","duplicate","invalid"].some(state=>items.filter(item=>item.status===state).length!==counts[state as "valid"|"duplicate"|"invalid"]))throw unavailable();
  for(const item of items){
    if(item.status==="invalid"&&(item.handle!==null||item.duplicateOf!==null))throw unavailable();
    if(item.status!=="invalid"&&item.handle===null)throw unavailable();
    if(item.status==="valid"&&item.duplicateOf!==null)throw unavailable();
    if(item.status==="duplicate"&&(item.duplicateOf===null||items[item.duplicateOf-1]?.status!=="valid"||items[item.duplicateOf-1]?.handle!==item.handle))throw unavailable();
  }
  const canSubmit=flag(row.canSubmit);if(canSubmit!==(counts.valid>0))throw unavailable();
  return {market:choice(row.market,["it"]),sourceLabel:outputString(row.sourceLabel,120),previewHash:outputMatch(row.previewHash,HASH),counts,items,canSubmit};
}
function item(value:unknown):DiscoveryBatchItem{
  const row=outputObject(value),index=number(row.index),duplicateOf=row.duplicateOf===null?null:number(row.duplicateOf);if(index<1||(duplicateOf!==null&&(duplicateOf<1||duplicateOf>=index)))throw unavailable();
  return {id:outputMatch(row.id,ITEM),index,handle:handle(row.handle),status:choice(row.status,["queued","running","completed","unresolved","blocked","invalid","duplicate"]),reason:safeReason(row.reason),duplicateOf,creatorId:nullableMatch(row.creatorId,CREATOR),oecId:nullableMatch(row.oecId,/^[0-9]{1,64}$/),startedAt:timestamp(row.startedAt),finishedAt:timestamp(row.finishedAt),requestCount:row.requestCount===null?null:number(row.requestCount,1_000_000),outcome:row.outcome===null?null:choice(row.outcome,["created","existing","identity_only"] as const)};
}
export function decodeDiscoveryOutput(command:CreatorDiscoveryCommand,output:string):CreatorDiscoveryResponse{
  let value:unknown;try{value=JSON.parse(output);}catch{throw unavailable();}
  const row=outputObject(value);
  if("error" in row){const failure=outputObject(row.error),code=typeof failure.code==="string"?failure.code:"",definition=messages[code];throw definition?new CreatorDiscoveryError(code,...definition):unavailable();}
  if(command==="preview")return preview(row);
  if(command==="submit"||command==="control")return batch(row);
  if(command==="list"){
    if(!Array.isArray(row.batches)||row.batches.length>20)throw unavailable();
    return {batches:row.batches.map(batch),workerOnline:flag(row.workerOnline)} satisfies DiscoveryList;
  }
  if(command==="detail"){
    if(!Array.isArray(row.items)||row.items.length>500)throw unavailable();
    const result={batch:batch(row.batch),items:row.items.map(item)} satisfies DiscoveryDetail;
    if(result.items.length!==result.batch.counts.total||new Set(result.items.map(item=>item.id)).size!==result.items.length||result.items.some((item,index)=>item.index!==index+1))throw unavailable();
    return result;
  }
  throw unavailable();
}
export function callDiscoveryCommand(command:CreatorDiscoveryCommand,input:ObjectValue,options:{run?:typeof execFile;root?:string}={}):Promise<CreatorDiscoveryResponse>{
  const call=normalizedCall(command,input),root=options.root??rootPath(),run=options.run??execFile;
  return new Promise((resolveResult,reject)=>{
    const child=run(join(root,"../01-BDSystem-V2/.venv/bin/python"),[join(root,"scripts/creator-discovery.py"),call.command],
      {cwd:root,timeout:10000,maxBuffer:1024*1024,shell:false,env:{...process.env,PYTHONDONTWRITEBYTECODE:"1"}},(error,stdout)=>{
        try{
          const result=decodeDiscoveryOutput(call.command,String(stdout));
          if(error)throw unavailable();
          resolveResult(result);
        }catch(failure){reject(failure instanceof CreatorDiscoveryError?failure:unavailable());}
      });
    child.stdin?.on("error",()=>{});child.stdin?.end(JSON.stringify(call.input));
  });
}
type Invoke=(command:CreatorDiscoveryCommand,input:ObjectValue)=>Promise<CreatorDiscoveryResponse>;
const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"};
function failure(error:unknown){
  if(error instanceof InputError||error instanceof CreatorDiscoveryError)return Response.json({error:{code:error.code,message:error.message}},{status:error.status,headers});
  const safe=unavailable();return Response.json({error:{code:safe.code,message:safe.message}},{status:safe.status,headers});
}
async function readBody(request:Request):Promise<unknown>{
  const declared=request.headers.get("content-length");if(declared&&Number(declared)>BODY_BYTES)throw new CreatorDiscoveryError("payload_too_large",413,messages.payload_too_large[1]);
  const reader=request.body?.getReader();if(!reader)throw new InputError("请求内容为空。");
  const chunks:Uint8Array[]=[];let size=0;
  try{for(;;){const {done,value}=await reader.read();if(done)break;size+=value.byteLength;if(size>BODY_BYTES){await reader.cancel();throw new CreatorDiscoveryError("payload_too_large",413,messages.payload_too_large[1]);}chunks.push(value);}}finally{reader.releaseLock();}
  const bytes=new Uint8Array(size);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.byteLength;}
  try{return JSON.parse(new TextDecoder("utf-8",{fatal:true}).decode(bytes));}catch{throw new InputError("请求不是有效 JSON。");}
}
export function createDiscoveryHandlers(invoke:Invoke=callDiscoveryCommand){
  const reject=()=>Response.json({error:{code:"local_origin_required",message:"名单接口只接受本机工作台请求。"}},{status:403,headers});
  return {
    async GET(request:Request){
      if(!isLocalRequest(request,false))return reject();
      try{const {command,input}=parseDiscoveryQuery(request.url);return Response.json(await invoke(command,input),{headers});}catch(error){return failure(error);}
    },
    async POST(request:Request){
      if(!isLocalRequest(request,true))return reject();
      if(request.headers.get("content-type")?.split(";")[0].trim()!=="application/json")return Response.json({error:{code:"json_required",message:"请使用 JSON 请求。"}},{status:415,headers});
      try{if(new URL(request.url).search)throw new InputError("请将操作参数放在请求正文中。");const {command,input}=parseDiscoveryPost(await readBody(request));return Response.json(await invoke(command,input),{headers});}catch(error){return failure(error);}
    },
  };
}
