import {readSendBatch,saveSendConfig,validateSendRequest} from "../../../server/send/bridge.ts";
import {isLocalRequest} from "../../../server/runtime/validation.ts";
export const runtime="nodejs";
export const dynamic="force-dynamic";

const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"};

export async function GET(request:Request){
 if(!isLocalRequest(request,false))return Response.json({error:'local_origin_required'},{status:403,headers});
 try{return Response.json(await readSendBatch(),{headers});}
 catch{return Response.json({error:'send_batch_unavailable'},{status:503,headers});}
}

/** 只保存设置。开始发送是另一个动作（要落批次授权），不在这条路由上。 */
export async function POST(request:Request){
 if(!isLocalRequest(request,true))return Response.json({error:'local_origin_required'},{status:403,headers});
 let body:unknown;
 try{body=await request.json();}
 catch{return Response.json({error:'invalid_send_request'},{status:400,headers});}
 let call;
 try{call=validateSendRequest(body);}
 catch{return Response.json({error:'invalid_send_request'},{status:400,headers});}
 try{return Response.json(await saveSendConfig(call.config),{headers});}
 catch{return Response.json({error:'send_batch_unavailable'},{status:503,headers});}
}
