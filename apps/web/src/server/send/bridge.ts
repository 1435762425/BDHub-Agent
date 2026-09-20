import {execFile} from "node:child_process";
import {join} from "node:path";
import {projectRoot} from "../creator-identities/refresh.ts";
import {PROBE_COUNT,SEND_COUNTS,SEND_TEMPLATE_IDS} from "../../features/second-outreach/send-contracts.ts";
import type {SendAuthorization,SendBatch,SendConfig,SendCapacity,SendPreview,SendRateGap,SendSample,SendState,SendTemplateId,SendTemplateOption,SendUnknownDelivery,SendWindow} from "../../features/second-outreach/send-contracts.ts";

// 类型与档位常量在 `features/second-outreach/send-contracts.ts`（客户端组件也要用，
// 不能从这一层 import：那会把 node:child_process 打进浏览器包）。这里只留校验与调用。
export {PROBE_COUNT,SEND_COUNTS};
export type {SendAuthorization,SendBatch,SendConfig,SendCapacity,SendPreview,SendRateGap,SendSample,SendState,SendTemplateId,SendTemplateOption,SendUnknownDelivery,SendWindow};

/**
 * 发送池 → 正式发送的播种层桥接。
 *
 * 这一层只做两件事：把 `send-batch.py status`（只读预检）翻成页面要的形状，和保存
 * 「这一批多少条 / 使用哪个模板 / 要不要越界 / 窗口开不开」四类设置。**它不发任何消息**：真正的发送
 * 由执行器按批次授权跑，页面上的「确认并开始」是那一步的入口。
 */

/** Internal reasons remain detailed; the page projects them to three business outcomes. */
const LAYERS=["ready","queued","cooling","awaiting_reply","excluded","product_inactive","sent"];

function int(value:unknown,code:string,max=Number.MAX_SAFE_INTEGER):number{
 if(typeof value!=="number"||!Number.isSafeInteger(value)||value<0||value>max)throw Error(code);
 return value;
}

function text(value:unknown,max=200):string{
 if(typeof value!=="string"||!value||value.length>max)throw Error('invalid_send');
 return value;
}

function nullableNumber(value:unknown):number|null{
 if(value==null)return null;
 if(typeof value!=="number"||!Number.isFinite(value)||value<=0)throw Error('invalid_send');
 return value;
}

function sha(value:unknown):string{
 const out=text(value,64);
 if(!/^[0-9a-f]{64}$/.test(out))throw Error('invalid_send');
 return out;
}

function counts(raw:unknown,code:string):Record<string,number>{
 if(raw==null)return {};
 if(typeof raw!=="object"||Array.isArray(raw))throw Error(code);
 const out:Record<string,number>={};
 for(const [key,value] of Object.entries(raw as Record<string,unknown>)){
  if(key.length>64)throw Error(code);
  out[key]=int(value,code);
 }
 return out;
}

function validateWindow(raw:unknown):SendWindow{
 const v=(raw??{}) as Record<string,unknown>;
 const edge=(value:unknown)=>(value==null?null:text(value,5));
 if(typeof v.enabled!=="boolean"||typeof v.open!=="boolean")throw Error('invalid_send');
 return {enabled:v.enabled,open:v.open,start:edge(v.start),end:edge(v.end)};
}

function validateCapacity(raw:unknown):SendCapacity|null{
 if(raw==null)return null;
 const v=raw as Record<string,unknown>;
 return {windowSeconds:int(v.windowSeconds,'invalid_send'),limit:int(v.limit,'invalid_send'),
  used:int(v.used,'invalid_send'),remaining:int(v.remaining,'invalid_send')};
}

