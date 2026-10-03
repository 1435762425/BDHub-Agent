"use client";

import dynamic from "next/dynamic";
import {useCallback,useEffect,useState} from "react";
import {Button,Card,Collapsible,EmptyState,Input,MetricTable,Notice,PageHeading,Pill,StatTile,Tabs} from "../bdhub/ui";
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
 return <Card title="商品明细" subtitle="搜索商品名称或 PID，查看佣金与选入情况。" action={<Button size="sm" variant={expanded?"outline":"primary"} aria-expanded={expanded} onClick={()=>setExpanded(value=>!value)}>{expanded?"收起商品":"查看商品"}</Button>}>
  {expanded&&<div className="space-y-4 p-5">
   {alternative&&<div className="flex flex-wrap gap-2"><Button size="sm" variant={scope==="current"?"primary":"outline"} onClick={()=>{setScope("current");setOffset(0);}}>当前商品</Button><Button size="sm" variant={scope===alternative.scope?"primary":"outline"} onClick={()=>{setScope(alternative.scope);setOffset(0);}}>{alternative.label}</Button></div>}
   {scope==="category"&&<Notice tone="info">这是原始类目快照，仅用于追溯；当前商品请切回「当前商品」。</Notice>}
   {scope==="weekly"&&<Notice tone="info">这是最近一次普通查询读到的商品，不代表全部覆盖范围。</Notice>}
   <form className="flex gap-2" onSubmit={event=>{event.preventDefault();setOffset(0);setQuery(draft.trim());}}><Input className="min-w-0 flex-1" aria-label="搜索货盘商品" placeholder="搜索 PID 或商品标题" value={draft} maxLength={100} onChange={event=>setDraft(event.target.value)}/><Button type="submit" variant="outline" className="shrink-0">搜索</Button></form>
   {error&&<Notice tone="warning">商品明细暂不可用，请稍后重试。</Notice>}
   {currentData&&<>{scope==="current"&&currentData.displayRunId!==currentData.latestRunId&&<Notice tone="info">新一轮尚未发布，当前仍展示上次已核对的商品。</Notice>}<div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead className="border-b border-gray-200 text-xs text-gray-500 dark:border-gray-700"><tr>{["商品","公开佣金","平台总佣金","选入情况","准备条件"].map(label=><th key={label} className="whitespace-nowrap px-3 py-3 font-medium">{label}</th>)}</tr></thead><tbody>{currentData.items.map(product=><tr key={product.pid} className="border-b border-gray-100 dark:border-gray-800"><td className="min-w-64 max-w-md px-3 py-4"><p className="line-clamp-2" title={product.title}>{product.title}</p><p className="mt-1 font-mono text-xs text-gray-400">{product.pid}</p></td><td className="px-3 py-4">{percent(product.publicCommissionRaw)}</td><td className="px-3 py-4">{percent(product.totalCommissionRaw)}</td><td className="whitespace-nowrap px-3 py-4">{product.selectionObservation&&["confirmed","already_selected"].includes(product.selectionObservation.state)?"已选入（已回查）":product.listedSelected===true?"采集时已选":product.listedSelected===false?"采集时未选":"待确认"}</td><td className="min-w-48 px-3 py-4">{product.stockChecked&&product.selectedOffers.length?product.selectedOffers.map((offer,index)=><p key={index} className="text-xs leading-6">拟给达人 {offer.creatorPercent??"未确定"}% · {offer.eligible?"符合准备条件":"条件不满足"}</p>):<Pill tone="neutral">{product.detailsChecked?"待选入／方案核验":"待核验活动与佣金"}</Pill>}</td></tr>)}</tbody></table></div>
    {!currentData.items.length&&<p className="py-6 text-center text-sm text-gray-500">当前没有匹配的商品</p>}
    <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-gray-500"><span>{currentData.total?`${currentData.offset+1}–${Math.min(currentData.offset+30,currentData.total)} / ${number(currentData.total)}`:"0 条"}</span><div className="flex gap-2"><Button size="sm" variant="outline" disabled={offset===0} onClick={()=>setOffset(value=>Math.max(0,value-30))}>上一页</Button><Button size="sm" variant="outline" disabled={offset+30>=currentData.total} onClick={()=>setOffset(value=>value+30)}>下一页</Button></div></div></>}
   {!currentData&&!error&&<p className="text-sm text-gray-500">正在读取商品明细…</p>}
  </div>}
 </Card>;
}

