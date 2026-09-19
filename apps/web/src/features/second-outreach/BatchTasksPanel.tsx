"use client";
import {useCallback,useEffect,useState} from "react";
import {Button,Card,EmptyState,Notice,Pill} from "../bdhub/ui";

type SourcePreparation={pids:number;pages:number;edges:number|null;selectionRecorded?:boolean;
 states:Record<string,number>;errors:string[]};
type Preparation={target:number;reserve:number;required:number;candidates:number;candidateGap:number;
 verifiedReady:number;blockers:string[]};
type Task={id:string;state:string;priority:number;revision:number;created:number;checkedAt:number|null;
 spec:{target:number;reserve:number;prepareNow?:boolean;startDate:string|null;startTime:string|null;endTime:string|null};
 preparation:Preparation|null;sourcePreparation:SourcePreparation|null;events:{kind:string;at:number}[]};
type Listing={tasks:Task[];worker:{running:boolean;heartbeat:number|null};executionConnected?:boolean};

const blockers:Record<string,string>={
 kalodata_daily_quota_exhausted:"Kalodata 当日额度曾用完，历史任务保留原断点。",
 pid_creator_supply_needed:"历史任务当时仍缺达人线索。",
 product_names_needed:"历史任务当时仍缺商品短名。",
 task_taplink_adapter_pending:"历史任务未完成旧版 TapLink 适配。",
 task_materials_pending:"历史任务仍有材料准备项。",
 local_source_unavailable:"历史任务最后一次读取没有完成。",
};
const time=(at:number|null)=>at?new Date(at*1000).toLocaleString("zh-CN",{timeZone:"Asia/Shanghai",hour12:false}):"未记录";
const stateLabel=(state:string)=>state==="ready"?"历史已备齐":state==="paused"?"历史已暂停":state==="preparing"?"历史准备中":state;

/**
 * 旧 batch-tasks.sqlite 只作历史追溯。当前发送入口是 SendBatchPanel 的预览→冻结→明确开始，
 * 这里不再创建任务、修改优先级或唤醒旧准备 worker，避免两份任务台账同时控制同一批达人。
 */
export default function BatchTasksPanel(){
 const [data,setData]=useState<Listing|null>(null),[selected,setSelected]=useState<string|null>(null);
 const [busy,setBusy]=useState(false),[error,setError]=useState("");
 const refresh=useCallback(async()=>{setBusy(true);setError("");try{
  const response=await fetch("/api/batch-tasks",{cache:"no-store"});
  if(!response.ok)throw Error();
  const value=await response.json() as Listing;setData(value);
 }catch{setError("暂时无法读取历史准备任务；当前发送池与冻结批次不受影响。");}
 finally{setBusy(false);}},[]);
 useEffect(()=>{void refresh();},[refresh]);
 const task=data?.tasks.find(item=>item.id===selected)??data?.tasks[0]??null;
 return <Card title="历史准备任务" subtitle="只读追溯早期整批准备台账；当前新批次只使用“发送池与发送”页签。"
  action={<Pill tone="neutral">历史只读</Pill>}>
  <div className="space-y-4 p-5">
   <Notice>这块不再创建任务、不修改状态、也不启动准备 worker。历史任务、候补和准备证据完整保留；当前发送以冻结批次的候选、revision 和明确“确认并开始”为唯一事实源。</Notice>
   {data?.worker.running&&<Notice tone="warning">检测到历史准备 worker 仍在运行。页面不会控制它；需先核对进程和断点再决定是否处理。</Notice>}
   {error&&<Notice tone="warning">{error}</Notice>}
   {!data&&!error?<p className="py-6 text-sm text-gray-500">正在读取历史任务…</p>:data?.tasks.length===0?<EmptyState title="没有历史准备任务" description="当前批次直接从发送池预览并冻结，不需要在这里新建。"/>:task&&<div className="grid gap-5 xl:grid-cols-[260px_minmax(0,1fr)]">
    <nav aria-label="历史准备任务列表" className="space-y-2">{data?.tasks.map(item=><button type="button" key={item.id} onClick={()=>setSelected(item.id)} aria-current={task.id===item.id?"true":undefined} className={`w-full rounded-xl border p-4 text-left ${task.id===item.id?"border-brand-300 bg-brand-50 dark:bg-brand-500/10":"border-gray-200 dark:border-gray-700"}`}><div className="flex items-center justify-between gap-2"><span className="font-semibold">意大利 · {item.spec.target.toLocaleString()} 人</span><Pill tone="neutral">{stateLabel(item.state)}</Pill></div><p className="mt-2 break-all font-mono text-[11px] text-gray-400">{item.id}</p><p className="mt-2 text-xs text-gray-500">最后检查：{time(item.checkedAt)}</p></button>)}</nav>
    <section className="min-w-0 space-y-4">
     <div className="flex flex-wrap items-start justify-between gap-3"><div><h3 className="text-base font-semibold text-gray-800 dark:text-white">历史整批准备结果</h3><p className="mt-1 text-xs text-gray-500">创建于 {time(task.created)} · revision {task.revision} · 不具备当前发送授权</p></div><Button size="sm" variant="outline" disabled={busy} onClick={()=>void refresh()}>{busy?"读取中…":"刷新只读状态"}</Button></div>
     <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">{[["正式目标",task.spec.target],["历史候补",task.spec.reserve],["本地候选",task.preparation?.candidates??0],["旧任务核验就绪",task.preparation?.verifiedReady??0]].map(([label,value])=><div key={String(label)} className="rounded-xl bg-gray-50 p-4 dark:bg-gray-800"><p className="text-xs text-gray-500">{label}</p><p className="mt-2 text-xl font-semibold tabular-nums">{Number(value).toLocaleString()}</p></div>)}</div>
     {task.preparation&&<div className="rounded-xl border border-gray-200 p-4 dark:border-gray-700"><p className="text-sm font-medium">历史准备口径：{task.preparation.candidates.toLocaleString()} / {task.preparation.required.toLocaleString()} 位，缺口 {task.preparation.candidateGap.toLocaleString()}</p><div className="mt-3 space-y-1">{task.preparation.blockers.map(code=><p key={code} className="text-xs leading-5 text-gray-500">{blockers[code]??code}</p>)}</div></div>}
     {task.sourcePreparation&&<div className="rounded-xl border border-gray-200 p-4 dark:border-gray-700"><h4 className="text-sm font-medium">历史线索准备</h4><p className="mt-2 text-sm text-gray-500">规划 PID {task.sourcePreparation.pids.toLocaleString()} · 保存页面 {task.sourcePreparation.pages.toLocaleString()} · 线索选择 {task.sourcePreparation.selectionRecorded?task.sourcePreparation.edges?.toLocaleString()??"未记录":"历史未记录"}</p>{!task.sourcePreparation.selectionRecorded&&<p className="mt-2 text-xs leading-5 text-warning-600">旧数据库没有保存后来新增的选择明细表，因此不能把这项显示成 0；PID、页面和任务状态仍按原账本展示。</p>}<p className="mt-2 text-xs text-gray-400">{Object.entries(task.sourcePreparation.states).map(([state,count])=>`${state} ${count}`).join(" · ")}</p></div>}
     <details><summary className="cursor-pointer text-sm text-brand-500">查看最近历史事件</summary><div className="mt-3 space-y-1">{task.events.map((event,index)=><p key={`${event.kind}-${index}`} className="text-xs text-gray-500">{time(event.at)} · {event.kind}</p>)}</div></details>
    </section>
   </div>}
  </div>
 </Card>;
}
