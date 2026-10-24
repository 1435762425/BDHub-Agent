"use client";
import Link from "next/link";
import {useEffect,useState,type ReactNode} from "react";
import {Card,Notice,Pill} from "../bdhub/ui";
import type {MarketOverview as Overview,OverviewPanel,Metric} from "../../server/market-overview/bridge";

const format=(value:number|null|undefined)=>value==null?"不可用":value.toLocaleString("zh-CN");
const time=(value:number|null)=>value?new Date(value*1000).toLocaleString("zh-CN",{timeZone:"Asia/Shanghai",hour12:false}):"未记录";
const labels:Record<string,string>={queued:"排队",waiting_upstream:"等待上游",running:"运行中",failed:"失败待恢复",needs_human:"当前需核对",stopped:"已停止",stop_requested:"正在停止",quota_exhausted:"等待额度",waiting_capacity:"等待额度",waiting_transport:"连接恢复中",waiting_window:"等待窗口",waiting_pool:"等待候选",waiting_reconciliation:"等待核验",paused:"已暂停",off:"未启用",idle:"空闲"};
const sources:Record<string,string>={inventory:"已发布货盘与链接台账",identity:"当前 A 类线索与身份记录",video:"当前视频线索与作者记录",pool:"当前发送池",handling:"投递、回复、案件及账号台账",inbox:"会话未回统计"};
const disclosure="rounded-2xl border border-gray-200 bg-white dark:border-gray-800 dark:bg-white/[0.025]";
const summary="cursor-pointer rounded-2xl px-5 py-4 text-sm font-semibold text-gray-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 dark:text-gray-200";
function Facts({panel}:{panel:OverviewPanel|undefined}){
 if(!panel?.available)return <p className="text-sm text-gray-500">{panel?.reason??"数据不可用"}</p>;
 return <><dl className="space-y-2">{panel.metrics.map(m=><div key={m.key} className="flex justify-between gap-4 text-sm" title={m.note}><dt className="text-gray-500">{m.label}</dt><dd className="font-medium tabular-nums">{format(m.value)}{m.value!==null&&<span className="ml-1 text-xs font-normal text-gray-400">{m.unit}</span>}</dd></div>)}</dl>{panel.note&&<p className="mt-3 text-xs leading-5 text-gray-400">{panel.note}</p>}<p className="mt-3 text-[11px] text-gray-400">来源：{sources[panel.id]??panel.title} · {time(panel.observedAt)}</p></>;
}
export default function MarketOverview({market,controls}:{market:string;controls?:ReactNode}){
 const [state,setState]=useState<{market:string;data:Overview}|null>(null),[failed,setFailed]=useState(false);
 useEffect(()=>{let stopped=false,timer:ReturnType<typeof setTimeout>|undefined;const controller=new AbortController();
  setState(null);setFailed(false);
  const poll=async()=>{try{if(document.visibilityState!=="hidden"){
   const response=await fetch(`/api/market-overview?market=${encodeURIComponent(market)}`,{cache:"no-store",signal:controller.signal});
   if(!response.ok)throw Error();const data=await response.json() as Overview;if(data.market!==market)throw Error();
   if(!stopped){setState({market,data});setFailed(false);}
  }}catch{if(!stopped)setFailed(true);}finally{if(!stopped)timer=setTimeout(poll,60000);}};
  void poll();return()=>{stopped=true;controller.abort();if(timer)clearTimeout(timer);};
 },[market]);
 const data=state?.market===market?state.data:null;
 if(!data)return <section className="min-w-0 space-y-5"><Card title="经营概览"><div className="p-5 text-sm text-gray-500">{failed?"概览读取失败，请稍后重试。":"正在读取经营概览…"}</div></Card>{controls}</section>;
 if(!data.available)return <section className="min-w-0 space-y-5"><Card title="经营概览"><div className="p-5 text-sm text-gray-500">当前市场数据不可用。</div></Card>{controls}</section>;
 const get=(id:string)=>data.panels.find(p=>p.id===id);
 const metric=(id:string,key:string):Metric|undefined=>get(id)?.available?get(id)?.metrics.find(m=>m.key===key):undefined;
 const highlights=[metric("inbox","unread"),metric("pool","ready"),metric("inventory","catalogPids"),metric("inventory","activeBindings")];
 const titles=["未处理达人来信","当前可发达人","当前货盘商品","当前有效链接绑定"];
 const identity=get("identity"),handling=get("handling"),inbox=get("inbox");
 const human=metric("handling","humanCases"),account=metric("handling","accountNeedsHuman");
 const humanRows=[{title:"业务及其他人工事项",metric:human},{title:"账号需处理",metric:account}];
 const noHumanTasks=human?.value===0&&account?.value===0;
 const verification=[metric("handling","deliveryUnknown"),metric("handling","replyUnknown")];
 const unresolved=verification.filter((m):m is Metric=>m!=null&&m.value!=null&&m.value>0);
 const isolated=[metric("handling","quarantined"),metric("handling","replyIsolated")].filter((m):m is Metric=>m!=null&&m.value!=null&&m.value>0);
 const unread=metric("inbox","unread"),oldest=metric("inbox","oldestMinutes");
 const range=data.activity.available&&data.activity.days.length?`${data.activity.days[0].date} — ${data.activity.days[data.activity.days.length-1].date} · 北京时间`:"最近 7 个北京日";
 return <section className="min-w-0 space-y-5" aria-label="经营概览">
  {failed&&<Notice tone="warning">刷新失败，显示上次结果：{time(data.checkedAt)}。</Notice>}
  <div>
   <div className="mb-3 flex flex-wrap items-center justify-between gap-2"><h2 className="text-base font-semibold">当前经营概览</h2><p className="text-xs text-gray-400">更新于 {time(data.checkedAt)}{data.planState!=="active"?` · ${data.planState==="paused"?"计划已暂停":"计划状态待核实"}`:""}</p></div>
   <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">{highlights.map((m,i)=><div key={titles[i]} className="rounded-2xl border border-gray-200 bg-white px-5 py-4 dark:border-gray-800 dark:bg-white/[0.025]"><p className="text-xs text-gray-500">{titles[i]}</p><p className="mt-2 text-2xl font-semibold tabular-nums">{format(m?.value)}<span className="ml-2 text-xs font-normal text-gray-400">{m?.value!=null?m.unit:""}</span></p></div>)}</div>
  </div>
  {controls}
  <div className="grid gap-5 xl:grid-cols-3">
   <Card className="min-w-0" title="需要人工"><div className="space-y-4 p-5">
    {noHumanTasks?<p className="text-sm text-gray-500">暂无人工待办</p>:<dl className="space-y-3 text-sm">{humanRows.map(({title,metric:m})=>m?.value===0?null:<div key={title} className="flex justify-between gap-3"><dt className="text-gray-500">{title}</dt><dd className="font-semibold tabular-nums">{format(m?.value)}{m?.value!=null?` ${m.unit}`:""}</dd></div>)}</dl>}
    <div className="flex flex-wrap gap-x-4 gap-y-2 text-sm"><Link className="font-medium text-brand-500" href={`/${market}/conversations`}>查看会话 →</Link><Link className="font-medium text-brand-500" href={`/${market}/ops/accounts`}>查看账号 →</Link></div>
   </div></Card>
   <Card className="min-w-0" title="正在等待"><div className="space-y-3 p-5">
    {data.waitsUnavailable?<p className="text-sm text-warning-600">等待状态读取失败</p>:data.waits.length?data.waits.map(w=><div key={w.id} className="flex items-start justify-between gap-3 text-sm"><span>{w.label}<span className="mt-1 block text-[11px] text-gray-400">记录于 {time(w.observedAt)}</span></span><Pill tone={w.state==="failed"||w.state==="needs_human"||w.state==="waiting_reconciliation"?"warning":"neutral"}>{labels[w.state]??"状态待核实"}</Pill></div>):<p className="text-sm text-gray-500">暂无等待记录</p>}
    {unresolved.map(m=><div key={m.key} className="flex justify-between gap-3 text-sm text-warning-600"><span>{m.label}</span><strong className="tabular-nums">{format(m.value)} {m.unit}</strong></div>)}
    {verification.some(m=>m?.value==null)&&<p className="text-xs text-warning-600">投递与回复核验数据不可用</p>}
    {isolated.length>0&&<p className="text-xs leading-5 text-gray-500">{isolated.map(m=>`${m.label} ${format(m.value)} ${m.unit}`).join(" · ")}</p>}
    {inbox?.available?<>{unread?.value!=null&&unread.value>0&&<div className="flex flex-wrap items-center justify-between gap-2 border-t border-gray-100 pt-3 text-sm dark:border-gray-800"><span className="text-gray-500">来信最久等待 <strong className="font-medium text-gray-700 dark:text-gray-200">{format(oldest?.value)}{oldest?.value!=null?` ${oldest.unit}`:""}</strong></span><Link className="font-medium text-brand-500" href={`/${market}/conversations`}>处理来信 →</Link></div>}</>:<p className="text-sm text-warning-600">来信等待数据不可用</p>}
   </div></Card>
   <Card className="min-w-0" title="近 7 天结果" subtitle={range}><div className="p-5">{data.activity.available?<dl className="space-y-2.5 text-sm">{[["完整触达","creators","人次"],["达人来信","replies","条"],["来信达人","replyCreators","人次"],["加橱窗通知","showcase","条事件"],["确认服务回复","autoReplies","条"]].map(([label,key,unit])=><div className="flex justify-between gap-3" key={key}><dt className="text-gray-500">{label}</dt><dd className="text-right tabular-nums">{format(data.activity.totals[key]??null)} {data.activity.totals[key]==null?"":unit}{key==="autoReplies"&&data.activity.totals.serviceRepliesAi!=null&&<span className="ml-1 text-xs text-gray-400">（AI {format(data.activity.totals.serviceRepliesAi)} · 人工 {format(data.activity.totals.serviceRepliesManual??null)}）</span>}</dd></div>)}</dl>:<p className="text-sm text-gray-500">每日事件数据不可用</p>}</div></Card>
  </div>
  <details className={disclosure}><summary className={summary}>近 7 天明细</summary><div className="border-t border-gray-100 px-5 pb-5 pt-4 dark:border-gray-800">
   <p className="mb-4 text-xs text-gray-500">{range}；每日达人为当日去重人数，跨日合计为人次。</p>
   {data.activity.available?<div className="overflow-x-auto"><table className="w-full min-w-[640px] text-left text-sm"><thead className="text-xs text-gray-500"><tr>{["日期","确认卡片（张）","确认文字（条）","完整触达达人（人）","达人回复事件（条）","橱窗事件（条）"].map(t=><th scope="col" className="pb-3 font-medium" key={t}>{t}</th>)}</tr></thead><tbody>{data.activity.days.map(d=><tr className="border-t border-gray-100 dark:border-gray-800" key={d.date}>{[d.date,d.cards,d.texts,d.creators,d.replies,d.showcase].map((v,i)=><td className="py-3 tabular-nums" key={i}>{typeof v==="number"?format(v):v}</td>)}</tr>)}</tbody></table></div>:<p className="text-sm text-gray-500">事件明细暂不可用。</p>}
  </div></details>
  <details className={disclosure}><summary className={summary}>统计口径与数据详情</summary><div className="space-y-6 border-t border-gray-100 p-5 dark:border-gray-800">
   <div className="grid gap-5 md:grid-cols-2">{highlights.map((m,i)=><div key={titles[i]}><h3 className="text-sm font-medium">{titles[i]}</h3><p className="mt-1 text-xs leading-5 text-gray-500">{m?.note||"统计说明未记录"}</p></div>)}</div>
   <div className="text-xs leading-6 text-gray-500"><p>完整触达按卡开始日归属，卡片与文字均确认后计入；每日去重达人跨日求和为人次。来信与橱窗按消息/事件计数，服务回复包含人工与 AI。</p><p>来源：消息与投递台账，与日历页同口径。历史补录不计入回复/橱窗事件；当前库存没有每日历史快照。</p><p>等待状态来自最近任务记录，各项保留记录时间。</p></div>
   <div className="grid gap-6 lg:grid-cols-2 xl:grid-cols-3">
    <div><h3 className="mb-3 text-sm font-semibold">A 类身份分布</h3>{identity?.available&&identity.rows&&identity.handles?<><div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead className="text-xs text-gray-400"><tr><th scope="col" className="pb-2">状态</th><th scope="col">线索行</th><th scope="col">去重账号名</th></tr></thead><tbody>{Object.keys(identity.rows).map(k=><tr key={k}><td className="py-1.5">{identity.labels?.[k]}</td><td>{format(identity.rows?.[k])}</td><td>{format(identity.handles?.[k])}</td></tr>)}</tbody></table></div><p className="mt-3 text-xs leading-5 text-gray-400">{identity.note}</p></>:<Facts panel={identity}/>}</div>
    {[get("video"),get("pool"),handling,get("inventory"),inbox].map(p=><div key={p?.id}><h3 className="mb-3 text-sm font-semibold">{p?.title}</h3><Facts panel={p}/></div>)}
   </div>
  </div></details>
 </section>;
}
