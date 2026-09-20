import {execFile} from "node:child_process";
import {join} from "node:path";
import {projectRoot} from "../creator-identities/refresh.ts";
import {isLocalRequest} from "../runtime/validation.ts";

export type SendTemplate={id:string;name:string;description:string;bodyIt:string;revision:number;builtIn:boolean;state:"active"|"archived";parameters:string[]};
export type ManualTemplate={id:string;name:string;category:string;body:string;revision:number;state:"active"|"archived"};
export type AgentSetting={enabled:boolean;timezone:"Asia/Shanghai";replyStart:string;replyEnd:string;sendStart:string;sendEnd:string;bufferMinutes:number;
 actions:{no_reply:boolean;sample_self_service:boolean;collaboration_ack:boolean;link_usage:boolean;human:false};revision:number;updatedAt:number};
export type AgentTemplate={id:string;action:string;language:string;text:string};
export type TemplateLibraryState={sendTemplates:SendTemplate[];manualTemplates:ManualTemplate[];agentTemplates:AgentTemplate[];agentSetting:AgentSetting;platformWrites:0;realSends:0};

const SEND_PARAMETERS=["creator_handle","product_name","creator_commission"];
const AGENT_ACTIONS=["no_reply","sample_self_service","collaboration_ack","link_usage","human"];
const safeErrors=new Set(["template_body_invalid","template_parameters_invalid","template_name_invalid","template_request_invalid","template_request_conflict","template_revision_conflict","template_missing","template_in_use","reply_setting_invalid","reply_setting_conflict","reply_schedule_invalid","reply_schedule_overlap"]);
const time=(value:unknown)=>{if(typeof value!=="string"||!/^([01]\d|2[0-3]):[0-5]\d$|^24:00$/.test(value))throw Error("invalid_template_library");return value;};
const text=(value:unknown,max:number)=>{if(typeof value!=="string"||!value.trim()||value.length>max)throw Error("invalid_template_library");return value;};
const integer=(value:unknown,max=100000)=>{if(typeof value!=="number"||!Number.isSafeInteger(value)||value<0||value>max)throw Error("invalid_template_library");return value;};
const exact=(value:Record<string,unknown>,keys:string[],code="invalid_template_request")=>{if(Object.keys(value).sort().join(",")!==[...keys].sort().join(","))throw Error(code);};
const minutes=(value:string)=>value==="24:00"?1440:Number(value.slice(0,2))*60+Number(value.slice(3));

function actions(raw:unknown,code:"invalid_template_library"|"invalid_template_request"){
 if(!raw||typeof raw!=="object"||Array.isArray(raw))throw Error(code);
 const value=raw as Record<string,unknown>;exact(value,AGENT_ACTIONS,code);
 if(value.human!==false||AGENT_ACTIONS.slice(0,4).some(key=>typeof value[key]!=="boolean"))throw Error(code);
 return {no_reply:value.no_reply as boolean,sample_self_service:value.sample_self_service as boolean,collaboration_ack:value.collaboration_ack as boolean,link_usage:value.link_usage as boolean,human:false as const};
}

function agentSettingInput(raw:unknown){
 if(!raw||typeof raw!=="object"||Array.isArray(raw))throw Error("invalid_template_request");
 const value=raw as Record<string,unknown>;exact(value,["enabled","timezone","replyStart","replyEnd","sendStart","sendEnd","bufferMinutes","actions"]);
 if(typeof value.enabled!=="boolean"||value.timezone!=="Asia/Shanghai")throw Error("invalid_template_request");
 let replyStart:string,replyEnd:string,sendStart:string,sendEnd:string,bufferMinutes:number;
 try{replyStart=time(value.replyStart);replyEnd=time(value.replyEnd);sendStart=time(value.sendStart);sendEnd=time(value.sendEnd);bufferMinutes=integer(value.bufferMinutes,180);}catch{throw Error("invalid_template_request");}
 if(replyStart==="24:00"||sendStart==="24:00"||!(minutes(replyStart)<minutes(replyEnd)&&minutes(replyEnd)<=minutes(sendStart)&&minutes(sendStart)<minutes(sendEnd))||minutes(sendStart)-minutes(replyEnd)<bufferMinutes)throw Error("invalid_template_request");
 return {enabled:value.enabled,timezone:"Asia/Shanghai" as const,replyStart,replyEnd,sendStart,sendEnd,bufferMinutes,actions:actions(value.actions,"invalid_template_request")};
}

