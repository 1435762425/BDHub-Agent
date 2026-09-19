import {execFile} from "node:child_process";
import {join} from "node:path";
import {projectRoot} from "../creator-identities/refresh.ts";

/** 非全托（Campaign）商品的筛分/入池摘要。数字来自本地账本，单位是 offer 与 PID。 */
export type CampaignPoolSample={pid:string;campaignId:string;creatorPercent:string|null;
 endAt:string|null;alternatives:{campaignId:string;creatorPercent:string|null;endAt:string|null}[]};
export type CampaignPanelState={available:boolean;reason?:string;snapshot?:string;offers?:number;distinctPids?:number;
 counts?:{eligible:number;ineligible:number};reasons?:Record<string,number>;eligiblePids?:number;
 multiCampaignPids?:number;poolReconciled?:boolean;
 poolCounts?:{chosen:number;held:number};withAlternatives?:number;sample?:CampaignPoolSample[];
 recorded?:{runId:string;snapshot:string;updated:number}|null};

/** 加入活动的账本状态。`platformWrites` 必须是 0 或真实次数，绝不假报。 */
export type CampaignJoinItem={campaignId:string;name:string;state:string;reason:string;
 writeAttempted:boolean;joinedCampaignId:string|null};
export type CampaignJoinState={available:boolean;reason?:string;jobId?:string;state?:string;error?:string;
 account?:string;email?:string;joinedCount?:number;counts?:Record<string,number>;unresolved?:string[];
 items?:CampaignJoinItem[];platformWrites?:number;campaigns?:number;joined?:number;eligible?:number;
 // 「一键加入」才带的三个字段
 attempted?:string[];appliedCounts?:Record<string,number>;
 // 回查结算掉的条数（0 是正常结果：没有待结算项）
 settled?:number;
 // 「其它分类」：加入的查询只覆盖 Seller collabs 一类，这里是平台活动那一族的只读对照。
 otherCategories?:{available:boolean;parents?:number;subs?:number;unjoinedEligible?:number;
  unjoined?:{campaignId:string;name:string;status:number|null;eligible:boolean;reason:string}[]}};

const SOURCES=new Set(["campaign","selected"]);
const JOIN_ACTIONS=new Set(["preview","apply","verify","joinAll"]);

function count(value:unknown,name:string):number{
 if(typeof value!=="number"||!Number.isSafeInteger(value)||value<0)throw Error(`invalid_${name}`);
 return value;
}

function countMap(value:unknown):Record<string,number>{
 if(!value||typeof value!=="object"||Array.isArray(value))throw Error('invalid_counts');
 const out:Record<string,number>={};
 for(const [key,raw] of Object.entries(value as Record<string,unknown>))out[key]=count(raw,key);
 return out;
}

export function validateCampaignPanel(value:unknown):CampaignPanelState{
 if(!value||typeof value!=="object"||Array.isArray(value))throw Error('invalid_campaign_panel');
 const v=value as Record<string,unknown>;
 if(v.available!==true)return {available:false,reason:typeof v.reason==="string"?v.reason:"unavailable"};
 const pool=v.pool as Record<string,unknown>|undefined;
 const sample=Array.isArray(pool?.sample)?pool!.sample as Record<string,unknown>[]:[];
 if(sample.length>200)throw Error('invalid_campaign_panel');
 const recorded=v.recorded as Record<string,unknown>|null|undefined;
 return {available:true,snapshot:typeof v.snapshot==="string"?v.snapshot:undefined,
  offers:count(v.offers,'offers'),distinctPids:count(v.distinctPids??0,'distinctPids'),
  counts:{eligible:count((v.counts as Record<string,unknown>)?.eligible,'eligible'),
   ineligible:count((v.counts as Record<string,unknown>)?.ineligible,'ineligible')},
  reasons:countMap(v.reasons??{}),eligiblePids:count(v.eligiblePids,'eligiblePids'),
  multiCampaignPids:count(v.multiCampaignPids,'multiCampaignPids'),
  poolReconciled:pool?.reconciled===true,
  poolCounts:{chosen:count((pool?.counts as Record<string,unknown>)?.chosen??0,'chosen'),
   held:count((pool?.counts as Record<string,unknown>)?.held??0,'held')},
  withAlternatives:count(pool?.withAlternatives??0,'withAlternatives'),
  sample:sample.map(row=>({pid:String(row.pid),campaignId:String(row.campaignId),
   creatorPercent:row.creatorPercent==null?null:String(row.creatorPercent),
   endAt:row.endAt==null?null:String(row.endAt),
   alternatives:Array.isArray(row.alternatives)?(row.alternatives as Record<string,unknown>[]).map(a=>({
    campaignId:String(a.campaignId),creatorPercent:a.creatorPercent==null?null:String(a.creatorPercent),
    endAt:a.endAt==null?null:String(a.endAt)})):[]})),
  recorded:recorded&&typeof recorded==="object"
   ?{runId:String(recorded.runId),snapshot:String(recorded.snapshot),updated:Number(recorded.updated)||0}
   :null};
}

