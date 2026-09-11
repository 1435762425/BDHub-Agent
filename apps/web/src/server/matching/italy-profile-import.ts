import {createHash} from "node:crypto";
import type {CreatorInput,MatchingBatch,MatchingDataset,ProductInput} from "../../features/matching/contracts.ts";
import type {CategoryFact} from "../../features/matching/category-facts.ts";
import {decimalToHundredths,type ItalySourceFile} from "./italy-import.ts";

type Obj=Record<string,unknown>;
const ALIGNMENT="it-history-top-label-v1";
export interface ItalyProfileSources {pilot:ItalySourceFile;products:ItalySourceFile;creators:ItalySourceFile;}
function obj(value:unknown,label:string):Obj {if(!value||typeof value!=="object"||Array.isArray(value))throw new Error(`${label}: expected object`);return value as Obj;}
function list(value:unknown,label:string):Obj[] {if(!Array.isArray(value))throw new Error(`${label}: expected array`);return value.map(v=>obj(v,label));}
function str(value:unknown,label:string,max=300):string {if(typeof value!=="string"||!value.trim()||value.length>max)throw new Error(`${label}: invalid text`);return value.trim();}
function id(value:unknown,label:string):string {const v=str(value,label,40);if(!/^\d+$/.test(v))throw new Error(`${label}: require exact numeric string`);return v;}
function time(value:unknown,label:string):number {const text=str(value,label,60);if(!/(Z|[+-]\d{2}:\d{2})$/.test(text))throw new Error(`${label}: timezone required`);const v=Date.parse(text);if(!Number.isSafeInteger(v))throw new Error(`${label}: invalid time`);return v;}
function source(ref:string,observedAt:number){return {ref,observedAt,windowStart:null,windowEnd:null};}

