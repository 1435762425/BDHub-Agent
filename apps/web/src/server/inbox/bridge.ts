import {execFile} from "node:child_process";
import {join} from "node:path";
import {projectRoot} from "../creator-identities/refresh.ts";
import {enabledMarket} from "../markets/registry.ts";

/** 收信监控与按天统计。监控本身是既有的只读脚本，这里只读它的状态、并启停它。 */
export type InboxConfig={limit:number;interval:number};
/** 监控一轮做了什么。`errorCode` 就是"这一轮为什么没读到东西"。 */
export type InboxStep={processed:number;added:number;historical:number;liveReplies:number;
 indexedTargets:number;serviceDecisions:number;errorCode:string|null;state:string;checkedAt:number;
 conversations:number;events:number;historicalEvents:number;gaps:number;pendingContent:number;
 lastCheckedAt:number;automaticRepliesEnabled:boolean};
export type InboxRun={name:string;label:string;pid:number;startedAt:number;log:string;
 config:InboxConfig;platformWrites:boolean;running:boolean;stopping:boolean;progress:InboxStep|null};
/** 一天的账。触达只算回查确认；回复与橱窗排除历史补录。 */
export type InboxDay={date:string;cards:number;texts:number;creators:number;unconfirmed:number;
 replies:number;showcase:number;ourMessages:number;autoReplies:number;casesOpened:number};
export type InboxTotals=Omit<InboxDay,"date">;
export type InboxState={available:boolean;market:string;config:InboxConfig;configInvalid:boolean;run:InboxRun|null;
 today:InboxDay|null;openCases:number;timezone:string;totals:InboxTotals;days:InboxDay[];saved?:boolean};
export type InboxDetailKind="delivery"|"reply"|"showcase"|"auto_reply"|"case";
export type InboxDetailItem={kind:InboxDetailKind;occurredAt:number;ref:string;creatorId:string|null;
 oec:string|null;handle:string|null;handleAtEvent:string|null;pid:string|null;status:string|null;
 product:string|null;creatorPercent:string|null;catalogSource:string|null;text:string|null;
 format:"text"|"attachment_or_unsupported"|"not_fetched"|null;textState:string|null};
export type InboxDayDetail={available:boolean;market:string;date:string;timezone:string;summary:InboxDay|null;
 total:number;offset:number;limit:number;nextOffset:number|null;items:InboxDetailItem[];platformWrites:false};
export type InboxQuery={view:"status";market:string;days:7|14|30}|{view:"detail";market:string;date:string;offset:number;limit:number};

const STAT_KEYS=(['cards','texts','creators','unconfirmed','replies','showcase','ourMessages','autoReplies','casesOpened'] as const);

// 上一轮核验最多 12 个会话、两轮之间至少 30 秒——沿用脚本自己的取值域（收信吃的是 ACC6 live 锁，
// 轮次开大只会把补身份/发送挤得更久）。
const INTERVAL_MIN=30,INTERVAL_MAX=3600,LIMIT_MAX=12;
const DAYS_MAX=92;

export function validateInboxConfig(value:unknown):InboxConfig{
 if(!value||typeof value!=="object"||Array.isArray(value))throw Error('invalid_inbox');
 const v=value as Record<string,unknown>;
 // A misspelled key would be ignored here and the monitor would start with defaults instead.
 if(Object.keys(v).some(key=>key!=="limit"&&key!=="interval"))throw Error('invalid_inbox');
 const whole=(raw:unknown,low:number,high:number)=>{if(typeof raw!=="number"||!Number.isSafeInteger(raw)||raw<low||raw>high)throw Error('invalid_inbox');return raw;};
 return {limit:whole(v.limit,1,LIMIT_MAX),interval:whole(v.interval,INTERVAL_MIN,INTERVAL_MAX)};
}

const count=(raw:unknown)=>{if(typeof raw!=="number"||!Number.isSafeInteger(raw)||raw<0)throw Error('invalid_inbox');return raw;};
const clock=(raw:unknown)=>{if(typeof raw!=="number"||!Number.isFinite(raw)||raw<0)throw Error('invalid_inbox');return raw;};
// 北京日：固定 +08:00，中国没有夏令时；格式钉死，免得一个手写的日期混进日历。
const DATE=/^\d{4}-\d{2}-\d{2}$/;
const DETAIL_KINDS=new Set<InboxDetailKind>(["delivery","reply","showcase","auto_reply","case"]);
const validDate=(value:string)=>DATE.test(value)&&new Date(`${value}T00:00:00Z`).toISOString().slice(0,10)===value;