function otherCategoryState(other:Record<string,unknown>|undefined):Partial<CampaignJoinState>{
 if(!other)return {};
 if(typeof other.available!=="boolean")throw Error('invalid_campaign_join');
 const rows=Array.isArray(other.unjoined)?other.unjoined as Record<string,unknown>[]:[];
 if(rows.length>200)throw Error('invalid_campaign_join');
 return {otherCategories:{available:other.available,
  ...(typeof other.parents==="number"?{parents:count(other.parents,'parents')}:{}),
  ...(typeof other.subs==="number"?{subs:count(other.subs,'subs')}:{}),
  ...(typeof other.unjoinedEligible==="number"?{unjoinedEligible:count(other.unjoinedEligible,'unjoinedEligible')}:{}),
  ...(Array.isArray(other.unjoined)?{unjoined:rows.map(row=>({campaignId:String(row.campaignId),
   name:String(row.name??""),status:typeof row.status==="number"?row.status:null,
   eligible:row.eligible===true,reason:String(row.reason??"")}))}:{})}};
}

export function validateCampaignJoin(value:unknown):CampaignJoinState{
 if(!value||typeof value!=="object"||Array.isArray(value))throw Error('invalid_campaign_join');
 const v=value as Record<string,unknown>;
 if(v.available!==true)return {available:false,reason:typeof v.reason==="string"?v.reason:"campaign_join_not_started"};
 const other=v.otherCategories as Record<string,unknown>|undefined;
 const items=Array.isArray(v.items)?v.items as Record<string,unknown>[]:[];
 if(items.length>400)throw Error('invalid_campaign_join');
 return {available:true,jobId:String(v.jobId??""),state:String(v.state??""),
  error:typeof v.error==="string"?v.error:"",account:String(v.account??""),
  email:typeof v.email==="string"?v.email:"",
  joinedCount:count(v.joinedCount??0,'joinedCount'),counts:countMap(v.counts??{}),
  unresolved:Array.isArray(v.unresolved)?v.unresolved.map(String):[],
  items:items.map(row=>({campaignId:String(row.campaign_id),name:String(row.name??""),
   state:String(row.state),reason:String(row.reason??""),
   writeAttempted:Number(row.write_attempted)===1,
   joinedCampaignId:row.joined_campaign_id==null?null:String(row.joined_campaign_id)})),
  platformWrites:count(v.platformWrites??0,'platformWrites'),
  // preview 才带的三个字段
  ...(typeof v.campaigns==="number"?{campaigns:count(v.campaigns,'campaigns')}:{}),
  ...(typeof v.joined==="number"?{joined:count(v.joined,'joined')}:{}),
  ...(typeof v.eligible==="number"?{eligible:count(v.eligible,'eligible')}:{}),
  ...(typeof v.settled==="number"?{settled:count(v.settled,'settled')}:{}),
  ...otherCategoryState(other),
  ...(Array.isArray(v.attempted)?{attempted:v.attempted.map(String)}:{}),
  ...(v.appliedCounts&&typeof v.appliedCounts==="object"&&!Array.isArray(v.appliedCounts)
   ?{appliedCounts:countMap(v.appliedCounts)}:{})};
}

