# BDHub-Agent 技术文档

当前实现合同，按 2026-09-26 代码基线 `d3bcc5a` 核对。产品规则见 [PROJECT.md](PROJECT.md)，运行快照见[当前交接](handoff/current.md)。本文保留模块入口、状态边界及恢复方法，实验数值与事故流水只链接证据。

## 1. 架构与环境

```text
React feature → App Router Route → server bridge → 固定 argv Python CLI
                                                       ↓
持久 worker ────────────────────────────────────── scripts/lib
                                                       ↓
                                      SQLite / 平台适配 / 账号锁与 lease
```

Web 使用 Next.js 16、React 19、TypeScript 5.9，Node ≥22.18.0；业务使用本项目 Python 3.13 `.venv`、SQLite。依赖分别固定在 `apps/web/package-lock.json` 与 `requirements.lock`。服务仅监听 `127.0.0.1:5198`。

- `apps/web/src/features/`：页面与交互；不访问 SQLite、不实现资格规则。
- `apps/web/src/app/`、`server/`：本机 Route、固定 argv bridge、输入输出校验与错误映射。
- `scripts/`：CLI/worker；`scripts/lib/`：业务合同、台账、调度和适配。
- `config/`：业务参数和敏感配置样例；`var/`：真实状态/证据；`vendor/`：本仓协议闭包。
- `tests/` 与 `apps/web/tests/`：状态机、恢复、API 和显示合同。

Web 路由与 bridge 显式携带 market，URL/body/CLI/返回值一致；约 10 个 CLI 的 `--market` 仍缺省为 `it`，手工调用必须显式传参。`markets.json` 是唯一市场注册表，未验收能力拒绝动作，空市场不回退 IT。注册表只登记 `campaignCatalog`、`fullManagedCatalog` 两个能力位；TapLink 清理、会话页人工文本/卡片、身份队列/精确发现/画像刷新、Kalodata 身份和全托作业页目前只在 IT 实现，由代码直接拒绝其他市场。自动 A/B 线索读取、身份衔接与发送已支持 IT/BR/MY/UK，不能由手动页面入口限制推断后台未接入。首页使用 `market_read_head → market_read_generation` 只读快照，同市场 GET 由 singleflight 合并；缺失/过期才本地聚合。快照失败不修改 workflow 结果，筛分摘要必须匹配当前来源 head。

## 2. 领域模块与合同

### 2.1 货盘与选入

| 入口/模块 | 职责 |
| --- | --- |
| `global-source-control.py` / `global_source.py` | 全托 run、分页、分区、覆盖与 head |
| `global_selection.py` / `global_selection_fast.py` | 持久选入队列、单 PID 提交、批后回读 |
| `campaign-join.py` / `campaign-collect.py` | 加入意图、未知核验、已加入列表完整采集 |
| `global_screen.py` / `campaign_screen.py` / `catalog_binding.py` | 商品筛分、Offer 指纹与唯一当前绑定 |

来源完整发布才推进下游，既有用户明确接受的 `accepted_partial` 保留覆盖缺口。类目采集按一级类目保存页码、reported total、唯一数和停止原因，完成后跨类目 PID 去重。历史 `coverageOverlay` 继续兼容：它保存类目基线和普通查询重叠更新证据；新常规发现不再创建每周普通查询。catalog 阶段沿用 `operations_policy.selected_source_run_id` 的原 workflow/source 命名，已完整发布的同轮类目或普通来源直接复用；未完成采集优先续原类目/页码，已停止任务不自动恢复。

`global_source_candidate` 在原来源库中按 market×PID 保存一次入池证据；`global_candidate_publication` 与 screening 在同一事务发布新增数。新轮只新增首次合格 PID，已纳入的商品不因本轮缺席、销量或评分变化被覆盖或删除。保存商品快照、来源/类目/页证据、规则及观测时间；候选本身不表示已选入或可发送。新采集的评分 0 记 `rating_unverified`，不解释为无评分；历史已录入的资格原样保留。`fullmanaged-candidates.py migrate --market <market> --confirm` 是备份后的增量幂等迁移入口：从既有 screening 回填，旧快照已轮换时只用原 intake 的冻结快照/规则补证，缺失分页明确留空、不编造；不会新建选入任务或调用平台。

`intake_candidate_owner` 指向每个 PID 的原选入条目，`intake_run_member` 让后续轮次引用原任务，不复制未知回执、不重置 pending/成功/明确失败/隔离状态。同一原申请的旧复制记录按冻结 attempt 与最终观测归并引用，原行均保留；不相关的未知申请不能被新的 pending 覆盖。资格入池与入队分两步，但入队、owner 和当前轮成员同事务，崩溃后幂等补齐。当前选入前的 `promotion_assessment` 只查当前推广状态和佣金，不再常规重查历史销量/评分；已选池及冻结 Campaign 回执合同不放宽。

分页重复先保存 repair page 回执再补洞/复读，符合 PROJECT 稳定重复行合同后才能发布。总数变化且无重复页可补时停止为空转错误；`--retry-partial-category` 仅重读原未发布分区。`accepted_partial` 必须由明确操作写入 `global_source_operator_acceptance`，API 不得显示 complete。

IT、UK 及已验收全托市场首次按一级类目发现，之后从上次完整/已明确接受的类目来源完成时间起每 15 天到期（`operations-policy.json.fullManagedCategoryRefreshDays`）。普通覆盖层不重置该时钟；未到期的材料维护复用已发布来源与累计候选，仍核对当前已选池/材料。BR/MY 不因该规则开启全托；恢复仍使用原 run，已被用户停止的类目不自动复活。

选入当前使用已验证单 PID `/pick_up/select`，不是未验收的 batch_select。先落意图，批后统一回读；验证码/登录明确拒绝，须同时有已选池缺失、当前 listing 未选入及同账号验证/新代次证据，才能重放同一冻结请求。网络歧义/code0 缺回读不重发，仍未知保留 `skipped_unknown`，后续 generation 不重新入队。提交的选入意图只由该冻结 campaign 的回读证据确认（`matching_selection_evidence`）；已在池中（任意 campaign）的 pending 商品记为 `already_selected`。若池中只在另一 campaign 下出现该商品，记录 `otherCampaignObserved` 与 `otherCampaignReads`、不记缺失；这样的回读相隔 5 分钟以上出现两次即改为 `isolated_unverified`（`isolation.reason='selection_campaign_mismatch'`，证据保留、不建链接，`prepare` 不再入队），catalog 阶段继续（用户 09-25 决定）。`verify` 首次遇到时最多等满 5 分钟再回读一次；告警条提示近 7 天内隔离的选品数。真实性能证据见[四市场报告](implementation/four-market-launch-implementation-20260923.md)。

Campaign 新申请按项提交，未知不截断其他活动或后续 100 项批次；明确账号/认证/验证故障停止继续 POST，旧已加入货盘仍独立尝试完整采集。catalog 阶段先采集/筛分确认范围；若有未知项，先用现有 TapLink 执行器准备确认范围，再 `campaign-join.py verify --bounded`，之后才发布本轮 catalog generation。核验新确认的活动触发完整列表/商品回读；后续正式 TapLink 阶段幂等复用已备材料，不再次加入活动。加入错误、未知/停止跟进范围单列在 generation scope/payload；已加入列表不完整仍不发布或筛分，不能冒充空货盘。未确认申请缺席不退役其原活动/返回子活动已有绑定，明确商业事实改变仍执行材料门禁。

Campaign 核验最多首次加 3 次补查，同市场整批共享 300 秒墙钟期限（从首次专项核验入口起，含初始化、锁、请求、分页及验证）；次数在请求前落盘，SIGALRM 限制专用 CLI 的阻塞读取，重启/跨日不重置。没有长时间等待或后台专项轮询；全部确认提前结束，耗尽保留真实 unknown 和停止跟进状态，不建人工事项、不重发。普通预览中原活动/返回子活动准确出现可被动结算。CLI `verify` 单轮与自动 `--bounded` 共用同一预算，已耗尽或无待核验项不访问平台。Web 单轮回查超时为 330 秒，给本模块的 300 秒核验与落账留余量。页面将剩余核验和停止跟进分开展示。

`campaign-join.py migrate --market <market> --confirm` 显式为原条目表追加请求 JSON、核验次数、截止时间、停止跟进位、脱敏读错码五列，事务化且幂等；先备份再升级旧库，写路径缺列拒绝启动。旧记录默认请求 JSON 为空，不伪造历史请求。status 使用 `mode=ro`，无库不创建，兼容迁移前读取；同市场 preview/apply/verify 使用本机互斥锁防止覆盖在途意图。新的冻结请求在 POST 前与 writing 同次落盘。当前迁移入口独立于只覆盖部分库的通用 registry。

### 2.2 TapLink 与当前材料

`catalog_links.py` 保存创建意图，`catalog_prepare.py` 和 `catalog-link-batch.py` 驱动准备，`catalog_clean.py` 核验清理，`link_naming.py` 读取市场短名。读链、创建、清理共享同一账本；`creates=0` 只读。

`catalog_current_binding` 是发送唯一材料投影，键覆盖 market/PID/来源/Campaign/分佣与命名指纹，保存 `currentListId`。普通历史卡不参与当前复用；仅同标准、同绑定且已核验卡幂等复用。完整 Campaign 筛分后 `reconcile_current_offers()` 将不再合格绑定 inactive、条款变化 waiting_refresh，全部写 event。

短名按 `cycle_product_name.locale` 校验，非 IT 不读 IT 缓存或截断标题；缺失停止创建。IT 准备顺序 seed/read → names → create，其他市场 seed → localized names → read → create。新建回读未知隔离该 PID，整批写完公共回读，再按 30/120 秒轮询两次；仍缺失只保留 unknown，不重复 POST。在 `step_create` 中，若实时 offer 在尝试创建前已不再合格（`product_no_longer_eligible`）或其商业事实变化（`commercial_facts_changed:*`），`retire_or_block` 会取代已准备的意图（`catalog_link_intent.state='superseded'`，回读记录原因与 0 次创建尝试）并退役 prepare 项（`retired`，不计入 summary 计数）；已尝试/unknown 的意图保留其回读路径；若退役失败则按原样阻断该项，错误中带 `retire_failed`。

