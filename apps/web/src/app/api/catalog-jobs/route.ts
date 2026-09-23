import {readCatalogJobs,saveCatalogJob,startCatalogJob,stopCatalogJob,validateCatalogJobsRequest} from "../../../server/catalog-jobs/bridge.ts";
import {isLocalRequest} from "../../../server/runtime/validation.ts";
import {enabledMarket} from "../../../server/markets/registry.ts";
export const runtime="nodejs";
export const dynamic="force-dynamic";

const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"};

export async function GET(request:Request){
 if(!isLocalRequest(request,false))return Response.json({error:'local_origin_required'},{status:403,headers});
 const url=new URL(request.url),market=url.searchParams.get('market');if([...url.searchParams.keys()].some(key=>key!=='market')||url.searchParams.getAll('market').length!==1||!market||!enabledMarket(market))return Response.json({error:'invalid_query'},{status:400,headers});if(market!=='it')return Response.json({error:'market_catalog_job_unavailable'},{status:409,headers});
 try{return Response.json({market,...await readCatalogJobs()},{headers});}
 catch{return Response.json({error:'catalog_jobs_unavailable'},{status:503,headers});}
}

export async function POST(request:Request){
 if(!isLocalRequest(request,true))return Response.json({error:'local_origin_required'},{status:403,headers});
 let body:unknown,market='';
 try{const url=new URL(request.url);if([...url.searchParams.keys()].some(key=>key!=='market')||url.searchParams.getAll('market').length!==1)throw Error();market=url.searchParams.get('market')!;if(!enabledMarket(market))throw Error();body=await request.json();}
 catch{return Response.json({error:'invalid_catalog_jobs_request'},{status:400,headers});}
 if(market!=='it')return Response.json({error:'market_catalog_job_unavailable'},{status:409,headers});
 let call:ReturnType<typeof validateCatalogJobsRequest>;
 try{call=validateCatalogJobsRequest(body);}
 catch{return Response.json({error:'invalid_catalog_jobs_request'},{status:400,headers});}
 if(call.market!==market)return Response.json({error:'market_mismatch'},{status:409,headers});
 try{
  if(call.action==="save")return Response.json(await saveCatalogJob(call.name,call.config),{headers});
  if(call.action==="stop")return Response.json(await stopCatalogJob(call.name),{headers});
  return Response.json(await startCatalogJob(call.name,call.config),{headers});
 }
 catch(error){
  const code=error instanceof Error?error.message:"";
  // Already running (or nothing to stop) is the operator's own doing, not a broken service.
  if(code==="job_already_running")return Response.json({error:code},{status:409,headers});
  if(code==="job_not_running")return Response.json({error:code},{status:409,headers});
  return Response.json({error:'catalog_jobs_unavailable'},{status:503,headers});
 }
}