function day(raw:unknown):InboxDay{
 if(!raw||typeof raw!=="object"||Array.isArray(raw))throw Error('invalid_inbox');
 const v=raw as Record<string,unknown>;
 if(typeof v.date!=="string"||!DATE.test(v.date))throw Error('invalid_inbox');
 return {date:v.date,cards:count(v.cards),texts:count(v.texts),creators:count(v.creators),
  unconfirmed:count(v.unconfirmed),replies:count(v.replies),showcase:count(v.showcase),
  ourMessages:count(v.ourMessages),autoReplies:count(v.autoReplies),casesOpened:count(v.casesOpened)};
}

function validateStep(value:unknown):InboxStep|null{
 if(value==null)return null;
 if(typeof value!=="object"||Array.isArray(value))throw Error('invalid_inbox');
 const v=value as Record<string,unknown>;
 // 一轮读不到东西时必须说得出原因；说不出来就不是一个状态文件，而是坏包。
 if(v.errorCode!=null&&typeof v.errorCode!=="string")throw Error('invalid_inbox');
 return {processed:count(v.processed),added:count(v.added),historical:count(v.historical),
  liveReplies:count(v.liveReplies),indexedTargets:count(v.indexedTargets),
  serviceDecisions:count(v.serviceDecisions),errorCode:v.errorCode==null?null:v.errorCode,
  state:typeof v.state==="string"?v.state:"",checkedAt:clock(v.checkedAt),
  conversations:count(v.conversations),events:count(v.events),
  historicalEvents:count(v.historicalEvents),gaps:count(v.gaps),
  pendingContent:count(v.pendingContent),lastCheckedAt:clock(v.lastCheckedAt),
  automaticRepliesEnabled:v.automaticRepliesEnabled===true};
}

function validateRun(value:unknown,fallback:InboxConfig):InboxRun|null{
 if(value==null)return null;
 if(typeof value!=="object"||Array.isArray(value))throw Error('invalid_inbox');
 const v=value as Record<string,unknown>;
 if(v.name!=="inbox"||typeof v.running!=="boolean"||typeof v.pid!=="number"||typeof v.label!=="string")
  throw Error('invalid_inbox');
 // 运行记录是历史：规则收紧后旧 config 不该让整张卡消失，退回当前 config 即可。
 let config:InboxConfig;try{config=validateInboxConfig(v.config);}catch{config=fallback;}
 return {name:"inbox",label:v.label,pid:v.pid,startedAt:typeof v.startedAt==="number"?v.startedAt:0,
  log:typeof v.log==="string"?v.log:"",config,
  // 收信只读平台，永远不该报写入。
  platformWrites:v.platformWrites===true,running:v.running,stopping:v.stopping===true,
  progress:validateStep(v.progress)};
}

export function validateInbox(value:unknown,expectedMarket?:string):InboxState{
 if(!value||typeof value!=="object"||Array.isArray(value))throw Error('invalid_inbox');
 const v=value as Record<string,unknown>;
 const market=typeof v.market==="string"&&enabledMarket(v.market)&&(!expectedMarket||v.market===expectedMarket)?v.market:(()=>{throw Error('invalid_inbox')})();
 const config=validateInboxConfig(v.config);
 const run=validateRun(v.run,config);
 const saved=typeof v.saved==="boolean"?{saved:v.saved}:{};
 const configInvalid=v.configInvalid===true;
 const zero:InboxTotals={cards:0,texts:0,creators:0,unconfirmed:0,replies:0,showcase:0,ourMessages:0,autoReplies:0,casesOpened:0};
 if(v.available!==true)return {available:false,market,config,configInvalid,run,today:null,openCases:0,timezone:"Asia/Shanghai",totals:zero,days:[],...saved};
 if(typeof v.timezone!=="string"||!v.timezone)throw Error('invalid_inbox');
 const rawDays=v.days;
 if(!Array.isArray(rawDays)||rawDays.length>DAYS_MAX)throw Error('invalid_inbox');
 const days=rawDays.map(day);
 if(new Set(days.map(row=>row.date)).size!==days.length)throw Error('invalid_inbox');
 const totals=v.totals&&typeof v.totals==="object"&&!Array.isArray(v.totals)
  ?day({...v.totals as Record<string,unknown>,date:"1970-01-01"}) : null;
 if(!totals||STAT_KEYS.some(key=>totals[key]!==days.reduce((sum,row)=>sum+row[key],0)))throw Error('invalid_inbox');
 const today=v.today==null?null:day(v.today);
 if(today){
  const row=days.find(item=>item.date===today.date);
  if(!row||STAT_KEYS.some(key=>row[key]!==today[key]))throw Error('invalid_inbox');
 }
 return {available:true,market,config,configInvalid,run,
  today,openCases:count(v.openCases),timezone:v.timezone,
  totals:totals?(({date,...rest})=>rest)(totals):zero,
  days,...saved};
}

