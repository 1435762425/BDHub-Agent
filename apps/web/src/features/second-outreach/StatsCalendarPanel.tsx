"use client";
import {useRef,useState} from "react";
import {Button,Card,Dialog,EmptyState,Notice,Pill,StatTile} from "../bdhub/ui";
import {buildStatsCalendar} from "./stats-calendar";
import type {InboxController} from "./useInboxMonitor";
import type {InboxDayDetail,InboxDetailItem} from "../../server/inbox/bridge";

const caseReasons:Record<string,string>={catalog_request:"更多商品目录",sample_request:"样品申请",link_issue:"链接问题",refund_sample:"退款或样品费用",multiple_requests:"多个诉求",unclassified:"待人工判断",human:"待人工判断"};
const detailTime=(value:number)=>new Intl.DateTimeFormat("zh-CN",{hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false,timeZone:"Asia/Shanghai"}).format(new Date(value));
const detailCount=(day:{cards:number;unconfirmed:number;replies:number;showcase:number;autoReplies:number;casesOpened:number})=>day.cards+day.unconfirmed+day.replies+day.showcase+day.autoReplies+day.casesOpened;

function DayMetric({label,value,strong=false}:{label:string;value:number;strong?:boolean}){
 return <div><p className="text-[11px] leading-4 text-gray-400">{label}</p><p className={`mt-0.5 tabular-nums ${strong?"text-base font-semibold text-gray-800 dark:text-white":"text-sm font-medium text-gray-600 dark:text-gray-300"}`}>{value.toLocaleString()}</p></div>;
}

function DetailItem({item}:{item:InboxDetailItem}){
 const creator=item.handle?`@${item.handle}`:item.creatorId?`达人 ${item.creatorId.slice(-8)}`:"达人未关联";
 const delivery=item.kind==="delivery",reply=item.kind==="reply";
 const title=delivery?(item.status==="confirmed"?"确认商品卡":"未确认商品卡"):reply?"达人回复":item.kind==="showcase"?"加橱窗通知":item.kind==="auto_reply"?"历史服务回复":"新建人工案件";
 const tone=delivery&&item.status!=="confirmed"?"warning":reply?"brand":item.kind==="case"?"warning":"neutral";
 return <article className="rounded-xl border border-gray-200 p-4 dark:border-gray-800">
  <div className="flex flex-wrap items-start justify-between gap-2"><div><p className="text-sm font-semibold text-gray-800 dark:text-white">{title}</p><p className="mt-1 text-xs text-gray-400">{detailTime(item.occurredAt)} · {creator}</p></div><Pill tone={tone}>{delivery?(item.status==="confirmed"?"已确认":item.status??"未确认"):item.kind==="case"?(caseReasons[item.status??""]??"人工事项"):title}</Pill></div>
  {delivery&&<p className="mt-3 text-xs leading-5 text-gray-500">{item.product??"商品名称未记录"}{item.pid?` · PID ${item.pid}`:""}{item.creatorPercent?` · 达人佣金 ${item.creatorPercent}%`:""}{item.catalogSource?` · ${item.catalogSource==="campaign"?"Campaign":"全托已选"}`:""}{item.handleAtEvent&&item.handleAtEvent!==item.handle?` · 发送时 @${item.handleAtEvent}`:""}</p>}
  {item.text?<p className="mt-3 whitespace-pre-wrap break-words rounded-lg bg-gray-50 p-3 text-sm leading-6 text-gray-700 dark:bg-gray-800 dark:text-gray-200">{item.text}</p>:reply&&<p className="mt-3 text-xs text-gray-500">正文尚未取得；请到原会话核对附件或消息。</p>}
  {delivery&&<p className="mt-2 text-xs text-gray-400">配套文字：{item.textState==="confirmed"?"已确认":item.textState??"未记录"}</p>}
 </article>;
}

