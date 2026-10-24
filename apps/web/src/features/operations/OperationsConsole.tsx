"use client";

import Link from "next/link";
import {useEffect,useState} from "react";
import {Card,Notice,PageHeading,Pill} from "../bdhub/ui";
import type {ConsoleMarket,FinishedStage,Lane,OperationsConsole as State} from "@/server/operations-console/bridge";
import type {MarketOverview} from "@/server/market-overview/bridge";

// 经营总览 (H12/H15): the first screen answers three questions -- which market needs a look, what needs
// me personally, and whether supply still has sendable stock. Run details sit below, collapsed.
// Read-only: nothing here starts, retries or claims work. Unknown values read "—", never 0.
const time=(value:number|null|undefined)=>value?new Date(value*1000).toLocaleString("zh-CN",{timeZone:"Asia/Shanghai",hour12:false,month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit"}):"—";
const minutes=(since:number|null,now:number)=>since?`${Math.max(0,Math.round((now-since)/60))} 分钟`:"—";
const num=(value:number|null|undefined,unit:string)=>value==null?"—":`${value.toLocaleString()} ${unit}`;
const STATE:Record<string,string>={completed:"已完成",quota_exhausted:"额度用尽，断点保留",failed:"失败",needs_human:"需核对",stopped:"已停止",
 skipped:"本轮跳过",running:"运行中",queued:"排队",sending:"发送中",waiting_window:"等待发送窗口",waiting_pool:"暂无可发达人",
 waiting_capacity:"等待可用额度",waiting_transport:"连接恢复中",waiting_reconciliation:"待核验",outside_reply_window:"回复窗口外",disabled:"未启用",off:"关闭",idle:"空闲",paused:"暂停",
 attention:"已停止，需关注",first_send_requires_page_start:"首发需在页面启动",confirmed:"已确认",cancelled:"已取消",partial_delivery:"部分送达",waiting_account:"等待账号",
 starting:"启动中",unverified:"状态待核实",waiting_send_window:"等待二发窗口结束",send_dispatch_active:"等待二发请求收口",waiting_dispatch:"等待在途核验",
 canary_confirmed:"首条已确认，已停止",pilot_complete_waiting_resume:"首条已确认，等待页面继续",pilot_waiting_confirmation:"首条结果尚未确认",waiting_canary_authorization:"首条授权归属待确认",original_intent_rechecked:"已核验原回复",waiting_reply_check:"等待原回复核验",reply_check_deferred:"回复核验暂缓"};
const label=(state:string|null)=>state?STATE[state]??"状态待核实":"—";
const tone=(state:string|null)=>state==="failed"||state==="needs_human"||state==="waiting_reconciliation"||state==="attention"?"warning":state==="running"||state==="sending"?"brand":state==="completed"?"success":"neutral";
const MARKET_NAME:Record<string,string>={it:"意大利",br:"巴西",my:"马来西亚",uk:"英国",de:"德国",fr:"法国",th:"泰国"};
function resource(key:string){
 if(key==="platform:global")return "平台读取槽（各市场共用）";
 if(key==="kalodata:global")return "Kalodata 读取槽";
 const [kind,name]=key.split(":");
 return kind==="workflow"?`${(name??"").toUpperCase()} 主链`:kind==="supply"?`供给账号 ${name}`:kind==="communications"?`通讯账号 ${name}`:key;
}
type Row=Extract<ConsoleMarket,{available:true}>;
const metric=(overview:MarketOverview|undefined,panel:string,key:string)=>{const p=overview?.panels.find(item=>item.id===panel);return p?.available?p.metrics.find(item=>item.key===key)?.value??null:null;};
function today(overview:MarketOverview|undefined){
 if(!overview?.activity.available)return null;
 const day=new Intl.DateTimeFormat("sv-SE",{timeZone:"Asia/Shanghai"}).format(new Date());
 return overview.activity.days.find(row=>row.date===day)??null;
}

/** One line for "what is this market doing": attention first, then the running or waiting stage. */
function Situation({row,labels,now}:{row:Row;labels:Record<string,string>;now:number}){
 if(row.needsReview)return <span className="text-warning-600">主链{label(row.needsReview.state)}{row.needsReview.errorCode?`（${row.needsReview.errorCode}）`:""}</span>;
 const current=row.current;
 if(!row.setting.automaticOperationsEnabled&&!row.run)return <span className="text-gray-400">自动运营未开启</span>;
 if(!current)return <span className="text-gray-500">主链本轮已走完</span>;
 const stage=labels[current.stage]??current.stage;
 if(current.state==="running")return <span>{stage}进行中 · 已 {minutes(current.since,now)}</span>;
 if(!current.waitingKnown)return <span className="text-gray-500">{stage}排队，原因待核实</span>;
 const wait=current.waitingOn[0];
 return <span className="text-gray-500">{stage}排队{wait?`，等 ${wait.heldBy.map(h=>`${h.market.toUpperCase()} ${labels[h.stage]??h.stage}`).join("、")||resource(wait.resource)}`:"，下一次调度领取"}</span>;
}

const LANE_REASON:Record<string,string>={status_stale:"采样已过期",worker_not_running:"进程未运行",worker_unknown:"进程待核实",
 status_missing:"尚无运行采样",previous_worker_sample:"等待新进程采样",control_changed:"等待设置生效后的采样",control_unavailable:"控制待核实",legacy_snapshot:"历史快照"};
function LaneLine({name,lane}:{name:string;lane:Lane|null}){
 if(!lane)return <p className="text-xs text-warning-700 dark:text-warning-400">{name}：状态待核实</p>;
 const caption=lane.observedAt?`${lane.statusSource==="control"?"控制核实":"采样"} ${time(lane.observedAt)}`:"尚无采样时间";
 return <div className="text-xs leading-5" title={caption}>
  <p className={!lane.fresh||tone(lane.state)==="warning"?"text-warning-700 dark:text-warning-400":"text-gray-700 dark:text-gray-300"}><span className="mr-2 text-gray-500">{name}</span>{lane.fresh?label(lane.state):"状态待核实"}</p>
  {!lane.fresh&&<p className="text-[11px] text-warning-700 dark:text-warning-400">{LANE_REASON[lane.statusReason??""]??"采样待核实"} · {caption}</p>}
 </div>;
}

function ReplyCount({day}:{day:ReturnType<typeof today>}){
 if(day?.serviceRepliesAi!=null&&day?.serviceRepliesManual!=null)return <span title={`AI ${day.serviceRepliesAi} · 人工 ${day.serviceRepliesManual}`}>{num(day.serviceRepliesAi+day.serviceRepliesManual,"条")}</span>;
 return <span>{num(day?.autoReplies,"条")}</span>;
}

function MarketStatus({row,labels,now}:{row:Row;labels:Record<string,string>;now:number}){
 return <div className="space-y-0.5"><LaneLine name="发送" lane={row.lanes.continuousSend}/><LaneLine name="回复" lane={row.lanes.agentReply}/>
  {(row.current||row.needsReview)&&<p className="pt-1 text-xs"><Situation row={row} labels={labels} now={now}/></p>}
  {row.selectionPaused&&<p className="pt-1 text-xs text-warning-700 dark:text-warning-400">选入等待平台验证 · 剩 {row.selectionPaused.pending??"—"} 个</p>}
 </div>;
}

function Finished({row,labels}:{row:FinishedStage|null;labels:Record<string,string>}){
 if(!row)return <span className="text-gray-400">—</span>;
 return <span>{labels[row.stage]??row.stage} <Pill tone={tone(row.state)}>{label(row.state)}</Pill> <span className="text-xs text-gray-400">{time(row.finishedAt)}{row.items!=null?` · 本轮 ${row.items.toLocaleString()} 项`:""}</span></span>;
}

const WRITES:Record<string,string>={zero:"没有平台写入",known:"有已记录的平台写入",uncertain:"平台写入情况待核实"};
// Read-only detail of one finished stage, from the row the console already returned.
function RunDetail({row}:{row:FinishedStage}){
 const took=row.startedAt&&row.finishedAt?`${Math.max(0,Math.round((row.finishedAt-row.startedAt)/60))} 分钟`:"—";
 return <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 rounded-lg bg-gray-50 px-3 py-2 text-xs dark:bg-white/[0.03]">
  <dt className="text-gray-400">开始 / 结束</dt><dd>{time(row.startedAt)} → {time(row.finishedAt)}（{took}）</dd>
  <dt className="text-gray-400">处理项数</dt><dd>{row.items!=null?row.items.toLocaleString():"未记录"}</dd>
  <dt className="text-gray-400">平台写入</dt><dd>{row.writeEvidence?WRITES[row.writeEvidence]??row.writeEvidence:"未记录"}</dd>
  {row.errorCode&&<><dt className="text-gray-400">原因代码</dt><dd className="font-mono text-warning-600">{row.errorCode}</dd></>}
  <dt className="text-gray-400">运行编号</dt><dd className="break-all font-mono text-gray-500">{row.runId}</dd>
  <dt className="text-gray-400">去处理</dt><dd><Link href={`/${row.market}/ops/jobs`} className="text-brand-500">{row.market.toUpperCase()} 作业页 →</Link></dd>
 </dl>;
}

export default function OperationsConsole(){
 const [data,setData]=useState<State|null>(null),[failed,setFailed]=useState(false);
 const [overviews,setOverviews]=useState<Record<string,MarketOverview>>({}),[overviewFailed,setOverviewFailed]=useState<string[]>([]);
 useEffect(()=>{let stopped=false,timer:ReturnType<typeof setTimeout>|undefined,controller:AbortController|null=null;
  const poll=async()=>{if(stopped)return;
   if(document.visibilityState==="visible"){controller?.abort();const current=new AbortController();controller=current;
    try{const response=await fetch("/api/operations-console",{cache:"no-store",signal:current.signal});if(!response.ok)throw Error();
     const value=await response.json() as State;if(!stopped){setData(value);setFailed(false);}
     // Each market's overview is cached server-side for 30 s and shared with its own page.
     const results=await Promise.all(value.markets.map(async row=>{try{const r=await fetch(`/api/market-overview?market=${row.market}`,{cache:"no-store",signal:current.signal});if(!r.ok)throw Error();return [row.market,await r.json() as MarketOverview] as const;}catch{return [row.market,null] as const;}}));
     if(!stopped){setOverviews(prior=>{const next={...prior};for(const [market,overview] of results)if(overview)next[market]=overview;return next;});setOverviewFailed(results.filter(([,o])=>!o).map(([m])=>m));}}
    catch(error){if(!stopped&&!(error instanceof DOMException&&error.name==="AbortError"))setFailed(true);}}
   if(!stopped)timer=setTimeout(poll,30_000);};
  const visible=()=>{if(document.visibilityState==="visible"){if(timer)clearTimeout(timer);void poll();}};
  document.addEventListener("visibilitychange",visible);void poll();
  return()=>{stopped=true;controller?.abort();if(timer)clearTimeout(timer);document.removeEventListener("visibilitychange",visible);};},[]);
 const now=data?.checkedAt??Date.now()/1000,labels=data?.stageLabels??{};
 const rows=(data?.markets??[]).filter((row):row is Row=>row.available);
 // What needs me: human conversations, accounts to maintain, sends stopped, main chains to review.
 const todo=rows.flatMap(row=>{const o=overviews[row.market],items:{key:string;text:string;href:string;since?:number|null}[]=[];
  if(row.humanQueue?.human)items.push({key:`${row.market}-human`,text:`${row.humanQueue.human} 位达人在人工队列`,href:`/${row.market}/conversations`});
  const accounts=metric(o,"handling","accountNeedsHuman");if(accounts)items.push({key:`${row.market}-accounts`,text:`${accounts} 个账号需要人工维护`,href:`/${row.market}/ops/accounts`});
  const sender=row.lanes.continuousSend;
  if(sender?.fresh&&sender.state==="attention")items.push({key:`${row.market}-send`,text:"持续发送已停止，需关注",href:`/${row.market}/workspace/send`,since:sender.observedAt});
  if(row.needsReview)items.push({key:`${row.market}-review`,text:`供给任务${label(row.needsReview.state)}`,href:`/${row.market}/ops/jobs`,since:row.needsReview.at});
  return items.map(item=>({...item,market:row.market}));});
 // "Nothing to do" is only said when every market's queue and overview were actually read (R11).
 const unchecked=(data?.markets??[]).filter(row=>!row.available||!row.humanQueue||!overviews[row.market]||overviewFailed.includes(row.market)).map(row=>row.market.toUpperCase());
 const unread=rows.map(row=>({market:row.market,count:metric(overviews[row.market],"inbox","unread"),oldest:metric(overviews[row.market],"inbox","oldestMinutes")}));
 const unreadComplete=!failed&&!!data?.markets.length&&data.markets.every(row=>row.available&&!overviewFailed.includes(row.market)&&metric(overviews[row.market],"inbox","unread")!==null);
 return <div className="space-y-5">
  <PageHeading title="经营总览" description="今日表现与待办 · 北京时间" action={data?<div className="flex items-center gap-3 text-xs text-gray-500">{!data.scheduler.running&&<Pill tone="warning">调度未运行</Pill>}<span>更新于 {time(data.checkedAt)}</span></div>:undefined}/>
  {failed&&<Notice tone="warning">总览暂时无法更新{data?`，以下为 ${time(data.checkedAt)} 的记录`:""}。</Notice>}
  {overviewFailed.length>0&&<Notice tone="warning">{overviewFailed.map(m=>m.toUpperCase()).join("、")} 的经营数据暂未更新，保留上次数据。</Notice>}
  {!data&&!failed&&<p className="text-sm text-gray-500">正在读取…</p>}
  {data&&<>
   <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_280px]">
    <div className="space-y-4 xl:col-start-2 xl:row-start-1">
     <Card title="需要你处理"><div className="divide-y divide-gray-100 px-4 dark:divide-gray-800">{todo.map(item=><Link key={item.key} href={item.href} className="group flex items-center justify-between gap-3 py-3.5 text-sm"><span className="min-w-0"><span className="block text-xs text-gray-500">{MARKET_NAME[item.market]??item.market.toUpperCase()}</span><span className="mt-1 block text-gray-800 group-hover:text-brand-600 dark:text-gray-100">{item.text.replace("位达人在人工队列","位达人需人工")}</span></span><span className="shrink-0 text-brand-600" aria-hidden="true">→</span></Link>)}
      {unchecked.length>0?<p className="py-4 text-xs leading-5 text-warning-700 dark:text-warning-400">{unchecked.join("、")} 的待办尚未读全。</p>:!todo.length&&<p className="py-5 text-sm text-gray-500">当前没有人工待办。</p>}</div>
     </Card>
     <details className="rounded-2xl border border-gray-200 bg-white dark:border-gray-800 dark:bg-white/[0.025]"><summary className="cursor-pointer px-4 py-3.5 text-sm font-medium">待回复来信</summary><div className="space-y-3 border-t border-gray-100 px-4 py-3 dark:border-gray-800">{unread.filter(u=>u.count!==0).map(u=><Link href={`/${u.market}/conversations`} key={u.market} className="flex items-center justify-between gap-2 text-sm"><span>{MARKET_NAME[u.market]??u.market.toUpperCase()}<span className="mt-0.5 block text-[11px] text-gray-500">{u.oldest?`最早等待 ${Math.round(u.oldest/60)} 小时`:"等待回复"}</span></span><span className="tabular-nums text-brand-600">{num(u.count,"个会话")}</span></Link>)}{!unreadComplete?<p className="text-xs text-warning-700 dark:text-warning-400">部分市场来信数据未读全，待核实。</p>:unread.every(u=>u.count===0)&&<p className="text-sm text-gray-500">暂无未回来信。</p>}</div></details>
    </div>
    <Card title="各市场表现" className="min-w-0 xl:col-start-1 xl:row-start-1" action={<span className="text-xs text-gray-500">{data.markets.length} 个市场</span>}>
     <div className="hidden overflow-x-auto md:block"><table className="w-full min-w-[650px] text-left text-sm"><thead className="bg-gray-50/70 text-xs text-gray-500 dark:bg-white/[0.02]"><tr>
      {["市场","今日触达","今日来信","已回复","可发达人","当前状态"].map((name,i)=><th key={name} scope="col" className={`${i===0?"px-5":"px-3"} py-3 font-medium`}>{name}</th>)}
     </tr></thead><tbody className="divide-y divide-gray-100 dark:divide-gray-800">{data.markets.map(row=>{const o=overviews[row.market],day=today(o);return <tr key={row.market} className="align-top">
      <th scope="row" className="px-5 py-4 font-normal"><Link href={`/${row.market}`} className="font-semibold text-gray-800 hover:text-brand-600 dark:text-gray-100">{MARKET_NAME[row.market]??row.market.toUpperCase()}</Link><p className="mt-1 text-[11px] text-gray-500">{row.market.toUpperCase()}{overviewFailed.includes(row.market)?" · 上次数据":""}</p></th>
      {row.available?<>
       <td className="whitespace-nowrap px-3 py-4 font-semibold tabular-nums">{num(day?.creators,"位")}</td>
       <td className="whitespace-nowrap px-3 py-4 tabular-nums">{num(day?.replies,"条")}<p className="mt-1 text-[11px] text-gray-500">{num(day?.replyCreators,"位达人")}</p></td>
       <td className="whitespace-nowrap px-3 py-4 tabular-nums"><ReplyCount day={day}/></td>
       <td className="whitespace-nowrap px-3 py-4 tabular-nums">{num(metric(o,"pool","ready"),"位")}</td>
       <td className="min-w-44 px-3 py-4"><MarketStatus row={row} labels={labels} now={now}/></td>
      </>:<td colSpan={5} className="px-3 py-4 text-xs text-warning-700 dark:text-warning-400">该市场数据暂不可用，状态待核实。</td>}
     </tr>;})}</tbody></table></div>
     <div className="divide-y divide-gray-100 dark:divide-gray-800 md:hidden">{data.markets.map(row=>{const o=overviews[row.market],day=today(o);return <article className="space-y-3 p-4" key={row.market}>
      <div className="flex items-center justify-between"><Link href={`/${row.market}`} className="font-semibold text-gray-800 dark:text-gray-100">{MARKET_NAME[row.market]??row.market.toUpperCase()} <span className="ml-1 text-xs font-normal text-gray-500">{row.market.toUpperCase()}</span></Link>{row.available&&<span className="text-xs text-gray-500">可发 {num(metric(o,"pool","ready"),"位")}</span>}</div>
      {row.available?<><dl className="grid grid-cols-3 gap-2"><div><dt className="text-xs text-gray-500">今日触达</dt><dd className="mt-1 text-base font-semibold tabular-nums">{num(day?.creators,"位")}</dd></div><div><dt className="text-xs text-gray-500">今日来信</dt><dd className="mt-1 text-base font-semibold tabular-nums">{num(day?.replies,"条")}</dd></div><div><dt className="text-xs text-gray-500">已回复</dt><dd className="mt-1 text-base font-semibold tabular-nums"><ReplyCount day={day}/></dd></div></dl><MarketStatus row={row} labels={labels} now={now}/>{overviewFailed.includes(row.market)&&<p className="text-xs text-warning-700">上次数据 · {time(o?.checkedAt)}</p>}</>:<p className="text-xs text-warning-700 dark:text-warning-400">数据暂不可用，状态待核实。</p>}
     </article>;})}</div>
    </Card>

   </div>
   <details className="rounded-2xl border border-gray-200 bg-white dark:border-gray-800 dark:bg-white/[0.025]"><summary className="cursor-pointer px-5 py-4 text-sm font-medium">供给与统计明细</summary>
    <div className="space-y-5 border-t border-gray-100 p-5 dark:border-gray-800"><div className="divide-y divide-gray-100 dark:divide-gray-800">{rows.map(row=>{const o=overviews[row.market],identity=o?.panels.find(p=>p.id==="identity");return <div key={row.market} className="flex flex-wrap justify-between gap-2 py-3 text-sm"><span className="font-medium">{MARKET_NAME[row.market]??row.market.toUpperCase()}</span><span className="text-xs text-gray-500">冷却 {num(metric(o,"pool","cooling"),"个位置")} · 商品暂不可用 {num(metric(o,"pool","inactive"),"个位置")} · 身份排队 {identity?.available?num(identity.rows?.queued??null,"条"):"—"} <Link href={`/${row.market}/catalog`} className="ml-2 text-brand-600">查看线索 →</Link></span></div>;})}</div>
     <p className="text-xs leading-6 text-gray-500">今日触达仅计卡片和文字均确认的达人；来信同时列消息条数与达人数。已回复含 AI 与人工；待办与可发达人是当前快照。</p>
     <div className="overflow-x-auto"><table className="w-full min-w-[580px] text-left text-xs"><thead className="text-gray-500"><tr>{["市场","经营数据","发送采样 / 控制核实","回复采样","今日回复构成"].map(t=><th key={t} className="pb-3 font-medium">{t}</th>)}</tr></thead><tbody>{rows.map(row=>{const o=overviews[row.market],day=today(o);return <tr key={row.market} className="border-t border-gray-100 dark:border-gray-800"><td className="py-2">{row.market.toUpperCase()}</td><td>{time(o?.checkedAt)}</td><td>{time(row.lanes.continuousSend?.observedAt)}</td><td>{time(row.lanes.agentReply?.observedAt)}</td><td>AI {day?.serviceRepliesAi??"—"} · 人工 {day?.serviceRepliesManual??"—"}</td></tr>;})}</tbody></table></div>
    </div>
   </details>
   <details className="rounded-2xl border border-gray-200 bg-white dark:border-gray-800 dark:bg-white/[0.025]"><summary className="cursor-pointer px-5 py-4 text-sm font-medium">运行详情</summary>
    <div className="grid gap-5 border-t border-gray-100 p-5 dark:border-gray-800 xl:grid-cols-2">
     <div className="space-y-3 text-sm">{rows.map(row=><div key={row.market}><p className="font-medium">{row.market.toUpperCase()}</p><p className="mt-1 text-xs"><Situation row={row} labels={labels} now={now}/> · 最近结束：<Finished row={row.lastFinished} labels={labels}/></p>{row.selectionPaused&&<p className="text-xs text-gray-500">剩余选入 {row.selectionPaused.pending??"—"} 个 · 预计重试 {time(row.selectionPaused.retryAt)}</p>}</div>)}
      <p className="pt-3 text-xs font-medium text-gray-500">资源占用</p>{data.resources.length?data.resources.map(row=><p key={`${row.resource}-${row.market}`} className="text-xs text-gray-500">{resource(row.resource)}：{row.market.toUpperCase()} {labels[row.stage]??row.stage} · 已 {minutes(row.since,now)} · 心跳 {time(row.heartbeatAt)}</p>):<p className="text-xs text-gray-500">当前没有阶段占用资源。</p>}</div>
     <div className="divide-y divide-gray-100 dark:divide-gray-800"><p className="pb-2 text-xs font-medium text-gray-500">最近运行</p>{data.recent.map(row=><details key={`${row.runId}-${row.stage}`} className="py-2 text-sm"><summary className="flex cursor-pointer list-none flex-wrap items-center justify-between gap-2"><span><span className="mr-2 font-medium">{row.market.toUpperCase()}</span>{labels[row.stage]??row.stage}</span><span className="flex items-center gap-2 text-xs text-gray-500">{row.items!=null?`${row.items.toLocaleString()} 项 · `:""}{time(row.finishedAt)} <Pill tone={tone(row.state)}>{label(row.state)}</Pill></span></summary><RunDetail row={row}/></details>)}</div>
    </div>
   </details>
  </>}
 </div>;
}