清理只删除整卡平台 invalid，内部期限/佣金门槛仅本地停用。`taplink_clean` 按 `jobs.json` 中 `taplink_clean.weekday`（作业页可改，默认周一，所有市场共用）排期：IT 执行账号库存 refresh/classify/delete；BR/MY/UK 只运行 `catalog-clean.py check-bindings --market`，经本市场供给账号只读重读每个 active binding 自己的卡成员，仅在商品不在卡内、`product_status≠2`、被治理或非 8 类不可用时把该 binding 转 `waiting_refresh` 并记证据，读取失败保留原可信 binding；非 IT 不复用 IT 账号库存表，远端删除不开放。标准卡判定（`classify_pid`）要求名称/creator 佣金精确匹配且平台有效、商品合格；同形但平台报不可用的卡返回 `ineligible`，落 `review` 并阻止替代建链，同一卡已是 active binding 时转 `waiting_refresh`。删除收口后完整回读，仍存在记 failed_known，不自动重复 DELETE。发送端本地核对 binding/Offer/currentListId，不调用 fresh_card。

### 2.3 工作流与调度

`operations_workflow.py` 保存不可变 run/stage/generation/checkpoint，`operations_scheduler.py` 执行和监督。开关来自市场 durable setting，共享供给时间来自 `jobs.json`，市场发送/Agent 窗口来自各自持久控制。`jobs.json` 各阶段的 `enabled` 已无读取方；`inbox_monitor.enabled` 仍可单独拉起 IT 收信（与自动运营总开关任一为真即启动），`continuous_send.enabled` 被 `jobs.save` 拒绝修改。`taplink_clean` 只在 `jobs.json` 所选维护日进入 run，且同一市场当天已有一次成功清理后，当天后续 run（如货盘失败后的重试）跳过该阶段；失败的清理不计。全托发现每 15 天到期，与 `taplink_clean` 原每周维护分别计算：维护到期但发现未到期时复用已发布来源，不重扫全市场。持久开关 `fullCatalogWeeklyEnabled` 保留字段名及原值，页面改称“全托商品发现”；`full_catalog_update` 显示固定天数间隔，旧无效时刻字段保留兼容但拒绝再修改。

- 同市场按上游 generation 串行。TikTok 平台上的重型阶段（catalog、taplink_clean、taplink_prepare、oecid）额外占用全局槽 `platform:global`，容量取 `operations-policy.json.platformMaxParallelMarkets`（1–2，2026-09-27 起为 2），所以同一时间最多两个市场做平台重型读取，其余市场排队；同一账号仍由各自 supply/communications 槽保证不并用；Kalodata 用自己的 `kalodata:global` 两槽，可与另一市场的平台阶段并行。排队顺序按市场轮流：最久没领到阶段的市场先领（`workflow_dispatch._last_served`），同等时有断点的先续；不再按“该阶段最近一次成功”排序（那会让新开市场从未成功过的阶段一直抢先）。正在运行的阶段不会被中途让出。
- `workflow_stage_claim/workflow_resource_slot` 短事务领取 owner/fence/300 秒 lease，执行每 30 秒续租；确认 owner 已退出才回收。子进程先登记真实 PID 再执行，父死子活仍占槽；子进程结束后 claim 的 PID 改回监督进程。阶段结算只要求本 owner 的 fence 仍持有 claim（`assert_owned`），租约过期但未被回收时照常结算；已被回收的迟到结果写入 `workflow_late_result` 作为待核对证据，不发布 generation。过期回收时，可写平台的 taplink_clean/catalog/taplink_prepare 转 `needs_human`（`recovery_claim_expired_write_unknown`，写入证据记 uncertain），只读阶段才回到 queued 从断点续。读模型投影在监督循环旁的线程里跑，不阻塞续租与结算。
- 锁占用、无效 CLI 输出、部分范围失败、pending、stuck 不能报 completed。外层平台写入数汇总内部 pass，item count 取最终 summary，不能把重复 pass 累加。
- 仅零平台写入、无 unknown/unresolved/ambiguous、claim 已释放的失败阶段，才在原 run/上游 generation 间隔 ≥1 小时重排，最多三次；不重跑完成的上游，不依赖来源下一次到期。其余转 needs_human。可写平台的 taplink_clean/catalog/taplink_prepare 另需阶段 `counts.writeEvidence='zero'`：执行器按阶段累计每个子进程报告的写入，子进程已放行后 stdout 丢失/损坏、超时或异常记 `uncertain` 并保留报告中的写入数；旧记录没有该证据也不视为零写。会写平台的子命令必须在报告中给出整数 `platformWrites`（或 `writeEvidence`），缺失、非整数即 `uncertain`；只有按 CLI 合同只读的命令（`_zero_write_contract`，如 select verify、catalog-clean check-bindings、`--creates 0` 的建链批次）可以缺省为零。批处理报告内任何失败/无结果的子步骤都使阶段失败（`nested_<label>_<state>`），`catalog-link-batch.py` 另输出顶层 state/writeEvidence。只读阶段（kalodata/oecid/send_pool）维持原规则。
- OECID 阶段在 `counts.identityAccount` 记录实际执行账号；`rolling_leads.identity_hold` 只用该账号（旧记录用同一 `identity_account` 解析）的新身份代次或更晚成功的 OECID 阶段解除等待。四市场 `market-identity.py` 有界切片（含 IT 的 `account_wait`/`nothing_pending`）走同一归一化，仍核对命令市场、零写入、零发送。
- 全托选入：登录故障（16201010）、非 PID 级错误或未决 PID 超过 3 个仍 needs_human；否则只冻结台账中列出的未决 PID（原意图不重发），其余 PID 继续选入、Campaign 继续，catalog 代次 scope 带 `selectionGap`（未决 PID、原因、`partial_selection`）。可容忍失败依子进程 `states.pending` 终止：为 0 即带缺口结束，未下降记 `global_selection_no_progress`，读不到进度记 `global_selection_progress_unknown`，不再空转到迭代上限。
- 恢复短名或 Kalodata 漏参预检需精确校验原错误、原阶段、零写入、当前证据和 claim，恢复事件写原 checkpoint；专用入口不得用于已实际执行的失败。
- 常驻 scheduler 在长任务未完成时持续重读开关/到期/重试并补领空闲槽，最多每 30 秒检查；没有 future 完成也刷新 checkedAt/runningStages 和租约。--once 保持单批边界；停止后不再补领，已在执行的原阶段先收口。完成发布在事务内再次核对 owner/fence，迟到旧 owner 不得发布。长任务等待仍监督收信、发送、Agent 和账号维护，普通失败退避与 unknown 核验分开。

`workflow_recovery.py` 对原选入轮次和后起零写重复轮次核验 schedule/config、原 upstream generation、已发布 source/overlay 与精确选入回执；回读结算（`global_selection.matching_selection_evidence`）接受冻结活动，或平台新建的全托子活动（type 8/9）且总佣金与提交时相同（记 `platformAssignedCampaign`）；其余活动失配仍拒绝，除非操作者用 `isolatePids` 明确隔离：该项改为 `isolated_unverified`、原证据保留，链接准备（只取 confirmed）本轮不再使用；此类隔离可经用户确认后用 `select-global-products.py restore-isolated --confirm-restore-isolated` 只读复核，已选池总佣金一致的恢复为 confirmed（保留 `restoredFromIsolation`），不重新提交。设置检查要求自动运营与全托周更都开启、revision 不低于原轮（暂停后重开只改 revision）。恢复保留原请求与累计写计数，以幂等 checkpoint 重排原 catalog，不把其它 source 改算本轮成功。`resume-video-author-skip` 只在 IT Kalodata 阶段因 `kalodata_video_author_missing` 转人工、且最新一代视频扫描正是在该阶段运行期间以同一错误停下时，把该阶段重排一次；扫描从原代次断点续跑，换请求号的第二次恢复被拒绝。`resume-kalodata-auth` 对任一市场因 `kalodata_auth_required` 转人工的 Kalodata 阶段做同样的一次性重排，前提是停下之后已有 Kalodata 页面读取成功（共用同一 Kalodata 登录）；A 类队列持久，续跑只处理仍到期的 PID。`resume-after-fix` 对自动重试已用完（`workflow_retry_exhausted`）的轮次，把第一个失败阶段重排一次，前提是该阶段零平台写入、失败码在 `FIXED_STAGE_ERRORS` 白名单内（原因已在代码中修复，目前为 OECID `market_identity_report_invalid`、已修复的本地交接 `oecid-submit_report_invalid` 与 Kalodata `kalodata-video-run_report_invalid`）。本地交接零写入错误修复后可在 failed 状态直接请求一次原阶段恢复，无需先耗尽按小时重试；原上游 generation、无其他活动 run/claim 等条件仍必须通过。实际恢复前需核对运行进程并加载对应 scheduler，检查通过不产生新授权。

### 2.4 线索与身份

`rolling_leads.py` 与 `rolling-leads.py` 是正式 A/B 持久读取入口，`lead_query_task` 按 market×PID×类型保存一份活动查询，冻结查询 ID、窗口、版本和 market/region/currency。A 的 `rolling-a50-v1` 范围最多 50 位正销量达人，按数值 GMV、来源排名确定性选择并按 creator/handle 去重；原 20 位发布结果不改判为读过 50。已实际开始的旧 A 页回执按原窗口和原范围续读，随后独立排入 A50 新查询。已完整结果保留直到新范围发布；每 PID 原子发布，未完成页不覆盖 head。

