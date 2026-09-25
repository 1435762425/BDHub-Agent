"use client";
import {Button,Card,Field,Input,MetricTable,Notice,Pill,Progress,Section} from "../bdhub/ui";
import FunnelBar from "./FunnelBar";
import type {FunnelStage} from "./funnel";
import ShortNames from "./ShortNames";
import {useCampaignJoin} from "./useCampaignJoin";
import {useCampaignLinks} from "./useCampaignLinks";
import {useCampaignPanel} from "./useCampaignPanel";
import {useCatalogJobs} from "./useCatalogJobs";

// 原因码与状态都翻成人话：页面不显示机器码。
const REASONS:Record<string,string>={insufficient_commission_gap:"佣金差不足 2 个点",
 missing_publicPercent:"缺公开佣金",missing_total_commission:"缺总佣金",unavailable:"商品不可用",
 stock_not_over_100:"库存不超过 100",expiry_not_over_45_days:"商品剩余不足 45 天",
 campaign_end_missing:"活动截止日期缺失",campaign_not_started:"活动尚未开始",campaign_ended:"活动已结束",
 campaign_expiry_within_45_days:"活动剩余不足 45 天",campaign_not_joinable_now:"已不在可加入列表",
 joined:"已加入",platform_accepted:"平台已接受",platform_rejected:"平台明确拒绝",
 not_joined_platform_lists_it_joinable:"平台说没加入（仍列在可加入里）"};
const JOIN_STATES:Record<string,string>={eligible:"可加入",joined:"已加入",writing:"提交中",
 result_unknown:"结果待核验",skipped:"不加入"};
const JOB_STATES:Record<string,string>={previewed:"已预览",completed:"已完成",
 needs_verification:"有结果待核验",blocked:"已停止",applying:"提交中"};
const COLLECT_STATES:Record<string,string>={running:"采集中",screening:"重新筛分中",paused:"可继续",
 completed:"采集完成",blocked:"已停止",stopped:"已停止",done:"采集完成"};
const COLLECT_ERRORS:Record<string,string>={account_busy:"账号正忙（另一个任务在用它）",
 catalog_total_changed:"平台的活动或商品数量在读取过程中变了，需要重起一轮",
 catalog_page_incomplete:"分页对不上总数，已保留进度",
 source_maintenance_due:"账号正在维护",identity_unverified:"身份文件未通过核对"};
const reason=(code:string)=>REASONS[code]??code;
const tone=(state:string)=>state==="eligible"||state==="completed"?"success"
 :state==="joined"?"brand":state==="result_unknown"||state==="needs_verification"?"warning"
 :state==="blocked"?"error":"neutral";

/**
 * 非全托（Campaign）商品页。
 *
 * 步骤与全托页一一对应，因为**漏一步就等于那段流程不受控**：
 *   ① 加入与采集：加入活动（平台写入，手动确认）→ 刷新采集（只读，读完自动重新筛分入池）
 *   ② 准备链接：识别标准卡 / 补建标准卡（平台写入，手动确认）＋ 商品短名（卡名来源）
 *   ③ 达人线索：查询队列 / 达人身份 / 发送池——**这两步跨渠道共用**，不是非全托独有
 *      （位置＝达人×商品，渠道只挂在商品与链接上，绝不按渠道拆达人）。
 */
