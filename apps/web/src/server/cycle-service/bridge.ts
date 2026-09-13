import {execFile} from "node:child_process";
import {join} from "node:path";
import {projectRoot} from "../creator-identities/refresh.ts";
import {isLocalRequest} from "../runtime/validation.ts";
export type ServiceCase={handle?:string|null;id:string;creator_id:string;assessment_revision:number;control_revision:number;reason:string;overdue:boolean;ack_state:string;messages:Array<{messageId:string;text:string|null;format:string}>};
export type ServiceStatus={replyCounts?:Record<string,number>;historicalAiEvaluations?:number;available:boolean;cases:ServiceCase[];incomingContents?:number;assessments?:number;caseCount?:number;pending?:Record<string,number>;automaticReplies:boolean};
type RequestData={action:"status"}|{action:"resolve";caseId:string;expectedRevision:number;expectedControlRevision:number;note:string};
export function invokeService(input:RequestData):Promise<unknown>{const root=projectRoot();return new Promise((resolve,reject)=>{const child=execFile(join(root,"../01-BDSystem-V2/.venv/bin/python"),[join(root,"scripts/cycle-service.py")],{cwd:root,timeout:10000,maxBuffer:2*1024*1024,env:{...process.env,PYTHONDONTWRITEBYTECODE:"1"}},(error,stdout)=>{try{const value=JSON.parse(stdout);if(error||value.error)throw new Error("service_conflict");resolve(value);}catch{reject(new Error("service_unavailable"));}});child.stdin?.end(JSON.stringify(input));});}
const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"};
export function createServiceHandlers(invoke=invokeService){return {
 GET:async(req:Request)=>{if(!isLocalRequest(req,false))return Response.json({error:"local_origin_required"},{status:403,headers});if(new URL(req.url).search)return Response.json({error:"invalid_query"},{status:400,headers});try{return Response.json(await invoke({action:"status"}),{headers});}catch{return Response.json({error:"service_unavailable"},{status:503,headers});}},
 POST:async(req:Request)=>{if(!isLocalRequest(req,true))return Response.json({error:"local_origin_required"},{status:403,headers});if(!req.headers.get("content-type")?.startsWith("application/json"))return Response.json({error:"json_required"},{status:415,headers});try{
 const reader=req.body?.getReader();if(!reader)throw Error();let raw="",size=0;const decoder=new TextDecoder("utf-8",{fatal:true});try{for(;;){const chunk=await reader.read();if(chunk.done)break;size+=chunk.value.byteLength;if(size>20000){await reader.cancel();throw Error();}raw+=decoder.decode(chunk.value,{stream:true});}raw+=decoder.decode();}finally{reader.releaseLock();}
 const v=JSON.parse(raw);if(!v||Object.keys(v).sort().join(",")!=="action,caseId,expectedControlRevision,expectedRevision,note"||v.action!=="resolve"||typeof v.caseId!=="string"||!/^case-[a-f0-9]{24}$/.test(v.caseId)||!Number.isSafeInteger(v.expectedRevision)||v.expectedRevision<1||!Number.isSafeInteger(v.expectedControlRevision)||v.expectedControlRevision<1||typeof v.note!=="string"||!v.note.trim()||v.note.length>4000)return Response.json({error:"invalid_input"},{status:400,headers});
 try{return Response.json(await invoke(v),{headers});}catch{return Response.json({error:"case_changed_review_latest"},{status:409,headers});}
 }catch{return Response.json({error:"invalid_input"},{status:400,headers});}}
};}