只将当前资格合格且有相符有效标准 binding 的 PID 入队；校验市场、命名/分佣版本、来源/Campaign/Offer 指纹，不把所有历史候选无条件查询。新/到期项按有效等待起点、商品历史销量、PID 排序；A/B 轮转，各类型内部先续断点，再给新/到期项一次机会，空侧可借用。轮转类型、内部阶段及 service_order 持久化，重启不回到固定队头。每片段最多 3 个网络请求，单次 workflow Kalodata 阶段最多 8 片段；这是让出资源的处理片段，不是每日视频数/请求数的业务截断。分页、详情及作者缓存继续复用原台账。

A 14 天、B 30 天窗口仍截至北京时间两天前。7 天刷新锚定冻结查询开始时间，晚完成不无限顺延；旧窗口原样读完、记录真实日期，已陈旧（7 天）的完成后马上具有新窗口资格。额度耗尽只暂停对应市场，保留页和排队时间、不计 PID 失败；当前接口没有已验证的精确重置时间，按小时用原待办检查，成功后续跑，不假定零点恢复。认证/初始化错误等待通道恢复，浏览器锁忙短退避，均不把全市场 PID 判失败。单 PID 技术失败按 5/30 分钟退避，累计三次（含首次）隔离；7 天后或标准材料实质变化才获得新范围机会，不按次日自动清零。

schema v21 增加视频 generation/run 的市场归属与请求 region/currency，head/current/author_cache 采用市场复合键；原 IT 三表完整保留为 `_legacy_it_v20`，原不可变视频回执不重写。旧 B 已开始项保留原 generation/窗口/页；仅旧批次中未真正读取的排队项在实际领取时冻结新窗口。已有更新完整结果覆盖的旧断点直接记录 superseded，不继续请求。B 的视频首日边界必须读到严格早于窗口起点（或短页）才结束，避免漏掉同一天跨页的视频。作者缓存按市场复用，复用不刷新为新的平台核验时间。

B 全窗口读取完成后，将回执/head/current/job 同事务发布；单条缺作者仍沿用明确缺口记录，过多缺口只影响对应 PID。`video_identity.py` 把当前 B 代表视频接到既有 source/身份台账，`current_identity_source` 汇合当前 A/B；不同市场同 PID/handle/videoId 不互借结果。A/B 同达人×PID 合并为 A、保留视频证据；B 按当前 30 天发布时间资格消费，未知作者不可发送。非 IT 的新来源边复用同市场已验证成功/有效未匹配结论，不因换 PID/窗口重查。

调度在没有新货盘任务时也可从 kalodata 开始一个有界续跑，然后衔接可完成的身份/发送池；沿用 workflow claims、fence、市场互斥与 Kalodata 全局两槽。已失败身份阶段单独等待原恢复证据（成功身份阶段或新的所选身份查询账号代次）；已解析线索和 A/B 读取可继续，原失败 run/checkpoint 不改判成功。页面手动推进也经同一 workflow 调度，不另开无资源约束的消费者。身份技术失败预算与隔离已接入，80/20 发送已接入 §2.5 的正式候选入口；二发有限核验隔离见 §2.5。

身份映射后的 `project_current_offers` 按本次涉及的 source ID 增量计算，未变化不写，最多 100 项一段短事务；计算移到写锁外，提交时核对原 catalog head。重复绑定后的中断可补投影，不重查身份或重写原回执；B 使用独立视频证据，不覆盖旧正销量 opportunity。全量 catalog 更新仍可重算完整投影，历史 source 事实不删除。
`creator_discovery.py/discovery_cohort.py` 执行精确 Find，`identity_queue.py/identity-batch.py` 聚合去重身份，`profile_refresh.py` 独立刷新画像。resolved/unresolved 复用证据，blocked 保留原断点。`IdentityBridge` 只交接当前 head 的 source edge，固定索引顺序避免历史全表 JSON 扫描；OECID 发布不能等待或伪装 Profile 成功。BR/MY/UK 的 OECID 由 scheduler 调 `market-identity.py`（`lib/market_identity.py`）：每 3 个 handle 拉起一次 `probe-italy-profile.py`，证据写入 `var/market-identity-{market}-*` 目录（目前不清理）。页面的身份队列、精确发现与画像刷新接口只支持 IT。

四市场 `identityAccountRole=supply` 时，OECID 走对应供给账号：IT acc9、BR acc2、MY acc5、UK acc4，先要求该账号已发布 `oecid_find=verified`。IT 先结算旧 cohort/outbox 的原报告，再使用 `market-identity.py` 的有界每批 3 人、每轮最多 50 人路径；原 acc6 的 12 QPS 验收不转授 acc9。旧 started 按原账号读取报告，当前批次也按冻结账号消费；handle 预算、有效身份和未匹配结论跨角色切换保留，账号故障只等待当前所选账号。workflow 的 OECID 资源槽绑定供给账号，与同账号货盘任务互斥。下面的 IT cohort 描述适用于兼容模式与旧任务恢复。

Find lanes 共用原账号 QPS 和滑块验证合同；IT 沿用已发布的 Find cohort 策略，其他市场沿用每 3 个 handle 的 probe。有效 unresolved 与已验证身份按同市场规范化 handle 复用，不因新 PID、A/B 或窗口重查；Profile 后续失败不丢弃已确认 Find。IT 的 `identity_only` 成功证据可交接为可用身份，原画像失败记录保留。

schema v22 的 `identity_handle_budget`、`identity_retry_event`、`identity_account_wait` 只保存执行预算和通道等待，身份真相仍在原发现/身份台账。单 handle 累计 3 次可归属个体的技术失败后隔离，前两次分别退避 300/1800 秒；跨来源、重启及账号代次不清零，不按 7 天自动重开，不记为搜索不到、不新建人工案件。账号、验证码、签名、HTTP/共享运行时错误不消耗个体预算；同 cohort 只计一次通道故障，按 60/900/3600 秒退避，后续最多每小时读取检查。真实健康 Find 重置通道退避升级，新身份代次可提前解除通道等待；不重置个体预算。验证字段缺失只记读取合同不完整，不当作已确认验证码。已证实 16201010 仍调用原账号恢复流程。

非 IT 开始 probe 前持久记录原目标及两次初始化目录，进程中断先读取原报告，不先换 token 重查；原报告缺失走共享等待，不把未尝试的兄弟目标记失败。IT 继续使用现有 discovery cohort、lease 和不可变尝试目录，`attempt_no` 保留为历史目录编号，不再作为每批重新获得的技术预算。新非 IT 消费者有每市场进程锁；IT 保留原 discovery 单 owner 合同。有效成功与否定结论优先于技术隔离投影，冲突继续拒绝猜测。

身份阶段处理有界范围：IT 最多 4 个 50 人 cohort/200 次领取；其他市场最多 50 个 handle、每批 3 个。报告明确 found/notFound/剩余/隔离/账号等待，只有真实已确认身份计入成功数量。已绑定结果随批交接发送池，不等待全部积压清空。重试到期可独立从 OECID 续跑，不依赖 Kalodata 额度或货盘刷新；原失败 run/checkpoint 保留，已由新通道接管的旧技术阻断不再永久禁止身份续跑。暂停与自动运营授权仍决定是否领取。

IT 交接在写事务外读取并 materialize 当前 A/B 来源，短事务内复核来源 head 和已交接项，再写 outbox；避免对历史来源进行慢扫描时占写锁。身份统计同样固定当前来源集合并使用完整 plan/source 键，按 handle 的一个最终判定统计关联线索，技术隔离与有效未匹配、等待分列。历史失败证据回填命令为 `identity-retry.py backfill --confirm`，先备份再运行，重复不增加预算；不把旧混合用途 `attempt_no` 直接当成个体失败次数。验证、运行回归与恢复见[身份预算交付报告](implementation/identity-budget-20260925.md)。

### 2.5 发送与 72 小时冷却

| 模块 | 职责 |
| --- | --- |
| `lead_pool.py` | 当前资格、A/B 排序（`lead_strength()`）、单达人槽位 |
| `cycle_review.py` | 领取前身份、材料、关系、去重复检 |
| `cycle_delivery.py` / `cycle_executor.py` | 不可变 delivery、组件意图、额度和回执 |
| `continuous_send.py` / `continuous-send-worker.py` | IT 控制、窗口、断点与 worker |
| `market_send_control.py` / `market_send_canary.py` / `market-send-worker.py` | BR/MY/UK 控制与持续发送；`market_send_canary.run` 是三市场正式发送核心，名称沿用首测阶段 |
| `request_budget.py` | 进程内请求节拍：每个 runtime 各持一份，不跨进程；跨进程只共享写门禁 |
| `cycle_service.py` / `collaboration_status.py` | 案件、人工接管、合作状态 |

`outreach_allocation.py` 在四市场正式持续发送与 market canary 的既有候选入口分配 4 A / 1 B 领取份额；schema v23 追加 `outreach_rotation` 和 `outreach_allocation`。轮转按 market 持久化、跨日连续；按北京时间冻结日记账，market×日期×OEC 唯一，不按 PID、组件或核验轮数计数。新 delivery、两条组件和份额行在同一事务提交；游标过期、资格/容量拒绝或并发同达人领取全部回滚，不消耗份额。已有冻结和在途意图不回填、不改写分类。

领取器同时检查 ready 与同达人其他 queued 位置，完成材料、模板与本地容量检查后择优；同人有有效 A 位置归 A，全部 A 位置不合格时才可使用 B。A/B 各自维持原数值排序，一类无合格候选时另一类补位，记录实际类别/偏好类别/原因，游标正常前进、不积累欠额。机构额度已耗尽时先筛选已解锁或已有预留的达人，避免逐人检查无用材料；最终逐写许可照旧。已冻结但后续历史/平台预检取消仍属于一次安排，不当成确认送达，也不退份额后再凑成功比例。

发送池独立展示今日已安排、A/B 安排与补位、各投递结果。卡已确认后的同 CID、同 market/OEC、72 小时内回复及加橱窗按达人去重观察，排除旧消息、未知时间和其他会话；这不是因果归因，也不是已成熟的转化率。页面当前展示今日领取 cohort，原始台账保留供历史核对。IT 候选来源读取从当前 A head 固定连接到 selection/index/edge，避免展开扫描全历史来源；保持 B 的独立视频证据。交付和生产实证见[80/20 发送报告](implementation/outreach-share-20260925.md)。

