"use client";

import {useMemo,useState} from "react";
import {Card,Collapsible,Icon,Notice,PageHeading,Pill,Tabs} from "../bdhub/ui";
import {BUSINESS_PHASES,DEMO_COUNTS,FLOW_STAGES,LOCKS,PID_REFRESH_CLOCKS,PID_SNAPSHOT,REFRESH_RULES,SCENARIOS,SIMPLE_POOL,TAPLINK_PERFORMANCE,TAPLINK_VALIDITY_CHECKS,poolReconciles,simpleScenario,type ScenarioKey} from "./pid-send-pool-demo";

type Tab="pid"|"people"|"details";
type PhaseKey=(typeof BUSINESS_PHASES)[number]["key"];

const format=(value:number)=>value.toLocaleString("zh-CN");

function Connector(){return <div className="flex h-8 items-center justify-center" aria-hidden="true"><span className="h-6 w-px bg-gray-300 dark:bg-gray-700"/><Icon name="down" className="-ml-2 mt-5 size-4 text-gray-400"/></div>}

function TreeOutcome({title,detail,tone}:{title:string;detail:string;tone:"success"|"warning"|"neutral"}){
 const style=tone==="success"?"border-success-200 bg-success-50/60 dark:border-success-900 dark:bg-success-900/5":tone==="warning"?"border-warning-200 bg-warning-50/60 dark:border-warning-900 dark:bg-warning-900/5":"border-gray-200 bg-gray-50 dark:border-gray-800 dark:bg-gray-800/60";
 return <div className={`rounded-xl border p-4 ${style}`}><p className="font-semibold text-gray-800 dark:text-gray-200">{title}</p><p className="mt-1 text-xs leading-5 text-gray-500">{detail}</p></div>;
}

