import {execFile} from "node:child_process";
import {join} from "node:path";
import {projectRoot} from "../creator-identities/refresh.ts";
import {InputError,isLocalRequest} from "../runtime/validation.ts";
const messages:Record<string,[number,string]>={promotion_reason_missing:[422,"这些商品尚缺已核验的本次推广理由，请先补齐商品素材。"],source_conflict:[409,"本批来源已更新，请重新读取后准备。"],invalid_request:[400,"二发请求格式不正确。"],source_unavailable:[503,"本机二发来源暂不可用，请核对导入资料。"],source_invalid:[422,"二发来源校验未通过。"],opportunity_not_found:[404,"这条二发机会不在当前来源中。"],identity_unverified:[409,"请先核验当前收件人的稳定身份。"],revision_conflict:[409,"资料已更新，请重新读取。"],stale_context:[409,"草稿资料已更新，请整理新资料包。"],relationship_suppressed:[409,"这条二发机会已暂停。"],request_conflict:[409,"此请求编号已用于其他操作。"],packet_missing:[404,"找不到这份二发资料包。"]};
export class SecondOutreachError extends Error {readonly code:string;readonly status:number;constructor(code:string,status:number,message:string){super(message);this.code=code;this.status=status;}}
function bad():never{throw new InputError("二发请求格式不正确。");}
function object(v:unknown):Record<string,unknown>{if(!v||typeof v!=="object"||Array.isArray(v))return bad();return v as Record<string,unknown>;}
function exact(v:Record<string,unknown>,keys:string[]){if(Object.keys(v).some(k=>!keys.includes(k))||keys.some(k=>!(k in v)))bad();}
function token(v:unknown):string{if(typeof v!=="string"||!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$/.test(v))return bad();return v;}
export function parseSecondOutreachCommand(value:unknown){
  const body=object(value);exact(body,["requestId","command"]);token(body.requestId);const command=object(body.command);
  if(command.type==="refresh")exact(command,["type"]);
  else if(command.type==="render_batch"){
    exact(command,["type","opportunityIds","templateId","expectedSourceFingerprint"]);token(command.templateId);
    if(!Array.isArray(command.opportunityIds)||!command.opportunityIds.length||command.opportunityIds.length>100)bad();command.opportunityIds.forEach(token);
    if(typeof command.expectedSourceFingerprint!=="string"||!/^[a-f0-9]{64}$/.test(command.expectedSourceFingerprint))bad();
  }
  else if(command.type==="prepare"||command.type==="control"||command.type==="render_template"){
    exact(command,command.type==="prepare"?["type","opportunityId","expectedRevision"]:command.type==="render_template"?["type","opportunityId","expectedRevision","templateId"]:["type","opportunityId","expectedRevision","control"]);if(command.type==="render_template")token(command.templateId);
    token(command.opportunityId);if(!Number.isSafeInteger(command.expectedRevision)||Number(command.expectedRevision)<1)bad();
    if(command.type==="control"&&!["active","paused"].includes(String(command.control)))bad();
  }else bad();return body;
}
export function parseSecondOutreachQuery(url:string){
  const q=new URL(url).searchParams,view=q.get("view")||"status";
  const fields=view==="status"||view==="templates"?["view"]:view==="opportunity"?["view","id"]:view==="list"?["view","offset","limit","q","filter"]:null;
  if(!fields)return bad();for(const k of q.keys())if(!fields.includes(k)||q.getAll(k).length!==1)bad();
  if(view==="status"||view==="templates")return {command:view,input:{}};
  if(view==="opportunity")return {command:"get",input:{opportunityId:token(q.get("id"))}};
  const number=(key:string,defaultValue:number,max:number)=>{const raw=q.get(key);if(raw!==null&&!/^\d+$/.test(raw))return bad();const n=raw===null?defaultValue:Number(raw);if(!Number.isSafeInteger(n)||n<0||n>max)return bad();return n;};
  const offset=number("offset",0,100000),limit=number("limit",20,100),search=q.get("q")||"",filter=q.get("filter")||"all";
  if(!limit||search.length>100||!["all","identified","unresolved"].includes(filter))bad();
  return {command:"list",input:{offset,limit,q:search,filter}};
}
export function callSecondOutreach(command:string,input:unknown):Promise<unknown>{
  if(!["status","list","get","command","context","templates"].includes(command))bad();const root=projectRoot();
  return new Promise((resolve,reject)=>{
    const child=execFile(join(root,"../01-BDSystem-V2/.venv/bin/python"),[join(root,"scripts/second-outreach.py"),command],{cwd:root,timeout:15000,maxBuffer:2*1024*1024,shell:false,env:{...process.env,PYTHONDONTWRITEBYTECODE:"1"}},(error,stdout)=>{
      try{const body=JSON.parse(stdout);if(body?.error){const code=String(body.error.code),safe=messages[code]||[503,"二发服务暂不可用。"] as [number,string];throw new SecondOutreachError(messages[code]?code:"second_unavailable",safe[0],safe[1]);}if(error||!body||typeof body!=="object")throw new Error();resolve(body);}
      catch(failure){reject(failure instanceof SecondOutreachError?failure:new SecondOutreachError("second_unavailable",503,"二发服务暂不可用，请保留原请求编号。"));}
    });child.stdin?.on("error",()=>{});child.stdin?.end(JSON.stringify(input));
  });
}
const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"};
export function createSecondOutreachHandlers(invoke=callSecondOutreach){
  const error=(e:unknown)=>e instanceof InputError||e instanceof SecondOutreachError?Response.json({error:{code:e.code,message:e.message}},{status:e.status,headers}):Response.json({error:{code:"second_unavailable",message:"二发服务暂不可用。"}},{status:503,headers});
  const reject=()=>Response.json({error:{code:"local_origin_required",message:"仅支持本机工作台请求。"}},{status:403,headers});
  return {GET:async(request:Request)=>{if(!isLocalRequest(request,false))return reject();try{const parsed=parseSecondOutreachQuery(request.url);return Response.json(await invoke(parsed.command,parsed.input),{headers});}catch(e){return error(e);}},
    POST:async(request:Request)=>{if(!isLocalRequest(request,true))return reject();try{
      if(!request.headers.get("content-type")?.startsWith("application/json"))throw new SecondOutreachError("unsupported_media_type",415,"请使用JSON请求。");
      const reader=request.body?.getReader();if(!reader)bad();let input="",size=0;const decoder=new TextDecoder("utf-8",{fatal:true});
      try{for(;;){const part=await reader.read();if(part.done)break;size+=part.value.byteLength;if(size>8192){await reader.cancel();bad();}input+=decoder.decode(part.value,{stream:true});}input+=decoder.decode();}finally{reader.releaseLock();}
      let value;try{value=JSON.parse(input);}catch{bad();}return Response.json(await invoke("command",parseSecondOutreachCommand(value)),{headers});
    }catch(e){return error(e);}}};
}
