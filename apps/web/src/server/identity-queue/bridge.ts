import {execFile} from "node:child_process";
import {join} from "node:path";
import {projectRoot} from "../creator-identities/refresh.ts";

/** The stage that turns a Kalodata handle into a platform OECID, and therefore into a pool position. */
export type IdentityConfig={batchSize:number;cohortSize:number};
// Why the last round failed, in the worker's own words. A bare `internal_error` tells the operator
// nothing they can act on, so the driver's captured traceback travels to the page with it.
export type IdentityError={round:number;detail:string};
// 已领是**线索条数**，找到/搜索不到是**达人个数**：一个达人名下可以挂很多条线索。这一块就是对账。
export type IdentityWalk={settled:number;newHandles:number;repeats:number};
/** 达人身份的唯一单位是**去重 handle**，而且三项互斥；线索级数字只解释"本次在干什么"。 */
/** 被挡住的原因：'request_or_signer_error' 这类说明"根本没拿到平台的回答"。 */
export type IdentityBlockedReason={reason:string;count:number};
export type IdentityByCreator={handles:number;resolved:number;unresolved:number;
 /** 「被挡住」＝问过但没拿到平台的真实回答（请求/签名失败、账号起不来、被远端挡回）。可重试。 */
 blocked:number;unknown:number;blockedReasons:IdentityBlockedReason[];
 /** 可达位置＝达人×商品：一位达人有了 OECID，他名下的所有线索商品就都是位置（达人级一次）。 */
 positions:number;leads:number;reconciled:boolean};
// Two jobs publish different steps, so this is the identity driver's shape, not the link driver's.
export type IdentityProgress={startedAt:number;updatedAt:number;rounds:number;limit:number;cohortSize:number;
 pendingAtStart:number;pending:number;claimed:number;lastTargets:number;found:number;
 notFound:number;stopReason:string|null;
 // 正在跑第几轮、这一轮何时开始：一轮几十秒，少了这两个字段进度条就只能一动不动。
 roundRunning:number;roundStartedAt:number|null;retry:number;errors:IdentityError[]};
export type IdentityRun={name:string;label:string;pid:number;startedAt:number;log:string;
 config:IdentityConfig;platformWrites:boolean;running:boolean;stopping:boolean;progress:IdentityProgress|null};
export type IdentityBreakdown={unhanded:number;queued:number;blocked:number};
export type IdentityQueueState={available:boolean;leads:number;resolvedLeads:number;resolvedCreators:number;
 pendingLeads:number;pendingCreators:number;unresolvedLeads:number;unresolvedCreators:number;
 pendingBreakdown:IdentityBreakdown;reconciled:boolean;
 policy:{qps:number;lanes:number;acceptanceId:string|null;published:boolean;readable:boolean};
 // 已领（线索条数）与找到/搜索不到（达人个数）的对账；读不到就是 null，页面少一行而不是报错。
 walk:IdentityWalk|null;
 byCreator:IdentityByCreator|null;
 config:IdentityConfig;run:IdentityRun|null;saved?:boolean};

// The worker accepts 1, 10, 20 or 50 per round, but 1 makes it skip the cohort runner for its
// single-item path, which reports no target count -- the progress bar would read zero. So only the
// measurable sizes reach the button. The ceiling is a ceiling, never a target, so its value is
// bounded but not tied to the cohort size.
const COHORTS=new Set([10,20,50]);
const BATCH_MAX=200000;

export function validateIdentityConfig(value:unknown):IdentityConfig{
 if(!value||typeof value!=="object"||Array.isArray(value))throw Error('invalid_identity_queue');
 const v=value as Record<string,unknown>;
 // A misspelled key would be ignored here and the job would start with defaults instead.
 if(Object.keys(v).some(key=>key!=="batchSize"&&key!=="cohortSize"))throw Error('invalid_identity_queue');
 const whole=(raw:unknown,low:number,high:number)=>{if(typeof raw!=="number"||!Number.isSafeInteger(raw)||raw<low||raw>high)throw Error('invalid_identity_queue');return raw;};
 const cohort=whole(v.cohortSize,1,50);
 if(!COHORTS.has(cohort))throw Error('invalid_identity_queue');
 return {batchSize:whole(v.batchSize,1,BATCH_MAX),cohortSize:cohort};
}

