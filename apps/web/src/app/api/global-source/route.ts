import {createGlobalGet,createGlobalPost,readLinkStatus,validateLinkStatus} from "../../../server/global-source/bridge.ts";
import {isLocalRequest} from "../../../server/runtime/validation.ts";
export const runtime="nodejs";
export const dynamic="force-dynamic";
const globalGet=createGlobalGet();

export async function GET(request:Request){
 const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"};
 const url=new URL(request.url);
 if(!isLocalRequest(request,false))return Response.json({error:'local_origin_required'},{status:403,headers});
 if(url.searchParams.getAll('market').length!==1||url.searchParams.get('market')!=='it')return Response.json({error:'invalid_query'},{status:400,headers});
 url.searchParams.delete('market');
 if(url.searchParams.get("links")==="1"){
  if([...url.searchParams.keys()].some(k=>k!=="links"))return Response.json({error:'invalid_query'},{status:400,headers});
  try{return Response.json({market:'it',...validateLinkStatus(await readLinkStatus())},{headers});}
  catch{return Response.json({error:'catalog_link_status_unavailable'},{status:503,headers});}
 }
 const result=await globalGet(new Request(url,{method:'GET',headers:request.headers}));return result;
}

const globalPost=createGlobalPost();
export async function POST(request:Request){
 const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"};
 if(!isLocalRequest(request,true))return Response.json({error:'local_origin_required'},{status:403,headers});
 const url=new URL(request.url);if([...url.searchParams.keys()].some(key=>key!=='market')||url.searchParams.getAll('market').length!==1||url.searchParams.get('market')!=='it')return Response.json({error:'invalid_query'},{status:400,headers});
 let input:Record<string,unknown>;try{input=await request.json();if(!input||Array.isArray(input)||input.market!=='it')return Response.json({error:'market_mismatch'},{status:409,headers});}catch{return Response.json({error:'invalid_sync_request'},{status:400,headers});}
 const {market:_market,...body}=input;url.searchParams.delete('market');
 return globalPost(new Request(url,{method:'POST',headers:request.headers,body:JSON.stringify(body)}));
}