export default function CampaignPanel({market,onOpenLeads}:{market:string;onOpenLeads?:()=>void}){
 const panel=useCampaignPanel(market);
 const join=useCampaignJoin(market);
 const links=useCampaignLinks(market);
 const jobs=useCatalogJobs(market);
 const pool=panel.data, ledger=join.data;
 const joinable=(ledger?.items??[]).filter(item=>item.state==="eligible");
 const activeVerification=ledger?.activeVerification??ledger?.unresolved??[];
 const stoppedUnknown=ledger?.stoppedUnknown??[];
 // 链接准备：只有当前标准链接算完成；旧卡只保留历史。
 const linkStates=links.data?.states??{};
 const linkTargets=links.data?.targets??0;
 const judged=Math.max(0,linkTargets-(linkStates.pending??0)-(linkStates.reading??0));
 const linkOk=links.data?.verifiedPids??0;
 const other=ledger?.otherCategories;
 const linkRun=jobs.data?.linksCampaign?.run;
 // 采集作业：进度是另一种形状（轮次/请求数/商品数），按 phase==="collect" 区分。
 const collectRun=jobs.data?.campaignCollect?.run;
 const collectProgress=collectRun?.progress;
 const jump=(id:string)=>document.getElementById(id)?.scrollIntoView({behavior:"smooth",block:"start"});
 // 漏斗与全托同一条读法：缺来源显示 null（"—"）而不是 0——"还没读到"和"一个都没有"是两件事。
 // 漏斗按**商品数**读，不按 offer 条数：同一个商品可能在多个活动里出现，
 // offer 数会让人以为商品比实际多。
 const collectStages:FunnelStage[]=[
  {key:"card-campaign-pool",label:"采集",value:pool?.distinctPids??null,hint:"去重商品（PID）"},
  {key:"card-campaign-pool",label:"合格",value:pool?.eligiblePids??null,
   hint:pool?.distinctPids?`合格率 ${(((pool.eligiblePids??0)/(pool.distinctPids||1))*100).toFixed(1)}%`:"按已确认门槛"},
  {key:"card-campaign-links",label:"标准链接",value:links.data?.available?linkOk:null,hint:"当前规则已核验"},
  // 线索现在是独立页签：这里只指路，不再在这一页读线索账本（避免两处各读一份）。
  {key:"card-leads",label:"达人线索",value:null,hint:"已移到独立页签（两条渠道共用）"},
 ];
 return <div className="space-y-5">
  <FunnelBar stages={collectStages} onJump={id=>id==="card-leads"&&onOpenLeads?onOpenLeads():jump(id)}/>

  <Section id="stage-campaign-join" index="①" title="加入与采集"
   summary={<>可加入 {joinable.length} · 已加入 {ledger?.joinedCount??"—"} · 快照 {pool?.offers?.toLocaleString()??"—"}</>}>

   <div id="card-campaign-join" className="scroll-mt-6"><Card title="加入活动"
    action={ledger?.state?<Pill tone={tone(ledger.state)}>{stoppedUnknown.length>0&&activeVerification.length===0?"未知项已停止跟进":JOB_STATES[ledger.state]??ledger.state}</Pill>:null}>
    <div className="space-y-4 p-5">
    {!ledger?.available&&<p className="text-sm text-gray-500">{join.loaded?"还没有读过可加入的活动。":"读取中…"}</p>}
    {ledger?.available&&<>
     <MetricTable rows={[
      {label:"可加入",value:joinable.length.toLocaleString(),detail:"通过资格判定，可以直接加入",accent:true},
      {label:"已加入（平台）",value:(ledger.joinedCount??0).toLocaleString(),detail:"平台自己的回答，不靠本地推断"},
      {label:"有限核验",value:activeVerification.length.toLocaleString(),detail:"不阻塞已加入活动的商品准备"},
      {label:"已停止跟进",value:stoppedUnknown.length.toLocaleString(),detail:"结果仍未知；不重发，无需人工处理"},
      {label:"其它分类可加入",value:(other?.unjoinedEligible??0).toLocaleString(),detail:other?.available?`平台活动：${other.parents} 父 / ${other.subs} 子${ledger?.account?` · 账号 ${ledger.account}`:""}`:(ledger?.account?`账号 ${ledger.account}`:"其它分类未读到")},
     ]}/>
     {activeVerification.length>0&&<Notice tone="info">有 {activeVerification.length} 个新申请结果未知，其他活动继续。自动运营先准备已确认商品，再做有限核验；耗尽后停止跟进，无需逐项处理。</Notice>}
     {stoppedUnknown.length>0&&<p className="text-sm text-gray-500">{stoppedUnknown.length} 个申请已停止主动核验。正常刷新若发现已加入，会自动纳入；原申请不会重发。</p>}
     {ledger.error&&<Notice tone="warning">上次运行停在 {reason(ledger.error)}。</Notice>}
     {joinable.length===0&&<Notice tone="info">平台当前没有新的可加入活动。活动有更新时点「刷新活动列表」再看。</Notice>}
     {other?.available&&(other.unjoinedEligible??0)>0&&<Notice tone="warning">
      另有 <b>{other.unjoinedEligible}</b> 个<b>其它分类</b>的活动（平台活动）可以加入，但「一键加入」现在只覆盖 Seller collabs 这一类。
      要一起加需要先确认那一类的加入方式。</Notice>}
     {other?.available&&(other.unjoinedEligible??0)===0&&(other.unjoined?.length??0)>0&&<Notice tone="info">
      其它分类（平台活动）：{other.parents} 个父活动 / {other.subs} 个子活动，没有可加入的——未加入的
      {other.unjoined?.map(row=>`${row.name}（${reason(row.reason)}）`).join("、")}。</Notice>}
     {/* 只在**有可加入的**时候列出来：那是这次会被写进去的东西，必须让人看见；
         已加入的记录不需要明细（用户明确不要）。 */}
     {joinable.length>0&&<div className="space-y-2">{joinable.map(item=><div key={item.campaignId} className="flex flex-wrap items-center gap-3 rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-gray-700">
      <span className="min-w-40 flex-1 truncate" title={item.name}>{item.name||item.campaignId}</span>
      <span className="font-mono text-xs text-gray-400">{item.campaignId}</span>
      {item.reason&&<span className="text-xs text-gray-500">{reason(item.reason)}</span>}
     </div>)}</div>}
    </>}
     <div className="grid gap-4 lg:grid-cols-3">
      <Field label="联系邮箱" hint="平台加入活动时要求填写，会成为平台侧的联系方式。">
       <Input value={join.email} onChange={e=>join.setEmail(e.target.value)} placeholder="you@example.com" maxLength={254}/></Field>
      <div className="flex flex-wrap items-end gap-2">
       <Button size="sm" variant="outline" disabled={join.busy} onClick={()=>void join.preview()}>{join.busy?"处理中…":"刷新活动列表"}</Button>
       <Button size="sm" variant="outline" disabled={join.busy||activeVerification.length===0} onClick={()=>void join.recheck()}>回查一次</Button>
      </div>
      <div className="flex flex-wrap items-end gap-2">
       <Button size="sm" disabled={join.busy||joinable.length===0} onClick={()=>void join.joinAll()}>
        {join.busy?"提交中…":joinable.length===0?"没有新的可加入活动":`一键加入全部可加入（${joinable.length}）`}</Button>
      </div>
     </div>
     {join.message&&<Notice tone="info">{join.message}</Notice>}
     <p className="text-xs leading-5 text-gray-400">「一键加入」是<b>平台写入</b>：点一次就把<b>当前全部可加入</b>的活动加进去（点之前会重新预览一遍，不拿页面上的旧列表去写）。每个活动一次会话只写一次；结果未知的只回查、不重发。本地记录的写入次数：{ledger?.platformWrites??0}。</p>
    </div></Card></div>

   <div id="card-campaign-pool" className="scroll-mt-6"><Card title="Campaign 商品池与采集"
    action={<div className="flex flex-wrap items-center gap-2">
     {collectRun?.running?<Pill tone="brand">采集中</Pill>
      :collectProgress?.status?<Pill tone={collectProgress.status==="completed"?"success":"neutral"}>{COLLECT_STATES[collectProgress.status]??collectProgress.status}</Pill>
      :<Pill tone={pool?.available?"success":"neutral"}>{pool?.available?"已接入":"暂无快照"}</Pill>}
     <Button size="sm" disabled={jobs.busy!==null||Boolean(collectRun?.running)}
      onClick={()=>void jobs.start("campaignCollect").then(()=>jobs.reload().then(()=>panel.reload())).catch(()=>{})}>
      {collectRun?.running?"采集中…":pool?.available?"重新采集":"刷新活动与商品"}</Button>
     {collectRun?.running&&<Button size="sm" variant="outline" disabled={jobs.busy!==null} onClick={()=>void jobs.stop("campaignCollect")}>停止</Button>}
    </div>}>
    <div className="space-y-4 p-5">
    <p className="text-sm leading-6 text-gray-500">
     加入活动只让账号**进入**活动；商品要**采集**回来才会进池子，所以加入新活动之后要点这里的按钮：
     重新读一遍已加入的活动与商品，读完自动按当前门槛重新筛分入池。只读平台，不建链、不改任何平台数据。
    </p>
    {collectRun?.running&&<Progress done={collectProgress?.round??0} total={collectProgress?.rounds??0} label={`采集进度（第 ${collectProgress?.round??0} / ${collectProgress?.rounds??0} 轮）`}/>}
    {collectProgress?.error&&<Notice tone="warning">上次采集停在 {COLLECT_ERRORS[collectProgress.error]??collectProgress.error}。已保存断点，再点一次会重新起一轮。</Notice>}
    {!pool?.available&&<p className="text-sm text-gray-500">{panel.loaded?"还没有非全托快照：先点上面的「刷新活动与商品」。":"读取中…"}</p>}
    {pool?.available&&<>
     <MetricTable rows={[
      {label:"采集到的商品（去重）",value:(pool.distinctPids??0).toLocaleString(),detail:"同一商品出现在多个活动时只算一次"},
      {label:"合格商品",value:(pool.eligiblePids??0).toLocaleString(),detail:"通过当前 Campaign 门槛",accent:true},
      {label:"标准链接已就绪",value:linkOk.toLocaleString(),detail:"可以进入达人线索查询"},
     ]}/>
     {!pool.poolReconciled&&<Notice tone="warning">合格商品数与去重商品数对不上，先别据此判断池子大小。</Notice>}
     <Progress done={pool.eligiblePids??0} total={pool.distinctPids??0} label="合格比例（合格商品 / 去重商品）"/>
     <div className="rounded-xl border border-gray-200 p-4 dark:border-gray-700">
      <p className="text-sm font-medium text-gray-700 dark:text-gray-200">筛选条件（已确认口径，不在页面调整）</p>
      <ul className="mt-2 space-y-1 text-xs leading-6 text-gray-500">
       <li>· 达人佣金必须 <b>高于公开佣金</b>（这是硬条件，不是偏好）</li>
       <li>· 佣金差至少 <b>2 个点</b>：机构 1–2 点、达人 = 总佣金 − 机构；差不足就不合格</li>
       <li>· <b>库存 &gt; 100</b>（非全托要求数量门槛；全托已取消，这里不适用）</li>
       <li>· 商品当前<b>可推</b>（平台状态正常、未被治理、未下架）</li>
       <li>· 活动剩余 <b>大于 45 天</b></li>
       <li>· 评分 &gt; 4 只作<b>偏好</b>，不挡商品</li>
      </ul>
      <p className="mt-2 text-xs text-gray-400">
       佣金规则来自 <span className="font-mono">config/catalog-link-policy.json</span>；改这里不会改门槛，只会影响新建链接的分佣。
       合格商品里若有多个活动可选，按「达人佣金最高 → 截止更晚 → 活动ID最小」挑一个，<b>不合格的不硬挑</b>。
      </p>
     </div>
     <p className="text-xs leading-5 text-gray-500">
      被筛掉的原因（按条数）：{Object.entries(pool.reasons??{}).sort((a,b)=>b[1]-a[1]).map(([code,value])=>`${reason(code)} ${value.toLocaleString()}`).join(" · ")||"没有被筛除的商品"}
     </p>
     {pool.recorded&&<p className="text-xs text-gray-400">上次落库 {new Date(pool.recorded.updated*1000).toLocaleString("zh-CN",{timeZone:"Asia/Shanghai",hour12:false})}{pool.recorded.snapshot===pool.snapshot?"（与当前快照一致）":"（快照已更新，上面的数字是实时算的）"}</p>}
    </>}
    </div></Card></div>
  </Section>

  <Section id="stage-campaign-links" index="②" title="准备链接"
   summary={<>待准备 {linkTargets.toLocaleString()} · 历史卡记录 {links.data?.reusePids??"—"} · 当前标准 {links.data?.verifiedPids??"—"}</>}>
   <div id="card-campaign-links" className="scroll-mt-6"><Card title="链接准备"
    action={links.data?.available?<Pill tone="success">已读卡</Pill>:<Pill tone="neutral">未开始</Pill>}>
    <div className="space-y-4 p-5">
    <p className="text-sm leading-6 text-gray-500">
     非全托卡片挂在活动上。系统只识别统一分佣、命名和活动绑定完全一致的标准卡；其他旧卡保留历史，
     但不参与新发送。没有标准卡时才补建，新建前会按活动重新核一遍商业事实。
    </p>
    {!links.data?.available&&<p className="text-sm text-gray-500">{links.loaded?"还没有为非全托读过卡。先点下面的「准备链接（只查不建）」。":"读取中…"}</p>}
    {links.data?.available&&<>
     <MetricTable rows={[
      {label:"待备链商品",value:linkTargets.toLocaleString(),detail:(links.data.excluded??0)>0?`已排除跨渠道重叠 ${links.data.excluded} 个（全托优先）`:"已入池商品，一个商品只出一条"},
      {label:"历史卡记录",value:(links.data.reusePids??0).toLocaleString(),detail:"只用于追溯，不进入发送材料"},
      {label:"缺标准链接",value:(linkStates.missing??0).toLocaleString(),detail:"需要确认或创建当前标准卡"},
      {label:"当前标准链接",value:(links.data.verifiedPids??0).toLocaleString(),detail:"统一规则并已回读确认",accent:true},
     ]}/>
     <Progress done={judged} total={linkTargets} label="准备进展（已判定 / 待备链商品）"/>
     {linkRun?.running&&<Progress done={linkRun.progress?.created??0} total={(linkRun.progress?.created??0)+((linkRun.progress?.states?.missing)??0)} label="本批建链进展（本批已建 / 本批已建＋还缺）"/>}
     {(links.data.planMissing??0)>0&&<Notice tone="warning">有 {links.data.planMissing} 个已入池商品在快照里找不到对应活动事实，已跳过——不按猜的活动建链。</Notice>}
     {(links.data.commissionInvalid??0)>0&&<Notice tone="warning">有 {links.data.commissionInvalid} 个商品按当前规则算不出佣金，已跳过。</Notice>}
     {(linkStates.read_incomplete??0)>0&&<Notice tone="warning">有 {linkStates.read_incomplete} 个商品所在活动这次没读到：按未判定处理，不会当成"没有卡"。</Notice>}
     {(linkStates.review??0)>0&&<Notice tone="warning">有 {linkStates.review} 条旧口径记录等待按标准链接规则重新检查。</Notice>}
    </>}
    <div className="grid gap-4 lg:grid-cols-3">
     <Field label="每次读取条数" hint="非全托一次就把未判定读完，通常点一次就够。">
      <Input type="number" min={1} max={200} value={jobs.draft.linksCampaign?.readLimit??15}
       onChange={e=>jobs.setDraft("linksCampaign",{...jobs.draft.linksCampaign,readLimit:Number(e.target.value)})}/></Field>
     <Field label="每批新建条数" hint="0＝只查不建。填了就是平台写入：完全一致的标准卡不会重复建，其余按上限补建。">
      <Input type="number" min={0} max={200} value={jobs.draft.linksCampaign?.creates??0}
       onChange={e=>jobs.setDraft("linksCampaign",{...jobs.draft.linksCampaign,creates:Number(e.target.value)})}/></Field>
     <div className="flex flex-wrap items-end gap-2">
      <Button size="sm" variant={(jobs.draft.linksCampaign?.creates??0)>0?"primary":"outline"}
       disabled={jobs.busy!==null||Boolean(linkRun?.running)}
       onClick={()=>void jobs.start("linksCampaign").then(()=>links.reload()).catch(()=>{})}>
       {linkRun?.running?"准备中…":(jobs.draft.linksCampaign?.creates??0)>0?`准备链接并新建（≤${jobs.draft.linksCampaign?.creates}）`:"准备链接（只查不建）"}</Button>
      {linkRun?.running&&<Pill tone={(jobs.draft.linksCampaign?.creates??0)>0?"warning":"brand"}>{(jobs.draft.linksCampaign?.creates??0)>0?"平台写入进行中":"只读检查中"}</Pill>}
      {linkRun?.running&&<Button size="sm" variant="outline" disabled={jobs.busy!==null} onClick={()=>void jobs.stop("linksCampaign").then(()=>links.reload()).catch(()=>{})}>停止</Button>}
     </div>
    </div>
    {jobs.message&&<Notice tone="info">{jobs.message}</Notice>}
    <p className="text-xs leading-5 text-gray-400">
     新建非全托链接是<b>平台写入</b>：每个商品先冻结意图再提交，提交后逐条回读确认；旧卡不删除、不复用，结果未知按原意图回查，绝不重复提交。
     上限填 0 时这个按钮只查不建。
    </p>
    {/* 卡名来源：缺短名就会退化成截断标题，所以这一步必须和"新建链接"放在一起，
        否则链接是建出来了，名字却不是我们要的那个。 */}
    <ShortNames market={market}/>
    </div></Card></div>
  </Section>

 </div>;
}