同市场同达人主动推品冷却统一 **72 小时**；池投影、领取前复检和最终平台写门禁必须同值，回复/橱窗不缩短。人工客服与新来信回复不套用主动推品冷却。`outreach_policy.py` 将已确认/部分送达的末次组件时间与同 plan/OEC 的平台我方外发观测时间取较晚值；逐写仅排除当前 delivery 在原 cid 已确认的 messageId。未知意图仍独立阻断，不靠冷却结束放行。

每位领取冻结全部发送事实，逐次发卡/文字前复检停止、时间窗、身份维护、关系和当前材料。容量预检在建会话/发卡前，最终滚动 24 小时预留在短事务复核；已解锁关系、或 24 小时内已为同一达人预留过的，不再占新联系名额（`cycle_delivery.contact_capacity_available`）。本地上限 `NEW_CONTACT_LIMIT` 现为 None，改由平台机构额度决定；`cycle_platform_signal` 里新联系闸门由 `cycle_delivery.new_contact_hold` 决定：同一北京日出现明确额度回执（状态 3、检查码 100、`im_limit_reached`）即 `platform_quota`，同一非额度拒绝特征当日达到 3 次（`REPEATED_REJECTION_HOLD`）即 `platform_rejection_repeated`，两者都拒绝新联系到次日，发送 worker 进入 waiting_capacity（每 5 分钟复查）；其他单次拒绝只结束该达人。`PLATFORM_REJECTION_HOLD` 仅是兼容旧调用方的计数常量，不是“一次拒绝即闸门”。IT 与 BR/MY/UK 两类发送都会结算已派发组件：明确拒收（状态 1–5）记 rejected，其它记 unknown 等回查；带响应的平台回执全部落账（`market_send_canary._record_send_failure`）。建会话返回非零码先保存原始失败/未知证据；201 沿原明确商业拒绝路径隔离，其余无可关联回执的结果进入下述有界核验，不永久停止整个市场。平台额度的原生码分类仍需继续收窄，不能把当前所有 rejected 信号都解释为已证实额度耗尽。页面与状态接口的 capacity.limit/remaining 为 null 时显示“由平台机构额度决定”，告警条报当天平台拒绝次数。卡＋文字要求两个剩余消息槽；历史单卡确认而文字未尝试已触限，只结算未发送文字，保留 partial_delivery。冻结后 30 分钟过期的投递，BR/MY/UK 发送 worker 在卡片或文字派发前被本地判定为单个收件人的终态原因（如货盘刷新后冻结报价失效 `offer_changed`）时，只取消该投递未提交的组件并继续下一位，不再让整个市场的 worker 停在 attention。发送 worker 每轮先用 `cancel_expired_unsubmitted` 结算，不迟发：建会话未开始、各组件未发出的记 cancelled；卡已确认、文字未开始的只取消文字，记 partial_delivery；可能已到达平台的（会话创建已开始、组件在途或 unknown）留给核验。二发未知由 `delivery_reconciliation.py` 在原执行器结束后只读核验：建会话最多 2 轮，每轮 180 秒、最多 101 页；卡片/文字各最多 3 轮，每轮 90 秒；相邻轮至少 300 秒，从首轮起 900 秒累计期限。请求前将次数、原 requestRef 和期限记入 `cycle_delivery_check`，重启/新窗口不清零。首次正常提交后的立即回读不计专项轮次。卡片仍保留“两次成功读取历史、间隔 ≥300 秒都缺失”的提前隔离证据；读取失败只记未完成，不冒充缺失。建会话列表有上限且不能关联原请求，找到同达人 CID 也不猜测成功或继续发卡文。

预算耗尽记 `quarantined_unknown`，保留原会话意图与卡文的真实状态；只阻断同市场/OEC 主动营销，不改关系为 human、不新增技术人工案件，不自动周期追查。原业务人工案件及拒联不清除。发送池独立展示“技术隔离”，不混入人工等待；原 unknown 组件不再占全市场派发槽或把状态接口误标为仍待核验。隔离后的精确迟到回执可幂等确认原组件，但不重开投递或补发从未提交组件；核验确认卡片和关闭未提交文字同事务完成。建会话业务码 201 保持明确商业拒绝的旧隔离/案件分类，额度状态 3 / check_code 100 / `im_limit_reached` 仍走额度暂停。

IT/BR/MY/UK 的正式发送与核验共用每市场 `delivery-executor-<market>.lock`，锁覆盖原执行、请求收口和落账，原执行器未结束不能释放槽；进程退出后内核释放，已有逐写账号锁与门禁保留。两类 worker 持续处于有限 `waiting_reconciliation`，调度器可在原持久授权下恢复退出的 worker；核验本身没有发送权限。新非 IT 投递冻结 senderAccount/senderImId；旧投递按原实现固定通信账号（BR acc1、MY acc8、UK acc11）兼容，配置换号拒绝读取，IT 继续校验原 senderBindingHash。适配器包装为 `it_delivery_dispatch_not_allowed` 的原本地 `CycleError`，仅在组件仍 ready、started/receipt/confirmation 全空时由 `local_permit_error()` 恢复；已提交组件不改判未提交。具体交付和运行证据见[二发未知隔离报告](implementation/delivery-isolation-20260925.md)。

组件已提交只读精确回查。`/api/send` reconcile 即使停止/窗口外也只查原账号/requestRef，结束即返回，不能领取下一人或启动未提交组件。会话 received 只有原 requestRef/CID/达人身份全部匹配才可确认，缺回执 inflight 保持未知。IT 已授权的零卡文未知会话隔离保留原意图和案件，不改成失败/成功，不对该达人再发。

非 IT 与 IT 一样，对明确的单达人预检排除收口完全未提交的卡文，保留原历史分类和会话回执，其他达人继续。已确认会话但卡文未开始的过期投递也可本地取消；会话 inflight/received 或组件在途/unknown 仍拒绝取消，只核验原意图。修复与原 UK 证据见[预检收口报告](implementation/uk-preflight-ai-simulation-20260926.md)。

worker 在等待窗口、池或容量时常驻退避；同账号在途写串行，僵尸进程不算存活。账号认证可短复用同进程/身份指纹/代次，逐位和逐写复检不省略。单账号串行是当前正式路径；30 人/分钟未由代码配置证明。

`invitation_continuation.py` 只为原正式投递的卡确认、文字从未提交状态记录继续凭证。Inbox/Service 在原入站事务内，将精确 CID/messageId、冻结 controlRevision → beforeRevision → afterRevision、inboxUntil/unlocked 写入 `cycle_delivery_check(kind=invitation_inbox_revision)`；中间暂停/接管等版本变化不能跳过。逐写重查关系、同 CID tracking、当前 pending、人工案件及所有原有授权/账号/材料/容量/窗口门禁，仅原文字可忽略已证明的入站变化，新领取不适用。非 IT 续发核对冻结 senderAccount/senderImId；明确终止只取消未提交文字、保留已确认卡为 partial_delivery，其他达人继续。schema v24 幂等确保原核验表和 `(delivery_id,kind,checked)` 索引，不回填业务行。当前凭证要求摄取入站时卡已经确认；入站先入库、卡后确认的顺序未放宽。

### 2.6 双向消息与会话

四市场当前采用 `market-accounts.json.imSessionMode=sdk_http`：每市场 `im-session-worker.py` 持通讯账号原 ProfileLease，负责浏览器 SDK 收信和现有 HTTP 会话的私有 IPC 借用。HTTP 已认证后即可服务原二发、AI 和回查；浏览器初始化不阻塞 HTTP。发送仍走原 HTTP 执行器、逐写门禁、原账号与 requestRef，SDK 不承担发送。

`sdk_inbox.py` 将 onMessageReceive/onMessageUpsert 变为持久唤醒信号，先提交 schema v26 `im_receive_signal` 再清浏览器缓存；消息身份、市场、正文和编辑版本仍由原 HTTP proof/history 解析器核实，进入原 Inbox/Service/reply_events。已知相同文本与非文本回放不产生新待回复。回查优先处理到期通知，保留 30 秒分页发现和冷 checkpoint 补扫；游标跨会话续期保存。每轮最多 5 页；已有 checkpoint 的会话未在预算内接上补读开始前已入库的消息（`inbox_event` rowid 水位）时，Inbox 记 `backfilling` 并在 `inbox_backfill` 保存原账号/IM/OEC 绑定的原生游标：期间消息入库但不生成待回复、暂停该达人营销，冷扫描优先该会话；下轮先读首页，接上本次补读已入库范围后从游标继续，直到覆盖证明成立才按 live 规则一次结算补读期间的新来信。换读取身份从首页重建链。续读只在本轮从首页连续读到保存链的顶端消息（`inbox_backfill.segment_top`）时跳到保存游标；其他入口存入的消息只是事实，不证明其间区间已读。其他单页读取入口（HTTP 轮询、回复前读取）遇到读不到重叠时同样转 `backfilling` 并记录读取前水位，不再产生新的 `gap`。存量 `gap` 用 `scripts/inbox-gap.py diagnose` 只读列出，`recover --cid --confirm` 仅在存在 gap 前最后一条 live 事件（作为水位）时转入补读；gap 期间已记为历史的事件保持历史，不改写为待回复；缺少水位证据时报告 `not_recoverable`。进程内读取预算 2 QPS，不能当作账号所有消费者共享的全局限速。

SDK 当前平台枚举为 0 未初始化、1 初始化中、2 成功、3 失败；必须等 2 才挂接接收回调。会话约每 600 秒续期，退出/维护时先拒绝新借用，等原 HTTP 客户端收口后释放 ProfileLease。Unix socket 为同 UID、0600，凭据仅在内存和私有 IPC 中，客户端检查 epoch、身份代次和 headers 指纹；借用不产生发送授权。维护/登录失效沿用原账号恢复任务。启动/断线由原 scheduler 监督，存活与 SDK 初始化成功均不能替代新来信时延验收。

