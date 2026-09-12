import {execFile} from "node:child_process";
import {existsSync} from "node:fs";
import {dirname,join,resolve} from "node:path";
import {InputError,isLocalRequest} from "../runtime/validation.ts";
import type {DraftAttempt,DraftContent,DraftFact,DraftProduct,DraftContextRequest,DraftCost,DraftDetail,DraftList,DraftRequest,DraftReview,DraftServiceStatus,DraftStyle,DraftSummary,DraftUsage} from "../../features/outreach-drafts/contracts.ts";

type RecordValue=Record<string,unknown>;
export type DraftCommand="status"|"list"|"detail"|"lookup_request"|"enqueue"|"creator_history";
type PrivateDetail=Omit<DraftDetail,"freshness">&{contextRequest:DraftContextRequest;contextFingerprint:string};
type CommandResult=DraftServiceStatus|DraftList|PrivateDetail|DraftSummary|null;
type BuildResult={context:unknown;fingerprint:string;contextRequest:DraftContextRequest};
type Builder=(input:DraftContextRequest)=>BuildResult|Promise<BuildResult>;
type Invoke=(command:DraftCommand,input:RecordValue)=>Promise<CommandResult>;
const CREATOR=/^creator_[a-f0-9]{32}$/;
const PACKET=/^(?:review|second)-packet-[0-9a-f-]{36}$/,DRAFT=/^outreach_draft_[0-9a-f]{32}$/,REQUEST=/^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$/,FINGERPRINT=/^[0-9a-f]{64}$/;
const messages:Record<string,[number,string]>={
  second_uses_templates:[409,"二发已改用固定模板，不再调用模型生成话术。"],
  packet_identity_mismatch:[409,"资料包与当前达人身份不一致，请重新核对关联。"],
  provider_deadline_unavailable:[503,"模型请求时限尚未就绪。"],provider_not_configured:[409,"模型服务尚未配置完成。"],provider_config_invalid:[503,"模型服务配置尚未就绪。"],provider_config_unavailable:[503,"暂时无法读取模型服务配置。"],provider_input_invalid:[400,"模型输入格式不完整。"],provider_input_too_large:[422,"模型输入超过本次处理范围。"],provider_limit_invalid:[503,"模型输出限制尚未正确配置。"],provider_clock_invalid:[503,"暂时无法确认模型调用的计费时段。"],provider_runtime_unavailable:[503,"模型运行服务暂不可用。"],provider_http_error:[503,"模型服务暂未返回正常结果。"],provider_redirect_rejected:[503,"模型服务响应地址不符合当前配置。"],provider_timeout:[503,"模型调用超时，原任务结果需核对。"],provider_network_error:[503,"模型连接中断，原任务结果需核对。"],provider_response_invalid:[503,"模型服务响应不完整。"],provider_response_too_large:[503,"模型服务响应超出本次处理范围。"],provider_model_mismatch:[503,"返回的模型与当前配置不一致。"],provider_usage_invalid:[503,"模型未返回完整用量，费用尚待核对。"],provider_output_invalid:[503,"模型正文未通过格式检查。"],provider_output_truncated:[503,"模型正文未完整返回。"],
  draft_validation_failed:[422,"草稿正文未通过检查。"],review_validation_failed:[422,"草稿检查结果不完整。"],usage_unavailable:[503,"调用用量尚未确认，不能按零费用处理。"],reservation_exceeded:[409,"调用费用需进一步核对，原预留保持。"],stage_result_unknown:[503,"模型调用结果尚未确认，请读取原任务记录。"],worker_interrupted:[503,"草稿任务中断，原记录保持。"],stale_lease:[409,"此任务已由另一轮处理接续，请重新读取状态。"],internal_error:[503,"草稿服务暂不可用，原请求编号已保留。"],
  model_disabled:[403,"草稿生成等待模型配置与使用范围确认。"],policy_invalid:[503,"模型使用配置尚未就绪。"],policy_changed:[409,"模型使用范围已变化，请更新状态后再操作。"],budget_exhausted:[429,"本次模型试用额度已用完。"],trial_limit_reached:[429,"本次可生成的草稿数量已用完。"],request_conflict:[409,"原请求编号对应另一份输入，请恢复原操作。"],context_stale:[409,"资料已更新，请重新分析并整理新资料包。"],context_unavailable:[503,"暂时无法核对草稿依据，原请求已保留。"],context_invalid:[409,"当前资料尚不满足草稿生成条件。"],skill_mismatch:[409,"草稿策略已变化，请重新整理当前资料。"],provider_unavailable:[503,"模型服务暂不可用。"],input_too_large:[422,"草稿必要资料超过本次处理范围。"],
  invalid_input:[400,"草稿请求格式不正确。"],invalid_request:[400,"草稿请求格式不正确。"],idempotency_conflict:[409,"原请求编号对应另一份输入，请恢复原操作。"],
  policy_disabled:[409,"草稿生成等待模型配置与使用范围确认。"],authorization_required:[409,"草稿生成等待模型配置与使用范围确认。"],provider_not_ready:[409,"模型服务尚未配置完成。"],
  budget_exceeded:[409,"本次模型试用额度已用完。"],draft_limit_exceeded:[409,"本次可生成的草稿数量已用完。"],packet_missing:[404,"没有找到这份关系资料包。"],packet_not_found:[404,"没有找到这份关系资料包。"],
  draft_not_found:[404,"没有找到这份草稿。"],job_not_found:[404,"没有找到这份草稿。"],stale_run:[409,"资料已更新，请重新分析并整理新资料包。"],stale_context:[409,"资料已更新，请重新分析并整理新资料包。"],
  relationship_suppressed:[409,"此关系当前不支持继续生成合作邀约。"],unsupported_market:[409,"本次草稿生成仅支持意大利。"],unsupported_dataset:[409,"请从意大利画像分析的资料包生成草稿。"],
  policy_unavailable:[503,"草稿策略尚未就绪。"],draft_facts_missing:[422,"当前资料包缺少足以组织个性化邀约的匹配依据。"],product_name_unverified:[422,"商品尚无已核对的意大利语简名。"],draft_context_too_large:[422,"必要事实超过单条草稿范围，请缩小商品或表达要求。"],
  context_too_large:[422,"本次必要资料超过草稿上下文范围，请缩小商品范围。"],unsafe_context:[409,"此资料包尚不满足草稿生成条件。"],identity_unverified:[409,"此资料包的稳定身份尚未核实。"],
};
const safeErrors=new Set([...Object.keys(messages),"provider_error","provider_timeout","invalid_model_output","review_failed","result_unknown","worker_interrupted","context_changed","model_unavailable","request_timeout","draft_validation_failed","review_validation_failed","usage_unavailable","reservation_exceeded","stage_result_unknown","stale_lease","internal_error","unknown_error", "provider_not_configured","provider_config_invalid","provider_config_unavailable","provider_input_invalid","provider_input_too_large","provider_limit_invalid","provider_clock_invalid","provider_runtime_unavailable","provider_http_error","provider_redirect_rejected","provider_network_error","provider_response_invalid","provider_response_too_large","provider_model_mismatch","provider_usage_invalid","provider_output_invalid","provider_output_truncated"]);
export class OutreachDraftError extends Error {readonly code:string;readonly status:number;constructor(code:string,status:number,message:string){super(message);this.code=code;this.status=status;}}
function unavailable(){return new OutreachDraftError("draft_service_unavailable",503,"草稿服务暂不可用，请保留原请求编号后重试。");}
function object(value:unknown):RecordValue {if(!value||typeof value!=="object"||Array.isArray(value))throw new InputError("请求必须为对象。");return value as RecordValue;}
function keys(row:RecordValue,allowed:string[]){if(Object.keys(row).some(key=>!allowed.includes(key)))throw new InputError("请求包含不支持的字段。");}
function match(value:unknown,pattern:RegExp,label:string):string {if(typeof value!=="string"||!pattern.test(value))throw new InputError(`${label}格式不正确。`);return value;}
export function parseDraftRequest(value:unknown):DraftRequest {
  const row=object(value);keys(row,["packetId","style","instructions","requestId"]);
  if(!["friendly","direct","detailed"].includes(String(row.style))||typeof row.instructions!=="string"||row.instructions.length>500||/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(row.instructions))throw new InputError("请选择可用风格，补充要求限 500 字。");
  return {packetId:match(row.packetId,PACKET,"资料包编号"),style:row.style as DraftStyle,instructions:row.instructions.trim(),requestId:match(row.requestId,REQUEST,"请求编号")};
}
export function parseDraftQuery(url:string):{command:"status"|"list"|"detail"|"creator_history";input:RecordValue}{
  if(url.length>2048)throw new InputError("查询过长。");const q=new URL(url).searchParams,view=q.get("view")||"status",allowed=view==="status"?["view"]:view==="list"?["view","packetId"]:view==="detail"?["view","draftId"]:view==="creator"?["view","creatorId"]:null;
  if(!allowed)throw new InputError("不支持此草稿查询。");for(const key of q.keys())if(!allowed.includes(key)||q.getAll(key).length!==1)throw new InputError("查询包含不支持或重复的字段。");
  return view==="creator"?{command:"creator_history",input:{creatorId:match(q.get("creatorId"),CREATOR,"达人身份编号")}}:view==="status"?{command:"status",input:{}}:view==="list"?{command:"list",input:{packetId:match(q.get("packetId"),PACKET,"资料包编号")}}:{command:"detail",input:{draftId:match(q.get("draftId"),DRAFT,"草稿编号")}};
}
function outObject(value:unknown):RecordValue{if(!value||typeof value!=="object"||Array.isArray(value))throw unavailable();return value as RecordValue;}
function text(value:unknown,max=512):string {if(typeof value!=="string"||value.length>max)throw unavailable();return value;}
function outMatch(value:unknown,pattern:RegExp){const result=text(value);if(!pattern.test(result))throw unavailable();return result;}
function choice<T extends string>(value:unknown,allowed:readonly T[]):T{if(typeof value!=="string"||!allowed.includes(value as T))throw unavailable();return value as T;}
function flag(value:unknown):boolean{if(typeof value!=="boolean")throw unavailable();return value;}
function integer(value:unknown):number{if(typeof value!=="number"||!Number.isSafeInteger(value)||value<0)throw unavailable();return value;}
function nullableInteger(value:unknown){return value===null?null:integer(value);}
function decimal(value:unknown):string{return outMatch(value,/^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$/);}
function nullableDecimal(value:unknown){return value===null?null:decimal(value);}
function time(value:unknown):string{const result=text(value,50);if(!/^\d{4}-\d{2}-\d{2}T.+(?:Z|[+-]\d{2}:\d{2})$/.test(result)||!Number.isFinite(Date.parse(result)))throw unavailable();return result;}
function nullableTime(value:unknown){return value===null?null:time(value);}
function texts(value:unknown,max=30,maxLength=2000):string[]{if(!Array.isArray(value)||value.length>max)throw unavailable();return value.map(item=>text(item,maxLength));}
function blocked(row:RecordValue){if(row.executionBlocked!==true)throw unavailable();return true as const;}
function summary(value:unknown):DraftSummary {
  const row=outObject(value);return {id:outMatch(row.id,DRAFT),packetId:outMatch(row.packetId,PACKET),status:choice(row.status,["queued","running","drafted","needs_review","stale","failed","result_unknown"]),style:choice(row.style,["friendly","direct","detailed"]),createdAt:time(row.createdAt),startedAt:nullableTime(row.startedAt),finishedAt:nullableTime(row.finishedAt),errorCode:row.errorCode===null?null:typeof row.errorCode==="string"&&safeErrors.has(row.errorCode)?row.errorCode:"unknown_error",stage:row.stage===null?null:choice(row.stage,["draft","review"] as const),reservedCostCny:decimal(row.reservedCostCny),knownCostCny:nullableDecimal(row.knownCostCny),executionBlocked:blocked(row)};
}
function usage(value:unknown):DraftUsage {const row=outObject(value);return {promptTokens:nullableInteger(row.promptTokens),cacheHitTokens:nullableInteger(row.cacheHitTokens),cacheMissTokens:nullableInteger(row.cacheMissTokens),completionTokens:nullableInteger(row.completionTokens),reasoningTokens:nullableInteger(row.reasoningTokens),totalTokens:nullableInteger(row.totalTokens)};}
function cost(value:unknown):DraftCost{const row=outObject(value);return {estimatedCny:nullableDecimal(row.estimatedCny),upperBoundCny:nullableDecimal(row.upperBoundCny),complete:flag(row.complete)};}
function content(value:unknown):DraftContent|null {if(value===null)return null;const row=outObject(value);return {textIt:text(row.textIt,12000),translationZh:text(row.translationZh,12000),selectedProductIds:texts(row.selectedProductIds,5,160),evidenceRefs:texts(row.evidenceRefs,40,500),rationaleZh:text(row.rationaleZh,6000)};}
function review(value:unknown):DraftReview|null{if(value===null)return null;const row=outObject(value);return {verdict:choice(row.verdict,["pass","needs_review"]),issues:texts(row.issues),unsupportedClaims:texts(row.unsupportedClaims),italianValid:flag(row.italianValid),translationFaithful:flag(row.translationFaithful)};}
function contextRequest(value:unknown):DraftContextRequest {const row=outObject(value),parsed=parseDraftRequest({...row,requestId:"read-context"});return {packetId:parsed.packetId,style:parsed.style,instructions:parsed.instructions};}
export function decodeDraftOutput(command:DraftCommand,output:string):CommandResult {
  let value:unknown;try{value=JSON.parse(output);}catch{throw unavailable();}
  if(command==="lookup_request"&&value===null)return null;
  const row=outObject(value);if("error" in row){const error=outObject(row.error),code=typeof error.code==="string"?error.code:"",definition=messages[code];throw definition?new OutreachDraftError(code,...definition):unavailable();}
  if(command==="enqueue"||command==="lookup_request")return summary(row);
  if(command==="list"||command==="creator_history"){if(!Array.isArray(row.drafts)||row.drafts.length>20)throw unavailable();return {drafts:row.drafts.map(summary),workerOnline:flag(row.workerOnline)};}
  if(command==="status"){
    const provider=outObject(row.provider),policy=outObject(row.policy),budget=outObject(row.budget);return {provider:{ready:flag(provider.ready),model:choice(provider.model,["deepseek-flash"])},policy:{enabled:flag(policy.enabled),id:policy.id===null?null:text(policy.id,160),version:integer(policy.version),model:choice(policy.model,["deepseek-flash"]),maxDrafts:integer(policy.maxDrafts),maxCostCny:decimal(policy.maxCostCny)},budget:{reservedCny:decimal(budget.reservedCny),knownCostCny:decimal(budget.knownCostCny),availableCny:decimal(budget.availableCny),usedDrafts:integer(budget.usedDrafts)},workerOnline:flag(row.workerOnline)};
  }
  if(!Array.isArray(row.attempts)||row.attempts.length>2)throw unavailable();
  const attempts:DraftAttempt[]=row.attempts.map(item=>{const one=outObject(item);return {stage:choice(one.stage,["draft","review"]),status:choice(one.status,["inflight","completed","failed","result_unknown"]),usage:usage(one.usage),cost:cost(one.cost)};});
  if(!Array.isArray(row.facts)||row.facts.length>12||!Array.isArray(row.products)||row.products.length>3)throw unavailable();
  const facts:DraftFact[]=row.facts.map(item=>{const fact=outObject(item);return {id:outMatch(fact.id,/^(?:recipient|p[1-3]-(?:name|fit))$/),kind:choice(fact.kind,["recipient_handle","product_name","category_alignment"]),value:Array.isArray(fact.value)?texts(fact.value,20,300):text(fact.value,500)};});
  const products:DraftProduct[]=row.products.map(item=>{const product=outObject(item);return {id:outMatch(product.id,/^p[1-3]$/),nameIt:text(product.nameIt,500)};});
  return {facts,products,draft:summary(row.draft),content:content(row.content),review:review(row.review),attempts,cost:cost(row.cost),executionBlocked:blocked(row),contextRequest:contextRequest(row.contextRequest),contextFingerprint:outMatch(row.contextFingerprint,FINGERPRINT)};
}
function rootPath(start=process.cwd()):string{for(let dir=resolve(start);;dir=dirname(dir)){if(existsSync(join(dir,"scripts/lib/creator_identity.py"))&&existsSync(join(dir,"apps/web/package.json")))return dir;if(dirname(dir)===dir)break;}throw unavailable();}
export function callDraftCommand(command:DraftCommand,input:RecordValue,options:{root?:string;run?:typeof execFile}={}):Promise<CommandResult>{
  if(!["status","list","detail","lookup_request","enqueue","creator_history"].includes(command))throw new InputError("不支持此草稿操作。");
  if(command==="creator_history"){keys(object(input),["creatorId"]);match(input.creatorId,CREATOR,"达人身份编号");}
  const root=options.root??rootPath(),run=options.run??execFile;
  return new Promise((resolveResult,reject)=>{const child=run(join(root,"../01-BDSystem-V2/.venv/bin/python"),[join(root,"scripts/outreach-drafts.py"),command],{cwd:root,timeout:15000,maxBuffer:1024*1024,shell:false,env:{...process.env,PYTHONDONTWRITEBYTECODE:"1"}},(error,stdout)=>{try{const result=decodeDraftOutput(command,String(stdout));if(error)throw unavailable();resolveResult(result);}catch(failure){reject(failure instanceof OutreachDraftError?failure:unavailable());}});child.stdin?.on("error",()=>{});child.stdin?.end(JSON.stringify(input));});
}
const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"};
function failure(error:unknown){const code=error&&typeof error==="object"&&"code" in error?String(error.code):"";if(error instanceof InputError)return Response.json({error:{code:error.code,message:error.message}},{status:error.status,headers});if(error instanceof OutreachDraftError)return Response.json({error:{code:error.code,message:error.message}},{status:error.status,headers});const safe=messages[code];return safe?Response.json({error:{code,message:safe[1]}},{status:safe[0],headers}):Response.json({error:{code:"draft_service_unavailable",message:unavailable().message}},{status:503,headers});}
async function readBody(request:Request){const reader=request.body?.getReader();if(!reader)throw new InputError("请求内容为空。");const chunks:Uint8Array[]=[];let size=0;try{for(;;){const{done,value}=await reader.read();if(done)break;size+=value.byteLength;if(size>8192){await reader.cancel();throw new InputError("请求内容过长。");}chunks.push(value);}}finally{reader.releaseLock();}const bytes=new Uint8Array(size);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.byteLength;}try{return JSON.parse(new TextDecoder("utf-8",{fatal:true}).decode(bytes));}catch{throw new InputError("请求不是有效 JSON。");}}
const build:Builder=async input=>{const{compileOutreachContext}=await import("./compile.ts");return compileOutreachContext(input);};
export function createDraftHandlers(options:{invoke?:Invoke;build?:Builder}={}){
  const invoke=options.invoke??callDraftCommand,compile=options.build??build,reject=()=>Response.json({error:{code:"local_origin_required",message:"草稿接口只接受本机工作台请求。"}},{status:403,headers});
  return {
    async GET(request:Request){if(!isLocalRequest(request,false))return reject();try{const{command,input}=parseDraftQuery(request.url),value=await invoke(command,input);if(command!=="detail")return Response.json(value,{headers});const detail=value as PrivateDetail;if(!detail?.draft||detail.draft.id!==input.draftId)throw unavailable();let freshness:DraftDetail["freshness"]="unknown";try{const current=await compile(detail.contextRequest);freshness=current.fingerprint===detail.contextFingerprint?"current":"stale";}catch(error){const code=error&&typeof error==="object"&&"code" in error?String(error.code):"";if(["stale_run","stale_context","packet_missing","packet_not_found","relationship_suppressed","identity_unverified","packet_identity_mismatch","unsafe_context","second_uses_templates"].includes(code))freshness="stale";}const{contextRequest:_,contextFingerprint:__,...safe}=detail;void _;void __;return Response.json({...safe,freshness},{headers});}catch(error){return failure(error);}},
    async POST(request:Request){if(!isLocalRequest(request,true))return reject();if(request.headers.get("content-type")?.split(";")[0].trim()!=="application/json")return Response.json({error:{code:"json_required",message:"请使用 JSON 请求。"}},{status:415,headers});try{if(new URL(request.url).search)throw new InputError("请将操作参数放在请求正文中。");const parsed=parseDraftRequest(await readBody(request)),existing=await invoke("lookup_request",parsed as unknown as RecordValue);if(existing!==null){const recovered=existing as DraftSummary;if(recovered.packetId!==parsed.packetId||recovered.style!==parsed.style)throw unavailable();return Response.json(recovered,{headers});}if(parsed.packetId.startsWith("second-packet-"))throw new OutreachDraftError("second_uses_templates",409,messages.second_uses_templates[1]);const status=await invoke("status",{}) as DraftServiceStatus;if(!status.policy.enabled)throw new OutreachDraftError("policy_disabled",409,messages.policy_disabled[1]);if(!status.provider.ready)throw new OutreachDraftError("provider_not_ready",409,messages.provider_not_ready[1]);const compiled=await compile({packetId:parsed.packetId,style:parsed.style,instructions:parsed.instructions});return Response.json(await invoke("enqueue",{requestId:parsed.requestId,...compiled}),{headers});}catch(error){return failure(error);}},
  };
}