function validateSamples(raw:unknown):SendSample[]{
 if(!Array.isArray(raw)||raw.length>5)throw Error('invalid_send');
 if(raw.length===0)return [];
 return raw.map(row=>{
  const r=(row??{}) as Record<string,unknown>;
  const pid=text(r.pid,19);
  if(!/^\d{19}$/.test(pid))throw Error('invalid_send');
  if(typeof r.unlocked!=="boolean"||(r.sourceClass!=="A"&&r.sourceClass!=="B"))throw Error('invalid_send');
  // 发出去的那句话必须原样带上（页面要显示的就是它），以及操作者的中文对照。
  const optional=(value:unknown,max:number)=>(typeof value==="string"&&value?text(value,max):"");
  const nullableNumber=(value:unknown)=>(value==null?null:typeof value==="number"&&Number.isFinite(value)&&value>=0?value:(()=>{throw Error('invalid_send')})());
  const nullableText=(value:unknown,max:number)=>(value==null||value===""?null:text(value,max));
  const oecId=text(r.oecId,64),currentListId=text(r.currentListId,64);
  if(!/^\d{1,64}$/.test(oecId)||!/^\d{1,64}$/.test(currentListId))throw Error('invalid_send');
  const gmv=nullableText(r.gmv,80);if(gmv!==null&&!/^\d+(?:\.\d+)?$/.test(gmv))throw Error('invalid_send');
  return {handle:text(r.handle,64),oecId,pid,sourceClass:r.sourceClass,sourceRank:nullableNumber(r.sourceRank),units:nullableNumber(r.units),gmv,
   videoViews:nullableNumber(r.videoViews),videoId:nullableText(r.videoId,100),videoReleasedAt:nullableText(r.videoReleasedAt,40),name:text(r.name,120),
   nameZh:optional(r.nameZh,120),nameSource:text(r.nameSource,16),
   messageIt:optional(r.messageIt,600),messageZh:optional(r.messageZh,600),template:optional(r.template,24),
   creatorPercent:text(r.creatorPercent,8),publicPercent:text(r.publicPercent,8),
   campaignId:text(r.campaignId,24),catalogSource:text(r.catalogSource,16),currentListId,unlocked:r.unlocked};
 });
}

/** 卡的佣金 vs 计划：缺这一块按"没有差距"读（老回答），写坏才拒。 */
function validateRateGap(raw:unknown):SendRateGap{
 const empty:SendRateGap={same:0,lower:0,lowerByOne:0,higher:0,noCard:0,examples:[]};
 if(raw==null)return empty;
 if(typeof raw!=="object"||Array.isArray(raw))throw Error('invalid_send');
 const v=raw as Record<string,unknown>;
 const rows=Array.isArray(v.examples)?v.examples:[];
 if(rows.length>5)throw Error('invalid_send');
 return {same:int(v.same??0,'invalid_send'),lower:int(v.lower??0,'invalid_send'),
  lowerByOne:int(v.lowerByOne??0,'invalid_send'),higher:int(v.higher??0,'invalid_send'),
  noCard:int(v.noCard??0,'invalid_send'),
  examples:rows.map(row=>{
   const r=(row??{}) as Record<string,unknown>;
   return {pid:text(r.pid,19),listName:text(r.listName,80),cardPercent:text(r.cardPercent,8),
    planPercent:text(r.planPercent,8),campaignId:text(r.campaignId,24)};
  })};
}

function validateConfig(raw:unknown):SendConfig{
 const v=(raw??{}) as Record<string,unknown>;
 const count=int(v.count,'invalid_send',2000);
 if(count<1)throw Error('invalid_send');
 if(typeof v.widen!=="boolean"||typeof v.windowEnabled!=="boolean")throw Error('invalid_send');
 if(!Array.isArray(v.window)||v.window.length!==2)throw Error('invalid_send');
 const edge=(value:unknown)=>{
  const t=text(value,5);
  if(!/^([01]\d|2[0-4]):[0-5]\d$/.test(t))throw Error('invalid_send');
  return t;
 };
 const template=text(v.template??"standard",40) as SendTemplateId;
 if(!SEND_TEMPLATE_IDS.includes(template as typeof SEND_TEMPLATE_IDS[number])&&!/^custom-[a-f0-9]{24}$/.test(template))throw Error('invalid_send');
 return {count,widen:v.widen,windowEnabled:v.windowEnabled,window:[edge(v.window[0]),edge(v.window[1])],template};
}

