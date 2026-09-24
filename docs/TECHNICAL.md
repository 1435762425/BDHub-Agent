# BDHub-Agent 技术文档

当前实现合同，整理于 2026-09-24。产品规则见 [PROJECT.md](PROJECT.md)，运行快照见[当前交接](handoff/current.md)。本文保留模块入口、状态边界及恢复方法，实验数值与事故流水只链接证据。

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

Web 路由与 bridge 显式携带 market，URL/body/CLI/返回值一致；约 10 个 CLI 的 `--market` 仍缺省为 `it`，手工调用必须显式传参。`markets.json` 是唯一市场注册表，未验收能力拒绝动作，空市场不回退 IT。注册表只登记 `campaignCatalog`、`fullManagedCatalog` 两个能力位；TapLink 清理、B 类视频线索、会话页人工文本/卡片、身份队列/精确发现/画像刷新、Kalodata 身份和全托作业页目前只在 IT 实现，由代码直接拒绝其他市场。首页使用 `market_read_head → market_read_generation` 只读快照，同市场 GET 由 singleflight 合并；缺失/过期才本地聚合。快照失败不修改 workflow 结果，筛分摘要必须匹配当前来源 head。

## 2. 领域模块与合同

### 2.1 货盘与选入

| 入口/模块 | 职责 |
| --- | --- |
| `global-source-control.py` / `global_source.py` | 全托 run、分页、分区、覆盖与 head |
| `global_selection.py` / `global_selection_fast.py` | 持久选入队列、单 PID 提交、批后回读 |
| `campaign-join.py` / `campaign-collect.py` | 加入意图、未知核验、已加入列表完整采集 |
| `global_screen.py` / `campaign_screen.py` / `catalog_binding.py` | 商品筛分、Offer 指纹与唯一当前绑定 |

来源完整发布才推进下游。类目采集按一级类目保存页码、reported total、唯一数和停止原因，完成后跨类目 PID 去重。普通周更不替换更广类目 head：`coverageOverlay` 复制基线，仅更新重叠 PID，保留类目/部分接受证据；范围外 PID 留在原查询。月度 cadence 取原类目 run 时间。

分页重复先保存 repair page 回执再补洞/复读，符合 PROJECT 稳定重复行合同后才能发布。总数变化且无重复页可补时停止为空转错误；`--retry-partial-category` 仅重读原未发布分区。`accepted_partial` 必须由明确操作写入 `global_source_operator_acceptance`，API 不得显示 complete。

IT `full_catalog_collection_mode()` 固定普通周更，拒绝新的 `--by-category`，不续已停止类目；UK 按首次/月度类目、其余普通查询。停止/修复原来源前先核对 worker、锁、原 run 和备份，不用新 run 掩盖断点。

选入当前使用已验证单 PID `/pick_up/select`，不是未验收的 batch_select。先落意图，批后统一回读；验证码/登录明确拒绝，须同时有已选池缺失、当前 listing 未选入及同账号验证/新代次证据，才能重放同一冻结请求。网络歧义/code0 缺回读不重发，仍未知保留 `skipped_unknown`，后续 generation 不重新入队。真实性能证据见[四市场报告](implementation/four-market-launch-implementation-20260923.md)。

### 2.2 TapLink 与当前材料

`catalog_links.py` 保存创建意图，`catalog_prepare.py` 和 `catalog-link-batch.py` 驱动准备，`catalog_clean.py` 核验清理，`link_naming.py` 读取市场短名。读链、创建、清理共享同一账本；`creates=0` 只读。

`catalog_current_binding` 是发送唯一材料投影，键覆盖 market/PID/来源/Campaign/分佣与命名指纹，保存 `currentListId`。普通历史卡不参与当前复用；仅同标准、同绑定且已核验卡幂等复用。完整 Campaign 筛分后 `reconcile_current_offers()` 将不再合格绑定 inactive、条款变化 waiting_refresh，全部写 event。