export function validateTemplateLibrary(raw:unknown):TemplateLibraryState{
 if(!raw||typeof raw!=="object"||Array.isArray(raw))throw Error("invalid_template_library");const value=raw as Record<string,unknown>;
 if(value.platformWrites!==0||value.realSends!==0||!Array.isArray(value.sendTemplates)||!Array.isArray(value.manualTemplates)||!Array.isArray(value.agentTemplates)||value.sendTemplates.length>200||value.manualTemplates.length>500||value.agentTemplates.length>20)throw Error("invalid_template_library");
 const seen=new Set<string>();
 const sendTemplates=value.sendTemplates.map(rawTemplate=>{if(!rawTemplate||typeof rawTemplate!=="object"||Array.isArray(rawTemplate))throw Error("invalid_template_library");const row=rawTemplate as Record<string,unknown>;const id=text(row.id,40);if(seen.has(id)||typeof row.builtIn!=="boolean"||(row.state!=="active"&&row.state!=="archived")||!Array.isArray(row.parameters))throw Error("invalid_template_library");seen.add(id);const parameters=row.parameters.map(item=>text(item,40));if(parameters.length!==SEND_PARAMETERS.length||parameters.some((item,index)=>item!==SEND_PARAMETERS[index]))throw Error("invalid_template_library");return {id,name:text(row.name,60),description:text(row.description,160),bodyIt:text(row.bodyIt,600),revision:integer(row.revision),builtIn:row.builtIn,state:row.state,parameters} as SendTemplate;});
 const manualTemplates=value.manualTemplates.map(rawTemplate=>{if(!rawTemplate||typeof rawTemplate!=="object"||Array.isArray(rawTemplate))throw Error("invalid_template_library");const row=rawTemplate as Record<string,unknown>;if(row.state!=="active"&&row.state!=="archived")throw Error("invalid_template_library");return {id:text(row.id,40),name:text(row.name,60),category:text(row.category,60),body:text(row.body,2000),revision:integer(row.revision),state:row.state} as ManualTemplate;});
 if(!value.agentSetting||typeof value.agentSetting!=="object"||Array.isArray(value.agentSetting))throw Error("invalid_template_library");const setting=value.agentSetting as Record<string,unknown>;
 if(typeof setting.enabled!=="boolean"||setting.timezone!=="Asia/Shanghai")throw Error("invalid_template_library");
 const replyStart=time(setting.replyStart),replyEnd=time(setting.replyEnd),sendStart=time(setting.sendStart),sendEnd=time(setting.sendEnd),bufferMinutes=integer(setting.bufferMinutes,180);
 if(replyStart==="24:00"||sendStart==="24:00"||!(minutes(replyStart)<minutes(replyEnd)&&minutes(replyEnd)<=minutes(sendStart)&&minutes(sendStart)<minutes(sendEnd))||minutes(sendStart)-minutes(replyEnd)<bufferMinutes)throw Error("invalid_template_library");
 const agentTemplates=value.agentTemplates.map(rawTemplate=>{if(!rawTemplate||typeof rawTemplate!=="object"||Array.isArray(rawTemplate))throw Error("invalid_template_library");const row=rawTemplate as Record<string,unknown>;const action=text(row.action,40);if(!AGENT_ACTIONS.slice(1,4).includes(action))throw Error("invalid_template_library");return {id:text(row.id,80),action,language:text(row.language,10),text:text(row.text,4000)};});
 return {sendTemplates,manualTemplates,agentTemplates,agentSetting:{enabled:setting.enabled,timezone:"Asia/Shanghai",replyStart,replyEnd,sendStart,sendEnd,bufferMinutes,actions:actions(setting.actions,"invalid_template_library"),revision:integer(setting.revision),updatedAt:typeof setting.updatedAt==="number"&&Number.isFinite(setting.updatedAt)&&setting.updatedAt>=0?setting.updatedAt:(()=>{throw Error("invalid_template_library");})()},platformWrites:0,realSends:0};
}

export function invokeTemplateLibrary(input:Record<string,unknown>):Promise<TemplateLibraryState>{
 const root=projectRoot();
 return new Promise((resolve,reject)=>{const child=execFile(join(root,".venv/bin/python"),[join(root,"scripts/template-library.py")],{cwd:root,timeout:60000,maxBuffer:2*1024*1024,env:{...process.env,PYTHONDONTWRITEBYTECODE:"1"}},(error,stdout)=>{try{const value=JSON.parse(stdout) as Record<string,unknown>;if(typeof value?.error==="string"){reject(Error(safeErrors.has(value.error)?value.error:"template_library_unavailable"));return;}if(error)throw error;resolve(validateTemplateLibrary(value));}catch{reject(Error("template_library_unavailable"));}});child.stdin?.end(JSON.stringify(input));});
}

