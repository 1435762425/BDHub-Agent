import {parseInboxQuery,readInbox,readInboxDay,saveInboxConfig,startInboxRun,stopInboxRun,validateInboxRequest} from "../../../server/inbox/bridge.ts";
import type {InboxConfig} from "../../../server/inbox/bridge.ts";
import {isLocalRequest} from "../../../server/runtime/validation.ts";
export const runtime="nodejs";
export const dynamic="force-dynamic";

const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"};

export async function GET(request:Request){
 if(!isLocalRequest(request,false))return Response.json({error:'local_origin_required'},{status:403,headers});
 let query;try{query=parseInboxQuery(request.url);}
 catch{return Response.json({error:'invalid_inbox_query'},{status:400,headers});}
 try{return Response.json(query.view==="status"?await readInbox():await readInboxDay(query.date,query.offset,query.limit),{headers});}
 catch{return Response.json({error:'inbox_unavailable'},{status:503,headers});}
}

export async function POST(request:Request){
 if(!isLocalRequest(request,true))return Response.json({error:'local_origin_required'},{status:403,headers});
 let body:unknown;
 try{body=await request.json();}
 catch{return Response.json({error:'invalid_inbox_request'},{status:400,headers});}
 let call:{action:"save";config:InboxConfig}|{action:"start";config:InboxConfig}|{action:"stop"};
 try{call=validateInboxRequest(body);}
 catch{return Response.json({error:'invalid_inbox_request'},{status:400,headers});}
 try{
  if(call.action==="save")return Response.json(await saveInboxConfig(call.config),{headers});
  if(call.action==="stop")return Response.json(await stopInboxRun(),{headers});
  // 只跑一个监控：已经在跑的那个有自己的记录，不会被重启。
  const current=await readInbox();
  if(current.run?.running)return Response.json({error:'inbox_run_already_running'},{status:409,headers});
  return Response.json(await startInboxRun(call.config),{headers});
 }
 catch(error){
  const code=error instanceof Error?error.message:"";
  // 已经在跑、或停一个没在跑的：是操作者的时机，不是服务坏了。
  if(code==="job_already_running"||code==="job_not_running")return Response.json({error:code},{status:409,headers});
  return Response.json({error:'inbox_unavailable'},{status:503,headers});
 }
}
