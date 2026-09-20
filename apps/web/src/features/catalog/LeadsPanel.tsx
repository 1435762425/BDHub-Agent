"use client";
import {MetricTable,Notice,Section} from "../bdhub/ui";
import IdentityPanel from "./IdentityPanel";
import LeadPool from "./LeadPool";
import LeadsQueuePanel from "./LeadsQueuePanel";
import {useIdentityQueue} from "./useIdentityQueue";
import {useLeadPool} from "./useLeadPool";
import {useLeadsQueue} from "./useLeadsQueue";

/**
 * 达人线索：与「全托商品」「非全托商品」并列的独立一步。
 *
 * 为什么单独一页：这一步**不属于任何一条渠道**——位置＝达人×商品，渠道只挂在商品与链接上；
 * 同一个达人在两条渠道下共用冷却、500 额度与拒联记录。放在商品页里会让人以为它是那条渠道的一部分，
 * 也会让商品页承担两种完全不同的读法。
 *
 * 顺序就是这一步自己的顺序：查线索 → 补身份（无 OECID 不进池）→ 发送池。
 */
export default function LeadsPanel(){
 const queue=useLeadsQueue();
 const identity=useIdentityQueue();
 const leadPool=useLeadPool();
 const byChannel=queue.data?.byChannel;
 const scopeValue=queue.data?.scope??null;
 return <div className="space-y-5">
  <Notice tone="info">
    这一步<b>不分渠道</b>：位置＝达人×商品，渠道只挂在商品与链接上；同一个达人在两条渠道下共用冷却、
    500 额度与拒联记录。所以下面看到的是整个机构的状态。
  </Notice>
  {scopeValue!=null&&<MetricTable rows={[
   {label:"队列商品（全托＋非全托）",value:scopeValue.toLocaleString(),detail:byChannel?`全托 ${byChannel.selected.toLocaleString()} · 非全托 ${byChannel.campaign.toLocaleString()}`:"两条渠道并集"},
   {label:"首次待查",value:(queue.data?.firstTime??0).toLocaleString(),detail:"从没问过平台，按累计销量降序",accent:true},
   {label:"未到期",value:(queue.data?.waiting??0).toLocaleString(),detail:"7 天内查过，到期后自动回到队列"},
   {label:"连续失败已移出",value:(queue.data?.stuck??0).toLocaleString(),detail:"保留记录，不静默丢弃"},
  ]}/>}
  {(queue.data?.unitsUnknown??0)>0&&<Notice tone="warning">
   有 {queue.data?.unitsUnknown} 个非全托商品没有销量数据，排序时按 0 处理（不会假装有销量）。
  </Notice>}

  <Section id="stage-leads" index="①" title="达人线索查询队列"
   summary={<>待查 {queue.data?.firstTime?.toLocaleString()??"—"} · 未到期 {queue.data?.waiting?.toLocaleString()??"—"}</>}>
   <div id="card-leads" className="scroll-mt-6"><LeadsQueuePanel controller={queue}/></div>
  </Section>

  {/* 这一段的单位就是**达人**（去重 handle），跟卡片里的数字同口径：线索数放在卡片里当解释，
      不要在这一行再写一遍线索口径的"已就位/待补"，否则两个数并排会互相打架。 */}
  <Section id="stage-identity" index="②" title="达人身份（OECID）"
   summary={identity.data?.byCreator
    ?<>已就位 {identity.data.byCreator.resolved.toLocaleString()} · 待补充身份 {(identity.data.byCreator.blocked+identity.data.byCreator.unknown).toLocaleString()} · 搜索不到 {identity.data.byCreator.unresolved.toLocaleString()}</>
    :<>已就位 {identity.data?.resolvedCreators?.toLocaleString()??"—"} · 搜索不到 {identity.data?.unresolvedCreators?.toLocaleString()??"—"}</>}>
   <div id="card-identity" className="scroll-mt-6"><IdentityPanel controller={identity}/></div>
  </Section>

  <Section id="stage-pool" index="③" title="发送池"
   summary={leadPool.data?.available?<>线索 {leadPool.data.counts.positions.toLocaleString()} · 可发 {leadPool.data.counts.ready.toLocaleString()}</>:"读取中…"}>
   <LeadPool data={leadPool.data} loaded={leadPool.loaded}/>
  </Section>
 </div>;
}
