import {readScreen,previewScreen,saveScreen,runScreenNow,validateScreenRequest} from "../../../server/catalog-screen/bridge.ts";
import type {CatalogScreenConfig} from "../../../server/catalog-screen/bridge.ts";
import {isLocalRequest} from "../../../server/runtime/validation.ts";
export const runtime="nodejs";
export const dynamic="force-dynamic";

const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"};

export async function GET(request:Request){
 if(!isLocalRequest(request,false))return Response.json({error:'local_origin_required'},{status:403,headers});
 try{return Response.json(await readScreen(),{headers});}
 catch{return Response.json({error:'catalog_screen_unavailable'},{status:503,headers});}
}

export async function POST(request:Request){
 if(!isLocalRequest(request,true))return Response.json({error:'local_origin_required'},{status:403,headers});
 let body:unknown;
 try{body=await request.json();}
 catch{return Response.json({error:'invalid_catalog_screen_request'},{status:400,headers});}
 let call:{action:"preview"|"save"|"run";config?:CatalogScreenConfig};
 try{call=validateScreenRequest(body);}
 catch{return Response.json({error:'invalid_catalog_screen_request'},{status:400,headers});}
 try{
  const result=call.action==="save"?await saveScreen(call.config as CatalogScreenConfig):call.action==="run"?await runScreenNow():await previewScreen(call.config as CatalogScreenConfig);
  if(call.action==="save"&&result.saved===false)return Response.json(result,{status:422,headers});
  return Response.json(result,{headers});
 }
 catch{return Response.json({error:'catalog_screen_unavailable'},{status:503,headers});}
}
