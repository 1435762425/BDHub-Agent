import {execFile} from "node:child_process";
import {join} from "node:path";
import {projectRoot} from "../creator-identities/refresh.ts";
import {isLocalRequest} from "../runtime/validation.ts";
import {enabledMarket} from "../markets/registry.ts";

type AgentAction="save-guide"|"simulate"|"replay"|"start-first"|"resume-full";
const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"};

function run(action:"status"|"trace"|AgentAction,market:string,input?:unknown,identifier?:string):Promise<Record<string,unknown>>{
 const root=projectRoot(),args=[join(root,"scripts/agent-replies.py"),action,"--market",market];
 if(action==="trace"&&identifier)args.push("--decision-id",identifier);
 if(action==="replay"&&identifier)args.push("--turn-id",identifier);
 return new Promise((resolve,reject)=>{
  const child=execFile(join(root,".venv/bin/python"),args,{cwd:root,timeout:120000,maxBuffer:2*1024*1024,
   env:{...process.env,PYTHONDONTWRITEBYTECODE:"1"}},(error,stdout)=>{
    try{const value=JSON.parse(stdout) as Record<string,unknown>;if(error||value.error)throw Error(String(value.error??"agent_reply_unavailable"));resolve(value);}
    catch(reason){reject(reason);}
   });
  if(input!==undefined)child.stdin?.end(JSON.stringify(input));
 });
}

async function body(request:Request):Promise<Record<string,unknown>>{
 if(request.headers.get("content-type")?.split(";")[0].trim()!=="application/json")throw Error("invalid_agent_request");
 const bytes=await request.arrayBuffer();if(bytes.byteLength>50000)throw Error("invalid_agent_request");
 const value=JSON.parse(new TextDecoder("utf-8",{fatal:true}).decode(bytes));
 if(!value||typeof value!=="object"||Array.isArray(value))throw Error("invalid_agent_request");
 return value as Record<string,unknown>;
}

const validMarket=(market:string|null)=>market&&enabledMarket(market)?.contentReady?market:null;
const id=(value:unknown,prefix:string)=>typeof value==="string"&&new RegExp(`^${prefix}-[a-f0-9]{24}$`).test(value)?value:null;

export function createAgentRepliesHandlers(invoke=run){return {
 GET:async(request:Request)=>{
  if(!isLocalRequest(request,false))return Response.json({error:"local_origin_required"},{status:403,headers});
  const url=new URL(request.url),market=validMarket(url.searchParams.get("market")),decisionId=url.searchParams.get("decisionId");
  if(!market||[...url.searchParams.keys()].some(key=>!["market","decisionId"].includes(key))||
     url.searchParams.getAll("market").length!==1||decisionId!=null&&(!id(decisionId,"agent-decision")||url.searchParams.getAll("decisionId").length!==1))
   return Response.json({error:"invalid_agent_query"},{status:400,headers});
  try{return Response.json(await invoke(decisionId?"trace":"status",market,undefined,decisionId??undefined),{headers});}
  catch{return Response.json({error:"agent_reply_unavailable"},{status:503,headers});}
 },
 POST:async(request:Request)=>{
  if(!isLocalRequest(request,true))return Response.json({error:"local_origin_required"},{status:403,headers});
  const url=new URL(request.url),market=validMarket(url.searchParams.get("market"));
  if(!market||[...url.searchParams.keys()].some(key=>key!=="market")||url.searchParams.getAll("market").length!==1)
   return Response.json({error:"invalid_agent_request"},{status:400,headers});
  let value:Record<string,unknown>,action:AgentAction,input:unknown,identifier:string|undefined;
  try{
   value=await body(request);if(value.market!==market)throw Error();
   if(value.action==="save-guide"){
    if(Object.keys(value).sort().join(",")!=="action,body,expectedRevision,market"||
       typeof value.body!=="string"||value.body.length<500||value.body.length>15000||
       !Number.isSafeInteger(value.expectedRevision)||Number(value.expectedRevision)<0)throw Error();
    action="save-guide";input={body:value.body,expectedRevision:value.expectedRevision};
   }else if(value.action==="simulate"){
    if(!["action,history,market","action,history,market,previousWaitFor"].includes(Object.keys(value).sort().join(","))||
       value.previousWaitFor!=null&&!['contact','clarification'].includes(String(value.previousWaitFor))||!Array.isArray(value.history)||
       value.history.length<1||value.history.length>20||value.history.some(row=>!row||typeof row!=="object"||
       !["inbound","outbound"].includes(row.direction)||typeof row.text!=="string"||!row.text.trim()||row.text.length>2000))throw Error();
    action="simulate";input={history:value.history,previousWaitFor:value.previousWaitFor??null};
   }else if(value.action==="start-first"||value.action==="resume-full"){
    if(Object.keys(value).sort().join(",")!=="action,market,requestId"||typeof value.requestId!=="string"||
       !/^[A-Za-z0-9][A-Za-z0-9._:-]{7,119}$/.test(value.requestId))throw Error();
    action=value.action;input={requestId:value.requestId};
   }else if(value.action==="replay"){
    if(Object.keys(value).sort().join(",")!=="action,market,turnId"||!id(value.turnId,"turn"))throw Error();
    action="replay";identifier=value.turnId as string;input=undefined;
   }else throw Error();
  }catch{return Response.json({error:"invalid_agent_request"},{status:400,headers});}
  try{return Response.json(await invoke(action,market,input,identifier),{headers});}
  catch(error){const message=error instanceof Error?error.message:"";
   return Response.json({error:message==="agent_guide_revision_conflict"?message:"agent_reply_unavailable"},
    {status:message==="agent_guide_revision_conflict"?409:503,headers});}
 }
};}