短名按 `cycle_product_name.locale` 校验，非 IT 不读 IT 缓存或截断标题；缺失停止创建。IT 准备顺序 seed/read → names → create，其他市场 seed → localized names → read → create。新建回读未知隔离该 PID，整批写完公共回读，再按 30/120 秒轮询两次；仍缺失只保留 unknown，不重复 POST。

清理只删除整卡平台 invalid，内部期限/佣金门槛仅本地停用。`taplink_clean` 阶段目前只对 IT 执行，BR/MY/UK 整段跳过（`market_taplink_cleanup_not_enabled`），既不本地停用也不做只读扫描。删除收口后完整回读，仍存在记 failed_known，不自动重复 DELETE。发送端本地核对 binding/Offer/currentListId，不调用 fresh_card。

### 2.3 工作流与调度

`operations_workflow.py` 保存不可变 run/stage/generation/checkpoint，`operations_scheduler.py` 执行和监督。开关来自市场 durable setting，共享供给时间来自 `jobs.json`，市场发送/Agent 窗口来自各自持久控制。`jobs.json` 各阶段的 `enabled` 已无读取方；`inbox_monitor.enabled` 仍可单独拉起 IT 收信（与自动运营总开关任一为真即启动），`continuous_send.enabled` 被 `jobs.save` 拒绝修改。全托周更到期时间沿用 `taplink_clean` 的 `at`/`weekday`，`full_catalog_update.at` 只在页面展示。

- 同市场按上游 generation 串行。TikTok 平台上的重型阶段（catalog、taplink_clean、taplink_prepare、oecid）额外占用全局槽 `platform:global`，容量 1（`workflow_dispatch.PLATFORM_PARALLEL_MARKETS`），所以同一时间只有一个市场在做平台重型读取，其余市场排队；Kalodata 用自己的 `kalodata:global` 两槽，可与另一市场的平台阶段并行。
- `workflow_stage_claim/workflow_resource_slot` 短事务领取 owner/fence/300 秒 lease，执行每 30 秒续租；确认 owner 已退出才回收。子进程先登记真实 PID 再执行，父死子活仍占槽。
- 锁占用、无效 CLI 输出、部分范围失败、pending、stuck 不能报 completed。外层平台写入数汇总内部 pass，item count 取最终 summary，不能把重复 pass 累加。
- 仅零平台写入、无 unknown/unresolved/ambiguous、claim 已释放的失败阶段，才在原 run/上游 generation 间隔 ≥1 小时重排，最多三次；不重跑完成的上游，不依赖来源下一次到期。其余转 needs_human。
- 恢复短名或 Kalodata 漏参预检需精确校验原错误、原阶段、零写入、当前证据和 claim，恢复事件写原 checkpoint；专用入口不得用于已实际执行的失败。
- 长任务等待仍监督收信、发送、Agent 和账号维护。错误只向页面提供脱敏稳定码；普通失败退避与 unknown 核验分开。

`workflow_recovery.py` 对原选入轮次和后起零写重复轮次核验 schedule/config、原 upstream generation、已发布 source/overlay 与精确选入回执；活动失配仍拒绝，除非操作者用 `isolatePids` 明确隔离：该项改为 `isolated_unverified`、原证据保留，链接准备（只取 confirmed）本轮不再使用。设置检查要求自动运营与全托周更都开启、revision 不低于原轮（暂停后重开只改 revision）。恢复保留原请求与累计写计数，以幂等 checkpoint 重排原 catalog，不把其它 source 改算本轮成功。`resume-video-author-skip` 只在 IT Kalodata 阶段因 `kalodata_video_author_missing` 转人工、且最新一代视频扫描正是在该阶段运行期间以同一错误停下时，把该阶段重排一次；扫描从原代次断点续跑，换请求号的第二次恢复被拒绝。`resume-kalodata-auth` 对任一市场因 `kalodata_auth_required` 转人工的 Kalodata 阶段做同样的一次性重排，前提是停下之后已有 Kalodata 页面读取成功（共用同一 Kalodata 登录）；A 类队列持久，续跑只处理仍到期的 PID。实际恢复前需核对运行进程并加载对应 scheduler，检查通过不产生新授权。