function validateTemplates(raw:unknown):SendTemplateOption[]{
 if(!Array.isArray(raw)||raw.length<1||raw.length>200)throw Error('invalid_send');
 const seen=new Set<string>();
 const rows=raw.map(value=>{const row=(value??{}) as Record<string,unknown>;const id=text(row.id,40) as SendTemplateId;
  if((!SEND_TEMPLATE_IDS.includes(id as typeof SEND_TEMPLATE_IDS[number])&&!/^custom-[a-f0-9]{24}$/.test(id))||seen.has(id)||
   typeof row.builtIn!=="boolean"||(row.state!=="active"&&row.state!=="archived")||!Array.isArray(row.parameters))throw Error('invalid_send');seen.add(id);
  const parameters=row.parameters.map(item=>text(item,40));
  return {id,name:text(row.name,60),description:text(row.description,160),bodyIt:text(row.bodyIt,600),
   revision:int(row.revision,'invalid_send',100000),builtIn:row.builtIn,state:row.state as "active"|"archived",parameters};});
 return rows;
}

function validateAuthorization(raw:unknown):SendAuthorization{
 if(!raw||typeof raw!=="object"||Array.isArray(raw))throw Error('invalid_send');
 const v=raw as Record<string,unknown>;
 const allowed=["source","scope","maxPeople","requestedPeople","reservePeople","frozenPeople","reservePolicy","widenLocalGate","sendWindow","messageTemplate",
  "institutionNewContactRollingCap","materialPolicy","note"];
 if(Object.keys(v).some(key=>!allowed.includes(key))||v.source!=="current_user_request"||
   v.scope!=="pool_to_send"||v.materialPolicy!=="frozen-current-binding-v1"||
   typeof v.widenLocalGate!=="boolean")throw Error('invalid_send');
 let sendWindow:[string,string]|null=null;
 if(v.sendWindow!=null){
  if(!Array.isArray(v.sendWindow)||v.sendWindow.length!==2)throw Error('invalid_send');
  sendWindow=[text(v.sendWindow[0],5),text(v.sendWindow[1],5)];
 }
 const maxPeople=int(v.maxPeople,'invalid_send',2000);
 const reservePeople=int(v.reservePeople??0,'invalid_send',200);
 const frozenPeople=int(v.frozenPeople??maxPeople,'invalid_send',2200);
 const reservePolicy=v.reservePolicy??(reservePeople?"ceil-10-percent-v1":"none");
 const messageTemplate=text(v.messageTemplate??"standard",40) as SendTemplateId;
 if(frozenPeople!==maxPeople+reservePeople||(reservePolicy!=="ceil-10-percent-v1"&&reservePolicy!=="none")||
   (reservePeople>0)!==(reservePolicy==="ceil-10-percent-v1")||
   (!SEND_TEMPLATE_IDS.includes(messageTemplate as typeof SEND_TEMPLATE_IDS[number])&&!/^custom-[a-f0-9]{24}$/.test(messageTemplate)))throw Error('invalid_send');
 return {source:v.source,scope:v.scope,maxPeople,
  requestedPeople:int(v.requestedPeople,'invalid_send',2000),reservePeople,frozenPeople,reservePolicy,widenLocalGate:v.widenLocalGate,
  sendWindow,messageTemplate,institutionNewContactRollingCap:int(v.institutionNewContactRollingCap,'invalid_send',2000),
  materialPolicy:v.materialPolicy,note:text(v.note,240)};
}

