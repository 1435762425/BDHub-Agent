import {createHash} from "node:crypto";
import {existsSync,statSync} from "node:fs";
import {DatabaseSync} from "node:sqlite";
import type {CreatorInput,FactSource,MatchCreator,ProfileSignals} from "../../features/matching/contracts.ts";
import type {CategoryFact} from "../../features/matching/category-facts.ts";
import type {MatchingProfileOrigin} from "../../features/matching/profile-sync-contracts.ts";
import {sanitizeProfileField} from "../creator-identities/store.ts";
import {profileObservationOrder} from "../creator-identities/observation-order.ts";
import {LABELS} from "./italy-profile-signals.ts";
import {ALIGNMENT} from "./italy-profile-import.ts";

type ObjectValue=Record<string,unknown>;
export interface RegistryProfileObservation {
  rowid:number;eventId:string;evidenceRef:string;fingerprint:string;
  observedAt:number;observedAtIso:string;observedUs:number;fields:ObjectValue;
}
export interface RegistryProfileRecord {
  registryCreatorId:string;market:"it";oecId:string;currentHandle:string|null;
  observation:RegistryProfileObservation|null;historicalCategory:RegistryProfileObservation|null;
}
export interface RegistryProfileChanges {
  status:"ready"|"not_imported";sourceKey:string|null;cursor:number;sourceHead:number;
  events:number;registryCreators:number;records:RegistryProfileRecord[];
}
export interface RegistryCheckpoint {sourceKey:string|null;cursor:number;revision:number;}
export interface RegistryProfileProjection {
  creator:CreatorInput|null;
  mapping:{registryCreatorId:string;market:"it";oecId:string;matchingCreatorId:string}|null;
  gaps:{categoryUnavailable:number;categoryHistorical:number;metricsPartial:number;periodUnknown:number};skipped:number;
}
export class IdentityProfileProjectionError extends Error {
  readonly code:string;readonly status:number;
  constructor(code:string,status=409){super(code);this.name="IdentityProfileProjectionError";this.code=code;this.status=status;}
}
const invalid=()=>new IdentityProfileProjectionError("registry_profile_invalid",503);
const object=(value:unknown):ObjectValue|null=>value!==null&&typeof value==="object"&&!Array.isArray(value)?value as ObjectValue:null;
const digest=(value:unknown)=>createHash("sha256").update(JSON.stringify(value)).digest("hex");
const known=(status:string)=>status==="value"||status==="zero";
function safeCount(value:unknown):number {if(typeof value!=="number"||!Number.isSafeInteger(value)||value<0)throw invalid();return value;}
function id(value:unknown):string {if(typeof value!=="string"||!/^[0-9]{1,64}$/.test(value))throw invalid();return value;}
function label(value:unknown,max=300):string {if(typeof value!=="string"||!value.trim()||value.length>max||/[\u0000-\u001f\u007f]/.test(value))throw invalid();return value;}
function observedTimestamp(value:unknown):{iso:string;ms:number} {
  if(typeof value!=="string"||!/(Z|[+-]\d{2}:\d{2})$/.test(value))throw invalid();
  const ms=Date.parse(value);if(!Number.isSafeInteger(ms)||ms<0)throw invalid();return {iso:value,ms};
}
function observation(row:Record<string,unknown>,oecId:string):RegistryProfileObservation {
  const payload=object(JSON.parse(String(row.payload_json))),fields=object(payload?.fields),identity=object(payload?.identity);
  if(!payload||!fields||!identity||identity.oecId!==oecId||identity.market!==null&&identity.market!=="it")throw invalid();
  const oecField=sanitizeProfileField("creator_oecuid",fields.creator_oecuid,null);
  if(!known(oecField.status)||oecField.value!==oecId)throw invalid();
  const region=sanitizeProfileField("selection_region",fields.selection_region,null);
  if(known(region.status)&&String(region.value).toLowerCase()!=="it")throw invalid();
  const at=observedTimestamp(row.observed_at);
  return {rowid:safeCount(row.event_rowid),eventId:label(row.event_id),evidenceRef:label(row.evidence_ref),fingerprint:label(row.fingerprint),
    observedAt:at.ms,observedAtIso:at.iso,observedUs:safeCount(row.observed_us),fields};
}
const safePayload="CASE WHEN json_valid(e.payload_json) THEN e.payload_json ELSE '{}' END";