缺省或显式 `http_polling` 时，`cycle_inbox.py`、`poll-cycle-inbox.py`、`poll-market-inbox.py` 负责最近会话发现与旧 checkpoint 公平补扫；`inbox_event/inbox_content*` 保存事件和正文。新会话精确匹配本市场关系后落库；冷 checkpoint 也重新校验 plan，合作状态不能回退 IT。

机构后台 `ourMessages` 与达人消息一并持久化正文、发生时间、精确 messageId 和市场/会话身份。保存或编辑我方正文不创建达人回复任务；只有外发、尚无达人入站的会话也可打开。`conversation_workbench.py` 与 V2 上下文读取同一事实；与本地 delivery/service_reply 的重复按精确 messageId 合并，不能按文本相同去重，不能把我方消息误作达人 pending。`observed_messages.py` 按 plan/OEC/cid/messageId 排除本系统已有回执，只把其余平台消息投影为 observed。会话列表先用集合查询预筛可能成行的达人（有入站、案件、pending、已确认/隔离回复、human 模式、收信补读或未记账的机构后台消息），其余关系与原逐行规则同样不会成行；已记账消息按原规则同样的 `(oec, cid, messageId)` 键匹配，预筛是逐行规则的超集，在生产快照上与原实现结果一致。列表另有“技术挂起”队列（回复送达未知已隔离、收信 gap/backfilling），与商业人工分开。队列按（状态、开始等待时间、达人）稳定排序，`nextCursor` 是这个键的位置，“加载更多”用 `after=` 游标，可遍历到第 5000 条之后（offset 仍兼容且上限 5000）；表结构在每次调用只读一次，`inbox_checkpoint(plan_id,oec,state)` 索引由 `Inbox` 创建，避免逐行扫描整个 plan 的 checkpoint。每次列表返回 `stats`（扫描关系数、候选、SQL 条数、耗时），bridge 对同参数的并发读取合并为一次。`scripts/bench-conversation-scale.py` 在临时目录生成合成台账测量（不打开 `var/`）：5 万关系、1 万候选时单次约 0.6 秒，此前约 6 秒；这是工程基线，不是验收门槛。回复投影 `reply_events.backfill` 只读取尚无投影的行（按 outbound_episode/inbound_turn/turn_episode_link 唯一键），每 500 行一个事务，中断后下次补完；生产快照上与旧实现投影完全一致，稳态调用约 0.6 秒→0.03 秒。详情时间线默认最近 300 条，以 `occurredAt|id` 游标向上翻页，游标同时下推到机构后台消息来源（不再固定取最近 200 条）；`heldReplies` 列出未决/隔离的 AI 回复，只能按原 requestRef 与冻结身份只读核验。人工发送先在 `manual_command` 登记 requestId 与正文哈希，创建意图时在同一事务内 CAS 交接；发送失败时 CLI 取消未开始的 ready 意图或关闭未交接的命令后才返回 `not_submitted`。核验查不到意图时：未登记的 requestId 写入关闭墓碑（原请求迟到会被拒绝），已登记且在途未超过 300 秒的保持 `unresolved`；页面只有 `not_submitted` 才解除待核验，核验确认也不清空编辑框。bridge 与 CLI 共用 32KB 请求体上限。热读每会话取最新 20 条，优先补缺失机构正文；`inbox-history.py` 使用原生 OLDER 游标做有界历史补扫，按页原子保存去重回执和断点。历史断点绑定原账号、IM 身份、完整 CID/OEC 与市场；不推进热读水位，不解除 gap/unknown，不创建回复任务。`completeOlderRange` 只证明从首次成功读取时间向旧的游标链完成；延后热读项单列，不能据此声称全部正文已入库。

首次 checkpoint 以同 plan/CID/OEC 最新已确认卡的 started 为实时回复基线，兼容只有文字确认的旧记录；没有已确认外发则首次导入为历史。冻结 CID 缺失时只由原已确认 conversation intent 补齐，received/inflight 不能猜测。查询按关系和原投递定点限定；不翻转旧 historical 行或重开已处理事项。已确认卡和文字按各自 `cycle_delivery_part` 状态/时间投影，不用 episode 冻结正文冒充已发文字。showcaseNotifications 单列系统事件，不输入文字分类器。消息完整性以覆盖/水位证明，worker 存活不足以证明新鲜。

兼容 HTTP polling 模式下，IT 默认热点＋冷 checkpoint 每轮 12 个/30 秒；其他三市场 20 个/10 秒，每个 poller 进程各自 2 QPS 读预算（进程内节拍，收信与发送不共用）。认证短持 profile 租约后释放，每次读取仍核验维护与身份指纹；账号忙显示 waiting_account。认证读取遇到平台业务码 16201010（通讯账号登录失效；BR/MY/UK 由 `market_im_runtime._read` 附在原错误上，IT 取认证报告 `authReads`）时，收信经 `login_recovery.request_refresh` 登记恢复（见下文“账号认证恢复”）。状态文件的 `accountRecovery` 记录这次登记。扫描规模增长后应按真实覆盖延迟调参，不能仅提高 tick。

### 2.7 Agent V2 与回复 transport

2026-09-25 已完成[AI 回复设计审查](architecture/ai-reply-optimization-20260925.md)；Jev 已清理、原邀请衔接已分批实施，集中回复的范围/排序/结算已实现；长期业务记忆、完整历史回放及新隔离服务策略仍待后续。以下描述当前代码。

`agent_reply_v2.py` 使用 `config/agent-reply-guide-v2.txt` 或 append-only `agent_reply_guide_revision`，从目标入站前的真实双向消息、商品事实和市场语言编译提示词。`agent_reply_decision_v2` 保存真实输入/输出/错误、指南版本及关联意图；模型只输出 reply/no_reply/request_detail/handoff，不提供账号或 transport 参数。

`production_context` 默认仍按目标入站时间截断；真实 worker 显式使用 `live=True` 读取当前已发生的本地对话，把当前全部未处理来信版本标为 `unansweredMessageIds` 并加入 messages，不受最近 24 条历史裁剪影响。原配套文字标 `purpose=outreach_invitation`，不当作客服回答。此处只分开当前与目标时刻消息，尚未实现完整历史指南/关系/事实的 as-of 重建，也未补齐 24 条之前的长期提醒证据。`reply_events.backfill` 的新 episode/link 优先用已确认卡时间定位，保留旧 episode/link；不是完整当前上下文改造。`observed_messages` 继续按精确 CID/OEC/messageId 排除系统已确认邀请，另一条 messageId 即使同文也仍是后台实际外发。

`reply_scope.py` 从原 inbox_event/content head、service_cursor、精确外发证据选出当前未处理范围。可运行达人按最早未处理入站时间、稳定 creator 排序；后续补充不重置年龄，due_at 只控制退避。人工接管/等待资料、缺正文、gap、多 CID 不明确、技术退避与本地超预算不占住前 20 个候选名额。模型输入/decision 内的 replyScope 冻结 eventRowid/CID/messageId/contentHash；apply 和逐写 begin 再核对同一范围及原控制版本，未提交内容过期即拒绝。非 IT 同样恢复适配器包装的确定未提交许可拒绝并取消 ready，inflight/unknown 不改判。

确认或 no_reply 在原事务内向 `service_message_resolution` 追加精确已处理版本和原 reply/decision 引用，仅连续已处理前缀推进 service_cursor；发送期间新增/编辑来信继续待办，较早消息被编辑也不会使同批已答的其他版本重复入队。清除 pending/inbox_until 还要求范围已尽且原控制版本未变，不覆盖后来的人工动作。商业 handoff 确认仅更新原案件 ack，不把商业问题当作已经解决。旧无 replyScope 的意图继续沿原兼容合同，不重建未知消息。

schema v25 追加处理证据，并在已有模块上建立 inbox 按市场/OEC/类型时间、delivery 按 plan/OEC、reply 按 creator 时间和 CID 的索引。只从既有 service_cursor 前缀复制原处理版本；`reference=legacy_service_cursor`、handled_at=NULL 明确原处理时间未知，不推测旧意图的消息范围。保留旧表和原字段，迁移重复为空；新空库的未初始化模块不被伪造。

输入先用 provider 同一序列化/24,000 字节规则检查，优先裁可选旧历史，当前未答问题和保留的邀请不裁。仍超限保存原 decision 台账的 input_blocked、modelCalls=0，并保留待答范围；同范围/指南不反复进入队头或消耗模型尝试，新问题/指南变化重新评估。页面显示“上下文过长，待答问题已保留”。这不是超长诉求自动分段，也不是 §9.17 全部错误分类/通道退避已完成。

指南包含肯定合作但未观察到当前商品橱窗证据时的加橱窗提醒，要求已有真实卡、不重复已提醒内容，并按市场语言说明下一个视频/直播生效。上下文提供实际观察事实；“未观察到”不升级成“核实没有”。