// 写入类调用要给足时间：18 次加入很可能超过 60 秒，而**中途被杀正是"结果未知"的制造机**
// （每次写入前都落了 write_attempted，所以被杀还能回查，但不该故意制造这种局面）。
const WRITE_TIMEOUT_MS=10*60*1000;

function run(script:string,args:string[],limit=4*1024*1024,timeout=60000):Promise<unknown>{
 const root=projectRoot();
 return new Promise((resolve,reject)=>{
  execFile(join(root,".venv/bin/python"),[join(root,"scripts",script),...args],
   {cwd:root,timeout,maxBuffer:limit,env:{...process.env,PYTHONDONTWRITEBYTECODE:"1"}},(error,out,err)=>{
    let parsed:unknown;
    try{parsed=JSON.parse(out);}
    catch{
     // stdout 不是 JSON（脚本崩了/被打断）时，把 stderr 的最后一行带出去：
     // 只回一句 campaign_unavailable 会把真正的原因挡在操作者看不到的地方。
     const tail=String(err??"").trim().split("\n").filter(Boolean).pop()??"";
     reject(Error(tail?`campaign_unavailable:${tail.slice(0,200)}`:"campaign_unavailable"));return;}
    // 失败约定是**只有 error 一个键**的裸信封（各 CLI 的失败输出都是 json.dumps({"error": code})）。
    // 不能只判"有非空 error"：status 里的 error 是 job 的历史错误，是给人看的记录，
    // 那样一来"只要这个 job 存过一次错误，状态接口就再也读不出来"。
    if(parsed&&typeof parsed==="object"&&Object.keys(parsed).length===1&&typeof (parsed as {error?:unknown}).error==="string"&&(parsed as {error:string}).error!==""){
     reject(Error((parsed as {error:string}).error));return;}
    resolve(parsed);
   });
 });
}

export async function readCampaignPanel(source="campaign"):Promise<CampaignPanelState>{
 return validateCampaignPanel(await run("campaign-screen.py",["status","--source",source,"--sample","20"]));
}

/** 非全托的链接准备覆盖情况。只读本地账本与池子，不访问平台。 */
export type CampaignLinkState={available:boolean;reason?:string;runId?:string;poolRun?:string;snapshot?:string;
 targets?:number;excluded?:number;planMissing?:number;commissionInvalid?:number;
 states?:Record<string,number>;verifiedPids?:number;reusePids?:number;outstanding?:number;
 platformWrites?:number;executionAllowed?:false};

export function validateCampaignLinks(value:unknown):CampaignLinkState{
 if(!value||typeof value!=="object"||Array.isArray(value))throw Error('invalid_campaign_links');
 const v=value as Record<string,unknown>;
 // 没开始过不是错误：页面上要显示"还没有准备过"，而不是一个红叉。
 if(v.available!==true)return {available:false,reason:typeof v.reason==="string"?v.reason:"catalog_prepare_not_started"};
 if(v.route!=="campaign")throw Error('invalid_campaign_links');
 const scope=(v.scope??{}) as Record<string,unknown>;
 const selection=(v.selection??{}) as Record<string,unknown>;
 const skipped=(selection.skipped??{}) as Record<string,unknown>;
 const summary=(v.summary??{}) as Record<string,unknown>;
 return {available:true,runId:String(v.runId??""),
  poolRun:selection.poolRun==null?undefined:String(selection.poolRun),
  snapshot:selection.snapshot==null?undefined:String(selection.snapshot),
  targets:count(selection.targets??0,'targets'),
  excluded:count(skipped.excluded??0,'excluded'),
  planMissing:count(skipped.plan_missing??0,'planMissing'),
  commissionInvalid:count(skipped.commission_invalid??0,'commissionInvalid'),
  states:countMap(summary.states??{}),
  verifiedPids:count(summary.verifiedPidCount??0,'verifiedPids'),
  reusePids:count(summary.reusePidCount??0,'reusePids'),
  outstanding:count(summary.pendingCount??0,'outstanding'),
  platformWrites:0,executionAllowed:false};
}

