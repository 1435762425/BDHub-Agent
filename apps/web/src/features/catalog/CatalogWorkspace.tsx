"use client";
import Link from "next/link";
import {useCallback,useEffect,useRef,useState} from "react";
import {Button,Card,Collapsible,Field,Input,Notice,PageHeading,Pill,Progress,Section,Tabs} from "../bdhub/ui";
import type {CatalogLinkStatus,GlobalStatus} from "../../server/global-source/bridge";
import type {CatalogScreenConfig,CatalogScreenState} from "../../server/catalog-screen/bridge";
import LeadsQueuePanel from "./LeadsQueuePanel";
import IdentityPanel from "./IdentityPanel";
import CampaignPanel from "./CampaignPanel";
import FunnelBar from "./FunnelBar";
import ShortNames from "./ShortNames";
import {buildFunnel,type FunnelStage} from "./funnel";
import {useLeadsQueue} from "./useLeadsQueue";
import {useIdentityQueue} from "./useIdentityQueue";
import {useCatalogJobs} from "./useCatalogJobs";
import LeadsPanel from "./LeadsPanel";
import {useLeadPool} from "./useLeadPool";
const states:Record<string,string>={collecting:"采集中",completed:"查询范围已采完",partial:"覆盖尚不完整",blocked:"等待处理"};
const linkReasons:Record<string,string>={card_search_incomplete:"商品卡查询不完整，暂不判断标准链接",card_read_unresolved:"商品卡读取未完成",card_members_incomplete:"成员回查不完整",existing_links_require_review:"旧卡只保留历史，等待标准链接",existing_links_other_campaign:"旧卡属于其他活动，不作为当前材料",product_no_longer_eligible:"商品当前不再符合初筛",selected_campaign_changed:"活动绑定已变化，需按当前活动重查",catalog_link_creation_pending:"等待标准链接",catalog_link_creation_unresolved:"建链结果未知，按原意图回查",catalog_link_requires_review:"旧口径记录待重新检查",catalog_link_terms_changed:"标准链接与当前商品方案不一致",catalog_link_not_prepared:"尚未进入标准链接准备",link_creator_not_above_public:"历史卡达人佣金未高于公开佣金",link_agency_below_minimum:"历史卡机构收益不足1个百分点",link_not_platform_valid:"历史卡当前平台无效",link_product_not_eligible:"历史卡商品当前不满足资格"};
// The step names a running link batch publishes, in the operator's terms rather than the driver's.
const linkPhases:Record<string,string>={seed:"整理商品清单",read:"只读检查已有链接",create:"平台新建缺链",done:"已结束"};
const pct=(value:unknown)=>(typeof value==="string"&&value!==""||typeof value==="number")&&Number.isFinite(Number(value))?`${Number(value)/100}%`:"未返回";
const screenReason=(code:string,config?:CatalogScreenConfig)=>{switch(code){case"sales_missing":return "销量字段缺失";case"sales_below_min":return `累计销量不足 ${config?.minSales??"—"}`;case"rating_unrated":return "暂无评分（当前不允许）";case"rating_below_min":return `评分低于 ${config?.minRating??"—"}`;case"commission_missing":return "佣金字段缺失";case"commission_gap_below_min":return `两档佣金差不足 ${config?.minCommissionGapPoints??"—"} 个点`;default:return code;}};