function PidTree(){
 const s=PID_SNAPSHOT;
 return <div className="space-y-5">
  <Notice><strong>当前只看 PID。</strong> 达人、冷却和发送先放到下一层；这棵树只回答：PID 从哪里来、何时合格、如何进入平台池、如何得到当前链接，以及刷新或清理后去哪。</Notice>
  <Card><div className="grid grid-cols-3 divide-x divide-gray-100 p-4 dark:divide-gray-800">{[
   ["全托当前合格",s.fullManaged.currentEligible,`${format(s.fullManaged.collected)} 中通过`],
   ["Campaign 入池候选",s.campaign.chosen,`${format(s.campaign.uniquePids)} 中选出`],
   ["已核验新建链接",s.linkIntents.verified,`快照 ${s.observedAt}`],
  ].map(([label,value,hint])=><div key={String(label)} className="min-w-0 px-2 text-center sm:px-4"><p className="truncate text-[11px] text-gray-500 sm:text-xs">{label}</p><p className="mt-1 text-xl font-semibold tabular-nums sm:text-2xl">{format(Number(value))}</p><p className="mt-1 hidden text-xs text-gray-400 sm:block">{hint}</p></div>)}</div></Card>
  <Card title="一个 PID 的生命周期树" subtitle="只有绿色结果才叫 PID Material Ready；其他情况只分为等待处理或当前不可用"><div className="p-5">
   <div className="mx-auto max-w-5xl">
    <div className="mx-auto max-w-sm rounded-2xl bg-brand-500 p-5 text-center text-white"><p className="text-xs font-medium text-brand-100">根元素</p><p className="mt-1 text-xl font-semibold">一个 PID</p><p className="mt-1 text-xs text-brand-100">所有后续状态都必须能回到这个商品</p></div>
    <Connector/>
    <div className="grid gap-4 md:grid-cols-2">
     <div className="rounded-2xl border border-gray-200 p-5 dark:border-gray-800"><div className="flex items-center justify-between"><h3 className="font-semibold">来源 A · 全托</h3><Pill tone="brand">{format(s.fullManaged.collected)} PID</Pill></div><p className="mt-2 text-sm text-gray-500">高机会商品 → 仅全球销售商品</p><p className="mt-3 text-xs leading-5 text-gray-400">当前合格 {format(s.fullManaged.currentEligible)}；当前不合格 {format(s.fullManaged.currentRejected)}。</p></div>
     <div className="rounded-2xl border border-gray-200 p-5 dark:border-gray-800"><div className="flex items-center justify-between"><h3 className="font-semibold">来源 B · Campaign</h3><Pill tone="warning">{format(s.campaign.uniquePids)} PID</Pill></div><p className="mt-2 text-sm text-gray-500">已加入/可加入活动的商品方案</p><p className="mt-3 text-xs leading-5 text-gray-400">当前选中 {format(s.campaign.chosen)}；不合格或未选 {format(s.campaign.held)}。</p></div>
    </div>
    <Connector/>
    <div className="rounded-2xl border-2 border-brand-200 bg-brand-25 p-5 text-center dark:border-brand-900 dark:bg-brand-500/5"><p className="text-xs font-semibold text-brand-500">问题 1</p><h3 className="mt-1 text-lg font-semibold">这个 PID 现在符合来源规则吗？</h3><p className="mt-2 text-sm text-gray-500">全托看销量/评分/佣金；Campaign 看 ACTIVE/期限/库存/佣金。</p></div>
    <div className="mt-4 grid gap-4 md:grid-cols-2"><TreeOutcome title="否 → 当前不可用" detail="不删除来源、选入、线索或旧卡；以后刷新重新判断。" tone="neutral"/><TreeOutcome title="是 → 继续" detail="冻结当前来源、活动和佣金方案，进入平台池判断。" tone="success"/></div>
    <Connector/>
    <div className="rounded-2xl border-2 border-brand-200 bg-brand-25 p-5 text-center dark:border-brand-900 dark:bg-brand-500/5"><p className="text-xs font-semibold text-brand-500">问题 2</p><h3 className="mt-1 text-lg font-semibold">它已经在对应的平台商品池里吗？</h3></div>
    <div className="mt-4 grid gap-4 md:grid-cols-2"><TreeOutcome title="全托：已选池" detail={`当前台账 ${format(s.fullManaged.selectedPool)} PID；未选则创建选入意图，回查 confirmed/already_selected。`} tone="success"/><TreeOutcome title="Campaign：已加入活动" detail={`当前候选 ${format(s.campaign.chosen)} PID；新加入台账 ${format(s.campaign.newlyJoined)} 条，额外条款转人工。`} tone="success"/></div>
    <Connector/>
    <div className="rounded-2xl border-2 border-brand-200 bg-brand-25 p-5 text-center dark:border-brand-900 dark:bg-brand-500/5"><p className="text-xs font-semibold text-brand-500">问题 3</p><h3 className="mt-1 text-lg font-semibold">最后成功快照中有可用 TapLink 吗？</h3><p className="mt-2 text-sm text-gray-500">刷新时匹配 PID、来源、Campaign、达人佣金和 listId；刷新之间沿用结果。</p></div>
    <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
     <TreeOutcome title="有且一致" detail="复用当前卡，进入 Material Ready。" tone="success"/>
     <TreeOutcome title="完全没有" detail="创建唯一建链意图；回执未知只核验原意图。" tone="warning"/>
     <TreeOutcome title="旧卡佣金不同" detail="旧卡保留，创建当前佣金新卡，核验后切新 listId。" tone="warning"/>
     <TreeOutcome title="状态未知" detail="不创建替代卡，先复读列表与成员。" tone="warning"/>
    </div>
    <Connector/>
    <div className="mx-auto max-w-lg rounded-2xl border-2 border-success-300 bg-success-50 p-5 text-center dark:border-success-900 dark:bg-success-900/10"><Pill tone="success">唯一前向结果</Pill><h3 className="mt-3 text-xl font-semibold">PID Material Ready</h3><p className="mt-2 text-sm text-gray-500">当前合格 Offer + 已在平台池 + 最后成功快照确认链接可用。只有这里的 PID 才能去查达人线索。</p></div>
   </div>
  </div></Card>
  <Card title="TapLink 只保留两个刷新周期" subtitle="活跃链接 48 小时，全部库存每周；新建后只回读一次"><div className="p-5">
   <div className="grid gap-4 lg:grid-cols-3">{PID_REFRESH_CLOCKS.map(clock=><div key={clock.key} className="rounded-xl border border-gray-200 p-4 dark:border-gray-800"><div className="flex items-center justify-between gap-3"><p className="font-semibold text-gray-800 dark:text-gray-200">{clock.title}</p><Pill tone={clock.key==="active"?"brand":clock.key==="create"?"success":"neutral"}>{clock.cadence}</Pill></div><ul className="mt-3 space-y-2">{clock.items.map(item=><li key={item} className="flex gap-2 text-sm leading-6 text-gray-500"><span aria-hidden="true">•</span><span>{item}</span></li>)}</ul></div>)}</div>
   <Notice tone="success"><strong>允许短期误差：</strong>达人查询、组批和发送都直接使用最后一次成功快照，不再发送前复读。快照逾期只提示，不阻塞流程。</Notice>
   <Notice tone="warning"><strong>当前运行事实：</strong>定时调度器尚未实现，现有作业开关全部关闭；48 小时和每周是已经确认的目标周期，不代表后台已经在跑。</Notice>
  </div></Card>
  <Card title="TapLink 清理是旁路，不参与 PID 前向资格" subtitle={`当前只读库存 ${format(s.inventory.lists)} 张列表；历史清理快照扫描 ${format(s.historicalCleanup.scanned)} 张`}><div className="p-5">
   <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
    <TreeOutcome title={`有效 ${format(s.historicalCleanup.valid)}`} detail="继续保留，不产生删除动作。" tone="success"/>
    <TreeOutcome title="混合有效" detail="只要列表中仍有有效商品，整张列表保留。" tone="neutral"/>
    <TreeOutcome title="状态未知" detail="转人工/复读，不允许猜测删除。" tone="warning"/>
    <TreeOutcome title={`历史无效 ${format(s.historicalCleanup.invalid)}`} detail="只进入独立清理范围；冻结删除意图并回读。" tone="warning"/>
   </div>
   <Notice tone="warning"><strong>当前 A 规则：</strong>PID 主流程不会自动删除任何旧卡。清理必须是独立动作，有明确范围、健康证据、删除意图和删除后回读；历史台账中已有 {format(s.historicalCleanup.verifiedDeletes)} 条已核验删除记录，不代表今后主流程会自动删除。</Notice>
  </div></Card>
  <Collapsible label="当前建链台账快照"><div className="grid gap-4 lg:grid-cols-2">
   <Card title="全托 · Selected"><div className="grid grid-cols-2 gap-3 p-5 sm:grid-cols-5">{[["已核验",s.fullManaged.links.ready],["可复用",s.fullManaged.links.reuse],["复读中",s.fullManaged.links.reading],["缺链",s.fullManaged.links.missing],["待判断",s.fullManaged.links.review]].map(([label,value])=><div key={String(label)}><p className="text-xs text-gray-400">{label}</p><p className="mt-1 text-lg font-semibold tabular-nums">{format(Number(value))}</p></div>)}</div></Card>
   <Card title="非全托 · Campaign"><div className="grid grid-cols-2 gap-3 p-5 sm:grid-cols-5">{[["已核验",s.campaign.linkRows.ready],["可复用PID",s.campaign.linkRows.reusePids],["缺链",s.campaign.linkRows.missing],["读取不完整",s.campaign.linkRows.readIncomplete],["待判断",s.campaign.linkRows.review]].map(([label,value])=><div key={String(label)}><p className="text-xs text-gray-400">{label}</p><p className="mt-1 text-lg font-semibold tabular-nums">{format(Number(value))}</p></div>)}</div></Card>
  </div></Collapsible>
 </div>;
}