export function readCampaignLinks():Promise<CampaignLinkState>{
 return run("catalog-link-prepare.py",["--status-links","--route","campaign"]).then(validateCampaignLinks);
}

export async function readCampaignJoin():Promise<CampaignJoinState>{
 return validateCampaignJoin(await run("campaign-join.py",["status"]));
}

/** 只读平台：列出可加入的活动与已加入对照。仍然要人点，因为它会访问平台。 */
export async function previewCampaignJoin():Promise<CampaignJoinState>{
 return validateCampaignJoin(await run("campaign-join.py",["preview"]));
}

export function validateCampaignJoinRequest(value:unknown):
 {action:"preview"}|{action:"verify"}|{action:"apply";campaignIds:string[];email:string;confirm:boolean}
 |{action:"joinAll";email:string;confirm:true}{
 if(!value||typeof value!=="object"||Array.isArray(value))throw Error('invalid_campaign_join_request');
 const v=value as Record<string,unknown>;
 if(!JOIN_ACTIONS.has(String(v.action)))throw Error('invalid_campaign_join_request');
 if(v.action==="preview"||v.action==="verify"){
  if(Object.keys(v).some(key=>key!=="action"))throw Error('invalid_campaign_join_request');
  return {action:v.action};
 }
 const email=typeof v.email==="string"?v.email:"";
 if(!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)||email.length>254)throw Error('invalid_campaign_join_request');
 // 写入必须显式确认；缺了就是坏请求，而不是"照做"。
 if(v.confirm!==true)throw Error('campaign_join_confirmation_required');
 if(v.action==="joinAll"){
  // 一键加入**不接受**活动列表：目标集合必须由服务端重新预览得出，
  // 否则页面可以拿一份过期名单去写平台。多带一个字段就是坏请求。
  if(Object.keys(v).some(key=>!["action","email","confirm"].includes(key)))throw Error('invalid_campaign_join_request');
  return {action:"joinAll",email,confirm:true};
 }
 const ids=Array.isArray(v.campaignIds)?v.campaignIds:[];
 if(!ids.length||ids.length>100||new Set(ids).size!==ids.length||
    !ids.every(id=>typeof id==="string"&&/^\d{10,32}$/.test(id)))throw Error('invalid_campaign_join_request');
 return {action:"apply",campaignIds:ids,email,confirm:true};
}

/** 只读结算：重新读已加入列表，把"提过但没结算"的条目落定。绝不重新提交。 */
export async function recheckCampaignJoin():Promise<CampaignJoinState>{
 return validateCampaignJoin(await run("campaign-join.py",["verify"]));
}

export async function applyCampaignJoin(campaignIds:string[],email:string):Promise<CampaignJoinState>{
 return validateCampaignJoin(await run("campaign-join.py",
  ["apply","--confirm","--email",email,"--campaigns",campaignIds.join(",")],8*1024*1024,WRITE_TIMEOUT_MS));
}

/** 一键加入：服务端**先重新预览一遍**，再把当前全部合格的活动一次提交。平台写入。 */
export async function joinAllCampaigns(email:string):Promise<CampaignJoinState>{
 return validateCampaignJoin(await run("campaign-join.py",["join-all","--confirm","--email",email],
  8*1024*1024,WRITE_TIMEOUT_MS));
}

export const CAMPAIGN_SOURCES=[...SOURCES];