function identityByCreator(value:unknown):IdentityByCreator|null{
 if(value==null)return null;
 if(typeof value!=="object"||Array.isArray(value))throw Error('invalid_identity_queue');
 const v=value as Record<string,unknown>;
 const whole=(raw:unknown)=>{if(typeof raw!=="number"||!Number.isSafeInteger(raw)||raw<0)throw Error('invalid_identity_queue');return raw;};
 const rawReasons=v.blockedReasons??[];
 if(!Array.isArray(rawReasons)||rawReasons.length>8)throw Error('invalid_identity_queue');
 const blockedReasons:IdentityBlockedReason[]=rawReasons.map(row=>{
  if(!row||typeof row!=="object"||Array.isArray(row))throw Error('invalid_identity_queue');
  const item=row as Record<string,unknown>;
  if(typeof item.reason!=="string"||!item.reason)throw Error('invalid_identity_queue');
  return {reason:item.reason,count:whole(item.count)};
 });
 const by={handles:whole(v.handles),resolved:whole(v.resolved),unresolved:whole(v.unresolved),
  blocked:whole(v.blocked),unknown:whole(v.unknown),blockedReasons,
  leads:whole(v.leads),positions:whole(v.positions),reconciled:v.reconciled===true};
 // 达人四项必须互斥且加成 handle 总数：这是这张卡唯一敢放主数字的理由，对不上就不能画。
 if(!by.reconciled
    ||by.resolved+by.unresolved+by.blocked+by.unknown!==by.handles)throw Error('invalid_identity_queue');
 return by;
}

function identityWalk(value:unknown):IdentityWalk|null{
 if(value==null)return null;
 if(typeof value!=="object"||Array.isArray(value))throw Error('invalid_identity_queue');
 const v=value as Record<string,unknown>;
 const whole=(raw:unknown)=>{if(typeof raw!=="number"||!Number.isSafeInteger(raw)||raw<0)throw Error('invalid_identity_queue');return raw;};
 const walk={settled:whole(v.settled),newHandles:whole(v.newHandles),repeats:whole(v.repeats)};
 // 三个数必须自洽：全新达人 + 同人线索 = 已结算。对不上说明这不是同一份对账。
 if(walk.newHandles+walk.repeats!==walk.settled)throw Error('invalid_identity_queue');
 return walk;
}

function validateProgress(value:unknown):IdentityProgress|null{
 if(value==null)return null;
 if(typeof value!=="object"||Array.isArray(value))throw Error('invalid_identity_queue');
 const v=value as Record<string,unknown>;
 const whole=(raw:unknown)=>{if(typeof raw!=="number"||!Number.isSafeInteger(raw)||raw<0)throw Error('invalid_identity_queue');return raw;};
 const clock=(raw:unknown)=>{if(typeof raw!=="number"||!Number.isFinite(raw)||raw<0)throw Error('invalid_identity_queue');return raw;};
 // A stop reason is either a code from the driver or nothing at all; a number here would be a
 // different payload wearing this one's shape.
 if(v.stopReason!=null&&typeof v.stopReason!=="string")throw Error('invalid_identity_queue');
 // 失败原因是可选的历史：老进度文件没有这个字段，不能因此让整张卡读不出来；写坏了才拒绝。
 const rawErrors=v.errors??[];
 if(!Array.isArray(rawErrors))throw Error('invalid_identity_queue');
 const errors:IdentityError[]=rawErrors.map(row=>{
  if(!row||typeof row!=="object"||Array.isArray(row))throw Error('invalid_identity_queue');
  const item=row as Record<string,unknown>;
  if(typeof item.detail!=="string"||!item.detail)throw Error('invalid_identity_queue');
  return {round:whole(item.round??0),detail:item.detail.slice(-1200)};
 });
 return {startedAt:clock(v.startedAt),updatedAt:clock(v.updatedAt),rounds:whole(v.rounds),limit:whole(v.limit),
  cohortSize:whole(v.cohortSize),pendingAtStart:whole(v.pendingAtStart),pending:whole(v.pending),
  claimed:whole(v.claimed),lastTargets:whole(v.lastTargets),found:whole(v.found),
  notFound:whole(v.notFound),stopReason:v.stopReason==null?null:v.stopReason,
  roundRunning:whole(v.roundRunning??0),
  roundStartedAt:v.roundStartedAt==null?null:clock(v.roundStartedAt),
  // 重试计数是新增的可选历史：老进度文件没有它，缺字段按 0 读。
  retry:whole(v.retry??0),
  errors:errors.slice(-2)};
}

