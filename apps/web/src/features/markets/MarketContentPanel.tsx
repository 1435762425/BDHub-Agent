"use client";
import {useEffect,useState} from "react";
import {Button,Card,Notice,Pill} from "../bdhub/ui";
import type {TemplateLibraryState} from "../../server/template-library/bridge";

export default function MarketContentPanel({market}:{market:string}){
 const [data,setData]=useState<TemplateLibraryState|null>(null),[busy,setBusy]=useState<string|null>(null),[error,setError]=useState("");
 const load=async(signal?:AbortSignal)=>{const response=await fetch(`/api/template-library?market=${encodeURIComponent(market)}`,{cache:"no-store",signal});if(!response.ok)throw Error();setData(await response.json());setError("");};
 useEffect(()=>{const controller=new AbortController();void load(controller.signal).catch(()=>{});return()=>controller.abort();},[market]);
 const review=async(templateId:string,state:"approved"|"rejected")=>{if(!data)return;const row=data.review.items.find(item=>item.templateId===templateId);if(!row)return;setBusy(templateId);setError("");try{const response=await fetch("/api/template-library",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action:"review_send",market,requestId:`template-review-${crypto.randomUUID()}`,templateId,state,expectedRevision:row.revision})});if(!response.ok)throw Error();setData(await response.json());}catch{setError("审核状态没有保存；内容或 revision 可能已变化，请刷新后重试。");}finally{setBusy(null);}};
 if(!data)return <Card title="市场语言模板"><div className="p-5 text-sm text-gray-500">正在读取本地化模板…</div></Card>;
 return <Card title="市场语言模板" subtitle={`所有对达人可见的内容固定使用 ${data.languageLabel}（${data.locale}）。`} action={<Pill tone={data.review.ready?"success":"warning"}>已审核 {data.review.approved}/{data.review.total}</Pill>}>
  <div className="space-y-5 p-5">
   <Notice tone={data.review.ready?"success":"warning"}>{data.review.ready?"已达到审核门槛；同一达人会自动轮换未使用过的话术。":`至少审核通过 ${data.review.minimumApproved} 条后，持续发送才允许开启；当前真实发送保持关闭。`}</Notice>
   {error&&<Notice tone="warning">{error}</Notice>}
   <div><p className="mb-2 text-xs font-medium text-gray-500">二发模板</p><div className="grid gap-3 lg:grid-cols-2">{data.sendTemplates.map(row=>{const status=data.review.items.find(item=>item.templateId===row.id);return <div key={row.id} className="rounded-xl border border-gray-200 p-3 dark:border-gray-800"><div className="flex items-center justify-between gap-2"><p className="text-sm font-medium">{row.name}</p><Pill tone={status?.state==="approved"?"success":status?.state==="rejected"?"warning":"neutral"}>{status?.state==="approved"?"已通过":status?.state==="rejected"?"已拒绝":"待审核"}</Pill></div><p className="mt-2 text-xs leading-5 text-gray-600 dark:text-gray-300">{row.bodyIt}</p>{row.translationZh&&<p className="mt-2 text-xs leading-5 text-gray-400">中文语义：{row.translationZh}</p>}<div className="mt-3 flex gap-2"><Button size="sm" disabled={busy===row.id} onClick={()=>void review(row.id,"approved")}>通过</Button><Button size="sm" variant="outline" disabled={busy===row.id} onClick={()=>void review(row.id,"rejected")}>拒绝</Button></div></div>})}</div></div>
   <div><p className="mb-2 text-xs font-medium text-gray-500">Agent 固定回复</p><div className="grid gap-3 lg:grid-cols-3">{data.agentTemplates.map(row=><div key={row.id} className="rounded-xl border border-gray-200 p-3 dark:border-gray-800"><p className="text-xs font-medium text-gray-500">{row.action}</p><p className="mt-2 text-xs leading-5">{row.text}</p></div>)}</div></div>
   <Notice tone="info">模板语义在各市场共用；审核状态也共用。各市场只使用自己的语言版本，禁止回退到意大利语。</Notice>
  </div>
 </Card>;
}
