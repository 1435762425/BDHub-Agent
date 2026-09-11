import {getSecondPilotStore} from "@/server/second-pilot/instance";
import {SecondPilotError} from "@/server/second-pilot/store";
import {parseSecondPilotCommand,parseSecondPilotQuery} from "@/server/second-pilot/validation";
import {InputError,isLocalRequest} from "@/server/runtime/validation";
export const runtime="nodejs";
export const dynamic="force-dynamic";
const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"};
const reject=()=>Response.json({error:{code:"local_origin_required",message:"二发预演只接受本机工作台请求。"}},{status:403,headers});
function failed(error:unknown){if(error instanceof InputError||error instanceof SecondPilotError)return Response.json({error:{code:error.code,message:error.message}},{status:error.status,headers});console.error("Second pilot failed",error instanceof Error?error.name:"unknown");return Response.json({error:{code:"pilot_unavailable",message:"本地预演暂不可用，请保留请求编号重试。"}},{status:503,headers});}
export async function GET(request:Request){if(!isLocalRequest(request,false))return reject();try{const q=parseSecondPilotQuery(request.url),store=getSecondPilotStore();return Response.json(q.view==="overview"?store.overview():q.view==="case"?store.get(q.caseId!):store.list({offset:q.offset,limit:q.limit}),{headers});}catch(e){return failed(e);}}
export async function POST(request:Request){
  if(!isLocalRequest(request,true))return reject();
  if(request.headers.get("content-type")?.split(";")[0].trim()!=="application/json")return Response.json({error:{code:"json_required",message:"请使用JSON请求。"}},{status:415,headers});
  try{
    const reader=request.body?.getReader();if(!reader)throw new InputError("请求为空。");let length=0;const parts:Uint8Array[]=[];
    try{for(;;){const {value,done}=await reader.read();if(done)break;length+=value.byteLength;if(length>8192){await reader.cancel();throw new InputError("请求内容过长。");}parts.push(value);}}finally{reader.releaseLock();}
    const bytes=new Uint8Array(length);let offset=0;for(const part of parts){bytes.set(part,offset);offset+=part.byteLength;}
    let value:unknown;try{value=JSON.parse(new TextDecoder("utf-8",{fatal:true}).decode(bytes));}catch{throw new InputError("无效JSON。");}
    const {requestId,command:c}=parseSecondPilotCommand(value),store=getSecondPilotStore();
    if(c.type==="freeze")return Response.json({kind:"snapshot",result:store.freeze(c.caseId,c.expectedRevision,requestId)},{headers});
    if(c.type==="queue")return Response.json({kind:"action",result:store.queue(c.snapshotId,c.scenario,requestId)},{headers});
    if(c.type==="control")return Response.json({kind:"case",result:store.control(c.caseId,c.expectedRevision,c.mode,requestId)},{headers});
    return Response.json({kind:"action",result:store.verify(c.actionId,requestId)},{headers});
  }catch(e){return failed(e);}
}
