import {readLeadsQueue,saveLeadsQueue,startLeadsRun,validateLeadsQueueRequest} from "../../../server/leads-queue/bridge.ts";
import type {LeadsQueueConfig} from "../../../server/leads-queue/bridge.ts";
import {isLocalRequest} from "../../../server/runtime/validation.ts";
import {operationalMarket} from "../../../server/markets/registry.ts";
export const runtime="nodejs";
export const dynamic="force-dynamic";

const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"};

export async function GET(request:Request){
 if(!isLocalRequest(request,false))return Response.json({error:'local_origin_required'},{status:403,headers});
 const url=new URL(request.url),market=url.searchParams.get('market');if([...url.searchParams.keys()].some(key=>key!=='market')||url.searchParams.getAll('market').length!==1||!market||!operationalMarket(market))return Response.json({error:'invalid_query'},{status:400,headers});
 try{return Response.json(await readLeadsQueue(market),{headers});}
 catch{return Response.json({error:'leads_queue_unavailable'},{status:503,headers});}
}

export async function POST(request:Request){
 if(!isLocalRequest(request,true))return Response.json({error:'local_origin_required'},{status:403,headers});
 let body:unknown,market='';
 try{const url=new URL(request.url);if([...url.searchParams.keys()].some(key=>key!=='market')||url.searchParams.getAll('market').length!==1)throw Error();market=url.searchParams.get('market')!;if(!operationalMarket(market))throw Error();body=await request.json();}
 catch{return Response.json({error:'invalid_leads_queue_request'},{status:400,headers});}
 let call:{action:"save";market:string;config:LeadsQueueConfig}|{action:"run";market:string};
 try{call=validateLeadsQueueRequest(body);}
 catch{return Response.json({error:'invalid_leads_queue_request'},{status:400,headers});}
 if(call.market!==market)return Response.json({error:'market_mismatch'},{status:409,headers});
 try{
  // One batch at a time: an already-running batch keeps its own record and is not restarted.
  if(call.action==="run"){
   const current=await readLeadsQueue(market);
   if(current.run?.running)return Response.json({error:'leads_run_already_running'},{status:409,headers});
   startLeadsRun(market);
   return Response.json(await readLeadsQueue(market),{headers});
  }
  return Response.json(await saveLeadsQueue(market,call.config),{headers});
 }
 catch{return Response.json({error:'leads_queue_unavailable'},{status:503,headers});}
}
