import {createGlobalGet,createGlobalPost,readLinkStatus,validateLinkStatus} from "../../../server/global-source/bridge.ts";
import {isLocalRequest} from "../../../server/runtime/validation.ts";
export const runtime="nodejs";
export const dynamic="force-dynamic";
const globalGet=createGlobalGet();

export async function GET(request:Request){
 const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"};
 const url=new URL(request.url);
 if(url.searchParams.get("links")==="1"){
  if(!isLocalRequest(request,false))return Response.json({error:'local_origin_required'},{status:403,headers});
  if([...url.searchParams.keys()].some(k=>k!=="links"))return Response.json({error:'invalid_query'},{status:400,headers});
  try{return Response.json(validateLinkStatus(await readLinkStatus()),{headers});}
  catch{return Response.json({error:'catalog_link_status_unavailable'},{status:503,headers});}
 }
 return globalGet(request);
}

export const POST=createGlobalPost();