/** One bounded rowid window, with each affected creator projected from the same read snapshot.
 * A late observation advances the cursor but cannot roll back the newest Profile.
 */
export function readRegistryProfileChanges(path:string,checkpoint:RegistryCheckpoint,limitEvents=1000):RegistryProfileChanges {
  if(!checkpoint||!Number.isSafeInteger(checkpoint.cursor)||checkpoint.cursor<0||!Number.isSafeInteger(checkpoint.revision)||checkpoint.revision<0||
      checkpoint.sourceKey!==null&&typeof checkpoint.sourceKey!=="string"||checkpoint.sourceKey===null&&checkpoint.cursor!==0||
      !Number.isSafeInteger(limitEvents)||limitEvents<1||limitEvents>5000)throw new IdentityProfileProjectionError("invalid_registry_checkpoint",400);
  if(!existsSync(path))return {status:"not_imported",sourceKey:checkpoint.sourceKey,cursor:checkpoint.cursor,sourceHead:checkpoint.cursor,events:0,registryCreators:0,records:[]};
  const before=statSync(path,{bigint:true});let db:DatabaseSync|null=null;
  try {
    db=new DatabaseSync(path,{readOnly:true});db.exec("PRAGMA query_only=ON;BEGIN");
    const version=db.prepare("SELECT version FROM identity_store_meta").all();
    if(version.length!==1||version[0].version!==1)throw new IdentityProfileProjectionError("registry_schema_mismatch",503);
    for(const [table,columns] of Object.entries({creator_identity:["creator_id","market","oec_id","current_handle"],identity_observation:["event_id","creator_id","market","oec_id","kind","observed_us","observed_at","evidence_ref","fingerprint","payload_json"]})) {
      const actual=new Set(db.prepare(`PRAGMA table_info(${table})`).all().map(row=>row.name));
      if(columns.some(column=>!actual.has(column)))throw new IdentityProfileProjectionError("registry_schema_mismatch",503);
    }
    const head=safeCount(db.prepare("SELECT COALESCE(MAX(rowid),0) n FROM identity_observation").get()!.n);
    const first=db.prepare("SELECT event_id FROM identity_observation ORDER BY rowid LIMIT 1").get();
    const base=`registry:${digest({dev:String(before.dev),ino:String(before.ino),version:1})}`;
    const sourceKey=`${base}:${first?digest(String(first.event_id)):"empty"}`;
    if(checkpoint.sourceKey!==null&&checkpoint.sourceKey!==sourceKey&&!(checkpoint.cursor===0&&checkpoint.sourceKey===`${base}:empty`))throw new IdentityProfileProjectionError("registry_source_changed");
    if(checkpoint.cursor>head)throw new IdentityProfileProjectionError("registry_cursor_rewound");
    const eventRows=db.prepare("SELECT rowid,market,oec_id FROM identity_observation WHERE rowid>? ORDER BY rowid LIMIT ?").all(checkpoint.cursor,limitEvents);
    const cursor=eventRows.length?safeCount(eventRows[eventRows.length-1].rowid):checkpoint.cursor;
    const affected=[...new Set(eventRows.filter(row=>row.market==="it").map(row=>id(row.oec_id)))];
    const records:RegistryProfileRecord[]=[];
    const latest=db.prepare(`SELECT e.rowid event_rowid,e.* FROM identity_observation e WHERE e.market='it' AND e.oec_id=? AND e.kind='profile'
      AND json_type(${safePayload},'$.fields')='object' ORDER BY ${profileObservationOrder()} LIMIT 1`);
    const historical=db.prepare(`SELECT e.rowid event_rowid,e.* FROM identity_observation e WHERE e.market='it' AND e.oec_id=? AND e.kind='profile'
      AND json_type(${safePayload},'$.fields')='object' AND json_extract(${safePayload},'$.fields.industry_groups.status') IN ('value','zero')
      AND EXISTS(SELECT 1 FROM json_each(${safePayload},'$.fields.industry_groups.value') g WHERE g.type='object'
        AND json_type(CASE WHEN g.type='object' THEN g.value ELSE '{}' END,'$.label')='text'
        AND length(trim(json_extract(CASE WHEN g.type='object' THEN g.value ELSE '{}' END,'$.label')))>0
        AND COALESCE(json_extract(CASE WHEN g.type='object' THEN g.value ELSE '{}' END,'$.id'),'')!='-1')
      ORDER BY ${profileObservationOrder()} LIMIT 1`);
    for(const oecId of affected){
      const creator=db.prepare("SELECT creator_id,oec_id,current_handle FROM creator_identity WHERE market='it' AND oec_id=?").get(oecId);
      if(!creator)continue;const handle=creator.current_handle;
      if(handle!==null&&(typeof handle!=="string"||!/^[a-z0-9._]{1,64}$/.test(handle)))throw invalid();
      const row=latest.get(oecId),old=historical.get(oecId);
      records.push({registryCreatorId:label(creator.creator_id,100),market:"it",oecId:id(creator.oec_id),currentHandle:handle as string|null,
        observation:row?observation(row,oecId):null,historicalCategory:old?observation(old,oecId):null});
    }
    const creators=safeCount(db.prepare("SELECT COUNT(*) n FROM creator_identity WHERE market='it'").get()!.n);
    const after=statSync(path,{bigint:true});
    if(after.dev!==before.dev||after.ino!==before.ino)throw new IdentityProfileProjectionError("registry_source_changed");
    return {status:"ready",sourceKey,cursor,sourceHead:head,events:eventRows.length,registryCreators:creators,records};
  } catch(error) {
    if(error instanceof IdentityProfileProjectionError)throw error;
    throw new IdentityProfileProjectionError("registry_schema_mismatch",503);
  } finally {db?.close();}
}