### 2.4 线索与身份

`leads_queue.py` 管理 PID 到期/尝试，`leads-run.py` 读取 A 类，`kalodata_video_scan.py` 管理 B 类扫描、作者缓存与 current 投影。`leads_page_scope` 以市场/PID/查询窗口生成 query ID，原窗口断点复用，新窗口重新读取；完整发布后才更新 queried_at，旧 receipt 转历史表。

`lead_query_head/selection` 发布每 PID 当前 A 类范围，`video_lead_current` 发布当前最高单视频 B 类。两类保存来源时间、数值币种与原始证据；额度耗尽只发布已完整完成的 PID。Kalodata 额度按市场分别计算，一个市场耗尽不影响其他市场读取。B 类重复页、坏日期、缺作者或中断不能冒充完整覆盖：扫描中详情缺作者的单条视频记为 `author_missing`（保留响应 hash），该 PID 的 run 记 `completed_with_gaps`，其余视频照常进入 current 投影；同一代超过 20 条缺作者时整体停下转人工。

B 类目前只在 IT 的 Kalodata 阶段执行（`kalodata-video-crawl.py` 没有 `--market`，`video_lead_current` 没有 market 列），BR/MY/UK 发送池只有 A 类。B 类作者只按 `creator_identity.current_handle` 匹配已知达人；OECID 提交（`cycle_identity.freeze`）只取 A 类 selection，未知作者计入 `videoUnresolved`，不进入可发层。每次 IT Kalodata 阶段新建一代 B 类，窗口截至两天前；没有单独的 7 天重读节奏（7 天是 A 类队列的 `refreshDays`）。**已知缺陷**：新一代初始化时立即清空 `video_lead_current` 与 `kalodata_video_head`（`kalodata_video_scan.py`），扫描完成前 B 类位置消失，与 PROJECT §3.1“未完成时继续消费上一完整版本”不符。

`creator_discovery.py/discovery_cohort.py` 执行精确 Find，`identity_queue.py/identity-batch.py` 聚合去重身份，`profile_refresh.py` 独立刷新画像。resolved/unresolved 复用证据，blocked 保留原断点。`IdentityBridge` 只交接当前 head 的 source edge，固定索引顺序避免历史全表 JSON 扫描；OECID 发布不能等待或伪装 Profile 成功。BR/MY/UK 的 OECID 由 scheduler 调 `market-identity.py`（`lib/market_identity.py`）：每 3 个 handle 拉起一次 `probe-italy-profile.py`，证据写入 `var/market-identity-{market}-*` 目录（目前不清理）。页面的身份队列、精确发现与画像刷新接口只支持 IT。

Find lanes 共用账号 QPS 和一次串行滑块结果，同账号原请求重放成功后才落身份；失败只存脱敏分类。OECID stage 排空当前 outbox 并复用已判定终态，任何 pending/queue_stalled/blocked 不发布完成 generation。稳定主键为 market×OECID，改名只追加 alias。

### 2.5 发送与 72 小时冷却

| 模块 | 职责 |
| --- | --- |
| `lead_pool.py` | 当前资格、A/B 排序（`strength()`）、单达人槽位 |
| `cycle_review.py` | 领取前身份、材料、关系、去重复检 |
| `cycle_delivery.py` / `cycle_executor.py` | 不可变 delivery、组件意图、额度和回执 |
| `continuous_send.py` / `continuous-send-worker.py` | IT 控制、窗口、断点与 worker |
| `market_send_control.py` / `market_send_canary.py` / `market-send-worker.py` | BR/MY/UK 控制与持续发送；`market_send_canary.run` 是三市场正式发送核心，名称沿用首测阶段 |
| `request_budget.py` | 进程内请求节拍：每个 runtime 各持一份，不跨进程；跨进程只共享写门禁 |
| `cycle_service.py` / `collaboration_status.py` | 案件、人工接管、合作状态 |

