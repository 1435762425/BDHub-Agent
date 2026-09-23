"use client";

import dynamic from "next/dynamic";
import {useCallback,useEffect,useState} from "react";
import {Button,Card,EmptyState,Input,MetricTable,Notice,PageHeading,Pill,Tabs} from "../bdhub/ui";
import type {MarketSummary} from "../shell/market-types";
import type {MarketCatalogState,MarketProductsState} from "../../server/market-catalog/bridge";

const CampaignPanel=dynamic(()=>import("./CampaignPanel"),{ssr:false});
const LeadsPanel=dynamic(()=>import("./LeadsPanel"),{ssr:false});
const LinkNamingPanel=dynamic(()=>import("./LinkNamingPanel"),{ssr:false});
type Tab="full"|"campaign"|"leads"|"naming";
const number=(value:unknown)=>typeof value==="number"&&Number.isFinite(value)?value.toLocaleString("zh-CN"):"—";
const object=(value:unknown)=>value&&typeof value==="object"&&!Array.isArray(value)?value as Record<string,unknown>:{};
const percent=(value:string|null)=>value!==null&&value!==""&&Number.isFinite(Number(value))?`${Number(value)/100}%`:"未返回";

function FullManagedProducts({market}:{market:string}){
 const [expanded,setExpanded]=useState(false),[offset,setOffset]=useState(0),[query,setQuery]=useState(""),[draft,setDraft]=useState("");
 const [data,setData]=useState<MarketProductsState|null>(null),[error,setError]=useState(false);
 const currentData=data?.market===market?data:null;
 useEffect(()=>{
  if(!expanded)return;
  const controller=new AbortController();
  const params=new URLSearchParams({market,view:"products",offset:String(offset),q:query});
  void fetch(`/api/market-catalog?${params}`,{cache:"no-store",signal:controller.signal})
   .then(async response=>{if(!response.ok)throw Error("products_unavailable");return response.json() as Promise<MarketProductsState>;})
   .then(value=>{if(value.market!==market||value.availability!=="ready")throw Error("products_invalid");setData(value);setError(false);})
   .catch(()=>{if(!controller.signal.aborted)setError(true);});
  return ()=>controller.abort();
 },[expanded,market,offset,query]);
 return <Card title="商品明细（可搜索）" action={<Button size="sm" variant="outline" onClick={()=>setExpanded(value=>!value)}>{expanded?"收起":"查看商品"}</Button>}>
  {expanded&&<div className="space-y-4 p-5">
   <form className="flex gap-2" onSubmit={event=>{event.preventDefault();setOffset(0);setQuery(draft.trim());}}><Input aria-label="搜索货盘商品" placeholder="搜索 PID 或商品标题" value={draft} maxLength={100} onChange={event=>setDraft(event.target.value)}/><Button type="submit" variant="outline">搜索</Button></form>
   {error&&<Notice tone="warning">商品明细暂不可用，请稍后重试。</Notice>}
   {currentData&&<>{currentData.displayRunId!==currentData.latestRunId&&<Notice tone="info">新一轮仍在采集；下方商品明细来自上次已发布的快照，待新批次完整核对后才会切换。</Notice>}<div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead className="border-b border-gray-200 text-xs text-gray-500 dark:border-gray-700"><tr>{["商品","公开佣金","平台展示总佣金","选入观察","进一步核验"].map(label=><th key={label} className="whitespace-nowrap px-3 py-3 font-medium">{label}</th>)}</tr></thead><tbody>{currentData.items.map(product=><tr key={product.pid} className="border-b border-gray-100 dark:border-gray-800"><td className="min-w-64 max-w-md px-3 py-4"><p className="line-clamp-2" title={product.title}>{product.title}</p><p className="mt-1 font-mono text-xs text-gray-400">{product.pid}</p></td><td className="px-3 py-4">{percent(product.publicCommissionRaw)}</td><td className="px-3 py-4">{percent(product.totalCommissionRaw)}</td><td className="whitespace-nowrap px-3 py-4">{product.selectionObservation&&["confirmed","already_selected"].includes(product.selectionObservation.state)?"已选入（已回查）":product.listedSelected===true?"采集时已选":product.listedSelected===false?"采集时未选":"待确认"}</td><td className="min-w-48 px-3 py-4">{product.stockChecked&&product.selectedOffers.length?product.selectedOffers.map((offer,index)=><p key={index} className="text-xs leading-6">拟给达人 {offer.creatorPercent??"未确定"}% · {offer.eligible?"符合本地准备条件":"条件不满足"}</p>):<Pill tone="neutral">{product.detailsChecked?"待选入／方案核验":"待核验活动与佣金"}</Pill>}</td></tr>)}</tbody></table></div>
    {!currentData.items.length&&<p className="py-6 text-center text-sm text-gray-500">当前没有匹配的商品</p>}
    <div className="flex items-center justify-between text-xs text-gray-500"><span>{currentData.total?`${currentData.offset+1}–${Math.min(currentData.offset+30,currentData.total)} / ${number(currentData.total)}`:"0 条"}</span><div className="flex gap-2"><Button size="sm" variant="outline" disabled={offset===0} onClick={()=>setOffset(value=>Math.max(0,value-30))}>上一页</Button><Button size="sm" variant="outline" disabled={offset+30>=currentData.total} onClick={()=>setOffset(value=>value+30)}>下一页</Button></div></div></>}
   {!currentData&&!error&&<p className="text-sm text-gray-500">正在读取商品明细…</p>}
  </div>}
 </Card>;
}

