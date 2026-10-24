"use client";
import {Button,Card,Field,Input,Notice,Pill,StatTile,Toggle} from "../bdhub/ui";
import type {SendController} from "./useContinuousSend";
import SendTemplateManager from "./SendTemplateManager";

const labels:Record<string,string>={off:"未开启",stopped:"已停止",starting:"启动中",confirmed:"已确认",waiting_window:"等待发送窗口",waiting_pool:"等待可发达人",waiting_account:"等待账号空闲",waiting_transport:"连接恢复中",sending:"发送中",waiting_capacity:"等待可用额度",waiting_platform_refusal:"平台拒绝该达人，继续发送",paused:"已暂停",attention:"发送已停止，需关注",waiting_reconciliation:"等待核验",first_send_requires_page_start:"等待首次启动",canary_confirmed:"首条已确认，等待继续",waiting_canary_authorization:"等待首发授权核对"};
const number=(value:number|null|undefined)=>value==null?"—":value.toLocaleString("zh-CN");
const time=(value:number|null)=>value?new Date(value*1000).toLocaleString("zh-CN",{timeZone:"Asia/Shanghai",hour12:false}):"—";

export default function ContinuousSendPanel({market,controller}:{market:string;controller:SendController}){
 const {data,draft,setDraft,busy,message,loaded,save,start,stop,reconcile}=controller;
 if(!data||!draft)return <Card title="发送状态"><div className="p-5 text-sm text-gray-500">{loaded?"暂时无法读取发送状态。":"正在读取发送状态…"}</div></Card>;
 const dirty=draft.automaticEnabled!==data.control.automaticEnabled||draft.template!==data.control.template||draft.window.some((value,index)=>value!==data.control.window[index]);
 const active=["waiting_window","waiting_pool","waiting_account","waiting_transport","sending","waiting_capacity","waiting_platform_refusal","waiting_reconciliation","paused"].includes(data.runtime.state)&&!data.control.stopRequested;
 const hasUnknown=data.runtime.unknown>0||data.unknownDeliveries.length>0||data.runtime.state==="waiting_reconciliation";
 return <div className="space-y-5">
  <Card title="发送状态" action={<div className="flex flex-wrap items-center gap-3"><Pill tone={hasUnknown||data.runtime.state==="attention"?"warning":data.runtime.state==="sending"?"success":"neutral"}>{labels[data.runtime.state]??"状态待核实"}</Pill>{!active?<Button disabled={busy} onClick={()=>void start()}>开始发送</Button>:<Button variant="outline" disabled={busy} onClick={()=>void stop()}>停止发送</Button>}</div>}>
   <div className="flex flex-wrap items-center justify-between gap-x-5 gap-y-2 px-5 pt-4 text-sm"><p className="text-gray-600 dark:text-gray-300">每日 {data.control.window[0]}–{data.control.window[1]} <span className="text-xs text-gray-400">北京时间</span></p><p className="text-xs text-gray-500">{data.window.open?"当前在发送窗口内":"当前在发送窗口外"} · 自动发送{data.control.automaticEnabled?"已开启":"未开启"}</p></div>
   {hasUnknown&&<div className="px-5 pt-4"><Notice tone="warning"><div className="flex flex-wrap items-center justify-between gap-3"><p><strong>{data.runtime.unknown>0?`${number(data.runtime.unknown)} 次发送结果未知。`:"有发送结果待核验。"}</strong> 核验原发送，不会重复发送。</p>{data.unknownDeliveries.length>0&&<Button size="sm" variant="outline" disabled={busy} onClick={()=>void reconcile()}>核验原发送</Button>}</div></Notice></div>}
   {data.runtime.state==="waiting_transport"&&<p className="px-5 pt-4 text-sm text-gray-500">连接恢复中，等待下次自动重试；已提交的发送仍沿原记录核验。</p>}
   {message&&<div className="px-5 pt-4"><Notice tone={message.includes("已")?"success":"warning"}>{message}</Notice></div>}
   <div className="grid grid-cols-2 gap-3 p-5"><StatTile label="今日确认触达" value={data.runtime.confirmedToday} hint="位达人 · 北京时间今日" brand/><StatTile label="发送池剩余" value={data.poolRemaining??"—"} hint={data.poolRemaining==null?"暂未读取":"位当前可发达人"}/></div>
   {data.runtime.state==="sending"&&<p className="px-5 pb-4 text-xs text-gray-500">最近 5 分钟速度：{number(data.runtime.speedPerMinute)} 达人/分钟</p>}
   <details className="mx-5 mb-5 rounded-xl bg-gray-50 px-4 py-3 text-xs leading-6 text-gray-500 dark:bg-gray-800"><summary className="cursor-pointer">发送详情与回执</summary><div className="mt-2 grid gap-x-6 sm:grid-cols-2"><p className="break-all">当前达人：{data.runtime.currentCreatorId??"—"}</p><p className="break-all">当前 PID：{data.runtime.currentPid??"—"}</p><p className="break-all">停止原因：{data.runtime.stopReason??"无"}</p><p>最近更新：{time(data.runtime.seenAt)}</p><p>最近 5 分钟速度：{number(data.runtime.speedPerMinute)} 达人/分钟</p><p>结果未知：{number(data.runtime.unknown)} 次</p><p>明确失败：{number(data.runtime.failedKnown)} 次</p><p>{data.capacity?`24 小时新联系：${number(data.capacity.used)} · ${data.capacity.limit==null?"上限由平台额度决定":`本地额度 ${number(data.capacity.limit)}`}`:"新联系额度暂未读取"}</p></div></details>
  </Card>
  <details open={dirty||undefined} className="rounded-2xl border border-gray-200 bg-white dark:border-gray-800 dark:bg-white/[0.03]"><summary className="cursor-pointer px-5 py-4 text-sm font-medium">发送设置与操作{dirty&&<span className="ml-3 text-xs font-normal text-warning-600">有未保存更改</span>}</summary>
   <div className="space-y-5 p-5 pt-0"><div className="grid gap-5 lg:grid-cols-3"><Toggle label="持续自动发送" description="每天在设定窗口内继续发送。" checked={draft.automaticEnabled} disabled={busy} onChange={value=>setDraft({...draft,automaticEnabled:value})}/><Field label="发送开始（北京时间）"><Input type="time" value={draft.window[0]} disabled={busy} onChange={event=>setDraft({...draft,window:[event.target.value,draft.window[1]]})}/></Field><Field label="发送结束（北京时间）"><Input value={draft.window[1]} maxLength={5} disabled={busy} onChange={event=>setDraft({...draft,window:[draft.window[0],event.target.value]})}/></Field></div>
    <Button variant="outline" disabled={busy||!dirty} onClick={()=>void save()}>{dirty?"保存设置":"设置已保存"}</Button>
   </div>
  </details>
  <details className="rounded-2xl border border-gray-200 bg-white dark:border-gray-800 dark:bg-white/[0.03]"><summary className="cursor-pointer px-5 py-4 text-sm font-medium">发送话术模板</summary><div className="p-5 pt-0"><SendTemplateManager market={market} controller={controller}/></div></details>
 </div>;
}