同市场同达人主动推品冷却统一 **72 小时**；池投影、领取前复检和最终平台写门禁必须同值，回复/橱窗不缩短。人工客服与新来信回复不套用主动推品冷却。`outreach_policy.py` 将已确认/部分送达的末次组件时间与同 plan/OEC 的平台我方外发观测时间取较晚值；逐写仅排除当前 delivery 在原 cid 已确认的 messageId。未知意图仍独立阻断，不靠冷却结束放行。

每位领取冻结全部发送事实，逐次发卡/文字前复检停止、时间窗、身份维护、关系和当前材料。容量预检在建会话/发卡前，最终滚动 24 小时预留在短事务复核；已解锁关系、或 24 小时内已为同一达人预留过的，不再占新联系名额（`cycle_delivery.contact_capacity_available`）。本地上限 `NEW_CONTACT_LIMIT` 现为 None，改由平台机构额度决定；`cycle_platform_signal` 里同一北京日 outcome=rejected 的平台回执达到 `PLATFORM_REJECTION_HOLD`（1 次）时，同一闸门拒绝新联系到次日，发送 worker 进入 waiting_capacity（每 5 分钟复查）。IT 与 BR/MY/UK 两类发送都会结算已派发组件：明确拒收（状态 1–5）记 rejected，其它记 unknown 等回查；带响应的平台回执全部落账（`market_send_canary._record_send_failure`）。建会话返回非零码仍记为 unknown 并停发，待核验原意图；首次撞到平台额度后，按落账的原生码补明确分类。页面与状态接口的 capacity.limit/remaining 为 null 时显示“由平台机构额度决定”，告警条报当天平台拒绝次数。卡＋文字要求两个剩余消息槽；历史单卡确认而文字未尝试已触限，只结算未发送文字，保留 partial_delivery。冻结后 30 分钟过期的投递，发送 worker 每轮先用 `cancel_expired_unsubmitted` 结算，不迟发：建会话未开始、各组件未发出的记 cancelled；卡已确认、文字未开始的只取消文字，记 partial_delivery；可能已到达平台的（会话创建已开始、组件在途或 unknown）留给核验。卡片 unknown、文字未开始，且至少两条间隔 ≥300 秒的回查读到历史却没有这条消息（`it_delivery_history_not_found`，读不到历史不算）时，两类发送的回查都会调用 `quarantine_absent_card`：投递记 quarantined_unknown，达人转 human，开 `card_result_unknown` 人工 case，不重发，市场继续。建会话返回业务码 201 且平台回执已落账的 unknown 投递，每轮开始由 `quarantine_refused_creates` 同样隔离（case `conversation_business_rejected`）；其它非零码仍为 unknown。并发发送名额（`begin`）不计已隔离投递。已落账的额度回执为发送状态 3、check_code 100、`im_limit_reached`。发送 worker 遇到结果未知不再退出（调度器不会重拉停在 unknown 的发送），改为 waiting_reconciliation，每 5 分钟用原请求只读回查一次；建会话结果未知仍需人工隔离。适配器会把发送许可里的本地拒绝统一改写成 `it_delivery_dispatch_not_allowed`，调用方看不到原因码（如 delivery_expired、marketing_cooldown），这是待修的已知缺陷。

组件已提交只读精确回查。`/api/send` reconcile 即使停止/窗口外也只查原账号/requestRef，结束即返回，不能领取下一人或启动未提交组件。会话 received 只有原 requestRef/CID/达人身份全部匹配才可确认，缺回执 inflight 保持未知。IT 已授权的零卡文未知会话隔离保留原意图和案件，不改成失败/成功，不对该达人再发。

worker 在等待窗口、池或容量时常驻退避；同账号在途写串行，僵尸进程不算存活。账号认证可短复用同进程/身份指纹/代次，逐位和逐写复检不省略。单账号串行是当前正式路径；30 人/分钟未由代码配置证明。

### 2.6 双向消息与会话

