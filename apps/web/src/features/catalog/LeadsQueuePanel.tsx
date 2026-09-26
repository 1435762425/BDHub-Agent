"use client";
import {Button,Card,MetricTable,Notice,Pill,Progress} from "../bdhub/ui";
import type {LeadsRunState} from "../../server/leads-queue/bridge";
import type {LeadsQueueController} from "./useLeadsQueue";

const stamp=(value:number|null|undefined)=>value?new Date(value*1000).toLocaleString("zh-CN",{timeZone:"Asia/Shanghai",hour12:false}):"—";
// A stop code means "the batch ended without finishing every product", and each one has a
// different next action. Raw codes on the page told the operator nothing.
const STOP:Record<string,string>={queue_empty:"队列里没有可查的商品。",browser_lock_busy:"另一个 Kalodata 读取器正占用浏览器锁——本次一个商品都没查，也没消耗额度，等它结束再点一次即可。",browser_lock_missing:"浏览器锁文件不存在，抓取器可能未就绪。",kalodata_daily_quota_exhausted:"平台今日详情额度已用完，断点已保留；系统按原任务核对额度恢复。",kalodata_auth_required:"抓取身份失效，请先到系统与运维页测身份。"};

function runLine(run:LeadsRunState){
 const done=(run.done??0).toLocaleString(),targets=(run.targets??0).toLocaleString();
 const leads=(run.leads??0).toLocaleString(),requests=(run.networkRequests??0).toLocaleString();
 if(run.running)return `正在查询：已完成 ${done} / ${targets}，取得线索 ${leads} 条，已发 ${requests} 次只读请求。`;
 return `上一批：完成 ${done} 个，取得线索 ${leads} 条，只读请求 ${requests} 次。${run.stopped?(STOP[run.stopped]??`停在 ${run.stopped}。`):"正常结束。"}`;
}

const FINISHED:Record<string,string>={completed:"已完成",quota_exhausted:"额度用尽，断点保留",failed:"失败",needs_human:"需核对",stopped:"已停止"};
const n=(value:number|null)=>value==null?"未记录":value.toLocaleString();
// What the scheduler is doing with Kalodata now, and what its last finished stage recorded (H05).
function LastRun({run}:{run:LeadsRunState|null|undefined}){
 const last=run?.lastFinished;
 const now=run?.running?<Pill tone="brand">正在读取</Pill>:run?.queued?<Pill tone="neutral">已排队，等待 Kalodata 资源</Pill>:<Pill tone="neutral">当前没有运行</Pill>;
 return <div className="rounded-xl border border-gray-200 p-4 text-sm dark:border-gray-800"><div className="flex flex-wrap items-center justify-between gap-2"><p className="font-medium">最近一次抓取</p>{now}</div>
  {last?<><p className="mt-2 text-xs text-gray-500">{FINISHED[last.state]??"状态待核实"} · 结束于 {stamp(last.finishedAt)}{last.sliceComplete?" · 本段预算用完即结束，不代表全队列已抓完":""}</p>
   <MetricTable rows={[{label:"完成的逻辑查询",value:n(last.completedQueries),detail:`A ${n(last.aQueries)} · B ${n(last.bQueries)}；同一 PID 的 A、B 各算一个查询`},
    {label:"读取片段",value:n(last.fragments),detail:"每段最多 3 个请求后轮换"},
    {label:"网络请求",value:n(last.networkRequests),detail:"实际发出的只读请求；复用已保存页面不计"},
    {label:"出错的查询",value:n(last.errorCount),detail:last.errors.length?last.errors.join("、"):last.errorCode?`阶段错误：${last.errorCode}`:"无"},
    ...(last.contribution?[{label:"新增达人×商品",value:last.contribution.newPairs.toLocaleString(),detail:`本段 ${last.contribution.publications} 次发布（A ${last.contribution.aPublications} · B ${last.contribution.bPublications}）里此前没有的组合；还需身份和资格检查才进入发送池`},
     {label:"刷新已有",value:last.contribution.refreshedPairs.toLocaleString(),detail:"已有组合换了新窗口，不算新增"},
     {label:"全新达人",value:last.contribution.newCreators.toLocaleString(),detail:"该市场此前任何商品下都没出现过的达人"}]
     :[{label:"新增达人×商品",value:"未记录",detail:"这段没有发布记录，或发生在开始记录之前"}])]}/>
   {!last.resultRecorded&&<p className="text-xs text-gray-400">这一段没有发布结果，计数未记录（不是 0）。</p>}</>
  :<p className="mt-2 text-xs text-gray-500">还没有已结束的抓取记录。</p>}
 </div>;
}

