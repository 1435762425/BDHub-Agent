import type {InboxDay,InboxState,InboxTotals} from "../../server/inbox/bridge";

const KEYS=(['cards','texts','creators','unconfirmed','replies','showcase','ourMessages','autoReplies','casesOpened'] as const);

export type StatsCalendarDay=InboxDay&{
 isToday:boolean;
 dateLabel:string;
 weekdayLabel:string;
};

export type StatsCalendarModel={
 days:StatsCalendarDay[];
 totals:InboxTotals;
 todayDate:string|null;
};

/** 只汇总 cycle_stats.py 返回的字段，不让展示层产生第二份统计口径。 */
export function sumStatsDays(days:InboxDay[]):InboxTotals{
 const totals:InboxTotals={cards:0,texts:0,creators:0,unconfirmed:0,replies:0,showcase:0,ourMessages:0,autoReplies:0,casesOpened:0};
 for(const row of days)for(const key of KEYS)totals[key]+=row[key];
 return totals;
}

function sameCounts(left:InboxDay|InboxTotals,right:InboxDay|InboxTotals){
 return KEYS.every(key=>left[key]===right[key]);
}

function labels(date:string){
 const [,month,day]=date.split('-').map(Number);
 const stamp=new Date(`${date}T00:00:00+08:00`);
 return {
  dateLabel:`${month}月${day}日`,
  weekdayLabel:new Intl.DateTimeFormat('zh-CN',{weekday:'short',timeZone:'Asia/Shanghai'}).format(stamp),
 };
}

/**
 * 生成展示模型，并复核日历依赖的两个恒等式：期间总数等于逐日求和，today 等于同日期行。
 */
export function buildStatsCalendar(data:Pick<InboxState,'days'|'totals'|'today'>):StatsCalendarModel{
 const computed=sumStatsDays(data.days);
 if(!sameCounts(computed,data.totals))throw Error('stats_totals_mismatch');
 const todayDate=data.today?.date??null;
 if(data.today){
  const row=data.days.find(item=>item.date===data.today?.date);
  if(!row||!sameCounts(row,data.today))throw Error('stats_today_mismatch');
 }
 return {totals:data.totals,todayDate,days:data.days.map(row=>({...row,...labels(row.date),isToday:row.date===todayDate}))};
}
