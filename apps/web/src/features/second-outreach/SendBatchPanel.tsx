"use client";
import {Button,Card,Field,Input,Notice,Pill,StatTile,Toggle} from "../bdhub/ui";
import {NOT_A_BLOCKER,PROBE_COUNT,SEND_COUNTS} from "./send-contracts";
import type {SendController} from "./useSendBatch";

/**
 * 发送池与发送。
 *
 * 池位＝达人×商品，冷却按达人算。这张卡按**四个问题**排：
 *   ① 现在能不能发（窗口、额度、池子六层）
 *   ② 这一批发给谁、发什么话（真实话术样例）
 *   ③ 发不了的是为什么（三种性质完全不同的卡点）
 *   ④ 这一批怎么定（规模、窗口、越界）
 * 最后一行写清楚"怎么真的发出去"，以及**哪一步还没接线**。
 *
 * 卡点必须分清性质——**台账没存下卡只需要一次只读复读 ≠ 卡挂在别的活动要平台写入 ≠ 规则
 * （商品不在合格货盘/关系被挡）本来就不该发**。把这三种混成一个"备链缺口"，等于把只读的活
 * 说成要动平台的活，也把真该发的人挡在外面。
 */
const BLOCKERS:Record<string,{label:string;detail:string;tone?:"warning"|"neutral"}> = {
 // 卡拿不到时必须分清三种完全不同的事：**读一次就有** ≠ **要平台写入** ≠ **台账里根本没这个商品**。
 // 混成一个"备链缺口"会把只读的事说成平台写入——用户就是被这个误导的。
 card_not_read:{label:"台账没存下卡",detail:"这个商品在平台上本来就有链接（有 OECID 的线索就是这么来的），但台账里这一行没有可定位的卡。补一次只读复读就有——不需要建链、不写平台。",tone:"warning"},
 card_rate_changed:{label:"卡的佣金与现在不一致",detail:"台账里那张卡记的是另一个佣金（平台或货盘改过）。话术里的佣金必须和平台一致，所以要只读复读一次确认；不是建链问题。",tone:"warning"},
 card_campaign_changed:{label:"卡挂在别的活动上",detail:"平台上的卡属于另一个活动。要建链或换商品，属于平台写入。",tone:"warning"},
 card_unverified:{label:"只有观察摘要，没有定位信息",detail:"台账里只记着「这个商品有链接」，没记 listId 和佣金。只读复读一次即可定位。",tone:"warning"},
 no_card_needs_link:{label:"平台上没有卡，要建链",detail:"读卡从来没有确认过有卡（行状态是 missing/reading/pending）。抽样 30 个这类商品去问发送时用的那个卡搜索（按 pid 搜卡），返回有卡的是 0 个——这是真·建链缺口，属于平台写入，只读复读解决不了。",tone:"warning"},
 no_link_in_ledger:{label:"台账里完全没有这个商品",detail:"池子里有这条位置，但建链台账里连一行都没有。要核实它凭什么进的池子（先查，不要直接建链）。",tone:"warning"},
 missing_card:{label:"备链缺口",detail:"没有可用的 TapLink 卡（旧口径）。",tone:"warning"},
 missing_short_name:{label:"缺短名",detail:"商品短名取不到，话术里会退化成截断标题；三档兜底都已打通，出现这条说明有别的错。"},
 offer_not_in_current_catalog:{label:"商品不在当前合格货盘",detail:"已下架、佣金不再有优势、剩余不足 45 天或库存不够——这是规则，本来就不该发。等它复活会自动回池。"},
 relationship_blocked:{label:"关系被挡",detail:"达人拒联，或有事项在人工手里。解除后自动回池，不需要重发。"},
 current_identity_missing:{label:"缺 OECID",detail:"平台没给出这个达人的 OECID，进不了池。补身份按钮在系统状态页。"},
 marketing_cooldown:{label:"达人在冷却里",detail:"已解锁 24 小时、未解锁 48 小时，按达人算。等时间就行，不用管。"},
 delivery_already_exists:{label:"这个位置已发过",detail:"同一位达人×同一个商品已经发出去过，不重复。"},
 duplicate_creator:{label:"同一达人这一批只发一条",detail:"一位达人这一批只占一个位置，名下其它商品排到下一批。"}};

const number=(value:number|undefined)=>value===undefined?"—":value.toLocaleString("zh-CN");

