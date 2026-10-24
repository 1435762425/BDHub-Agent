"use client";
import Link from "next/link";
import MarketOverview from "./MarketOverview";
import {useCallback,useEffect,useRef,useState} from "react";
import {Button,Card,Notice,PageHeading,Pill,Toggle} from "../bdhub/ui";
import type {OperationsHome as State,OperationsStage,SaveCommit} from "../../server/operations-home/bridge";

const stateLabel:Record<string,string>={disabled:"未启用",waiting_upstream:"等待上游",queued:"排队",running:"运行中",completed:"已完成",quota_exhausted:"额度耗尽",needs_human:"需人工",failed:"失败",skipped:"本轮跳过",off:"关闭",waiting_window:"等待窗口",sending:"发送中",waiting_capacity:"等待可用额度",waiting_transport:"连接恢复中",waiting_reconciliation:"待核验",paused:"暂停",stopped:"已停止",waiting_pool:"等待发送池",idle:"空闲"};
// Unknown codes never pass as a business state: they read "状态待核实" and keep the machine code as a tooltip.
const label=(state:string)=>stateLabel[state]??"状态待核实";
const REASONS:Record<string,string>={identity_lane_waiting_recovery:"身份解析通道等待恢复",source_stopped:"数据源已停止",
 parallel_selection_requires_review:"并行选品结果需核对",global_selection_unresolved:"有选入结果待核对",global_selection_no_progress:"全托选入无进展，本轮已停",
 global_selection_progress_unknown:"全托选入进度不明，本轮已停",global_catalog_not_published:"全托货盘尚未发布",market_identity_report_invalid:"身份解析报告无效",
 market_identity_blocked:"身份解析被平台拦截",market_identity_auth_required:"账号登录失效，已进入统一恢复",auth_recovery_wait_timeout:"账号恢复等待超时",
 selection_relogin_not_published:"账号恢复未产生新身份",identity_queue_stalled:"身份解析队列无进展",kalodata_daily_quota_exhausted:"Kalodata 当日额度用尽",
 scan_already_collecting:"已有采集在进行",nothing_pending:"没有待处理项",TimeoutExpired:"子任务超时",account_maintenance_active:"账号维护进行中"};
// A reason the page cannot explain is shown as such, with the machine code behind a diagnostics toggle.
function Reason({code}:{code:string|null}){
 if(!code)return null;
 const known=REASONS[code]??(code.endsWith("_failed")?"子任务失败":null);
 return <div><p title={code}>原因：{known??"需核对的技术原因"}</p>{!known&&<details><summary className="cursor-pointer text-gray-400">诊断</summary><code className="break-all">{code}</code></details>}</div>;
}
const tone=(state:string)=>state==="completed"||state==="sending"?"success":state==="failed"||state==="needs_human"||state==="waiting_reconciliation"?"warning":state==="running"||state==="queued"?"brand":"neutral";
const stamp=(value:number|null)=>value?new Date(value*1000).toLocaleString("zh-CN",{timeZone:"Asia/Shanghai",hour12:false}):"—";
const LANES=new Set(["continuous_send","agent_reply"]);
const SCOPE:Record<string,string>={run:"本轮",today:"今日（北京时间）",last_run:"最近一轮"};
// Units read on the card itself (I07): 12 PID, 12 位 and 12 条 must never look alike.
const UNIT:Record<string,string>={item:"项",query:"个查询",delivery:"次投递",message:"条",creator_pid:"个位置",creator:"位"};
// One card per stage; the figure carries its own name, unit and window (H07). Missing data reads "—".
function StageCard({stage,index}:{stage:OperationsStage;index:number}){
 const metric=stage.metric,writes=stage.writes;
 const value=metric?metric.value:typeof stage.counts.items==="number"?stage.processed:null;
 const caption=metric?`${metric.label}${metric.availability==="known"?"":"（未记录）"}`:"处理数未记录";
 const writeText=!writes?`平台写入 ${stage.platformWrites}`:writes.availability==="known"?`平台写入 ${writes.value}`:writes.availability==="uncertain"?"平台写入 未确认":null;
 return <article className="min-w-0 rounded-2xl border border-gray-200 bg-white p-4 dark:border-gray-800 dark:bg-white/[0.025]"><div className="flex items-start justify-between gap-2"><div>{!LANES.has(stage.id)&&<p className="text-[11px] font-medium text-gray-400">{String(index+1).padStart(2,"0")}</p>}<h3 className="mt-1 text-sm font-semibold text-gray-800 dark:text-white">{stage.label}</h3></div><span title={stage.state}><Pill tone={tone(stage.state)}>{label(stage.state)}</Pill></span></div><p className="mt-4 text-2xl font-semibold tabular-nums">{value==null?"—":value.toLocaleString()}{value!=null&&metric&&UNIT[metric.unit]&&<span className="ml-1 text-sm font-normal text-gray-400">{UNIT[metric.unit]}</span>}</p><p className="mt-1 text-xs text-gray-400">{caption}{metric?` · ${SCOPE[metric.scope]??"范围未记录"}`:""}{writeText?` · ${writeText}`:""}</p><div className="mt-4 space-y-1 text-[11px] leading-5 text-gray-500"><p>上次成功 {stamp(stage.lastSuccessAt)}</p><Reason code={stage.stopReason}/></div></article>}

