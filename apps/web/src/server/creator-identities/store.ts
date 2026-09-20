import {existsSync} from "node:fs";
import {DatabaseSync} from "node:sqlite";
import {profileObservationOrder} from "./observation-order.ts";
import type {CreatorIdentityDetail,CreatorIdentityList,CreatorIdentityOverview,CreatorIdentitySummary,IdentityMarket,IdentityOverviewCounts,IdentityProfileField,IdentitySourceResolution,ProfileFieldStatus,ProfileFieldValue} from "../../features/creator-identities/contracts.ts";

export const PROFILE_FIELDS=["handle","creator_oecuid","selection_region","follower_cnt","med_gmv_revenue","video_gmv","live_gmv","units_sold","video_avg_view_cnt","industry_groups","content_groups","top_video_data","product_price_range","video_publish_cnt_30d","ec_video_publish_cnt_30d","live_streaming_cnt_30d","ec_live_streaming_cnt_30d","gpm","ec_live_gpm","ec_video_gpm","partnered_brand","sales_performance_end_time"] as const;
const STATES=new Set(["absent","no_value","unauthorized","error","zero","value"]);
const COUNTS=new Set(["follower_cnt","units_sold","video_avg_view_cnt","video_publish_cnt_30d","ec_video_publish_cnt_30d","live_streaming_cnt_30d","ec_live_streaming_cnt_30d","sales_performance_end_time"]);
const MONEY=new Set(["med_gmv_revenue","video_gmv","live_gmv","gpm","ec_live_gpm","ec_video_gpm"]);
const TABLES:Record<string,string[]>={identity_store_meta:["version"],creator_identity:["creator_id","market","oec_id","current_handle","handle_observed_at","handle_observed_us","handle_conflict","last_observed_at","last_observed_us","created_at"],identity_observation:["event_id","creator_id","market","oec_id","kind","handle","outcome","observed_at","observed_us","evidence_ref","fingerprint","payload_json"],handle_lead:["lead_id","market","handle","observed_at","evidence_ref","fingerprint","external_source","external_id","payload_json"],handle_lead_resolution:["lead_id","creator_id","observed_at","evidence_ref","fingerprint","exact_find_json"]};
const ID_COLUMNS="i.creator_id,i.market,i.oec_id,i.current_handle,i.handle_observed_at,i.handle_conflict,i.last_observed_at";
type Row=Record<string,unknown>;
type SafeObject=Record<string,ProfileFieldValue>;
export class CreatorIdentityError extends Error {
  readonly status:number;readonly code:string;
  constructor(status:number,code:string,message:string){super(message);this.name="CreatorIdentityError";this.status=status;this.code=code;}
}
const invalid=()=>new CreatorIdentityError(503,"identity_schema_mismatch","身份资料格式不兼容，请检查导入结果。");
const obj=(value:unknown):Record<string,unknown>|null=>value!==null&&typeof value==="object"&&!Array.isArray(value)?value as Row:null;
function text(value:unknown,max=512):string|null{return typeof value==="string"&&value.trim()&&value.length<=max&&!/[\u0000-\u001f\u007f]|https?:|javascript:|data:|www\./i.test(value)?value:null;}
function integer(value:unknown):number|null{return typeof value==="number"&&Number.isSafeInteger(value)&&value>=0?value:null;}
function decimal(value:unknown):string|null{return typeof value==="string"&&value.length<=205&&/^\d+(?:\.\d+)?$/.test(value)?value:null;}
function groups(value:unknown):SafeObject[]|null{
  if(!Array.isArray(value)||value.length>100)return null;
  const result:SafeObject[]=[];
  for(const raw of value){const item=obj(raw);if(!item)continue;const clean:SafeObject={};const id=text(item.id,128),label=text(item.label,256),weight=decimal(item.weight);if(id!==null)clean.id=id;if(label!==null)clean.label=label;if(weight!==null)clean.weight=weight;if(Object.keys(clean).length)result.push(clean);}
  return result;
}
function fieldValue(name:string,value:unknown):ProfileFieldValue|undefined{
  if(COUNTS.has(name))return integer(value)??undefined;
  if(name==="handle")return typeof value==="string"&&/^[a-z0-9._]{1,64}$/.test(value)?value:undefined;
  if(name==="creator_oecuid")return typeof value==="string"&&/^[0-9]{1,64}$/.test(value)?value:undefined;
  if(name==="selection_region")return typeof value==="string"&&/^[A-Za-z]{2}$/.test(value)?value:undefined;
  if(name==="product_price_range")return text(value)??undefined;
  if(name==="industry_groups"||name==="content_groups"){const result=groups(value);return result?.length?result:undefined;}
  const item=obj(value);if(!item)return undefined;
  if(MONEY.has(name)){
    const clean:SafeObject={},amount=decimal(item.decimal),min=decimal(item.minimum),max=decimal(item.maximum);
    if(amount!==null)clean.decimal=amount;else if(min!==null&&max!==null){clean.minimum=min;clean.maximum=max;}else return undefined;
    for(const key of ["rawSymbol","format","minimumFormat","maximumFormat"]){const label=text(item[key],128);if(label!==null)clean[key]=label;}return clean;
  }
  if(name==="top_video_data"){
    const count=integer(item.count);if(count===null)return undefined;
    const allowed=new Set(["name","video","item_id","like_cnt","play_cnt","comment_cnt","release_date"]);
    return {count,structureKeys:Array.isArray(item.structureKeys)?[...new Set(item.structureKeys.filter((key):key is string=>typeof key==="string"&&allowed.has(key)))]:[]};
  }
  if(name==="partnered_brand"){
    const brands=groups(item.brands);if(brands===null)return undefined;
    return {brands,...(typeof item.hasBrands==="boolean"?{hasBrands:item.hasBrands}:{})};
  }
  return undefined;
}
export function sanitizeProfileField(name:string,raw:unknown,observedAt:string|null):IdentityProfileField{
  const item=obj(raw),state=item?.status;
  if(!item||typeof state!=="string"||!STATES.has(state))return {name,status:raw===undefined?"absent":"error",observedAt};
  if(state!=="value"&&state!=="zero")return {name,status:state as ProfileFieldStatus,observedAt};
  const value=fieldValue(name,item.value);
  return value===undefined?{name,status:"error",observedAt}:{name,status:state,value,observedAt};
}
function identity(row:Row):CreatorIdentitySummary{
  return {kind:"creator",status:"verified",creatorId:String(row.creator_id),market:row.market as IdentityMarket,oecId:String(row.oec_id),currentHandle:row.current_handle===null?null:String(row.current_handle),currentHandleVerifiedAt:row.handle_observed_at===null?null:String(row.handle_observed_at),verifiedAt:String(row.last_observed_at),lastObservedAt:String(row.last_observed_at),handleConflict:row.handle_conflict===1};
}
function like(q:string){return `%${q.toLowerCase().replace(/[\\%_]/g,"\\$&")}%`;}
const safePayload="CASE WHEN json_valid(e.payload_json) THEN e.payload_json ELSE '{}' END";

