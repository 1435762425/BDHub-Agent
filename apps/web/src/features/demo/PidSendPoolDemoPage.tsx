"use client";

import {useMemo,useState} from "react";
import {Button,Card,Icon,Notice,PageHeading,Pill,Progress,Section,StatTile,Tabs} from "../bdhub/ui";
import {DEMO_COUNTS,FLOW_STAGES,LOCKS,REFRESH_RULES,SCENARIOS,evaluateScenario,poolReconciles,type ScenarioKey} from "./pid-send-pool-demo";

type Tab="flow"|"rules"|"locks"|"simulator";

const format=(value:number)=>value.toLocaleString("zh-CN");
const gateTone={pass:"success",wait:"warning",stop:"error"} as const;
const gateText={pass:"通过",wait:"等待",stop:"停止"};

function Metric({label,value,hint,accent=false}:{label:string;value:number;hint:string;accent?:boolean}){
 return <StatTile label={label} value={value} hint={hint} brand={accent}/>;
}

function StageFlow({active,onSelect}:{active:string;onSelect:(key:string)=>void}){
 return <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">{FLOW_STAGES.map((stage,index)=><button key={stage.key} type="button" onClick={()=>onSelect(stage.key)} aria-pressed={active===stage.key} className={`group relative rounded-2xl border p-4 text-left transition ${active===stage.key?"border-brand-400 bg-brand-50 shadow-theme-xs dark:border-brand-700 dark:bg-brand-500/10":"border-gray-200 bg-white hover:border-brand-200 hover:bg-brand-25 dark:border-gray-800 dark:bg-white/[0.025] dark:hover:border-brand-900"}`}>
  <div className="flex items-center justify-between"><span className="text-xs font-semibold tracking-[0.16em] text-brand-500">{stage.index}</span><Pill tone={active===stage.key?"brand":"neutral"}>{stage.unit}</Pill></div>
  <h3 className="mt-3 text-base font-semibold text-gray-800 dark:text-white/90">{stage.title}</h3><p className="mt-2 text-xs leading-5 text-gray-500">{stage.output}</p>
  {index<FLOW_STAGES.length-1&&<span className="absolute -right-2 top-1/2 z-10 hidden size-4 items-center justify-center rounded-full bg-brand-500 text-white xl:flex"><Icon name="arrow" className="size-3"/></span>}
 </button>)}</div>;
}

