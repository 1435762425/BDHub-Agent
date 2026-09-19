import {readJobs,saveJobs,validateJobsSave} from "../../../server/jobs/bridge.ts";
import {isLocalRequest} from "../../../server/runtime/validation.ts";
export const runtime="nodejs";
export const dynamic="force-dynamic";

const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"};

export async function GET(request:Request){
 if(!isLocalRequest(request,false))return Response.json({error:'local_origin_required'},{status:403,headers});
 try{return Response.json(await readJobs(),{headers});}
 catch{return Response.json({error:'jobs_unavailable'},{status:503,headers});}
}

export async function POST(request:Request){
 if(!isLocalRequest(request,true))return Response.json({error:'local_origin_required'},{status:403,headers});
 let body:unknown;
 try{body=await request.json();}
 catch{return Response.json({error:'invalid_jobs_request'},{status:400,headers});}
 try{return Response.json(await saveJobs(validateJobsSave(body)),{headers});}
 catch{return Response.json({error:'invalid_jobs_request'},{status:400,headers});}
}
