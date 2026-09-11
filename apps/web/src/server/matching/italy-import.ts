import {createHash} from "node:crypto";
import type {ContentFormat, CreatorInput, MatchingBatch, MatchingDataset, ProductEvidence, ProductInput} from "../../features/matching/contracts.ts";

type Json = Record<string,unknown>;
export interface ItalySourceFile {ref:string;path:string;sha256:string;data:unknown;}
export interface ItalySources {pilot:ItalySourceFile;request:ItalySourceFile;result:ItalySourceFile;}
export interface ItalyImportPlan {
  batch:MatchingBatch;
  dataset:MatchingDataset;
  report:{
    schema:"bdhub.italy-offline-import.v1";datasetId:string;importedAt:number;
    sources:{ref:string;path:string;sha256:string}[];
    counts:{sourceProducts:number;sourceRows:number;products:number;creators:number;evidence:number;offers:0;rejected:number;historicalCommercialObservations:number};
    window:{startDate:string;endDate:string;timezone:"unspecified_by_source"};
    excludedFields:string[];missingFacts:string[];
    rejected:{kind:string;index:number;reason:string}[];
    historicalCommercialObservations:Json[];
  };
}
function object(value:unknown,label:string):Json {if(!value||typeof value!=="object"||Array.isArray(value))throw new Error(`${label}: expected object`);return value as Json;}
function rows(value:unknown,label:string):Json[]{if(!Array.isArray(value))throw new Error(`${label}: expected array`);return value.map((v,i)=>object(v,`${label}[${i}]`));}
function text(value:unknown,label:string,max=300):string {if(typeof value!=="string"||!value.trim()||value.trim().length>max)throw new Error(`${label}: invalid text`);return value.trim();}
function identity(value:unknown,label:string):string {const s=text(value,label,40);if(!/^\d+$/.test(s))throw new Error(`${label}: require exact numeric string`);return s;}
function timestamp(value:unknown,label:string):number {const n=Date.parse(text(value,label,50));if(!Number.isSafeInteger(n)||n<0)throw new Error(`${label}: invalid timestamp`);return n;}
function day(value:unknown,label:string):string {const d=text(value,label,10);if(!/^\d{4}-\d{2}-\d{2}$/.test(d)||new Date(`${d}T00:00:00Z`).toISOString().slice(0,10)!==d)throw new Error(`${label}: invalid date`);return d;}
function nonnegativeInteger(value:unknown,label:string):number {if(typeof value!=="number"||!Number.isSafeInteger(value)||value<0)throw new Error(`${label}: invalid integer`);return value;}
/** No float rounding, compact-unit parsing or inferred currency. */
export function decimalToHundredths(value:unknown):number {
  if(typeof value!=="string"||!/^\d+(?:\.\d{1,2})?$/.test(value))throw new Error("amount: require unambiguous decimal string");
  const [whole,fraction=""]=value.split(".");const n=BigInt(whole)*BigInt(100)+BigInt(fraction.padEnd(2,"0"));
  if(n>BigInt(Number.MAX_SAFE_INTEGER))throw new Error("amount: exceeds safe integer");return Number(n);
}
function rate(value:unknown):number|null {if(value===null||value===undefined)return null;const n=decimalToHundredths(value);if(n>10000)throw new Error("commission: outside percent range");return n;}

