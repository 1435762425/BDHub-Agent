import {createHash} from "node:crypto";
import type {MatchingBatch,CreatorInput,ProfileSignals} from "../../features/matching/contracts.ts";

// Explicit label translation only. Modest Fashion stays distinct; it is not inferred to be a religious category.
export const LABELS:Record<string,string>={
  "Beauty & Personal Care":"美妆个护","Household Appliances":"家电","Phones & Electronics":"手机与数码","Health":"保健",
  "Sports & Outdoor":"运动与户外","Womenswear & Underwear":"女装与女士内衣","Home Supplies":"居家日用","Furniture":"家具",
  "Food & Beverages":"食品饮料","Home Improvement":"家装建材","Automotive & Motorcycle":"汽车与摩托车","Tools & Hardware":"五金工具",
  "Fashion Accessories":"时尚配件","Shoes":"鞋靴","Kitchenware":"厨房用品","Toys & Hobbies":"玩具和爱好","Luggage & Bags":"箱包",
  "Menswear & Underwear":"男装与男士内衣","Textiles & Soft Furnishings":"家纺布艺","Pet Supplies":"宠物用品","Baby & Maternity":"母婴用品",
  "Computers & Office Equipment":"电脑办公","Collectibles":"收藏品","Modest Fashion":"Modest Fashion","Kids' Fashion":"儿童时尚",
  "Jewelry Accessories & Derivatives":"珠宝与衍生品","Books, Magazines & Audio":"图书&杂志&音频",
};
type Obj=Record<string,unknown>;
function object(value:unknown,label:string):Obj{if(!value||typeof value!=="object"||Array.isArray(value))throw new Error(`${label}: expected object`);return value as Obj;}
function exactId(value:unknown,label:string):string{if(typeof value!=="string"||!/^\d+$/.test(value))throw new Error(`${label}: require exact numeric string`);return value;}
function decimal(value:unknown,label:string):string{if(typeof value!=="string"||!/^\d+(\.\d+)?$/.test(value)||!Number.isFinite(Number(value)))throw new Error(`${label}: require decimal string`);return value;}
function count(value:unknown,label:string):number|null{if(value===null)return null;if(typeof value!=="number"||!Number.isSafeInteger(value)||value<0)throw new Error(`${label}: invalid count`);return value;}
function canonical(value:unknown):string {if(Array.isArray(value))return `[${value.map(canonical).join(",")}]`;if(value&&typeof value==="object")return `{${Object.entries(value).sort(([a],[b])=>a<b?-1:a>b?1:0).map(([k,v])=>`${JSON.stringify(k)}:${canonical(v)}`).join(",")}}`;return JSON.stringify(value);}

