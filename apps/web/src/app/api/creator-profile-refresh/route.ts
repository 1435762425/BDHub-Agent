import {InputError,isLocalRequest} from "../../../server/runtime/validation.ts";
import {callRefreshCommand,parseRefreshQuery,parseRefreshRequest,ProfileRefreshError} from "../../../server/creator-identities/refresh.ts";
export const runtime="nodejs";
export const dynamic="force-dynamic";
const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"};
function reject(){return Response.json({error:{code:"local_origin_required",message:"画像刷新仅接受本机工作台请求。"}},{status:403,headers});}
function failed(error:unknown){if(error instanceof InputError||error instanceof ProfileRefreshError)return Response.json({error:{code:error.code,message:error.message}},{status:error.status,headers});return Response.json({error:{code:"refresh_unavailable",message:"暂未确认画像请求，请保留原请求编号。"}},{status:503,headers});}
export async function GET(request:Request){if(!isLocalRequest(request,false))return reject();try{const {command,input}=parseRefreshQuery(request.url);return Response.json(await callRefreshCommand(command,input),{headers});}catch(error){return failed(error);}}
export async function POST(request:Request){
 if(!isLocalRequest(request,true))return reject();
 const url=new URL(request.url),market=url.searchParams.get("market");
 if([...url.searchParams.keys()].some(key=>key!=="market")||url.searchParams.getAll("market").length!==1||market!=="it")return Response.json({error:{code:"invalid_market",message:"市场无效或画像刷新尚未接通。"}},{status:400,headers});
  if(request.headers.get("content-type")?.split(";")[0].trim()!=="application/json")return Response.json({error:{code:"json_required",message:"请使用 JSON 请求。"}},{status:415,headers});
  try{
    const reader=request.body?.getReader();if(!reader)throw new InputError("请求为空。");const chunks:Uint8Array[]=[];let length=0;
    try{for(;;){const {value,done}=await reader.read();if(done)break;length+=value.byteLength;if(length>4096){await reader.cancel();throw new InputError("请求内容过长。");}chunks.push(value);}}finally{reader.releaseLock();}
    const bytes=new Uint8Array(length);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.byteLength;}
    let input:unknown;try{input=JSON.parse(new TextDecoder("utf-8",{fatal:true}).decode(bytes));}catch{throw new InputError("请求不是有效 JSON。");}
    const payload=parseRefreshRequest(input);if(payload.market!==market)return Response.json({error:{code:"market_mismatch",message:"市场不一致。"}},{status:409,headers});
    return Response.json(await callRefreshCommand("enqueue",payload),{headers});
  }catch(error){return failed(error);}
}
