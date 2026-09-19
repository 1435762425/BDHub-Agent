import {applyCampaignJoin,joinAllCampaigns,previewCampaignJoin,readCampaignJoin,recheckCampaignJoin,validateCampaignJoinRequest} from "../../../server/campaign/bridge.ts";
import {isLocalRequest} from "../../../server/runtime/validation.ts";
export const runtime="nodejs";
export const dynamic="force-dynamic";

const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"};

export async function GET(request:Request){
 if(!isLocalRequest(request,false))return Response.json({error:'local_origin_required'},{status:403,headers});
 try{return Response.json(await readCampaignJoin(),{headers});}
 catch{return Response.json({error:'campaign_join_unavailable'},{status:503,headers});}
}

export async function POST(request:Request){
 if(!isLocalRequest(request,true))return Response.json({error:'local_origin_required'},{status:403,headers});
 let body:unknown;
 try{body=await request.json();}
 catch{return Response.json({error:'invalid_campaign_join_request'},{status:400,headers});}
 let call:ReturnType<typeof validateCampaignJoinRequest>;
 try{call=validateCampaignJoinRequest(body);}
 catch(error){
  const code=error instanceof Error?error.message:"";
  return Response.json({error:code},{status:code==='campaign_join_confirmation_required'?400:400,headers});
 }
 try{
  if(call.action==="preview")return Response.json(await previewCampaignJoin(),{headers});
  if(call.action==="verify")return Response.json(await recheckCampaignJoin(),{headers});
  // 一键加入：目标集合由服务端重新预览得出，请求里不接受活动列表。
  if(call.action==="joinAll")return Response.json(await joinAllCampaigns(call.email),{headers});
  return Response.json(await applyCampaignJoin(call.campaignIds,call.email),{headers});
 }
 catch(error){
  const code=error instanceof Error?error.message:"";
  // 结果未知是操作者必须看到的正常结局，不是服务坏了。
  if(code==='campaign_write_requires_verification'||code==='campaign_join_not_in_preview'||
     code.startsWith('campaign_'))return Response.json({error:code},{status:409,headers});
  return Response.json({error:'campaign_join_unavailable'},{status:503,headers});
 }
}