/** Pure adapter: explicit allowlist; no legacy services, credentials, contacts, model or network. */
export function planItalyImport(sources:ItalySources, importedAt:number):ItalyImportPlan {
  nonnegativeInteger(importedAt,"importedAt");
  const pilot=object(sources.pilot.data,"pilot"),request=object(sources.request.data,"request"),result=object(sources.result.data,"result"),query=object(request.query,"query");
  if(pilot.market!=="it"||query.market!=="it"||query.mode!=="pid"||result.state!=="completed")throw new Error("source_scope: require completed Italy exact PID research");
  const runId=text(request.run_id,"run_id",100);
  if(pilot.lead_run_id!==runId)throw new Error("source_scope: pilot and research run differ");
  const startDate=day(query.start_date,"start_date"),endDate=day(query.end_date,"end_date");
  if(startDate>endDate)throw new Error("source_window: reversed dates");
  const window=object(pilot.window,"pilot.window");if(window.start!==startDate||window.end!==endDate)throw new Error("source_window: pilot and research window differ");
  if(!Array.isArray(query.targets))throw new Error("query.targets: expected array");
  const targets=new Set(query.targets.map(v=>identity(v,"target"))),productRows=rows(pilot.rows,"pilot.rows"),leadRows=rows(result.rows,"result.rows");
  const items=rows(result.items,"result.items");
  if(targets.size!==query.targets.length||items.length!==targets.size||new Set(items.map(i=>i.target)).size!==targets.size||items.some(i=>i.status!=="success"||!targets.has(String(i.target))))throw new Error("source_scope: incomplete or duplicate PID result");
  const observedProductAt=timestamp(pilot.observed_at,"pilot.observed_at"),observedLeadAt=timestamp(result.finished_at,"result.finished_at");
  // Calendar dates are normalized for storage only; the source does not establish a timezone.
  const windowStart=Date.parse(`${startDate}T00:00:00.000Z`),windowEnd=Date.parse(`${endDate}T23:59:59.999Z`);
  const products:ProductInput[]=[],creators=new Map<string,CreatorInput>(),evidence:ProductEvidence[]=[],seenProducts=new Set<string>(),seenPairs=new Set<string>();
  const rejected:ItalyImportPlan["report"]["rejected"]=[],commercial:Json[]=[];
  for(const [index,row] of productRows.entries()) {
    try {
      const pid=identity(row.pid,"pid");if(!targets.has(pid))throw new Error("pid: outside requested targets");if(seenProducts.has(pid))throw new Error("pid: duplicate product");
      if(row.currency!=="EUR")throw new Error("currency: product price must explicitly identify EUR");
      const priceMinor=decimalToHundredths(row.price),label=text(row.label,"label");
      const description=leadRows.find(r=>r.pid===pid)?.product_title;
      const source={ref:`${sources.pilot.ref}:pid:${pid}`,observedAt:observedProductAt,windowStart:null,windowEnd:null};
      const observation={pid,observedAt:observedProductAt,publicDisplayedCommissionBps:rate(row.public_commission),platformDisplayedCommissionBps:rate(row.platform_commission),freeSampleBadge:typeof row.free_sample_badge==="boolean"?row.free_sample_badge:null,
        campaignId:null,stock:null,endsAt:null,creatorQuoteBps:null,agencyQuoteBps:null,executable:false,sourceRef:source.ref};
      products.push({id:`it-product-${pid}`,market:"it",pid,title:label,image:"",categories:[],formats:[],description:typeof description==="string"?text(description,"product_title",300):label,priceMinor,currency:"EUR",source});
      commercial.push(observation);seenProducts.add(pid);
    } catch(error) {rejected.push({kind:"product",index,reason:error instanceof Error?error.message:"invalid product"});}
  }
  for(const [index,row] of leadRows.entries()) {
    try {
      const pid=identity(row.pid,"pid"),externalId=identity(row.kalodata_creator_id,"kalodata_creator_id");
      if(!seenProducts.has(pid))throw new Error("pid: no accepted product");
      const units=nonnegativeInteger(row.sale,"sale");if(units===0)throw new Error("sale: no positive evidence");
      const handle=text(row.handle,"handle",100);if(!/^[A-Za-z0-9._]+$/.test(handle))throw new Error("handle: invalid exact handle");
      const pair=`${pid}:${externalId}`;if(seenPairs.has(pair))throw new Error("pair: duplicate observation");
      const format:ContentFormat|null=row.channel==="video"||row.channel==="live"?row.channel:null;
      const creatorId=`it-kalodata-${externalId}`,existing=creators.get(externalId);
      if(existing&&existing.name!==`@${handle}`)throw new Error("identity: same Kalodata ID has conflicting handles");
      if([...creators.values()].some(c=>c.name===`@${handle}`&&c.id!==creatorId))throw new Error("identity: same handle has conflicting Kalodata IDs");
      if(!existing)creators.set(externalId,{id:creatorId,market:"it",oecId:null,externalIdentity:{namespace:"kalodata",id:externalId},name:`@${handle}`,avatar:"",categories:[],formats:[],bio:"仅导入本次同品销量观察；达人画像、当前关系和平台身份尚未核验。",priceMinMinor:null,priceMaxMinor:null,currency:"EUR",control:"unknown",marketingStopped:null,source:{ref:`${sources.result.ref}:creator:${externalId}`,observedAt:observedLeadAt,windowStart,windowEnd,windowBasis:"calendar_date_unknown_timezone"}});
      evidence.push({id:`it-sale-${pid}-${externalId}`,creatorId,market:"it",pid,units,format,source:{ref:`${sources.result.ref}:pair:${pid}:${externalId}`,observedAt:observedLeadAt,windowStart,windowEnd,windowBasis:"calendar_date_unknown_timezone"}});seenPairs.add(pair);
    } catch(error) {rejected.push({kind:"evidence",index,reason:error instanceof Error?error.message:"invalid evidence"});}
  }
  if(!products.length||!creators.size||!evidence.length)throw new Error("source_empty: no usable Italy evidence");
  const manifest=Object.values(sources).map(({ref,path,sha256})=>({ref,path,sha256}));
  const digest=createHash("sha256").update(JSON.stringify(manifest.map(({ref,sha256})=>({ref,sha256})))).digest("hex");
  const id=`italy-pilot-${digest.slice(0,16)}`;
  const warnings=[
    `历史快照：商品采集于 ${new Date(observedProductAt).toISOString().slice(0,10)}，销量资料采集于 ${new Date(observedLeadAt).toISOString().slice(0,10)}；销量窗口 ${startDate}—${endDate}，不是视频发布日期。`,
    "每个 PID 为 GMV 前 50 中最多 10 位有出单的达人；这里只能验证已知证据回放，不能证明推荐质量或全量覆盖。",
    "达人 OEC、类目、价格带、当前关系与拒联状态缺失；历史条件未作当前核验。一发推荐质量尚不能评估。",
    "销售额原始显示为 Rp，不能按查询参数当成 EUR；本次不导入销售额。佣金展示快照单独保留，未生成可执行 Offer。",
    "此数据集仅供本机离线研究和上下文准备；不连接发送、建链、样品审批或模型。",
  ];
  return {batch:{products,creators:[...creators.values()],evidence,offers:[],demands:[]},dataset:{id,mode:"imported-offline",label:"意大利真实数据 · 离线",importedAt,sourceRefs:manifest.map(s=>`${s.ref} sha256:${s.sha256}`),warnings},report:{schema:"bdhub.italy-offline-import.v1",datasetId:id,importedAt,sources:manifest,
    counts:{sourceProducts:productRows.length,sourceRows:leadRows.length,products:products.length,creators:creators.size,evidence:evidence.length,offers:0,rejected:rejected.length,historicalCommercialObservations:commercial.length},
    window:{startDate,endDate,timezone:"unspecified_by_source"},excludedFields:["revenue","live_revenue","video_revenue","matched","avatar","nickname","contact","cookies","account credentials"],
    missingFacts:["verified OEC mapping","creator/product categories","creator price band","current relationship control","marketing stop state","campaign identity and validity","stock","creator and agency commission quote","sendable card"],rejected,historicalCommercialObservations:commercial}};
}
