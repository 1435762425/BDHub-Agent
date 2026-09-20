"use client";
import {Button,Card,MetricTable,Notice,Pill,Progress} from "../bdhub/ui";
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
 const {data,busy,message,startRun,loaded}=controller;
 const due=data?.nextDue?.[0];
 // "Already queried" has to exclude both never-asked products and the ones parked after repeated
 // failures, or the coverage bar would count work that never reached the platform as done.
 const queried=data?Math.max(0,data.scope-data.firstTime-data.stuck):0;
 return <Card title="达人线索查询队列"><div className="space-y-4 p-5">
  {!data&&<p className="text-sm text-gray-500">{loaded?"暂时无法读取查询队列。":"读取中…"}</p>}
  {data&&<>
  <MetricTable rows={[
   {label:"队列 PID（总数）",value:data.scope.toLocaleString(),detail:`合格 ${data.eligible.toLocaleString()} ∩ 有链接 ${data.linked.toLocaleString()}`},
   {label:"首次待查",value:data.firstTime.toLocaleString(),detail:"从没问过平台，按累计销量降序",accent:true},
   {label:"到期待刷新",value:data.due.toLocaleString(),detail:"距上次查询已满刷新周期"},
   {label:"冷却中",value:data.waiting.toLocaleString(),detail:`${data.refreshDays} 天内查过，下次 ${due?stamp(due.dueAt):`${data.refreshDays} 天后`}`},
   {label:"连续失败暂停",value:data.stuck.toLocaleString(),detail:"失败到上限，已移出队列"},
   {label:"本次将跑",value:data.taken.toLocaleString(),detail:`上限 ${data.batchSize.toLocaleString()}；不使用冷却中的 PID 补足`},
  ]}/>
  <Progress done={queried} total={data.scope} label="线索覆盖（已查过 / 队列总数）"/>
  <MetricTable rows={[
   {label:"A 类销售线索",value:"14 天",detail:"每 PID 最多 20 位正销量达人；按数值 GMV 降序，未查 PID 按商品累计销量优先"},
   {label:"A 类刷新",value:"7 天",detail:"到期后重读最近 14 天；历史证据保留，当前 head 原子切换"},
   {label:"B 类内容线索",value:"30 天",detail:"精确 PID 视频当前播放量 ≥1,000；同达人×PID只留最高播放量代表视频"},
   {label:"B 类刷新",value:"7 天",detail:"完整重读最近 30 天视频列表，发现后来跨过 1,000 的视频"},
   {label:"请求边界",value:"真实额度",detail:"不设每天500次或单 PID 50个视频的业务截断；额度耗尽保存断点"},
  ]}/>
  <div className="flex flex-wrap items-center gap-2"><Button size="sm" disabled={busy||Boolean(data.run?.running)} onClick={()=>void startRun()}>{data.run?.running?"正在跑…":"继续运行线索任务"}</Button>{data.run?.running?<Pill tone="brand">查询中</Pill>:<Pill tone="neutral">就绪</Pill>}</div>
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