export default function OperationsHome({market,marketLabel,fullManagedCatalog,runtimeAvailable}:{market:string;marketLabel:string;fullManagedCatalog:boolean|null;runtimeAvailable:boolean}){
 const [data,setData]=useState<State|null>(null),[busy,setBusy]=useState<string|null>(null),[message,setMessage]=useState("");// One frozen command per switch: a retry after an unknown result reuses the same id, value and base revision.
 const requestIds=useRef(new Map<string,{requestId:string;value:boolean;expectedRevision:number}>());
 // Reads are applied in order and never move the setting back behind a newer committed revision.
 const readSeq=useRef(0),appliedSeq=useRef(0),knownRevision=useRef(0),marketRef=useRef(market);
 if(marketRef.current!==market){marketRef.current=market;readSeq.current=0;appliedSeq.current=0;knownRevision.current=0;requestIds.current.clear();}
 // Freshness of what is shown: a failed read never leaves an unconditional "normal" on screen.
 const [readAt,setReadAt]=useState<number|null>(null),[readError,setReadError]=useState(false);
 const load=useCallback(async()=>{if(!runtimeAvailable)return;const seq=++readSeq.current;try{const response=await fetch(`/api/operations-home?market=${encodeURIComponent(market)}`,{cache:"no-store"});if(!response.ok)throw Error();const value=await response.json() as State;if(market!==marketRef.current||seq<appliedSeq.current||value.market!==market||value.setting.revision<knownRevision.current)return;appliedSeq.current=seq;knownRevision.current=value.setting.revision;setData(value);setReadAt(Date.now()/1000);setReadError(false);}catch(error){if(seq>=appliedSeq.current)setReadError(true);throw error;}},[market,runtimeAvailable]);
 useEffect(()=>{if(!runtimeAvailable)return;let timer:ReturnType<typeof setTimeout>|undefined,inFlight=false,stopped=false;const poll=async()=>{if(stopped)return;if(document.visibilityState!=="visible"){timer=setTimeout(poll,60000);return;}if(inFlight){timer=setTimeout(poll,15000);return;}inFlight=true;try{await load();}catch{}finally{inFlight=false;if(!stopped)timer=setTimeout(poll,15000);}};const visible=()=>{if(document.visibilityState==="visible"&&!inFlight){if(timer)clearTimeout(timer);void poll();}};document.addEventListener("visibilitychange",visible);void poll();return()=>{stopped=true;if(timer)clearTimeout(timer);document.removeEventListener("visibilitychange",visible);};},[load,runtimeAvailable]);
 const toggle=async(key:"automaticOperationsEnabled"|"fullCatalogWeeklyEnabled"|"continuousSendEnabled",value:boolean)=>{if(!data)return;setBusy(key);setMessage("");const frozen=requestIds.current.get(key);const command=frozen&&frozen.value===value?frozen:{requestId:`home-${crypto.randomUUID()}`,value,expectedRevision:data.setting.revision};requestIds.current.set(key,command);const requestId=command.requestId;
  let response:Response;
  try{response=await fetch(`/api/operations-home?market=${encodeURIComponent(market)}`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action:"save",market,requestId,expectedRevision:command.expectedRevision,changes:{[key]:command.value}})});}
  catch{setBusy(null);setMessage("保存结果未确认（网络中断）；正在重新读取实际设置，重试会沿用同一请求，不会重复变更。");void load().catch(()=>{});return;}
  try{
   if(response.status===409){requestIds.current.delete(key);setMessage("设置未保存：版本已在另一窗口变化，已重新读取当前设置。");await load().catch(()=>{});return;}
   if(response.status===400||response.status===415){requestIds.current.delete(key);setMessage("设置未保存：请求不符合要求。");return;}
   if(!response.ok&&response.status!==202){setMessage("保存结果未确认；正在重新读取实际设置，重试会沿用同一请求，不会重复变更。");await load().catch(()=>{});return;}
   const value=await response.json() as (State&{commit:SaveCommit})|{committed:true;commit:SaveCommit};
   requestIds.current.delete(key);
   const commit=value.commit;
   // The page shows the current setting; the receipt below states what this request committed.
   const current="schemaVersion" in value?value.setting:commit.setting;
   if(current.revision>=knownRevision.current){knownRevision.current=current.revision;appliedSeq.current=readSeq.current;
    if("schemaVersion" in value){setData(value);setReadAt(Date.now()/1000);setReadError(false);}
    else setData(state=>state?{...state,setting:commit.setting}:state);}
   const notes=[commit.refreshFailed?"页面数据暂未刷新，稍后自动重读":commit.projectionStale?"概览缓存稍后刷新":"",
    ...commit.launchErrors.map(row=>`${row.worker} 未能启动（${row.error}），调度器会按已保存的开关继续尝试`)].filter(Boolean);
   setMessage(`${commit.duplicate?"这次提交此前已完成":"设置已保存"}（修订 ${commit.setting.revision}${commit.originalAvailable?"":"，原提交值未留存"}）${notes.length?`；${notes.join("；")}`:""}。不会恢复旧任务或结果未知意图。`);
   if(commit.refreshFailed)void load().catch(()=>{});
  }finally{setBusy(null);}};
 const workflow=async(action:"run"|"stop")=>{if(!data)return;setBusy(action);setMessage("");try{const body=action==="run"?{action,market,requestId:`workflow-${crypto.randomUUID()}`}:{action,market,runId:data.workflow.runId,expectedState:data.workflow.state};const response=await fetch(`/api/workflow?market=${encodeURIComponent(market)}`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});if(!response.ok)throw Error();setMessage(action==="run"?"已创建一次人工主链；后台只执行已启用的真实作业。":"已请求安全停止；在途请求先收口。 ");await load();}catch{setMessage("主链状态已变化，请刷新后核对。");}finally{setBusy(null);}};
 if(!runtimeAvailable)return <div className="space-y-5"><PageHeading title="运营首页" action={<Pill tone="warning">{marketLabel}</Pill>}/><Card title="自动运营"><div className="grid gap-5 p-5 lg:grid-cols-3">{["自动运营总开关","全托商品发现","持续自动发送"].map(label=><div key={label}><p className="text-sm font-medium">{label}</p><p className="mt-1 text-xs leading-5 text-gray-500">市场能力待验证，暂不可操作。</p></div>)}</div></Card></div>;
 if(!data)return <div className="space-y-5"><PageHeading title="运营首页"/><MarketOverview key={market} market={market} controls={<Card title="自动运营"><div className="p-5 text-sm text-gray-500">{readError?"运行设置读取失败，暂不可操作。":"正在读取运行设置…"}</div></Card>}/></div>;
 const active=["queued","running","stop_requested"].includes(data.workflow.state);
 const activationPending=data.issues.some(issue=>issue.kind==="activation");
 const templatePending=!data.templateReview.ready;
 const attentionStages=data.stages.filter(stage=>["failed","needs_human","waiting_reconciliation"].includes(stage.state)||!stateLabel[stage.state]||stage.writes?.availability==="uncertain");
 const controls=<>
  <Card title="自动运营" action={<div className="flex items-center gap-4"><Link href={`/${market}/ops/jobs`} className="text-sm font-medium text-brand-500">作业与时间 →</Link>{active&&<Button size="sm" variant="outline" disabled={busy!==null} onClick={()=>void workflow("stop")}>安全停止</Button>}</div>}>
   <div className="flex flex-wrap items-center gap-x-6 gap-y-3 px-5 py-3 text-xs dark:border-gray-800">
    <span className="flex items-center gap-2"><span className="text-gray-500">经营主链</span><span title={data.workflow.state}><Pill tone={tone(data.workflow.state)}>{label(data.workflow.state)}</Pill></span></span>
    {data.stages.filter(stage=>LANES.has(stage.id)).map(stage=><span key={stage.id} className="flex items-center gap-2"><span className="text-gray-500">{stage.label}</span><span title={stage.state}><Pill tone={tone(stage.state)}>{label(stage.state)}</Pill></span></span>)}
    <span className="text-gray-500">回复设置：{data.agent.enabled?"开启":"关闭"} · {data.agent.replyWindow.join("–")}</span>
   </div>
   <details className="border-t border-gray-100 dark:border-gray-800"><summary className="cursor-pointer px-5 py-3 text-sm font-medium text-gray-600 dark:text-gray-300">自动运营设置<span className="ml-3 text-xs font-normal text-gray-500">总开关{data.setting.automaticOperationsEnabled?"开启":"关闭"} · 自动发送{data.setting.continuousSendEnabled?"开启":"关闭"}</span></summary>
   <div className="grid gap-x-8 px-5 py-2 lg:grid-cols-3">
    <Toggle label="自动运营总开关" description={activationPending?"市场能力验证完成后可开启。":"自动推进货盘、链接、达人线索与身份解析。"} checked={data.setting.automaticOperationsEnabled} disabled={busy!==null||activationPending&&!data.setting.automaticOperationsEnabled} onChange={value=>void toggle("automaticOperationsEnabled",value)}/>
    {fullManagedCatalog===true?<Toggle label="全托商品发现" description="首次及每 15 天发现新商品。" checked={data.setting.fullCatalogWeeklyEnabled} disabled={busy!==null} onChange={value=>void toggle("fullCatalogWeeklyEnabled",value)}/>:<div className="py-3"><p className="text-sm font-medium">全托商品发现</p><p className="mt-1 text-xs leading-5 text-gray-500">{fullManagedCatalog===false?"当前市场不支持全托商品。":"全托能力待验证，暂不可用。"}</p></div>}
    <Toggle label="持续自动发送" description={templatePending?`需审核至少 ${data.templateReview.minimumApproved} 条话术，当前 ${data.templateReview.approved}/${data.templateReview.total}。`:"在发送窗口内触达可发达人。"} checked={data.setting.continuousSendEnabled} disabled={busy!==null||(activationPending||templatePending)&&!data.setting.continuousSendEnabled} onChange={value=>void toggle("continuousSendEnabled",value)}/>
   </div>
   </details>
  </Card>
  {message&&<Notice tone={message.includes("已")?"success":"warning"}>{message}</Notice>}
  {data.issues.length>0&&<Card title="运行事项"><div className="divide-y divide-gray-100 px-5 dark:divide-gray-800">{data.issues.map(issue=><Link key={issue.id} href={`/${market}/${issue.kind==="conversation"?"conversations":issue.kind==="account"?"ops/accounts":"ops/jobs"}`} className="group flex items-start justify-between gap-4 py-4"><div><p className="text-sm font-medium text-gray-800 group-hover:text-brand-600 dark:text-gray-200">{issue.title}</p><p className="mt-1 text-xs leading-5 text-gray-500">{issue.reason}</p></div><span className="shrink-0 text-sm text-brand-600">查看 →</span></Link>)}</div></Card>}
 </>;
 return <div className="space-y-5">
  <PageHeading title="运营首页" action={<div className="flex flex-wrap items-center gap-2">{readError?<Pill tone="warning">刷新失败 · 上次读取 {stamp(readAt)}</Pill>:data.issues.length?<Pill tone="warning">{data.issues.length} 项待关注</Pill>:<span className="text-xs text-gray-400">更新于 {stamp(readAt)}</span>}</div>}/>
  <MarketOverview key={market} market={market} controls={controls}/>

  <details className="rounded-2xl border border-gray-200 bg-white dark:border-gray-800 dark:bg-white/[0.025]">
   <summary className="cursor-pointer rounded-2xl px-5 py-4 text-sm font-semibold text-gray-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 dark:text-gray-200">作业与运行详情 <span className="ml-2 text-xs font-normal text-gray-400">{data.stages.length} 个阶段</span>{attentionStages.length>0&&<span className="mt-2 flex flex-wrap gap-2">{attentionStages.map(stage=><Pill key={stage.id} tone="warning">{stage.label} · {label(stage.state)}{stage.writes?.availability==="uncertain"?" · 写入未确认":""}</Pill>)}</span>}</summary>
   <div className="space-y-5 border-t border-gray-100 p-5 dark:border-gray-800">
    <p className="break-all text-xs text-gray-500">运行编号：{data.workflow.runId??"尚未建立"} · {label(data.workflow.state)}</p>
    <div><h2 className="mb-3 text-sm font-medium text-gray-700 dark:text-gray-200">供给主链</h2><div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">{data.stages.filter(stage=>!LANES.has(stage.id)).map((stage,index)=><StageCard key={stage.id} stage={stage} index={index}/>)}</div></div>
    <div><h2 className="mb-3 text-sm font-medium text-gray-700 dark:text-gray-200">持续发送与回复</h2><div className="grid gap-3 sm:grid-cols-2">{data.stages.filter(stage=>LANES.has(stage.id)).map((stage,index)=><StageCard key={stage.id} stage={stage} index={index}/>)}</div></div>
   </div>
  </details>
 </div>;
}
