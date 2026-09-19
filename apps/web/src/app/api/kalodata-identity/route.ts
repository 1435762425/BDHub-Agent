import {readIdentity,saveIdentity,probeIdentity,startIdentityLogin,validateIdentityRequest} from "../../../server/kalodata-identity/bridge.ts";
import type {KalodataIdentityConfig} from "../../../server/kalodata-identity/bridge.ts";
import {isLocalRequest} from "../../../server/runtime/validation.ts";
export const runtime="nodejs";
export const dynamic="force-dynamic";

const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"};

export async function GET(request:Request){
 if(!isLocalRequest(request,false))return Response.json({error:'local_origin_required'},{status:403,headers});
 try{return Response.json(await readIdentity(),{headers});}
 catch{return Response.json({error:'kalodata_identity_unavailable'},{status:503,headers});}
}

export async function POST(request:Request){
 if(!isLocalRequest(request,true))return Response.json({error:'local_origin_required'},{status:403,headers});
 let body:unknown;
 try{body=await request.json();}
 catch{return Response.json({error:'invalid_kalodata_identity_request'},{status:400,headers});}
 let call:{action:"save"|"probe"|"activate"|"refresh";config?:KalodataIdentityConfig};
 try{call=validateIdentityRequest(body);}
 catch{return Response.json({error:'invalid_kalodata_identity_request'},{status:400,headers});}
 try{
  if(call.action==="save")return Response.json(await saveIdentity(call.config as KalodataIdentityConfig),{headers});
  if(call.action==="probe")return Response.json(await probeIdentity(),{headers});
  return Response.json(await startIdentityLogin(call.action),{headers});
 }
 catch{return Response.json({error:'kalodata_identity_unavailable'},{status:503,headers});}
}