function FlowTab(){
 const [active,setActive]=useState("link");
 const stage=FLOW_STAGES.find(item=>item.key===active)??FLOW_STAGES[0];
 return <div className="space-y-5">
  <StageFlow active={active} onSelect={setActive}/>
  <div className="grid gap-5 xl:grid-cols-[1.25fr_.75fr]">
   <Card title={`${stage.index} · ${stage.title}`} subtitle={`输入：${stage.input}　→　输出：${stage.output}`}><div className="grid gap-4 p-5 md:grid-cols-2">
    {[{title:"进入条件",text:stage.rule,icon:"check" as const},{title:"失败去向",text:stage.failure,icon:"lock" as const},{title:"刷新方式",text:stage.refresh,icon:"time" as const},{title:"计数单位",text:stage.unit,icon:"chart" as const}].map(item=><div key={item.title} className="rounded-xl bg-gray-50 p-4 dark:bg-gray-800"><p className="flex items-center gap-2 text-sm font-semibold text-gray-700 dark:text-gray-200"><Icon name={item.icon} className="size-4 text-brand-500"/>{item.title}</p><p className="mt-2 text-sm leading-6 text-gray-500">{item.text}</p></div>)}
   </div></Card>
   <Card title="数量必须对得平" subtitle="演示数据 · 每一条位置只能属于一个池层"><div className="space-y-4 p-5">
    <div className="rounded-xl bg-brand-50 p-4 dark:bg-brand-500/10"><p className="text-xs text-gray-500">达人×PID 总位置</p><p className="mt-1 text-3xl font-semibold tabular-nums text-brand-600 dark:text-brand-300">{format(DEMO_COUNTS.positions)}</p></div>
    <p className="text-sm leading-6 text-gray-500">Ready {format(DEMO_COUNTS.pool.ready)} ＋ 等待 {format(DEMO_COUNTS.pool.queued)} ＋ 冷却 {format(DEMO_COUNTS.pool.cooling)} ＋ 等回复 {format(DEMO_COUNTS.pool.reply)} ＋ 商品失效 {format(DEMO_COUNTS.pool.invalid)} ＋ 待链接 {format(DEMO_COUNTS.pool.waitingLink)} ＝ {format(DEMO_COUNTS.positions)}</p>
    <Notice tone={poolReconciles()?"success":"warning"}>{poolReconciles()?"恒等式成立，页面可以展示这些数字。":"恒等式不成立，整包必须拒绝展示。"}</Notice>
   </div></Card>
  </div>
  <Card title="从 PID 到发送池的两组漏斗" subtitle="商品阶段按 PID；达人阶段按 handle 与达人×PID，不能把不同单位塞进同一条进度条"><div className="grid gap-5 p-5 lg:grid-cols-2">
   <div><p className="mb-3 text-sm font-semibold text-gray-700 dark:text-gray-200">商品与材料</p><div className="space-y-3">{[
    ["采集 PID",DEMO_COUNTS.collected,"来源快照"],["合格 PID",DEMO_COUNTS.qualified,"销量 / 评分 / 佣金 / 期限"],["精确 Offer",DEMO_COUNTS.offers,"来源与活动已绑定"],["有效 TapLink",DEMO_COUNTS.linked,"listId 与当前佣金一致"],["已查询 PID",DEMO_COUNTS.queriedPids,"首次或 7 天到期"],
   ].map(([label,value,hint])=><div key={String(label)} className="flex items-center gap-3"><div className="w-32 text-sm text-gray-600 dark:text-gray-300">{label}</div><div className="h-2 flex-1 overflow-hidden rounded-full bg-gray-100 dark:bg-gray-800"><div className="h-full rounded-full bg-brand-500" style={{width:`${Math.max(6,Number(value)/DEMO_COUNTS.collected*100)}%`}}/></div><div className="w-20 text-right text-sm font-semibold tabular-nums">{format(Number(value))}</div><span className="hidden w-40 text-xs text-gray-400 2xl:block">{hint}</span></div>)}</div></div>
   <div><p className="mb-3 text-sm font-semibold text-gray-700 dark:text-gray-200">达人与位置</p><div className="space-y-3">{[
    ["正销量线索",DEMO_COUNTS.leads,"达人×PID 原始边"],["去重 handle",DEMO_COUNTS.handles,"达人级 Find 单位"],["OECID 已就位",DEMO_COUNTS.resolved,"稳定身份"],["达人×PID 位置",DEMO_COUNTS.positions,"保留全部机会"],["当前 Ready",DEMO_COUNTS.pool.ready,"每达人一个最优槽位"],
   ].map(([label,value,hint])=><div key={String(label)} className="flex items-center gap-3"><div className="w-32 text-sm text-gray-600 dark:text-gray-300">{label}</div><div className="h-2 flex-1 overflow-hidden rounded-full bg-gray-100 dark:bg-gray-800"><div className="h-full rounded-full bg-success-500" style={{width:`${Math.max(6,Number(value)/DEMO_COUNTS.leads*100)}%`}}/></div><div className="w-20 text-right text-sm font-semibold tabular-nums">{format(Number(value))}</div><span className="hidden w-40 text-xs text-gray-400 2xl:block">{hint}</span></div>)}</div></div>
  </div></Card>
 </div>;
}