/** Adds allowlisted raw profile evidence to the same OEC cohort, without overwriting historical sources or human labels. */
export function enrichItalyProfileSignals(batch:MatchingBatch,input:unknown,cohortSha256:string) {
  const doc=object(input,"signals"),provenance=object(doc.provenance,"provenance");
  if(doc.schemaVersion!==1||doc.market!=="it"||!Array.isArray(doc.records)||provenance.transactionReadOnly!==true||provenance.cohortSha256!==cohortSha256)throw new Error("Profile signal source does not match the read-only Italy cohort.");
  const recordsHash=createHash("sha256").update(canonical(doc.records)).digest("hex");
  if(recordsHash!==provenance.recordsSha256)throw new Error("Profile signal content fingerprint differs from its manifest.");
  const endings=object(provenance.salesPerformanceEndTimeUnixSeconds,"source ending");
  if(Object.keys(endings).length!==1)throw new Error("Multiple source endpoints require separate comparison scopes.");
  const endpoint=Object.keys(endings)[0],endpointSeconds=Number(endpoint);
  if(!Number.isSafeInteger(endpointSeconds)||endpointSeconds<0||endings[endpoint]!==doc.records.length)throw new Error("Invalid source reporting endpoint.");
  const date=new Date(endpointSeconds*1000).toISOString().slice(0,10);
  const existing=new Map((batch.creators??[]).map(c=>[c.oecId,c]));
  if(existing.size!==(batch.creators??[]).length||doc.records.length!==existing.size)throw new Error("Signals must cover each original OEC exactly once.");
  const seen=new Set<string>(),creators:CreatorInput[]=[],categoryCounts:Record<string,number>={};
  let namedEdges=0,unknownEdges=0,zeroViews=0;
  for(const entry of doc.records){
    const row=object(entry,"profile"),oec=exactId(row.oecId,"oecId"),original=existing.get(oec);
    if(!original||seen.has(oec)||original.market!=="it")throw new Error("Signal identity is absent, duplicated or in another market.");seen.add(oec);
    if(typeof row.capturedAt!=="string"||!/(Z|[+-]\d{2}:\d{2})$/.test(row.capturedAt)||Date.parse(row.capturedAt)!==original.source.observedAt)throw new Error("Signals must reference the original profile observation time.");
    if(typeof row.rawSnapshotId!=="string"||! /^[A-Za-z0-9._:-]{1,100}$/.test(row.rawSnapshotId))throw new Error("Snapshot reference malformed.");
    const money=object(row.gmv,"gmv");if(money.currency!=="EUR"||money.symbol!=="€"||money.period!==null)throw new Error("GMV must retain its verified EUR symbol and unknown duration.");
    if(!Array.isArray(row.categories)||row.categories.length>16)throw new Error("Profile categories are not bounded source entries.");
    const categories:string[]=[],sourceLabels:string[]=[],categoryIds=new Set<string>();let weightSum=0,unknownForCreator=0;
    for(const entry of row.categories){
      const category=object(entry,"source category"),rawId=category.categoryId;
      if(typeof rawId!=="string"||! /^(-1|\d+)$/.test(rawId)||categoryIds.has(rawId))throw new Error("Source category identity is invalid or duplicated.");categoryIds.add(rawId);
      const weight=Number(decimal(category.weight,"category weight"));if(weight>1)throw new Error("Source category weight exceeds one.");weightSum+=weight;
      if(rawId==="-1"||category.name===null||category.name===undefined){unknownEdges++;unknownForCreator++;continue;}
      if(typeof category.name!=="string"||!LABELS[category.name])throw new Error("Unmapped source category requires an explicit label mapping update.");
      sourceLabels.push(category.name);categories.push(LABELS[category.name]);namedEdges++;
    }
    if(Math.abs(weightSum-1)>0.000201)throw new Error("Source weights are incomplete; do not renormalize unknown categories.");
    const uniqueCategories=[...new Set(categories)];for(const category of uniqueCategories)categoryCounts[category]=(categoryCounts[category]??0)+1;
    const source={ref:`legacy:profile-snapshot:${row.rawSnapshotId}`,observedAt:original.source.observedAt,windowStart:null,windowEnd:null};
    const profileSignals:ProfileSignals={followers:count(row.followers,"followers"),unitsSold:count(row.unitsSold,"unitsSold"),avgViews:count(row.avgViews,"avgViews"),gmvValue:decimal(money.value,"GMV"),gmvCurrency:"EUR",periodLabel:`来源统计截至 ${date}；起始日未提供`,comparisonScope:`tiktok-profile:it:sales-end:${endpoint}`,source};
    if(profileSignals.avgViews===0)zeroViews++;
    creators.push({...original,categories:uniqueCategories,categoryFact:{status:uniqueCategories.length?"historical":"missing",namespace:original.categoryFact!.namespace,sourceLabels,source,transformVersion:"it-raw-profile-labels-v1",timeBasis:"field_observation",note:`采用原画像全部具名类目；${unknownForCreator}项无名称类目保留在来源，不补名或重算权重。原权重不解释为成交份额。`},profileSignals});
  }
  return {batch:{...batch,creators},report:{extractorVersion:"it-raw-profile-labels-v1",recordsHash,sourceTable:provenance.sourceTable,readOnlyVerified:true,profiles:creators.length,namedCategoryEdges:namedEdges,unknownCategoryEdges:unknownEdges,namedCategories:Object.keys(categoryCounts).length,categoryCounts,metricCoverage:{followers:creators.filter(c=>c.profileSignals?.followers!==null).length,unitsSold:creators.filter(c=>c.profileSignals?.unitsSold!==null).length,avgViews:creators.filter(c=>c.profileSignals?.avgViews!==null).length,gmv:creators.filter(c=>c.profileSignals?.gmvValue!==null).length},zeroAverageViews:zeroViews,gmvCurrency:"EUR",sourcePeriodEndUnix:endpointSeconds,sourcePeriodStart:null,policy:"Profile age ignored; price band and content format are reference only. Source category weights are not used as sales shares or ranking weights.",labelsWritten:0}};
}
