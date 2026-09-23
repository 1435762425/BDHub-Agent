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

function FullManagedProducts({market,alternative}:{market:string;alternative:{scope:"category"|"weekly";label:string}|null}){
 const [expanded,setExpanded]=useState(false),[offset,setOffset]=useState(0),[query,setQuery]=useState(""),[draft,setDraft]=useState("");
 const [scope,setScope]=useState<MarketProductsState["scope"]>("current");
 const [data,setData]=useState<MarketProductsState|null>(null),[error,setError]=useState(false);
 const currentData=data?.market===market&&data.scope===scope?data:null;
 useEffect(()=>{setScope("current");setOffset(0);setData(null);},[market,alternative?.scope]);
 useEffect(()=>{
  if(!expanded)return;
  const controller=new AbortController();
  const params=new URLSearchParams({market,view:"products",snapshot:scope,offset:String(offset),q:query});
  void fetch(`/api/market-catalog?${params}`,{cache:"no-store",signal:controller.signal})
   .then(async response=>{if(!response.ok)throw Error("products_unavailable");return response.json() as Promise<MarketProductsState>;})
   .then(value=>{if(value.market!==market||value.scope!==scope||value.availability!=="ready")throw Error("products_invalid");setData(value);setError(false);})
   .catch(()=>{if(!controller.signal.aborted)setError(true);});
  return ()=>controller.abort();
 },[expanded,market,offset,query,scope]);
 return <Card title="商品明细（可搜索）" action={<Button size="sm" variant="outline" onClick={()=>setExpanded(value=>!value)}>{expanded?"收起":"查看商品"}</Button>}>
  {expanded&&<div className="space-y-4 p-5">
   {alternative&&<div className="flex flex-wrap gap-2"><Button size="sm" variant={scope==="current"?"primary":"outline"} onClick={()=>{setScope("current");setOffset(0);}}>当前发布覆盖</Button><Button size="sm" variant={scope===alternative.scope?"primary":"outline"} onClick={()=>{setScope(alternative.scope);setOffset(0);}}>{alternative.label}</Button></div>}
   {scope==="category"&&<Notice tone="info">这里是原始类目快照，保留历史覆盖证据；当前材料以已发布的组合快照为准。</Notice>}
   {scope==="weekly"&&<Notice tone="info">这里仅展示最近一次普通周更读到的商品。范围外的商品没有加入类目覆盖，已覆盖商品只更新重叠 PID。</Notice>}
   <form className="flex gap-2" onSubmit={event=>{event.preventDefault();setOffset(0);setQuery(draft.trim());}}><Input aria-label="搜索货盘商品" placeholder="搜索 PID 或商品标题" value={draft} maxLength={100} onChange={event=>setDraft(event.target.value)}/><Button type="submit" variant="outline">搜索</Button></form>
   {error&&<Notice tone="warning">商品明细暂不可用，请稍后重试。</Notice>}
   {currentData&&<>{scope==="current"&&currentData.displayRunId!==currentData.latestRunId&&<Notice tone="info">新一轮仍在采集；下方商品明细来自上次已发布的快照，待新批次完整核对后才会切换。</Notice>}<div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead className="border-b border-gray-200 text-xs text-gray-500 dark:border-gray-700"><tr>{["商品","公开佣金","平台展示总佣金","选入观察","进一步核验"].map(label=><th key={label} className="whitespace-nowrap px-3 py-3 font-medium">{label}</th>)}</tr></thead><tbody>{currentData.items.map(product=><tr key={product.pid} className="border-b border-gray-100 dark:border-gray-800"><td className="min-w-64 max-w-md px-3 py-4"><p className="line-clamp-2" title={product.title}>{product.title}</p><p className="mt-1 font-mono text-xs text-gray-400">{product.pid}</p></td><td className="px-3 py-4">{percent(product.publicCommissionRaw)}</td><td className="px-3 py-4">{percent(product.totalCommissionRaw)}</td><td className="whitespace-nowrap px-3 py-4">{product.selectionObservation&&["confirmed","already_selected"].includes(product.selectionObservation.state)?"已选入（已回查）":product.listedSelected===true?"采集时已选":product.listedSelected===false?"采集时未选":"待确认"}</td><td className="min-w-48 px-3 py-4">{product.stockChecked&&product.selectedOffers.length?product.selectedOffers.map((offer,index)=><p key={index} className="text-xs leading-6">拟给达人 {offer.creatorPercent??"未确定"}% · {offer.eligible?"符合本地准备条件":"条件不满足"}</p>):<Pill tone="neutral">{product.detailsChecked?"待选入／方案核验":"待核验活动与佣金"}</Pill>}</td></tr>)}</tbody></table></div>
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
 const full=object(data.fullManaged),active=object(full.activePublished),category=object(full.categorySnapshot),overlay=object(full.coverageOverlay),refresh=object(full.weeklyRefresh);
 if(full.available!==true)return <Card title="全托商品"><EmptyState title="全托快照尚不可用" description="账号或来源仍需核验；页面不会把缺失显示为业务 0。"/></Card>;
 const composed=typeof overlay.refreshRunId==="string",categoryAvailable=typeof category.runId==="string";
 const screen=object(composed?refresh.screen:full.screen),selection=object(composed?refresh.selection:full.selection);
 const currentProducts=typeof active.products==="number"?active.products:full.products;
 const weeklyProducts=composed?refresh.products:full.partitionMode==="category_l1_v1"?null:full.products;
 const alternative:{scope:"category"|"weekly";label:string}|null=composed?{scope:"weekly",label:"最近普通周更"}:categoryAvailable&&category.isCurrentHead!==true?{scope:"category",label:"原始类目快照"}:null;
 const partial=category.state==="accepted_partial",eligible=typeof screen.eligible==="number"?screen.eligible:null;
 const selectedAtRead=typeof screen.eligibleListedSelected==="number"?screen.eligibleListedSelected:null;
 const unselectedAtRead=typeof screen.eligibleListedUnselected==="number"?screen.eligibleListedUnselected:null;
 const selectedNow=typeof selection.selected==="number"?selection.selected:null;
 const unknownNow=typeof selection.skippedUnknown==="number"?selection.skippedUnknown:null;
 const carried=typeof selection.carriedUnknown==="number"?selection.carriedUnknown:null;
 const pending=typeof selection.pending==="number"?selection.pending:null;
 const other=typeof selection.untrackedEligible==="number"?selection.untrackedEligible:null;
 const screenReconciled=eligible!==null&&selectedAtRead!==null&&unselectedAtRead!==null&&eligible===selectedAtRead+unselectedAtRead;
 const selectionReconciled=unselectedAtRead!==null&&[selectedNow,unknownNow,carried,pending,other].every(value=>value!==null)&&unselectedAtRead===selectedNow!+unknownNow!+carried!+pending!+other!;
 return <div className="space-y-5">
  <Card title="全托覆盖与刷新" subtitle="类目覆盖与普通周更分别记账；本周查询的结果窗口不能代表整个市场。" action={<Button size="sm" variant="outline" disabled={refreshing} onClick={()=>{setRefreshing(true);void load().finally(()=>setRefreshing(false));}}>{refreshing?"读取中…":"刷新状态"}</Button>}><div className="space-y-4 p-5">
   <MetricTable rows={[{label:"当前发布覆盖",value:number(currentProducts),detail:composed?"类目基线，周更仅更新重叠 PID":"当前发布 head 按 PID 去重",accent:true},
    {label:"原始类目快照",value:number(category.products),detail:categoryAvailable?`已完整读取 ${number(category.categoriesCompleted)}/${number(category.categoryCount)} 个一级类目`:"尚无类目快照"},
    {label:"最近普通周更",value:number(weeklyProducts),detail:"接口本次普通查询的结果窗口"},
    ...(composed?[{label:"周更命中并更新",value:number(overlay.overlapProducts),detail:"属于原类目覆盖的 PID"},{label:"周更范围外",value:number(overlay.refreshOutsideCoverage),detail:"保留在周更证据中，未加入类目覆盖"}]:[])]}/>
   {partial&&<Notice tone="warning">原始类目快照是用户接受的部分覆盖：{number(category.categoriesCompleted)}/{number(category.categoryCount)} 类完成，不能称为全市场完整覆盖。</Notice>}
   {!composed&&categoryAvailable&&category.isCurrentHead!==true&&<Notice tone="warning">当前发布 head 是较窄的普通周更；原始类目快照仍保留，可在商品明细切换查看。</Notice>}
   {composed&&<Notice tone="info">当前组合覆盖保留 {number(category.products)} 个 PID；最近周更更新其中 {number(overlay.overlapProducts)} 个，另 {number(overlay.refreshOutsideCoverage)} 个仅保留为周更观察。未把查询窗口外的商品误算为已覆盖。</Notice>}
   <p className="text-xs leading-5 text-gray-500">当前 head：{String(active.id??"—")} · 状态：{String(full.state??full.reason??"尚未运行")}。页面只读本机快照，不触发平台采集或选入。</p>
  </div></Card>
  <Card title={composed?"最近普通周更：筛分与选入":"本轮筛分与选入"} subtitle="所有数字都绑定同一轮普通读取；历史未确认意图继续保留。"><div className="space-y-4 p-5">
   <MetricTable rows={[{label:"筛选合格",value:number(screen.eligible),detail:"本轮确定性门槛",accent:true},
    {label:"其中采集时已选",value:number(screen.eligibleListedSelected),detail:"合格商品与采集时已选状态的交集"},
    {label:"其中采集时未选",value:number(screen.eligibleListedUnselected),detail:"本轮需判定的候选"},
    {label:"本轮确认选入",value:number(selection.selected),detail:"平台回读确认"},
    {label:"本轮结果未知",value:number(selection.skippedUnknown),detail:"不重复提交原意图"},
    {label:"继承上轮未知",value:number(selection.carriedUnknown),detail:"未生成第二次选入意图"},
    {label:"本轮待提交",value:number(selection.pending),detail:"当前冻结队列"}]}/>
   {screenReconciled&&selectionReconciled?<p className="text-xs text-gray-500">本轮对账：{number(eligible)} = {number(selectedAtRead)} + {number(unselectedAtRead)}；{number(unselectedAtRead)} = {number(selectedNow)} + {number(unknownNow)} + {number(carried)} + {number(pending)}{other?` + ${number(other)} 其他未排队`:""}。</p>:<Notice tone="warning">本轮筛分或选入记录尚未形成可对账的完整快照，缺失项显示为“—”，不会冒充 0。</Notice>}
  </div></Card>
  <FullManagedProducts market={definition.key} alternative={alternative}/>
  <Card title="材料与下游" subtitle="全托已选货盘、Campaign 池、当前线索和累计触达分别有自己的来源范围。"><div className="space-y-4 p-5">
   <MetricTable rows={[{label:"全托已选货盘",value:number(data.downstream.currentSelectedCatalogOffers),detail:"当前已发布 selected catalog 的 Offer 数"},
    {label:"全托标准 TapLink",value:number(data.downstream.activeSelectedTapLinks),detail:"当前 active binding · selected"},
    {label:"Campaign 合格 PID",value:number(data.screen.eligiblePids),detail:"当前非全托方案"},
    {label:"Campaign 标准 TapLink",value:number(data.downstream.activeCampaignTapLinks),detail:"当前 active binding · campaign"},
    {label:"当前线索证据",value:number(data.downstream.currentLeadEdges),detail:"当前 lead query heads 的来源行"},
    {label:"当前已解析线索",value:number(data.downstream.currentResolvedLeadEdges),detail:`关联 ${number(data.downstream.currentResolvedCreators)} 位不同达人`},
    {label:"累计确认触达",value:number(data.downstream.deliveriesConfirmed),detail:"历史累计，不是本轮全托转化"}]}/>
   <p className="text-xs leading-5 text-gray-500">标准 TapLink 两来源合计 {number(data.downstream.activeTapLinks)} = 全托 {number(data.downstream.activeSelectedTapLinks)} + Campaign {number(data.downstream.activeCampaignTapLinks)}。这些材料来自各自已发布的货盘，不应与本轮普通查询的商品数相减。</p>
   <details className="text-xs text-gray-400"><summary className="cursor-pointer">查看历史累计台账</summary><p className="mt-2">累计线索来源 {number(data.downstream.leadEdges)} 条，累计身份解析 {number(data.downstream.identityResolved)} 条；它们包含旧 generation，不能代替上面的当前线索数。</p></details>
  </div></Card>
 </div>;
}

function Unavailable({title}:{title:string}){return <Card title={title}><EmptyState title="当前市场运行能力尚未验收" description="页面结构已统一；真实账号、locale 或写能力确认前保持关闭，并且不读取其它市场数据。"/></Card>;}

export default function CatalogWorkspace({definition}:{definition:MarketSummary}){
 const [tab,setTab]=useState<Tab>("full"),runtimeAvailable=definition.runtimeState!=="planned";
 const tabs=[{value:"full",label:"全托商品"},{value:"campaign",label:"非全托商品"},{value:"leads",label:"达人线索"},{value:"naming",label:"新建链接命名"}];
 return <div className="space-y-5"><PageHeading title="货盘" description="全托、Campaign、TapLink、Kalodata/OECID 与发送池使用同一市场隔离合同。" action={<Pill tone={runtimeAvailable?"brand":"warning"}>{definition.label} · {definition.shortLabel}</Pill>}/><Tabs items={tabs} value={tab} onChange={value=>setTab(value as Tab)}/>{tab==="full"?<FullManagedPanel definition={definition}/>:tab==="campaign"?(runtimeAvailable?<CampaignPanel market={definition.key} onOpenLeads={()=>setTab("leads")}/>:<Unavailable title="非全托商品"/>):tab==="leads"?(runtimeAvailable?<LeadsPanel market={definition.key}/>:<Unavailable title="达人线索"/>):(runtimeAvailable&&definition.contentReady?<LinkNamingPanel market={definition.key}/>:<Unavailable title="新建链接命名"/>)}</div>;
}