/** The PID -> creator-lead queue. State is owned by the page so the funnel bar reads the same numbers. */
export default function LeadsQueuePanel({controller}:{controller:LeadsQueueController}){
 const {data,busy,message,startRun,loaded}=controller;
 const due=data?.nextDue?.[0];
 // "Already queried" has to exclude both never-asked products and the ones parked after repeated
 // failures, or the coverage bar would count work that never reached the platform as done.
 const queried=data?Math.max(0,data.scope-data.firstTime-data.stuck):0;
 if(data?.rolling){const queue=data.rolling,control=queue.control;return <Card title="A/B 持久读取队列" subtitle="先续断点，再给新／到期项机会；A/B 每次最多 3 个请求后轮换。每 7 天到期，不每天重扫。"><div className="space-y-4 p-5">{!queue.automaticEnabled&&<Notice tone="info">自动运营总开关当前关闭，队列与断点保留。手动推进本次流程不会开启持续任务。</Notice>}{queue.identityHold&&<Notice tone="info">身份阶段因原有故障等待恢复。已确认身份的线索继续使用，A/B 读取不被阻塞；原失败记录保留。</Notice>}<MetricTable rows={(["A","B"] as const).flatMap(kind=>{const q=queue.types[kind];return [
 {label:`${kind} · 待首次查询`,value:q.first.toLocaleString(),detail:kind==="A"?"近 14 天，每 PID 最多 50 位正销量达人":"近 30 天，单视频至少 1,000 播放"},
 {label:`${kind} · 到期刷新`,value:q.refresh.toLocaleString(),detail:"旧到期任务与新任务按等待起点公平排序"},
 {label:`${kind} · 可续断点`,value:q.checkpoints.toLocaleString(),detail:`保持原窗口与页码；最早窗口截至 ${q.oldestWindowEnd??"—"}${q.staleCheckpoints?`，${q.staleCheckpoints} 项窗口已陈旧，完成后立即排新窗口`:""}`},
 {label:`${kind} · 技术隔离`,value:(q.states.isolated??0).toLocaleString(),detail:"保留错误，其他商品继续；不需人工逐项处理"},
 {label:`${kind} · 材料暂停`,value:(q.states.material_paused??0).toLocaleString(),detail:"没有有效材料时不消耗查询额度"},
 {label:`${kind} · 最早等待`,value:stamp(q.oldestReadyAt),detail:`队列共 ${q.total.toLocaleString()} 个 PID，当前可运行 ${q.runnable.toLocaleString()}`},
 ];})}/>{control?.state==="waiting_quota"?<Notice tone="info">该市场等待 Kalodata 额度。下次核对：{stamp(control.retry_at)}。继续原窗口和断点；其他市场独立运行。</Notice>:control?.state==="waiting_account"?<Notice tone="info">读取通道暂不可用，任务与断点保留。下次检查：{stamp(control.retry_at)}。</Notice>:null}<LastRun run={data.run}/>{queue.recentOutcomes&&<div className="rounded-xl border border-gray-200 p-4 text-sm dark:border-gray-800"><p className="font-medium">近 7 天发布的线索后来怎样</p><p className="mt-1 text-xs text-gray-500">每次发送只算到它发送时冻结的那条来源；回复指卡片发出后达人发来的新消息。观察窗口仍在进行，近期发布的数字会继续变化，不是因果转化，也不含成交金额。</p>
  <MetricTable rows={(["A","B"] as const).map(kind=>{const o=queue.recentOutcomes![kind];return {label:`${kind} 类`,value:`${o.creatorsReplied.toLocaleString()} 位回复`,detail:`发布来源 ${o.publishedSources.toLocaleString()} · 被采用 ${o.deliveries.toLocaleString()} 次 · 确认发出 ${o.sentDeliveries.toLocaleString()} 次 · 触达 ${o.creatorsReached.toLocaleString()} 位${o.creatorsReached?`（回复率 ${(o.creatorsReplied/o.creatorsReached*100).toFixed(1)}%）`:""}`};})}/></div>}<Button size="sm" disabled={busy||Boolean(data.run?.running)||Boolean(data.run?.queued)} onClick={()=>void startRun()}>推进一段 A/B 查询</Button>{message&&<Notice tone="info">{message}</Notice>}</div></Card>;}
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
   {label:"A 类销售线索",value:"14 天",detail:"每 PID 最多 50 位正销量达人；按数值 GMV 降序，未查 PID 按商品累计销量优先"},
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
