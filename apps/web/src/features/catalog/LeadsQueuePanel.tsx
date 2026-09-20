"use client";
import {Button,Card,Field,Input,MetricTable,Notice,Pill,Progress} from "../bdhub/ui";
import type {LeadsRunState} from "../../server/leads-queue/bridge";
import type {LeadsQueueController} from "./useLeadsQueue";

const stamp=(value:number|null|undefined)=>value?new Date(value*1000).toLocaleString("zh-CN",{timeZone:"Asia/Shanghai",hour12:false}):"—";
// A stop code means "the batch ended without finishing every product", and each one has a
// different next action. Raw codes on the page told the operator nothing.
const STOP:Record<string,string>={queue_empty:"队列里没有可查的商品。",browser_lock_busy:"另一个 Kalodata 读取器正占用浏览器锁——本次一个商品都没查，也没消耗额度，等它结束再点一次即可。",browser_lock_missing:"浏览器锁文件不存在，抓取器可能未就绪。",kalodata_daily_quota_exhausted:"平台今日详情额度已用完，断点已保留，明天再点。",kalodata_auth_required:"抓取身份失效，请先到系统与运维页测身份。"};

function runLine(run:LeadsRunState){
 const done=(run.done??0).toLocaleString(),targets=(run.targets??0).toLocaleString();
 const leads=(run.leads??0).toLocaleString(),requests=(run.networkRequests??0).toLocaleString();
 if(run.running)return `正在查询：已完成 ${done} / ${targets}，取得线索 ${leads} 条，已发 ${requests} 次只读请求。`;
 return `上一批：完成 ${done} 个，取得线索 ${leads} 条，只读请求 ${requests} 次。${run.stopped?(STOP[run.stopped]??`停在 ${run.stopped}。`):"正常结束。"}`;
}

/** The PID -> creator-lead queue. State is owned by the page so the funnel bar reads the same numbers. */
export default function LeadsQueuePanel({controller}:{controller:LeadsQueueController}){
 const {data,draft,setDraft,busy,message,save,startRun,loaded}=controller;
 const due=data?.nextDue?.[0];
 // "Already queried" has to exclude both never-asked products and the ones parked after repeated
 // failures, or the coverage bar would count work that never reached the platform as done.
 const queried=data?Math.max(0,data.scope-data.firstTime-data.stuck):0;
 return <Card title="达人线索查询队列"><div className="space-y-4 p-5">
  {!draft&&<p className="text-sm text-gray-500">{loaded?"暂时无法读取查询队列。":"读取中…"}</p>}
  {draft&&data&&<>
  <MetricTable rows={[
   {label:"队列 PID（总数）",value:data.scope.toLocaleString(),detail:`合格 ${data.eligible.toLocaleString()} ∩ 有链接 ${data.linked.toLocaleString()}`},
   {label:"首次待查",value:data.firstTime.toLocaleString(),detail:"从没问过平台，按累计销量降序",accent:true},
   {label:"到期待刷新",value:data.due.toLocaleString(),detail:"距上次查询已满刷新周期"},
   {label:"冷却中",value:data.waiting.toLocaleString(),detail:`${data.refreshDays} 天内查过，下次 ${due?stamp(due.dueAt):`${data.refreshDays} 天后`}`},
   {label:"连续失败暂停",value:data.stuck.toLocaleString(),detail:"失败到上限，已移出队列"},
   {label:"本次将跑",value:data.taken.toLocaleString(),detail:`上限 ${data.batchSize.toLocaleString()}；不使用冷却中的 PID 补足`},
  ]}/>
  <Progress done={queried} total={data.scope} label="线索覆盖（已查过 / 队列总数）"/>
  <div className="grid gap-4 lg:grid-cols-3">
   <Field label="刷新周期（天）" hint="已查过的 PID 多久重查一次。"><Input type="number" min={1} max={90} value={draft.refreshDays} onChange={e=>setDraft({...draft,refreshDays:Number(e.target.value)})}/></Field>
   <Field label="每 PID 线索数" hint="平台按 GMV 排序，取销量大于 0 的前若干位。"><Input type="number" min={1} max={50} value={draft.leadsPerPid} onChange={e=>setDraft({...draft,leadsPerPid:Number(e.target.value)})}/></Field>
   <Field label="统计窗口（天）" hint="沿用平台 T−2 窗口，首尾都计入。"><Input type="number" min={1} max={30} value={draft.windowDays} onChange={e=>setDraft({...draft,windowDays:Number(e.target.value)})}/></Field>
  </div>
  <div className="grid gap-4 lg:grid-cols-2">
   <Field label="一次跑多少条（上限）" hint="从队首往下取这么多条。这是上限不是目标——队列不够就少跑，绝不用未到期的 PID 补足。"><Input type="number" min={1} max={5000} value={draft.batchSize} onChange={e=>setDraft({...draft,batchSize:Number(e.target.value)})}/></Field>
   <Field label="连续失败几次后暂停该商品" hint="查询失败的不会记成已查，会留在队首重试；连续失败到次数后移出队列，避免一直消耗额度。"><Input type="number" min={1} max={20} value={draft.maxAttempts} onChange={e=>setDraft({...draft,maxAttempts:Number(e.target.value)})}/></Field>
  </div>
  <div className="flex flex-wrap items-center gap-2"><Button size="sm" variant="outline" disabled={busy} onClick={()=>void save()}>{busy?"保存中…":"保存队列设置"}</Button><Button size="sm" disabled={busy||Boolean(data.run?.running)} onClick={()=>void startRun()}>{data.run?.running?"正在跑…":"立即运行这一批"}</Button>{data.run?.running?<Pill tone="brand">查询中</Pill>:<Pill tone="neutral">就绪</Pill>}</div>
  {message&&<Notice tone="info">{message}</Notice>}
  {data.run&&<div className="rounded-xl border border-gray-200 p-4 dark:border-gray-700">
   {(data.run.targets??0)>0
    ?<Progress done={data.run.done??0} total={data.run.targets??0} label={data.run.running?"查询进度":"上一批进度"}/>
    :<p className="text-sm text-gray-500">{data.run.running?"正在查询：已启动，等第一个商品返回…":"上一批没有开始查询。"}</p>}
   <p className="mt-2 text-xs leading-5 text-gray-500">{runLine(data.run)}</p>
  </div>}
  </>}
 </div></Card>;
}
