import {readJobs,saveJobs,startJobsScheduler,stopJobsScheduler,validateJobsRequest} from "../../../server/jobs/bridge.ts";
import {isLocalRequest} from "../../../server/runtime/validation.ts";
import {enabledMarket} from "../../../server/markets/registry.ts";
export const runtime="nodejs";
export const dynamic="force-dynamic";

const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"};

export async function GET(request:Request){
 if(!isLocalRequest(request,false))return Response.json({error:'local_origin_required'},{status:403,headers});
 const url=new URL(request.url),market=url.searchParams.get('market');if([...url.searchParams.keys()].some(key=>key!=='market')||url.searchParams.getAll('market').length!==1||!market||!enabledMarket(market))return Response.json({error:'invalid_query'},{status:400,headers});
 try{return Response.json(await readJobs(market),{headers});}
 catch{return Response.json({error:'jobs_unavailable'},{status:503,headers});}
}

export async function POST(request:Request){
 if(!isLocalRequest(request,true))return Response.json({error:'local_origin_required'},{status:403,headers});
 let body:unknown,market='';
 try{const url=new URL(request.url);if([...url.searchParams.keys()].some(key=>key!=='market')||url.searchParams.getAll('market').length!==1)throw Error();market=url.searchParams.get('market')!;if(!enabledMarket(market))throw Error();body=await request.json();}
 catch{return Response.json({error:'invalid_jobs_request'},{status:400,headers});}
 let call;try{call=validateJobsRequest(body);}catch{return Response.json({error:'invalid_jobs_request'},{status:400,headers});}
 if(call.market!==market)return Response.json({error:'market_mismatch'},{status:409,headers});
 try{return Response.json(call.action==="save"?await saveJobs(market,call.payload):call.action==="start_scheduler"?await startJobsScheduler(market):await stopJobsScheduler(market),{headers});}
 catch(error){const code=error instanceof Error?error.message:"jobs_unavailable";return Response.json({error:code},{status:code.includes("already")||code.includes("not_running")?409:503,headers});}
}