function validateBatch(raw:unknown):SendBatch|null{
 if(raw==null)return null;
 if(typeof raw!=="object"||Array.isArray(raw))throw Error('invalid_send');
 const v=raw as Record<string,unknown>;
 const batchId=text(v.batchId,120),batchRequestId=text(v.requestId,120),state=text(v.state,48);
 if(!/^[A-Za-z0-9][A-Za-z0-9._:-]{7,119}$/.test(batchId)||
   !/^[A-Za-z0-9][A-Za-z0-9._:-]{7,119}$/.test(batchRequestId))throw Error('invalid_send');
 let runtime:SendBatch["runtime"]=null;
 if(v.runtime!=null){
  if(typeof v.runtime!=="object"||Array.isArray(v.runtime))throw Error('invalid_send');
  const r=v.runtime as Record<string,unknown>;
  runtime={pid:r.pid==null?null:int(r.pid,'invalid_send',2**31-1),
   seenAt:nullableNumber(r.seenAt)??0,phase:text(r.phase,64)};
 }
 const target=int(v.target,'invalid_send',2000),attempted=int(v.attempted??v.target,'invalid_send',2200);
 const reserveTotal=int(v.reserveTotal??0,'invalid_send',200),reservePromoted=int(v.reservePromoted??0,'invalid_send',200);
 const reserveRemaining=int(v.reserveRemaining??reserveTotal-reservePromoted,'invalid_send',200);
 if(!Array.isArray(v.unknownDeliveries)||v.unknownDeliveries.length>20)throw Error('invalid_send');
 const unknownDeliveries:SendUnknownDelivery[]=v.unknownDeliveries.map(rawUnknown=>{const row=rawUnknown as Record<string,unknown>;if(!row||typeof row!=="object"||!row.parts||typeof row.parts!=="object"||Array.isArray(row.parts))throw Error('invalid_send');const parts:Record<string,string>={};for(const [kind,state] of Object.entries(row.parts as Record<string,unknown>)){if(!["card","text"].includes(kind)||typeof state!=="string"||state.length>32)throw Error('invalid_send');parts[kind]=state;}const oecId=text(row.oecId,64),pid=text(row.pid,19);if(!/^\d{1,64}$/.test(oecId)||!/^\d{19}$/.test(pid))throw Error('invalid_send');return {deliveryId:requestId(row.deliveryId),creatorId:text(row.creatorId,120),oecId,pid,parts};});
 const out:SendBatch={batchId,requestId:batchRequestId,previewHash:sha(v.previewHash),
  revision:int(v.revision,'invalid_send',1000000),state,target,attempted,reserveTotal,reservePromoted,reserveRemaining,
  counts:counts(v.counts,'invalid_send'),config:validateConfig(v.config),
  authorization:validateAuthorization(v.authorization),authorizedAt:nullableNumber(v.authorizedAt),
  stopRequestedAt:nullableNumber(v.stopRequestedAt),createdAt:nullableNumber(v.createdAt)??0,runtime,unknownDeliveries};
 if(v.workerPid!==undefined)out.workerPid=int(v.workerPid,'invalid_send',2**31-1);
 if(v.duplicate!==undefined){if(typeof v.duplicate!=="boolean")throw Error('invalid_send');out.duplicate=v.duplicate;}
 if(Object.values(out.counts).reduce((a,b)=>a+b,0)!==out.attempted||out.attempted!==out.target+out.reservePromoted||
   out.reservePromoted+out.reserveRemaining!==out.reserveTotal)throw Error('invalid_send');
 return out;
}

