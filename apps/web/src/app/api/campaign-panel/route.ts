import {readCampaignPanel} from "../../../server/campaign/bridge.ts";
import {isLocalRequest} from "../../../server/runtime/validation.ts";
import {operationalMarket} from "../../../server/markets/registry.ts";
export const runtime="nodejs";
export const dynamic="force-dynamic";

const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"};

export async function GET(request:Request){
 if(!isLocalRequest(request,false))return Response.json({error:'local_origin_required'},{status:403,headers});
 const url=new URL(request.url),market=url.searchParams.get('market');if([...url.searchParams.keys()].some(key=>key!=='market')||url.searchParams.getAll('market').length!==1||!market||!operationalMarket(market))return Response.json({error:'invalid_query'},{status:400,headers});
 try{return Response.json(await readCampaignPanel(market,"campaign"),{headers});}
 catch{return Response.json({error:'campaign_panel_unavailable'},{status:503,headers});}
}
