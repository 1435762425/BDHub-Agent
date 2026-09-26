"use client";

import Link from "next/link";
import {useEffect,useState} from "react";
import {Card,Notice,PageHeading,Pill} from "../bdhub/ui";
import type {ConsoleMarket,FinishedStage,Lane,OperationsConsole as State} from "@/server/operations-console/bridge";

// One screen for all markets (H12): what each is doing or waiting for, what it last achieved, and
// what needs a person. Read-only; every figure keeps its own time and unknown data reads "—".
const time=(value:number|null)=>value?new Date(value*1000).toLocaleString("zh-CN",{timeZone:"Asia/Shanghai",hour12:false,month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit"}):"—";
const minutes=(since:number|null,now:number)=>since?`${Math.max(0,Math.round((now-since)/60))} 分钟`:"—";
const STATE:Record<string,string>={completed:"已完成",quota_exhausted:"额度用尽，断点保留",failed:"失败",needs_human:"需核对",stopped:"已停止",
 skipped:"本轮跳过",running:"运行中",queued:"排队",sending:"发送中",waiting_window:"等待发送窗口",waiting_pool:"等待发送池",
 waiting_capacity:"等待可用额度",waiting_reconciliation:"待核验",outside_reply_window:"回复窗口外",disabled:"未启用",off:"关闭",idle:"空闲",paused:"暂停"};
const label=(state:string|null)=>state?STATE[state]??"状态待核实":"—";
const tone=(state:string|null)=>state==="failed"||state==="needs_human"||state==="waiting_reconciliation"?"warning":state==="running"||state==="sending"?"brand":state==="completed"?"success":"neutral";
function resource(key:string){
 if(key==="platform:global")return "平台读取槽（四市场共用）";
 if(key==="kalodata:global")return "Kalodata 读取槽";
 const [kind,name]=key.split(":");
 return kind==="workflow"?`${(name??"").toUpperCase()} 主链`:kind==="supply"?`供给账号 ${name}`:kind==="communications"?`通讯账号 ${name}`:key;
}

function Current({row,labels,now}:{row:Extract<ConsoleMarket,{available:true}>;labels:Record<string,string>;now:number}){
 const current=row.current;
 if(!row.setting.automaticOperationsEnabled&&!row.run)return <span className="text-gray-400">自动运营未开启</span>;
 if(!current)return <span className="text-gray-500">没有进行中的主链阶段</span>;
 const stage=labels[current.stage]??current.stage;
 if(current.state==="running")return <div><Pill tone="brand">运行中</Pill> <span className="ml-1">{stage}</span><p className="mt-1 text-xs text-gray-400">已 {minutes(current.since,now)} · 心跳 {time(current.heartbeatAt)}</p></div>;
 return <div><Pill tone="neutral">排队</Pill> <span className="ml-1">{stage}</span>{current.waitingOn.length?current.waitingOn.map(wait=><p key={wait.resource} className="mt-1 text-xs text-gray-500">等待{resource(wait.resource)}：{wait.heldBy.map(h=>`${h.market.toUpperCase()} ${labels[h.stage]??h.stage}（已占 ${minutes(h.since,now)}）`).join("、")}</p>):<p className="mt-1 text-xs text-gray-400">资源空闲，下一次调度即领取</p>}</div>;
}

function Finished({row,labels}:{row:FinishedStage|null;labels:Record<string,string>}){
 if(!row)return <span className="text-gray-400">—</span>;
 return <div><span>{labels[row.stage]??row.stage}</span> <Pill tone={tone(row.state)}>{label(row.state)}</Pill><p className="mt-1 text-xs text-gray-400">{time(row.finishedAt)}{row.items!=null?` · 本轮 ${row.items.toLocaleString()} 项`:""}{row.errorCode?` · ${row.errorCode}`:""}</p></div>;
}

function LaneLine({name,lane}:{name:string;lane:Lane|null}){
 if(!lane)return <p className="text-xs text-gray-400">{name}：—</p>;
 return <p className="text-xs"><span className="text-gray-500">{name}：</span>{label(lane.state)}{lane.value!=null&&lane.label?` · ${lane.label} ${lane.value.toLocaleString()}`:""}</p>;
}

export default function OperationsConsole(){
 const [data,setData]=useState<State|null>(null),[failed,setFailed]=useState(false);
 useEffect(()=>{let stopped=false,timer:ReturnType<typeof setTimeout>|undefined,controller:AbortController|null=null;
  const poll=async()=>{if(stopped)return;
   if(document.visibilityState==="visible"){controller?.abort();controller=new AbortController();
    try{const response=await fetch("/api/operations-console",{cache:"no-store",signal:controller.signal});if(!response.ok)throw Error();const value=await response.json() as State;if(!stopped){setData(value);setFailed(false);}}
    catch(error){if(!stopped&&!(error instanceof DOMException&&error.name==="AbortError"))setFailed(true);}}
   if(!stopped)timer=setTimeout(poll,15_000);};
  const visible=()=>{if(document.visibilityState==="visible"){if(timer)clearTimeout(timer);void poll();}};
  document.addEventListener("visibilitychange",visible);void poll();
  return()=>{stopped=true;controller?.abort();if(timer)clearTimeout(timer);document.removeEventListener("visibilitychange",visible);};},[]);
 const now=data?.checkedAt??Date.now()/1000,labels=data?.stageLabels??{};
 return <div className="space-y-5">
  <PageHeading title="总控制台" description="四个市场现在在做什么、在等什么、最近得到什么；只读，不启动任何作业。" action={data?<div className="flex flex-wrap gap-2"><Pill tone={data.scheduler.running?"success":"warning"}>{data.scheduler.running?"调度器在运行":"调度器未运行"}</Pill><Pill tone="neutral">数据截至 {time(data.checkedAt)}</Pill></div>:undefined}/>
  {failed&&<Notice tone="warning">总控数据暂时读不到，状态待核实{data?`；以下是 ${time(data.checkedAt)} 的记录`:""}。</Notice>}
  {!data&&!failed&&<p className="text-sm text-gray-500">正在读取…</p>}
  {data&&<>
   <Card title="市场矩阵" subtitle="主链同一时间只有一个市场占用平台读取槽；持续发送和 AI 回复独立运行。">
    <div className="overflow-x-auto"><table className="w-full min-w-[860px] text-left text-sm"><thead className="text-xs text-gray-400"><tr><th className="px-5 py-3 font-medium">市场</th><th className="px-3 py-3 font-medium">主链：当前执行 / 等待</th><th className="px-3 py-3 font-medium">最近一次结束</th><th className="px-3 py-3 font-medium">持续通道</th><th className="px-3 py-3 font-medium">需要处理</th></tr></thead>
     <tbody className="divide-y divide-gray-100 dark:divide-gray-800">{data.markets.map(row=><tr key={row.market} className="align-top">
      <td className="px-5 py-4"><Link href={`/${row.market}`} className="font-semibold text-brand-500">{row.market.toUpperCase()}</Link></td>
      {row.available?<>
       <td className="px-3 py-4"><Current row={row} labels={labels} now={now}/></td>
       <td className="px-3 py-4"><Finished row={row.lastFinished} labels={labels}/></td>
       <td className="space-y-1 px-3 py-4"><LaneLine name="持续发送" lane={row.lanes.continuousSend}/><LaneLine name="AI 回复" lane={row.lanes.agentReply}/>{row.lanes.available?<p className="text-[11px] text-gray-400">截至 {time(row.lanes.observedAt)}</p>:<p className="text-[11px] text-gray-400">通道快照不可用</p>}</td>
       <td className="space-y-1 px-3 py-4 text-xs">{row.needsReview&&<p className="text-warning-600">主链{label(row.needsReview.state)}{row.needsReview.errorCode?`：${row.needsReview.errorCode}`:""}</p>}{row.openHumanCases?<Link href={`/${row.market}/conversations`} className="block text-brand-500">{row.openHumanCases} 条人工会话</Link>:null}{!row.needsReview&&!row.openHumanCases&&<span className="text-gray-400">{row.openHumanCases==null?"—":"无"}</span>}</td>
      </>:<td colSpan={4} className="px-3 py-4 text-xs text-warning-600">该市场台账读取失败，状态待核实（{row.error}）</td>}
     </tr>)}</tbody></table></div>
   </Card>
   <div className="grid gap-5 xl:grid-cols-[.8fr_1.2fr]">
    <Card title="资源占用" subtitle="谁占着共享资源、占了多久"><div className="space-y-2 p-5 text-sm">{data.resources.length?data.resources.map(row=><div key={`${row.resource}-${row.market}`} className="flex flex-wrap items-baseline justify-between gap-2"><span>{resource(row.resource)}</span><span className="text-xs text-gray-500">{row.market.toUpperCase()} {labels[row.stage]??row.stage} · 已 {minutes(row.since,now)} · 心跳 {time(row.heartbeatAt)}</span></div>):<p className="text-gray-400">当前没有阶段占用资源。</p>}</div></Card>
    <Card title="最近运行" subtitle="每个主链阶段结束时记下的结果；跳过的阶段不列出"><div className="divide-y divide-gray-100 px-5 dark:divide-gray-800">{data.recent.map(row=><div key={`${row.runId}-${row.stage}`} className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm"><span><span className="mr-2 font-medium">{row.market.toUpperCase()}</span>{labels[row.stage]??row.stage}</span><span className="flex items-center gap-2 text-xs text-gray-500">{row.items!=null?`${row.items.toLocaleString()} 项 · `:""}{time(row.finishedAt)} <Pill tone={tone(row.state)}>{label(row.state)}</Pill></span></div>)}{!data.recent.length&&<p className="py-6 text-sm text-gray-400">还没有结束的阶段。</p>}</div></Card>
   </div>
  </>}
 </div>;
}