function FullManagedPanel({definition}:{definition:MarketSummary}){
 const supported=definition.capabilities.fullManagedCatalog===true,runtimeAvailable=definition.runtimeState!=="planned";
 const [data,setData]=useState<MarketCatalogState|null>(null),[error,setError]=useState(false),[refreshing,setRefreshing]=useState(false);
 const load=useCallback(async()=>{if(!supported||!runtimeAvailable)return;const response=await fetch(`/api/market-catalog?market=${encodeURIComponent(definition.key)}`,{cache:"no-store"});if(!response.ok)throw Error();const value=await response.json() as MarketCatalogState;if(value.market!==definition.key)throw Error();setData(value);setError(false);},[definition.key,runtimeAvailable,supported]);
 useEffect(()=>{void load().catch(()=>setError(true));},[load]);
 if(definition.capabilities.fullManagedCatalog===false)return <Card title="全托商品"><EmptyState title="该市场暂无全托商品" description="Campaign、TapLink、达人线索和发送池仍按同一页面结构运行；本页不会请求或启动全托 API、worker 或调度。"/></Card>;
 if(!supported||!runtimeAvailable)return <Card title="全托商品"><EmptyState title="全托能力尚未验收" description="页面位置已经保留；账号、平台能力和类目口径确认前不会读取意大利数据，也不会创建全托任务。"/></Card>;
 if(!data||data.market!==definition.key)return <Card title="全托商品"><div className="p-6"><EmptyState title={error?"全托状态暂不可用":"正在读取全托状态…"} description="没有可靠快照时不会显示成业务 0。"/></div></Card>;
 const full=data.fullManaged,screen=object(full.screen),selection=object(full.selection),partial=full.coverage==="operator_accepted_partial";
 return <div className="space-y-5"><Card title="全托商品" subtitle="首次及每 30 天按一级类目完整读取，其余周更使用普通读取。" action={<Button size="sm" variant="outline" disabled={refreshing} onClick={()=>{setRefreshing(true);void load().finally(()=>setRefreshing(false));}}>{refreshing?"读取中…":"刷新状态"}</Button>}><div className="space-y-4 p-5"><MetricTable rows={[{label:"本轮已读取商品",value:number(full.products),detail:"当前采集批次按 PID 去重"},{label:"本轮已读页数",value:number(full.pages),detail:"平台只读分页"},{label:"筛选合格",value:number(screen.eligible),detail:"当前确定性门槛",accent:true},{label:"已选入商品",value:number(selection.selected),detail:"平台回读确认"},{label:"待选入",value:number(selection.pending),detail:"冻结意图，不重复提交"}]}/>{partial&&<Notice tone="warning">用户接受的部分快照：已完整读取 {number(full.categoriesCompleted)}/{number(full.categoryCount)} 个一级类目；页面不会把它称为完整覆盖。</Notice>}<p className="text-xs leading-5 text-gray-500">状态：{String(full.state??full.reason??"尚未运行")} · 平台写入由自动运营 workflow 的持久意图执行，页面读取本市场快照。</p></div></Card><FullManagedProducts market={definition.key}/><Card title="材料与下游"><div className="grid gap-4 p-5 sm:grid-cols-4"><div><p className="text-xs text-gray-400">当前标准 TapLink</p><p className="mt-2 text-2xl font-semibold">{number(data.downstream.activeTapLinks)}</p></div><div><p className="text-xs text-gray-400">线索证据</p><p className="mt-2 text-2xl font-semibold">{number(data.downstream.leadEdges)}</p></div><div><p className="text-xs text-gray-400">已解析身份</p><p className="mt-2 text-2xl font-semibold">{number(data.downstream.identityResolved)}</p></div><div><p className="text-xs text-gray-400">确认触达</p><p className="mt-2 text-2xl font-semibold">{number(data.downstream.deliveriesConfirmed)}</p></div></div></Card></div>;
}

function Unavailable({title}:{title:string}){return <Card title={title}><EmptyState title="当前市场运行能力尚未验收" description="页面结构已统一；真实账号、locale 或写能力确认前保持关闭，并且不读取其它市场数据。"/></Card>;}

export default function CatalogWorkspace({definition}:{definition:MarketSummary}){
 const [tab,setTab]=useState<Tab>("full"),runtimeAvailable=definition.runtimeState!=="planned";
 const tabs=[{value:"full",label:"全托商品"},{value:"campaign",label:"非全托商品"},{value:"leads",label:"达人线索"},{value:"naming",label:"新建链接命名"}];
 return <div className="space-y-5"><PageHeading title="货盘" description="全托、Campaign、TapLink、Kalodata/OECID 与发送池使用同一市场隔离合同。" action={<Pill tone={runtimeAvailable?"brand":"warning"}>{definition.label} · {definition.shortLabel}</Pill>}/><Tabs items={tabs} value={tab} onChange={value=>setTab(value as Tab)}/>{tab==="full"?<FullManagedPanel definition={definition}/>:tab==="campaign"?(runtimeAvailable?<CampaignPanel market={definition.key} onOpenLeads={()=>setTab("leads")}/>:<Unavailable title="非全托商品"/>):tab==="leads"?(runtimeAvailable?<LeadsPanel market={definition.key}/>:<Unavailable title="达人线索"/>):(runtimeAvailable&&definition.contentReady?<LinkNamingPanel market={definition.key}/>:<Unavailable title="新建链接命名"/>)}</div>;
}
