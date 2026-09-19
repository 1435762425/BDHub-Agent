import {readCampaignPanel} from "../../../server/campaign/bridge.ts";
import {isLocalRequest} from "../../../server/runtime/validation.ts";
export const runtime="nodejs";
export const dynamic="force-dynamic";

const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"};

export async function GET(request:Request){
 if(!isLocalRequest(request,false))return Response.json({error:'local_origin_required'},{status:403,headers});
 try{return Response.json(await readCampaignPanel("campaign"),{headers});}
 catch{return Response.json({error:'campaign_panel_unavailable'},{status:503,headers});}
}
