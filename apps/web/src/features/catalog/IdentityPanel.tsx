"use client";
import {Button,Card,Field,Input,Notice,Pill,Progress,StatTile} from "../bdhub/ui";
import type {IdentityProgress} from "../../server/identity-queue/bridge";
import type {IdentityController} from "./useIdentityQueue";

// Why a backfill ended. Each one has a different next action, so a raw code would tell the operator
// nothing -- the same reason the link and lead batches translate their stop codes.
const STOP:Record<string,string>={backlog_clear:"待补的 handle 已经补完。",nothing_pending:"没有待补的 handle。",limit_reached:"达到本次上限，剩下的下次再补。",stopped_by_operator:"你请求了停止，跑完当前这一轮就结束了。",queue_stalled:"连续两轮没有领到可查的 handle（多是被账号挡住的），先停下等下次。",round_limit:"到达轮次上限，剩下的下次再补。",round_timeout:"某一轮超时，已保留断点。",round_output_invalid:"抓取进程没有返回可读结果，已停下。",account_not_startable:"采集账号暂不可用。",maintenance_due:"采集账号维护中。",shared_backoff:"平台正在退避，稍后再试。",verification_required:"平台验证尚未完成。",identity_policy_unreadable:"读不到账号的已发布通道配置（数据库正被别人占用）。没有拿 3 QPS 默认值冒充它，稍后重开即可。",published_identity_policy_invalid:"账号上发布的通道配置与它的验收证据对不上。这不是网络问题，先不要跑。",internal_error:"抓取进程碰到了没预料到的错误，按纪律直接停下、没有继续猜。异常类型和本机 traceback 位置见下面那一行（更早的记录里可能没有）。"};

// The driver's captured explanation of a failed round. It is deliberately a one-line marker naming
// the exception type and pointing at the local log: a bare `internal_error` cannot be acted on, but
// raw exception text can carry credentials or a remote body and must not travel through the API.
function failureDetail(progress:IdentityProgress){
 const last=progress.errors?.at(-1);
 if(!last?.detail)return null;
 const lines=last.detail.split("\n").map(line=>line.trimEnd()).filter(Boolean);
 return {round:last.round,text:lines.slice(-3).join("\n")};
}

// 「被挡住」的原因：这些都不是"平台上没有这个人"，而是**我们没拿到回答**，所以能重试。
const BLOCKED:Record<string,string>={"request_or_signer_error":"请求构造或签名失败","account_not_startable":"采集账号起不来","account_not_prepared":"采集账号未就绪","worker_interrupted":"抓取进程被中断","remote_error":"远端错误","maintenance_due":"账号维护中","shared_backoff":"平台退避中","probe_timeout":"查询超时","probe_failed":"查询失败","probe_report_missing":"抓取进程没交结果","probe_report_invalid":"抓取结果不可读","identity_import_failed":"身份写入失败","runtime_unavailable":"运行环境不可用"};

// Only a stop that needs the operator's attention is spelled out. The routine "batch finished,
// N found, M not found" narration was removed on request; the counts are in the tiles and the bar.
const ROUTINE=new Set(["backlog_clear","nothing_pending","limit_reached","stopped_by_operator","round_limit","queue_stalled"]);
function faultReason(progress:IdentityProgress,running:boolean){
 if(running||!progress.stopReason||ROUTINE.has(progress.stopReason))return null;
 return STOP[progress.stopReason]??`停在 ${progress.stopReason}。`;
}

/**
 * The gate between a Kalodata handle and the sending pool.
 *
 * A position in the pool only exists once a Find returned the platform OECID, so this stage is not
 * a column the pool forgot to show -- it is the step that decides whether a lead can ever be sent.
 */