function optionalText(raw:unknown,max:number):string|null{
 if(raw==null)return null;
 if(typeof raw!=="string"||!raw.length||raw.length>max)throw Error('invalid_inbox_detail');
 return raw;
}

export function validateInboxDayDetail(value:unknown,expectedMarket?:string):InboxDayDetail{
 if(!value||typeof value!=="object"||Array.isArray(value))throw Error('invalid_inbox_detail');
 const v=value as Record<string,unknown>;
 const market=typeof v.market==="string"&&enabledMarket(v.market)&&(!expectedMarket||v.market===expectedMarket)?v.market:(()=>{throw Error('invalid_inbox_detail')})();
 if(typeof v.date!=="string"||!validDate(v.date)||typeof v.timezone!=="string"||!v.timezone)
  throw Error('invalid_inbox_detail');
 const offset=count(v.offset),limit=count(v.limit),total=count(v.total);
 if(offset>5000||limit<1||limit>100||typeof v.platformWrites!=="boolean"||v.platformWrites)
  throw Error('invalid_inbox_detail');
 if(v.available!==true){
  if(total!==0||v.summary!=null||v.nextOffset!=null||!Array.isArray(v.items)||v.items.length)throw Error('invalid_inbox_detail');
  return {available:false,market,date:v.date,timezone:v.timezone,summary:null,total:0,
   offset,limit,nextOffset:null,items:[],platformWrites:false};
 }
 if(!Array.isArray(v.items)||v.items.length>limit)throw Error('invalid_inbox_detail');
 const summary=v.summary==null?null:day(v.summary);
 if(!summary||summary.date!==v.date||total!==summary.cards+summary.unconfirmed+summary.replies+
  summary.showcase+summary.autoReplies+summary.casesOpened)throw Error('invalid_inbox_detail');
 const items=v.items.map(raw=>{
  if(!raw||typeof raw!=="object"||Array.isArray(raw))throw Error('invalid_inbox_detail');
  const item=raw as Record<string,unknown>;
  if(typeof item.kind!=="string"||!DETAIL_KINDS.has(item.kind as InboxDetailKind))throw Error('invalid_inbox_detail');
  let format:InboxDetailItem["format"]=null;
  if(item.format==="text"||item.format==="attachment_or_unsupported"||item.format==="not_fetched")format=item.format;
  else if(item.format!=null)throw Error('invalid_inbox_detail');
  return {kind:item.kind as InboxDetailKind,occurredAt:count(item.occurredAt),ref:optionalText(item.ref,200)!,
   creatorId:optionalText(item.creatorId,200),oec:optionalText(item.oec,40),handle:optionalText(item.handle,100),
   handleAtEvent:optionalText(item.handleAtEvent,100),pid:optionalText(item.pid,40),status:optionalText(item.status,80),
   product:optionalText(item.product,300),creatorPercent:optionalText(item.creatorPercent,32),
   catalogSource:optionalText(item.catalogSource,40),text:optionalText(item.text,4000),
   format,textState:optionalText(item.textState,80)};
 });
 if(items.some(item=>!item.ref)||offset+items.length>total)throw Error('invalid_inbox_detail');
 const nextOffset=v.nextOffset==null?null:count(v.nextOffset);
 const expected=offset+items.length<total?offset+items.length:null;
 if(nextOffset!==expected)throw Error('invalid_inbox_detail');
 return {available:true,market,date:v.date,timezone:v.timezone,summary,total,offset,limit,nextOffset,items,platformWrites:false};
}

export function parseInboxQuery(url:string):InboxQuery{
 const params=new URL(url).searchParams;
 const market=params.get('market');if(params.getAll('market').length!==1||!market||!enabledMarket(market))throw Error('invalid_inbox_query');
 if([...params.keys()].every(key=>key==='market'||key==='days')){if(params.getAll('days').length>1)throw Error('invalid_inbox_query');const raw=params.get('days')??'14';if(raw!=='7'&&raw!=='14'&&raw!=='30')throw Error('invalid_inbox_query');return {view:'status',market,days:Number(raw) as 7|14|30};}
 if([...params.keys()].some(key=>!['market','date','offset','limit'].includes(key))||
  ['date','offset','limit'].some(key=>params.getAll(key).length>1))throw Error('invalid_inbox_query');
 const date=params.get('date');
 if(!date||!validDate(date))
  throw Error('invalid_inbox_query');
 const integer=(key:string,fallback:number,max:number)=>{const raw=params.get(key);if(raw==null)return fallback;
  if(!/^(0|[1-9]\d*)$/.test(raw))throw Error('invalid_inbox_query');const value=Number(raw);
  if(!Number.isSafeInteger(value)||value>max)throw Error('invalid_inbox_query');return value;};
 const offset=integer('offset',0,5000),limit=integer('limit',50,100);
 if(limit<1)throw Error('invalid_inbox_query');
 return {view:"detail",market,date,offset,limit};
}

