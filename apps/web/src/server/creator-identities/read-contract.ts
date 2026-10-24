import type {CreatorIdentityDetail,CreatorIdentityList,CreatorIdentityOverview,CreatorIdentityQuery,CreatorIdentitySummary,IdentityProfileField,IdentitySourceResolution,ProfileFieldValue} from "../../features/creator-identities/contracts.ts";
import {CreatorIdentityError} from "./errors.ts";

export type CreatorIdentityResponse=CreatorIdentityOverview|CreatorIdentityList|CreatorIdentityDetail|IdentitySourceResolution;
const invalid=():never=>{throw new CreatorIdentityError(503,"identity_unavailable","身份资料暂不可用，请稍后重试。");};
const object=(raw:unknown)=>raw&&typeof raw==="object"&&!Array.isArray(raw)?raw as Record<string,unknown>:invalid();
function keys(row:Record<string,unknown>,required:string[],optional:string[]=[]){if(required.some(k=>!Object.hasOwn(row,k))||Object.keys(row).some(k=>!required.includes(k)&&!optional.includes(k)))invalid();}
const text=(raw:unknown,max=512)=>typeof raw==="string"&&raw.length>0&&raw.length<=max?raw:invalid();
const maybeText=(raw:unknown,max=80)=>raw===null?null:text(raw,max);
const count=(raw:unknown)=>typeof raw==="number"&&Number.isSafeInteger(raw)&&raw>=0?raw:invalid();
const flag=(raw:unknown)=>typeof raw==="boolean"?raw:invalid();
const list=(raw:unknown,max=10000)=>Array.isArray(raw)&&raw.length<=max?raw:invalid();
const match=(raw:unknown,pattern:RegExp,max:number)=>{const value=text(raw,max);return pattern.test(value)?value:invalid();};
const creatorId=(raw:unknown)=>match(raw,/^creator_[a-f0-9]{32}$/,40);
const oecId=(raw:unknown)=>match(raw,/^\d{1,64}$/,64);
const dataset=(raw:unknown)=>raw==="ready"||raw==="not_imported"?raw:invalid();
function creator(raw:unknown,market:string):CreatorIdentitySummary{
 const row=object(raw);keys(row,["kind","status","creatorId","market","oecId","currentHandle","currentHandleVerifiedAt","verifiedAt","lastObservedAt","handleConflict"]);
 if(row.kind!=="creator"||row.status!=="verified"||row.market!==market)invalid();
 return {kind:"creator",status:"verified",creatorId:creatorId(row.creatorId),market,oecId:oecId(row.oecId),currentHandle:maybeText(row.currentHandle,64),currentHandleVerifiedAt:maybeText(row.currentHandleVerifiedAt),verifiedAt:text(row.verifiedAt,80),lastObservedAt:text(row.lastObservedAt,80),handleConflict:flag(row.handleConflict)};
}
function counts(raw:Record<string,unknown>){return {verifiedIdentities:count(raw.verifiedIdentities),pendingLeads:count(raw.pendingLeads),resolvedLeads:count(raw.resolvedLeads),observations:count(raw.observations),lastVerifiedAt:maybeText(raw.lastVerifiedAt)};}
const COUNT_KEYS=["verifiedIdentities","pendingLeads","resolvedLeads","observations","lastVerifiedAt"];
function jsonValue(raw:unknown,depth=0):ProfileFieldValue{
 if(depth>12)invalid();
 if(raw===null||typeof raw==="boolean")return raw;
 if(typeof raw==="number")return Number.isFinite(raw)?raw:invalid();
 if(typeof raw==="string")return raw.length<=4096?raw:invalid();
 if(Array.isArray(raw))return list(raw,1000).map(value=>jsonValue(value,depth+1));
 const row=object(raw);if(Object.keys(row).length>100)invalid();
 return Object.fromEntries(Object.entries(row).map(([key,value])=>[text(key,100),jsonValue(value,depth+1)]));
}
const PROFILE_FIELDS=new Set(["handle","creator_oecuid","selection_region","follower_cnt","med_gmv_revenue","video_gmv","live_gmv","units_sold","video_avg_view_cnt","industry_groups","content_groups","top_video_data","product_price_range","video_publish_cnt_30d","ec_video_publish_cnt_30d","live_streaming_cnt_30d","ec_live_streaming_cnt_30d","gpm","ec_live_gpm","ec_video_gpm","partnered_brand","sales_performance_end_time"]);
function profileField(raw:unknown):IdentityProfileField{
 const row=object(raw);keys(row,["name","status","observedAt"],["value","lastAvailable"]);
 const name=text(row.name,80);if(!PROFILE_FIELDS.has(name)||!["absent","no_value","unauthorized","error","zero","value"].includes(String(row.status)))invalid();
 const status=row.status as IdentityProfileField["status"],hasValue=Object.hasOwn(row,"value");
 if(hasValue!==(status==="value"||status==="zero"))invalid();
 let lastAvailable:IdentityProfileField["lastAvailable"];
 if(row.lastAvailable!==undefined){const last=object(row.lastAvailable);keys(last,["status","value","observedAt"]);if(last.status!=="value"&&last.status!=="zero")invalid();lastAvailable={status:last.status as "value"|"zero",value:jsonValue(last.value),observedAt:text(last.observedAt,80)};}
 return {name,status,observedAt:maybeText(row.observedAt),...(hasValue?{value:jsonValue(row.value)}:{}),...(lastAvailable?{lastAvailable}:{})};
}