export function syncAgentReplyWorker(enabled:boolean):Promise<void>{
 const root=projectRoot();
 return new Promise((resolve,reject)=>{execFile(join(root,".venv/bin/python"),[join(root,"scripts/job-run.py"),"status","--name","agentReply"],{cwd:root,timeout:30000,env:{...process.env,PYTHONDONTWRITEBYTECODE:"1"}},(error,stdout)=>{try{if(error)throw error;const state=JSON.parse(stdout)?.agentReply?.run;const running=state?.running===true;if(enabled===running){resolve();return;}const action=enabled?"start":"stop";execFile(join(root,".venv/bin/python"),[join(root,"scripts/job-run.py"),action,"--name","agentReply"],{cwd:root,timeout:30000,env:{...process.env,PYTHONDONTWRITEBYTECODE:"1"}},second=>second?reject(Error("agent_worker_unavailable")):resolve());}catch{reject(Error("agent_worker_unavailable"));}});});
}

export function validateTemplateRequest(raw:unknown):Record<string,unknown>{
 if(!raw||typeof raw!=="object"||Array.isArray(raw))throw Error("invalid_template_request");const value=raw as Record<string,unknown>,action=value.action;
 if(action==="create_send"){exact(value,["action","requestId","name","bodyIt"]);return {action,requestId:text(value.requestId,120),name:text(value.name,60),bodyIt:text(value.bodyIt,600)};}
 if(action==="update_send"){exact(value,["action","templateId","expectedRevision","name","bodyIt"]);return {action,templateId:text(value.templateId,40),expectedRevision:integer(value.expectedRevision),name:text(value.name,60),bodyIt:text(value.bodyIt,600)};}
 if(action==="archive_send"){exact(value,["action","templateId","expectedRevision"]);return {action,templateId:text(value.templateId,40),expectedRevision:integer(value.expectedRevision)};}
 if(action==="upsert_manual"){exact(value,["action","requestId","templateId","expectedRevision","name","category","body"]);return {action,requestId:text(value.requestId,120),templateId:value.templateId==null?null:text(value.templateId,40),expectedRevision:value.expectedRevision==null?null:integer(value.expectedRevision),name:text(value.name,60),category:text(value.category,60),body:text(value.body,2000)};}
 if(action==="save_agent"){exact(value,["action","expectedRevision","setting"]);return {action,expectedRevision:integer(value.expectedRevision),setting:agentSettingInput(value.setting)};}
 throw Error("invalid_template_request");
}

async function jsonBody(request:Request,maxBytes=20_000){
 if(request.headers.get("content-type")?.split(";")[0].trim()!=="application/json")throw Error("json_required");
 const reader=request.body?.getReader();if(!reader)throw Error("invalid_template_request");const decoder=new TextDecoder("utf-8",{fatal:true});let raw="",size=0;
 try{for(;;){const part=await reader.read();if(part.done)break;size+=part.value.byteLength;if(size>maxBytes){await reader.cancel();throw Error("invalid_template_request");}raw+=decoder.decode(part.value,{stream:true});}raw+=decoder.decode();}finally{reader.releaseLock();}
 return JSON.parse(raw);
}

const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"};
export function createTemplateLibraryHandlers(invoke=invokeTemplateLibrary,syncWorker=syncAgentReplyWorker){return {
 GET:async(request:Request)=>{if(!isLocalRequest(request,false))return Response.json({error:"local_origin_required"},{status:403,headers});if(new URL(request.url).search)return Response.json({error:"invalid_query"},{status:400,headers});try{return Response.json(await invoke({action:"status"}),{headers});}catch{return Response.json({error:"template_library_unavailable"},{status:503,headers});}},
 POST:async(request:Request)=>{
  if(!isLocalRequest(request,true))return Response.json({error:"local_origin_required"},{status:403,headers});
  let body:Record<string,unknown>;
  try{body=validateTemplateRequest(await jsonBody(request));}catch(error){const status=error instanceof Error&&error.message==="json_required"?415:400;return Response.json({error:status===415?"json_required":"invalid_template_request"},{status,headers});}
  try{const result=await invoke(body);if(body.action==="save_agent")await syncWorker(result.agentSetting.enabled);return Response.json(result,{headers});}
  catch(error){const code=error instanceof Error?error.message:"";if(["template_request_conflict","template_revision_conflict","template_in_use","reply_setting_conflict"].includes(code))return Response.json({error:code},{status:409,headers});if(["template_body_invalid","template_parameters_invalid","template_name_invalid","template_request_invalid","template_missing","reply_setting_invalid","reply_schedule_invalid","reply_schedule_overlap"].includes(code))return Response.json({error:code},{status:422,headers});return Response.json({error:"template_library_unavailable"},{status:503,headers});}
 }
};}