export function validateSendState(value:unknown):SendState{
 if(!value||typeof value!=="object")throw Error('invalid_send');
 const v=value as Record<string,unknown>;
 if(v.market!=="it"||v.account!=="acc6")throw Error('invalid_send');
 const config=validateConfig(v.config);
 const templates=validateTemplates(v.templates);
 if(!templates.some(item=>item.id===config.template&&item.state==="active"))throw Error('invalid_send');
 const raw=(v.preview??{}) as Record<string,unknown>;
 if(raw.available!==true){
  return {market:"it",account:"acc6",available:false,config,templates,pool:{counts:{},layers:{}},
   preview:{available:false,requested:int(raw.requested??0,'invalid_send'),
    reserveRequested:int(raw.reserveRequested??0,'invalid_send',200),required:int(raw.required??0,'invalid_send',2200),
    sendable:0,reserveReady:0,frozenTotal:0,fullPreparation:false,samples:[],
    nameQuality:{},skipped:{},rateGap:validateRateGap(raw.rateGap),capacity:null,
    window:validateWindow(raw.window),widen:config.widen,positions:0,readyAvailable:0,
    previewHash:null,authorization:null},batch:validateBatch(v.batch)};
 }
 const requested=int(raw.requested,'invalid_send',2000),reserveRequested=int(raw.reserveRequested,'invalid_send',200);
 const required=int(raw.required,'invalid_send',2200),sendable=int(raw.sendable,'invalid_send',2000);
 const reserveReady=int(raw.reserveReady,'invalid_send',200),frozenTotal=int(raw.frozenTotal,'invalid_send',2200);
 if(typeof raw.fullPreparation!=="boolean"||required!==requested+reserveRequested||
   frozenTotal!==sendable+reserveReady||raw.fullPreparation!==(sendable===requested&&reserveReady===reserveRequested))throw Error('invalid_send');
 const positions=int(raw.positions,'invalid_send',4000);
 const skipped=counts(raw.skipped,'invalid_send');
 const authorization=raw.authorization==null?null:validateAuthorization(raw.authorization);
 if(!authorization||authorization.requestedPeople!==requested||authorization.maxPeople!==sendable||
   authorization.reservePeople!==reserveRequested||authorization.frozenPeople!==frozenTotal||
   reserveRequested!==Math.ceil(requested*0.1))throw Error('invalid_send');
 const preview:SendPreview={available:true,requested,reserveRequested,required,sendable,reserveReady,frozenTotal,
  fullPreparation:raw.fullPreparation,
  positions,readyAvailable:int(raw.readyAvailable,'invalid_send'),samples:validateSamples(raw.samples),
  nameQuality:counts(raw.nameQuality,'invalid_send'),skipped,rateGap:validateRateGap(raw.rateGap),
  capacity:validateCapacity(raw.capacity),window:validateWindow(raw.window),widen:raw.widen===true,
  previewHash:raw.previewHash==null?null:sha(raw.previewHash),authorization};
 // 池子是个划分：扫到的每个槽位要么进这一批，要么有具名原因。对不平就是桥接读错了，整包拒。
 const total=frozenTotal+Object.values(skipped).reduce((a,b)=>a+b,0);
 if(total!==positions)throw Error('invalid_send');
 const pool=(v.pool??{}) as Record<string,unknown>;
 const layers=counts(pool.layers,'invalid_send');
 if(Object.keys(layers).some(name=>!LAYERS.includes(name)))throw Error('invalid_send');
 return {market:"it",account:"acc6",available:true,config,templates,preview,pool:{counts:counts(pool.counts,'invalid_send'),layers},
  batch:validateBatch(v.batch)};
}

function run(args:string[]):Promise<SendState>{
 const root=projectRoot();
 return new Promise((resolve,reject)=>{
  execFile(join(root,".venv/bin/python"),
   [join(root,"scripts/send-batch.py"),...args],
   {cwd:root,timeout:180000,maxBuffer:8*1024*1024,env:{...process.env,PYTHONDONTWRITEBYTECODE:"1"}},
   (error,out)=>{
    try{
     const parsed=JSON.parse(out) as unknown;
     if(parsed&&typeof parsed==="object"&&!Array.isArray(parsed)&&
       typeof (parsed as Record<string,unknown>).error==="string"){
      reject(Error(String((parsed as Record<string,unknown>).error)));return;
     }
     if(error)throw error;
     resolve(validateSendState(parsed));
    }
    catch{reject(Error('send_batch_unavailable'));}
   });
 });
}

export function readSendBatch():Promise<SendState>{return run(["status"]);}

