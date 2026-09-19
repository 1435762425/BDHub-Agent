import {readLeadPool} from "../../../server/lead-pool/bridge.ts";
import {isLocalRequest} from "../../../server/runtime/validation.ts";
export const runtime="nodejs";
export const dynamic="force-dynamic";
const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"};

export async function GET(request:Request){
 if(!isLocalRequest(request,false))return Response.json({error:'local_origin_required'},{status:403,headers});
 try{return Response.json(await readLeadPool(),{headers});}
 catch{return Response.json({error:'lead_pool_unavailable'},{status:503,headers});}
}