function FullManagedPanel({definition}:{definition:MarketSummary}){
 const supported=definition.capabilities.fullManagedCatalog===true,runtimeAvailable=definition.runtimeState!=="planned";
 const [data,setData]=useState<MarketCatalogState|null>(null),[error,setError]=useState(false),[refreshing,setRefreshing]=useState(false);
 const load=useCallback(async()=>{if(!supported||!runtimeAvailable)return;const response=await fetch(`/api/market-catalog?market=${encodeURIComponent(definition.key)}`,{cache:"no-store"});if(!response.ok)throw Error();const value=await response.json() as MarketCatalogState;if(value.market!==definition.key)throw Error();setData(value);setError(false);},[definition.key,runtimeAvailable,supported]);
 useEffect(()=>{void load().catch(()=>setError(true));},[load]);
 if(definition.capabilities.fullManagedCatalog===false)return <Card title="全托商品"><EmptyState title="该市场暂无全托商品" description="可在「非全托商品」查看 Campaign 货盘。"/></Card>;
 if(!supported||!runtimeAvailable)return <Card title="全托商品"><EmptyState title="全托能力尚未验收" description="完成该市场的账号与商品能力验收后开放。"/></Card>;
 if(!data||data.market!==definition.key)return <Card title="全托商品"><EmptyState title={error?"全托状态暂不可用":"正在读取全托状态…"} description={error?"暂未取得可靠的商品记录，请稍后重试。":""}/></Card>;
 const full=object(data.fullManaged),active=object(full.activePublished),category=object(full.categorySnapshot),overlay=object(full.coverageOverlay),refresh=object(full.weeklyRefresh);
 if(full.available!==true)return <Card title="全托商品"><EmptyState title="暂无可用全托记录" description="账号或商品来源仍待核验。"/></Card>;
 const composed=typeof overlay.refreshRunId==="string",categoryAvailable=typeof category.runId==="string";
 const screen=object(composed?refresh.screen:full.screen),selection=object(composed?refresh.selection:full.selection);
 const currentProducts=typeof active.products==="number"?active.products:full.products;
 const weeklyProducts=composed?refresh.products:full.partitionMode==="category_l1_v1"?null:full.products;
 const alternative:{scope:"category"|"weekly";label:string}|null=composed?{scope:"weekly",label:"历史普通查询"}:categoryAvailable&&category.isCurrentHead!==true?{scope:"category",label:"原始类目快照"}:null;
 const partial=category.state==="accepted_partial",eligible=typeof screen.eligible==="number"?screen.eligible:null;
 const selectedAtRead=typeof screen.eligibleListedSelected==="number"?screen.eligibleListedSelected:null;
 const unselectedAtRead=typeof screen.eligibleListedUnselected==="number"?screen.eligibleListedUnselected:null;
 const selectedNow=typeof selection.selected==="number"?selection.selected:null;
 const unknownNow=typeof selection.skippedUnknown==="number"?selection.skippedUnknown:null;
 const carried=typeof selection.carriedUnknown==="number"?selection.carriedUnknown:null;
 const pending=typeof selection.pending==="number"?selection.pending:null;
 const ended=typeof selection.endedWithoutSelection==="number"?selection.endedWithoutSelection:null;
 const other=typeof selection.untrackedEligible==="number"?selection.untrackedEligible:null;
 const screenReconciled=eligible!==null&&selectedAtRead!==null&&unselectedAtRead!==null&&eligible===selectedAtRead+unselectedAtRead;
 const selectionReconciled=unselectedAtRead!==null&&[selectedNow,unknownNow,carried,pending,other,ended].every(value=>value!==null)&&unselectedAtRead===selectedNow!+unknownNow!+carried!+pending!+other!+ended!;
 const selectionScope=composed?"历史查询":"本轮";
 const latestRun=object(full.latestRun),candidates=object(full.candidates);
 const latestStatus:Record<string,string>={collecting:"采集中",queued:"等待采集",blocked:"采集受阻",partial:"采集未完整"};
 const latestState=String(latestRun.state??"");
 return <div className="space-y-5">
  <Card title="全托商品概况" action={<Button size="sm" variant="outline" disabled={refreshing} onClick={()=>{setRefreshing(true);void load().catch(()=>setError(true)).finally(()=>setRefreshing(false));}}>{refreshing?"读取中…":"刷新状态"}</Button>}><div className="space-y-4 p-5">
   <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
    <StatTile label="累计合格候选" value={candidates.available===true?number(candidates.total):"—"} hint="历次发现，按商品去重"/>
    <StatTile label={`${selectionScope}已选入`} value={number(selectedNow)} hint="原申请已获平台确认"/>
    <StatTile label={`${selectionScope}结果未知`} value={number(unknownNow)} hint="仅核验原申请，不重复提交"/>
    <StatTile label="当前有效链接" value={number(data.downstream.activeSelectedTapLinks)} hint="全托商品 · 已核验" brand/>
   </div>
   <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-sm text-gray-500">
    <span>当前覆盖 <b className="font-medium tabular-nums text-gray-700 dark:text-gray-200">{number(currentProducts)}</b> 件商品</span>
    {categoryAvailable&&<span className="text-xs">类目已读 {number(category.categoriesCompleted)} / {number(category.categoryCount)}</span>}
    {(carried===null||carried>0)&&<Pill tone="warning">上轮结果未知 {number(carried)}</Pill>}
    {pending!==null&&pending>0&&<Pill tone="neutral">{selectionScope}待选入 {number(pending)}</Pill>}
    {ended!==null&&ended>0&&<Pill tone="warning">{selectionScope}不可继续 {number(ended)}</Pill>}
   </div>
   {error&&<Notice tone="warning">刷新暂未完成，当前显示上次取得的记录。</Notice>}
   {partial&&<Notice tone="warning">当前保留的是已接受的部分覆盖，尚未完成全部类目。</Notice>}
   {!composed&&categoryAvailable&&category.isCurrentHead!==true&&<Notice tone="warning">当前商品来自较窄的普通查询；原始类目快照可在商品明细中切换查看。</Notice>}
   {composed&&<p className="text-xs leading-5 text-gray-500">当前商品保留原类目覆盖，普通查询只更新重叠商品。</p>}
   {latestStatus[latestState]&&<Notice tone={latestState==="blocked"||latestState==="partial"?"warning":"info"}>最近一轮：{latestStatus[latestState]}。{typeof active.id==="string"?"当前商品仍使用已发布结果。":"尚无已发布商品。"}</Notice>}
   {(!screenReconciled||!selectionReconciled)&&<Notice tone="warning">{selectionScope}筛分与选入记录尚未核对完整，缺失项显示为“—”。</Notice>}
  </div></Card>
  <FullManagedProducts market={definition.key} alternative={alternative}/>
  <div className="space-y-3">
   <Collapsible label="覆盖与来源详情"><Card><div className="space-y-4 p-5">
    <p className="text-xs leading-5 text-gray-500">首次及每 15 天按类目发现商品，合格候选累积保留。</p>
    <MetricTable rows={[
     {label:"本来源新增候选",value:candidates.available===true?number(candidates.addedInSource):"—",detail:"首次合格记录来自本来源"},
     {label:"当前发布覆盖",value:number(currentProducts),detail:composed?"原类目与普通查询组合覆盖":"已发布商品，按 PID 去重"},
     {label:"原始类目快照",value:number(category.products),detail:categoryAvailable?`已读 ${number(category.categoriesCompleted)} / ${number(category.categoryCount)} 个一级类目`:"尚无类目快照"},
     {label:"历史普通查询",value:number(weeklyProducts),detail:"最近一次普通查询读到的商品"},
     ...(composed?[{label:"查询命中并更新",value:number(overlay.overlapProducts),detail:"原类目覆盖内的商品"},{label:"原覆盖外商品",value:number(overlay.refreshOutsideCoverage),detail:"仅留作查询记录，未加入类目覆盖"}]:[])]}/>
    <p className="break-all text-xs leading-5 text-gray-500">发布批次：{String(active.id??"—")} · 状态：{String(full.state??full.reason??"尚未运行")}。{latestRun.id!==active.id?`最近采集状态：${String(latestRun.state??"尚未运行")}。`:""}</p>
   </div></Card></Collapsible>
   <Collapsible label={`${selectionScope}筛分与选入明细`}><Card><div className="space-y-4 p-5">
    <p className="text-xs leading-5 text-gray-500">以下属于同一采集来源，累计候选单独保留。</p>
    <MetricTable rows={[
     {label:"筛选合格",value:number(screen.eligible),detail:"采集时符合资格"},
     {label:"其中采集时已选",value:number(screen.eligibleListedSelected),detail:"合格且当时已选入"},
     {label:"其中采集时未选",value:number(screen.eligibleListedUnselected),detail:"合格且当时未选入"},
     {label:"已确认选入",value:number(selection.selected),detail:"平台回读确认"},
     {label:"结果未知",value:number(selection.skippedUnknown),detail:"仅核验原申请，不重复提交"},
     {label:"继承上轮未知",value:number(selection.carriedUnknown),detail:"保留原申请与核验记录"},
     {label:"本次不可继续",value:number(ended),detail:"保留候选与失败记录，不自动重排"},
     {label:"待提交",value:number(selection.pending),detail:`已进入${selectionScope}队列`}]}/>
    {screenReconciled&&selectionReconciled&&<p className="text-xs leading-5 text-gray-500">{selectionScope}对账：{number(eligible)} = {number(selectedAtRead)} + {number(unselectedAtRead)}；{number(unselectedAtRead)} = {number(selectedNow)} + {number(unknownNow)} + {number(carried)} + {number(pending)}{ended?` + ${number(ended)} 本次不可继续`:""}{other?` + ${number(other)} 其他未排队`:""}。</p>}
   </div></Card></Collapsible>
   <Collapsible label="链接、线索与历史记录"><Card><div className="space-y-4 p-5">
    <MetricTable rows={[
     {label:"当前全托商品方案",value:number(data.downstream.currentSelectedCatalogOffers),detail:"已发布的全托方案"},
     {label:"全托有效链接",value:number(data.downstream.activeSelectedTapLinks),detail:"当前已核验链接"},
     {label:"非全托合格商品",value:number(data.screen.eligiblePids),detail:"当前 Campaign 货盘"},
     {label:"非全托有效链接",value:number(data.downstream.activeCampaignTapLinks),detail:"当前已核验链接"},
     {label:"当前线索记录",value:number(data.downstream.currentLeadEdges),detail:"当前各商品的达人来源记录"},
     {label:"当前已解析线索",value:number(data.downstream.currentResolvedLeadEdges),detail:`关联 ${number(data.downstream.currentResolvedCreators)} 位不同达人`},
     {label:"累计确认触达",value:number(data.downstream.deliveriesConfirmed),detail:"历史累计，与本轮选入分别统计"}]}/>
    <p className="text-xs leading-5 text-gray-500">有效链接合计 {number(data.downstream.activeTapLinks)}，其中全托 {number(data.downstream.activeSelectedTapLinks)}、非全托 {number(data.downstream.activeCampaignTapLinks)}。各来源独立统计。</p>
    <p className="text-xs leading-5 text-gray-500">历史累计：线索来源 {number(data.downstream.leadEdges)} 条，身份解析 {number(data.downstream.identityResolved)} 条（含历史批次）。</p>
   </div></Card></Collapsible>
  </div>
 </div>;
}

