import {readLeadPool} from "../../../server/lead-pool/bridge.ts";
import {isLocalRequest} from "../../../server/runtime/validation.ts";
import {enabledMarket} from "../../../server/markets/registry.ts";
export const runtime="nodejs";
export const dynamic="force-dynamic";
const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"};

export async function GET(request:Request){
 if(!isLocalRequest(request,false))return Response.json({error:'local_origin_required'},{status:403,headers});
 const url=new URL(request.url),market=url.searchParams.get('market')??'it';
 if([...url.searchParams.keys()].some(key=>key!=='market')||url.searchParams.getAll('market').length>1||!enabledMarket(market))return Response.json({error:'invalid_query'},{status:400,headers});
 try{return Response.json(await readLeadPool(market),{headers});}
 catch{return Response.json({error:'lead_pool_unavailable'},{status:503,headers});}
}