`cycle_inbox.py`、`poll-cycle-inbox.py`、`poll-market-inbox.py` 负责最近会话发现与旧 checkpoint 公平补扫；`inbox_event/inbox_content*` 保存事件和正文。新会话精确匹配本市场关系后落库；冷 checkpoint 也重新校验 plan，合作状态不能回退 IT。

机构后台 `ourMessages` 与达人消息一并持久化正文、发生时间、精确 messageId 和市场/会话身份。保存或编辑我方正文不创建达人回复任务；只有外发、尚无达人入站的会话也可打开。`conversation_workbench.py` 与 V2 上下文读取同一事实；与本地 delivery/service_reply 的重复按精确 messageId 合并，不能按文本相同去重，不能把我方消息误作达人 pending。`observed_messages.py` 按 plan/OEC/cid/messageId 排除本系统已有回执，只把其余平台消息投影为 observed。热读每会话取最新 20 条，优先补缺失机构正文；`inbox-history.py` 使用原生 OLDER 游标做有界历史补扫，按页原子保存去重回执和断点。历史断点绑定原账号、IM 身份、完整 CID/OEC 与市场；不推进热读水位，不解除 gap/unknown，不创建回复任务。`completeOlderRange` 只证明从首次成功读取时间向旧的游标链完成；延后热读项单列，不能据此声称全部正文已入库。

首次 checkpoint 有项目已确认外发时，以文字开始时间区分实时回复；否则首次导入为历史。已确认卡和文字按各自 `cycle_delivery_part` 状态/时间投影，不用 episode 冻结正文冒充已发文字。showcaseNotifications 单列系统事件，不输入文字分类器。消息完整性以覆盖/水位证明，worker 存活不足以证明新鲜。

IT 默认热点＋冷 checkpoint 每轮 12 个/30 秒；其他三市场 20 个/10 秒，每个 poller 进程各自 2 QPS 读预算（进程内节拍，收信与发送不共用）。认证短持 profile 租约后释放，每次读取仍核验维护与身份指纹；账号忙显示 waiting_account。扫描规模增长后应按真实覆盖延迟调参，不能仅提高 tick。

### 2.7 Agent V2 与回复 transport

`agent_reply_v2.py` 使用 `config/agent-reply-guide-v2.txt` 或 append-only `agent_reply_guide_revision`，从目标入站前的真实双向消息、商品事实和市场语言编译提示词。`agent_reply_decision_v2` 保存真实输入/输出/错误、指南版本及关联意图；模型只输出 reply/no_reply/request_detail/handoff，不提供账号或 transport 参数。

指南包含肯定合作但未观察到当前商品橱窗证据时的加橱窗提醒，要求已有真实卡、不重复已提醒内容，并按市场语言说明下一个视频/直播生效。上下文提供实际观察事实；“未观察到”不升级成“核实没有”。

`run-agent-replies.py` 先回查 inflight/accepted/unknown，再处理新 turn；模型失败逐达人隔离，同输入最多三次。确定未提交而上下文过期的 ready 可审计终结，已提交只核验。IT 人工/Agent 共用 `reply_transport.py`，其他市场用 `market_agent_reply.py`，均冻结唯一 `service_reply` 正文/requestRef 再发送。

模型调用统一经 `draft_provider.py`：DeepSeek `https://api.deepseek.com/chat/completions`、模型 `deepseek-flash`，JSON object 输出、关闭 thinking，单次请求不重试，上限约 24,000 输入字节、1,200 输出 token、60 秒。密钥先读 `DEEPSEEK_API_KEY`，缺失时只读旧项目 `01-BDSystem-V2/config.yaml` 的 `reply.api_key`（绝对路径写死）。用途：Agent V2、会话翻译、商品短名和离线评测。`typesafe_provider.py` 只服务 `reply_events.py` 里的 V1 历史分类代码，已无页面入口。

