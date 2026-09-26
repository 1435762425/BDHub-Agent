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
 waiting_capacity:"等待可用额度",waiting_reconciliation:"待核验",outside_reply_window:"回复窗口外",disabled:"未启用",off:"关闭",idle:"空闲",paused:"暂停",
 attention:"已停止，需关注",first_send_requires_page_start:"首发需在页面启动",confirmed:"已确认",cancelled:"已取消",partial_delivery:"部分送达",waiting_account:"等待账号"};
const label=(state:string|null)=>state?STATE[state]??"状态待核实":"—";
const tone=(state:string|null)=>state==="failed"||state==="needs_human"||state==="waiting_reconciliation"||state==="attention"?"warning":state==="running"||state==="sending"?"brand":state==="completed"?"success":"neutral";
const MARKET_NAME:Record<string,string>={it:"意大利",br:"巴西",my:"马来西亚",uk:"英国"};
function resource(key:string){
 if(key==="platform:global")return "平台读取槽（四市场共用）";
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

function LaneLine({name,lane}:{name:string;lane:Lane|null}){
 if(!lane)return <p className="text-xs text-gray-400">{name}：—</p>;
 return <p className="text-xs"><span className="text-gray-500">{name}：</span>{label(lane.state)}</p>;
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
  if(row.lanes.continuousSend?.state==="attention")items.push({key:`${row.market}-send`,text:"持续发送已停止，需关注",href:`/${row.market}/workspace/send`});
  if(row.needsReview)items.push({key:`${row.market}-review`,text:`主链需核对：${row.needsReview.errorCode??label(row.needsReview.state)}`,href:`/${row.market}/ops/jobs`,since:row.needsReview.at});
  return items.map(item=>({...item,market:row.market}));});
 const unread=rows.map(row=>({market:row.market,count:metric(overviews[row.market],"inbox","unread"),oldest:metric(overviews[row.market],"inbox","oldestMinutes")}));
 return <div className="space-y-5">
  <PageHeading title="经营总览" description="全部市场 · 北京时间今日 00:00 起 · 只读，不启动任何作业" action={data?<div className="flex flex-wrap gap-2"><Pill tone={data.scheduler.running?"success":"warning"}>{data.scheduler.running?"调度器在运行":"调度器未运行"}</Pill><Pill tone="neutral">数据截至 {time(data.checkedAt)}</Pill></div>:undefined}/>
  {failed&&<Notice tone="warning">总览数据暂时读不到，状态待核实{data?`；以下是 ${time(data.checkedAt)} 的记录`:""}。</Notice>}
  {overviewFailed.length>0&&data&&<Notice tone="warning">{overviewFailed.map(m=>m.toUpperCase()).join("、")} 的经营数据本次读取失败，相关列显示“—”或上次读取的值。</Notice>}
  {!data&&!failed&&<p className="text-sm text-gray-500">正在读取…</p>}
  {data&&<>
   <Card title="今日各市场" subtitle="发送与来信为北京时间今日；人工队列与当前可发为当前快照。点市场进入该市场。">
    <div className="overflow-x-auto"><table className="w-full min-w-[900px] text-left text-sm"><thead className="text-xs text-gray-400"><tr>
     <th className="px-5 py-3 font-medium">市场</th><th className="px-3 py-3 font-medium">完整卡文触达</th><th className="px-3 py-3 font-medium">收到来信</th><th className="px-3 py-3 font-medium">来信达人</th><th className="px-3 py-3 font-medium">已确认回复</th><th className="px-3 py-3 font-medium">人工队列</th><th className="px-3 py-3 font-medium">当前可发</th><th className="px-3 py-3 font-medium">当前状况</th></tr></thead>
     <tbody className="divide-y divide-gray-100 dark:divide-gray-800">{data.markets.map(row=>{const o=overviews[row.market],day=today(o);return <tr key={row.market} className="align-top">
      <td className="px-5 py-4"><Link href={`/${row.market}`} className="font-semibold text-brand-500">{row.market.toUpperCase()}</Link><p className="text-xs text-gray-400">{MARKET_NAME[row.market]??""}</p></td>
      {row.available?<>
       <td className="px-3 py-4 tabular-nums">{num(day?.creators,"位")}</td>
       <td className="px-3 py-4 tabular-nums">{num(day?.replies,"条")}</td>
       <td className="px-3 py-4 tabular-nums">{num(day?.replyCreators,"位")}</td>
       <td className="px-3 py-4 tabular-nums">{day?.serviceRepliesAi==null&&day?.serviceRepliesManual==null?num(day?.autoReplies,"条"):<>{num((day?.serviceRepliesAi??0)+(day?.serviceRepliesManual??0),"条")}<p className="text-xs text-gray-400">AI {day?.serviceRepliesAi??"—"} · 人工 {day?.serviceRepliesManual??"—"}</p></>}</td>
       <td className="px-3 py-4 tabular-nums">{row.humanQueue?<Link href={`/${row.market}/conversations`} className="text-brand-500">{num(row.humanQueue.human,"位")}</Link>:"—"}</td>
       <td className="px-3 py-4 tabular-nums">{num(metric(o,"pool","ready"),"位")}</td>
       <td className="space-y-1 px-3 py-4 text-xs"><Situation row={row} labels={labels} now={now}/><LaneLine name="发送" lane={row.lanes.continuousSend}/><LaneLine name="AI 回复" lane={row.lanes.agentReply}/></td>
      </>:<td colSpan={7} className="px-3 py-4 text-xs text-warning-600">该市场台账读取失败，状态待核实（{row.error}）</td>}
     </tr>;})}</tbody></table></div>
   </Card>
   <div className="grid gap-5 xl:grid-cols-2">
    <Card title="需要我处理" subtitle="只列需要人决定或操作的事项；正常等待不在这里">
     <div className="divide-y divide-gray-100 px-5 dark:divide-gray-800">{todo.length?todo.map(item=><Link key={item.key} href={item.href} className="flex items-center justify-between gap-3 py-3 text-sm hover:text-brand-500"><span><span className="mr-2 font-semibold">{item.market.toUpperCase()}</span>{item.text}</span><span className="text-xs text-gray-400">{item.since?time(item.since):""} 处理 →</span></Link>):<p className="py-6 text-sm text-gray-400">{rows.length?"当前没有需要人工处理的事项。":"—"}</p>}</div>
     <div className="border-t border-gray-100 px-5 py-3 text-xs text-gray-500 dark:border-gray-800">未处理达人来信：{unread.map(u=>`${u.market.toUpperCase()} ${u.count==null?"—":`${u.count} 个会话${u.oldest?`（最早已等 ${Math.round(u.oldest/60)} 小时）`:""}`}`).join("；")}</div>
    </Card>
    <Card title="供给是否够用" subtitle="当前快照；各层数字不能相加成达人总数">
     <div className="divide-y divide-gray-100 px-5 dark:divide-gray-800">{rows.map(row=>{const o=overviews[row.market],ready=metric(o,"pool","ready"),cooling=metric(o,"pool","cooling"),inactive=metric(o,"pool","inactive");const identity=o?.panels.find(p=>p.id==="identity");
      return <div key={row.market} className="flex flex-wrap items-baseline justify-between gap-2 py-3 text-sm"><span><span className="mr-2 font-semibold">{row.market.toUpperCase()}</span>当前可发 {num(ready,"位")}</span>
       <span className="text-xs text-gray-500">冷却中 {num(cooling,"个位置")} · 商品暂不可用 {num(inactive,"个位置")} · 身份排队 {identity?.available?num(identity.rows?.queued??null,"条"):"—"} <Link href={`/${row.market}/catalog`} className="ml-1 text-brand-500">看线索 →</Link></span></div>;})}</div>
    </Card>
   </div>
   <details className="rounded-2xl border border-gray-200 bg-white dark:border-gray-800 dark:bg-white/[0.03]"><summary className="cursor-pointer px-5 py-4 text-sm font-medium">运行详情（主链阶段、资源占用、最近运行）</summary>
    <div className="grid gap-5 p-5 xl:grid-cols-[1fr_1fr]">
     <div className="space-y-2 text-sm"><p className="text-xs font-medium text-gray-500">各市场主链</p>{rows.map(row=><div key={row.market} className="flex flex-wrap items-baseline justify-between gap-2"><span className="font-semibold">{row.market.toUpperCase()}</span><span className="text-xs"><Situation row={row} labels={labels} now={now}/> · 最近结束：<Finished row={row.lastFinished} labels={labels}/></span></div>)}
      <p className="pt-3 text-xs font-medium text-gray-500">资源占用</p>{data.resources.length?data.resources.map(row=><p key={`${row.resource}-${row.market}`} className="text-xs text-gray-500">{resource(row.resource)}：{row.market.toUpperCase()} {labels[row.stage]??row.stage} · 已 {minutes(row.since,now)} · 心跳 {time(row.heartbeatAt)}</p>):<p className="text-xs text-gray-400">当前没有阶段占用资源。</p>}</div>
     <div className="divide-y divide-gray-100 dark:divide-gray-800"><p className="pb-2 text-xs font-medium text-gray-500">最近运行（跳过的阶段不列出）</p>{data.recent.map(row=><details key={`${row.runId}-${row.stage}`} className="py-2 text-sm"><summary className="flex cursor-pointer list-none flex-wrap items-center justify-between gap-2"><span><span className="mr-2 font-medium">{row.market.toUpperCase()}</span>{labels[row.stage]??row.stage}</span><span className="flex items-center gap-2 text-xs text-gray-500">{row.items!=null?`${row.items.toLocaleString()} 项 · `:""}{time(row.finishedAt)} <Pill tone={tone(row.state)}>{label(row.state)}</Pill></span></summary><RunDetail row={row}/></details>)}</div>
    </div>
   </details>
  </>}
 </div>;
}