/** 日历复用 useInboxMonitor 的载荷，不启动第二个请求或轮询。 */
export default function StatsCalendarPanel({controller}:{controller:InboxController}){
 const {data,loaded}=controller;
 const [selectedDate,setSelectedDate]=useState<string|null>(null);
 const [detail,setDetail]=useState<InboxDayDetail|null>(null);
 const [detailBusy,setDetailBusy]=useState(false);
 const [detailError,setDetailError]=useState("");
 const request=useRef(0);
 const loadDetail=async(date:string,offset=0)=>{
  const sequence=++request.current;setSelectedDate(date);setDetailBusy(true);setDetailError("");
  try{
   const query=new URLSearchParams({date,offset:String(offset),limit:"50"});
   const response=await fetch(`/api/inbox?${query}`,{cache:"no-store"});
   if(!response.ok)throw Error();
   const value=await response.json() as InboxDayDetail;
   if(sequence!==request.current)return;
   setDetail(current=>offset>0&&current?.date===date?{...value,items:[...current.items,...value.items]}:value);
  }catch{if(sequence===request.current)setDetailError("暂时无法读取这一天的明细，汇总数字仍保留。");}
  finally{if(sequence===request.current)setDetailBusy(false);}
 };
 const closeDetail=()=>{request.current+=1;setSelectedDate(null);setDetail(null);setDetailError("");setDetailBusy(false);};
 if(!data?.available)return <Card title="统计日历" subtitle="按北京时间查看最近 14 个自然日。">
  <div className="p-5"><EmptyState title={!loaded?"正在读取统计…":"暂时无法读取统计"}
   description={!loaded?"读取完成后会显示每天的确认触达、回复和加橱窗。":"没有可靠数据时不会用 0 代替真实业务结果。"}/></div>
 </Card>;
 if(!data.days.length)return <Card title="统计日历" subtitle="按北京时间查看最近 14 个自然日。">
  <div className="p-5"><EmptyState title="还没有可展示的日统计" description="当前读取结果不包含有效日期，因此不显示看起来像真实结果的 0。"/></div>
 </Card>;
 let model:ReturnType<typeof buildStatsCalendar>;
 try{model=buildStatsCalendar(data);}catch{return <Card title="统计日历" subtitle="按北京时间查看最近 14 个自然日。">
  <div className="p-5"><Notice tone="warning">统计总数与每日明细没有对平，页面已停止展示，避免把不一致的数据当成经营结果。</Notice></div>
 </Card>;}
 const {totals,days}=model;
 return <Card title="统计日历" subtitle={`最近 ${days.length} 个北京自然日；每日数据从同一份发送与收信账本只读汇总。`}
  action={<Pill tone="neutral">北京时间 UTC+8</Pill>}>
  <div className="space-y-5 p-5">
   <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
    <StatTile label="确认触达（达人次）" value={totals.creators} hint="每日按达人去重，再按天相加" brand/>
    <StatTile label="确认商品卡" value={totals.cards} hint={`另有 ${totals.texts.toLocaleString()} 条确认文字`}/>
    <StatTile label="收到达人回复" value={totals.replies} hint="只算实时事件；历史补录不计"/>
    <StatTile label="加橱窗" value={totals.showcase} hint="只算实时的平台橱窗通知"/>
   </div>

   <div className={`flex flex-wrap items-center justify-between gap-3 rounded-xl border px-4 py-3 ${totals.unconfirmed?"border-warning-200 bg-warning-50 dark:border-warning-900 dark:bg-warning-900/10":"border-gray-200 bg-gray-50 dark:border-gray-800 dark:bg-white/[0.03]"}`}>
    <div><p className="text-sm font-medium text-gray-700 dark:text-gray-200">未确认商品卡</p><p className="mt-1 text-xs leading-5 text-gray-500">已产生发送动作但没有确认回执；单独保留，不计入确认商品卡和触达。</p></div>
    <strong className={`text-2xl tabular-nums ${totals.unconfirmed?"text-warning-600 dark:text-warning-400":"text-gray-500"}`}>{totals.unconfirmed.toLocaleString()}</strong>
   </div>

   <div>
    <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
     <h3 className="text-sm font-medium text-gray-700 dark:text-gray-200">每日明细</h3>
     <p className="text-xs text-gray-400">今天用蓝色标出</p>
    </div>
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-7">
     {days.map(day=><button type="button" key={day.date} aria-label={`${day.date}${day.isToday?' 今天':''}，点击查看明细`} onClick={()=>void loadDetail(day.date)}
      className={`min-h-48 rounded-xl border p-3 text-left transition hover:border-brand-300 hover:shadow-theme-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 ${day.isToday?"border-brand-300 bg-brand-50 ring-1 ring-brand-200 dark:border-brand-700 dark:bg-brand-500/10 dark:ring-brand-900":"border-gray-200 bg-white dark:border-gray-800 dark:bg-white/[0.02]"}`}>
      <div className="mb-3 flex items-start justify-between gap-2 border-b border-gray-100 pb-2 dark:border-gray-800">
       <div><p className="text-sm font-semibold text-gray-800 dark:text-white">{day.dateLabel}</p><p className="mt-0.5 text-xs text-gray-400">{day.weekdayLabel}</p></div>
       {day.isToday&&<Pill tone="brand">今天</Pill>}
      </div>
      <div className="grid grid-cols-2 gap-x-3 gap-y-2.5">
       <DayMetric label="触达达人" value={day.creators} strong/>
       <DayMetric label="确认卡片" value={day.cards}/>
       <DayMetric label="达人回复" value={day.replies}/>
       <DayMetric label="加橱窗" value={day.showcase}/>
       <DayMetric label="确认文字" value={day.texts}/>
       <DayMetric label="未确认" value={day.unconfirmed}/>
      </div>
      <p className="mt-3 border-t border-gray-100 pt-2 text-[11px] text-brand-500 dark:border-gray-800">{detailCount(day)>0?`查看 ${detailCount(day)} 条核对记录`:`查看当天空记录`}</p>
     </button>)}
    </div>
   </div>

   <Notice>统计按<strong>北京时间</strong>分天；未确认发送不算成功；回复与加橱窗均排除历史补录。当前未结人工事项为 <strong>{data.openCases.toLocaleString()}</strong> 件，它是现在的状态，不拆到某一天。</Notice>
  </div>
  <Dialog open={selectedDate!==null} onClose={closeDetail} title={`${selectedDate??""} · 日明细`} description="只读核对当日确认/未确认商品卡、达人回复、加橱窗、历史服务回复和新建人工事项。" wide>
   {detailError&&<Notice tone="warning">{detailError}</Notice>}
   {!detail&&!detailError?<p className="py-8 text-center text-sm text-gray-500">正在读取当天明细…</p>:detail&&!detail.available?<EmptyState title="当天明细不可用" description="读取失败不会显示成真实的 0。"/>:detail&&<div className="space-y-4">
    <div className="grid grid-cols-3 gap-3 sm:grid-cols-6">{[["确认卡",detail.summary?.cards??0],["未确认",detail.summary?.unconfirmed??0],["回复",detail.summary?.replies??0],["加橱窗",detail.summary?.showcase??0],["历史回复",detail.summary?.autoReplies??0],["新案件",detail.summary?.casesOpened??0]].map(([label,value])=><div key={String(label)} className="rounded-lg bg-gray-50 p-3 text-center dark:bg-gray-800"><p className="text-[11px] text-gray-400">{label}</p><p className="mt-1 text-lg font-semibold tabular-nums">{Number(value).toLocaleString()}</p></div>)}</div>
    <p className="text-xs leading-5 text-gray-500">共 {detail.total.toLocaleString()} 条核对记录，当前显示 {detail.items.length.toLocaleString()} 条。确认文字跟随对应商品卡展示，不把同一次触达重复列成两行。</p>
    <div className="space-y-3">{detail.items.map(item=><DetailItem key={`${item.kind}-${item.ref}`} item={item}/>)}</div>
    {!detail.items.length&&<EmptyState title="当天没有发送或入站明细" description="这是已成功读取的真实空结果。"/>}
    {detail.nextOffset!==null&&<div className="flex justify-center"><Button variant="outline" disabled={detailBusy} onClick={()=>void loadDetail(detail.date,detail.nextOffset??0)}>{detailBusy?"正在读取…":`继续加载（还有 ${(detail.total-detail.items.length).toLocaleString()} 条）`}</Button></div>}
   </div>}
  </Dialog>
 </Card>;
}