function RulesTab(){return <div className="space-y-5">
 <div className="grid gap-5 lg:grid-cols-2">
  <Card title="全托商品 · 进入规则" subtitle="高机会商品 → 仅全球销售商品"><div className="space-y-3 p-5 text-sm leading-6 text-gray-600 dark:text-gray-300">
   {["累计销量 ≥ 300（包含 300）","有评分时评分 ≥ 4.0","没有评分也允许入池","总佣金 − 公开佣金 ≥ 2 个百分点","不使用库存数量作为门槛","下架、治理或明确失效仍然拦截"].map((item,index)=><p key={item} className="flex gap-3"><span className="mt-1 flex size-5 shrink-0 items-center justify-center rounded-full bg-brand-50 text-xs font-semibold text-brand-600 dark:bg-brand-500/10">{index+1}</span>{item}</p>)}
  </div></Card>
  <Card title="Campaign 商品 · 进入规则" subtitle="非全托保持独立资格，不套用全托规则"><div className="space-y-3 p-5 text-sm leading-6 text-gray-600 dark:text-gray-300">
   {["活动必须 ACTIVE","剩余期限 > 45 天","普通商品库存 > 100","无额外条款才可自动加入，否则转人工","达人佣金必须高于公开佣金","每天刷新后，失效 PID 的未发送位置退出可发层"].map((item,index)=><p key={item} className="flex gap-3"><span className="mt-1 flex size-5 shrink-0 items-center justify-center rounded-full bg-warning-50 text-xs font-semibold text-warning-600 dark:bg-warning-500/10">{index+1}</span>{item}</p>)}
  </div></Card>
 </div>
 <Card title="刷新周期与触发方式" subtitle="定时不是唯一入口；任何定时都必须在页面显示开关和下次运行时间"><div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead className="border-b border-gray-200 bg-gray-50 text-xs text-gray-500 dark:border-gray-800 dark:bg-gray-800/60"><tr>{["对象","方式","周期","是否可选","状态影响"].map(item=><th key={item} className="whitespace-nowrap px-5 py-3 font-medium">{item}</th>)}</tr></thead><tbody>{REFRESH_RULES.map(row=><tr key={row.object} className="border-b border-gray-100 align-top dark:border-gray-800"><td className="whitespace-nowrap px-5 py-4 font-medium text-gray-800 dark:text-gray-200">{row.object}</td><td className="whitespace-nowrap px-5 py-4">{row.mode}</td><td className="min-w-48 px-5 py-4 text-gray-500">{row.cycle}</td><td className="whitespace-nowrap px-5 py-4"><Pill tone="neutral">{row.optional}</Pill></td><td className="min-w-72 px-5 py-4 text-gray-500">{row.effect}</td></tr>)}</tbody></table></div></Card>
 <Card title="严格发送池分层" subtitle="缺材料和商品失效不是 ready；历史线索不删除，只移动到正确的层"><div className="grid gap-3 p-5 sm:grid-cols-2 xl:grid-cols-3">
  {[
   ["Ready",DEMO_COUNTS.pool.ready,"有当前有效卡、关系清晰、未冷却","success"],["Queued",DEMO_COUNTS.pool.queued,"同一达人其他 PID，等待轮到","brand"],["Cooling",DEMO_COUNTS.pool.cooling,"达人级时间门禁","neutral"],["等待回复",DEMO_COUNTS.pool.reply,"存在未结问题或人工接管","warning"],["商品失效",DEMO_COUNTS.pool.invalid,"当前商品或活动不合格，可逆","error"],["待链接",DEMO_COUNTS.pool.waitingLink,"待建链 / 待重建，不属于发送池","warning"],
  ].map(([label,value,hint,tone])=><div key={String(label)} className="rounded-xl border border-gray-200 p-4 dark:border-gray-800"><div className="flex items-center justify-between"><p className="font-semibold">{label}</p><Pill tone={tone as "success"|"brand"|"neutral"|"warning"|"error"}>{format(Number(value))}</Pill></div><p className="mt-2 text-xs leading-5 text-gray-500">{hint}</p></div>)}
 </div></Card>
 </div>}