export function validateCreatorIdentityResult(raw:unknown,query:CreatorIdentityQuery):CreatorIdentityResponse{
 const value=object(raw);
 if(query.view==="overview"){
  keys(value,["datasetStatus","market",...COUNT_KEYS,"repliedCreators","showcaseCreators","identityStage","markets"]);
  if(value.market!==query.market)invalid();
  const totals=counts(value),markets=list(value.markets,1).map(rawMarket=>{const row=object(rawMarket);keys(row,["market",...COUNT_KEYS]);if(row.market!==query.market)invalid();const result=counts(row);if(COUNT_KEYS.some(key=>result[key as keyof typeof result]!==totals[key as keyof typeof totals]))invalid();return {market:query.market,...result};});
  if(markets.length!==1)invalid();
  let identityStage:CreatorIdentityOverview["identityStage"]=null;
  if(value.identityStage!==null){const row=object(value.identityStage);keys(row,["resolved","unresolved"]);identityStage={resolved:count(row.resolved),unresolved:count(row.unresolved)};}
  return {datasetStatus:dataset(value.datasetStatus),market:query.market,...totals,repliedCreators:value.repliedCreators===null?null:count(value.repliedCreators),showcaseCreators:value.showcaseCreators===null?null:count(value.showcaseCreators),identityStage,markets};
 }
 if(query.view==="list"){
  keys(value,["datasetStatus","market","status","items","total","offset","limit"]);
  if(value.market!==query.market||value.status!==query.status||value.offset!==query.offset||value.limit!==query.limit)invalid();
  const items=list(value.items,query.limit).map(rawItem=>{
   if(query.status==="verified")return creator(rawItem,query.market);
   const row=object(rawItem);keys(row,["kind","status","leadId","market","handle","observedAt","historicalCrossSourceIdentityProven"]);
   if(row.kind!=="lead"||row.status!=="pending"||row.market!==query.market||row.historicalCrossSourceIdentityProven!==false)invalid();
   return {kind:"lead" as const,status:"pending" as const,leadId:text(row.leadId,160),market:query.market,handle:text(row.handle,64),observedAt:text(row.observedAt,80),historicalCrossSourceIdentityProven:false as const};
  });
  const total=count(value.total);if(items.length&&query.offset+items.length>total)invalid();
  return {datasetStatus:dataset(value.datasetStatus),market:query.market,status:query.status,items,total,offset:query.offset,limit:query.limit};
 }
 if(query.view==="detail"){
  keys(value,["datasetStatus","creator","aliases","latestProfileObservedAt","fields","leadGmv","latestObservation"]);
  const person=value.creator===null?null:creator(value.creator,query.market);if(person&&person.creatorId!==query.creatorId)invalid();
  const aliases=list(value.aliases).map(rawAlias=>{const row=object(rawAlias);keys(row,["handle","firstObservedAt","lastObservedAt","isCurrent"]);return {handle:text(row.handle,64),firstObservedAt:text(row.firstObservedAt,80),lastObservedAt:text(row.lastObservedAt,80),isCurrent:flag(row.isCurrent)};});
  const fields=list(value.fields,22).map(profileField);if(new Set(fields.map(row=>row.name)).size!==fields.length||person&&fields.length!==22)invalid();
  const leadGmv=list(value.leadGmv,8).map(rawLead=>{const row=object(rawLead);keys(row,["pid","gmv","currency","windowStart","windowEnd","units"]);return {pid:match(row.pid,/^\d{19}$/,19),gmv:match(row.gmv,/^\d+(?:\.\d+)?$/,205),currency:match(row.currency,/^[A-Z]{3}$/,3),windowStart:match(row.windowStart,/^\d{4}-\d{2}-\d{2}$/,10),windowEnd:match(row.windowEnd,/^\d{4}-\d{2}-\d{2}$/,10),units:count(row.units)};});
  let latestObservation:CreatorIdentityDetail["latestObservation"]=null;
  if(value.latestObservation!==null){const row=object(value.latestObservation);keys(row,["kind","outcome","observedAt"]);if(row.kind!=="profile"&&row.kind!=="failure"||!["observed","unknown","timeout","not_found","blocked","error"].includes(String(row.outcome)))invalid();latestObservation={kind:row.kind as "profile"|"failure",outcome:row.outcome as NonNullable<CreatorIdentityDetail["latestObservation"]>["outcome"],observedAt:text(row.observedAt,80)};}
  return {datasetStatus:dataset(value.datasetStatus),creator:person,aliases,latestProfileObservedAt:maybeText(value.latestProfileObservedAt),fields,leadGmv,latestObservation};
 }
 keys(value,["datasetStatus","market","queryKind","resolutionRelation","historicalCrossSourceIdentityProven","creator","candidates","candidateCount","candidatesTruncated","pendingLeadCount","resolvedLeadCount","status"]);
 const queryKind=query.oecId!==undefined?"oec":"external_source",relation=queryKind==="oec"?"canonical_oec":"current_discovery_only";
 if(value.market!==query.market||value.queryKind!==queryKind||value.resolutionRelation!==relation||value.historicalCrossSourceIdentityProven!==false||!["verified","pending","not_found","ambiguous","not_imported"].includes(String(value.status)))invalid();
 const candidates=list(value.candidates,50).map(row=>creator(row,query.market)),candidateCount=count(value.candidateCount),truncated=flag(value.candidatesTruncated),person=value.creator===null?null:creator(value.creator,query.market);
 if(candidates.length!==Math.min(candidateCount,50)||truncated!==(candidateCount>50)||value.status==="verified"&&(!person||candidateCount!==1)||value.status!=="verified"&&person!==null||value.status==="ambiguous"&&candidateCount<2)invalid();
 if(person&&person.creatorId!==candidates[0]?.creatorId||value.status!=="verified"&&value.status!=="ambiguous"&&candidateCount!==0)invalid();
 if(query.oecId!==undefined&&candidates.some(row=>row.oecId!==query.oecId))invalid();
 return {datasetStatus:dataset(value.datasetStatus),market:query.market,queryKind,resolutionRelation:relation,historicalCrossSourceIdentityProven:false,creator:person,candidates,candidateCount,candidatesTruncated:truncated,pendingLeadCount:count(value.pendingLeadCount),resolvedLeadCount:count(value.resolvedLeadCount),status:value.status as IdentitySourceResolution["status"]};
}