export default function CatalogWorkspace(){
 const [tab,setTab]=useState<"full"|"campaign"|"leads">("full");
 const queue=useLeadsQueue();
 const identity=useIdentityQueue();
 const jobs=useCatalogJobs();
 const leadPool=useLeadPool();
 const [syncing,setSyncing]=useState(false);const syncId=useRef<string|null>(null);const [revision,setRevision]=useState(0);
 async function sync(){setSyncing(true);try{syncId.current??=crypto.randomUUID();const r=await fetch("/api/global-source",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action:"sync",requestId:syncId.current})});if(!r.ok)throw Error();syncId.current=null;setRevision(v=>v+1);}catch{setError(true);}finally{setSyncing(false);}}
 const [data,setData]=useState<GlobalStatus|null>(null),[query,setQuery]=useState(""),[text,setText]=useState(""),[offset,setOffset]=useState(0),[error,setError]=useState(false);
 const [links,setLinks]=useState<CatalogLinkStatus|null>(null),[linksError,setLinksError]=useState(false);
 useEffect(()=>{const controller=new AbortController();let timer:ReturnType<typeof setTimeout>;const poll=async()=>{try{const r=await fetch(`/api/global-source?offset=${offset}&q=${encodeURIComponent(query)}`,{signal:controller.signal,cache:"no-store"});if(!r.ok)throw Error();const v=await r.json();if(!controller.signal.aborted){setData(v);setError(false);}}catch{if(!controller.signal.aborted)setError(true);}finally{if(!controller.signal.aborted)timer=setTimeout(poll,10000);}};void poll();return()=>{controller.abort();clearTimeout(timer);};},[offset,query,revision]);
 useEffect(()=>{const controller=new AbortController();let timer:ReturnType<typeof setTimeout>;const poll=async()=>{try{const r=await fetch("/api/global-source?links=1",{signal:controller.signal,cache:"no-store"});if(!r.ok)throw Error();const v=await r.json();if(!controller.signal.aborted){setLinks(v);setLinksError(false);}}catch{if(!controller.signal.aborted)setLinksError(true);}finally{if(!controller.signal.aborted)timer=setTimeout(poll,30000);}};void poll();return()=>{controller.abort();clearTimeout(timer);};},[]);
 const [screen,setScreen]=useState<CatalogScreenState|null>(null),[screenDraft,setScreenDraft]=useState<CatalogScreenConfig|null>(null);
 const [screenBusy,setScreenBusy]=useState(false),[screenMessage,setScreenMessage]=useState<string|null>(null);
 // A first read that has not landed yet is loading, not unavailable: the page mounts eight
 // Python-backed reads at once and the slowest can take a few seconds.
 const [screenLoaded,setScreenLoaded]=useState(false);
 useEffect(()=>{const controller=new AbortController();void(async()=>{try{const r=await fetch("/api/catalog-screen",{signal:controller.signal,cache:"no-store"});if(!r.ok)throw Error();const v:CatalogScreenState=await r.json();if(!controller.signal.aborted){setScreen(v);setScreenDraft(v.config);setScreenLoaded(true);}}catch{if(!controller.signal.aborted)setScreen(null);}})();return()=>controller.abort();},[]);
 async function screenAction(action:"preview"|"save"){if(!screenDraft)return;setScreenBusy(true);setScreenMessage(null);try{const r=await fetch("/api/catalog-screen",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action,config:screenDraft})});const v=await r.json();if(!r.ok){setScreenMessage("保存被拒绝：门槛超出允许范围，请检查三个数值。");return;}setScreen(v);setScreenDraft(v.config);setScreenMessage(action==="save"?"门槛已保存，并已按新门槛重新筛分本批采集结果。":"试算已按当前输入更新，未写入任何记录。");}catch{setScreenMessage("暂时无法读取或保存筛选门槛。");}finally{setScreenBusy(false);}}
 const linkedCount=links?.summary?links.summary.verifiedPidCount:null;
 const jump=(id:string)=>document.getElementById(id)?.scrollIntoView({behavior:"smooth",block:"start"});
 const stages:FunnelStage[]=buildFunnel({collected:data?.products??null,screened:screen?.funnel?.eligible??null,
  screenedOf:screen?.funnel?.collected??null,linked:linkedCount,leadsPending:queue.data?.firstTime??null});
 const selection=data?.selectionBatch;
 // Link preparation reports the step it is on, so a long batch does not look hung. A read pass
 // counts products it has judged; once it starts writing, the bar counts links it has built.
 const linkProgress=jobs.data?.links.run?.progress??null;
 const linksRunning=Boolean(jobs.data?.links.run?.running);
 const judgedLinks=linkProgress?Math.max(0,linkProgress.total-(linkProgress.states.pending??0)-(linkProgress.states.reading??0)):0;
 const buildingLinks=linkProgress!==null&&linkProgress.phase==="create";
 // A step with no total yet means the batch has started but not judged anything: there is no bar
 // to draw, and the only honest thing to say is that it is still reading.
 const showLinkBar=linkProgress!==null&&linkProgress.total>0;
 const linkBar=buildingLinks&&linkProgress
  ?{done:linkProgress.created,total:linkProgress.created+(linkProgress.states.missing??0),label:"建链进展（本批已建 / 待建起点）"}
  :{done:judgedLinks,total:linkProgress?.total??0,label:"准备进展（已判定 / 本批已选商品）"};
 return <div className="space-y-5"><PageHeading title="货盘" description="高机会商品 · 仅全球销售商品。一条链：采集 → 筛选入池 → 准备链接 → 查达人线索。"/>
 {error&&<Notice tone="warning">暂时无法读取最新货盘状态，已有记录保留。</Notice>}
 <Tabs items={[{value:"full",label:"全托商品",count:data?.products},{value:"campaign",label:"非全托商品"},{value:"leads",label:"达人线索",count:queue.data?.scope??leadPool.data?.counts?.positions}]} value={tab} onChange={setTab}/>
 {tab==="leads"?<LeadsPanel/>:tab==="campaign"?<CampaignPanel onOpenLeads={()=>setTab("leads")}/>:<>
 <FunnelBar stages={stages} onJump={id=>id==="card-leads"?setTab("leads"):jump(id)}/>
 {tab==="full"&&leadPool.data?.available&&<div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
  {(()=>{const c=leadPool.data.counts,b=leadPool.data.business;return [["可发送",b.sendable,`${c.readyCreators.toLocaleString()} 个达人，各取一个最优 PID`],["等待中",b.waiting,"等轮次、冷却或达人问题处理"],["暂不参与",b.inactive,"商品当前不合格或明确排除"],["已发送历史",leadPool.data.history.sent,`${c.positions.toLocaleString()} 个累计位置`]].map(([label,value,hint])=><div key={String(label)} className="rounded-2xl border border-gray-200 bg-white p-4 dark:border-gray-800 dark:bg-white/[0.025]"><p className="text-xs text-gray-500">{label}</p><p className="mt-1 text-xl font-semibold tabular-nums">{Number(value).toLocaleString()}</p><p className="mt-0.5 text-xs leading-5 text-gray-400">{hint}</p></div>);})()}
 </div>}
 <Section id="stage-collect" index="①" title="采集与筛选" summary={<>采集 {data?.products?.toLocaleString()??"—"} · 筛出 {screen?.funnel?.eligible.toLocaleString()??"—"} · 已备链 {linkedCount?.toLocaleString()??"—"}</>}>
 <div id="card-collect" className="scroll-mt-6"><Card title="意大利 · 全托主力来源" action={<div className="flex flex-wrap items-center gap-2"><Pill tone={data?.published?"success":data?.state==="blocked"?"warning":"brand"}>{data?.state?states[data.state]:"读取中"}</Pill><Button size="sm" onClick={()=>void sync()} disabled={syncing}>{syncing?"提交中…":data?.state==="collecting"?"继续采集":"主动采集"}</Button></div>}><div className="space-y-4 p-5"><div className="grid grid-cols-2 gap-4 lg:grid-cols-4">{[["已采集去重商品",data?.products],["本查询报告数量",data?.reportedTotal],["采集时未选",data?.listedUnselectedProducts],["已核对活动详情",data?.detailProducts]].map(([label,value])=><div key={String(label)} className="rounded-xl bg-gray-50 p-4 dark:bg-gray-800"><p className="text-xs text-gray-500">{label}</p><p className="mt-2 text-2xl font-semibold">{value==null?"—":Number(value).toLocaleString()}</p></div>)}</div>
 {data?.reason&&data.state!=="completed"&&<Notice tone="warning">{{source_remote_rejected:"平台暂未接受查询，已保留采集进度。",repeated_page:"平台返回重复分页，需要核对后续采。",endpoint_end_total_mismatch:"已到接口末页，但数量尚未核对一致。",source_maintenance_due:"账号正在维护，等待恢复后续采。"}[data.reason]||"采集需进一步核对，已保存原始进度。"}</Notice>}
 {data?.updatedAt&&<p className="text-xs text-gray-400">最近写入 {new Date(data.updatedAt*1000).toLocaleString("zh-CN",{timeZone:"Asia/Shanghai",hour12:false})} · 北京时间</p>}
 <Collapsible label="商品明细（可搜索）" count={data?.totalMatches}><form className="mb-4 flex gap-2" onSubmit={e=>{e.preventDefault();setOffset(0);setQuery(text.trim());}}><Input aria-label="搜索货盘商品" placeholder="搜索 PID 或商品标题" value={text} onChange={e=>setText(e.target.value)} maxLength={100}/><Button type="submit" variant="outline" className="shrink-0 whitespace-nowrap">搜索</Button></form>
 {data?.displayRunId&&data.displayRunId!==data.id&&<p className="mb-4 text-xs text-gray-500">当前新一轮仍在采集，商品列表继续展示上次完整快照；新快照完整核对后再切换。</p>}<div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead className="border-b border-gray-200 text-xs text-gray-500 dark:border-gray-700"><tr>{["商品","公开佣金","平台展示总佣金","选入观察","进一步核验"].map(label=><th key={label} className="whitespace-nowrap px-3 py-3 font-medium">{label}</th>)}</tr></thead><tbody>{data?.items.map(p=><tr key={p.pid} className="border-b border-gray-100 dark:border-gray-800"><td className="min-w-64 max-w-md px-3 py-4"><p className="line-clamp-2" title={p.title}>{p.title}</p><p className="mt-1 font-mono text-xs text-gray-400">{p.pid}</p></td><td className="px-3 py-4">{pct(p.publicCommissionRaw)}</td><td className="px-3 py-4">{pct(p.totalCommissionRaw)}</td><td className="whitespace-nowrap px-3 py-4">{p.selectionObservation&&["confirmed","already_selected"].includes(p.selectionObservation.state)?"已选入（已回查）":p.listedSelected===true?"采集时已选":p.listedSelected===false?"采集时未选":"待确认"}</td><td className="min-w-48 px-3 py-4">{p.stockChecked&&p.selectedOffers.length?p.selectedOffers.map((o,i)=><p key={i} className="text-xs leading-6">拟给达人 {o.creatorPercent??"未确定"}% · {o.assessment.eligible?"符合本地准备条件":"条件不满足"}</p>):<Pill tone="neutral">{p.detailsChecked?"待选入／方案核验":"待核验活动与佣金"}</Pill>}</td></tr>)}</tbody></table></div>
 {!data?.items.length&&<p className="py-8 text-center text-sm text-gray-500">{data?.available?"当前没有匹配的商品":"等待采集记录"}</p>}
 <div className="mt-4 flex items-center justify-between text-xs text-gray-500"><span>{data?.totalMatches?`${offset+1}–${Math.min(offset+30,data.totalMatches)} / ${data.totalMatches.toLocaleString()}`:"0 条"}</span><div className="flex gap-2"><Button size="sm" variant="outline" disabled={offset===0} onClick={()=>setOffset(v=>Math.max(0,v-30))}>上一页</Button><Button size="sm" variant="outline" disabled={!data||offset+30>=data.totalMatches} onClick={()=>setOffset(v=>v+30)}>下一页</Button></div></div>
 </Collapsible>
 </div></Card></div>
 <div id="card-screen" className="scroll-mt-6"><Card title="采集即筛：全托商品入池门槛"><div className="space-y-4 p-5">
 {!screenDraft&&<p className="text-sm text-gray-500">{screenLoaded?"暂时无法读取筛选门槛。":"读取中…"}</p>}
 {screenDraft&&<>
 {screen?.funnel?<>
 <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">{[["本批采集去重商品",screen.funnel.collected],["筛出（合格）",screen.funnel.eligible],["其中已在池中",screen.funnel.selectedEligible]].map(([label,value])=><div key={String(label)} className="rounded-xl bg-gray-50 p-4 dark:bg-gray-800"><p className="text-xs text-gray-500">{label}</p><p className="mt-2 text-2xl font-semibold">{Number(value).toLocaleString()}</p></div>)}<div className="rounded-xl bg-brand-50 p-4 dark:bg-brand-500/10"><p className="text-xs text-gray-500">待入池</p><p className="mt-2 text-2xl font-semibold">{screen.funnel.unselectedEligible.toLocaleString()}</p><div className="mt-2"><Button size="sm" disabled={jobs.busy!==null||Boolean(jobs.data?.selection.run?.running)||screen.funnel.unselectedEligible===0} onClick={()=>void jobs.start("selection")}>{jobs.data?.selection.run?.running?"选入中…":"一键选入"}</Button></div></div></div>
 <p className="text-xs italic leading-5 text-gray-500">合格率 {screen.funnel.collected?((screen.funnel.eligible/screen.funnel.collected)*100).toFixed(1):"0.0"}%；筛除 {screen.funnel.rejected.toLocaleString()} 个（{Object.entries(screen.funnel.reasons).sort((a,b)=>b[1]-a[1]).map(([code,count])=>`${screenReason(code,screen.funnel?.config)}，${count.toLocaleString()} 个`).join("；")||"—"}）；另有暂无评分的 {screen.funnel.unrated.toLocaleString()} 个（评分为 0 的语义未核实）。</p>
 </>:<p className="text-sm text-gray-500">还没有可筛分的采集批次。</p>}
 {selection&&<><Progress done={(selection.states.confirmed??0)+(selection.states.already_selected??0)} total={selection.total} label="选入进度"/>{((selection.states.needs_review??0)+(selection.states.result_unknown??0))>0&&<p className="text-xs text-gray-500">需人工核对 {selection.states.needs_review??0} · 结果待核验 {selection.states.result_unknown??0}</p>}</>}
 {jobs.message&&jobs.busy===null&&<Notice tone="info">{jobs.message}</Notice>}
 <div className="grid gap-4 lg:grid-cols-4">
 <Field label="累计销量下限（件）"><Input type="number" min={0} max={1000000} value={screenDraft.minSales} onChange={e=>setScreenDraft({...screenDraft,minSales:Number(e.target.value)})}/></Field>
 <Field label="评分下限"><Input type="number" min={0} max={5} step={0.1} value={screenDraft.minRating} onChange={e=>setScreenDraft({...screenDraft,minRating:Number(e.target.value)})}/></Field>
 <Field label="佣金差下限（百分点）"><Input type="number" min={0} max={100} step={0.5} value={screenDraft.minCommissionGapPoints} onChange={e=>setScreenDraft({...screenDraft,minCommissionGapPoints:Number(e.target.value)})}/></Field>
 <Field label="暂无评分允许入池" hint="关闭后评分为 0 的商品一律筛除。"><label className="flex items-center gap-2 text-sm"><input type="checkbox" className="h-4 w-4" checked={screenDraft.allowUnrated} onChange={e=>setScreenDraft({...screenDraft,allowUnrated:e.target.checked})}/><span>{screenDraft.allowUnrated?"允许":"不允许"}</span></label></Field>
 </div>
 <div className="flex gap-2"><Button size="sm" variant="outline" disabled={screenBusy} onClick={()=>void screenAction("preview")}>试算改动</Button><Button size="sm" disabled={screenBusy} onClick={()=>void screenAction("save")}>{screenBusy?"处理中…":"保存门槛并重新筛分"}</Button></div>
 {screenMessage&&<Notice tone="info">{screenMessage}</Notice>}
 {screen?.preview&&<div className="rounded-xl border border-gray-200 p-4 text-sm dark:border-gray-700"><p>按新门槛（销量 ≥{screen.preview.config.minSales}、评分 ≥{screen.preview.config.minRating}、佣金差 ≥{screen.preview.config.minCommissionGapPoints} 个点{screen.preview.config.allowUnrated?"、暂无评分允许":""}）：筛出 {screen.preview.eligible.toLocaleString()} 个，现在 {screen.funnel?.eligible.toLocaleString()??"—"} 个。比现在新增 {screen.preview.addedVersusActive.toLocaleString()}、移出 {screen.preview.removedVersusActive.toLocaleString()}。</p></div>}
 </>}
 </div></Card></div>
 <div id="card-links" className="scroll-mt-6"><Card title="TapLink 准备"><div className="space-y-4 p-5">
 {linksError&&<Notice tone="warning">暂时无法读取链接准备台账，已保存的准备结果不受影响。</Notice>}
 {links&&!links.available&&!linksRunning&&<p className="text-sm text-gray-500">尚未开始链接准备。</p>}
 {(showLinkBar||linksRunning)&&<div className="rounded-xl border border-gray-200 p-4 dark:border-gray-700">
 {showLinkBar&&linkProgress?<><Progress done={linkBar.done} total={linkBar.total} label={linkBar.label}/>
 <p className="mt-2 text-xs leading-5 text-gray-500">{linksRunning?"进行中":"上一批"}：第 {linkProgress.step} 步 · {linkPhases[linkProgress.phase]??(linkProgress.phase||"—")}{linkProgress.phase==="read"&&linkProgress.pass?`（第 ${linkProgress.pass} / ${linkProgress.passes} 遍）`:""} · 已判定 {judgedLinks.toLocaleString()} · 待建链 {(linkProgress.states.missing??0).toLocaleString()} · 本批已建 {linkProgress.created.toLocaleString()}{linkProgress.updatedAt?` · 更新 ${new Date(linkProgress.updatedAt*1000).toLocaleString("zh-CN",{timeZone:"Asia/Shanghai",hour12:false})}`:""}</p></>
 :<p className="text-sm text-gray-500">已启动，正在整理商品清单…</p>}
 </div>}
 {links?.available&&links.summary&&<>
 <div className="grid grid-cols-2 gap-4 lg:grid-cols-3">{[["本批已选商品",links.summary.total],["当前标准链接",links.summary.verifiedPidCount],["等待标准链接",links.summary.pendingCount]].map(([label,value])=><div key={String(label)} className="rounded-xl bg-gray-50 p-4 dark:bg-gray-800"><p className="text-xs text-gray-500">{label}</p><p className="mt-2 text-2xl font-semibold">{Number(value).toLocaleString()}</p></div>)}</div>
 <ShortNames/>
 {links.summary.errors.length>0&&<Notice tone="warning">读取不完整，需核对：{links.summary.errors.map(e=>linkReasons[e]??e).join("；")}</Notice>}
 {links.summary.retryableErrors&&links.summary.retryableErrors.length>0&&<Notice tone="info">上次建链有 {links.summary.retryableErrors.length} 类未完成{links.summary.errorsUpdatedAt?`（${new Date(links.summary.errorsUpdatedAt*1000).toLocaleString("zh-CN",{timeZone:"Asia/Shanghai",hour12:false})}）`:""}：{links.summary.retryableErrors.map(e=>{const base=e.split(":")[0];const detail=e.slice(base.length+1);return (linkReasons[base]??base)+(detail?`（${detail}）`:"");}).join("；")}。这些商品仍在待建链里，再点一次「准备链接并新建」会重试。</Notice>}
 <div className="grid gap-3 border-t border-gray-100 pt-4 dark:border-gray-800 sm:grid-cols-2 lg:grid-cols-4 lg:items-end"><Field label="每次读取条数"><Input type="number" min={1} max={200} value={jobs.draft.links?.readLimit??15} onChange={e=>jobs.setDraft("links",{...jobs.draft.links,readLimit:Number(e.target.value)})}/></Field><Field label="每次新建上限"><Input type="number" min={0} max={200} value={jobs.draft.links?.creates??0} onChange={e=>jobs.setDraft("links",{...jobs.draft.links,creates:Number(e.target.value)})}/></Field><Button size="sm" disabled={jobs.busy!==null||Boolean(jobs.data?.links.run?.running)} onClick={()=>void jobs.start("links")}>{jobs.data?.links.run?.running?"准备中…":(jobs.draft.links?.creates??0)>0?"准备链接并新建":"准备链接（只查不建）"}</Button>{jobs.data?.links.run?.running&&<Pill tone={(jobs.draft.links?.creates??0)>0?"warning":"brand"}>{(jobs.draft.links?.creates??0)>0?"平台写入进行中":"只读检查中"}</Pill>}</div>
 </>}
 </div></Card></div>
 </Section>
 </>}
 <Link href="/it/workspace/send" className="inline-block text-sm text-brand-500">进入发送工作台 →</Link>
 </div>;
}