运营在机构后台于目标入站之后已回复时，旧 turn 暂停自动回答；不擅自结案，下一条达人来信重新进入处理。prepare 与逐写前校验最新 turn、pending/control revision、人工案件及平台外发。澄清/联系方式确认送达进入 waiting_clarification/waiting_contact，礼貌回复未提供资料仍保持等待。handoff 先建 case 和锁，再准备唯一确认消息；人工接管后 AI 停止。

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

`state-retention.py` 执行历史保留：Campaign/全托货盘快照保留当前加最近 2 个完整版本；Campaign 采集 JSON 每组只留最新一份用于重同步；每轮报告 JSON 与 `var/cycle-scheduler/` 阶段日志保留 14 天；账号身份目录保留当前与上一代（72 小时内的目录不动）；超过 20MB 的日志轮转。当前 head、head 覆盖引用的基线/刷新轮次、未结束 workflow 对应的来源轮次、未完成轮次和被未结束阶段记录引用的文件一律保留。`plan` 只读；`apply --confirm [--vacuum]` 先把候选写入仓库外 `../BDHub-Agent-backups/retention/<时间>/` 并校验，再删除原件；全托轮次只删明细行，保留轮次记录；`restore --archive <目录> --confirm` 补回缺失的行和文件。应在 worker 暂停或空闲时执行。

`state-backup.py` 按 `config/state-backup.json` 显式清单执行 SQLite online backup，保存 hash、quick_check、schema；新业务库未入清单则失败。备份目录/文件为 0700/0600，敏感配置独立管理。restore 只接受空目标并生成回执；跨机器切换需排空写 worker 后最终备份，多库备份不声称跨库单事务。`retention-plan` 校验现存备份并只生成保留建议（默认 7 天全部、30 天每日、84 天每周，每种库清单至少 3 份）；迁移标签、损坏和不明文件保留，无删除操作。

`offsite-backup.py` 把代码、当前状态和本机专有配置加密写到外接盘。每次先按 `state-backup.json` 做一份全量 online backup 到临时目录，连同 `git bundle --all`、未提交改动补丁、`../BDHub-Agent-backups/` 下的归档（git 离线包与密钥目录除外）和 3 个被忽略的敏感配置打成 tar.gz，经 openssl AES-256-CBC（PBKDF2 20 万次）直接写入 `<盘>/BDHub-Agent-offsite/<UTC 时间>/`；刷到设备后解密回读，核对明文 hash 与成员，最后写 manifest 表示完成，失败则删除本次目录。盘上只动这个文件夹，保留最近 3 份。密钥 `../BDHub-Agent-backups/offsite/offsite.key` 只在本机，须另存一份到密码管理器，丢失则副本无法解开。账号浏览器身份、`var/` 运行文件和旧项目 `01-BDSystem-V2`（账号配置、保存凭据、签名 runtime）不在副本内，换机恢复仍需旧项目。`plan` 只读，`run --confirm` 写入，`verify` 从盘上重读最新一份；`auto` 供插盘触发，只处理已有该文件夹的卷，先校验最新一份，超过 20 小时或校验失败才写新的，并发系统通知。每次写入成功后，在本机 `offsite/latest.json` 记下这份副本，告警条据此判断备份是否过期。

`ops-alerts.py`（`lib/ops_alerts.py`）为页面顶部告警条汇总异常，只读、不启动或重试任何作业，只读 SQLite。`gather` 读取页面本来就用的台账和状态文件：调度器运行态与停止文件、各市场收信状态文件、最新一轮 workflow 各阶段、`unknown`/`quarantined_unknown` 发送、未结人工 case、账号维护队列与下次维护时间、Agent 设置与首发阶段、`offsite/latest.json`。`evaluate` 把这些事实转成告警，分 critical/warning/info 三级：
- 调度器没有停止请求却不在运行 → critical；有停止请求 → “生产已暂停”提示，暂停直接造成的收信停滞不报，身份维护逾期降为提示。
- 收信错误码（`live_guard_busy` 与停止请求触发的 `stopped` 除外）且最后成功超过 10 分钟 → 运行中为 critical、暂停时为 warning；运行中收信超过 15 分钟没有新记录 → critical。
- 未回来信用会话列表 unread 标记的同一规则（`conversation_workbench.UNREAD_PENDING` 且机构后台未在达人最新消息后回复），最早一条超过 26 小时（错过一个每日回复窗口）才升为 warning。
- 身份维护超过下次维护时间 2 小时且不在队列中才报；U 盘备份缺失或超过 7 天报 warning。

