import {createDiscoveryHandlers} from "../../../server/creator-identities/discovery.ts";
import {isLocalRequest} from "../../../server/runtime/validation.ts";

export const runtime="nodejs";
export const dynamic="force-dynamic";
const handlers=createDiscoveryHandlers();
const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"};
function scoped(request:Request,mutating:boolean){
 if(!isLocalRequest(request,mutating))return {error:Response.json({error:{code:"local_origin_required",message:"名单接口只接受本机工作台请求。"}},{status:403,headers})};
 const url=new URL(request.url);
 if([...url.searchParams.keys()].some(key=>!["market","view","batchId"].includes(key))||url.searchParams.getAll("market").length!==1||url.searchParams.get("market")!=="it")return {error:Response.json({error:{code:"invalid_request",message:"必须指定已接通的市场。"}},{status:400,headers})};
 url.searchParams.delete("market");return {url};
}
async function withMarket(response:Response){if(!response.ok)return response;const value=await response.json();return Response.json({market:"it",...value},{headers});}
export async function GET(request:Request){const scope=scoped(request,false);if(scope.error)return scope.error;return withMarket(await handlers.GET(new Request(scope.url!,{method:"GET",headers:request.headers})));}
export async function POST(request:Request){const scope=scoped(request,true);if(scope.error)return scope.error;if([...scope.url!.searchParams.keys()].length)return Response.json({error:{code:"invalid_request",message:"请求参数不完整。"}},{status:400,headers});
 let value:Record<string,unknown>;try{const raw=await request.text();if(new TextEncoder().encode(raw).length>131072)return Response.json({error:{code:"payload_too_large",message:"请求内容过长。"}},{status:413,headers});value=JSON.parse(raw);if(!value||typeof value!=="object"||Array.isArray(value)||value.market!=="it")return Response.json({error:{code:"market_mismatch",message:"市场不一致。"}},{status:409,headers});}catch{return Response.json({error:{code:"invalid_request",message:"请求不是有效 JSON。"}},{status:400,headers});}
 if(value.command==="control")delete value.market;
 return withMarket(await handlers.POST(new Request(scope.url!,{method:"POST",headers:request.headers,body:JSON.stringify(value)})));
}
