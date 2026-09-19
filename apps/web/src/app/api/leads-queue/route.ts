import {readLeadsQueue,saveLeadsQueue,startLeadsRun,validateLeadsQueueRequest} from "../../../server/leads-queue/bridge.ts";
import type {LeadsQueueConfig} from "../../../server/leads-queue/bridge.ts";
import {isLocalRequest} from "../../../server/runtime/validation.ts";
export const runtime="nodejs";
export const dynamic="force-dynamic";

const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"};

export async function GET(request:Request){
 if(!isLocalRequest(request,false))return Response.json({error:'local_origin_required'},{status:403,headers});
 try{return Response.json(await readLeadsQueue(),{headers});}
 catch{return Response.json({error:'leads_queue_unavailable'},{status:503,headers});}
}

export async function POST(request:Request){
 if(!isLocalRequest(request,true))return Response.json({error:'local_origin_required'},{status:403,headers});
 let body:unknown;
 try{body=await request.json();}
 catch{return Response.json({error:'invalid_leads_queue_request'},{status:400,headers});}
 let call:{action:"save";config:LeadsQueueConfig}|{action:"run"};
 try{call=validateLeadsQueueRequest(body);}
 catch{return Response.json({error:'invalid_leads_queue_request'},{status:400,headers});}
 try{
  // One batch at a time: an already-running batch keeps its own record and is not restarted.
  if(call.action==="run"){
   const current=await readLeadsQueue();
   if(current.run?.running)return Response.json({error:'leads_run_already_running'},{status:409,headers});
   startLeadsRun();
   return Response.json(await readLeadsQueue(),{headers});
  }
  return Response.json(await saveLeadsQueue(call.config),{headers});
 }
 catch{return Response.json({error:'leads_queue_unavailable'},{status:503,headers});}
}