某个市场读取失败只报该市场，不影响其它告警。收信状态文件里的 `pendingContent` 统计全部 `inbox_pending` 行（含已处理状态），不能当待回复数。

`delivery-diagnostics.py` 只读发送台账与已有 timing，按完整确认触达的首次组件开始时间汇总。组件开始到首次确认回查包含等待/恢复，不能当 HTTP 耗时；缺失认证样本时不能判定认证瓶颈。

## 4. API 与 Web

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
| `typesafe.json` | 仅 V1 历史分类使用的本机忽略文件 |
| `*.example.json` | 敏感配置样例；真实凭据仅本机忽略文件/环境 |

页面保存会改写受 git 跟踪的 `config/*.json`（`jobs.py`、`job_run.py`、`leads_queue.py`、`link_naming.py`、`send_batch.py`），提交前需区分运营设置变化与代码改动。

`legacy_runtime.configure_vendored_bdhub()` 强制从本仓 vendor 加载协议，已加载其他源码则失败。旧目录在运行时只读提供尚未迁移的账号配置、保存凭据、签名 runtime 与 DeepSeek key 回退；IT 每次写入前还会只读检查旧系统写门禁目录和 PostgreSQL（`second_live_runtime.conflicts()`），旧库不可用时 IT 发送与回复报 `legacy_state_unavailable`。`scripts/vendor-runtime/manifest.json` 固定源码、必要资源与本地补丁 hash；`--check` 漂移非零退出，`--write` 在临时目录重建验证后交换。pure_http_runtime_manifest.json 属于运行闭包，不能按“非 Python”删除。细节见 [vendor README](../vendor/README.md)。

## 6. 运行与验证

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
npm run typecheck
npm run build
npm run dev   # 开发；生产使用 npm run start，不能同时占用 5198
```

本工作目录就是生产：scheduler 按路径拉起子脚本，`io.bdhub.agent.web`（`launchctl submit`）在 `apps/web` 运行 `next start`。开发与验证在 `/Users/bjn00003/BDHub/` 下的独立 git worktree 进行（代码按 `ROOT.parent/'01-BDSystem-V2'` 定位旧项目；`.venv` 可软链生产 venv），不在生产目录 build 或 dev，也不要同时起第二个 5198。`tsc` 会读取 `.next/types`，删改路由后先 build 再 typecheck。部署前核对 PID/cwd/worker 依赖，替换后回读 API；源码变更不自动重启持久 worker 或改授权。状态查询使用 CLI status/check，不用 run-agent-replies 等执行入口探测状态。手动作业优先页面/job-run，不绕过同名作业锁另开底层 CLI。

历史补扫：`inbox-history.py --market <market> --cid <cid> --oec <oec>` 默认只读本地状态，显式 `--read --max-pages 5 --page-size 20` 才做平台只读与历史写入；单会话锁和暂停文件约束执行，断点未完可重跑同一命令。

迁移/回填前先 `state-backup.py create --label <label>` 并 verify（单库变更可只做该库 online backup，保存 hash/quick_check 回执）；apply 仅按明确范围升级既有库。`migrate-lead-receipts.py` 缺 schema 拒绝运行，不在 worker 启动时隐式升级；backfill-current-bindings/leads 的 check 只读，apply 只本地投影，无平台调用。只读检查直接 SQLite 时使用 mode=ro。

交付分别报告：合同测试、类型/构建、本机 API、只读真实数据、平台回执、业务结果。不把历史测试数、旧吞吐或存活进程当当前验收。结构改进与历史实测限制见[项目审计与清理](implementation/project-audit-20260924.md)。