`run-agent-replies.py` 先回查 inflight/accepted/unknown，再处理新 turn；`begin()` 在转 inflight 的同一事务冻结 `sender_account`/`sender_identity`（非 IT 为 IM id，IT 为 sender binding hash），恢复时通讯角色或身份与冻结值不同即拒绝读取，旧记录缺失时不以当前账号补。未知回复最多 3 次只读核验（间隔 ≥120 秒），计数在读取前持久化，读取未能开始（发送锁/账号不可用）退还次数但不延长期限：提交起 900 秒截止，与读取是否完成无关，一轮内一条回复被推迟时继续检查其他回复；`service_reply` 新列在 `BEGIN IMMEDIATE` 内复查后追加，多进程首次启动每列只加一次；只有读取尚未访问平台（传输层 `read_started` 回调未触发）的失败才退还次数；正常收信已存下与平台受理回执同一 messageId、同会话/OEC 且正文逐字相同的我方消息时，`settle_observed` 本地幂等结算 accepted/unknown/isolated 回复（不做平台读取、不耗预算，回执无 messageId 的仍保持未知）；耗尽转 `isolated`，保留原正文/requestRef/证据，只暂停该达人的自动回复和主动推品（并入 `outreach_policy` 营销隔离），其他达人继续，精确迟到证据仍可幂等结算，商业 handoff 案件保留。模型失败逐达人隔离，同输入最多三次。确定未提交而上下文过期的 ready 可审计终结，已提交只核验。IT 人工/Agent 共用 `reply_transport.py`，其他市场用 `market_agent_reply.py`，均冻结唯一 `service_reply` 正文/requestRef 再发送。Agent 生成第一份可发送回复后立即进入执行，不再批量积压 20 份草稿；有处理进展时短间隔继续，等待/错误/未知保持轮询退避。每次生成前重查停止、窗口与缓冲。

模型调用统一经 `draft_provider.py`：DeepSeek `https://api.deepseek.com/chat/completions`、模型 `deepseek-flash`，JSON object 输出、关闭 thinking，单次请求不重试，上限约 24,000 输入字节、1,200 输出 token、60 秒。密钥先读 `DEEPSEEK_API_KEY`，缺失时只读旧项目 `01-BDSystem-V2/config.yaml` 的 `reply.api_key`（绝对路径写死）。用途：Agent V2、会话翻译、商品短名和离线评测。Jev 可执行 adapter、配置样例和新分类入口已移除；`reply_events.py` 保留消息投影、DeepSeek V1 兼容及历史评测只读，不读取 TypeSafe 配置或执行 Jev。私密历史配置由原备份规则保护，不作为运行依赖。

运营在机构后台于目标入站之后已回复时，旧 turn 暂停自动回答；不擅自结案，下一条达人来信重新进入处理。prepare 与逐写前校验最新 turn、pending/control revision、人工案件及平台外发。澄清/联系方式确认送达进入 waiting_clarification/waiting_contact，礼貌回复未提供资料仍保持等待。handoff 的 case、控制锁、唯一确认意图和 decision 关联在同一事务落账；准备失败整体回滚，人工接管后 AI 停止。普通生成回复的意图与 decision 关联也原子保存。

`evaluate-agent-v2.py` 复用生产 prompt 和校验器，读取固定四市场多轮合成案例。默认零模型调用；显式 `--call-model` 保存全输入输出、指南 hash 与失败样本，`--guide-db` 只读生效指南。筛查和逐条复核分开，合成通过率不代表生产准确率。

首次真实启动由 `agent-v2-first-send` 页面事件授权，首条确认后停在 pilot_complete_waiting_resume，再由 `agent-v2-full-run` 继续。simulate 与 trace 不调用平台 transport；`--authorized-now` 单次请求不能和常驻 worker 合用。旧 V1 自动回复入口已删除；V1 turn_review/固定模板只用于历史评测。

回复窗口 `replyStart`–`replyEnd` 是北京时间的同一天区间（不跨午夜）。`validate_agent_setting` 与 template-library bridge 要求它位于同日二发窗口之前 `bufferMinutes` 以上，并且距前一天二发结束也有 `bufferMinutes` 以上，所以 00:30–08:00 可以跟在 16:30–24:00 之后。worker 每轮还用 `near_send_window` 核对二发此刻实际使用的窗口（IT 取 `continuous_send.control`，其他市场取 `market_send_control.control`）：窗口前后 buffer 内，计划内运行返回 `waiting_send_window`；页面单次 `--authorized-now` 不受此限，但仍受在途发送互斥约束。窗口内调用模型之前先查 `reply_blocker`（与 `AutoReplies.begin` 同一条件）：本市场有 unknown 投递或在途/未知回复时返回 `waiting_dispatch`，不生成回复、不消耗来信的重试次数（每条来信 1 小时后重试、最多 3 次）。作业页修改 Agent 开始时间时保持已保存的窗口长度，作业说明显示实际保存的窗口。

## 3. 数据、并发与备份

| SQLite/表族 | 事实归属 |
| --- | --- |
| `global-source[-{m}].sqlite` | 全托来源、分页、覆盖、head |
| `global-selection[-{m}].sqlite` / `campaign-join[-{m}].sqlite` | 选入/加入意图与回执 |
| `campaign-screen[-{m}].sqlite` / `catalog-links.sqlite` | 筛分、标准卡、current binding |
| `kalodata-leads[-{m}].sqlite` | 查询窗口、分页回执与尝试 |
| `creator-discovery.sqlite` / `creator-identities.sqlite` | 精确发现、OECID、别名、观测 |
| `creator-profile-refresh.sqlite` | 画像 job/request/heartbeat |
| `second-cycle.sqlite` | 四市场共用主库：关系、delivery、inbox、service_reply、Agent、workflow、控制，以及 A/B 线索 head、身份 outbox、首页只读快照 |
| `it-conversations.sqlite` | IT 会话索引；建表定义只在 `cycle_conversations.py`，IT 发送直接读写 |
| `batch-tasks.sqlite` | 仍被读取：身份运行策略 `identity_runtime_policy`（`identity_acceptance.production_policy`）与 A 类旧来源作业（`leads_queue.py`） |
| `{m}-global-onboarding-canary.sqlite` | 市场接入 canary 证据，`market-catalog-status.py` 读取 |
| `second-cycle` 中的 `cycle_bulk*` 表 | 历史追溯，不启动旧执行链 |

IT 使用无后缀文件名，其他市场加 `-{market}` 后缀。`state-backup.json` 还列着 9 个已无代码读写的旧库：`matching*.sqlite`（3 个）、`outreach-drafts`、`runtime`、`second-italy`、`second-live-trials`、`second-outreach`、`kalodata-source-cache`。

事件为事实源，当前投影可重建；JSON progress 不替代最终台账。ID/PID/OECID/Campaign/listId 一律字符串。凭据、Cookie、私密原文不进入普通日志或 API 错误。

外部写入统一 pending → submitted → confirmed/failed_known/unknown；unknown 无自动重试资格。账号锁约束同身份操作，lease/fence 拒绝过期 worker 迟到提交，stop 在安全点退出。多 lane 共享预算，不各自累加。当前 schema registry 尚未覆盖全部库；新增迁移必须 additive、幂等并测试旧库升级，不能删库重建。

`state-retention.py` 执行历史保留：Campaign/全托货盘快照保留当前加最近 2 个完整版本；Campaign 采集 JSON 每组只留最新一份用于重同步；每轮报告 JSON 与 `var/cycle-scheduler/` 阶段日志保留 14 天；账号身份目录保留当前与上一代（72 小时内的目录不动）；超过 20MB 的日志轮转：仍有进程打开（`lsof` 判定，判定不了按有人写）的日志先跳过、记在清单 `skippedLogs`，等写入进程退出后再轮转；超过 100MB 才强制复制后截断，截断前补抄尾部并标 `mayMissTail`，不作审计日志用。当前 head、head 覆盖引用的基线/刷新轮次、未结束 workflow 对应的来源轮次、未完成轮次和被未结束阶段记录引用的文件一律保留。累计候选及其自包含资格/分页证据不参与来源版本轮换；历史来源明细归档后候选事实和原选入意图仍保留。`plan` 只读；`apply --confirm [--vacuum]` 先把候选写入仓库外 `../BDHub-Agent-backups/retention/<时间>/` 并校验，再删除原件；全托轮次只删明细行，保留轮次记录；`restore --archive <目录> --confirm` 补回缺失的行和文件。应在 worker 暂停或空闲时执行。

`state-backup.py` 按 `config/state-backup.json` 显式清单执行 SQLite online backup（每库在单个读事务内一步复制，业务 worker 持续写入时也能完成一致快照，无需停机；开始前按各库及 WAL 实测体积×1.2＋512MB 检查剩余空间，不足即拒绝，不删除旧备份腾空间），保存 hash、quick_check、schema；新业务库未入清单则失败。备份目录/文件为 0700/0600，敏感配置独立管理。restore 只接受空目标并生成回执；跨机器切换需排空写 worker 后最终备份，多库备份不声称跨库单事务。`retention-plan` 校验现存备份并只生成保留建议（默认 7 天全部、30 天每日、84 天每周，每种库清单至少 3 份）；迁移标签、损坏和不明文件保留，无删除操作。`drill --backup <目录> [--database …]`（`lib/restore_drill.py`）是离线恢复演练：把备份恢复到新建的临时目录，所有副本只读打开，不启动进程、不联网、平台写入 0，结束后删除副本（`--keep` 保留）。它检查备份本身与恢复路径；声明的跨库引用（关系/投递达人→creator_identity，建链准备→Campaign 筛选或全托来源 run，全托选入→来源 run），父库复制开始后才写入的子行缺父记录判为复制时差并阻断，更早的只算历史孤儿警告；库 schema 新于代码时阻断；未决发送意图所在市场须有已发布身份代次（身份文件本不在备份内，列为外部依赖）。部分库演练缺父库时阻断。演练还逐条核对未决意图：AI/人工回复冻结了发送账号与 IM 身份，备份中该账号须有已发布代次（与当天配置账号无关），缺失即阻断；二发投递组件没有冻结发送身份，只能记为 `identity_not_frozen` 警告。报告 `layers` 分层说明快照、schema、跨库引用、原意图依赖（complete/partial）和外部身份文件（external_required）各自证明到哪一步，`businessResumeProven` 始终为 false。报告写在 `var/backups/drills/`，不放进备份目录；最近一次未通过时告警条提示。

