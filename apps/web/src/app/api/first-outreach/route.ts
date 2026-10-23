import {invokeFirstOutreach,readFirstOutreach,validateFirstOutreachRequest} from "../../../server/first-outreach/bridge.ts";
import {isLocalRequest} from "../../../server/runtime/validation.ts";
export const runtime="nodejs";
export const dynamic="force-dynamic";
const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"};
function marketQuery(request:Request):string|null{
 const query=new URL(request.url).searchParams;
 return [...query.keys()].every(key=>key==="market")&&query.getAll("market").length===1&&query.get("market")==="br"?"br":null;
}
export async function GET(request:Request){
 if(!isLocalRequest(request,false))return Response.json({error:"local_origin_required"},{status:403,headers});
 if(!marketQuery(request))return Response.json({error:"invalid_query"},{status:400,headers});
 try{return Response.json(await readFirstOutreach(),{headers});}catch{return Response.json({error:"first_outreach_unavailable"},{status:503,headers});}
}
export async function POST(request:Request){
 if(!isLocalRequest(request,true))return Response.json({error:"local_origin_required"},{status:403,headers});
 const market=marketQuery(request);if(!market)return Response.json({error:"invalid_query"},{status:400,headers});
 if(request.headers.get("content-type")?.split(";")[0].trim()!=="application/json")return Response.json({error:"json_required"},{status:415,headers});
 let body;
 try{const raw=await request.text();if(new TextEncoder().encode(raw).length>8192)throw Error();const value=JSON.parse(raw);if(value?.market!==market)return Response.json({error:"market_mismatch"},{status:409,headers});body=validateFirstOutreachRequest(value);}catch{return Response.json({error:"invalid_first_outreach_request"},{status:400,headers});}
 try{return Response.json(await invokeFirstOutreach(body),{headers});}catch(error){
  const code=error instanceof Error?error.message:"first_outreach_unavailable";
  const known=/^[a-z0-9_:-]{1,100}$/.test(code)?code:"first_outreach_unavailable";
  return Response.json({error:known},{status:/conflict|approval_required|not_ready|pending|unknown|canary/.test(known)?409:503,headers});
 }
}
