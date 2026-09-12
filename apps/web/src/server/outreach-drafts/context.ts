import {createHash} from "node:crypto";
import {readFileSync} from "node:fs";
import {join} from "node:path";
import type {MatchRun,ReviewPacket} from "../../features/matching/contracts.ts";
import type {CreatorIdentitySummary} from "../../features/creator-identities/contracts.ts";
import {getMatchingStore} from "../matching/instance.ts";
import {getCreatorIdentityStore} from "../creator-identities/instance.ts";
import {ITALIAN_PRODUCT_NAMES} from "../second-pilot/skills.ts";
import {projectRoot} from "../creator-identities/refresh.ts";

export type DraftStyle="friendly"|"direct"|"detailed";
export interface DraftContextRequest {packetId:string;style:DraftStyle;instructions:string;}
interface DraftFact {id:string;kind:"recipient_handle"|"product_name"|"category_alignment";value:string|string[];}
export interface DraftContext {
  schema:"bdhub.outreach-draft-context.v1";
  binding:{market:"it";registryCreatorId:string;matchingCreatorId:string;oecId:string;runId:string;runFingerprint:string;packetId:string;packetFingerprint:string;identityObservedAt:string;policyVersion:"it-intent-invitation@1";skillSha256:string;};
  modelFacts:{intent:"explore_interest";language:"it";recipient:{handle:string|null};style:DraftStyle;instructions:string;products:{id:string;nameIt:string;sharedCategories:string[];factIds:string[]}[];facts:DraftFact[];commercialTerms:{status:"not_verified";allowedPromises:never[]};};
  productBindings:{id:string;productId:string;pid:string}[];
  evidenceSources:{factId:string;sourceRef:string;observedAt:number}[];
  executionBlocked:true;
}
export class DraftContextError extends Error {readonly code:string;readonly status:number;constructor(code:string,message:string,status=409){super(message);this.code=code;this.status=status;}}
function fail(code:string,message:string,status=409):never{throw new DraftContextError(code,message,status);}
function stable(value:unknown):string{if(Array.isArray(value))return `[${value.map(stable).join(",")}]`;if(value&&typeof value==="object")return `{${Object.entries(value).sort(([a],[b])=>a<b?-1:a>b?1:0).map(([key,v])=>`${JSON.stringify(key)}:${stable(v)}`).join(",")}}`;return JSON.stringify(value);}
export function normalizeDraftContextRequest(input:unknown):DraftContextRequest {
  if(!input||typeof input!=="object"||Array.isArray(input))fail("invalid_request","草稿请求格式不正确。",400);
  const value=input as Record<string,unknown>;
  if(Object.keys(value).some(key=>!["packetId","style","instructions"].includes(key))||typeof value.packetId!=="string"||!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$/.test(value.packetId)||!["friendly","direct","detailed"].includes(String(value.style))||typeof value.instructions!=="string"||value.instructions.length>500)fail("invalid_request","请提供有效资料包、口吻和最多 500 字的表达要求。",400);
  return {packetId:value.packetId,style:value.style as DraftStyle,instructions:value.instructions.trim()};
}

/** Pure compiler: commercial conditions and statistics are deliberately absent
 * from the text-generation capsule. Identity binding remains outside modelFacts.
 */