const blockers:Record<PhaseKey,string[]>={
 product:["商品不符合当前来源规则","没有完整、同源的 Offer","缺 TapLink 或卡上佣金已变化"],
 creator:["PID 尚未查询达人","handle 明确搜索不到","OECID 尚未解析"],
 send:["达人仍在冷却","存在未解决回复或人工接管","最后快照中商品或链接不可用"],
};

function PhaseCard({phase,active,onClick}:{phase:(typeof BUSINESS_PHASES)[number];active:boolean;onClick:()=>void}){
 return <button type="button" onClick={onClick} aria-pressed={active} className={`relative rounded-2xl border p-5 text-left transition ${active?"border-brand-400 bg-brand-50 shadow-theme-xs dark:border-brand-700 dark:bg-brand-500/10":"border-gray-200 bg-white hover:border-brand-200 dark:border-gray-800 dark:bg-white/[0.025]"}`}>
  <div className="flex items-center justify-between"><span className="text-xs font-semibold tracking-[0.16em] text-brand-500">{phase.index}</span><Pill tone={active?"brand":"neutral"}>{phase.unit}</Pill></div>
  <h3 className="mt-4 text-lg font-semibold text-gray-800 dark:text-white/90">{phase.title}</h3>
  <p className="mt-2 text-sm font-medium text-gray-600 dark:text-gray-300">{phase.question}</p>
  <p className="mt-2 text-xs leading-5 text-gray-500">{phase.summary}</p>
 </button>;
}