function runMonitor(args:string[]):Promise<unknown>{
 const root=projectRoot();
 return new Promise((resolve,reject)=>{
  execFile(join(root,".venv/bin/python"),[join(root,"scripts/inbox-monitor.py"),...args],
   {cwd:root,timeout:60000,maxBuffer:2*1024*1024,env:{...process.env,PYTHONDONTWRITEBYTECODE:"1"}},(error,out)=>{
    let parsed:unknown;
    try{parsed=JSON.parse(out);}
    catch{reject(Error('inbox_unavailable'));return;}
    // 只有"错误码一个键"的裸信封才是失败；带 error 字段的成功回答不算。
    if(parsed&&typeof parsed==="object"&&Object.keys(parsed).length===1&&typeof (parsed as {error?:unknown}).error==="string"&&(parsed as {error:string}).error!==""){
     reject(Error((parsed as {error:string}).error));return;}
    resolve(parsed);
   });
 });
}

export async function readInbox(market:string,days:7|14|30=14):Promise<InboxState>{return validateInbox(await runMonitor(["status","--market",market,"--days",String(days)]),market);}
export async function readInboxDay(market:string,date:string,offset:number,limit:number):Promise<InboxDayDetail>{
 return validateInboxDayDetail(await runMonitor(["detail","--market",market,"--date",date,"--offset",String(offset),"--limit",String(limit)]),market);
}
export function saveInboxConfig(market:string,config:InboxConfig):Promise<InboxState>{
 return runMonitor(["save","--market",market,"--json",JSON.stringify(config)]).then(value=>validateInbox(value,market));
}

/** job-run.py 的拒绝要原样带出来；它的成功回答是全部作业，不是这一个。 */
function runJob(args:string[]):Promise<void>{
 const root=projectRoot();
 return new Promise((resolve,reject)=>{
  execFile(join(root,".venv/bin/python"),[join(root,"scripts/job-run.py"),...args],
   {cwd:root,timeout:60000,maxBuffer:1024*1024,env:{...process.env,PYTHONDONTWRITEBYTECODE:"1"}},(error,out)=>{
    let parsed:unknown;
    try{parsed=JSON.parse(out);}
    catch{reject(Error('inbox_unavailable'));return;}
    if(parsed&&typeof parsed==="object"&&Object.keys(parsed).length===1&&typeof (parsed as {error?:unknown}).error==="string"&&(parsed as {error:string}).error!==""){
     reject(Error((parsed as {error:string}).error));return;}
    resolve();
   });
 });
}

/** 拉起只读监控，然后回读一次，让页面只看到一种形状。 */
export async function startInboxRun(market:string,config:InboxConfig):Promise<InboxState>{
 if(market!=="it")throw Error("market_inbox_control_unavailable");
 await runJob(["start","--name","inbox","--json",JSON.stringify(config)]);
 return readInbox(market);
}

/** 让监控在**这一轮跑完之后**停下，不打断正在读的会话。 */
export async function stopInboxRun(market:string):Promise<InboxState>{
 if(market!=="it")throw Error("market_inbox_control_unavailable");
 await runJob(["stop","--name","inbox"]);
 return readInbox(market);
}

export function validateInboxRequest(value:unknown):
 {action:"save";market:string;config:InboxConfig}|{action:"start";market:string;config:InboxConfig}|{action:"stop";market:string}{
 if(!value||typeof value!=="object"||Array.isArray(value))throw Error('invalid_inbox_request');
 const v=value as Record<string,unknown>;
 const market=typeof v.market==="string"&&enabledMarket(v.market)?v.market:(()=>{throw Error('invalid_inbox_request')})();
 if(v.action==="stop"){
  // 停止不需要参数；夹带其它字段的是坏请求，不是停止。
  if(Object.keys(v).some(key=>key!=="action"&&key!=="market"))throw Error('invalid_inbox_request');
  return {action:"stop",market};
 }
 if(v.action!=="save"&&v.action!=="start")throw Error('invalid_inbox_request');
 if(Object.keys(v).some(key=>!['action','market','config'].includes(key)))throw Error('invalid_inbox_request');
 try{return {action:v.action,market,config:validateInboxConfig(v.config)};}
 catch{throw Error('invalid_inbox_request');}
}
