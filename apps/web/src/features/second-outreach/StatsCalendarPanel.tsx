import {Card,EmptyState,Notice,Pill,StatTile} from "../bdhub/ui";
import {buildStatsCalendar} from "./stats-calendar";
import type {InboxController} from "./useInboxMonitor";

function DayMetric({label,value,strong=false}:{label:string;value:number;strong?:boolean}){
 return <div><p className="text-[11px] leading-4 text-gray-400">{label}</p><p className={`mt-0.5 tabular-nums ${strong?"text-base font-semibold text-gray-800 dark:text-white":"text-sm font-medium text-gray-600 dark:text-gray-300"}`}>{value.toLocaleString()}</p></div>;
}

/** 日历复用 useInboxMonitor 的载荷，不启动第二个请求或轮询。 */
export default function StatsCalendarPanel({controller}:{controller:InboxController}){
 const {data,loaded}=controller;
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
     {days.map(day=><section key={day.date} aria-label={`${day.date}${day.isToday?' 今天':''}`}
      className={`min-h-48 rounded-xl border p-3 ${day.isToday?"border-brand-300 bg-brand-50 ring-1 ring-brand-200 dark:border-brand-700 dark:bg-brand-500/10 dark:ring-brand-900":"border-gray-200 bg-white dark:border-gray-800 dark:bg-white/[0.02]"}`}>
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
     </section>)}
    </div>
   </div>

   <Notice>统计按<strong>北京时间</strong>分天；未确认发送不算成功；回复与加橱窗均排除历史补录。当前未结人工事项为 <strong>{data.openCases.toLocaleString()}</strong> 件，它是现在的状态，不拆到某一天。</Notice>
  </div>
 </Card>;
}