export class CreatorIdentityReadStore {
  private db:DatabaseSync|null=null;
  private cycle:DatabaseSync|null=null;
  constructor(path:string,cyclePath?:string){
    if(!existsSync(path))return;
    try{
      this.db=new DatabaseSync(path,{readOnly:true});
      this.db.exec("PRAGMA query_only=ON");
      const tables=this.db.prepare("SELECT name FROM sqlite_master WHERE type='table'").all().map(row=>String(row.name));
      for(const [table,columns] of Object.entries(TABLES)){
        if(!tables.includes(table))throw invalid();
        const actual=this.db.prepare(`PRAGMA table_info(${table})`).all().map(row=>String(row.name));
        if(columns.some(column=>!actual.includes(column)))throw invalid();
      }
      const versions=this.db.prepare("SELECT version FROM identity_store_meta").all();
      if(versions.length!==1||versions[0].version!==1)throw invalid();
      // All projections in this short-lived request see the same WAL snapshot.
      this.db.exec("BEGIN");
      if(cyclePath&&existsSync(cyclePath))try{
        const cycle=new DatabaseSync(cyclePath,{readOnly:true});cycle.exec("PRAGMA query_only=ON");
        const cycleTables=new Set(cycle.prepare("SELECT name FROM sqlite_master WHERE type='table'").all().map(row=>String(row.name)));
        if(["plan","relationship","inbox_event"].every(table=>cycleTables.has(table))){cycle.exec("BEGIN");this.cycle=cycle;}else cycle.close();
      }catch{this.cycle?.close();this.cycle=null;}
    }catch{this.close();throw invalid();}
  }
  close(){this.cycle?.close();this.cycle=null;this.db?.close();this.db=null;}
  get datasetStatus(){return this.db?"ready" as const:"not_imported" as const;}
  private counts(market:IdentityMarket):IdentityOverviewCounts{
    if(!this.db)return {verifiedIdentities:0,pendingLeads:0,resolvedLeads:0,observations:0,lastVerifiedAt:null};
    const row=this.db.prepare(`SELECT
      (SELECT count(*) FROM creator_identity WHERE market=?) identities,
      (SELECT count(*) FROM handle_lead l LEFT JOIN handle_lead_resolution r USING(lead_id) WHERE l.market=? AND r.lead_id IS NULL) pending,
      (SELECT count(*) FROM handle_lead l JOIN handle_lead_resolution r USING(lead_id) WHERE l.market=?) resolved,
      (SELECT count(*) FROM identity_observation WHERE market=?) observations,
      (SELECT last_observed_at FROM creator_identity WHERE market=? ORDER BY last_observed_us DESC LIMIT 1) verified`).get(market,market,market,market,market)!;
    return {verifiedIdentities:Number(row.identities),pendingLeads:Number(row.pending),resolvedLeads:Number(row.resolved),observations:Number(row.observations),lastVerifiedAt:row.verified===null?null:String(row.verified)};
  }
  private interactionCounts(market:IdentityMarket){
    if(!this.cycle)return {repliedCreators:null,showcaseCreators:null};
    const row=this.cycle.prepare(`SELECT
      count(DISTINCT CASE WHEN e.kind='creatorReplies' THEN r.creator_id END) replied,
      count(DISTINCT CASE WHEN e.kind='showcaseNotifications' THEN r.creator_id END) showcase
      FROM inbox_event e JOIN relationship r ON r.plan_id=e.plan_id AND r.oec=e.oec
      JOIN plan p ON p.id=e.plan_id WHERE p.market=? AND e.historical=0`).get(market)!;
    return {repliedCreators:Number(row.replied||0),showcaseCreators:Number(row.showcase||0)};
  }
  overview(market:IdentityMarket="it"):CreatorIdentityOverview{return {datasetStatus:this.datasetStatus,market,...this.counts(market),...this.interactionCounts(market),markets:(["it","mx","br"] as const).map(key=>({market:key,...this.counts(key)}))};}
  list({market="it",status="verified",q="",offset=0,limit=20}:{market?:IdentityMarket;status?:"verified"|"pending";q?:string;offset?:number;limit?:number}={}):CreatorIdentityList{
    const base={datasetStatus:this.datasetStatus,market,status,offset,limit};if(!this.db)return {...base,items:[],total:0};
    const search=like(q);
    if(status==="pending"){
      const where="l.market=? AND r.lead_id IS NULL AND lower(l.handle) LIKE ? ESCAPE '\\'";
      const total=Number(this.db.prepare(`SELECT count(*) n FROM handle_lead l LEFT JOIN handle_lead_resolution r USING(lead_id) WHERE ${where}`).get(market,search)!.n);
      const rows=this.db.prepare(`SELECT l.lead_id,l.market,l.handle,l.observed_at FROM handle_lead l LEFT JOIN handle_lead_resolution r USING(lead_id) WHERE ${where} ORDER BY l.observed_at DESC,l.lead_id LIMIT ? OFFSET ?`).all(market,search,limit,offset);
      return {...base,total,items:rows.map(row=>({kind:"lead" as const,status:"pending" as const,leadId:String(row.lead_id),market:row.market as IdentityMarket,handle:String(row.handle),observedAt:String(row.observed_at),historicalCrossSourceIdentityProven:false as const}))};
    }
    // Resolve matching aliases once, avoiding one full event scan per identity.
    const where=q?"i.market=? AND (lower(COALESCE(i.current_handle,'')) LIKE ? ESCAPE '\\' OR i.oec_id LIKE ? ESCAPE '\\' OR i.creator_id IN (SELECT DISTINCT e.creator_id FROM identity_observation e WHERE e.market=? AND e.kind='profile' AND lower(e.handle) LIKE ? ESCAPE '\\'))":"i.market=?";
    const args=q?[market,search,search,market,search]:[market];
    const total=Number(this.db.prepare(`SELECT count(*) n FROM creator_identity i WHERE ${where}`).get(...args)!.n);
    const rows=this.db.prepare(`SELECT ${ID_COLUMNS} FROM creator_identity i WHERE ${where} ORDER BY i.last_observed_us DESC,i.creator_id LIMIT ? OFFSET ?`).all(...args,limit,offset);
    return {...base,total,items:rows.map(identity)};
  }
  detail(creatorId:string):CreatorIdentityDetail{
    const empty:CreatorIdentityDetail={datasetStatus:this.datasetStatus,creator:null,aliases:[],latestProfileObservedAt:null,fields:[],latestObservation:null};if(!this.db)return empty;
    const row=this.db.prepare(`SELECT ${ID_COLUMNS} FROM creator_identity i WHERE i.creator_id=? AND i.market IN ('it','mx','br')`).get(creatorId);
    if(!row)return empty;
    const scope=[String(row.market),String(row.oec_id)];
    const aliases=this.db.prepare("SELECT handle,min(observed_at) first_at,max(observed_at) last_at FROM identity_observation WHERE market=? AND oec_id=? AND kind='profile' AND handle IS NOT NULL GROUP BY handle ORDER BY max(observed_us) DESC,handle").all(...scope).map(alias=>({handle:String(alias.handle),firstObservedAt:String(alias.first_at),lastObservedAt:String(alias.last_at),isCurrent:alias.handle===row.current_handle}));
    const latest=this.db.prepare(`SELECT e.observed_at,json_extract(${safePayload},'$.fields') fields_json FROM identity_observation e WHERE e.market=? AND e.oec_id=? AND e.kind='profile' AND json_type(${safePayload},'$.fields')='object' ORDER BY ${profileObservationOrder()} LIMIT 1`).get(...scope);
    const fieldsObject=latest?obj(JSON.parse(String(latest.fields_json))):null;
    const observedAt=latest?String(latest.observed_at):null;
    const fields=PROFILE_FIELDS.map(name=>sanitizeProfileField(name,fieldsObject?.[name],observedAt));
    if(latest){
      const placeholders=PROFILE_FIELDS.map(()=>"?").join(",");
      const historical=this.db.prepare(`WITH values_by_field AS (
        SELECT j.key field_name,j.value field_json,e.observed_at,
          row_number() OVER(PARTITION BY j.key ORDER BY ${profileObservationOrder()}) position
        FROM identity_observation e,json_each(${safePayload},'$.fields') j
        WHERE e.market=? AND e.oec_id=? AND e.kind='profile' AND j.key IN (${placeholders})
          AND json_extract(CASE WHEN j.type='object' THEN j.value ELSE '{}' END,'$.status') IN ('value','zero')
      ) SELECT field_name,field_json,observed_at FROM values_by_field WHERE position=1`).all(...scope,...PROFILE_FIELDS);
      for(const old of historical){const current=fields.find(field=>field.name===old.field_name)!;
        if(current.status==="value"||current.status==="zero")continue;
        const projected=sanitizeProfileField(String(old.field_name),JSON.parse(String(old.field_json)),String(old.observed_at));
        if((projected.status==="value"||projected.status==="zero")&&projected.value!==undefined)current.lastAvailable={status:projected.status,value:projected.value,observedAt:String(old.observed_at)};
      }
    }
    const event=this.db.prepare("SELECT kind,outcome,observed_at FROM identity_observation WHERE market=? AND oec_id=? ORDER BY observed_us DESC,event_id DESC LIMIT 1").get(...scope);
    const kind=event?.kind==="profile"?"profile":"failure";
    const outcome=event?.outcome;
    const allowed=new Set(["observed","unknown","timeout","not_found","blocked","error"]);
    return {datasetStatus:this.datasetStatus,creator:identity(row),aliases,latestProfileObservedAt:observedAt,fields,
      latestObservation:event?{kind,outcome:allowed.has(String(outcome))?outcome as NonNullable<CreatorIdentityDetail["latestObservation"]>["outcome"]:"error",observedAt:String(event.observed_at)}:null};
  }
  source({market,oecId,externalId}:{market:IdentityMarket;oecId?:string;externalId?:string}):IdentitySourceResolution{
    const queryKind=oecId!==undefined?"oec":"external_source",base={datasetStatus:this.datasetStatus,market,queryKind,
      resolutionRelation:queryKind==="oec"?"canonical_oec":"current_discovery_only",historicalCrossSourceIdentityProven:false,
      creator:null,candidates:[],candidateCount:0,candidatesTruncated:false,pendingLeadCount:0,resolvedLeadCount:0} as Omit<IdentitySourceResolution,"status">;
    if(!this.db)return {...base,status:"not_imported"};
    if(oecId!==undefined){const row=this.db.prepare(`SELECT ${ID_COLUMNS} FROM creator_identity i WHERE i.market=? AND i.oec_id=?`).get(market,oecId);return {...base,status:row?"verified":"not_found",creator:row?identity(row):null,candidates:row?[identity(row)]:[],candidateCount:row?1:0};}
    if(externalId===undefined)throw new CreatorIdentityError(400,"invalid_request","请提供 OEC 或来源标识。");
    const scope="l.market=? AND l.external_source='probe_source_reference' AND l.external_id=?";
    const counts=this.db.prepare(`SELECT sum(CASE WHEN r.creator_id IS NULL THEN 1 ELSE 0 END) pending,sum(CASE WHEN r.creator_id IS NOT NULL THEN 1 ELSE 0 END) resolved FROM handle_lead l LEFT JOIN handle_lead_resolution r USING(lead_id) WHERE ${scope}`).get(market,externalId)!;
    const rows=this.db.prepare(`SELECT DISTINCT ${ID_COLUMNS} FROM handle_lead l JOIN handle_lead_resolution r USING(lead_id) JOIN creator_identity i ON i.creator_id=r.creator_id AND i.market=l.market WHERE ${scope} ORDER BY i.creator_id`).all(market,externalId);
    const candidates=rows.slice(0,50).map(identity),candidateCount=rows.length,pendingLeadCount=Number(counts.pending||0),resolvedLeadCount=Number(counts.resolved||0);
    return {...base,status:candidateCount>1?"ambiguous":candidateCount===1?"verified":pendingLeadCount>0?"pending":"not_found",creator:candidateCount===1?candidates[0]:null,candidates,candidateCount,candidatesTruncated:candidateCount>50,pendingLeadCount,resolvedLeadCount};
  }
}