function ResultCard({title,value,description,tone}:{title:string;value:number;description:string;tone:"success"|"warning"|"neutral"}){
 const styles={success:"border-success-200 bg-success-50/60 dark:border-success-900 dark:bg-success-900/5",warning:"border-warning-200 bg-warning-50/60 dark:border-warning-900 dark:bg-warning-900/5",neutral:"border-gray-200 bg-gray-50 dark:border-gray-800 dark:bg-gray-800/60"};
 return <div className={`rounded-2xl border p-5 ${styles[tone]}`}><div className="flex items-center justify-between"><h3 className="font-semibold text-gray-800 dark:text-gray-200">{title}</h3><span className="text-2xl font-semibold tabular-nums">{format(value)}</span></div><p className="mt-2 text-xs leading-5 text-gray-500">{description}</p></div>;
}

function Overview(){
 const [phaseKey,setPhaseKey]=useState<PhaseKey>("product");
 const [scenarioKey,setScenarioKey]=useState<ScenarioKey>("clean");
 const phase=BUSINESS_PHASES.find(item=>item.key===phaseKey)??BUSINESS_PHASES[0];
 const scenario=SCENARIOS.find(item=>item.key===scenarioKey)??SCENARIOS[0];
 const result=useMemo(()=>simpleScenario(scenario),[scenario]);
 const checks=[
  {label:"商品准备",ok:result.product,detail:result.product?"合格商品 + 精确 Offer + 最后成功链接快照":"停在商品准备"},
  {label:"达人准备",ok:result.creator,detail:result.creator?"稳定 OECID 已确认":"等待身份判定"},
  {label:"发送安排",ok:result.send,detail:result.send?"可以进入严格发送池":"不进入 Ready"},
 ];
 const scenarioNote=scenario.key==="rate_changed"
  ?`旧卡创建时达人佣金是 ${scenario.cardPercent}%，当前 Offer 按新规则变成 ${scenario.creatorPercent}%。旧卡没有出错，只是不再代表当前方案。`
  :scenario.key==="product_invalid"
   ?`加入 Campaign 时剩余 ${scenario.joinedCampaignDays} 天；今天刷新只剩 ${scenario.campaignDays} 天，已低于 45 天门槛。这个 PID 的未发送位置退出池子。`
   :scenario.key==="reply_open"
    ?"冻结的是这个达人名下的全部 PID；同一个 PID 对其他达人不受影响。"
    :scenario.description;
 return <div className="space-y-5">
  <Card title="只需要理解三个阶段" subtitle="每个阶段只回答一个业务问题；内部步骤和技术锁默认隐藏"><div className="grid gap-4 p-5 lg:grid-cols-3">{BUSINESS_PHASES.map(item=><PhaseCard key={item.key} phase={item} active={phaseKey===item.key} onClick={()=>setPhaseKey(item.key)}/>)}</div></Card>
  <div className="grid gap-5 xl:grid-cols-[1.1fr_.9fr]">
   <Card title={`${phase.index} · ${phase.title}`} subtitle={phase.question}><div className="grid gap-5 p-5 md:grid-cols-2">
    <div><p className="text-xs font-medium text-gray-400">系统在做什么</p><ol className="mt-3 space-y-3">{phase.steps.map((step,index)=><li key={step} className="flex gap-3 text-sm text-gray-600 dark:text-gray-300"><span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-brand-50 text-xs font-semibold text-brand-600 dark:bg-brand-500/10">{index+1}</span><span className="pt-0.5">{step}</span></li>)}</ol></div>
    <div><p className="text-xs font-medium text-gray-400">什么情况下停在这里</p><ul className="mt-3 space-y-3">{blockers[phase.key].map(reason=><li key={reason} className="flex gap-3 text-sm text-gray-600 dark:text-gray-300"><Icon name="lock" className="mt-0.5 size-4 shrink-0 text-warning-500"/><span>{reason}</span></li>)}</ul></div>
   </div></Card>
   <Card title="最终只看三种结果" subtitle="技术原因是解释，不再单独膨胀成业务状态"><div className="space-y-3 p-5">
    <ResultCard title="可以发" value={SIMPLE_POOL.sendable} description="三个阶段全部完成，当前每位达人只保留一个最优位置。" tone="success"/>
    <ResultCard title="等待中" value={SIMPLE_POOL.waiting} description="等链接、身份、冷却、回复处理或轮到该商品；数据不会丢。" tone="warning"/>
    <ResultCard title="暂不可用" value={SIMPLE_POOL.unavailable} description="当前商品失效或不合格；事实保留，恢复后重新计算。" tone="neutral"/>
    <p className="pt-1 text-xs leading-5 text-gray-400">{format(SIMPLE_POOL.sendable)} + {format(SIMPLE_POOL.waiting)} + {format(SIMPLE_POOL.unavailable)} = {format(SIMPLE_POOL.total)} 个达人×PID 位置</p>
   </div></Card>
  </div>
  <Card title="拿一个 PID 看结果" subtitle="不展示六七个状态，只看它能否连续通过三个阶段"><div className="p-5">
   <div className="flex gap-2 overflow-x-auto pb-2">{SCENARIOS.map(item=><button key={item.key} type="button" aria-pressed={scenarioKey===item.key} onClick={()=>setScenarioKey(item.key)} className={`shrink-0 rounded-lg border px-3 py-2 text-sm font-medium transition ${scenarioKey===item.key?"border-brand-400 bg-brand-50 text-brand-600 dark:border-brand-700 dark:bg-brand-500/10 dark:text-brand-300":"border-gray-200 text-gray-500 hover:border-brand-200 dark:border-gray-800"}`}>{item.label}</button>)}</div>
   <div className="mt-4 grid gap-3 lg:grid-cols-3">{checks.map((check,index)=><div key={check.label} className={`rounded-xl border p-4 ${check.ok?"border-success-200 bg-success-50/50 dark:border-success-900 dark:bg-success-900/5":"border-warning-200 bg-warning-50/50 dark:border-warning-900 dark:bg-warning-900/5"}`}><div className="flex items-center justify-between"><p className="font-semibold"><span className="mr-2 text-xs text-gray-400">{index+1}</span>{check.label}</p><Pill tone={check.ok?"success":"warning"}>{check.ok?"通过":"等待"}</Pill></div><p className="mt-2 text-xs leading-5 text-gray-500">{check.detail}</p></div>)}</div>
   <Notice tone={result.send?"success":result.layer.includes("失效")?"warning":"info"}><strong>{result.layer}</strong><span className="ml-2">{result.summary}</span><p className="mt-1">{scenarioNote}</p></Notice>
  </div></Card>
  <Collapsible label="一个具体排序例子" defaultOpen><div className="space-y-4">
   <div className="grid gap-3 md:grid-cols-5">{[
    ["1","先清资格","只保留商品、链接、身份都就绪的位置"],["2","按达人分组","同一达人保留多个 PID 机会"],["3","冻结关系","未结回复只冻结这个达人"],["4","排达人顺序","取该达人全部有效位置中最小 rank"],["5","选本次商品","佣金最高 → rank → 全托 → PID"],
   ].map(([number,title,text])=><div key={number} className="rounded-xl bg-gray-50 p-4 dark:bg-gray-800"><p className="text-xs font-semibold text-brand-500">{number}</p><p className="mt-2 text-sm font-semibold">{title}</p><p className="mt-1 text-xs leading-5 text-gray-500">{text}</p></div>)}</div>
   <div className="overflow-x-auto rounded-xl border border-gray-200 dark:border-gray-800"><table className="w-full text-left text-sm"><thead className="bg-gray-50 text-xs text-gray-500 dark:bg-gray-800/60"><tr>{["达人","当前有效位置","达人优先级","本次选择","结果"].map(label=><th key={label} className="whitespace-nowrap px-4 py-3 font-medium">{label}</th>)}</tr></thead><tbody>{[
    ["@anna","全托 A：rank1 / 13%；Campaign B：rank4 / 17%","1（最佳 rank=1）","Campaign B · 17%","第 1 位，发 B"],
    ["@bruno","全托 A：rank2 / 15%","2（最佳 rank=2）","全托 A · 15%","第 2 位，发 A"],
    ["@carla","全托 C：rank1 / 18%；全托 D：rank3 / 16%","—","—","有未结回复，仅冻结 Carla"],
   ].map(row=><tr key={row[0]} className="border-t border-gray-100 align-top dark:border-gray-800">{row.map((cell,index)=><td key={index} className={`min-w-32 px-4 py-3 ${index===4?"font-medium text-brand-600 dark:text-brand-300":"text-gray-600 dark:text-gray-300"}`}>{cell}</td>)}</tr>)}</tbody></table></div>
   <Notice><strong>关键点：</strong>@anna 虽然用 rank1 决定她排第 1，但本次商品选佣金更高的 Campaign B；@carla 的冻结不会影响 @bruno 继续使用 PID-A，也不会冻结 PID-C 对其他达人使用。</Notice>
  </div></Collapsible>
 </div>;
}

function Details(){return <div className="space-y-4">
 <Notice>以下内容用于解释系统为什么作出判断，不是运营每天需要处理的状态。</Notice>
 <Collapsible label="商品筛选规则" defaultOpen><div className="grid gap-4 lg:grid-cols-2">
  <Card title="全托商品"><div className="space-y-2 p-5 text-sm leading-6 text-gray-600 dark:text-gray-300">{["累计销量 ≥ 300（包含 300）","有评分时 ≥ 4.0；无评分允许","总佣金 − 公开佣金 ≥ 2 个百分点","库存不作为门槛","明确下架、治理或失效仍然拦截"].map(item=><p key={item} className="flex gap-2"><Icon name="check" className="mt-1 size-4 shrink-0 text-success-500"/>{item}</p>)}</div></Card>
  <Card title="Campaign 商品"><div className="space-y-2 p-5 text-sm leading-6 text-gray-600 dark:text-gray-300">{["活动必须 ACTIVE","剩余期限 > 45 天","普通商品库存 > 100","无额外条款才自动加入","达人佣金高于公开佣金"].map(item=><p key={item} className="flex gap-2"><Icon name="check" className="mt-1 size-4 shrink-0 text-success-500"/>{item}</p>)}</div></Card>
 </div></Collapsible>
 <Collapsible label="刷新周期" defaultOpen><div className="space-y-4">
  <Notice tone="warning"><strong>先区分规则与运行：</strong>48 小时和每周是目标规则；当前定时调度器未实现，所有定时开关均为关闭。</Notice>
  <div className="overflow-x-auto rounded-xl border border-gray-200 dark:border-gray-800"><table className="w-full text-left text-sm"><thead className="bg-gray-50 text-xs text-gray-500 dark:bg-gray-800/60"><tr>{["对象","什么时候刷新","如何触发","影响"].map(label=><th key={label} className="whitespace-nowrap px-4 py-3 font-medium">{label}</th>)}</tr></thead><tbody>{REFRESH_RULES.map(row=><tr key={row.object} className="border-t border-gray-100 align-top dark:border-gray-800"><td className="whitespace-nowrap px-4 py-3 font-medium">{row.object}</td><td className="min-w-40 px-4 py-3 text-gray-500">{row.cycle}</td><td className="min-w-48 px-4 py-3 text-gray-500">{row.mode} · {row.optional}</td><td className="min-w-64 px-4 py-3 text-gray-500">{row.effect}</td></tr>)}</tbody></table></div>
 </div></Collapsible>
 <Collapsible label="TapLink 核验效率" defaultOpen><div className="space-y-4">
  <div className="grid gap-4 lg:grid-cols-2">{TAPLINK_PERFORMANCE.map(item=><Card key={item.label} title={item.label} action={<Pill tone="brand">{item.value}</Pill>}><p className="p-5 text-sm leading-6 text-gray-500">{item.detail}</p></Card>)}</div>
  <Notice tone="success"><strong>效率结论：</strong>不做发送前核验。活跃链接集中每 48 小时刷新，全库存每周扫一次；其他时间直接使用最后成功快照。</Notice>
 </div></Collapsible>
 <Collapsible label="刷新时核对哪些 TapLink 事实"><div className="space-y-4">
  <Notice><strong>Material Ready = 当前 Offer 合格 + 最后成功快照确认 TapLink 可用。</strong>刷新窗口内允许平台事实和本地快照存在短期误差。</Notice>
  <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">{TAPLINK_VALIDITY_CHECKS.map((check,index)=><div key={check.label} className="rounded-xl bg-gray-50 p-4 dark:bg-gray-800"><p className="text-xs font-semibold text-brand-500">{index+1} · {check.label}</p><p className="mt-2 text-sm leading-6 text-gray-500">{check.detail}</p></div>)}</div>
  <div className="grid gap-4 lg:grid-cols-3">{[
   ["新建后","立即一次","平台读回创建结果和 listId；之后不围绕本次创建反复核验。"],
   ["正常运行","以上次为准","达人查询、组批和发送直接使用最后成功快照，不增加前置等待。"],
   ["发送明确拒卡","只影响该 PID","转为等待下一次或人工刷新，其他成员继续；不冻结整个批次。"],
  ].map(([title,tag,text])=><Card key={title} title={title} action={<Pill tone="neutral">{tag}</Pill>}><p className="p-5 text-sm leading-6 text-gray-500">{text}</p></Card>)}</div>
 </div></Collapsible>
 <Collapsible label="技术锁与恢复保障"><div className="grid gap-3 md:grid-cols-2">{LOCKS.map(lock=><div key={lock.name} className="rounded-xl border border-gray-200 p-4 dark:border-gray-800"><div className="flex items-center gap-2"><Icon name="lock" className="size-4 text-brand-500"/><p className="font-semibold text-gray-800 dark:text-gray-200">{lock.name}</p></div><p className="mt-2 text-xs text-brand-600 dark:text-brand-300">{lock.scope} · {lock.when}</p><p className="mt-2 text-sm leading-6 text-gray-500">{lock.protects}</p></div>)}</div></Collapsible>
 <Collapsible label="内部完整步骤（开发视角）"><div className="grid gap-3 md:grid-cols-2">{FLOW_STAGES.map(stage=><div key={stage.key} className="rounded-xl bg-gray-50 p-4 dark:bg-gray-800"><div className="flex items-center gap-2"><span className="text-xs font-semibold text-brand-500">{stage.index}</span><p className="font-semibold">{stage.title}</p></div><p className="mt-2 text-xs leading-5 text-gray-500">{stage.rule}</p></div>)}</div></Collapsible>
 </div>}

export default function PidSendPoolDemoPage(){
 const [tab,setTab]=useState<Tab>("pid");
 return <div className="space-y-5">
  <PageHeading title="PID → 发送池业务沙盘" description="先把 PID 来源、选入、建链、刷新、核验与清理讲清楚，再进入达人和发送。" action={<div className="flex items-center gap-2"><Pill tone="brand">PID 优先</Pill><Pill tone="neutral">不连接后端</Pill></div>}/>
  <Notice tone="warning"><strong>PID 页使用 2026-09-19 只读台账快照；达人示例仍为虚构。</strong> 页面运行时不会读取 API、SQLite、TikTok、Kalodata 或模型，也不会执行任何业务动作。</Notice>
  <Tabs items={[{value:"pid",label:"PID 生命周期树"},{value:"people",label:"达人和发送"},{value:"details",label:"规则明细"}]} value={tab} onChange={setTab}/>
  {tab==="pid"?<PidTree/>:tab==="people"?<Overview/>:<Details/>}
  <p className="text-xs text-gray-400">PID 主链只有三个业务结果：Material Ready、等待处理、当前不可用。达人/发送恒等式：{poolReconciles()?"已通过":"未通过"}。</p>
 </div>;
}