/** 顺手校验一遍请求：字段多一个就拒，`save` 只接受明确的批次设置。 */
export type SendRequest={action:"save";config:Partial<SendConfig>}|
 {action:"freeze";requestId:string;expectedPreviewHash:string}|
 {action:"start";batchId:string;expectedRevision:number;confirmed:true}|
 {action:"stop";batchId:string;expectedRevision:number}|
 {action:"reconcile";batchId:string;deliveryId:string;expectedRevision:number;confirmed:true};

const requestId=(value:unknown)=>{
 const out=text(value,120);
 if(!/^[A-Za-z0-9][A-Za-z0-9._:-]{7,119}$/.test(out))throw Error('invalid_send_request');
 return out;
};

export function validateSendRequest(body:unknown):SendRequest{
 if(!body||typeof body!=="object")throw Error('invalid_send_request');
 const v=body as Record<string,unknown>;
 if(v.action==="freeze"){
  if(Object.keys(v).some(key=>!["action","requestId","expectedPreviewHash"].includes(key)))throw Error('invalid_send_request');
  return {action:"freeze",requestId:requestId(v.requestId),expectedPreviewHash:sha(v.expectedPreviewHash)};
 }
 if(v.action==="start"){
  if(Object.keys(v).some(key=>!["action","batchId","expectedRevision","confirmed"].includes(key))||v.confirmed!==true)
   throw Error('invalid_send_request');
  return {action:"start",batchId:requestId(v.batchId),expectedRevision:int(v.expectedRevision,'invalid_send_request'),confirmed:true};
 }
 if(v.action==="stop"){
  if(Object.keys(v).some(key=>!["action","batchId","expectedRevision"].includes(key)))throw Error('invalid_send_request');
  return {action:"stop",batchId:requestId(v.batchId),expectedRevision:int(v.expectedRevision,'invalid_send_request')};
 }
 if(v.action==="reconcile"){
  if(Object.keys(v).some(key=>!["action","batchId","deliveryId","expectedRevision","confirmed"].includes(key))||v.confirmed!==true)
   throw Error('invalid_send_request');
  return {action:"reconcile",batchId:requestId(v.batchId),deliveryId:requestId(v.deliveryId),expectedRevision:int(v.expectedRevision,'invalid_send_request'),confirmed:true};
 }
 if(v.action!=="save")throw Error('invalid_send_request');
 if(Object.keys(v).some(key=>!["action","config"].includes(key)))throw Error('invalid_send_request');
 const raw=(v.config??{}) as Record<string,unknown>;
 if(Object.keys(raw).some(key=>!["count","widen","windowEnabled","window","template"].includes(key)))
  throw Error('invalid_send_request');
 // 任意明确目标 N 均可；500/1000/600 只是页面快捷档，不是业务上限。
 if(raw.count!==undefined){
  const count=int(raw.count,'invalid_send_request',2000);
  if(count<1)throw Error('invalid_send_request');
 }
 return {action:"save",config:validateConfig({...raw,
  count:raw.count??500,widen:raw.widen??false,windowEnabled:raw.windowEnabled??false,
  window:raw.window??["09:00","24:00"],template:raw.template??"standard"})};
}

export function saveSendConfig(config:Partial<SendConfig>):Promise<SendState>{
 return run(["save","--json",JSON.stringify(config)]);
}

export function freezeSendBatch(requestIdValue:string,expectedPreviewHash:string):Promise<SendState>{
 return run(["freeze","--request-id",requestIdValue,"--expected-preview-hash",expectedPreviewHash]);
}

export function startSendBatch(batchId:string,expectedRevision:number):Promise<SendState>{
 return run(["start","--batch-id",batchId,"--expected-revision",String(expectedRevision),"--confirmed"]);
}

export function stopSendBatch(batchId:string,expectedRevision:number):Promise<SendState>{
 return run(["stop","--batch-id",batchId,"--expected-revision",String(expectedRevision)]);
}

export function reconcileSendBatch(batchId:string,deliveryId:string,expectedRevision:number):Promise<SendState>{
 return run(["reconcile","--batch-id",batchId,"--delivery-id",deliveryId,"--expected-revision",String(expectedRevision),"--confirmed"]);
}