function LocksTab(){return <div className="space-y-5">
 <Notice>锁不是为了增加步骤，而是保证暂停、重启、超时和重复点击后，系统仍然知道“原来那一次到底发生了什么”。</Notice>
 <div className="grid gap-4 lg:grid-cols-2">{LOCKS.map((lock,index)=><Card key={lock.name} className="overflow-hidden"><div className="flex gap-4 p-5"><span className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-gray-100 font-mono text-xs font-semibold text-gray-500 dark:bg-gray-800">L{index+1}</span><div><div className="flex flex-wrap items-center gap-2"><h3 className="font-semibold text-gray-800 dark:text-gray-200">{lock.name}</h3><Pill tone="neutral">{lock.scope}</Pill></div><p className="mt-2 text-xs text-brand-600 dark:text-brand-300">触发：{lock.when}</p><p className="mt-2 text-sm leading-6 text-gray-500">{lock.protects}</p></div></div></Card>)}</div>
 <Card title="TapLink 三层检验" subtitle="池子必须严格，但不能每次打开页面都向平台发上千个请求"><div className="grid gap-4 p-5 lg:grid-cols-3">
  {[{n:"01",title:"池子读取",tag:"本地",text:"读取最近一次已核验台账；必须有精确 listId、来源、活动和当前佣金。失败进入待建链/待重建。"},{n:"02",title:"正式组批",tag:"平台只读",text:"只复读本批 PID，刷新卡片事实；失败项从批次剔除并保存具名原因。"},{n:"03",title:"逐条发送",tag:"最终权威",text:"fresh_card 按冻结 listId 再读一次；不一致、超时或 unknown 立即停止该路径。"}].map((item,index)=><div key={item.n} className="relative rounded-2xl border border-gray-200 p-5 dark:border-gray-800"><div className="flex items-center justify-between"><span className="text-2xl font-semibold text-brand-500">{item.n}</span><Pill tone={index===0?"neutral":"brand"}>{item.tag}</Pill></div><h3 className="mt-4 font-semibold">{item.title}</h3><p className="mt-2 text-sm leading-6 text-gray-500">{item.text}</p>{index<2&&<Icon name="arrow" className="absolute -right-3 top-1/2 hidden size-6 rounded-full bg-brand-500 p-1 text-white lg:block"/>}</div>)}
 </div></Card>
 <Card title="批次冻结内容" subtitle="一旦用户点击开始，下面字段共同决定这一批到底允许发送什么"><div className="flex flex-wrap gap-2 p-5">{["market","institution","creatorOecId","pid","catalogSource","campaignId","creatorPercent","publicPercent","listId","linkRuleVersion","templateVersion","relationshipRevision","authorizationVersion"].map(field=><code key={field} className="rounded-lg bg-gray-100 px-3 py-2 text-xs text-gray-700 dark:bg-gray-800 dark:text-gray-300">{field}</code>)}</div></Card>
 </div>}

function SimulatorTab(){
 const [key,setKey]=useState<ScenarioKey>("clean");
 const scenario=SCENARIOS.find(item=>item.key===key)??SCENARIOS[0];
 const result=useMemo(()=>evaluateScenario(scenario),[scenario]);
 return <div className="space-y-5">
  <Card title="选择一个虚构 PID 场景" subtitle="切换后观察它在哪道门停止，以及最终应该落到哪个层"><div className="grid gap-3 p-5 sm:grid-cols-2 xl:grid-cols-3">{SCENARIOS.map(item=><button key={item.key} type="button" aria-pressed={key===item.key} onClick={()=>setKey(item.key)} className={`rounded-xl border p-4 text-left transition ${key===item.key?"border-brand-400 bg-brand-50 dark:border-brand-700 dark:bg-brand-500/10":"border-gray-200 hover:border-brand-200 dark:border-gray-800"}`}><div className="flex items-center justify-between"><p className="font-semibold text-gray-800 dark:text-gray-200">{item.label}</p><Pill tone={item.source==="selected"?"brand":"warning"}>{item.source==="selected"?"全托":"Campaign"}</Pill></div><p className="mt-2 text-xs leading-5 text-gray-500">{item.description}</p></button>)}</div></Card>
  <div className="grid gap-5 xl:grid-cols-[.8fr_1.2fr]">
   <Card title="PID 与当前方案" subtitle="所有内容均为演示数据"><div className="space-y-4 p-5">
    <div className="rounded-xl bg-gray-50 p-4 dark:bg-gray-800"><p className="text-xs text-gray-500">PID</p><p className="mt-1 font-mono text-sm">1730-DEMO-{String(SCENARIOS.findIndex(item=>item.key===key)+1).padStart(2,"0")}</p></div>
    <div className="grid grid-cols-2 gap-3">{[["累计销量",format(scenario.sales)],["评分",scenario.rating===null?"暂无评分":scenario.rating.toFixed(1)],["公开佣金",`${scenario.publicPercent}%`],["达人佣金",`${scenario.creatorPercent}%`],["活动剩余",scenario.campaignDays===null?"全托方案":`${scenario.campaignDays} 天`],["库存",scenario.stock===null?"不作为门槛":format(scenario.stock)]].map(([label,value])=><div key={label} className="rounded-xl border border-gray-200 p-3 dark:border-gray-800"><p className="text-xs text-gray-400">{label}</p><p className="mt-1 text-sm font-semibold">{value}</p></div>)}</div>
    <Notice tone={result.layer.includes("Ready")?"success":result.layer.includes("失效")?"warning":"info"}><strong>{result.layer}</strong><p className="mt-1">{result.summary}</p></Notice>
   </div></Card>
   <Card title="逐门判定"><div className="p-5"><div className="grid gap-3 md:grid-cols-2">{result.gates.map((gate,index)=><div key={gate.key} className={`rounded-xl border p-4 ${gate.state==="pass"?"border-success-200 bg-success-50/50 dark:border-success-900 dark:bg-success-900/5":gate.state==="stop"?"border-error-200 bg-error-50/50 dark:border-error-900 dark:bg-error-900/5":"border-warning-200 bg-warning-50/50 dark:border-warning-900 dark:bg-warning-900/5"}`}><div className="flex items-center justify-between"><p className="flex items-center gap-2 font-semibold"><span className="text-xs text-gray-400">G{index+1}</span>{gate.label}</p><Pill tone={gateTone[gate.state]}>{gateText[gate.state]}</Pill></div><p className="mt-2 text-xs leading-5 text-gray-500">{gate.detail}</p></div>)}</div></div></Card>
  </div>
  <Card title="达人内部选商品示例" subtitle="业务位置仍是达人×PID；当前槽位选择不靠渠道配额"><div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead className="border-b border-gray-200 bg-gray-50 text-xs text-gray-500 dark:border-gray-800 dark:bg-gray-800/60"><tr>{["候选","来源","达人佣金","线索 rank","当前材料","裁决"].map(label=><th key={label} className="whitespace-nowrap px-5 py-3 font-medium">{label}</th>)}</tr></thead><tbody>{[
   ["PID-A","Campaign","17%","4","已核验","选中：佣金最高"],["PID-B","全托","15%","1","已核验","Queued：rank 更好但佣金低"],["PID-C","全托","15%","3","待重建","不参与当前槽位"],
  ].map(row=><tr key={row[0]} className="border-b border-gray-100 dark:border-gray-800">{row.map((cell,index)=><td key={index} className={`px-5 py-4 ${index===0?"font-mono":index===5?"font-medium text-brand-600 dark:text-brand-300":"text-gray-600 dark:text-gray-300"}`}>{cell}</td>)}</tr>)}</tbody></table></div></Card>
 </div>;
}

export default function PidSendPoolDemoPage(){
 const [tab,setTab]=useState<Tab>("flow");
 return <div className="space-y-5">
  <PageHeading title="PID → 发送池业务沙盘" description="用虚构数据演示从商品 PID 到严格发送池的完整流程、筛选条件、刷新周期、检验门禁与锁。" action={<div className="flex items-center gap-2"><Pill tone="warning">演示数据</Pill><Pill tone="neutral">不连接后端</Pill></div>}/>
  <Notice tone="warning"><strong>这是一张业务定义页面，不是运行页面。</strong> 所有 PID、达人、数量和结果均为虚构；按钮只切换演示场景，不会读取或写入 `var/`，也不会调用 TikTok、Kalodata 或模型。</Notice>
  <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
   <Metric label="采集 PID" value={DEMO_COUNTS.collected} hint="完整来源快照"/>
   <Metric label="合格 PID" value={DEMO_COUNTS.qualified} hint="当前规则通过"/>
   <Metric label="有效 TapLink" value={DEMO_COUNTS.linked} hint="精确绑定已核验"/>
   <Metric label="达人×PID 位置" value={DEMO_COUNTS.positions} hint="历史机会全部保留"/>
   <Metric label="当前 Ready" value={DEMO_COUNTS.pool.ready} hint="每达人一个槽位" accent/>
  </div>
  <Tabs items={[{value:"flow",label:"全链路"},{value:"rules",label:"筛选与刷新"},{value:"locks",label:"门禁与锁"},{value:"simulator",label:"单 PID 演练"}]} value={tab} onChange={setTab}/>
  {tab==="flow"?<FlowTab/>:tab==="rules"?<RulesTab/>:tab==="locks"?<LocksTab/>:<SimulatorTab/>}
  <Section id="demo-principles" title="这套流程要守住的四个定义"><div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">{[
   ["线索不是发送资格","找到达人只证明历史同品，不证明商品、链接或关系现在可发。"],["发送池必须严格","没有当前有效 TapLink 的位置只能待建链或待重建，不能显示 Ready。"],["位置与身份分开","身份按达人判一次；经营机会按达人×PID 保存，其他商品不丢。"],["批次冻结精确材料","真正发送使用冻结的来源、活动、佣金和 listId，不按 PID 临时猜方案。"],
  ].map(([title,text])=><Card key={title}><div className="p-5"><p className="font-semibold text-gray-800 dark:text-gray-200">{title}</p><p className="mt-2 text-sm leading-6 text-gray-500">{text}</p></div></Card>)}</div></Section>
 </div>;
}