function Unavailable({title}:{title:string}){return <Card title={title}><EmptyState title="当前市场运行能力尚未验收" description="完成该市场的账号与对应能力验收后开放。"/></Card>;}

export default function CatalogWorkspace({definition}:{definition:MarketSummary}){
 // Markets without full-managed goods open on the Campaign catalog they actually use.
 const [tab,setTab]=useState<Tab>(definition.capabilities.fullManagedCatalog===false?"campaign":"full"),runtimeAvailable=definition.runtimeState!=="planned";
 const tabs=[{value:"full",label:"全托商品"},{value:"campaign",label:"非全托商品"},{value:"leads",label:"达人线索"},{value:"naming",label:"新建链接命名"}];
 return <div className="space-y-5"><PageHeading title="货盘"/><Tabs items={tabs} value={tab} onChange={value=>setTab(value as Tab)}/>{tab==="full"?<FullManagedPanel definition={definition}/>:tab==="campaign"?(runtimeAvailable?<CampaignPanel market={definition.key} onOpenLeads={()=>setTab("leads")}/>:<Unavailable title="非全托商品"/>):tab==="leads"?(runtimeAvailable?<LeadsPanel market={definition.key}/>:<Unavailable title="达人线索"/>):(runtimeAvailable&&definition.contentReady?<LinkNamingPanel market={definition.key}/>:<Unavailable title="新建链接命名"/>)}</div>;
}