`offsite-backup.py` 把代码、当前状态和本机专有配置加密写到外接盘。每次先按 `state-backup.json` 做一份全量 online backup 到临时目录，连同 `git bundle --all`、未提交改动补丁、`../BDHub-Agent-backups/` 下的归档（git 离线包与密钥目录除外）和 3 个被忽略的敏感配置打成 tar.gz，经 openssl AES-256-CBC（PBKDF2 20 万次）直接写入 `<盘>/BDHub-Agent-offsite/<UTC 时间>/`；刷到设备后解密回读，核对明文 hash 与成员，最后写 manifest 表示完成，失败则删除本次目录。盘上只动这个文件夹，保留最近 3 份。密钥 `../BDHub-Agent-backups/offsite/offsite.key` 只在本机，须另存一份到密码管理器，丢失则副本无法解开。账号浏览器身份、`var/` 运行文件和旧项目 `01-BDSystem-V2`（账号配置、保存凭据、签名 runtime）不在副本内，换机恢复仍需旧项目。`plan` 只读，`run --confirm` 写入，`verify` 从盘上重读最新一份；`auto` 供插盘触发，只处理已有该文件夹的卷，先校验最新一份，超过 20 小时或校验失败才写新的，并发系统通知。每次写入成功后，在本机 `offsite/latest.json` 记下这份副本，告警条据此判断备份是否过期。

**账号认证恢复（单一入口）：** 所有发现登录失效的地方——SDK 会话 owner、HTTP 收信、全托选入、OECID——都用 `login_recovery.auth_family` 判定（平台码 16201010、`sdk_login_required`、`market_identity_auth_required`；超时、额度、业务拒绝不算），再调用 `login_recovery.request_recovery`。一条恢复链的键是市场/账号/身份代次/错误族，报告来源和角色不进键，所以并发报告者都汇入同一个 `account_maintenance_intent`；旧的 `inbox-login-*` 请求号仍被识别为同一代次已处理。意图一律从 `refresh` 开始，刷新失败时维护 worker 在同一意图内改为可见重登。失败或需验证的意图留给人处理，只有新发布的代次才能再发起。同账号已有其他维护在进行时返回 `waiting_active`，不排第二个登录。scheduler 的阶段恢复（`_relogin_market_account`）加入这条链并等待其结束，只有发布了比故障代次更新的身份才算恢复；角色取实际执行的账号，不按简称换号。

**总控制台（只读）：** `scripts/operations-console.py`（`lib/operations_console.py`）一次读取四市场的当前主链阶段（运行中，或排队中并列出在等哪个资源、被哪个市场哪个阶段占了多久）、最近一次结束的阶段结果、持续通道快照（来自各市场运营页读模型，带其截至时间）、需核对的主链与人工会话数、全局资源占用和最近 15 个阶段结果；`/api/operations-console` 多标签页合并为一次读取，页面在 `/<市场>/console`，只在可见时每 15 秒刷新。首页阶段卡片各带 `metric`（名称、单位、窗口：本轮/今日/最近一轮，未记录为 null）与 `writes`（持续发送和 AI 回复不在此记录写入数，不再显示 0）；供给主链与持续通道分组显示，去掉串联箭头。Kalodata 状态区分“已排队等资源”和“正在读取”，并返回 `lastFinished`（取该阶段自己发布的结果，未发布为 null）；`leads-queue.py status/plan` 以只读方式打开线索台账，库不存在时读作空、旧结构报 `leads_ledger_migration_required`，不再建库或建表。首页设置只有“新提交且仍是当前修订”才会拉起调度器或发送 worker，重放旧回执或已被后续提交覆盖的请求不启动任何进程、不删除停止标记。会话时间线把完整 `(occurredAt,id)` 游标下推到机构消息来源，同一时间的大量消息也能完整翻页。

`ops-alerts.py`（`lib/ops_alerts.py`）为页面顶部告警条汇总异常，只读、不启动或重试任何作业，只读 SQLite。`gather` 读取页面本来就用的台账和状态文件：调度器运行态与停止文件、各市场收信状态文件、最新一轮 workflow 各阶段、`unknown`/`quarantined_unknown` 发送、未结人工 case、账号维护队列与下次维护时间、Agent 设置与首发阶段、`offsite/latest.json`。`evaluate` 把这些事实转成告警，分 critical/warning/info 三级：
- 调度器没有停止请求却不在运行 → critical；有停止请求 → “生产已暂停”提示，暂停直接造成的收信停滞不报，身份维护逾期降为提示。
- 收信错误码（`live_guard_busy` 与停止请求触发的 `stopped` 除外）且最后成功超过 10 分钟 → 运行中为 critical、暂停时为 warning，详情附带自动刷新的结果；运行中收信超过 15 分钟没有新记录 → critical。
- 未回来信用会话列表 unread 标记的同一规则（`conversation_workbench.UNREAD_PENDING` 且机构后台未在达人最新消息后回复），最早一条超过 26 小时（错过一个每日回复窗口）才升为 warning。
- 身份维护超过下次维护时间 2 小时且不在队列中才报；U 盘备份缺失或超过 7 天报 warning。

某个市场读取失败只报该市场，不影响其它告警。收信状态文件里的 `pendingContent` 统计全部 `inbox_pending` 行（含已处理状态），不能当待回复数。

`delivery-diagnostics.py` 只读发送台账与已有 timing，按完整确认触达的首次组件开始时间汇总。组件开始到首次确认回查包含等待/恢复，不能当 HTTP 耗时；缺失认证样本时不能判定认证瓶颈。

## 4. API 与 Web

2026-09-25 首批只读运营概览代码已合入：`GET /api/market-overview?market=<market>` → 固定 argv `market-overview.py` → `lib/market_overview.py`。直接只读现有台账，不初始化 Store/库、不发布新持久投影；同市场 singleflight 加 30 秒进程内缓存。页面分当前库存、近 7 日事件、等待和人工事项，身份来源行/去重 handle 分区对账，历史技术案件保留真实锁定状态。原 `lead_pool.counts.queued` 保持发送候选排队含义，新增 `candidateQueued` 别名与 `identityQueued`，不改变发送资格。B 旧表只认 IT，其他市场在实现接通前显示不可用；累计候选没有台账也显示不可用。生产 Web 切换状态和验证见[首批交付报告](implementation/readonly-operations-overview-20260925.md)，不把代码合入当成运行页面已更新。

首页开关保存返回提交回执（requestId、实际设置与 revision、worker 启动错误）；回执在提交事务内写入 `market_automation_receipt`，重放同一 requestId 先于开启前置条件返回原回执（旧请求只有修订号时标 `originalAvailable=false`），页面对同一开关冻结取值与预期修订号并丢弃晚到的旧读取；保存后读模型投影或页面重读失败只标记 `projectionStale/refreshFailed`，不再报“未保存”，关闭开关不受开启前置条件限制。首页与告警条读取失败时显示“状态不可确认”及上次成功时间，不沿用旧的“运行正常/无告警”。

AI 模型调用按错误分型：本地输入无效记 `input_blocked`，请求未发出记 `not_sent`（不占同输入 3 次），已调用失败记 `unknown`。同一模型服务连续 3 次服务级失败（配置/运行时、超时、网络、HTTP 错误）即在 `agent_model_service` 暂停，5 分钟起翻倍至 1 小时，暂停期间不外调、不扣任何达人的次数，到期只放行一个探测；列表显示“模型服务暂停，问题已保留”，告警条同步提示。这些退避值为工程值。常驻进程启动时把载入的提交登记到 `var/runtime-loaded/<角色>.json`；告警条比较存活进程所载提交与当前 HEAD 的 `scripts`、`vendor` 两棵 git 树（只改文档的提交不算版本差异；不比较页面改写的配置文件），并比较这两个目录未提交改动（含未跟踪文件）的摘要 `runtimeDirty`；工作区有未提交的运行代码改动时另报 `runtime-code-uncommitted`，未登记该摘要的旧进程只在工作区干净时算作当前代码。不一致提示按流程重启。`/api/ops-alerts` 同时返回只读 `evidence`（各进程载入提交与是否为当前代码、调度器心跳、模型服务暂停、最近恢复演练），“运行与设置 → 运行证据”页展示；读取失败显示“状态待核实”，不显示无条件“运行正常”。

全部 `/api/*` 限本机；写请求校验 Origin/Host、JSON 大小、market、revision、字段白名单和范围。bridge 固定 argv/cwd，不拼 shell；返回 decoder 复核枚举、ID、数量恒等式，available=false 与 0 分开。本机校验只接受 Host `127.0.0.1:5198`/`localhost:5198`，在其他端口起开发服务时全部 API 返回 403。已知例外：`/api/creator-identities` 由 Node 只读直连 `creator-identities.sqlite` 与 `second-cycle.sqlite`，并在 SQL 中计算投影，待迁回 CLI bridge。

| API | 用途 |
| --- | --- |
| `/api/operations-home`、`/api/workflow` | 主链、开关、断点、运行/安全停止 |
| `/api/campaign-*`、`/api/catalog-jobs`（仅 IT） | 活动与材料作业 |
| `/api/catalog-names`、`/api/link-naming` | 商品短名生成进度、卡名模板 |
| `/api/leads-queue`、`/api/identity-queue`（仅 IT）、`/api/lead-pool` | 线索、身份与发送池 |
| `/api/creator-identities`、`/api/creator-discovery`、`/api/creator-profile-refresh`、`/api/kalodata-identity` | 达人库、精确发现、画像刷新与 Kalodata 身份（后三者仅 IT） |
| `/api/send` | 保存、明确启动/停止、原意图只读核验 |
| `/api/conversations` | 队列、时间线、翻译、草稿、人工回复/核验（文本/卡片发送仅 IT）、接管解除 |
| `/api/template-library`、`/api/agent-replies` | 模板、市场窗口、指南、试聊、调用记录 |
| `/api/inbox` | worker（启停仅 IT）、水位、按北京日统计与分页明细 |
| `/api/jobs`、`/api/market-accounts`、`/api/market-catalog` | 共享供给时间、市场账号与货盘能力 |
| `/api/ops-alerts` | 跨市场告警条（只读，无参数） |

