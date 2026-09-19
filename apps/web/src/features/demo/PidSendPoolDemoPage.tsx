"use client";

import {useMemo,useState} from "react";
import {Card,Collapsible,Icon,Notice,PageHeading,Pill,StatTile,Tabs} from "../bdhub/ui";
import {BUSINESS_PHASES,DEMO_COUNTS,FLOW_STAGES,LOCKS,REFRESH_RULES,SCENARIOS,SIMPLE_POOL,poolReconciles,simpleScenario,type ScenarioKey} from "./pid-send-pool-demo";

type Tab="overview"|"details";
type PhaseKey=(typeof BUSINESS_PHASES)[number]["key"];

const format=(value:number)=>value.toLocaleString("zh-CN");

const blockers:Record<PhaseKey,string[]>={
 product:["商品不符合当前来源规则","没有完整、同源的 Offer","缺 TapLink 或卡上佣金已变化"],
 creator:["PID 尚未查询达人","handle 明确搜索不到","OECID 尚未解析"],
 send:["达人仍在冷却","存在未解决回复或人工接管","商品或链接在组批复读时发生变化"],
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
  {label:"商品准备",ok:result.product,detail:result.product?"合格商品 + 精确 Offer + 当前链接":"停在商品准备"},
  {label:"达人准备",ok:result.creator,detail:result.creator?"稳定 OECID 已确认":"等待身份判定"},
  {label:"发送安排",ok:result.send,detail:result.send?"可以进入严格发送池":"不进入 Ready"},
 ];
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
   <Notice tone={result.send?"success":result.layer.includes("失效")?"warning":"info"}><strong>{result.layer}</strong><span className="ml-2">{result.summary}</span></Notice>
  </div></Card>
  <Collapsible label="为什么这个达人会选这个 PID"><div className="overflow-x-auto rounded-xl border border-gray-200 dark:border-gray-800"><table className="w-full text-left text-sm"><thead className="bg-gray-50 text-xs text-gray-500 dark:bg-gray-800/60"><tr>{["候选","达人佣金","rank","材料","结果"].map(label=><th key={label} className="px-4 py-3 font-medium">{label}</th>)}</tr></thead><tbody>{[
   ["Campaign · PID-A","17%","4","已核验","选中：佣金最高"],["全托 · PID-B","15%","1","已核验","等待：佣金较低"],["全托 · PID-C","15%","3","待重建","等待：材料未完成"],
  ].map(row=><tr key={row[0]} className="border-t border-gray-100 dark:border-gray-800">{row.map((cell,index)=><td key={index} className={`px-4 py-3 ${index===4?"font-medium text-brand-600 dark:text-brand-300":"text-gray-600 dark:text-gray-300"}`}>{cell}</td>)}</tr>)}</tbody></table></div></Collapsible>
 </div>;
}

