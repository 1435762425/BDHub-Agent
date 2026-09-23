import {readCampaignLinks} from "../../../server/campaign/bridge.ts";
import {isLocalRequest} from "../../../server/runtime/validation.ts";
import {operationalMarket} from "../../../server/markets/registry.ts";
export const runtime="nodejs";
export const dynamic="force-dynamic";

const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"};

/** 只读：非全托的链接准备覆盖情况。启动准备走 /api/catalog-jobs（作业名 linksCampaign）。 */
export async function GET(request:Request){
 if(!isLocalRequest(request,false))return Response.json({error:'local_origin_required'},{status:403,headers});
 const url=new URL(request.url),market=url.searchParams.get('market');if([...url.searchParams.keys()].some(key=>key!=='market')||url.searchParams.getAll('market').length!==1||!market||!operationalMarket(market))return Response.json({error:'invalid_query'},{status:400,headers});
 try{return Response.json(await readCampaignLinks(market),{headers});}
 catch{return Response.json({error:'campaign_links_unavailable'},{status:503,headers});}
}
