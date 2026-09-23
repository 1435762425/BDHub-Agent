import {getCreatorIdentityStore} from "../../../server/creator-identities/instance.ts";
import {CreatorIdentityError} from "../../../server/creator-identities/store.ts";
import {parseCreatorIdentityQuery} from "../../../server/creator-identities/validation.ts";
import {InputError,isLocalRequest} from "../../../server/runtime/validation.ts";

export const runtime="nodejs";
export const dynamic="force-dynamic";
const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"};
export async function GET(request:Request){
  if(!isLocalRequest(request,false))return Response.json({error:{code:"local_origin_required",message:"身份资料接口只接受本机工作台请求。"}},{status:403,headers});
  let store:ReturnType<typeof getCreatorIdentityStore>|undefined;
  try{
    const query=parseCreatorIdentityQuery(request.url);store=getCreatorIdentityStore();
    const result=query.view==="overview"?store.overview(query.market):query.view==="list"?store.list(query):query.view==="detail"?store.detail(query.market,query.creatorId):store.source(query);
    if(query.view==="detail"&&"creator" in result&&result.creator===null)throw new CreatorIdentityError(404,"creator_not_found","当前市场没有这位达人。");
    return Response.json(result,{headers});
  }catch(error){
    if(error instanceof InputError||error instanceof CreatorIdentityError)return Response.json({error:{code:error.code,message:error.message}},{status:error.status,headers});
    console.error("Creator identity read failed",error instanceof Error?error.name:"unknown");
    return Response.json({error:{code:"identity_unavailable",message:"身份资料暂不可用，请稍后重试。"}},{status:503,headers});
  }finally{store?.close();}
}
