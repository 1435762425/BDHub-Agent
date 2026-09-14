import {execFile,spawn} from "node:child_process";
import {join} from "node:path";
import {projectRoot} from "../creator-identities/refresh.ts";
import {isLocalRequest} from "../runtime/validation.ts";
export class TaskInputError extends Error {}
export function taskCommand(body:unknown):Record<string,unknown>{
 if(!body||typeof body!=="object"||Array.isArray(body))throw new TaskInputError("invalid_request");
 const b=body as Record<string,unknown>;
 const shapes:Record<string,string[]>={list:["action"],preview:["action","spec"],confirm:["action","token","requestKey","authorizationScope"],detail:["action","id"],pause:["action","id","revision"],resume:["action","id","revision"],priority:["action","id","revision","priority"]};
 if(b.action==="confirm"&&b.authorizationScope!=="full_preparation_no_messages")throw new TaskInputError("task_confirmation_scope_required");
 const keys=typeof b.action==="string"?shapes[b.action]:undefined;
 if(!keys||Object.keys(b).length!==keys.length||Object.keys(b).some(k=>!keys.includes(k)))throw new TaskInputError("invalid_request");
 for(const k of ["id","token","requestKey"])if(k in b&&(typeof b[k]!=="string"||!/^[A-Za-z0-9_.:-]{1,120}$/.test(b[k] as string)))throw new TaskInputError("invalid_request");
 if("revision" in b&&(!Number.isSafeInteger(b.revision)||Number(b.revision)<1))throw new TaskInputError("invalid_request");
 if("priority" in b&&(!Number.isSafeInteger(b.priority)||Math.abs(Number(b.priority))>100))throw new TaskInputError("invalid_request");
 return b;
}
export function callTasks(body:Record<string,unknown>):Promise<Record<string,unknown>>{
 const root=projectRoot();return new Promise((resolve,reject)=>{
  const child=execFile(join(root,"../01-BDSystem-V2/.venv/bin/python"),[join(root,"scripts/batch-task-control.py")],{cwd:root,timeout:20000,maxBuffer:2*1024*1024,env:{...process.env,PYTHONDONTWRITEBYTECODE:"1"}},(error,stdout)=>{
   try{const data=JSON.parse(stdout);if(error||data.error)throw error&&"code" in error&&error.code===2?new TaskInputError(data.error):new Error("task_store_unavailable");resolve(data);}catch(e){reject(e);}
  });child.stdin?.end(JSON.stringify(body));
 });
}
export function ensurePreparationWorker(){
 const root=projectRoot();const worker=spawn(join(root,"../01-BDSystem-V2/.venv/bin/python"),[join(root,"scripts/batch-preparation-worker.py")],{cwd:root,detached:true,stdio:"ignore",env:{...process.env,PYTHONDONTWRITEBYTECODE:"1"}});
 worker.on("error",()=>{});worker.unref(); // singleton flock in worker; status exposes failure
}
export function createTaskHandlers(call=callTasks,wake=ensurePreparationWorker){
 const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"};
 async function response(body:Record<string,unknown>){
  try{const result=await call(body);if(["confirm","resume"].includes(String(body.action)))wake();return Response.json(result,{headers});}
  catch(e){return Response.json({error:e instanceof TaskInputError?e.message:"task_store_unavailable"},{status:e instanceof TaskInputError?400:503,headers});}
 }
 return {
  GET:async(request:Request)=>{if(!isLocalRequest(request,false))return Response.json({error:"local_origin_required"},{status:403,headers});return response({action:"list"});},
  POST:async(request:Request)=>{
   if(!isLocalRequest(request,true))return Response.json({error:"local_origin_required"},{status:403,headers});
   try{const raw=await request.text();if(raw.length>65536)throw new TaskInputError("invalid_request");const body=taskCommand(JSON.parse(raw));return response(body);}
   catch{return Response.json({error:"invalid_request"},{status:400,headers});}
  }
 };
}
