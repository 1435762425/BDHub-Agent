"use client";
import Link from "next/link";
import {useCallback,useEffect,useRef,useState} from "react";
import {useRouter,useSearchParams} from "next/navigation";
import {Button,Card,EmptyState,Icon,Input,Notice,PageHeading,Pill,Tabs} from "../bdhub/ui";
import type {CreatorIdentityDetail,CreatorIdentityList,CreatorIdentityOverview,CreatorIdentitySummary,IdentityProfileField,ProfileFieldValue} from "./contracts";
import ProfileRefreshPanel from "./ProfileRefreshPanel";
import CreatorDiscoveryPanel from "./CreatorDiscoveryPanel";
import CreatorDraftHistory from "../outreach-drafts/CreatorDraftHistory";

const API="/api/creator-identities",PAGE_SIZE=10;
const marketNames={it:"意大利",mx:"墨西哥",br:"巴西"};
const fieldNames:Record<string,string>={follower_cnt:"粉丝数",med_gmv_revenue:"成交额",video_gmv:"视频成交额",live_gmv:"直播成交额",units_sold:"销量",video_avg_view_cnt:"视频均播",industry_groups:"带货类目",content_groups:"成交来源",top_video_data:"代表视频",product_price_range:"商品价格范围",video_publish_cnt_30d:"30 天发布视频",ec_video_publish_cnt_30d:"30 天带货视频",live_streaming_cnt_30d:"30 天直播次数",ec_live_streaming_cnt_30d:"30 天带货直播",gpm:"GPM",ec_live_gpm:"直播 GPM",ec_video_gpm:"视频 GPM",partnered_brand:"合作品牌"};
const emptyLabels={absent:"本次未返回",no_value:"平台未提供值",unauthorized:"平台未授权",error:"字段暂不可用"};
function date(value:string|null){if(!value)return "尚无记录";const number=Date.parse(value);return Number.isFinite(number)?new Intl.DateTimeFormat("zh-CN",{month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false,timeZone:"Asia/Shanghai"}).format(number):"时间未确认";}
function display(value:ProfileFieldValue|undefined):string {
  if(value===undefined||value===null)return "—";
  if(typeof value==="number")return value.toLocaleString("zh-CN");
  if(typeof value==="string")return value;
  if(typeof value==="boolean")return value?"是":"否";
  if(Array.isArray(value))return value.map(v=>typeof v==="object"&&v&&!Array.isArray(v)?String(v.label??({video_gmv:"视频成交",live_gmv:"直播成交",showcase_gmv:"橱窗成交"}[String(v.id) as "video_gmv"|"live_gmv"|"showcase_gmv"]??"未命名条目")):display(v)).slice(0,6).join(" · ")||"无条目";
  if(typeof value.format==="string")return value.format;
  if(typeof value.decimal==="string")return `${typeof value.rawSymbol==="string"?value.rawSymbol:""}${value.decimal}`;
  if(value.minimum!==undefined&&value.maximum!==undefined){const start=display(value.minimumFormat??value.minimum),end=display(value.maximumFormat??value.maximum);return start===end?start:`${start} – ${end}`;}
  if(typeof value.count==="number")return `${value.count} 条`;
  if(Array.isArray(value.brands))return display(value.brands);
  return "已记录";
}
function FieldValue({field}:{field:IdentityProfileField}) {
  const available=field.status==="value"||field.status==="zero";
  return <div className="min-w-0 rounded-xl bg-gray-50 px-4 py-3 dark:bg-gray-800/50"><p className="text-xs text-gray-500">{fieldNames[field.name]}</p><p className={`mt-2 break-words text-sm leading-6 ${available?"font-medium text-gray-800 dark:text-gray-200":"text-gray-400"}`}>{available?display(field.value):emptyLabels[field.status as keyof typeof emptyLabels]}</p>{!available&&field.lastAvailable&&<p className="mt-1 text-xs leading-5 text-gray-400">历史 {display(field.lastAvailable.value)} · {date(field.lastAvailable.observedAt)}</p>}</div>;
}
function Detail({detail,onRefresh}:{detail:CreatorIdentityDetail;onRefresh:()=>void}) {
  const creator=detail.creator!;
  const metrics=detail.fields.filter(f=>fieldNames[f.name]);
  const available=metrics.filter(f=>["value","zero"].includes(f.status)).length;
  return <div className="space-y-5">
    <Card><div className="p-5 sm:p-6"><div className="flex flex-wrap items-start justify-between gap-3"><div className="flex min-w-0 gap-3"><div className="flex size-12 shrink-0 items-center justify-center rounded-xl bg-brand-50 text-brand-500 dark:bg-brand-500/10"><Icon name="users" className="size-6"/></div><div className="min-w-0"><h2 className="break-all text-lg font-semibold text-gray-800 dark:text-gray-100">{creator.currentHandle?`@${creator.currentHandle}`:"名字暂未返回"}</h2><p className="mt-1 text-xs text-gray-500">{marketNames[creator.market]} · 稳定身份已核验</p></div></div><Pill tone={creator.handleConflict?"warning":"success"}>{creator.handleConflict?"名字存在冲突":"OECID 已确认"}</Pill></div><div className="my-5 grid gap-3 rounded-xl border border-gray-100 p-4 text-xs dark:border-gray-800 sm:grid-cols-2"><div><p className="text-gray-400">平台 OECID</p><p className="mt-1.5 break-all font-mono text-gray-700 dark:text-gray-300">{creator.oecId}</p></div><div><p className="text-gray-400">名字最近核验 · 北京时间</p><p className="mt-1.5 text-gray-700 dark:text-gray-300">{date(creator.currentHandleVerifiedAt)}</p></div></div><ProfileRefreshPanel key={creator.creatorId} creatorId={creator.creatorId} market={creator.market} onComplete={onRefresh}/>{creator.market==="it"&&<div className="mt-4 flex justify-end"><Link href={`/opportunities?mode=matching&dataset=italy-profiles&direction=creator&registryCreatorId=${encodeURIComponent(creator.creatorId)}`} className="inline-flex items-center gap-2 rounded-lg border border-gray-300 bg-white px-4 py-2.5 text-sm font-medium text-gray-700 hover:bg-gray-50 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-300"><Icon name="search" className="size-4"/>分析适合商品</Link></div>}</div></Card>
    {creator.market==="it"&&<CreatorDraftHistory key={creator.creatorId} creatorId={creator.creatorId}/>}
    <Card title="平台画像" subtitle={`本次 ${available} 项经营字段有值 · ${date(detail.latestProfileObservedAt)}（北京时间）`}><div className="p-5"><div className="grid grid-cols-2 gap-3">{metrics.map(field=><FieldValue key={field.name} field={field}/>)}</div><p className="mt-4 text-xs leading-6 text-gray-400">价格与内容形式仅作参考。未返回的值保留来源状态，历史数据不会标成本次新结果。</p></div></Card>
    <Card title="名字历史" subtitle="改名只更新显示名称，达人身份保持不变。"><div className="divide-y divide-gray-100 dark:divide-gray-800">{detail.aliases.map(alias=><div key={alias.handle} className="flex flex-wrap items-center justify-between gap-2 px-5 py-4"><div><p className="break-all text-sm font-medium text-gray-700 dark:text-gray-300">@{alias.handle}</p><p className="mt-1 text-xs text-gray-400">首次 {date(alias.firstObservedAt)} · 最近 {date(alias.lastObservedAt)}</p></div><Pill tone={alias.isCurrent?"brand":"neutral"}>{alias.isCurrent?"最近核验名称":"历史名称"}</Pill></div>)}{!detail.aliases.length&&<p className="p-5 text-sm text-gray-500">平台尚未返回可保存的名字。</p>}</div></Card>
  </div>;
}