function source(observation:RegistryProfileObservation):FactSource {
  return {ref:`identity-registry:${observation.eventId}`,observedAt:observation.observedAt,windowStart:null,windowEnd:null};
}
function categories(observation:RegistryProfileObservation):{labels:string[];mapped:string[];unknown:string[]} {
  const field=sanitizeProfileField("industry_groups",observation.fields.industry_groups,observation.observedAtIso);
  const labels=[...new Set((known(field.status)&&Array.isArray(field.value)?field.value:[]).flatMap(group=>{
    const item=object(group);return item&&item.id!=="-1"&&typeof item.label==="string"&&item.label.trim()?[item.label]:[];
  }))];
  return {labels,mapped:[...new Set(labels.flatMap(label=>Object.hasOwn(LABELS,label)?[LABELS[label]]:[]))].sort(),unknown:labels.filter(label=>!Object.hasOwn(LABELS,label))};
}
function categoryProjection(record:RegistryProfileRecord,existing:MatchCreator|null):{categories:string[];fact:CategoryFact;mode:MatchingProfileOrigin["categoryMode"]} {
  const latest=record.observation!;
  if(existing?.categoryFact?.status==="conflict")return {categories:[...existing.categories],fact:structuredClone(existing.categoryFact),mode:"conflict_preserved"};
  const state=sanitizeProfileField("industry_groups",latest.fields.industry_groups,latest.observedAtIso).status;
  let selected=latest,historical=false;
  if(state==="absent"||state==="no_value") {
    if(record.historicalCategory&&record.historicalCategory.observedUs<=latest.observedUs&&categories(record.historicalCategory).labels.length){selected=record.historicalCategory;historical=true;}
    else if(existing?.categories.length&&existing.categoryFact&&existing.categoryFact.source.observedAt<=latest.observedAt) {
      return {categories:[...existing.categories],fact:{...structuredClone(existing.categoryFact),status:"historical"},mode:"historical_fallback"};
    }
  }
  const projected=categories(selected);
  const unavailable=state==="unauthorized"||state==="error";
  const mapped=unavailable?[]:projected.mapped,labels=unavailable?[]:projected.labels;
  return {categories:mapped,mode:mapped.length?(historical?"historical_fallback":"observed"):"unavailable",
    fact:{status:mapped.length?(historical?"historical":"observed"):"missing",namespace:ALIGNMENT,sourceLabels:labels,source:source(selected),
      transformVersion:"identity-profile-labels-v1",timeBasis:"field_observation",
      note:unavailable?`当前类目${state==="unauthorized"?"未获授权":"返回错误"}，不回填历史分类。`:
        `${historical?"当前未提供类目，沿用同 OEC 历史具名类目及原观测时间。":"采用当前画像具名类目。"}${projected.unknown.length?`未映射标签仅保留来源：${projected.unknown.join("、")}。`:""}类目权重不解释为成交份额。`}};
}

