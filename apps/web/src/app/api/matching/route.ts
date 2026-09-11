import {getMatchingStore} from "@/server/matching/instance";
import {MatchingError} from "@/server/matching/store";
import {parseMatchingCommand,parseMatchingQuery} from "@/server/matching/validation";
import {InputError,isLocalRequest} from "@/server/runtime/validation";

export const runtime="nodejs";
export const dynamic="force-dynamic";
const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"};
const reject=()=>Response.json({error:{code:"local_origin_required",message:"匹配接口只接受本机工作台请求。"}},{status:403,headers});
function failure(error:unknown){
  if(error instanceof InputError||error instanceof MatchingError)return Response.json({error:{code:error.code,message:error.message}},{status:error.status,headers});
  console.error("Matching operation failed",error instanceof Error?error.name:"unknown");
  return Response.json({error:{code:"matching_unavailable",message:"匹配服务暂不可用，请保留当前请求编号后重试。"}},{status:503,headers});
}
export async function GET(request:Request){
  if(!isLocalRequest(request,false))return reject();
  try {
    const {view,options}=parseMatchingQuery(request.url),store=getMatchingStore();
    return Response.json(view==="stats"?store.stats():view==="products"?store.listProducts(options):store.listCreators(options),{headers});
  }catch(error){return failure(error);}
}
export async function POST(request:Request){
  if(!isLocalRequest(request,true))return reject();
  if(request.headers.get("content-type")?.split(";")[0].trim()!=="application/json")return Response.json({error:{code:"json_required",message:"请使用 JSON 请求。"}},{status:415,headers});
  try {
    const reader=request.body?.getReader();if(!reader)throw new InputError("请求内容为空。");
    const chunks:Uint8Array[]=[];let length=0;
    try {for(;;){const {done,value}=await reader.read();if(done)break;length+=value.byteLength;if(length>8192){await reader.cancel();throw new InputError("请求内容过长。");}chunks.push(value);}}finally{reader.releaseLock();}
    const buffer=new Uint8Array(length);let offset=0;for(const c of chunks){buffer.set(c,offset);offset+=c.byteLength;}
    let body:unknown;try{body=JSON.parse(new TextDecoder("utf-8",{fatal:true}).decode(buffer));}catch{throw new InputError("请求不是有效 JSON。");}
    const {command:c,requestId}=parseMatchingCommand(body),store=getMatchingStore();
    if(c.type==="recall")return Response.json({kind:"run",run:store.recall(c.query)},{headers});
    if(c.type==="prepare_review")return Response.json({kind:"packet",packet:store.prepareReview(c.runId,c.creatorId)},{headers});
    return Response.json({kind:"change",...store.demoChange(c.productId,c.expectedRevision,c.change,requestId)},{headers});
  }catch(error){return failure(error);}
}