export default function IdentityPanel({controller}:{controller:IdentityController}){
 const {data,draft,setDraft,busy,message,save,start,stop,loaded}=controller;
 if(!data?.available)return <Card title="达人身份（OECID）"><div className="p-5"><p className="text-sm text-gray-500">{!loaded?"读取中…":data?"这个活动还没有可解析的线索。":"暂时无法读取身份进度。"}</p></div></Card>;
 const progress=data.run?.progress??null;
 const running=Boolean(data.run?.running);
 const stopping=Boolean(data.run?.stopping);
 // The bar's denominator is the batch actually planned (ceiling vs backlog), never the raw ceiling:
 // a 2,000 ceiling over a 1,100 backlog would otherwise never reach the end.
 const batchTarget=progress?Math.min(progress.limit,progress.pendingAtStart):0;

 const by=data.byCreator;
  // 进度读数：一轮只领 20 个、分母上千，光看一根条子会以为"根本没动"。所以把轮次、已用时间、
  // 实测速率与预计剩余一并摆出来——这才是操作者真正想知道的"它有没有在走"。
  const elapsedSeconds=progress?Math.max(0,(running?Date.now()/1000:progress.updatedAt)-progress.startedAt):0;
  const perSecond=progress&&elapsedSeconds>0?progress.claimed/elapsedSeconds:0;
  const remaining=batchTarget-(progress?.claimed??0);
  const etaMinutes=perSecond>0?Math.ceil(remaining/perSecond/60):null;
  const minutes=(value:number)=>value<1?"不到 1 分钟":`约 ${Math.round(value)} 分钟`;
  const roundSeconds=progress?.roundStartedAt?Math.max(0,Date.now()/1000-progress.roundStartedAt):null;
  const done=progress?.claimed??0;
  const percentOfBatch=batchTarget>0?Math.floor((done/batchTarget)*100):0;
  // 停止原因**一律**写出来（包括"你请求了停止"这种常规的）：条子停在 40/1948 时不说为什么停，
  // 看上去就像任务卡住了。
  const stateLine=running
   ?(progress&&progress.retry>0
     ?`账号通道配置那个库正被别人占用，已重试 ${progress.retry} 次，还在等——不是卡死。`
     :progress?.roundRunning?`第 ${progress.roundRunning} 轮进行中${roundSeconds!=null?`（本轮已 ${Math.round(roundSeconds)} 秒）`:""}`:"正在准备第一轮…")
   :progress?.stopReason?(STOP[progress.stopReason]??`停在 ${progress.stopReason}。`):"当前没有在跑。";
 return <Card title="达人身份（OECID）" subtitle="Kalodata 只给 handle；这里把它换成平台身份。换不到的线索不会进发送池，也不会被伪造一个 OECID。">
  <div className="space-y-4 p-5">
  {/* 这张卡**只以达人（去重 handle）为单位**。一个达人名下可以挂很多条线索，所以三个分类必须互斥，
      否则同一个达人会在"已就位"和"待补"里各算一次——实测那样会多算 1,100+，主数字就不可信了。 */}
  <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
   {by?<>
    <StatTile label="已就位（可进发送池）" value={by.resolved} hint={`可发位置 ${by.positions.toLocaleString()} 个（达人×商品）`}/>
    <StatTile label="搜索不到（不进池）" value={by.unresolved} hint="平台明确说没这个人；记录保留、不自动重试"/>
    <StatTile label="被挡住（可重试）" value={by.blocked+by.unknown} hint="问过但没拿到平台的真实回答；这一批会被重试" brand/>
    <StatTile label="达人合计" value={by.handles} hint="已就位＋搜索不到＋被挡住＋从没提交，四项互斥"/>
   </>:<>
    <StatTile label="已就位（可进发送池）" value={data.resolvedCreators} hint="按去重 handle"/>
    <StatTile label="被挡住（可重试）" value={0} hint="暂时读不到分类，先别据此判断" brand/>
    <StatTile label="搜索不到（不进池）" value={data.unresolvedCreators} hint="按去重 handle"/>
    <StatTile label="达人合计" value={0} hint="分类读不到时不显示一个自己都对不平的总数"/>
   </>}
  </div>
  {/* **当前进度**：只要这次运行存在就显示——包括第一轮还在跑、进度刚写下 0 的时候。
      以前只在"有进度且分母>0"时渲染、驱动又只在轮末发布，于是点了按钮看到的是一根 0% 的空条。 */}
  {batchTarget>0&&<div className="space-y-2">
   <Progress done={done} total={batchTarget}
    label={`当前进度（已领 ${done.toLocaleString()} / 本次计划 ${batchTarget.toLocaleString()}，${percentOfBatch}%）`}/>
   {/* 这四个读数是文字（"2.4 个/分"、"约 12 分钟"），StatTile 只收数字，所以用同样的外观自己排。 */}
   <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
    {[
     ["轮次", String(progress?.rounds??0), `每轮 ${progress?.cohortSize??data.config.cohortSize} 个 handle`],
     ["速度", `${perSecond>0?(perSecond*60).toFixed(1):"0"} 个/分`, elapsedSeconds>0?`本次已用 ${minutes(elapsedSeconds/60)}`:"刚开始"],
     ["预计剩余", etaMinutes==null?"—":minutes(etaMinutes), remaining>0?`还差 ${remaining.toLocaleString()} 个`:"本次计划已跑完"],
     ["本次结果", `找到 ${progress?.found??0}`, `搜索不到 ${progress?.notFound??0} 个达人`],
    ].map(([label,value,hint])=><div key={label} className="rounded-xl bg-brand-50 p-4 dark:bg-brand-500/10">
     <p className="text-xs text-gray-500">{label}</p>
     <p className="mt-2 text-xl font-semibold tabular-nums">{value}</p>
     <p className="mt-1 text-xs leading-5 text-gray-400">{hint}</p>
    </div>)}
   </div>
   {/* 为什么"已领"不等于"找到＋搜索不到"：已领数的是**线索条数**，上面两个数的是**达人个数**。
       一个达人名下可以挂很多条线索（实测有 13 条的），平台按 handle 判定、池位按线索记账。 */}
   {data.walk&&progress&&<p className="text-xs leading-5 text-gray-500">
    本次已判定 <strong>{data.walk.settled.toLocaleString()}</strong> 条线索：
    其中 <strong>{data.walk.newHandles.toLocaleString()} 位</strong>是全新达人，
    另 <strong>{data.walk.repeats.toLocaleString()} 条</strong>是同一位达人的其它线索（达人身份已存在，只是这条线索要自己的判定）。
   </p>}
   <p className={`text-xs leading-5 ${running?"text-gray-500":"text-gray-400"}`}>{stateLine}</p>
   {running&&progress?.rounds===0&&<p className="text-xs leading-5 text-gray-400">
     第一轮要几十秒：跑完第一轮才会出现轮次与速度——它没有卡住，只是还没跑完一轮。</p>}
   {progress&&faultReason(progress,running)&&<p className="text-xs leading-5 text-warning-600 dark:text-warning-400">{faultReason(progress,running)}</p>}
   {/* 真实线索：`internal_error` 只是一个"我们不知道"的兜底码，异常类型＋本地日志位置才是可查的。 */}
   {!running&&progress&&failureDetail(progress)&&<div className="rounded-xl bg-gray-50 p-3 dark:bg-white/[0.03]">
     <p className="text-xs text-gray-500">出错的那一轮（第 {failureDetail(progress)!.round} 轮）留下的线索：</p>
     <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-all text-xs leading-5 text-gray-600 dark:text-gray-300">{failureDetail(progress)!.text}</pre>
    </div>}
  </div>}
  {running&&!progress&&<Progress done={0} total={1} label="已启动，正在准备第一轮…"/>}
  <p className="text-xs leading-5 text-gray-500">{by&&by.reconciled
   ?`${by.handles.toLocaleString()} 个达人 ＝ 已就位 ${by.resolved.toLocaleString()} ＋ 搜索不到 ${by.unresolved.toLocaleString()} ＋ 被挡住 ${(by.blocked+by.unknown).toLocaleString()}。（按去重 handle，四项互斥）`
   :"四个分类的合计暂时对不上达人总数，先别据此判断还剩多少。"}</p>
  {/* 「被挡住」不是一个含糊的状态：把原因摊开——大多是请求/签名失败或账号起不来，
      也就是**根本没拿到平台的回答**，所以必须重试，而不是当成"找不到"或"没问过"。 */}
  {by&&(by.blocked+by.unknown)>0&&<p className="text-xs leading-5 text-gray-500">
   被挡住的原因{by.blockedReasons.length?"（按达人）":""}：
   {by.blockedReasons.length
     ? by.blockedReasons.map(r=>`${BLOCKED[r.reason]??r.reason} ${r.count}`).join(" · ")
     : "状态文件里没有留下原因"}
   。点「开始补 OECID」会重试这一批；同一项连续 3 次都被挡住就不再自动重试，留在这一列等处理。
  </p>}
  {/* 位置＝达人×商品。一位达人有了 OECID，他名下的所有线索商品就都是可发位置——平台回答的是
      "这个 handle 是谁"，与商品无关，所以不需要每个商品各自再查一次身份。 */}
  {by&&<p className="text-xs leading-5 text-gray-500">
   可发位置 <strong>{by.positions.toLocaleString()}</strong> 个（达人×商品）＝ 已就位 {by.resolved.toLocaleString()} 位达人 × 他们名下的线索商品，
   去重后得到。达人身份**一位查一次**：找到就覆盖他的全部线索商品，找不到就整位归"搜索不到"，不再重查。
  </p>}
  <div className="grid gap-4 lg:grid-cols-2">
   <Field label="一次补多少（上限）" hint="从待补里按顺序取这么多个达人。这是上限不是目标——待补不够就少补。"><Input type="number" min={1} max={200000} value={draft?.batchSize??data.config.batchSize} onChange={e=>draft&&setDraft({...draft,batchSize:Number(e.target.value)})}/></Field>
   <Field label="每轮查几个 handle" hint="一轮＝一次平台查询。已验收的通道是 20 个一组，改小只会更慢。"><Input type="number" min={1} max={20} value={draft?.cohortSize??data.config.cohortSize} onChange={e=>draft&&setDraft({...draft,cohortSize:Number(e.target.value)})}/></Field>
  </div>
  <div className="flex flex-wrap items-center gap-2">
   <Button size="sm" variant="outline" disabled={busy} onClick={()=>void save()}>{busy?"保存中…":"保存设置"}</Button>
   <Button size="sm" disabled={busy||running||data.pendingCreators===0} onClick={()=>void start()}>{running?"补身份中…":"开始补 OECID"}</Button>
   {running&&<Button size="sm" variant="outline" disabled={busy||stopping} onClick={()=>void stop()}>{stopping?"本轮到点就停…":"停止"}</Button>}
   {running?(stopping?<Pill tone="warning">已请求停止</Pill>:<Pill tone="brand">查询中</Pill>):data.pendingCreators===0?<Pill tone="success">没有待补</Pill>:<Pill tone="neutral">就绪</Pill>}
  </div>
  {message&&<Notice tone="info">{message}</Notice>}
  </div>
 </Card>;
}