function validateRun(value:unknown,fallback:IdentityConfig):IdentityRun|null{
 if(value==null)return null;
 if(typeof value!=="object"||Array.isArray(value))throw Error('invalid_identity_queue');
 const v=value as Record<string,unknown>;
 if(v.name!=="identity"||typeof v.running!=="boolean"||typeof v.pid!=="number"||typeof v.label!=="string")
  throw Error('invalid_identity_queue');
 // A run record is history. If a rule tightened after it was written, its stored config no longer
 // validates -- that must not blank the page. The current config stands in; the run's own numbers
 // (progress, running, stopping) are untouched and are what the card actually reads.
 let runConfig:IdentityConfig;try{runConfig=validateIdentityConfig(v.config);}catch{runConfig=fallback;}
 return {name:"identity",label:v.label,pid:v.pid,startedAt:typeof v.startedAt==="number"?v.startedAt:0,
  log:typeof v.log==="string"?v.log:"",config:runConfig,
  // Resolving an identity is a read of the platform profile; it writes nothing there.
  platformWrites:v.platformWrites===true,running:v.running,stopping:v.stopping===true,
  progress:validateProgress(v.progress)};
}

const count=(raw:unknown)=>{if(typeof raw!=="number"||!Number.isSafeInteger(raw)||raw<0)throw Error('invalid_identity_queue');return raw;};

export function validateIdentityQueue(value:unknown):IdentityQueueState{
 if(!value||typeof value!=="object"||Array.isArray(value))throw Error('invalid_identity_queue');
 const v=value as Record<string,unknown>;
 const config=validateIdentityConfig(v.config);
 const run=validateRun(v.run,config);
 const saved=typeof v.saved==="boolean"?{saved:v.saved}:{};
 if(v.available!==true)return {available:false,leads:0,resolvedLeads:0,resolvedCreators:0,pendingLeads:0,
  pendingCreators:0,unresolvedLeads:0,unresolvedCreators:0,
  pendingBreakdown:{unhanded:0,queued:0,blocked:0},reconciled:true,walk:null,byCreator:null,
  policy:{qps:0,lanes:0,acceptanceId:null,published:false,readable:false},config,run,...saved};
 const breakdown=v.pendingBreakdown as Record<string,unknown>|undefined;
 const policy=v.policy as Record<string,unknown>|undefined;
 if(typeof v.reconciled!=="boolean")throw Error('invalid_identity_queue');
 return {available:true,leads:count(v.leads),resolvedLeads:count(v.resolvedLeads),
  resolvedCreators:count(v.resolvedCreators),pendingLeads:count(v.pendingLeads),
  pendingCreators:count(v.pendingCreators),unresolvedLeads:count(v.unresolvedLeads),
  unresolvedCreators:count(v.unresolvedCreators),
  pendingBreakdown:{unhanded:count(breakdown?.unhanded),queued:count(breakdown?.queued),blocked:count(breakdown?.blocked)},
  // The three groups must partition every lead. If they ever do not, the page says so rather than
  // presenting a total it cannot account for.
  reconciled:v.reconciled,
  walk:identityWalk(v.walk),
  byCreator:identityByCreator(v.byCreator),
  policy:{qps:count(policy?.qps),lanes:count(policy?.lanes),
   acceptanceId:typeof policy?.acceptanceId==="string"?policy.acceptanceId:null,
   // "not published" and "could not be read" are different facts about the same two numbers.
   published:policy?.published===true,readable:policy?.readable===true},
  config,run,...saved};
}