export function compileDraftContext(request:DraftContextRequest,packet:ReviewPacket,run:MatchRun,identity:CreatorIdentitySummary,skillSha256:string) {
  request=normalizeDraftContextRequest(request);
  if(packet.id!==request.packetId||packet.runId!==run.id||run.stale||packet.executable!==false||packet.executionBlocked!==true||packet.payload.datasetId===undefined||!String(packet.payload.datasetId).startsWith("italy-profiles-"))fail("stale_context","资料包不是当前可用的画像分析版本。");
  const candidates=run.candidates.filter(candidate=>candidate.creator.id===packet.creatorId);
  const creator=candidates[0]?.creator;
  if(!creator||creator.market!=="it"||!creator.oecId||creator.externalIdentity||!creator.profileOrigin||identity.market!=="it"||identity.oecId!==creator.oecId||identity.creatorId!==creator.profileOrigin.creatorId||identity.handleConflict)fail("identity_unverified","此草稿尚未取得唯一、稳定的意大利达人身份。");
  if(creator.control==="human"||creator.control==="paused"||creator.marketingStopped===true||candidates.some(c=>c.readiness==="suppressed"))fail("relationship_suppressed","此关系已暂停、拒联或由人工接管，未生成新稿。");
  if(!Number.isFinite(Date.parse(identity.lastObservedAt))||Date.parse(identity.lastObservedAt)>creator.profileOrigin.observedAt)fail("stale_context","达人画像已有更新，请按最新资料重新分析。");
  const packetCreator=packet.payload.creator as Record<string,unknown>|undefined;
  if(packetCreator?.id!==creator.id||packetCreator.oecId!==creator.oecId||packetCreator.identityRegistryId!==identity.creatorId)fail("identity_unverified","资料包身份与当前档案不一致。");
  if(!/^[a-f0-9]{64}$/.test(skillSha256))fail("policy_unavailable","草稿策略版本未就绪。",503);
  const included=Array.isArray(packet.payload.candidates)?packet.payload.candidates as {product?:{id?:unknown;pid?:unknown}}[]:[];
  const allowed=new Map(included.map(row=>[row.product?.id,row.product?.pid]));
  const selected=candidates.filter(candidate=>allowed.get(candidate.product.id)===candidate.product.pid&&candidate.analysis.tier==="category_aligned"&&candidate.features.categoryOverlap>0&&candidate.product.categoryFact?.status!=="conflict"&&creator.categoryFact?.status!=="conflict").slice(0,3);
  if(!selected.length)fail("draft_facts_missing","当前资料包缺少足以组织个性化邀约的商品匹配依据。",422);
  const modelFacts:DraftContext["modelFacts"]={intent:"explore_interest",language:"it",recipient:{handle:identity.currentHandle},style:request.style,instructions:request.instructions,products:[],facts:[],commercialTerms:{status:"not_verified",allowedPromises:[]}};
  const productBindings:DraftContext["productBindings"]=[],evidenceSources:DraftContext["evidenceSources"]=[];
  if(identity.currentHandle){modelFacts.facts.push({id:"recipient",kind:"recipient_handle",value:identity.currentHandle});evidenceSources.push({factId:"recipient",sourceRef:`identity-registry:${identity.creatorId}:handle`,observedAt:Date.parse(identity.currentHandleVerifiedAt||identity.lastObservedAt)});}
  selected.forEach((candidate,index)=>{
    const id=`p${index+1}`,nameIt=ITALIAN_PRODUCT_NAMES[candidate.product.pid];
    if(!nameIt)fail("product_name_unverified","商品尚无已核对的意大利语简名。",422);
    const shared=candidate.product.categories.filter(category=>creator.categories.includes(category));
    if(!shared.length||candidate.product.categoryFact?.namespace!==creator.categoryFact?.namespace)fail("draft_facts_missing","商品和达人缺少同口径类目依据。",422);
    const factIds=[`${id}-name`,`${id}-fit`];modelFacts.products.push({id,nameIt,sharedCategories:shared,factIds});
    modelFacts.facts.push({id:factIds[0],kind:"product_name",value:nameIt},{id:factIds[1],kind:"category_alignment",value:shared});
    productBindings.push({id,productId:candidate.product.id,pid:candidate.product.pid});
    evidenceSources.push({factId:factIds[0],sourceRef:candidate.product.source.ref,observedAt:candidate.product.source.observedAt},{factId:factIds[1],sourceRef:creator.categoryFact!.source.ref,observedAt:creator.categoryFact!.source.observedAt});
  });
  const context:DraftContext={schema:"bdhub.outreach-draft-context.v1",binding:{market:"it",registryCreatorId:identity.creatorId,matchingCreatorId:creator.id,oecId:creator.oecId,runId:run.id,runFingerprint:run.fingerprint,packetId:packet.id,packetFingerprint:packet.fingerprint,identityObservedAt:identity.lastObservedAt,policyVersion:"it-intent-invitation@1",skillSha256},modelFacts,productBindings,evidenceSources,executionBlocked:true};
  if(Buffer.byteLength(JSON.stringify(modelFacts),"utf8")>8000)fail("draft_context_too_large","必要事实超过单条草稿范围，请缩小商品或表达要求。",422);
  return {context,fingerprint:createHash("sha256").update(stable(context)).digest("hex"),contextRequest:request};
}
export function buildDraftContext(input:DraftContextRequest){
  const request=normalizeDraftContextRequest(input),store=getMatchingStore("italy-profiles"),packet=store.getCurrentReviewPacket(request.packetId),run=store.getRun(packet.runId);
  const creator=run.candidates.find(candidate=>candidate.creator.id===packet.creatorId)?.creator;
  if(!creator?.oecId||!creator.profileOrigin)fail("identity_unverified","该资料尚未关联到稳定身份档案。");
  const identities=getCreatorIdentityStore();
  try{
    const found=identities.source({market:"it",oecId:creator.oecId});
    if(found.status!=="verified"||!found.creator)fail("identity_unverified","当前达人身份尚未核实。");
    let skill:string;try{skill=readFileSync(join(projectRoot(),"apps/agent/skills/it-intent-invitation/v1.md"),"utf8");}catch{fail("policy_unavailable","草稿策略文件未就绪。",503);}
    return compileDraftContext(request,packet,run,found.creator,createHash("sha256").update(skill).digest("hex"));
  }finally{identities.close();}
}
