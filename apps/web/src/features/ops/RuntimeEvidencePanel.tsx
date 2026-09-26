"use client";

import {useEffect,useState} from "react";
import {Card,MetricTable,Notice,Pill} from "../bdhub/ui";
import type {OpsAlerts,RuntimeEvidence} from "@/server/ops-alerts/bridge";

// Read-only evidence for "can I trust what the pages say?" (G21/U3). It separates a process existing,
// the code it actually loaded and the last recorded check; nothing here starts, stops or restores anything.
const time=(value:number|null)=>value?new Date(value*1000).toLocaleString("zh-CN",{timeZone:"Asia/Shanghai",hour12:false}):"—";
const short=(value:string|null)=>value?value.slice(0,7):"未记录";
const ROLE_LABEL:Record<string,string>={scheduler:"调度器","im-session":"会话收信","agent-reply":"AI 回复","market-send":"发送","continuous-send":"持续发送"};
const roleLabel=(role:string)=>{const market=role.match(/-(it|br|my|uk)$/)?.[1];const base=market?role.slice(0,-market.length-1):role;return `${ROLE_LABEL[base]??role}${market?` · ${market.toUpperCase()}`:""}`;};
const DRILL_STATE:Record<string,string>={restorable:"通过：可在隔离目录恢复",blocked:"未通过：存在阻断项"};

export default function RuntimeEvidencePanel(){
 const [evidence,setEvidence]=useState<RuntimeEvidence|null>(null),[checkedAt,setCheckedAt]=useState<number|null>(null),[failed,setFailed]=useState(false),[missing,setMissing]=useState(false);
 useEffect(()=>{let stopped=false,timer:ReturnType<typeof setTimeout>|undefined;
  const poll=async()=>{if(stopped)return;
   if(document.visibilityState==="visible"){try{const response=await fetch("/api/ops-alerts",{cache:"no-store"});if(!response.ok)throw Error();const value=await response.json() as OpsAlerts;
    if(!stopped){setEvidence(value.evidence);setMissing(value.evidence==null);setCheckedAt(value.checkedAt);setFailed(false);}}catch{if(!stopped)setFailed(true);}}
   if(!stopped)timer=setTimeout(poll,60_000);};
  const visible=()=>{if(document.visibilityState==="visible"){if(timer)clearTimeout(timer);void poll();}};
  document.addEventListener("visibilitychange",visible);void poll();
  return()=>{stopped=true;if(timer)clearTimeout(timer);document.removeEventListener("visibilitychange",visible);};},[]);
 const stale=evidence?.processes.filter(row=>!row.current)??[];
 return <div className="space-y-5">
  {failed&&<Notice tone="warning">运行证据暂时读不到，状态待核实{checkedAt?`；以下是 ${time(checkedAt)} 的记录`:""}。</Notice>}
  {missing&&<Notice tone="warning">当前后端未提供运行证据，状态待核实。</Notice>}
  <Card title="代码版本" subtitle={`读取于 ${time(checkedAt)} · 进程存在不代表业务在推进，业务进度看运营首页与作业页`} action={evidence?<Pill tone={stale.length?"warning":"success"}>{stale.length?`${stale.length} 个进程不是当前代码`:"全部为当前代码"}</Pill>:undefined}>
   <div className="space-y-3 p-5">{evidence?.runtimeDirty&&<Notice tone="warning">scripts/ 或 vendor/ 有未提交的改动：下一次拉起的进程会读到它们，这些进程不算与当前代码一致。</Notice>}{evidence?<MetricTable rows={[{label:"仓库当前提交",value:short(evidence.head),detail:"下一次启动的进程会载入它"},
    ...evidence.processes.map(row=>({label:roleLabel(row.role),value:<span className="font-mono">{short(row.sha)}</span>,
     detail:`PID ${row.pid} · 启动于 ${time(row.startedAt)}${row.current?"":" · 与当前代码不同，按发布流程安全重启后生效"}`,accent:!row.current}))]}/>
    :<p className="text-sm text-gray-500">{failed?"读不到进程登记。":"正在读取…"}</p>}</div>
  </Card>
  {evidence&&<div className="grid gap-5 lg:grid-cols-3">
   <Card title="调度器"><div className="space-y-2 p-5 text-sm"><Pill tone={evidence.scheduler.running?"success":"warning"}>{evidence.scheduler.running?"进程在运行":"未运行"}</Pill><p className="text-xs leading-5 text-gray-500">最近心跳 {time(evidence.scheduler.checkedAt)}</p></div></Card>
   <Card title="AI 模型服务"><div className="space-y-2 p-5 text-sm"><Pill tone={evidence.modelService.paused?"warning":"neutral"}>{evidence.modelService.paused?"暂停中":"未暂停"}</Pill>
    <p className="text-xs leading-5 text-gray-500">{evidence.modelService.paused?`下次尝试 ${time(evidence.modelService.nextAt)}；待答问题保留。`:"没有记录服务级暂停；单个问题的失败仍在会话页显示。"}</p>
    {evidence.modelService.lastError&&<details className="text-xs text-gray-400"><summary className="cursor-pointer">诊断</summary><code>{evidence.modelService.lastError}</code></details>}</div></Card>
   <Card title="恢复演练"><div className="space-y-2 p-5 text-sm">{evidence.restoreDrill?<>
    <Pill tone={evidence.restoreDrill.state==="restorable"?"success":"warning"}>{DRILL_STATE[evidence.restoreDrill.state]??"状态待核实"}</Pill>
    <p className="text-xs leading-5 text-gray-500">备份 {evidence.restoreDrill.backup} · 演练于 {time(evidence.restoreDrill.finishedAt)}。演练在隔离目录只读进行，不代表已做异机备份。</p>
    {evidence.restoreDrill.blockers.length>0&&<details className="text-xs text-gray-400"><summary className="cursor-pointer">阻断项</summary><ul className="mt-1 space-y-1">{evidence.restoreDrill.blockers.map(item=><li key={item}><code>{item}</code></li>)}</ul></details>}
   </>:<p className="text-xs leading-5 text-gray-500">还没有恢复演练记录。演练用 <code>scripts/state-backup.py drill --backup …</code> 在隔离目录只读执行。</p>}</div></Card>
  </div>}
 </div>;
}