export default function CreatorIdentityWorkspace(){
  const router=useRouter(),query=useSearchParams();
  const rawMarket=query.get("market"),market=rawMarket==="mx"||rawMarket==="br"?rawMarket:"it";
  const status=query.get("status")==="pending"?"pending":"verified",search=query.get("q")||"",selectedId=query.get("creatorId")||"";
  const rawOffset=Number(query.get("offset")||0),offset=Number.isSafeInteger(rawOffset)&&rawOffset>=0?rawOffset:0;
  const [text,setText]=useState(search),[overview,setOverview]=useState<CreatorIdentityOverview|null>(null),[page,setPage]=useState<CreatorIdentityList|null>(null),[detail,setDetail]=useState<CreatorIdentityDetail|null>(null),[loading,setLoading]=useState(true),[error,setError]=useState(""),[revision,setRevision]=useState(0);
  const [discoveryOpen,setDiscoveryOpen]=useState(false),[discoverySeed,setDiscoverySeed]=useState<{handle:string;key:number}|null>(null);
  const sequence=useRef(0);const reload=useCallback(()=>setRevision(v=>v+1),[]);
  const openDiscovery=(handle?:string)=>{if(handle)setDiscoverySeed({handle,key:Date.now()});setDiscoveryOpen(true);};
  useEffect(()=>setText(search),[search]);
  const navigate=(patch:Record<string,string|null>)=>{const params=new URLSearchParams(query.toString());params.set("market",market);for(const [key,value] of Object.entries(patch))if(value===null)params.delete(key);else params.set(key,value);router.replace(`/creators?${params}`,{scroll:false});};
  useEffect(()=>{
    const controller=new AbortController(),version=++sequence.current;setLoading(true);setError("");
    const get=async(url:string)=>{const response=await fetch(url,{cache:"no-store",signal:controller.signal});const value=await response.json();if(!response.ok)throw new Error(value?.error?.message||"暂时无法读取达人库。");return value;};
    Promise.all([get(`${API}?view=overview&market=${market}`),get(`${API}?view=list&market=${market}&status=${status}&offset=${offset}&limit=${PAGE_SIZE}&q=${encodeURIComponent(search)}`),selectedId&&status==="verified"?get(`${API}?view=detail&creatorId=${encodeURIComponent(selectedId)}`):Promise.resolve(null)]).then(([nextOverview,nextPage,nextDetail])=>{if(controller.signal.aborted||version!==sequence.current)return;if(!Array.isArray(nextPage.items)||typeof nextOverview.verifiedIdentities!=="number")throw new Error("达人库响应不完整。");setOverview(nextOverview);setPage(nextPage);setDetail(nextDetail);}).catch(failure=>{if(!controller.signal.aborted&&version===sequence.current)setError(failure instanceof Error?failure.message:"无法读取达人库。");}).finally(()=>{if(!controller.signal.aborted&&version===sequence.current)setLoading(false);});
    return()=>controller.abort();
  },[market,status,offset,search,selectedId,revision]);
  const current=detail?.creator?.creatorId===selectedId?detail:null;
  const select=(creator:CreatorIdentitySummary)=>navigate({creatorId:creator.creatorId});
  return <div className="min-w-0"><PageHeading title="达人库" description="不断发现新达人，让每一位已收录的达人都能持续跟进。" action={<div className="flex flex-wrap gap-2"><Button variant="outline" onClick={reload} disabled={loading}><Icon name="arrow" className="size-4"/>更新列表</Button><Button onClick={()=>openDiscovery()}><Icon name="plus" className="size-4"/>批量添加达人</Button></div>}/>
    <CreatorDiscoveryPanel open={discoveryOpen} onClose={()=>setDiscoveryOpen(false)} onOpen={()=>setDiscoveryOpen(true)} market={market} seed={discoverySeed} onChanged={reload}/>
    <div className="mb-6 grid grid-cols-2 gap-4 xl:grid-cols-3">{[["已核验达人",overview?.verifiedIdentities,"按市场与 OECID 归并"],["待解析线索",overview?.pendingLeads,"保留原输入，等待身份核实"],["已解析线索",overview?.resolvedLeads,"重复来源不会重复建人"]].map(([label,value,note])=><Card key={String(label)} className={label==="已解析线索"?"col-span-2 xl:col-span-1":""}><div className="p-5"><p className="text-xs text-gray-500">{label}</p><p className="mt-3 text-3xl font-semibold tracking-tight text-gray-800 dark:text-gray-100">{value===undefined?"—":Number(value).toLocaleString()}</p><p className="mt-2 text-xs text-gray-400">{note}</p></div></Card>)}</div>
    <div className="mb-5 flex flex-wrap items-center justify-between gap-3"><Tabs value={status} onChange={value=>navigate({status:value,offset:null,creatorId:null})} items={[{value:"verified",label:"已核验达人",count:overview?.verifiedIdentities},{value:"pending",label:"待解析线索",count:overview?.pendingLeads}]}/><form className="flex w-full gap-2 sm:w-auto" onSubmit={event=>{event.preventDefault();navigate({q:text.trim()||null,offset:null,creatorId:null});}}><Input aria-label="搜索达人名称或 OECID" className="sm:!w-72" placeholder="当前名字、历史名字或 OECID" value={text} onChange={e=>setText(e.target.value)} maxLength={100}/><Button variant="outline" aria-label="搜索达人" type="submit" className="!px-3"><Icon name="search"/></Button></form></div>
    {error&&<div className="mb-5" role="alert"><Notice tone="warning">{error}</Notice></div>}
    {overview?.datasetStatus==="not_imported"?<Card><EmptyState title="尚未导入身份资料" description="达人库只展示已经保存的身份与线索。"/></Card>:<div className={`grid items-start gap-5 ${status==="verified"?"xl:grid-cols-[minmax(280px,0.85fr)_minmax(0,1.35fr)]":""}`}>
      <Card className={status==="verified"&&selectedId?"hidden xl:block":""} title={status==="verified"?`${marketNames[market]}达人`:`${marketNames[market]}待解析线索`} subtitle={status==="verified"?"点击档案查看当前画像与名字历史。":"这些 handle 尚未取得精确 OECID，不计入已核验达人。"}><div aria-busy={loading}>{page?.items.length?<div className="divide-y divide-gray-100 dark:divide-gray-800">{page.items.map(item=>item.kind==="creator"?<button key={item.creatorId} type="button" aria-pressed={item.creatorId===selectedId} onClick={()=>select(item)} className={`block w-full px-5 py-4 text-left transition hover:bg-gray-50 dark:hover:bg-white/[0.025] ${item.creatorId===selectedId?"bg-brand-25 dark:bg-brand-500/10":""}`}><div className="flex items-center gap-3"><span className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-gray-100 font-medium text-gray-500 dark:bg-gray-800">{(item.currentHandle||"?").slice(0,1).toUpperCase()}</span><div className="min-w-0 flex-1"><p className="break-all text-sm font-medium text-gray-800 dark:text-gray-200">{item.currentHandle?`@${item.currentHandle}`:"名字暂未返回"}</p><p className="mt-1 text-xs text-gray-400">最近核验 {date(item.verifiedAt)}</p></div><Icon name="arrow" className="size-4 shrink-0 text-gray-400"/></div><p className="mt-3 truncate font-mono text-[11px] text-gray-400">OECID {item.oecId}</p></button>:<div key={item.leadId} className="flex flex-wrap items-center justify-between gap-3 px-5 py-4"><div><p className="break-all text-sm font-medium text-gray-800 dark:text-gray-200">@{item.handle}</p><p className="mt-1 text-xs text-gray-400">收录于 {date(item.observedAt)}</p></div><div className="flex items-center gap-2"><Pill tone="warning">未精确匹配</Pill><Button size="sm" variant="outline" disabled={market!=="it"} onClick={()=>openDiscovery(item.handle)}>重新解析</Button></div></div>)}</div>:<EmptyState title={loading?"正在读取达人资料…":"没有符合条件的记录"} description={loading?"从本机身份库读取。":search?"可以尝试当前名字、历史名字或完整 OECID。":"此市场尚无对应记录。"}/>}</div><div className="flex items-center justify-between border-t border-gray-100 px-5 py-3 dark:border-gray-800"><span className="text-xs text-gray-400">{page?.total?`${offset+1}–${Math.min(offset+PAGE_SIZE,page.total)} / ${page.total}`:"0 条"}</span><div className="flex gap-1"><Button size="sm" variant="ghost" disabled={loading||offset===0} onClick={()=>navigate({offset:String(Math.max(0,offset-PAGE_SIZE)),creatorId:null})}>上页</Button><Button size="sm" variant="ghost" disabled={loading||!page||offset+PAGE_SIZE>=page.total} onClick={()=>navigate({offset:String(offset+PAGE_SIZE),creatorId:null})}>下页</Button></div></div></Card>
      {status==="verified"&&(current?<div className="min-w-0"><Button variant="ghost" size="sm" className="mb-3 xl:hidden" onClick={()=>navigate({creatorId:null})}>返回达人名单</Button><Detail detail={current} onRefresh={reload}/></div>:<Card><EmptyState title={loading&&selectedId?"正在读取档案…":"选择一位达人"} description="查看已经核验的身份、平台画像和历史名字。刷新会一直跟随 OECID，不因改名丢失档案。"/></Card>)}
    </div>}
    <div className="mt-6 flex flex-wrap gap-5 text-xs text-gray-500"><Link href="/opportunities?mode=matching&dataset=italy-profiles" className="hover:text-brand-500">查看画像匹配 →</Link><Link href="/workspace?mode=second" className="hover:text-brand-500">回到二发工作台 →</Link><span>原消息与合作状态保持原有归属。</span></div>
  </div>;
}
