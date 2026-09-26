"use client";
import Link from "next/link";
import {useEffect,useState} from "react";
import {Card,Notice,Pill} from "../bdhub/ui";
import type {MarketOverview as Overview,OverviewPanel,Metric} from "../../server/market-overview/bridge";

const format=(value:number|null|undefined)=>value==null?"不可用":value.toLocaleString("zh-CN");
const time=(value:number|null)=>value?new Date(value*1000).toLocaleString("zh-CN",{timeZone:"Asia/Shanghai",hour12:false}):"未记录";
const labels:Record<string,string>={queued:"排队",waiting_upstream:"等待上游",running:"运行中",failed:"失败待恢复",needs_human:"当前需核对",stopped:"已停止",stop_requested:"正在停止",quota_exhausted:"等待额度",waiting_capacity:"等待额度",waiting_window:"等待窗口",waiting_pool:"等待候选",waiting_reconciliation:"等待核验",paused:"已暂停",off:"未启用",idle:"空闲"};
const sources:Record<string,string>={inventory:"已发布货盘与链接台账",identity:"当前 A 类线索与身份记录",video:"当前视频线索与作者记录",pool:"当前发送池",handling:"投递、回复、案件及账号台账",inbox:"会话未回统计"};
function Facts({panel}:{panel:OverviewPanel|undefined}){
 if(!panel?.available)return <p className="text-sm text-gray-500">{panel?.reason??"数据不可用"}</p>;
 return <><dl className="space-y-2">{panel.metrics.map(m=><div key={m.key} className="flex justify-between gap-4 text-sm" title={m.note}><dt className="text-gray-500">{m.label}</dt><dd className="font-medium tabular-nums">{format(m.value)}{m.value!==null&&<span className="ml-1 text-xs font-normal text-gray-400">{m.unit}</span>}</dd></div>)}</dl>{panel.note&&<p className="mt-3 text-xs leading-5 text-gray-400">{panel.note}</p>}<p className="mt-3 text-[11px] text-gray-400">来源：{sources[panel.id]??panel.title} · {time(panel.observedAt)}</p></>;
}
export default function MarketOverview({market}:{market:string}){
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
 if(!data)return <Card className="min-w-0" title="经营概览"><div className="p-5 text-sm text-gray-500">{failed?"概览读取失败，请稍后重试；不会将缺失显示为 0。":"正在读取本地经营台账…"}</div></Card>;
 if(!data.available)return <Card className="min-w-0" title="经营概览"><div className="p-5 text-sm text-gray-500">当前市场台账不可用。暂停、未建库或读取失败均不会显示为业务 0。</div></Card>;
 const get=(id:string)=>data.panels.find(p=>p.id===id);
 const metric=(id:string,key:string):Metric|undefined=>get(id)?.available?get(id)?.metrics.find(m=>m.key===key):undefined;
 const highlights=[metric("inventory","catalogPids"),metric("inventory","activeBindings"),metric("pool","ready"),metric("inbox","unread")];
 const title=["当前货盘商品","当前有效链接绑定","当前可发达人","未处理达人来信"];
 const identity=get("identity"),handling=get("handling"),human=metric("handling","humanCases"),account=metric("handling","accountNeedsHuman");
 return <section className="min-w-0 space-y-5" aria-label="经营概览">
 {failed&&<Notice tone="warning">刷新失败，下方保留上次结果（{time(data.checkedAt)}），不能当作最新状态。</Notice>}
 <div className="flex flex-wrap items-center justify-between gap-2"><h2 className="text-base font-semibold">经营概览</h2><p className="text-xs text-gray-400">当前快照 · {time(data.checkedAt)}{data.planState!=="active"?" · 计划已暂停":""}</p></div>
 <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{highlights.map((m,i)=><div key={title[i]} className="rounded-2xl border border-gray-200 bg-white p-5 dark:border-gray-800 dark:bg-white/[0.025]"><p className="text-xs text-gray-500">{title[i]}</p><p className="mt-2 text-2xl font-semibold tabular-nums">{format(m?.value)}<span className="ml-2 text-xs font-normal text-gray-400">{m?.value!=null?m.unit:""}</span></p>{m?.note&&<p className="mt-2 text-xs leading-5 text-gray-400">{m.note}</p>}</div>)}</div>
 <div className="grid gap-5 xl:grid-cols-3">
 <Card className="min-w-0" title="已处理" subtitle="最近 7 个北京日的事件合计"><div className="p-5">{data.activity.available?<dl className="space-y-3 text-sm">{[["完整触达","creators","人次"],["达人回复","replies","条事件"],["加橱窗通知","showcase","条事件"],["确认服务回复","autoReplies","条"]].map(([label,key,unit])=><div className="flex justify-between" key={key}><dt className="text-gray-500">{label}</dt><dd>{format(data.activity.totals[key])} {unit}</dd></div>)}</dl>:<p className="text-sm text-gray-500">每日事件数据不可用</p>}<p className="mt-3 text-xs leading-5 text-gray-400">每日达人去重后求和；回复/橱窗是事件数，不是转化人数。服务回复包含人工与自动回复。</p></div></Card>
 <Card className="min-w-0" title="正在等待" subtitle="当前持久任务状态，不表示进程已核验存活"><div className="space-y-3 p-5">{data.waitsUnavailable?<p className="text-sm text-gray-500">等待状态读取失败</p>:data.waits.length?data.waits.map(w=><div key={w.id} className="flex items-start justify-between gap-3 text-sm"><span>{w.label}<span className="mt-1 block text-[11px] text-gray-400">记录时间 {time(w.observedAt)}</span></span><Pill tone={w.state==="failed"||w.state==="needs_human"?"warning":"neutral"}>{labels[w.state]??"状态待核实"}</Pill></div>):<p className="text-sm text-gray-500">未记录等待状态，不代表所有任务均已完成。</p>}<Facts panel={get("inbox")}/></div></Card>
 <Card className="min-w-0" title="需要人工" subtitle="与技术隔离、未回复来信分开"><div className="space-y-3 p-5"><p className="flex justify-between text-sm"><span>业务及其他人工事项</span><strong>{format(human?.value)}</strong></p><p className="flex justify-between text-sm"><span>账号需处理</span><strong>{format(account?.value)}</strong></p><div className="flex gap-4 text-sm"><Link className="text-brand-500" href={`/${market}/conversations`}>查看会话</Link><Link className="text-brand-500" href={`/${market}/ops/accounts`}>查看账号</Link></div><p className="text-xs leading-5 text-gray-400">历史技术人工案件单列在下方；只读展示不会结案或解除原有锁定。</p></div></Card>
 </div>
 <details className="rounded-2xl border border-gray-200 bg-white p-5 dark:border-gray-800 dark:bg-white/[0.025]"><summary className="cursor-pointer text-sm font-semibold">展开身份分布、发送池与技术状态</summary><div className="mt-5 grid gap-6 lg:grid-cols-2 xl:grid-cols-3">
 <div><h3 className="mb-3 text-sm font-semibold">A 类身份分布</h3>{identity?.available&&identity.rows&&identity.handles?<><div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead className="text-xs text-gray-400"><tr><th className="pb-2">状态</th><th>线索行</th><th>去重账号名</th></tr></thead><tbody>{Object.keys(identity.rows).map(k=><tr key={k}><td className="py-1.5">{identity.labels?.[k]}</td><td>{format(identity.rows?.[k])}</td><td>{format(identity.handles?.[k])}</td></tr>)}</tbody></table></div><p className="mt-3 text-xs leading-5 text-gray-400">{identity.note}</p></>:<Facts panel={identity}/>}</div>
 {[get("video"),get("pool"),handling,get("inventory")].map(p=><div key={p?.id}><h3 className="mb-3 text-sm font-semibold">{p?.title}</h3><Facts panel={p}/></div>)}
 </div></details>
 <Card className="min-w-0" title="近 7 天事件" subtitle="北京时间；完整触达按卡开始日归属，卡文均确认才计入。历史补录不计入回复/橱窗事件。"><div className="overflow-x-auto p-5">{data.activity.available?<table className="w-full min-w-[580px] text-left text-sm"><thead className="text-xs text-gray-500"><tr>{["日期","确认卡片","确认文字","完整触达达人","达人回复事件","橱窗事件"].map(t=><th className="pb-3" key={t}>{t}</th>)}</tr></thead><tbody>{data.activity.days.map(d=><tr className="border-t border-gray-100 dark:border-gray-800" key={d.date}>{[d.date,d.cards,d.texts,d.creators,d.replies,d.showcase].map((v,i)=><td className="py-3 tabular-nums" key={i}>{typeof v==="number"?format(v):v}</td>)}</tr>)}</tbody></table>:<p className="text-sm text-gray-500">事件台账缺失或读取失败，暂不可用。</p>}<p className="mt-3 text-xs text-gray-400">来源：消息与投递台账，与日历页同口径。当前库存没有每日历史快照。</p></div></Card>
 </section>;
}
