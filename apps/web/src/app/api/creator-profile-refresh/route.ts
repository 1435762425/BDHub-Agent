import {InputError,isLocalRequest} from "@/server/runtime/validation";
import {callRefreshCommand,parseRefreshQuery,parseRefreshRequest,ProfileRefreshError} from "@/server/creator-identities/refresh";
export const runtime="nodejs";
export const dynamic="force-dynamic";
const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"};
function reject(){return Response.json({error:{code:"local_origin_required",message:"画像刷新仅接受本机工作台请求。"}},{status:403,headers});}
function failed(error:unknown){if(error instanceof InputError||error instanceof ProfileRefreshError)return Response.json({error:{code:error.code,message:error.message}},{status:error.status,headers});return Response.json({error:{code:"refresh_unavailable",message:"暂未确认画像请求，请保留原请求编号。"}},{status:503,headers});}
export async function GET(request:Request){if(!isLocalRequest(request,false))return reject();try{const {command,input}=parseRefreshQuery(request.url);return Response.json(await callRefreshCommand(command,input),{headers});}catch(error){return failed(error);}}
export async function POST(request:Request){
  if(!isLocalRequest(request,true))return reject();
  if(request.headers.get("content-type")?.split(";")[0].trim()!=="application/json")return Response.json({error:{code:"json_required",message:"请使用 JSON 请求。"}},{status:415,headers});
  try{
    const reader=request.body?.getReader();if(!reader)throw new InputError("请求为空。");const chunks:Uint8Array[]=[];let length=0;
    try{for(;;){const {value,done}=await reader.read();if(done)break;length+=value.byteLength;if(length>4096){await reader.cancel();throw new InputError("请求内容过长。");}chunks.push(value);}}finally{reader.releaseLock();}
    const bytes=new Uint8Array(length);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.byteLength;}
    let input:unknown;try{input=JSON.parse(new TextDecoder("utf-8",{fatal:true}).decode(bytes));}catch{throw new InputError("请求不是有效 JSON。");}
    return Response.json(await callRefreshCommand("enqueue",parseRefreshRequest(input)),{headers});
  }catch(error){return failed(error);}
}
