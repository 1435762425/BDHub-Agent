import {readIdentity,saveIdentity,probeIdentity,startIdentityLogin,validateIdentityRequest} from "../../../server/kalodata-identity/bridge.ts";
import type {KalodataIdentityConfig} from "../../../server/kalodata-identity/bridge.ts";
import {isLocalRequest} from "../../../server/runtime/validation.ts";
import {enabledMarket} from "../../../server/markets/registry.ts";
export const runtime="nodejs";
export const dynamic="force-dynamic";

const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"};

export async function GET(request:Request){
 if(!isLocalRequest(request,false))return Response.json({error:'local_origin_required'},{status:403,headers});
 const url=new URL(request.url),market=url.searchParams.get('market');if([...url.searchParams.keys()].some(key=>key!=='market')||url.searchParams.getAll('market').length!==1||!market||!enabledMarket(market))return Response.json({error:'invalid_query'},{status:400,headers});if(market!=='it')return Response.json({error:'market_kalodata_identity_unavailable'},{status:409,headers});
 try{return Response.json({market,...await readIdentity()},{headers});}
 catch{return Response.json({error:'kalodata_identity_unavailable'},{status:503,headers});}
}

export async function POST(request:Request){
 if(!isLocalRequest(request,true))return Response.json({error:'local_origin_required'},{status:403,headers});
 let body:unknown,market='';
 try{const url=new URL(request.url);if([...url.searchParams.keys()].some(key=>key!=='market')||url.searchParams.getAll('market').length!==1)throw Error();market=url.searchParams.get('market')!;if(!enabledMarket(market))throw Error();body=await request.json();}
 catch{return Response.json({error:'invalid_kalodata_identity_request'},{status:400,headers});}
 if(!body||typeof body!=="object"||Array.isArray(body)||(body as Record<string,unknown>).market!==market)return Response.json({error:'market_mismatch'},{status:409,headers});
 if(market!=='it')return Response.json({error:'market_kalodata_identity_unavailable'},{status:409,headers});
 const {market:_market,...payload}=body as Record<string,unknown>;
 let call:{action:"save"|"probe"|"activate"|"refresh";config?:KalodataIdentityConfig};
 try{call=validateIdentityRequest(payload);}
 catch{return Response.json({error:'invalid_kalodata_identity_request'},{status:400,headers});}
 try{
  if(call.action==="save")return Response.json(await saveIdentity(call.config as KalodataIdentityConfig),{headers});
  if(call.action==="probe")return Response.json(await probeIdentity(),{headers});
  return Response.json(await startIdentityLogin(call.action),{headers});
 }
 catch{return Response.json({error:'kalodata_identity_unavailable'},{status:503,headers});}
}