/** Uses three explicit local snapshots; never consults same-PID creator edges or old ranking outputs. */
export function planItalyProfileImport(sources:ItalyProfileSources,importedAt:number) {
  const pilot=obj(sources.pilot.data,"pilot"),catalog=obj(sources.products.data,"products"),snapshot=obj(sources.creators.data,"creators"),facts=obj(snapshot.facts,"creator facts"),query=obj(catalog.query,"product query");
  if(pilot.market!=="it"||catalog.market!=="it"||snapshot.market!=="it"||catalog.state!=="completed"||query.mode!=="pids"||query.market!=="it")throw new Error("Require completed Italy product research and Italy creator snapshot.");
  const readAt=time(facts.read_at,"facts.read_at"),productAt=time(pilot.observed_at,"product observed_at"),categoryAt=time(catalog.updated_at,"category batch completed"),categoryStartedAt=time(catalog.created_at,"category batch started");
  if(categoryStartedAt>categoryAt)throw new Error("Category collection interval is reversed.");
  const creatorRows=list(snapshot.creators,"creators"),catalogRows=list(catalog.rows,"catalog rows"),pilotRows=list(pilot.rows,"pilot rows");
  const creators:CreatorInput[]=[],products:ProductInput[]=[],ids=new Set<string>(),handles=new Set<string>(),counts:Record<string,number>={};
  let sourceExpired=0;
  for(const row of creatorRows) {
    const oec=id(row.oec_id,"oec_id"),handle=str(row.handle,"handle",100),category=str(row.category,"category",100),observedAt=time(row.category_observed_at,"category_observed_at"),capturedAt=time(row.captured_at,"captured_at");
    if(!/^[A-Za-z0-9._]+$/.test(handle)||ids.has(oec)||handles.has(handle))throw new Error("Creator identity is duplicated or malformed; no silent handle merge.");
    if(typeof row.category_fresh!=="boolean"||observedAt>readAt||capturedAt>readAt)throw new Error("Creator observation time or source freshness flag is invalid.");
    if(row.category_fresh===false)sourceExpired++;
    ids.add(oec);handles.add(handle);counts[category]=(counts[category]??0)+1;
    const categoryFact:CategoryFact={status:"historical",namespace:ALIGNMENT,sourceLabels:[category],source:source(`${sources.creators.ref}:category:${oec}`,observedAt),transformVersion:null,timeBasis:"field_observation",note:`旧快照只保留归一后的单一主类目，归一器版本未记录；${row.category_fresh?"来源当时标为新鲜，未核验今天状态":"来源当时已标过期"}。不是完整多类目画像。`};
    creators.push({id:`it-profile-oec-${oec}`,market:"it",oecId:oec,name:`@${handle}`,avatar:"",categories:[category],categoryFact,formats:[],bio:"历史 OEC 画像记录；当前身份、关系和内容表现未复核，不与其他来源按 handle 合并。",priceMinMinor:null,priceMaxMinor:null,currency:"EUR",control:"unknown",marketingStopped:null,source:source(`${sources.creators.ref}:creator:${oec}`,capturedAt)});
  }
  const seenPids=new Set<string>(),productCategoryCounts:{pid:string;title:string;status:CategoryFact["status"];sourceLabels:string[];categoryCandidates:number}[]=[];
  for(const row of pilotRows) {
    const pid=id(row.pid,"pid");if(seenPids.has(pid))throw new Error("Duplicate product PID.");seenPids.add(pid);
    const matches=catalogRows.filter(r=>r.pid===pid);if(matches.length!==1||matches[0].detail_checked!==true)throw new Error("Product category requires one exact-PID detail observation.");
    const detail=matches[0],label=str(row.label,"label"),title=str(detail.title,"title");
    if(!Array.isArray(detail.category_ids)||detail.category_ids.length>16)throw new Error("category_ids must contain the observed label path.");
    const labels=detail.category_ids.map(v=>str(v,"category label",100));
    // Explicit finding from this source audit; preserve the contradictory observation, do not replace it with a guessed category.
    const conflict=pid==="1729779362302171335"&&labels[0]==="家纺布艺"&&labels.includes("枕头和背垫");
    const status:CategoryFact["status"]=conflict?"conflict":labels[0]&&counts[labels[0]]?"historical":"missing";
    const categoryFact:CategoryFact={status,namespace:ALIGNMENT,sourceLabels:labels,source:source(`${sources.products.ref}:category:${pid}`,categoryAt),transformVersion:ALIGNMENT,timeBasis:"batch_completed",note:conflict?"口腔清新片标题与来源的枕头分类冲突；原观察保留，本轮停用分类并待核验。":status==="missing"?"来源标签尚不能与本次旧画像主类目对齐；不猜测分类。":"Kalodata 历史中文层级标签按顶级原文对齐旧画像；不是官方数字类目映射。时间为采集批次完成时间，非行级更新时间。"};
    if(row.currency!=="EUR")throw new Error("Product price must explicitly declare EUR; research Rp prices are excluded.");
    products.push({id:`it-profile-product-${pid}`,market:"it",pid,title:label,image:"",description:title,categories:status==="historical"?[labels[0]]:[],categoryFact,formats:[],priceMinor:decimalToHundredths(row.price),currency:"EUR",source:source(`${sources.pilot.ref}:pid:${pid}`,productAt)});
    productCategoryCounts.push({pid,title:label,status,sourceLabels:labels,categoryCandidates:status==="historical"?counts[labels[0]]:0});
  }
  if(!products.length||!creators.length)throw new Error("The first-source experiment must not be empty.");
  const manifest=Object.values(sources).map(({ref,path,sha256})=>({ref,path,sha256}));
  const fingerprint=createHash("sha256").update(JSON.stringify({version:ALIGNMENT,sources:manifest.map(({ref,sha256})=>({ref,sha256}))})).digest("hex");
  const dataset:MatchingDataset={id:`italy-profiles-${fingerprint.slice(0,16)}`,mode:"imported-offline",label:"意大利画像实验 · 一发",importedAt,sourceRefs:manifest.map(s=>`${s.ref} sha256:${s.sha256}`),warnings:[
    `历史画像：${creators.length} 条独立 OEC 记录；类目观测于 ${new Date(Math.min(...creators.map(c=>c.categoryFact!.source.observedAt))).toISOString().slice(0,10)}，${sourceExpired} 条在来源读取时已标过期。9月10日读取快照不等于刷新画像。`,
    "本实验不导入同品销量边，不按 handle 合并上一轮 Kalodata 达人，不用旧匹配排序。相同标签只说明值得进一步研究。",
    "商品与达人价格带/内容形式仍不完整；标签对齐是明确版本的离线实验，不是官方分类或可联系性验证。",
    "Freegrin 口腔清新片与来源枕头分类冲突，已停用该分类；其余商品沿用可追溯的历史标签。",
    "人工评审默认未评估；资料不足不算不适合，人工适配比例不是模型准确率或发送授权。",
  ]};
  const batch:MatchingBatch={products,creators,offers:[],evidence:[],demands:[]};
  return {batch,dataset,report:{schema:"bdhub.italy-profile-import.v1",importedAt,datasetId:dataset.id,alignmentVersion:ALIGNMENT,sources:manifest,counts:{products:products.length,profileRecords:creators.length,sourceProfileTotal:facts.total,sourceOmittedForMissingHandleOrCategory:facts.category_missing,sourceExpiredProfiles:sourceExpired,categories:Object.keys(counts).length,usableProductCategories:products.filter(p=>p.categories.length).length,conflictingProductCategories:products.filter(p=>p.categoryFact?.status==="conflict").length,evidence:0,offers:0,demands:0},productCategoryCounts,categoryDistribution:counts,categoryCollection:{startedAt:categoryStartedAt,completedAt:categoryAt,timeBasis:"batch_completed"},profileSnapshotReadAt:readAt,profileFieldObservedAt:{min:Math.min(...creators.map(c=>c.categoryFact!.source.observedAt)),max:Math.max(...creators.map(c=>c.categoryFact!.source.observedAt))},scope:"Historical top-label discovery experiment; no pairwise relevance ground truth, no first-source accuracy claim, no cross-source identity merge.",excludedFields:["same_pid_sales_edges","old_priority_score","old_shortlist","gmv","followers","contact","avatars","credentials"],qualityStatus:"awaiting_operator_assessment",modelCalls:0,realSends:0}};
}
