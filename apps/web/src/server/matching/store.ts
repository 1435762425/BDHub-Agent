import { DatabaseSync, type StatementSync, type SQLInputValue } from "node:sqlite";
import { createHash, randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { dirname } from "node:path";
import type { MatchingBatch, MatchingDataset, MatchMarket, FactSource, MatchProduct, MatchCreator, MatchOffer, ProductInput, CreatorInput, OfferInput, ProductEvidence, CreatorDemand, ImportResult, MatchPage, RecallQuery, MatchRun, MatchCandidate, CandidateSource, ReviewPacket, MatchingStats, AssessmentLabel, CandidateAssessment, AssessmentResponse, ProfileSignals } from "../../features/matching/contracts.ts";
import type {CategoryFact} from "../../features/matching/category-facts.ts";
import { makeMatchingFixture } from "./fixtures.ts";
import {analysisPolicy,analyzeProfile,orderProfileCandidates,profileRankScope} from "./profile-analysis.ts";

const VERSION="structured-recall-v4",ROUTE_LIMIT=200,FINAL_LIMIT=50,PACKET_LIMIT=6000;
const currencies={mx:"MXN",br:"BRL",it:"EUR"} as const;
type Row={data:string;semantic_hash?:string;commercial_hash?:string;relation_hash?:string};
type Route={source:CandidateSource;sql:string;args:SQLInputValue[]};
type StoredRun={run:MatchRun;dependencies:string[];expiresAt:number|null};

export class MatchingError extends Error {
  status:number;code:string;
  constructor(status:number,code:string,message:string){super(message);this.name="MatchingError";this.status=status;this.code=code;}
}
function bad(message:string):never{throw new MatchingError(400,"invalid_fact",message);}
function text(value:unknown,label:string,max=500):string {if(typeof value!=="string"||!value.trim()||value.length>max)bad(`${label} 必须为非空字符串且不超过 ${max} 字符。`);return value.trim();}
function optionalText(value:unknown,label:string,max=4000):string{if(typeof value!=="string"||value.length>max)bad(`${label} 必须为字符串且不超过 ${max} 字符。`);return value.trim();}
function integer(value:unknown,label:string,nullable=false,max=Number.MAX_SAFE_INTEGER):number|null{if(nullable&&value===null)return null;if(typeof value!=="number"||!Number.isSafeInteger(value)||value<0||value>max)bad(`${label} 必须是非负安全整数${nullable?"或明确的 null":""}。`);return value;}
function bool(value:unknown,label:string,nullable=false):boolean|null{if(nullable&&value===null)return null;if(typeof value!=="boolean")bad(`${label} 必须是布尔值${nullable?"或明确的 null":""}。`);return value;}
function choice<T extends string>(value:unknown,allowed:readonly T[],label:string):T{if(typeof value!=="string"||!allowed.includes(value as T))bad(`${label} 不在允许范围。`);return value as T;}
function strings(value:unknown,label:string,max=16):string[]{if(!Array.isArray(value)||value.length>max)bad(`${label} 必须为最多 ${max} 项的数组。`);return [...new Set(value.map(v=>text(v,label,100)))].sort();}
function fact(value:unknown):FactSource {
  if(!value||typeof value!=="object"||Array.isArray(value))bad("source 缺失。");
  const v=value as FactSource;
  const result:FactSource={ref:text(v.ref,"source.ref",300),observedAt:integer(v.observedAt,"source.observedAt")!,windowStart:integer(v.windowStart,"source.windowStart",true),windowEnd:integer(v.windowEnd,"source.windowEnd",true)};
  if(result.windowStart!==null&&result.windowEnd!==null&&result.windowStart>result.windowEnd)bad("证据窗口起点晚于终点。");
  if(v.windowBasis!==undefined) {
    result.windowBasis=choice(v.windowBasis,["calendar_date_unknown_timezone"] as const,"source.windowBasis");
    if(result.windowStart===null||result.windowEnd===null)bad("日历日期窗口必须保留起止日期；时区未知不得解释为精确 UTC 时刻。");
  }
  return result;
}
function identity(value:unknown,label:string):string{const v=text(value,label,100);if(!/^\d+$/.test(v))bad(`${label} 必须为保留精度的数字字符串。`);return v;}
function marketCurrency(market:MatchMarket,currency:unknown){if(currencies[market]!==currency)bad(`${market} 的币种必须是 ${currencies[market]}。`);return currencies[market];}
function stable(v:unknown):string{if(Array.isArray(v))return `[${v.map(stable).join(",")}]`;if(v&&typeof v==="object")return `{${Object.entries(v).sort(([a],[b])=>a.localeCompare(b)).map(([k,x])=>`${JSON.stringify(k)}:${stable(x)}`).join(",")}}`;return JSON.stringify(v);}
function hash(v:unknown):string{return createHash("sha256").update(stable(v)).digest("hex");}
function categoryFact(value:CategoryFact|undefined,categories:string[]):CategoryFact|undefined {
  if(value===undefined)return undefined;
  if(!value||typeof value!=="object"||Array.isArray(value))bad("分类来源必须为对象。");
  const result:CategoryFact={status:choice(value.status,["historical","conflict","missing"],"categoryFact.status"),namespace:text(value.namespace,"categoryFact.namespace",100),sourceLabels:strings(value.sourceLabels,"categoryFact.sourceLabels"),source:fact(value.source),transformVersion:value.transformVersion===null?null:text(value.transformVersion,"categoryFact.transformVersion",100),timeBasis:choice(value.timeBasis,["field_observation","batch_completed"],"categoryFact.timeBasis"),note:optionalText(value.note,"categoryFact.note",600)};
  // The sequence of raw category labels is a hierarchy, not a set to alphabetize.
  result.sourceLabels=[...new Set(value.sourceLabels.map(label=>text(label,"categoryFact.sourceLabels",100)))];
  if(result.status!=="historical"&&categories.length)bad("冲突或缺失分类不能写入可用类目索引。");
  return result;
}
function productInput(value:ProductInput):ProductInput{const v=value,m=choice(v.market,["mx","br","it"],"market");return {id:text(v.id,"product.id",100),market:m,pid:identity(v.pid,"pid"),title:text(v.title,"title",300),image:optionalText(v.image,"image",500),categories:strings(v.categories,"categories"),...(v.categoryFact!==undefined?{categoryFact:categoryFact(v.categoryFact,v.categories)}:{}),formats:strings(v.formats,"formats",2).map(f=>choice(f,["video","live"],"format")),description:optionalText(v.description,"description"),priceMinor:integer(v.priceMinor,"priceMinor",true),currency:marketCurrency(m,v.currency),source:fact(v.source)};}
function profileSignals(value:ProfileSignals,market:MatchMarket):ProfileSignals {
  if(!value||typeof value!=="object"||Array.isArray(value))bad("profileSignals 必须为带来源的对象。");
  const average=value.avgViews;
  if(average!==null&&(typeof average!=="number"||!Number.isFinite(average)||average<0||average>Number.MAX_SAFE_INTEGER))bad("profileSignals.avgViews 必须为非负有限数或 null。");
  const gmv=value.gmvValue===null?null:text(value.gmvValue,"profileSignals.gmvValue",100);
  if(gmv!==null&&!/^(0|[1-9]\d*)(\.\d+)?$/.test(gmv))bad("画像 GMV 必须保留非负十进制原值字符串。");
  return {followers:integer(value.followers,"profileSignals.followers",true),unitsSold:integer(value.unitsSold,"profileSignals.unitsSold",true),avgViews:average,gmvValue:gmv,gmvCurrency:value.gmvCurrency===null?null:marketCurrency(market,value.gmvCurrency),periodLabel:value.periodLabel===null?null:text(value.periodLabel,"profileSignals.periodLabel",200),comparisonScope:text(value.comparisonScope,"profileSignals.comparisonScope",200),source:fact(value.source)};
}
function creatorInput(value:CreatorInput):CreatorInput{
  const v=value,m=choice(v.market,["mx","br","it"],"market"),min=integer(v.priceMinMinor,"priceMinMinor",true),max=integer(v.priceMaxMinor,"priceMaxMinor",true);
  if(min!==null&&max!==null&&min>max)bad("达人价格带起点大于终点。");
  const oecId=v.oecId===null?null:identity(v.oecId,"oecId");
  let externalIdentity:CreatorInput["externalIdentity"];
  if(v.externalIdentity!==undefined){
    if(!v.externalIdentity||typeof v.externalIdentity!=="object"||Array.isArray(v.externalIdentity))bad("externalIdentity 必须包含来源命名空间与原始 ID。");
    externalIdentity={namespace:choice(v.externalIdentity.namespace,["kalodata"],"externalIdentity.namespace"),id:identity(v.externalIdentity.id,"externalIdentity.id")};
  }
  if(oecId===null&&!externalIdentity)bad("OEC 未知时必须提供独立的外部身份，不可用外部 ID 代填 OEC。");
  return {id:text(v.id,"creator.id",100),market:m,oecId,...(externalIdentity?{externalIdentity}:{}),name:text(v.name,"name",200),avatar:optionalText(v.avatar,"avatar",500),categories:strings(v.categories,"categories"),...(v.categoryFact!==undefined?{categoryFact:categoryFact(v.categoryFact,v.categories)}:{}),...(v.profileSignals!==undefined?{profileSignals:profileSignals(v.profileSignals,m)}:{}),formats:strings(v.formats,"formats",2).map(f=>choice(f,["video","live"],"format")),bio:optionalText(v.bio,"bio"),priceMinMinor:min,priceMaxMinor:max,currency:marketCurrency(m,v.currency),control:choice(v.control,["auto","human","paused","unknown"],"control"),marketingStopped:bool(v.marketingStopped,"marketingStopped",true),source:fact(v.source)};
}
function datasetInput(value:MatchingDataset):MatchingDataset{
  if(!value||typeof value!=="object"||Array.isArray(value))bad("dataset 必须是明确的数据集描述。");
  const list=(items:unknown,label:string,limit:number,max:number)=>{if(!Array.isArray(items)||items.length>limit)bad(`${label} 数量无效。`);return [...new Set(items.map(item=>text(item,label,max)))];};
  const dataset={id:text(value.id,"dataset.id",100),mode:choice(value.mode,["synthetic-local","imported-offline"],"dataset.mode"),label:text(value.label,"dataset.label",200),importedAt:integer(value.importedAt,"dataset.importedAt",true),sourceRefs:list(value.sourceRefs,"dataset.sourceRefs",100,300).sort(),warnings:list(value.warnings,"dataset.warnings",32,1000)};
  if(dataset.mode==="imported-offline"&&(dataset.importedAt===null||!dataset.sourceRefs.length))bad("离线导入必须保留导入时间与来源引用。");
  return dataset;
}
function offerInput(v:OfferInput):OfferInput{const result={id:text(v.id,"offer.id",100),productId:text(v.productId,"productId",100),campaignId:text(v.campaignId,"campaignId",100),accountRef:text(v.accountRef,"accountRef",100),publicCommissionBps:integer(v.publicCommissionBps,"publicCommissionBps",true,10000),totalCommissionBps:integer(v.totalCommissionBps,"totalCommissionBps",true,10000),creatorCommissionBps:integer(v.creatorCommissionBps,"creatorCommissionBps",true,10000),agencyCommissionBps:integer(v.agencyCommissionBps,"agencyCommissionBps",true,10000),stock:integer(v.stock,"stock",true),sampleAvailable:bool(v.sampleAvailable,"sampleAvailable",true),sampleQuota:integer(v.sampleQuota,"sampleQuota",true),startsAt:integer(v.startsAt,"startsAt")!,endsAt:integer(v.endsAt,"endsAt")!,cardStatus:choice(v.cardStatus,["verified","needs_preparation","unknown"],"cardStatus"),source:fact(v.source)};if(result.startsAt>=result.endsAt)bad("Offer 结束时间必须晚于开始时间。");if(result.creatorCommissionBps!==null&&result.agencyCommissionBps!==null&&result.creatorCommissionBps+result.agencyCommissionBps!==result.totalCommissionBps)bad("同一 Offer 的达人佣金与机构佣金之和必须等于总佣金。");return result;}
function evidenceInput(v:ProductEvidence):ProductEvidence{return {id:text(v.id,"evidence.id",100),creatorId:text(v.creatorId,"creatorId",100),market:choice(v.market,["mx","br","it"],"market"),pid:identity(v.pid,"pid"),units:integer(v.units,"units")!,format:v.format===null?null:choice(v.format,["video","live"] as const,"format"),source:fact(v.source)};}
function demandInput(v:CreatorDemand):CreatorDemand{return {id:text(v.id,"demand.id",100),creatorId:text(v.creatorId,"creatorId",100),productId:v.productId===null?null:text(v.productId,"productId",100),categories:strings(v.categories,"categories"),active:bool(v.active,"active")!,source:fact(v.source)};}
function productSemantic(p:ProductInput){return {title:p.title,categories:p.categories,categoryFact:p.categoryFact??null,formats:p.formats,description:p.description};}
function productCommercial(p:ProductInput){return {priceMinor:p.priceMinor,currency:p.currency};}
function creatorSemantic(c:CreatorInput){return {categories:c.categories,categoryFact:c.categoryFact??null,profileSignals:c.profileSignals??null,formats:c.formats,bio:c.bio};}
function creatorRelation(c:CreatorInput){return {control:c.control,marketingStopped:c.marketingStopped};}

export class MatchingStore {
  private db:DatabaseSync;private now:()=>number;private statements=new Map<string,StatementSync>();private dataset:MatchingDataset;
  constructor(dbPath:string,options:{now?:()=>number;seed?:boolean;dataset?:MatchingDataset}={}) {
    if(dbPath!==":memory:")mkdirSync(dirname(dbPath),{recursive:true});this.now=options.now??Date.now;this.db=new DatabaseSync(dbPath);
    try {
    this.db.exec(`PRAGMA busy_timeout=5000; PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;
      CREATE TABLE IF NOT EXISTS matching_meta(key TEXT PRIMARY KEY,value INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS matching_dataset(singleton INTEGER PRIMARY KEY CHECK(singleton=1),data TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS products(id TEXT PRIMARY KEY,market TEXT NOT NULL,pid TEXT NOT NULL,price_minor INTEGER,title TEXT NOT NULL,semantic_hash TEXT NOT NULL,commercial_hash TEXT NOT NULL,data TEXT NOT NULL,UNIQUE(market,pid));
      CREATE INDEX IF NOT EXISTS products_market_price ON products(market,price_minor,id);
      CREATE TABLE IF NOT EXISTS creators(id TEXT PRIMARY KEY,market TEXT NOT NULL,oec_id TEXT NOT NULL,price_min INTEGER,price_max INTEGER,name TEXT NOT NULL,semantic_hash TEXT NOT NULL,relation_hash TEXT NOT NULL,data TEXT NOT NULL,UNIQUE(market,oec_id));
      CREATE INDEX IF NOT EXISTS creators_market_price ON creators(market,price_min,price_max,id);
      CREATE TABLE IF NOT EXISTS product_categories(market TEXT NOT NULL,category TEXT NOT NULL,entity_id TEXT NOT NULL REFERENCES products(id),price_minor INTEGER,PRIMARY KEY(market,category,entity_id));
      CREATE INDEX IF NOT EXISTS product_category_price ON product_categories(market,category,price_minor,entity_id);
      CREATE INDEX IF NOT EXISTS product_category_entity ON product_categories(entity_id);
      CREATE TABLE IF NOT EXISTS creator_categories(market TEXT NOT NULL,category TEXT NOT NULL,entity_id TEXT NOT NULL REFERENCES creators(id),price_min INTEGER,price_max INTEGER,PRIMARY KEY(market,category,entity_id));
      CREATE INDEX IF NOT EXISTS creator_category_price ON creator_categories(market,category,price_min,price_max,entity_id);
      CREATE INDEX IF NOT EXISTS creator_category_entity ON creator_categories(entity_id);
      CREATE TABLE IF NOT EXISTS offers(id TEXT PRIMARY KEY,product_id TEXT NOT NULL REFERENCES products(id),campaign_id TEXT NOT NULL,account_ref TEXT NOT NULL,starts_at INTEGER NOT NULL,ends_at INTEGER NOT NULL,stock INTEGER,data TEXT NOT NULL,UNIQUE(product_id,campaign_id,account_ref));
      CREATE INDEX IF NOT EXISTS offers_product ON offers(product_id,ends_at,id);
      CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY,creator_id TEXT NOT NULL REFERENCES creators(id),market TEXT NOT NULL,pid TEXT NOT NULL,units INTEGER NOT NULL,data TEXT NOT NULL);
      CREATE INDEX IF NOT EXISTS evidence_exact ON evidence(market,pid,units DESC,creator_id);
      CREATE INDEX IF NOT EXISTS evidence_creator ON evidence(creator_id,market,units DESC,pid);
      CREATE TABLE IF NOT EXISTS demands(id TEXT PRIMARY KEY,creator_id TEXT NOT NULL REFERENCES creators(id),market TEXT NOT NULL,product_id TEXT REFERENCES products(id),active INTEGER NOT NULL,data TEXT NOT NULL);
      CREATE INDEX IF NOT EXISTS demands_product ON demands(market,product_id,active,creator_id);
      CREATE INDEX IF NOT EXISTS demands_creator ON demands(creator_id,active);
      CREATE TABLE IF NOT EXISTS demand_categories(market TEXT NOT NULL,category TEXT NOT NULL,demand_id TEXT NOT NULL REFERENCES demands(id),creator_id TEXT NOT NULL,active INTEGER NOT NULL,PRIMARY KEY(market,category,demand_id));
      CREATE INDEX IF NOT EXISTS demand_category_active ON demand_categories(market,category,active,creator_id);
      CREATE INDEX IF NOT EXISTS demand_category_id ON demand_categories(demand_id);
      CREATE TABLE IF NOT EXISTS partition_versions(key TEXT PRIMARY KEY,version INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS recall_runs(id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL UNIQUE,data TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS review_packets(id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL UNIQUE,data TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS matching_requests(id TEXT PRIMARY KEY,request_hash TEXT NOT NULL,data TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS candidate_assessments(
        dataset_id TEXT NOT NULL,fingerprint TEXT NOT NULL,creator_id TEXT NOT NULL,product_id TEXT NOT NULL,
        label TEXT CHECK(label IS NULL OR label IN ('suitable','unsuitable','insufficient')),note TEXT NOT NULL CHECK(length(note)<=1000),
        revision INTEGER NOT NULL CHECK(revision>0),reviewed_at INTEGER,
        PRIMARY KEY(dataset_id,fingerprint,creator_id,product_id));
      INSERT OR IGNORE INTO matching_meta VALUES('schema_version',1),('semantic_builds',0);
    `);
    this.migrateIdentitySchema();
    this.migrateProfileRank();
    this.dataset=this.transaction(()=>{
      const saved=this.stmt("SELECT data FROM matching_dataset WHERE singleton=1").get() as Row|undefined;
      const provided=options.dataset?datasetInput(options.dataset):null;
      if(saved){const dataset=datasetInput(JSON.parse(saved.data));if(provided&&stable(provided)!==stable(dataset))throw new MatchingError(409,"dataset_conflict","数据库已绑定数据集；请使用独立文件，不可变更现有来源或模式。");return dataset;}
      if(provided?.mode==="imported-offline"&&(this.count("products")||this.count("creators")))throw new MatchingError(409,"dataset_conflict","已有事实的旧库不能重标为真实离线数据；请使用独立数据库。");
      const dataset=provided??{id:"synthetic-default",mode:"synthetic-local" as const,label:"合成匹配数据",importedAt:null,sourceRefs:["synthetic:fixture"],warnings:[]};
      this.stmt("INSERT INTO matching_dataset VALUES(1,?)").run(JSON.stringify(dataset));return dataset;
    });
    if(this.dataset.mode==="synthetic-local"&&options.seed!==false&&this.count("products")===0&&this.count("creators")===0)this.upsert(makeMatchingFixture({now:this.now()}));
    }catch(error){this.db.close();throw error;}
  }
  private migrateIdentitySchema(){
    const version=(this.stmt("SELECT value FROM matching_meta WHERE key='schema_version'").get() as {value:number}).value;
    if(version===2)return;
    if(version!==1)throw new MatchingError(409,"schema_version","匹配数据库版本不兼容。");
    // Rebuild the parent table without renaming the old parent: existing FK targets stay creators.
    this.db.exec("PRAGMA foreign_keys=OFF");
    try{this.transaction(()=>{
      this.db.exec(`CREATE TABLE creators_v2(id TEXT PRIMARY KEY,market TEXT NOT NULL,oec_id TEXT,price_min INTEGER,price_max INTEGER,name TEXT NOT NULL,semantic_hash TEXT NOT NULL,relation_hash TEXT NOT NULL,data TEXT NOT NULL,external_namespace TEXT,external_id TEXT,UNIQUE(market,oec_id),UNIQUE(market,external_namespace,external_id),CHECK(oec_id IS NOT NULL OR (external_namespace IS NOT NULL AND external_id IS NOT NULL)));
        INSERT INTO creators_v2 SELECT id,market,oec_id,price_min,price_max,name,semantic_hash,relation_hash,data,NULL,NULL FROM creators;
        DROP TABLE creators;
        ALTER TABLE creators_v2 RENAME TO creators;
        CREATE INDEX creators_market_price ON creators(market,price_min,price_max,id);
        UPDATE matching_meta SET value=2 WHERE key='schema_version';`);
      if(this.db.prepare("PRAGMA foreign_key_check").all().length)throw new MatchingError(409,"schema_integrity","身份迁移的引用检查未通过。");
    });}finally{this.db.exec("PRAGMA foreign_keys=ON");this.statements.clear();}
  }
  private migrateProfileRank() {
    this.transaction(()=>{
      this.db.exec(`CREATE TABLE IF NOT EXISTS creator_profile_rank(market TEXT NOT NULL,category TEXT NOT NULL,entity_id TEXT NOT NULL REFERENCES creators(id),category_namespace TEXT NOT NULL,rank_scope TEXT NOT NULL,units_sold INTEGER,avg_views REAL,PRIMARY KEY(market,category,entity_id));
        CREATE INDEX IF NOT EXISTS creator_profile_category_rank ON creator_profile_rank(market,category,category_namespace,rank_scope,units_sold DESC,avg_views DESC,entity_id);
        CREATE INDEX IF NOT EXISTS creator_profile_entity ON creator_profile_rank(entity_id);`);
      if(!this.stmt("SELECT value FROM matching_meta WHERE key='profile_rank_version'").get()) {
        for(const row of this.stmt("SELECT data FROM creators").all() as Row[])this.indexProfile(JSON.parse(row.data) as MatchCreator);
        this.stmt("INSERT INTO matching_meta VALUES('profile_rank_version',1)").run();
      }
    });
  }
  private indexProfile(creator:MatchCreator) {
    this.stmt("DELETE FROM creator_profile_rank WHERE entity_id=?").run(creator.id);
    if(creator.categoryFact?.status==="conflict")return;
    const signals=creator.profileSignals;
    for(const category of [...(creator.categories.length?creator.categories:["__missing__"]),"__all__"])
      this.stmt("INSERT INTO creator_profile_rank VALUES(?,?,?,?,?,?,?)").run(creator.market,category,creator.id,creator.categoryFact?.namespace??"",profileRankScope(creator),signals?.unitsSold??null,signals?.avgViews??null);
  }
  close(){this.db.close();}
  private stmt(sql:string){let statement=this.statements.get(sql);if(!statement){statement=this.db.prepare(sql);this.statements.set(sql,statement);}return statement;}
  private transaction<T>(fn:()=>T):T{this.db.exec("BEGIN IMMEDIATE");try{const result=fn();this.db.exec("COMMIT");return result;}catch(error){this.db.exec("ROLLBACK");if(error instanceof MatchingError)throw error;if(error instanceof Error&&/UNIQUE constraint/.test(error.message))throw new MatchingError(409,"identity_conflict","稳定身份或同活动 Offer 已存在，不能用不同 ID 覆盖。");throw error;}}
  private rejectOlder(previous:{source:FactSource}|null,next:{source:FactSource}) {
    if(previous&&next.source.observedAt<previous.source.observedAt)throw new MatchingError(409,"older_observation","较早的观测不能覆盖当前事实；请在导入层保留历史并重新核对来源。");
  }
  private count(table:string){return (this.stmt(`SELECT COUNT(*) n FROM ${table}`).get() as {n:number}).n;}
  private raw(table:string,id:string){return this.stmt(`SELECT * FROM ${table} WHERE id=?`).get(id) as Row|undefined;}
  private entity<T>(table:string,id:string):T{const row=this.raw(table,id);if(!row)throw new MatchingError(404,"entity_missing",`未找到 ${table} 中的记录。`);return JSON.parse(row.data) as T;}
  private bump(key:string){this.stmt("INSERT INTO partition_versions(key,version) VALUES(?,1) ON CONFLICT(key) DO UPDATE SET version=version+1").run(key);}
  private productPartitions(p:MatchProduct){this.bump(`product:${p.id}`);this.bump(`products:${p.market}`);this.bump(`pid:${p.market}:${p.pid}`);for(const cat of p.categories.length?p.categories:["__missing__"])this.bump(`pc:${p.market}:${cat}`);}
  private creatorPartitions(c:MatchCreator){this.bump(`creator:${c.id}`);this.bump(`creators:${c.market}`);for(const cat of c.categories.length?c.categories:["__missing__"])this.bump(`cc:${c.market}:${cat}`);const edges=this.stmt("SELECT DISTINCT pid FROM evidence WHERE creator_id=?").all(c.id) as {pid:string}[];for(const e of edges)this.bump(`ep:${c.market}:${e.pid}`);for(const d of this.demandsFor(c.id))this.demandPartitions(d,c.market);}
  private demandPartitions(d:CreatorDemand,m:MatchMarket){this.bump(`dc:${d.creatorId}`);if(d.productId)this.bump(`dp:${d.productId}`);for(const cat of d.categories)this.bump(`dcat:${m}:${cat}`);}
  stats():MatchingStats{return {mode:this.dataset.mode,dataset:structuredClone(this.dataset),products:this.count("products"),creators:this.count("creators"),offers:this.count("offers"),evidence:this.count("evidence"),demands:this.count("demands"),runs:this.count("recall_runs"),packets:this.count("review_packets"),semanticBuilds:(this.stmt("SELECT value FROM matching_meta WHERE key='semantic_builds'").get() as {value:number}).value,llmCalls:0,billedTokens:0,matchingVersion:VERSION};}
  upsert(batch:MatchingBatch):ImportResult{return this.transaction(()=>this.importBatch(batch));}
  private importBatch(batch:MatchingBatch):ImportResult {
    if(!batch||typeof batch!=="object"||Array.isArray(batch))bad("导入必须为结构化事实对象。");
    for(const key of Object.keys(batch))if(!["products","creators","offers","evidence","demands"].includes(key))bad(`未知导入字段 ${key}。`);
    for(const list of Object.values(batch))if(!Array.isArray(list)||list.length>100_000||list.some(v=>!v||typeof v!=="object"||Array.isArray(v)))bad("事实数组无效或超过单批限制。");
    const result:ImportResult={inserted:0,updated:0,unchanged:0,semanticChanges:0,commercialChanges:0,relationChanges:0};
    const record=(old:unknown)=>{if(old)result.updated++;else result.inserted++;};
    for(const input of batch.products??[]) {
      const p=productInput(input),row=this.raw("products",p.id),old=row?JSON.parse(row.data) as MatchProduct:null;
      if(old&&(old.market!==p.market||old.pid!==p.pid))throw new MatchingError(409,"identity_conflict","商品稳定 ID 不能更换市场或 PID。");
      this.rejectOlder(old,p);
      const semantic=hash(productSemantic(p)),commercial=hash(productCommercial(p));
      const next:MatchProduct={...p,semanticRevision:(old?.semanticRevision??0)+(row?.semantic_hash!==semantic?1:0),commercialRevision:(old?.commercialRevision??0)+(row?.commercial_hash!==commercial?1:0)};
      if(old&&stable(old)===stable(next)){result.unchanged++;continue;}
      if(row?.semantic_hash!==semantic)result.semanticChanges++;if(row?.commercial_hash!==commercial)result.commercialChanges++;
      this.stmt("INSERT INTO products VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET price_minor=excluded.price_minor,title=excluded.title,semantic_hash=excluded.semantic_hash,commercial_hash=excluded.commercial_hash,data=excluded.data").run(p.id,p.market,p.pid,p.priceMinor,p.title,semantic,commercial,JSON.stringify(next));
      this.stmt("DELETE FROM product_categories WHERE entity_id=?").run(p.id);
      for(const category of p.categories.length?p.categories:["__missing__"])this.stmt("INSERT INTO product_categories VALUES(?,?,?,?)").run(p.market,category,p.id,p.priceMinor);
      if(old)this.productPartitions(old);this.productPartitions(next);record(old);
    }
    for(const input of batch.creators??[]) {
      const c=creatorInput(input),row=this.raw("creators",c.id),old=row?JSON.parse(row.data) as MatchCreator:null;
      if(this.dataset.mode==="imported-offline"&&c.avatar)bad("离线导入不加载达人远端头像，avatar 必须留空。");
      if(old&&(old.market!==c.market||old.oecId!==c.oecId||stable(old.externalIdentity??null)!==stable(c.externalIdentity??null)))throw new MatchingError(409,"identity_conflict","达人稳定 ID 不能更换市场、OEC 或外部身份；身份核验需独立处理。");
      this.rejectOlder(old,c);
      if(old?.profileSignals&&c.profileSignals)this.rejectOlder(old.profileSignals,c.profileSignals);
      const semantic=hash(creatorSemantic(c)),relation=hash(creatorRelation(c));
      const next:MatchCreator={...c,semanticRevision:(old?.semanticRevision??0)+(row?.semantic_hash!==semantic?1:0),relationRevision:(old?.relationRevision??0)+(row?.relation_hash!==relation?1:0)};
      if(old&&stable(old)===stable(next)){result.unchanged++;continue;}
      if(row?.semantic_hash!==semantic)result.semanticChanges++;if(row?.relation_hash!==relation)result.relationChanges++;
      this.stmt("INSERT INTO creators VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET price_min=excluded.price_min,price_max=excluded.price_max,name=excluded.name,semantic_hash=excluded.semantic_hash,relation_hash=excluded.relation_hash,data=excluded.data").run(c.id,c.market,c.oecId,c.priceMinMinor,c.priceMaxMinor,c.name,semantic,relation,JSON.stringify(next),c.externalIdentity?.namespace??null,c.externalIdentity?.id??null);
      this.stmt("DELETE FROM creator_categories WHERE entity_id=?").run(c.id);
      for(const category of c.categories.length?c.categories:["__missing__"])this.stmt("INSERT INTO creator_categories VALUES(?,?,?,?,?)").run(c.market,category,c.id,c.priceMinMinor,c.priceMaxMinor);
      this.indexProfile(next);
      if(old)this.creatorPartitions(old);this.creatorPartitions(next);record(old);
    }
    for(const input of batch.offers??[]) {
      const o=offerInput(input),p=this.entity<MatchProduct>("products",o.productId),row=this.raw("offers",o.id),old=row?JSON.parse(row.data) as MatchOffer:null;
      if(old&&(old.productId!==o.productId||old.campaignId!==o.campaignId||old.accountRef!==o.accountRef))throw new MatchingError(409,"identity_conflict","Offer 稳定 ID 不能切换商品、活动或账号。");
      this.rejectOlder(old,o);
      if(old){const {version,...original}=old;void version;if(stable(original)===stable(o)){result.unchanged++;continue;}}
      const next:MatchOffer={...o,version:(old?.version??0)+1};this.stmt("INSERT INTO offers VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET starts_at=excluded.starts_at,ends_at=excluded.ends_at,stock=excluded.stock,data=excluded.data").run(o.id,o.productId,o.campaignId,o.accountRef,o.startsAt,o.endsAt,o.stock,JSON.stringify(next));
      p.commercialRevision++;this.stmt("UPDATE products SET data=? WHERE id=?").run(JSON.stringify(p),p.id);this.productPartitions(p);result.commercialChanges++;record(old);
    }
    for(const input of batch.evidence??[]) {
      const e=evidenceInput(input),creator=this.entity<MatchCreator>("creators",e.creatorId),row=this.raw("evidence",e.id),old=row?JSON.parse(row.data) as ProductEvidence:null;
      if(creator.market!==e.market)bad("销量证据与达人市场不一致。");
      if(old&&(old.creatorId!==e.creatorId||old.market!==e.market||old.pid!==e.pid))throw new MatchingError(409,"identity_conflict","证据 ID 不能更换达人、市场或 PID。");
      this.rejectOlder(old,e);
      if(old&&stable(old)===stable(e)){result.unchanged++;continue;}
      this.stmt("INSERT INTO evidence VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET units=excluded.units,data=excluded.data").run(e.id,e.creatorId,e.market,e.pid,e.units,JSON.stringify(e));this.bump(`ep:${e.market}:${e.pid}`);this.bump(`ec:${e.creatorId}`);record(old);
    }
    for(const input of batch.demands??[]) {
      const d=demandInput(input),creator=this.entity<MatchCreator>("creators",d.creatorId),row=this.raw("demands",d.id),old=row?JSON.parse(row.data) as CreatorDemand:null;
      if(d.productId&&this.entity<MatchProduct>("products",d.productId).market!==creator.market)bad("需求商品与达人市场不一致。");
      if(old&&old.creatorId!==d.creatorId)throw new MatchingError(409,"identity_conflict","需求 ID 不能切换达人。");
      this.rejectOlder(old,d);
      if(old&&stable(old)===stable(d)){result.unchanged++;continue;}
      this.stmt("INSERT INTO demands VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET product_id=excluded.product_id,active=excluded.active,data=excluded.data").run(d.id,d.creatorId,creator.market,d.productId,d.active?1:0,JSON.stringify(d));
      this.stmt("DELETE FROM demand_categories WHERE demand_id=?").run(d.id);for(const category of d.categories)this.stmt("INSERT INTO demand_categories VALUES(?,?,?,?,?)").run(creator.market,category,d.id,d.creatorId,d.active?1:0);
      if(old)this.demandPartitions(old,creator.market);this.demandPartitions(d,creator.market);record(old);
    }
    this.stmt("UPDATE matching_meta SET value=value+? WHERE key='semantic_builds'").run(result.semanticChanges);return result;
  }
  private page<T>(table:"products"|"creators",query:{market?:MatchMarket;q?:string;offset?:number;limit?:number}):MatchPage<T>{
    const offset=integer(query.offset??0,"offset")!,limit=integer(query.limit??24,"limit",false,100)!;
    if(limit<1)bad("limit 至少为 1。");
    const args:SQLInputValue[]=[],where:string[]=[];
    if(query.market){where.push("market=?");args.push(choice(query.market,["mx","br","it"],"market"));}
    if(query.q){
      const q=text(query.q,"q",200).replace(/[\\%_]/g,"\\$&"),columns=table==="products"?["title","pid"]:["name","oec_id","external_id"];
      where.push(`(${columns.map(column=>`${column} LIKE ? ESCAPE '\\'`).join(" OR ")})`);args.push(...columns.map(()=>`%${q}%`));
    }
    const clause=where.length?` WHERE ${where.join(" AND ")}`:"";
    return {items:(this.stmt(`SELECT data FROM ${table}${clause} ORDER BY id LIMIT ? OFFSET ?`).all(...args,limit,offset) as Row[]).map(r=>JSON.parse(r.data) as T),total:(this.stmt(`SELECT COUNT(*) n FROM ${table}${clause}`).get(...args) as {n:number}).n,offset,limit};
  }
  listProducts(query:{market?:MatchMarket;q?:string;offset?:number;limit?:number}={}):MatchPage<MatchProduct>{return this.page("products",query);}
  listCreators(query:{market?:MatchMarket;q?:string;offset?:number;limit?:number}={}):MatchPage<MatchCreator>{return this.page("creators",query);}
  private offersFor(productId:string):MatchOffer[]{return (this.stmt("SELECT data FROM offers WHERE product_id=? ORDER BY CASE WHEN starts_at<=? AND ends_at>? AND stock>0 AND json_extract(data,'$.cardStatus')='verified' AND json_extract(data,'$.creatorCommissionBps') IS NOT NULL AND json_extract(data,'$.agencyCommissionBps') IS NOT NULL THEN 0 ELSE 1 END,id LIMIT 20").all(productId,this.now(),this.now()) as Row[]).map(r=>JSON.parse(r.data));}
  private demandsFor(creatorId:string):CreatorDemand[]{return (this.stmt("SELECT data FROM demands WHERE creator_id=? AND active=1 ORDER BY id LIMIT 200").all(creatorId) as Row[]).map(r=>JSON.parse(r.data));}
  private query(value:RecallQuery):RecallQuery{if(!value||typeof value!=="object")bad("缺少召回查询。");const limit=integer(value.limit,"limit",false,FINAL_LIMIT)!;if(limit<1)bad("召回数量至少为 1。");return {direction:choice(value.direction,["product","creator"],"direction"),subjectId:text(value.subjectId,"subjectId",100),source:choice(value.source,["all","first","second"],"source"),limit};}
  private subject(query:RecallQuery):MatchProduct|MatchCreator{return this.entity(query.direction==="product"?"products":"creators",query.subjectId);}
  private dependencies(q:RecallQuery,s:MatchProduct|MatchCreator):string[]{
    const keys=new Set<string>([`${q.direction}:${s.id}`,`clock:${s.market}`]),m=s.market;
    if(q.direction==="product") {
      const p=s as MatchProduct;
      keys.add(`ep:${m}:${p.pid}`);keys.add(`dp:${p.id}`);for(const cat of p.categories)keys.add(`dcat:${m}:${cat}`);
      if(q.source!=="second") {keys.add(`dp:${p.id}`);for(const cat of p.categories){keys.add(`cc:${m}:${cat}`);keys.add(`dcat:${m}:${cat}`);}keys.add(`cc:${m}:__missing__`);if(!p.categories.length)keys.add(`creators:${m}`);}
    } else {
      const c=s as MatchCreator;
      {keys.add(`ec:${c.id}`);keys.add(`dc:${c.id}`);for(const e of this.stmt("SELECT DISTINCT pid FROM evidence WHERE creator_id=? AND market=? AND units>0").all(c.id,m) as {pid:string}[])keys.add(`pid:${m}:${e.pid}`);}
      if(q.source!=="second") {keys.add(`dc:${c.id}`);for(const cat of c.categories)keys.add(`pc:${m}:${cat}`);keys.add(`pc:${m}:__missing__`);if(!c.categories.length)keys.add(`products:${m}`);for(const d of this.demandsFor(c.id)){if(d.productId)keys.add(`product:${d.productId}`);for(const cat of d.categories)keys.add(`pc:${m}:${cat}`);}}
    }
    return [...keys].sort();
  }
  private fingerprint(q:RecallQuery,dependencies:string[]){return hash({query:q,version:VERSION,dataset:this.dataset,partitions:dependencies.map(key=>[key,(this.stmt("SELECT version FROM partition_versions WHERE key=?").get(key) as {version:number}|undefined)?.version??0])});}
  private profileRoute(market:MatchMarket,categories:string[],namespace:string|null):{sql:string;args:SQLInputValue[]} {
    return {sql:`WITH scoped AS (
      SELECT entity_id id,COUNT(*) overlap,rank_scope,MAX(units_sold) units_sold,MAX(avg_views) avg_views
      FROM creator_profile_rank INDEXED BY creator_profile_category_rank
      WHERE market=? AND category IN (${categories.map(()=>"?").join(",")})${namespace===null?"":" AND category_namespace=?"}
      GROUP BY entity_id
    ), ranked AS (
      SELECT id,overlap,rank_scope,ROW_NUMBER() OVER (PARTITION BY overlap,rank_scope ORDER BY units_sold DESC,avg_views DESC,id) lane_position FROM scoped
    ) SELECT id FROM ranked ORDER BY overlap DESC,lane_position,rank_scope,id`,args:[market,...categories,...(namespace===null?[]:[namespace])]};
  }
  private routes(q:RecallQuery,s:MatchProduct|MatchCreator):Route[] {
    const routes:Route[]=[],m=s.market,profileFirst=this.dataset.mode==="imported-offline";const add=(source:CandidateSource,sql:string,args:SQLInputValue[])=>routes.push({source,sql:`${sql} LIMIT ${ROUTE_LIMIT}`,args});
    const addProfile=(source:CandidateSource,categories:string[],namespace:string|null)=>{const route=this.profileRoute(m,categories,namespace);add(source,route.sql,route.args);};
    if(q.direction==="product") {
      const p=s as MatchProduct;
      if(q.source!=="first")add("exact_pid","SELECT creator_id id FROM evidence INDEXED BY evidence_exact WHERE market=? AND pid=? AND units>0 GROUP BY creator_id ORDER BY MAX(units) DESC,creator_id",[m,p.pid]);
      if(q.source!=="second") {
        add("explicit_demand","SELECT DISTINCT creator_id id FROM demands INDEXED BY demands_product WHERE market=? AND product_id=? AND active=1 ORDER BY creator_id",[m,p.id]);
        if(p.categoryFact?.status==="conflict")return routes;
        if(p.categories.length) {
          const holders=p.categories.map(()=>"?").join(",");
          add("explicit_demand",`SELECT DISTINCT creator_id id FROM demand_categories INDEXED BY demand_category_active WHERE market=? AND category IN (${holders}) AND active=1 ORDER BY creator_id`,[m,...p.categories]);
          if(profileFirst){addProfile("category",p.categories,p.categoryFact?.namespace??"");addProfile("cold_start",["__missing__"],null);}
          else {
            if(p.priceMinor!==null)add("category_price",`SELECT DISTINCT entity_id id FROM creator_categories INDEXED BY creator_category_price WHERE market=? AND category IN (${holders}) AND price_min<=? AND price_max>=? ORDER BY price_min DESC,entity_id`,[m,...p.categories,p.priceMinor,p.priceMinor]);
            add("category",`SELECT DISTINCT entity_id id FROM creator_categories INDEXED BY creator_category_price WHERE market=? AND category IN (${holders}) ORDER BY entity_id`,[m,...p.categories]);
            add("cold_start","SELECT DISTINCT entity_id id FROM creator_categories INDEXED BY creator_category_price WHERE market=? AND category='__missing__' ORDER BY entity_id",[m]);
          }
        } else if(profileFirst)addProfile("cold_start",["__all__"],null);else add("cold_start","SELECT id FROM creators INDEXED BY creators_market_price WHERE market=? ORDER BY price_min,id",[m]);
      }
    } else {
      const c=s as MatchCreator;
      if(q.source!=="first")add("exact_pid","SELECT p.id FROM evidence e INDEXED BY evidence_creator JOIN products p ON p.market=e.market AND p.pid=e.pid WHERE e.creator_id=? AND e.market=? AND e.units>0 GROUP BY p.id ORDER BY MAX(e.units) DESC,p.id",[c.id,m]);
      if(q.source!=="second") {
        add("explicit_demand","SELECT DISTINCT product_id id FROM demands INDEXED BY demands_creator WHERE creator_id=? AND active=1 AND product_id IS NOT NULL ORDER BY product_id",[c.id]);
        const demandCats=[...new Set(this.demandsFor(c.id).flatMap(d=>d.categories))];
        if(demandCats.length)add("explicit_demand",`SELECT DISTINCT entity_id id FROM product_categories INDEXED BY product_category_price WHERE market=? AND category IN (${demandCats.map(()=>"?").join(",")}) ORDER BY ${profileFirst?"entity_id":"price_minor,entity_id"}`,[m,...demandCats]);
        if(c.categoryFact?.status==="conflict")return routes;
        if(c.categories.length) {
          const holders=c.categories.map(()=>"?").join(",");
          if(!profileFirst&&c.priceMinMinor!==null&&c.priceMaxMinor!==null)add("category_price",`SELECT DISTINCT entity_id id FROM product_categories INDEXED BY product_category_price WHERE market=? AND category IN (${holders}) AND price_minor>=? AND price_minor<=? ORDER BY price_minor,entity_id`,[m,...c.categories,c.priceMinMinor,c.priceMaxMinor]);
          add("category",profileFirst?`SELECT pc.entity_id id FROM product_categories pc INDEXED BY product_category_price JOIN products p ON p.id=pc.entity_id WHERE pc.market=? AND pc.category IN (${holders}) AND COALESCE(json_extract(p.data,'$.categoryFact.namespace'),'')=? GROUP BY pc.entity_id ORDER BY COUNT(*) DESC,pc.entity_id`:`SELECT DISTINCT entity_id id FROM product_categories INDEXED BY product_category_price WHERE market=? AND category IN (${holders}) ORDER BY entity_id`,[m,...c.categories,...(profileFirst?[c.categoryFact?.namespace??""]:[])]);
          add("cold_start","SELECT DISTINCT entity_id id FROM product_categories INDEXED BY product_category_price WHERE market=? AND category='__missing__' ORDER BY entity_id",[m]);
        } else add("cold_start",`SELECT id FROM products INDEXED BY products_market_price WHERE market=? ORDER BY ${profileFirst?"id":"price_minor,id"}`,[m]);
      }
    }
    return routes;
  }
  explainRecall(input:RecallQuery):string[]{const q=this.query(input),s=this.subject(q);return this.routes(q,s).flatMap(route=>(this.stmt(`EXPLAIN QUERY PLAN ${route.sql}`).all(...route.args) as {detail:string}[]).map(r=>`${route.source}: ${r.detail}`));}
  private candidate(p:MatchProduct,c:MatchCreator,sources:Set<CandidateSource>):MatchCandidate {
    const evidence=(this.stmt("SELECT data FROM evidence INDEXED BY evidence_creator WHERE creator_id=? AND market=? AND pid=? AND units>0 ORDER BY units DESC LIMIT 20").all(c.id,p.market,p.pid) as Row[]).map(r=>JSON.parse(r.data) as ProductEvidence);
    const demands=this.demandsFor(c.id).filter(d=>d.productId===p.id||d.categories.some(cat=>p.categories.includes(cat)));
    // Sources describe verified pair facts, even when another route supplied this pair first.
    if(evidence.length)sources.add("exact_pid");if(demands.length)sources.add("explicit_demand");
    const compatibleNamespace=!p.categoryFact&&!c.categoryFact||Boolean(p.categoryFact&&c.categoryFact&&p.categoryFact.namespace===c.categoryFact.namespace);
    const categoryOverlap=compatibleNamespace?p.categories.filter(cat=>c.categories.includes(cat)).length:0;
    if(!compatibleNamespace){sources.delete("category");sources.delete("category_price");}
    if(p.categoryFact?.status==="conflict"||c.categoryFact?.status==="conflict") {
      sources.delete("category");sources.delete("category_price");sources.delete("cold_start");
    }
    const priceOverlap=p.priceMinor===null||c.priceMinMinor===null||c.priceMaxMinor===null?null:p.priceMinor>=c.priceMinMinor&&p.priceMinor<=c.priceMaxMinor;
    const formatOverlap=!p.formats.length||!c.formats.length?null:p.formats.some(f=>c.formats.includes(f));
    const offers=this.offersFor(p.id),now=this.now(),gaps:string[]=[],reasons:string[]=[],profileFirst=this.dataset.mode==="imported-offline";
    if(profileFirst)gaps.push("离线历史快照：平台执行、关系控制与商业条件未核验为当前可执行事实；不影响已有画像分析");
    if(p.categoryFact&&(!profileFirst||p.categoryFact.status==="conflict"))gaps.push(`商品分类${p.categoryFact.status==="conflict"?"冲突，已停用":"来自历史标签，需复核"}：${p.categoryFact.note}`);
    if(c.categoryFact&&!profileFirst)gaps.push(`达人主类目来自历史快照：${c.categoryFact.note}`);
    if(!compatibleNamespace)gaps.push("分类命名空间不一致，不能按标签相同认定类目适配");
    if(c.oecId===null)gaps.push("OEC 身份未知；外部 Kalodata ID 不能作为发送身份");
    if(c.control==="unknown")gaps.push("人工接管／暂停状态未知，不能推定 Agent 有执行权");
    if(c.marketingStopped===null)gaps.push("拒联状态未知，不能将没有记录当作允许营销联系");
    if(demands.length)reasons.push("有当前明确需求及来源引用");
    if(evidence.length)reasons.push(`同市场精确 PID 有正销量证据；显示 ${evidence[0].source.ref} 单一观测 ${evidence[0].units} 件，窗口不相加；不证明当前持有样品或接受新条件`);
    if(evidence.some(e=>e.source.windowStart===null||e.source.windowEnd===null))gaps.push("部分销量证据的统计窗口未知");
    if(categoryOverlap)reasons.push(`${p.categoryFact||c.categoryFact?"历史顶级标签相同：":"匹配 "}${categoryOverlap} 个共同类目；需另评适配性`);else gaps.push("缺少共同类目证据，需补充适配判断");
    if(!profileFirst) {
      if(priceOverlap===true)reasons.push("商品价格在达人同币种价格带内");else gaps.push(priceOverlap===null?"商品价格或达人价格带未知":"价格超出已知价格带，需确认新需求");
      if(formatOverlap===true)reasons.push("视频／直播内容形式有交集");else gaps.push(formatOverlap===null?"内容形式尚缺证据":"已知内容形式没有交集");
    }
    if(!offers.length)gaps.push("尚无活动 Offer");
    if(offers.length===20&&(this.stmt("SELECT COUNT(*) n FROM offers WHERE product_id=?").get(p.id) as {n:number}).n>20)gaps.push("活动 Offer 超过 20 个，当前只展示优先的 20 个方案");
    for(const offer of offers) {
      const prefix=`Offer ${offer.id}: `;
      if(offer.startsAt>now)gaps.push(prefix+"活动尚未开始");if(offer.endsAt<=now)gaps.push(prefix+"活动已过期");
      if(offer.stock===null)gaps.push(prefix+"库存未知");else if(offer.stock===0)gaps.push(prefix+"已知无库存");
      if(offer.cardStatus!=="verified")gaps.push(prefix+"商品卡准备状态需核验");
      if(offer.creatorCommissionBps===null||offer.agencyCommissionBps===null)gaps.push(prefix+"达人／机构分佣报价未完整，不能承诺总佣金");
      if(offer.sampleAvailable===null||offer.sampleQuota===null)gaps.push(prefix+"样品条件未知");
    }
    if(c.marketingStopped)gaps.push("已明确拒绝营销联系");if(c.control==="human"||c.control==="paused")gaps.push(c.control==="human"?"关系由人工接管":"关系已暂停");
    const usable=offers.some(o=>o.startsAt<=now&&o.endsAt>now&&o.stock!==null&&o.stock>0&&o.cardStatus==="verified"&&o.creatorCommissionBps!==null&&o.agencyCommissionBps!==null);
    const unknown=c.oecId===null||c.control==="unknown"||c.marketingStopped===null;
    const suppressed=c.marketingStopped===true||c.control==="human"||c.control==="paused"||(this.dataset.mode==="synthetic-local"&&unknown);
    const exactUnits=evidence.length?Math.max(...evidence.map(e=>e.units)):null,analysis=analyzeProfile(p,c,sources,categoryOverlap,exactUnits,analysisPolicy(this.dataset.mode));
    return {analysis,creator:c,product:p,offers,sources:[...sources].sort(),reasons:profileFirst?analysis.positiveEvidence:reasons,gaps,evidenceRefs:[...new Set([p.source.ref,c.source.ref,...(c.profileSignals?[c.profileSignals.source.ref]:[]),...evidence.map(e=>e.source.ref),...demands.map(d=>d.source.ref)])],readiness:suppressed?"suppressed":this.dataset.mode==="imported-offline"||!usable||priceOverlap!==true||formatOverlap!==true||!categoryOverlap?"needs_facts":"reviewable",features:{categoryOverlap,priceOverlap,formatOverlap,exactUnits}};
  }
  recall(input:RecallQuery):MatchRun {return this.transaction(()=>{
    const started=performance.now(),q=this.query(input),s=this.subject(q),dependencies=this.dependencies(q,s);let fingerprint=this.fingerprint(q,dependencies);const cached=this.stmt("SELECT data FROM recall_runs WHERE fingerprint=?").get(fingerprint) as Row|undefined;
    if(cached){const stored=JSON.parse(cached.data) as StoredRun;if(stored.expiresAt===null||stored.expiresAt>this.now())return {...stored.run,cacheHit:true};this.bump(`clock:${s.market}`);fingerprint=this.fingerprint(q,dependencies);}
    const pairSources=new Map<string,Set<CandidateSource>>();let rowsFetched=0,truncated=false;
    for(const route of this.routes(q,s)) {
      const rows=this.stmt(route.sql).all(...route.args) as {id:string}[];rowsFetched+=rows.length;if(rows.length>=ROUTE_LIMIT)truncated=true;
      for(const row of rows){let set=pairSources.get(row.id);if(!set){set=new Set();pairSources.set(row.id,set);}set.add(route.source);}
    }
    let candidates=[...pairSources.entries()].map(([other,sources])=>this.candidate(q.direction==="product"?s as MatchProduct:this.entity<MatchProduct>("products",other),q.direction==="creator"?s as MatchCreator:this.entity<MatchCreator>("creators",other),sources)).filter(candidate=>candidate.sources.length>0);
    // Lexicographic evidence priorities, not a fabricated probability or uncalibrated success score.
    const priority=(c:MatchCandidate)=>[c.sources.includes("explicit_demand")?1:0,c.sources.includes("exact_pid")?1:0,c.features.priceOverlap===true?1:0,c.features.formatOverlap===true?1:0,c.features.categoryOverlap];
    if(this.dataset.mode==="imported-offline")candidates=orderProfileCandidates(candidates);
    else candidates.sort((a,b)=>{const aa=priority(a),bb=priority(b);for(let i=0;i<aa.length;i++)if(aa[i]!==bb[i])return bb[i]-aa[i];return `${a.creator.id}:${a.product.id}`.localeCompare(`${b.creator.id}:${b.product.id}`);});
    if(candidates.length>q.limit)truncated=true;
    const result:MatchRun={analysisPolicy:analysisPolicy(this.dataset.mode),id:`match-run-${randomUUID()}`,query:q,market:s.market,createdAt:this.now(),matchingVersion:VERSION,fingerprint,cacheHit:false,stale:false,subject:s,candidates:candidates.slice(0,q.limit),diagnostics:{rowsFetched,perRouteLimit:ROUTE_LIMIT,candidateLimit:FINAL_LIMIT,truncated,durationMs:Math.round((performance.now()-started)*100)/100,fullCartesianEvaluated:false,llmCalls:0,billedTokens:0},warnings:[...("categoryFact" in s&&s.categoryFact?.status==="conflict"?["主体商品分类存在来源冲突，已暂停类目和探索召回；保留原观察供核验。"]:[]),...(this.dataset.mode==="imported-offline"?["已自动分析已有画像；画像年龄、价格带和内容形式不阻碍本轮判断。人工标注可选，不是推进前提。","表现仅在相同来源口径和指标可用性组内辅助排序；不同组按组内名次交错，未知不当零。","分析与平台执行分开；本轮不执行发送，真实合作效果尚未验证。"]:["全部为合成事实；结构召回覆盖与排序质量仍需真实样本评估。"]),"每路有界返回不能证明真实适配质量；销量窗口可能重叠，因此不累计为总销量。",...(truncated?["召回或结果达到数量边界；部分候选未展示。"]:[])]};
    const boundaries=candidates.flatMap(c=>c.offers.flatMap(o=>[o.startsAt,o.endsAt])).filter(at=>at>this.now());
    const stored:StoredRun={run:result,dependencies,expiresAt:boundaries.length?Math.min(...boundaries):null};
    this.stmt("INSERT INTO recall_runs VALUES(?,?,?)").run(result.id,fingerprint,JSON.stringify(stored));return result;
  });}
  private currentRun(runId:string):MatchRun {const row=this.raw("recall_runs",text(runId,"runId",100));if(!row)throw new MatchingError(404,"run_missing","召回记录不存在或已过期，请重新召回。");const stored=JSON.parse(row.data) as StoredRun,run=stored.run;const current=this.fingerprint(run.query,this.dependencies(run.query,this.subject(run.query)));if(current!==run.fingerprint||stored.expiresAt!==null&&stored.expiresAt<=this.now())throw new MatchingError(409,"stale_run","商品、关系或候选索引已经变化，请重新召回后再准备上下文。");return run;}
  private assessmentsFor(run:MatchRun):AssessmentResponse {
    const rows=this.stmt("SELECT creator_id,product_id,label,note,revision,reviewed_at FROM candidate_assessments WHERE dataset_id=? AND fingerprint=?").all(this.dataset.id,run.fingerprint) as {creator_id:string;product_id:string;label:AssessmentLabel|null;note:string;revision:number;reviewed_at:number|null}[];
    const saved=new Map(rows.map(row=>[JSON.stringify([row.creator_id,row.product_id]),row]));
    const items:CandidateAssessment[]=run.candidates.map(candidate=>{
      const row=saved.get(JSON.stringify([candidate.creator.id,candidate.product.id]));
      return {creatorId:candidate.creator.id,productId:candidate.product.id,label:row?.label??null,note:row?.note??"",revision:row?.revision??0,reviewedAt:row?.reviewed_at??null};
    });
    const suitable=items.filter(item=>item.label==="suitable").length,unsuitable=items.filter(item=>item.label==="unsuitable").length,insufficient=items.filter(item=>item.label==="insufficient").length;
    const total=items.length,decided=suitable+unsuitable,reviewed=decided+insufficient;
    return {runId:run.id,items,summary:{total,reviewed,suitable,unsuitable,insufficient,decided,suitabilityRate:decided?suitable/decided:null,coverage:total?reviewed/total:0}};
  }
  getRun(runId:string):MatchRun {return this.transaction(()=>this.currentRun(runId));}
  assessments(runId:string):AssessmentResponse {return this.transaction(()=>this.assessmentsFor(this.currentRun(runId)));}
  assessCandidate(runId:string,creatorId:string,productId:string,label:AssessmentLabel|null,note:string,expectedRevision:number,requestId:string):AssessmentResponse {return this.transaction(()=>{
    if(this.dataset.mode!=="synthetic-local"&&this.dataset.mode!=="imported-offline")throw new MatchingError(403,"offline_only","人工候选评审仅用于本地离线测试，不产生发送授权。");
    text(runId,"runId",100);text(creatorId,"creatorId",100);text(productId,"productId",100);text(requestId,"requestId",150);
    if(label!==null)choice(label,["suitable","unsuitable","insufficient"],"label");
    optionalText(note,"note",1000);integer(expectedRevision,"expectedRevision");
    const run=this.currentRun(runId);
    if(!run.candidates.some(candidate=>candidate.creator.id===creatorId&&candidate.product.id===productId))throw new MatchingError(404,"candidate_missing","该达人与商品组合不在当前召回结果中。");
    const requestHash=hash({type:"assess_candidate",datasetId:this.dataset.id,runId,fingerprint:run.fingerprint,creatorId,productId,label,note,expectedRevision});
    const cached=this.stmt("SELECT request_hash FROM matching_requests WHERE id=?").get(requestId) as {request_hash:string}|undefined;
    if(cached){if(cached.request_hash!==requestHash)throw new MatchingError(409,"request_conflict","同一个请求 ID 不能用于不同评审。请为新操作生成新的请求 ID。");return this.assessmentsFor(run);}
    const previous=this.stmt("SELECT revision FROM candidate_assessments WHERE dataset_id=? AND fingerprint=? AND creator_id=? AND product_id=?").get(this.dataset.id,run.fingerprint,creatorId,productId) as {revision:number}|undefined;
    if((previous?.revision??0)!==expectedRevision)throw new MatchingError(409,"revision_conflict","这条人工评审已变化，请刷新后再保存。");
    this.stmt(`INSERT INTO candidate_assessments(dataset_id,fingerprint,creator_id,product_id,label,note,revision,reviewed_at) VALUES(?,?,?,?,?,?,?,?)
      ON CONFLICT(dataset_id,fingerprint,creator_id,product_id) DO UPDATE SET label=excluded.label,note=excluded.note,revision=excluded.revision,reviewed_at=excluded.reviewed_at`).run(this.dataset.id,run.fingerprint,creatorId,productId,label,note,expectedRevision+1,label===null?null:this.now());
    const result=this.assessmentsFor(run);
    // The request receipt also retains every successful revision; a stale run never loses its annotations.
    this.stmt("INSERT INTO matching_requests VALUES(?,?,?)").run(requestId,requestHash,JSON.stringify(result));return result;
  });}
  prepareReview(runId:string,creatorId:string):ReviewPacket {return this.transaction(()=>{
    const run=this.currentRun(runId);text(creatorId,"creatorId",100);const matching=run.candidates.filter(c=>c.creator.id===creatorId);if(!matching.length)throw new MatchingError(404,"candidate_missing","该达人不在当前候选中。");if(matching.some(c=>c.readiness==="suppressed"))throw new MatchingError(409,"relationship_suppressed","该关系已拒联、暂停或由人工接管，不能准备 Agent 决策。");
    const fingerprint=hash({run:run.fingerprint,creatorId,packetVersion:6}),cached=this.stmt("SELECT data FROM review_packets WHERE fingerprint=?").get(fingerprint) as Row|undefined;if(cached)return JSON.parse(cached.data) as ReviewPacket;
    const creator=matching[0].creator;
    const packetCategory=(value:CategoryFact|undefined)=>{
      if(!value)return null;if(!creator.profileSignals)return value;
      const {note,...provenance}=value;return {...provenance,omittedNoteCharacters:note.length};
    };
    const common=(get:(candidate:MatchCandidate)=>string[])=>creator.profileSignals?get(matching[0]).filter(value=>matching.every(candidate=>get(candidate).includes(value))):[];
    const sharedExecutionGaps=common(candidate=>candidate.gaps),sharedAnalysisLimitations=common(candidate=>candidate.analysis.limitations);
    const payload:Record<string,unknown>={schema:"bdhub.relationship-review.v2",analysisPolicy:run.analysisPolicy,mode:this.dataset.mode,datasetId:this.dataset.id,datasetImportedAt:this.dataset.importedAt,datasetWarnings:this.dataset.warnings.slice(0,2),datasetWarningsRole:"仅为来源快照备注；本轮分析遵循 analysisPolicy，旧备注中的过期、价带、形式或人工评审提示不是分析前提",omittedDatasetWarnings:Math.max(0,this.dataset.warnings.length-2),executionBlocked:true,purpose:this.dataset.mode==="imported-offline"?"依据已有画像自动分析少量候选；忽略画像年龄，价格和形式仅作背景，人工标注可选；执行资格独立判断":"供一个关系 Agent 判断少量候选；尚未调用模型，不构成发送授权",creator:{id:creator.id,oecId:creator.oecId,externalIdentity:creator.externalIdentity??null,market:creator.market,currency:creator.currency,categories:creator.categories,categoryFact:packetCategory(creator.categoryFact),profileSignals:creator.profileSignals??null,formats:creator.formats,priceBand:[creator.priceMinMinor,creator.priceMaxMinor],control:creator.control,marketingStopped:creator.marketingStopped,semanticRevision:creator.semanticRevision,relationRevision:creator.relationRevision,source:creator.source},candidates:[]};
    if(creator.profileSignals){
      payload.sharedExecutionGaps=sharedExecutionGaps;payload.sharedAnalysisLimitations=sharedAnalysisLimitations;
      payload.datasetWarnings=[];payload.omittedDatasetWarnings=this.dataset.warnings.length;
      delete payload.datasetWarningsRole;
      payload.sourceNotesStoredLocally=true;
    }
    // Include the final envelope before checking each whole candidate against the character limit.
    payload.omittedCandidates=matching.length;payload.offerLimitPerCandidate=2;
    const selected:unknown[]=[];
    for(const c of matching.slice(0,5)) {
      const positiveEvidence=c.analysis.positiveEvidence.filter(value=>!creator.profileSignals||!value.startsWith("画像来源记录销量 ")&&!value.startsWith("来源平均观看 "));
      const limitations=c.analysis.limitations.filter(value=>!sharedAnalysisLimitations.includes(value)),gaps=c.gaps.filter(value=>!sharedExecutionGaps.includes(value));
      const item={analysis:{policyVersion:c.analysis.policyVersion,tier:c.analysis.tier,summary:c.analysis.summary,positiveEvidence:positiveEvidence.slice(0,3),limitations:limitations.slice(0,2),omittedPositiveEvidence:Math.max(0,positiveEvidence.length-3),omittedLimitations:Math.max(0,limitations.length-2)},executionReadiness:c.readiness,product:{id:c.product.id,pid:c.product.pid,title:c.product.title.slice(0,100),categories:c.product.categories,categoryFact:packetCategory(c.product.categoryFact),priceMinor:c.product.priceMinor,currency:c.product.currency,semanticRevision:c.product.semanticRevision,commercialRevision:c.product.commercialRevision,source:c.product.source},offers:c.offers.slice(0,2).map(o=>({id:o.id,campaignId:o.campaignId,accountRef:o.accountRef,version:o.version,creatorCommissionBps:o.creatorCommissionBps,agencyCommissionBps:o.agencyCommissionBps,totalCommissionBps:o.totalCommissionBps,stock:o.stock,sampleAvailable:o.sampleAvailable,sampleQuota:o.sampleQuota,startsAt:o.startsAt,endsAt:o.endsAt,cardStatus:o.cardStatus,sourceRef:o.source.ref})),sources:c.sources,features:c.features,exactObservation:(this.stmt("SELECT data FROM evidence INDEXED BY evidence_creator WHERE creator_id=? AND market=? AND pid=? AND units>0 ORDER BY units DESC LIMIT 1").all(c.creator.id,c.product.market,c.product.pid) as Row[]).map(r=>{const e=JSON.parse(r.data) as ProductEvidence;return {units:e.units,source:e.source};})[0]??null,evidenceRefs:c.evidenceRefs.slice(0,5),gaps:gaps.slice(0,8),omittedGaps:Math.max(0,gaps.length-8)};
      payload.candidates=[...selected,item];if(JSON.stringify(payload).length>PACKET_LIMIT){payload.candidates=selected;break;}selected.push(item);
    }
    if(!selected.length)throw new MatchingError(422,"context_too_large","单个候选必要上下文超过字符预算，须缩小证据引用后再准备。");
    payload.omittedCandidates=matching.length-selected.length;payload.offerLimitPerCandidate=2;
    const characters=JSON.stringify(payload).length;if(characters>PACKET_LIMIT)throw new MatchingError(422,"context_too_large","必要上下文超过字符预算。");
    const packet:ReviewPacket={id:`review-packet-${randomUUID()}`,creatorId,createdAt:this.now(),fingerprint,runId,candidates:selected.length,characters,estimatedTokens:null,modelStatus:"not_called",executable:false,executionBlocked:true,payload};this.stmt("INSERT INTO review_packets VALUES(?,?,?)").run(packet.id,fingerprint,JSON.stringify(packet));return packet;
  });}
  demoChange(productId:string,expectedRevision:number,change:"raise_price"|"lower_price"|"offer_unavailable"|"offer_available",requestId?:string):{result:ImportResult;product:MatchProduct;message:string} {return this.transaction(()=>{
    if(this.dataset.mode!=="synthetic-local")throw new MatchingError(403,"synthetic_only","离线真实数据不能使用合成事实修改功能。");
    text(productId,"productId",100);integer(expectedRevision,"expectedRevision");choice(change,["raise_price","lower_price","offer_unavailable","offer_available"],"change");const requestHash=hash({productId,expectedRevision,change});
    if(requestId){text(requestId,"requestId",150);const cached=this.stmt("SELECT request_hash,data FROM matching_requests WHERE id=?").get(requestId) as {request_hash:string;data:string}|undefined;if(cached){if(cached.request_hash!==requestHash)throw new MatchingError(409,"request_conflict","同一个请求 ID 不能用于不同修改。");const original=JSON.parse(cached.data) as {result:ImportResult;product:MatchProduct;message:string};const current=this.entity<MatchProduct>("products",productId);return {...original,product:current,message:current.commercialRevision!==original.product.commercialRevision||current.semanticRevision!==original.product.semanticRevision?"原请求已经处理；显示当前商品条件，没有重复修改。":original.message};}}
    const p=this.entity<MatchProduct>("products",productId);if(!p.id.startsWith("synthetic-product-")||!p.source.ref.startsWith("synthetic:"))throw new MatchingError(403,"synthetic_only","演示修改仅能应用于生成的合成事实。");if(p.commercialRevision!==expectedRevision)throw new MatchingError(409,"revision_conflict","商品条件已变化，请刷新后重试。");
    let batch:MatchingBatch;if(change==="raise_price"||change==="lower_price") {if(p.priceMinor===null)throw new MatchingError(409,"price_unknown","价格未知，不能将未知值当作 0 调价。");const {semanticRevision,commercialRevision,...input}=p;void semanticRevision;void commercialRevision;batch={products:[{...input,priceMinor:change==="raise_price"?p.priceMinor*2:Math.floor(p.priceMinor/2),source:{...p.source,observedAt:this.now()}}]};}else {const offers=this.offersFor(p.id);if(!offers.length)throw new MatchingError(409,"offer_missing","该商品没有可修改的演示 Offer。");batch={offers:offers.map(({version,...o})=>{void version;return {...o,stock:change==="offer_unavailable"?0:100,source:{...o.source,observedAt:this.now()}};})};}
    const result=this.importBatch(batch),response={result,product:this.entity<MatchProduct>("products",productId),message:"合成商业事实已更新；相关候选与决策包需要重新召回，未调用模型或触发发送。"};if(requestId)this.stmt("INSERT INTO matching_requests VALUES(?,?,?)").run(requestId,requestHash,JSON.stringify(response));return response;
  });}
}