function runQueue(args:string[]):Promise<IdentityQueueState>{
 const root=projectRoot();
 return new Promise((resolve,reject)=>{
  execFile(join(root,".venv/bin/python"),[join(root,"scripts/identity-queue.py"),...args],
   {cwd:root,timeout:60000,maxBuffer:2*1024*1024,env:{...process.env,PYTHONDONTWRITEBYTECODE:"1"}},(error,out)=>{
    let parsed:unknown;
    try{parsed=JSON.parse(out);}
    catch{reject(Error('identity_queue_unavailable'));return;}
    if(parsed&&typeof parsed==="object"&&Object.keys(parsed).length===1&&typeof (parsed as {error?:unknown}).error==="string"&&(parsed as {error:string}).error!==""){
     reject(Error((parsed as {error:string}).error));return;}
    try{resolve(validateIdentityQueue(parsed));}
    catch{reject(Error('identity_queue_unavailable'));}
   });
 });
}

export function readIdentityQueue():Promise<IdentityQueueState>{return runQueue(["status"]);}
export function saveIdentityConfig(config:IdentityConfig):Promise<IdentityQueueState>{
 return runQueue(["save","--json",JSON.stringify(config)]);
}

/** Run job-run.py and keep only its refusal: the payload of ``start`` is every job, not this one. */
function runJob(args:string[]):Promise<void>{
 const root=projectRoot();
 return new Promise((resolve,reject)=>{
  execFile(join(root,".venv/bin/python"),[join(root,"scripts/job-run.py"),...args],
   {cwd:root,timeout:60000,maxBuffer:1024*1024,env:{...process.env,PYTHONDONTWRITEBYTECODE:"1"}},(error,out)=>{
    let parsed:unknown;
    try{parsed=JSON.parse(out);}
    catch{reject(Error('identity_queue_unavailable'));return;}
    if(parsed&&typeof parsed==="object"&&Object.keys(parsed).length===1&&typeof (parsed as {error?:unknown}).error==="string"&&(parsed as {error:string}).error!==""){
     reject(Error((parsed as {error:string}).error));return;}
    resolve();
   });
 });
}

/** Start the backfill detached, then read the state back so the page sees one consistent shape. */
export async function startIdentityRun(config:IdentityConfig):Promise<IdentityQueueState>{
 await runJob(["start","--name","identity","--json",JSON.stringify(config)]);
 return readIdentityQueue();
}

/** Ask the running backfill to end after the round it is in. It does not interrupt a query. */
export async function stopIdentityRun():Promise<IdentityQueueState>{
 await runJob(["stop","--name","identity"]);
 return readIdentityQueue();
}

export function validateIdentityQueueRequest(value:unknown):
 {action:"save";market:"it";config:IdentityConfig}|{action:"start";market:"it";config:IdentityConfig}|{action:"stop";market:"it"}{
 if(!value||typeof value!=="object"||Array.isArray(value))throw Error('invalid_identity_queue_request');
 const v=value as Record<string,unknown>;
 if(v.market!=="it")throw Error('invalid_identity_queue_request');
 if(v.action==="stop"){
  // Stopping needs no parameters; anything riding along is a malformed request, not a stop.
  if(Object.keys(v).some(key=>key!=="action"&&key!=="market"))throw Error('invalid_identity_queue_request');
  return {action:"stop",market:"it"};
 }
 if(v.action!=="save"&&v.action!=="start")throw Error('invalid_identity_queue_request');
 if(Object.keys(v).some(key=>!['action','market','config'].includes(key)))throw Error('invalid_identity_queue_request');
 try{return {action:v.action,market:"it",config:validateIdentityConfig(v.config)};}
 catch{throw Error('invalid_identity_queue_request');}
}
