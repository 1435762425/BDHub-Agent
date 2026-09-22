import {readNaming,previewNaming,saveNaming,validateNamingRequest} from "../../../server/link-naming/bridge.ts";
import {isLocalRequest} from "../../../server/runtime/validation.ts";
import {enabledMarket} from "../../../server/markets/registry.ts";
export const runtime="nodejs";
export const dynamic="force-dynamic";

const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"};

export async function GET(request:Request){
 if(!isLocalRequest(request,false))return Response.json({error:'local_origin_required'},{status:403,headers});
 const url=new URL(request.url),raw=url.searchParams.get('market'),market=raw??'it';
 if([...url.searchParams.keys()].some(key=>key!=='market')||url.searchParams.getAll('market').length>1||!enabledMarket(market))return Response.json({error:'invalid_query'},{status:400,headers});
 try{return Response.json(await readNaming(market),{headers});}
 catch{return Response.json({error:'link_naming_unavailable'},{status:503,headers});}
}

export async function POST(request:Request){
 if(!isLocalRequest(request,true))return Response.json({error:'local_origin_required'},{status:403,headers});
 let body:unknown;
 try{body=await request.json();}
 catch{return Response.json({error:'invalid_link_naming_request'},{status:400,headers});}
 let action:"preview"|"save",market:string,config:unknown;
 try{({action,market,config}=validateNamingRequest(body));}
 catch{return Response.json({error:'invalid_link_naming_request'},{status:400,headers});}
 try{
  const result=action==="save"?await saveNaming(config,market):await previewNaming(config,market);
  if(action==="save"&&result.saved===false)return Response.json(result,{status:422,headers});
  return Response.json(result,{headers});
 }
 catch{return Response.json({error:'link_naming_unavailable'},{status:503,headers});}
}
