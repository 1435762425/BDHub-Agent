import {freezeSendBatch,readSendBatch,reconcileSendBatch,saveSendConfig,startSendBatch,stopSendBatch,validateSendRequest} from "../../../server/send/bridge.ts";
import {isLocalRequest} from "../../../server/runtime/validation.ts";
export const runtime="nodejs";
export const dynamic="force-dynamic";

const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"};

export async function GET(request:Request){
 if(!isLocalRequest(request,false))return Response.json({error:'local_origin_required'},{status:403,headers});
 try{return Response.json(await readSendBatch(),{headers});}
 catch{return Response.json({error:'send_batch_unavailable'},{status:503,headers});}
}

const conflicts=new Set(['preview_conflict','freeze_request_conflict','active_batch_exists','revision_conflict']);

/** 保存设置、冻结预览、明确启动或停止；没有任何动作会从 GET 或 save 隐式启动。 */
export async function POST(request:Request){
 if(!isLocalRequest(request,true))return Response.json({error:'local_origin_required'},{status:403,headers});
 let body:unknown;
 try{body=await request.json();}
 catch{return Response.json({error:'invalid_send_request'},{status:400,headers});}
 let call;
 try{call=validateSendRequest(body);}
 catch{return Response.json({error:'invalid_send_request'},{status:400,headers});}
 try{
  const result=call.action==='save'?await saveSendConfig(call.config):
   call.action==='freeze'?await freezeSendBatch(call.requestId,call.expectedPreviewHash):
   call.action==='start'?await startSendBatch(call.batchId,call.expectedRevision):
   call.action==='stop'?await stopSendBatch(call.batchId,call.expectedRevision):
   await reconcileSendBatch(call.batchId,call.deliveryId,call.expectedRevision);
  return Response.json(result,{headers});
 }
 catch(error){
  const code=error instanceof Error?error.message:'send_batch_unavailable';
  if(conflicts.has(code))return Response.json({error:code},{status:409,headers});
  if(['batch_empty','full_preparation_required','batch_not_startable','batch_not_stoppable','start_confirmation_required',
      'batch_missing','frozen_batch_incomplete','plan_paused','batch_not_reconcilable','unknown_delivery_missing',
      'reconciliation_confirmation_required'].includes(code))
   return Response.json({error:code},{status:422,headers});
  return Response.json({error:'send_batch_unavailable'},{status:503,headers});
 }
}