会话详情请求绑定 market/cid/请求代次，迟到结果不能覆盖新选择；刷新替换当前队列页，追加才合并。unknown 人工回复保留原 requestId/正文供核验；只有确认终态才清理草稿。轮询不得覆盖未保存设置；市场 Agent 时间独立保存，不写共享 jobs。resolve_manual 校验最新 turn/case/pending/control/合作状态 revision，幂等且平台写入 0。

inbox 趋势和日明细使用同一账本，totals 等于每日求和，文字跟随卡不重复计达人；缺失或恒等式失败不显示业务 0。Profile 指标与 PID 线索 GMV 分开展示来源/时间。旧 demo、matching、frozen 批次路由不再构建；2026-09-24 删除了页面已不调用的 reply-review、global-source、catalog-screen、cycle-service 路由。

## 5. 账号、配置与 vendor

通信/货盘固定为 IT ACC6/ACC9、BR ACC1/ACC2、MY ACC8/ACC5、UK ACC11/ACC4。`market-accounts.json` 定角色，`account_runtime_setting` 和已发布 generation 定当前能力；不继承旧项目停用状态。

能力验收发布使用 `reason=capability`，不推迟原 browser/HTTP/IM 身份的维护时钟；`identity_baseline` 找同一组引用的最近非能力发布作为基线。

账号 72 小时串行维护：可见浏览器读取既有保存凭据，候选 browser/HTTP/IM 联合验证后发布项目内新代次，失败保留上一代。重登不自动把未验收能力变 verified。同市场/账号/机构的新身份可继承已验证能力，新的 failed/blocked 观察优先。

| 配置 | 权威用途 |
| --- | --- |
| `markets.json`、`market-content.json` | 市场能力、语言和固定内容 |
| `catalog-screen.json`、`catalog-link-policy.json`、`link-naming*.json` | 筛分、分佣、命名 |
| `leads-queue.json`、`identity-run.json`、`link-prepare*.json`、`selection-run.json`、`cycle-inbox.json`、`agent-reply-run.json` | 查询/身份/准备参数与 IT `job_run` 作业参数 |
| `operations-policy.json`、`jobs.json` | 稳定调度策略、共享供给时间 |
| `agent-reply-guide-v2.txt` / guide revision | 当前 Agent 指南 |
| `agent_reply_setting`、`continuous_send_control` | 市场持久授权、窗口与发送设置 |
| `state-backup.json` | 备份库清单与排除规则 |
| `send-batch.json`、`reply-policy.json` | 名称沿用旧批次/V1，但仍在用：前者提供 IT 发送控制缺省值与窗口显示，后者提供 IT Agent 模板缺省与 V1 约束 |
| `evals/agent-v2-multiturn.json` | V2 离线多轮评测用例 |
| `typesafe.json` | 已退出运行依赖的历史私密配置；本次未删除，仍由备份排除/保护规则管理 |
| `*.example.json` | 敏感配置样例；真实凭据仅本机忽略文件/环境 |

页面保存会改写受 git 跟踪的 `config/*.json`（`jobs.py`、`job_run.py`、`leads_queue.py`、`link_naming.py`、`send_batch.py`），提交前需区分运营设置变化与代码改动。

`legacy_runtime.configure_vendored_bdhub()` 强制从本仓 vendor 加载协议，已加载其他源码则失败。旧目录在运行时只读提供尚未迁移的账号配置、保存凭据、签名 runtime 与 DeepSeek key 回退；IT 每次写入前还会只读检查旧系统写门禁目录和 PostgreSQL（`second_live_runtime.conflicts()`），旧库不可用时 IT 发送与回复报 `legacy_state_unavailable`。`scripts/vendor-runtime/manifest.json` 固定源码、必要资源与本地补丁 hash；`--check` 漂移非零退出，`--write` 在临时目录重建验证后交换。pure_http_runtime_manifest.json 属于运行闭包，不能按“非 Python”删除。细节见 [vendor README](../vendor/README.md)。

## 6. 尚未完成的合同与验证边界

- **全托选入：** 单 PID 未知已按 §3 缩小为 PID 级缺口；容忍上限 3 为工程保护值，尚无业务样本校准。`skip_unknown` 仍只对两次延迟缺失的原项生效。
- **发送额度：** 只有平台额度回执（状态 3、检查码 100、`im_limit_reached`）暂停当日新联系；其他单次拒绝只结束该达人，同一非额度特征当日重复 3 次按系统性拒绝暂停并单列原因。额度闸门按北京日解除；按平台实际恢复信号解除（G14）经用户决定不做。
- **AI 失败与未知：** `agent_reply_v2.generate` 已按 input_hash 限制三次；错误分型、服务级熔断与暂停展示已实施（见上文），熔断按调用代次（epoch）结算，只有当前代次的探针能关闭暂停。会话翻译、商品短名、素材准备经 `model_service.guard` 共用同一熔断（离线评估与已退役的影子分类器除外）。回复送达未知在核验预算内仍设市场级门禁（最长约 15 分钟），隔离后只按达人阻断；核验参数为工程评估值，尚无真实 unknown 样本。
- **二发隔离后的服务：** `quarantined_unknown` 已释放二发派发槽；新来信的独立 AI 服务许可（模块方案 §9.19，G13）经用户决定不做，因此不能由“不再阻塞二发”推导“新来信必然可自动回复”。
- **上下文：** 全部当前未答消息范围、生产 live 模式和超长输入拦截已接入；长期提醒记忆、自动分段以及严格按历史指南/关系/事实回放尚未完成。
- **账号恢复：** 认证错误判定与恢复请求已统一到 `login_recovery`（见上文）；刷新/重登仍串行执行，SDK owner 在维护前排空借用客户端，真实维护与写入交错尚无业务样本。
- **规模：** 会话队列已有游标与索引。生产实测的主要耗时来自按市场全量解析投递回执（随发送历史每天增长），现改由 SQLite `json_extract` 读取 messageId，生产备份上 24 个视图结果与旧实现一致，单次读取约减半（IT 约 0.36→0.2 秒）；AI 待办 `pending_rows` 实测 7–25 毫秒。按候选逐行分类仍在，增量队列读模型等回执或候选规模再增长时再做。
- **调度粒度：** 市场轮流已实施；阶段在等待账号恢复时把 `platform:global` 借给其他市场（账号槽与市场主链槽保留），恢复后取回再继续读取，取不回 1 小时即以 `platform_slot_reclaim_timeout` 失败，不会无槽读取平台。重阶段（如一次 Campaign 采集约 20–30 分钟、首次建链 30–60 分钟）按设计跑完再让出，不中途让槽：评估后认为被挡住的主要是 OECID，晚一段时间进发送池对当天发送影响很小，而中途暂停会拉长长任务、引入分页状态失效与“取不回槽”的新失败方式（2026-09-26 用户确认不做）。
- **Kalodata 贡献：** 每次 A/B 发布在同一事务内、写入前记一条 `lead_publication_contribution`（本 PID 新增的达人×商品、刷新的已有组合、该市场从未出现过的达人；重复发布不重计），最近一次抓取汇总其时间窗内的贡献。线索状态另给出近 7 天发布的 A/B 线索后来被采用、确认发出、触达达人数与卡片后回复的达人数（`later_outcomes`）：每次发送只归到它发送时冻结的来源，属观察窗口内的相关统计，不是因果转化，也不含成交金额；“首次达到可发送条件”仍未单独记录。
- **收信/吞吐：** SDK 初始化成功和周期续期已验证，HTTP 借用仍可能在续期交接时短暂等待；四市场自然新来信时延、正式二发窗口卡文吞吐和完整维护故障交错仍需实际业务样本。账号内 HTTP 预算当前按进程设置，尚无统一的账号总请求限速器。

以上是代码边界，不更改已确认业务目标。后续任务与原审查发现见[模块方案](implementation/module-optimization-plan-20260925.md)和[AI 回复优化方案](architecture/ai-reply-optimization-20260925.md)。

## 7. 运行与验证

```bash
# 项目根目录
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/check-docs.py
.venv/bin/python scripts/vendor-legacy-bdhub.py --check
.venv/bin/python scripts/migrate-agent.py check
.venv/bin/python scripts/migrate-lead-receipts.py check
```

```bash
# apps/web
npm ci
npm test
npm run build
npm run typecheck
npm run dev   # 开发；生产使用 npm run start，不能同时占用 5198
```

本工作目录就是生产：scheduler 按路径拉起子脚本，`io.bdhub.agent.web`（`launchctl submit`）在 `apps/web` 运行 `next start`。开发与验证在 `/Users/bjn00003/BDHub/` 下的独立 git worktree 进行（代码按 `ROOT.parent/'01-BDSystem-V2'` 定位旧项目；`.venv` 可软链生产 venv），不在生产目录 build 或 dev，也不要同时起第二个 5198。`tsc` 会读取 `.next/types`，删改路由后先 build 再 typecheck。部署前核对 PID/cwd/worker 依赖，替换后回读 API；源码变更不自动重启持久 worker 或改授权。状态查询使用 CLI status/check，不用 run-agent-replies 等执行入口探测状态。手动作业优先页面/job-run，不绕过同名作业锁另开底层 CLI。

历史补扫：`inbox-history.py --market <market> --cid <cid> --oec <oec>` 默认只读本地状态，显式 `--read --max-pages 5 --page-size 20` 才做平台只读与历史写入；单会话锁和暂停文件约束执行，断点未完可重跑同一命令。

迁移/回填前先 `state-backup.py create --label <label>` 并 verify（单库变更可只做该库 online backup，保存 hash/quick_check 回执）；apply 仅按明确范围升级既有库。`migrate-lead-receipts.py` 缺 schema 拒绝运行，不在 worker 启动时隐式升级；backfill-current-bindings/leads 的 check 只读，apply 只本地投影，无平台调用。只读检查直接 SQLite 时使用 mode=ro。

交付分别报告：合同测试、类型/构建、本机 API、只读真实数据、平台回执、业务结果。不把历史测试数、旧吞吐或存活进程当当前验收。结构改进与历史实测限制见[项目审计与清理](implementation/project-audit-20260924.md)。