function Details(){return <div className="space-y-4">
 <Notice>以下内容用于解释系统为什么作出判断，不是运营每天需要处理的状态。</Notice>
 <Collapsible label="商品筛选规则" defaultOpen><div className="grid gap-4 lg:grid-cols-2">
  <Card title="全托商品"><div className="space-y-2 p-5 text-sm leading-6 text-gray-600 dark:text-gray-300">{["累计销量 ≥ 300（包含 300）","有评分时 ≥ 4.0；无评分允许","总佣金 − 公开佣金 ≥ 2 个百分点","库存不作为门槛","明确下架、治理或失效仍然拦截"].map(item=><p key={item} className="flex gap-2"><Icon name="check" className="mt-1 size-4 shrink-0 text-success-500"/>{item}</p>)}</div></Card>
  <Card title="Campaign 商品"><div className="space-y-2 p-5 text-sm leading-6 text-gray-600 dark:text-gray-300">{["活动必须 ACTIVE","剩余期限 > 45 天","普通商品库存 > 100","无额外条款才自动加入","达人佣金高于公开佣金"].map(item=><p key={item} className="flex gap-2"><Icon name="check" className="mt-1 size-4 shrink-0 text-success-500"/>{item}</p>)}</div></Card>
 </div></Collapsible>
 <Collapsible label="刷新周期"><div className="overflow-x-auto rounded-xl border border-gray-200 dark:border-gray-800"><table className="w-full text-left text-sm"><thead className="bg-gray-50 text-xs text-gray-500 dark:bg-gray-800/60"><tr>{["对象","什么时候刷新","如何触发","影响"].map(label=><th key={label} className="whitespace-nowrap px-4 py-3 font-medium">{label}</th>)}</tr></thead><tbody>{REFRESH_RULES.map(row=><tr key={row.object} className="border-t border-gray-100 align-top dark:border-gray-800"><td className="whitespace-nowrap px-4 py-3 font-medium">{row.object}</td><td className="min-w-40 px-4 py-3 text-gray-500">{row.cycle}</td><td className="min-w-40 px-4 py-3 text-gray-500">{row.mode} · {row.optional}</td><td className="min-w-64 px-4 py-3 text-gray-500">{row.effect}</td></tr>)}</tbody></table></div></Collapsible>
 <Collapsible label="TapLink 三层检验"><div className="grid gap-4 lg:grid-cols-3">{[
  ["1 · 池子读取","本地台账","有精确 listId、来源、活动和当前佣金，才允许进入商品准备完成。"],["2 · 正式组批","平台只读","只复读本批 PID；变化项退出批次并显示原因。"],["3 · 逐条发送","最终权威","fresh_card 按冻结 listId 再读；不一致或 unknown 立即停止。"],
 ].map(([title,tag,text])=><Card key={title} title={title} action={<Pill tone="neutral">{tag}</Pill>}><p className="p-5 text-sm leading-6 text-gray-500">{text}</p></Card>)}</div></Collapsible>
 <Collapsible label="技术锁与恢复保障"><div className="grid gap-3 md:grid-cols-2">{LOCKS.map(lock=><div key={lock.name} className="rounded-xl border border-gray-200 p-4 dark:border-gray-800"><div className="flex items-center gap-2"><Icon name="lock" className="size-4 text-brand-500"/><p className="font-semibold text-gray-800 dark:text-gray-200">{lock.name}</p></div><p className="mt-2 text-xs text-brand-600 dark:text-brand-300">{lock.scope} · {lock.when}</p><p className="mt-2 text-sm leading-6 text-gray-500">{lock.protects}</p></div>)}</div></Collapsible>
 <Collapsible label="内部完整步骤（开发视角）"><div className="grid gap-3 md:grid-cols-2">{FLOW_STAGES.map(stage=><div key={stage.key} className="rounded-xl bg-gray-50 p-4 dark:bg-gray-800"><div className="flex items-center gap-2"><span className="text-xs font-semibold text-brand-500">{stage.index}</span><p className="font-semibold">{stage.title}</p></div><p className="mt-2 text-xs leading-5 text-gray-500">{stage.rule}</p></div>)}</div></Collapsible>
 </div>}

export default function PidSendPoolDemoPage(){
 const [tab,setTab]=useState<Tab>("overview");
 return <div className="space-y-5">
  <PageHeading title="PID → 发送池业务沙盘" description="默认只看三个阶段和三种结果；完整规则、刷新周期和技术锁按需展开。" action={<div className="flex items-center gap-2"><Pill tone="warning">演示数据</Pill><Pill tone="neutral">不连接后端</Pill></div>}/>
  <Notice tone="warning"><strong>这是一张业务定义页面，不是运行页面。</strong> 所有 PID、达人和数量均为虚构，不会调用 API、SQLite、TikTok、Kalodata 或模型。</Notice>
  <div className="grid gap-3 sm:grid-cols-3">
   <StatTile label="商品准备完成" value={DEMO_COUNTS.linked} hint="合格商品 + 当前有效 TapLink"/>
   <StatTile label="达人身份完成" value={DEMO_COUNTS.resolved} hint="去重达人已有稳定 OECID"/>
   <StatTile label="现在可以发" value={SIMPLE_POOL.sendable} hint="严格发送池 Ready" brand/>
  </div>
  <Tabs items={[{value:"overview",label:"业务总览"},{value:"details",label:"规则明细"}]} value={tab} onChange={setTab}/>
  {tab==="overview"?<Overview/>:<Details/>}
  <p className="text-xs text-gray-400">演示恒等式：{poolReconciles()?"已通过":"未通过"} · 技术原因只用于解释，不新增业务状态。</p>
 </div>;
}
