import {readScreen,previewScreen,saveScreen,runScreenNow,validateScreenRequest} from "../../../server/catalog-screen/bridge.ts";
import type {CatalogScreenConfig} from "../../../server/catalog-screen/bridge.ts";
import {isLocalRequest} from "../../../server/runtime/validation.ts";
export const runtime="nodejs";
export const dynamic="force-dynamic";

const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"};

export async function GET(request:Request){
 if(!isLocalRequest(request,false))return Response.json({error:'local_origin_required'},{status:403,headers});
 const url=new URL(request.url);if([...url.searchParams.keys()].some(key=>key!=='market')||url.searchParams.getAll('market').length!==1||url.searchParams.get('market')!=='it')return Response.json({error:'invalid_query'},{status:400,headers});
 try{return Response.json({market:'it',...await readScreen()},{headers});}
 catch{return Response.json({error:'catalog_screen_unavailable'},{status:503,headers});}
}

export async function POST(request:Request){
 if(!isLocalRequest(request,true))return Response.json({error:'local_origin_required'},{status:403,headers});
 const url=new URL(request.url);if([...url.searchParams.keys()].some(key=>key!=='market')||url.searchParams.getAll('market').length!==1||url.searchParams.get('market')!=='it')return Response.json({error:'invalid_query'},{status:400,headers});
 let body:unknown;
 try{body=await request.json();}
 catch{return Response.json({error:'invalid_catalog_screen_request'},{status:400,headers});}
 if(!body||typeof body!=='object'||Array.isArray(body)||(body as Record<string,unknown>).market!=='it')return Response.json({error:'market_mismatch'},{status:409,headers});
 const {market:_market,...input}=body as Record<string,unknown>;
 let call:{action:"preview"|"save"|"run";config?:CatalogScreenConfig};
 try{call=validateScreenRequest(input);}
 catch{return Response.json({error:'invalid_catalog_screen_request'},{status:400,headers});}
 try{
  const result=call.action==="save"?await saveScreen(call.config as CatalogScreenConfig):call.action==="run"?await runScreenNow():await previewScreen(call.config as CatalogScreenConfig);
  if(call.action==="save"&&result.saved===false)return Response.json(result,{status:422,headers});
  return Response.json({market:'it',...result},{headers});
 }
 catch{return Response.json({error:'catalog_screen_unavailable'},{status:503,headers});}
}