/** 四个问题各占一段，页面上从 ① 读到 ④ 就是这套逻辑本身。 */
const Step=({index,title,hint}:{index:string;title:string;hint?:string})=>
 <div className="flex items-start gap-2.5 border-t border-gray-100 pt-4 first:border-t-0 first:pt-0 dark:border-gray-800">
  <span className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full bg-brand-50 text-xs font-semibold text-brand-600 dark:bg-brand-500/10 dark:text-brand-300">{index}</span>
  <div className="min-w-0">
   <p className="text-sm font-semibold text-gray-800 dark:text-white/90">{title}</p>
   {hint&&<p className="mt-0.5 text-xs leading-5 text-gray-500">{hint}</p>}
  </div>
 </div>;

/**
 * 发送池与发送。
 *
 * 这里不发任何消息：它只把"下一批会发给谁"算清楚给人看。真正的发送要落一份批次授权，
 * 由执行器按批跑；那一步的按钮在 ⑤ 里说明。
 */
export default function SendBatchPanel({controller}:{controller:SendController}){
 const {data,draft,setDraft,busy,message,loaded,save}=controller;
 if(!data?.available)return <Card title="发送池与发送" subtitle="池位＝达人×商品，冷却按达人算。">
  <div className="p-5"><p className="text-sm text-gray-500">{!loaded?"正在算这一批会发给谁…":"暂时无法读取发送池。"}</p></div></Card>;
 const {preview,pool}=data;
 const layers=pool.layers;
 const blockers=Object.entries(preview.skipped).filter(([reason])=>!NOT_A_BLOCKER.has(reason));
 const deferred=Object.entries(preview.skipped).filter(([reason])=>NOT_A_BLOCKER.has(reason));
 const outsideWindow=preview.skipped.outside_send_window??0;
 // "这一批比要的少"必须说清是**哪种少**：窗口关着 ≠ 池子里过不了复检。说反了会让人以为池子空了。
 const shortfall=outsideWindow>0&&preview.sendable===0
  ? `现在不在发送窗口内（${preview.window.start??""}\u2013${preview.window.end??""}），到点会自动开`
  : outsideWindow>0
   ? `其中 ${number(outsideWindow)} 条要等到窗口内才发`
   : `池子里只有 ${number(preview.sendable)} 条过得了复检`;
 const rereadGap=(preview.skipped.card_not_read??0)+(preview.skipped.card_unverified??0)+(preview.skipped.missing_card??0);
 const rateGap=preview.skipped.card_rate_changed??0;
 const linkGap=(preview.skipped.no_card_needs_link??0)+(preview.skipped.card_campaign_changed??0)+(preview.skipped.no_link_in_ledger??0);
 // 池子口径的"可发位置"（不在冷却/没被挡/有 OECID）**不等于**过完卡检真能发的数。
 // 两个数都要显示，并且相等关系要写出来：positions = passable + 真卡点。
 const blocked=rereadGap+rateGap+linkGap+(preview.skipped.offer_not_in_current_catalog??0)
  +(preview.skipped.relationship_blocked??0)+(preview.skipped.current_identity_missing??0)
  +(preview.skipped.marketing_cooldown??0)+(preview.skipped.delivery_already_exists??0)
  +(preview.skipped.duplicate_creator??0)+(preview.skipped.missing_short_name??0);
 const passable=Math.max(0,preview.positions-blocked);
 // 卡点的性质必须分开报：台账没存下卡（复读就好） ≠ 卡佣金不一致（业务决定） ≠ 平台上没有卡（平台写入）。
 const names=Object.entries(preview.nameQuality);
 const count=draft?.count??data.config.count;
 const widen=Boolean(draft?.widen);
 // 越界时多一档 600：账号级日额度还没拿到，先按它探。
 const choices=widen?[...SEND_COUNTS,PROBE_COUNT]:SEND_COUNTS.filter(value=>value!==PROBE_COUNT);
 return <Card title="发送池与发送" subtitle="池位＝达人×商品，冷却按达人算。这张卡只算「下一批会发给谁」，不发消息。"
  action={preview.window.enabled?<Pill tone={preview.window.open?"success":"warning"}>窗口 {preview.window.open?"开着":"关着"}</Pill>:<Pill tone="neutral">不设窗口</Pill>}>
  <div className="space-y-5 p-5">

   {/* ① 现在能不能发 */}
   <Step index="1" title="现在能不能发" hint="窗口、额度、池子六层。这一层只读，不碰平台。"/>
   <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
    <StatTile label="可发位置" value={layers.ready??preview.readyAvailable} hint="不在冷却、关系没被挡、有 OECID" brand/>
    <StatTile label="池中等待" value={layers.queued??0} hint="同一达人的其它商品排在后面；一条发完自动上位"/>
    <StatTile label="冷却中" value={layers.cooling??0} hint="按达人算（已解锁 24h / 未解锁 48h），到点自动回池"/>
    <StatTile label="已发送" value={layers.sent??0} hint="发出去过的达人×商品，记录保留不删"/>
   </div>
   <div className="flex flex-wrap gap-x-6 gap-y-2 text-sm text-gray-600 dark:text-gray-300">
    <span>等达人回复 <strong>{number(layers.awaiting_reply)}</strong>（等人，不是等时间）</span>
    <span>已排除 <strong>{number(layers.excluded)}</strong></span>
    <span>近 24 小时新联系 <strong>{number(preview.capacity?.used)}</strong> / {number(preview.capacity?.limit)}（还剩 {number(preview.capacity?.remaining)}）</span>
    <span>发送窗口 <strong>{preview.window.enabled?`${preview.window.start}\u2013${preview.window.end}`:"没启用"}</strong>（北京时间）</span>
   </div>

   {/* ② 这一批发给谁、发什么话 */}
   <Step index="2" title="这一批发给谁、发什么话" hint="两三条例样，就是手指点下去之后真正会发出去的那句话。"/>
   <div className="rounded-xl border border-gray-200 p-4 dark:border-gray-800">
    {preview.samples.length===0?<p className="text-xs text-gray-500">
      {preview.sendable===0?"这一批没有可发的位置，所以没有样例。":"这一批还没挑出可发的样例。"}</p>
     :<ul className="divide-y divide-gray-100 dark:divide-gray-800">{preview.samples.map(row=>
      <li key={`${row.handle}-${row.pid}`} className="space-y-1.5 py-3 text-sm first:pt-0">
       <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <strong className="text-gray-800 dark:text-white/90">@{row.handle}</strong>
        <span className="text-xs text-gray-500">达人 {row.creatorPercent}% · 公开 {row.publicPercent}% · 商品短名{row.nameSource}：{row.name}</span>
        <span className="text-xs text-gray-400">{row.unlocked?"已解锁过":"首次联系"}</span>
       </div>
       {/* 发出去的就是这句话：既有模板（v4 standard）＋ 意语商品短语，逐字渲染，不另写文案。 */}
       <p className="rounded-lg bg-gray-50 px-3 py-2 leading-6 text-gray-800 dark:bg-white/[0.04] dark:text-gray-200">
        {row.messageIt||"（这句话渲染不出来：商品短语缺失，这一条不该进批次）"}</p>
       {row.messageZh&&<p className="text-xs leading-5 text-gray-500">中文对照（只是给你看的，不会发给达人）：{row.messageZh}</p>}
      </li>)}</ul>}
   </div>

   {/* ③ 发不了的是为什么 */}
   <Step index="3" title="发不了的是为什么" hint="扫到的槽位要么进这一批，要么有具名原因。原因分成三种性质，别混着看。"/>
   <div className="rounded-xl bg-gray-50 p-4 dark:bg-white/[0.03]">
    <div className="flex flex-wrap items-baseline gap-x-6 gap-y-2">
     <span className="text-sm text-gray-600 dark:text-gray-300">这一批准备发 <strong className="text-lg text-gray-800 dark:text-white/90">{number(preview.sendable)}</strong> 条
      {preview.requested>preview.sendable&&<span className="text-xs text-gray-500">（要 {number(preview.requested)} 条，{shortfall}）</span>}</span>
     <span className="text-xs text-gray-500">为凑够这一批往下翻了 <strong>{number(preview.positions)}</strong> 个可发槽位</span>
    </div>
    {/* 两个数字必须对得上：池子口径的"可发" ≠ 过完卡检"真能发"的。差在哪要写出来。 */}
    <p className="mt-2 text-xs leading-5 text-gray-600 dark:text-gray-300">
     扫到的 <strong>{number(preview.positions)}</strong> 个可发槽位 ＝ 真能发 <strong>{number(passable)}</strong> ＋ 卡点 <strong>{number(blocked)}</strong>。
     真能发里这一批取 <strong>{number(preview.sendable)}</strong> 条{preview.sendable<passable?`，剩下 ${number(passable-preview.sendable)} 条留在池子里等下一批`:"（这一批全取走）"}。
    </p>
    {blockers.length>0&&<ul className="mt-3 space-y-2">{blockers.map(([reason,count])=>{
     const row=BLOCKERS[reason]??{label:reason,detail:"未知原因，需要查代码。"};
     return <li key={reason} className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-sm">
      <Pill tone={row.tone??"neutral"}>{row.label}</Pill>
      <strong className="text-gray-800 dark:text-white/90">{number(count)}</strong>
      <span className="text-xs leading-5 text-gray-500">{row.detail}</span>
     </li>;})}</ul>}
    {blockers.length===0&&<p className="mt-2 text-sm text-gray-600 dark:text-gray-300">这个规模下没有卡点：扫到的槽位都进得了这一批。</p>}
    {deferred.length>0&&<p className="mt-3 text-xs leading-5 text-gray-500">
     另外 {deferred.map(([reason,value])=>`${reason==="beyond_requested_size"?"超出这一批的规模":reason==="local_capacity_reached"?"超出 24 小时本地额度":"还没到窗口"} ${number(value)} 条`).join("、")}——
     这些不是卡点，池子里留着，下一批或换个规模就能发。
    </p>}
    {(rereadGap+rateGap+linkGap)>0&&<div className="mt-3"><Notice tone="warning">
     <p>卡点分成<strong>三种性质完全不同的</strong>事，处理它们要花的代价完全不同：</p>
     <ul className="mt-1 list-disc space-y-1 pl-5">
      {rereadGap>0&&<li><strong>台账没存下卡 {number(rereadGap)}</strong>：商品在平台上本来就有链接（不然线索根本查不出来），缺一次<strong>只读复读</strong>。</li>}
      {rateGap>0&&<li><strong>卡的佣金与计划不一致 {number(rateGap)}</strong>：旧卡比计划低 1 点。要么按卡上的佣金发（不写平台），要么重建链——<strong>这是业务决定</strong>，见下面那一块。</li>}
      {linkGap>0&&<li><strong>平台上没有卡 {number(linkGap)}</strong>：抽样问过发送用的那个卡搜索，有卡的是 0 个。这一档才是真·<strong>建链缺口（平台写入）</strong>。</li>}
     </ul>
    </Notice></div>}
   </div>

   {/* 卡的佣金与当前计划：旧卡比计划低 1 点，这是**业务决定**，不能由代码替用户选。 */}
   {(preview.rateGap.lower+preview.rateGap.higher>0||preview.rateGap.same>0)&&<div className="rounded-xl border border-warning-200 p-4 dark:border-warning-900">
    <p className="text-sm font-medium text-gray-700 dark:text-gray-200">台账里的卡，佣金与当前计划一致吗</p>
    <div className="mt-2 flex flex-wrap gap-x-6 gap-y-1 text-sm text-gray-600 dark:text-gray-300">
     <span>一致 <strong>{number(preview.rateGap.same)}</strong>（这些就能发）</span>
     <span className={preview.rateGap.lower>0?"text-warning-600 dark:text-warning-400":""}>卡上更低 <strong>{number(preview.rateGap.lower)}</strong>{preview.rateGap.lowerByOne>0&&<span className="text-xs">（其中低 1 点 {number(preview.rateGap.lowerByOne)}）</span>}</span>
     {preview.rateGap.higher>0&&<span>卡上更高 <strong>{number(preview.rateGap.higher)}</strong></span>}
     <span>台账里还没卡 <strong>{number(preview.rateGap.noCard)}</strong></span>
    </div>
    {preview.rateGap.examples.length>0&&<ul className="mt-2 space-y-1 text-xs leading-5 text-gray-500">
     {preview.rateGap.examples.map(row=><li key={row.pid}>{row.listName||"（没有卡名）"}：卡上 <strong>{row.cardPercent}%</strong> / 计划 <strong>{row.planPercent}%</strong></li>)}
    </ul>}
    {preview.rateGap.lower>0&&<Notice tone="warning">
     这些是<strong>旧卡</strong>（卡名多为 <code className="rounded bg-white/60 px-1 dark:bg-black/20">🚀 Incentivo Boost disponibile | BJN</code>），
     达人佣金比现在的计划<strong>低 1 个点</strong>。发送时读卡那一步要求「卡上的佣金 == 我们声明的佣金」，
     所以声明计划佣金、卡上却是旧数，会被它挡下（实测：同一个商品在发送读卡端返回 12%，计划是 13%）。
     两条路，得你定：<strong>①按卡上的佣金发</strong>（话术里说卡上那个数，不写平台，立刻可发）；
     <strong>②按计划的佣金重建链</strong>（话术说计划那个数，属于平台写入）。在你定之前，这一批不会动它们。
    </Notice>}
   </div>}

   {/* 短名质量：卡点里的"缺短名"已经打通，这里报的是名字从哪来。 */}
   <div className="rounded-xl border border-gray-200 p-4 dark:border-gray-800">
    <p className="text-sm font-medium text-gray-700 dark:text-gray-200">这一批商品短名的来源</p>
    <p className="mt-1 text-xs leading-5 text-gray-500">
     话术只填商品短名＋固定模板，不逐人写文案。短名优先用缓存（AI 生成），取不到就用建链时冻结在卡名里的那一个
     （<code className="rounded bg-gray-100 px-1 dark:bg-gray-800">BJN 短名 达人佣金%</code>），再不行才截断标题——
     <strong>三档都不挡发送</strong>，只是名字质量的差别。
    </p>
    <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-sm text-gray-600 dark:text-gray-300">
     {names.length===0?<span className="text-xs text-gray-500">这一批还没挑出可发的位置。</span>
      :names.map(([source,value])=><span key={source}>{source} <strong>{number(value)}</strong></span>)}
     {(preview.skipped.missing_short_name??0)>0&&<span className="text-warning-600">缺短名 {number(preview.skipped.missing_short_name)}（已不该出现）</span>}
    </div>
   </div>

   {/* ④ 这一批怎么定 */}
   <Step index="4" title="这一批怎么定" hint="只改这三件：多少条、窗口开不开、要不要越界。保存不会开始发送。"/>
   <div className="grid gap-4 lg:grid-cols-3">
    <Field label="这一批多少条" hint="默认 500，可切 1000。到 24 小时上限会自动停，不会超发。">
     <div className="flex flex-wrap gap-2">
      {choices.map(value=><Button key={value} size="sm" variant={count===value?"primary":"outline"}
       disabled={busy||!draft} onClick={()=>draft&&setDraft({...draft,count:value})}>{value}</Button>)}
     </div>
    </Field>
    <Field label="发送窗口（北京时间）" hint="只在窗口内发；默认 9:00–24:00，两端都能改。关掉就是不设窗口。">
     <div className="flex items-center gap-2">
      <Input type="time" value={draft?.window[0]??data.config.window[0]} disabled={busy||!draft||!draft.windowEnabled}
       onChange={e=>draft&&setDraft({...draft,window:[e.target.value,draft.window[1]]})}/>
      <span className="text-sm text-gray-500">到</span>
      <Input type="time" value={draft?.window[1]??data.config.window[1]} disabled={busy||!draft||!draft.windowEnabled}
       onChange={e=>draft&&setDraft({...draft,window:[draft.window[0],e.target.value]})}/>
     </div>
    </Field>
    <div className="space-y-1">
     <Toggle label="启用发送窗口" description={`现在${preview.window.enabled?(preview.window.open?"在窗口内":"不在窗口内，会等到点"):"没启用，随时可发"}`}
      checked={Boolean(draft?.windowEnabled)} disabled={busy||!draft}
      onChange={value=>draft&&setDraft({...draft,windowEnabled:value})}/>
     <Toggle label="越过本地 24 小时 500 新联系闸门" description="显式越界探测。账号级日额度还没拿到，先按 600 探；每条真实回执都会单独落信号，单达人到上限只标那一条、不停整批。"
      checked={widen} disabled={busy||!draft}
      onChange={value=>draft&&setDraft({...draft,widen:value,count:value&&!SEND_COUNTS.includes(draft.count)?PROBE_COUNT:draft.count})}/>
    </div>
   </div>
   <div className="flex flex-wrap items-center gap-2">
    <Button size="sm" variant="outline" disabled={busy||!draft} onClick={()=>void save()}>{busy?"保存中…":"保存设置"}</Button>
    {data.configInvalid&&<Pill tone="warning">配置文件已过期，按默认值读</Pill>}
   </div>
   {message&&<Notice tone="info">{message}</Notice>}

   {/* ⑤ 怎么真的发出去：算清楚 → 你确认 → 执行器发 */}
   <Step index="5" title="怎么真的发出去" hint="中间那一步是你的，最后一步还没接线。"/>
   <div className="rounded-xl border border-gray-200 p-4 dark:border-gray-800">
    <ol className="space-y-2 text-xs leading-5 text-gray-500">
     <li><strong className="text-gray-700 dark:text-gray-300">1. 算清楚（就是上面的数字）</strong>：只读复检，不碰平台、不落库，可以反复看。</li>
     <li><strong className="text-gray-700 dark:text-gray-300">2. 你确认这一批</strong>：确认后才落一份批次授权（多少条、发谁的、窗口、是否越界），执行器按这份授权跑。</li>
     <li><strong className="text-gray-700 dark:text-gray-300">3. 执行器发</strong>：复用既有发送链（车道、预算、回查、未知即停）。<strong className="text-warning-600 dark:text-warning-400">这一步的按钮还没接上——
      它要把批次授权写进 <code>cycle_bulk</code> 再交给既有执行器，是下一步的活。现在这里不会发出任何消息。</strong></li>
    </ol>
   </div>
  </div>
 </Card>;
}
