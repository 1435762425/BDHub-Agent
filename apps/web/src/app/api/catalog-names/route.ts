import {readCatalogNames,readNamesProgress,startCatalogNames} from "../../../server/catalog-names/bridge.ts";
import {isLocalRequest} from "../../../server/runtime/validation.ts";
export const runtime="nodejs";
export const dynamic="force-dynamic";
const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"};

export async function GET(request:Request){
 if(!isLocalRequest(request,false))return Response.json({error:'local_origin_required'},{status:403,headers});
 try{return Response.json(await readNamesProgress(),{headers});}
 catch{return Response.json({error:'catalog_names_unavailable'},{status:503,headers});}
}

export async function POST(request:Request){
 if(!isLocalRequest(request,true))return Response.json({error:'local_origin_required'},{status:403,headers});
 let body:unknown;
 try{body=await request.json();}
 catch{return Response.json({error:'invalid_catalog_names_request'},{status:400,headers});}
 const v=body as Record<string,unknown>|null;
 const all=v?.all===true;
 if(!v||v.action!=="prepare"||(v.all!==undefined&&typeof v.all!=="boolean"))
  return Response.json({error:'invalid_catalog_names_request'},{status:400,headers});
 if(!all&&(typeof v.limit!=="number"||!Number.isSafeInteger(v.limit)||v.limit<1||v.limit>5000))
  return Response.json({error:'invalid_catalog_names_request'},{status:400,headers});
 try{
  const current=await readNamesProgress();
  if(current.run?.running)return Response.json({error:'catalog_names_already_running'},{status:409,headers});
  startCatalogNames(typeof v.limit==="number"?v.limit:1,all);
  return Response.json(await readNamesProgress(),{headers});
 }
 catch{return Response.json({error:'catalog_names_unavailable'},{status:503,headers});}
}
