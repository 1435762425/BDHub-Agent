import {readIdentityQueue,saveIdentityConfig,startIdentityRun,stopIdentityRun,validateIdentityQueueRequest} from "../../../server/identity-queue/bridge.ts";
import type {IdentityConfig} from "../../../server/identity-queue/bridge.ts";
import {isLocalRequest} from "../../../server/runtime/validation.ts";
export const runtime="nodejs";
export const dynamic="force-dynamic";

const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"};

export async function GET(request:Request){
 if(!isLocalRequest(request,false))return Response.json({error:'local_origin_required'},{status:403,headers});
 try{return Response.json(await readIdentityQueue(),{headers});}
 catch{return Response.json({error:'identity_queue_unavailable'},{status:503,headers});}
}

export async function POST(request:Request){
 if(!isLocalRequest(request,true))return Response.json({error:'local_origin_required'},{status:403,headers});
 let body:unknown;
 try{body=await request.json();}
 catch{return Response.json({error:'invalid_identity_queue_request'},{status:400,headers});}
 let call:{action:"save";config:IdentityConfig}|{action:"start";config:IdentityConfig}|{action:"stop"};
 try{call=validateIdentityQueueRequest(body);}
 catch{return Response.json({error:'invalid_identity_queue_request'},{status:400,headers});}
 try{
  if(call.action==="save")return Response.json(await saveIdentityConfig(call.config),{headers});
  if(call.action==="stop")return Response.json(await stopIdentityRun(),{headers});
  // One backfill at a time: a running batch keeps its own record and is never restarted.
  const current=await readIdentityQueue();
  if(current.run?.running)return Response.json({error:'identity_run_already_running'},{status:409,headers});
  return Response.json(await startIdentityRun(call.config),{headers});
 }
 catch(error){
  const code=error instanceof Error?error.message:"";
  if(code==="job_already_running")return Response.json({error:code},{status:409,headers});
  // Asking to stop something that is not running is the operator's own timing, not a broken service.
  if(code==="job_not_running")return Response.json({error:code},{status:409,headers});
  return Response.json({error:'identity_queue_unavailable'},{status:503,headers});
 }
}