/** Latest metrics are one bundle. Category history is separately dated and never supplies missing metrics. */
export function projectRegistryCreator(record:RegistryProfileRecord,existing:MatchCreator|null):RegistryProfileProjection {
  if(record.market!=="it"||id(record.oecId)!==record.oecId||!record.registryCreatorId||existing&&(existing.market!=="it"||existing.oecId!==record.oecId))throw invalid();
  const gaps={categoryUnavailable:0,categoryHistorical:0,metricsPartial:0,periodUnknown:0};
  if(!record.observation)return {creator:null,mapping:null,gaps:{categoryUnavailable:1,categoryHistorical:0,metricsPartial:1,periodUnknown:1},skipped:1};
  for(const observation of [record.observation,record.historicalCategory])if(observation){
    const observedOec=sanitizeProfileField("creator_oecuid",observation.fields.creator_oecuid,observation.observedAtIso);
    const region=sanitizeProfileField("selection_region",observation.fields.selection_region,observation.observedAtIso);
    if(!known(observedOec.status)||observedOec.value!==record.oecId||known(region.status)&&String(region.value).toLowerCase()!=="it")throw invalid();
  }
  const latest=record.observation,read=(name:string)=>sanitizeProfileField(name,latest.fields[name],latest.observedAtIso);
  const followers=read("follower_cnt"),units=read("units_sold"),views=read("video_avg_view_cnt"),gmv=read("med_gmv_revenue"),end=read("sales_performance_end_time");
  const number=(field:ReturnType<typeof read>)=>known(field.status)&&typeof field.value==="number"?field.value:null;
  const money=known(gmv.status)?object(gmv.value):null;
  const amount=money?.rawSymbol==="€"&&typeof money.decimal==="string"&&/^(0|[1-9]\d*)(\.\d+)?$/.test(money.decimal)?money.decimal:null;
  const endpoint=number(end),endpointValid=endpoint!==null&&endpoint<=8_640_000_000_000;
  const signals:ProfileSignals={followers:number(followers),unitsSold:number(units),avgViews:number(views),gmvValue:amount,gmvCurrency:amount===null?null:"EUR",
    periodLabel:endpointValid?`来源统计截至 ${new Date(endpoint*1000).toISOString().slice(0,10)}；起始日未提供`:null,
    comparisonScope:endpointValid?`tiktok-profile:it:sales-end:${endpoint}`:`tiktok-profile:it:unknown-period:oec:${record.oecId}`,source:source(latest)};
  const category=categoryProjection(record,existing);
  gaps.categoryUnavailable=category.categories.length?0:1;
  gaps.categoryHistorical=category.mode==="historical_fallback"?1:0;
  gaps.metricsPartial=[signals.followers,signals.unitsSold,signals.avgViews,signals.gmvValue].some(value=>value===null)?1:0;
  gaps.periodUnknown=endpointValid?0:1;
  const profileOrigin:MatchingProfileOrigin={kind:"identity_registry",creatorId:record.registryCreatorId,observationRef:source(latest).ref,observedAt:latest.observedAt,
    categoryMode:category.mode,metricStates:{followers:followers.status,unitsSold:units.status,avgViews:views.status,
      gmvValue:known(gmv.status)&&amount===null?"error":gmv.status} as MatchingProfileOrigin["metricStates"]};
  const defaults:CreatorInput={id:`it-profile-oec-${record.oecId}`,market:"it",oecId:record.oecId,name:record.currentHandle?`@${record.currentHandle}`:`OEC ${record.oecId}`,avatar:"",categories:[],formats:[],bio:"",
    priceMinMinor:null,priceMaxMinor:null,currency:"EUR",control:"unknown",marketingStopped:null,source:source(latest)};
  const {semanticRevision:ignoredSemantic,relationRevision:ignoredRelation,...base}=existing??{...defaults,semanticRevision:0,relationRevision:0};
  void ignoredSemantic;void ignoredRelation;
  const creator:CreatorInput&{profileOrigin:MatchingProfileOrigin}={...base,name:record.currentHandle?`@${record.currentHandle}`:base.name,categories:category.categories,categoryFact:category.fact,profileSignals:signals,profileOrigin};
  return {creator,mapping:{registryCreatorId:record.registryCreatorId,market:"it",oecId:record.oecId,matchingCreatorId:creator.id},gaps,skipped:0};
}
