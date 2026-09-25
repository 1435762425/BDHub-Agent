# 各功能模块后续优化方案（2026-09-25）

范围：按功能模块给出后续开发方案，交给 Codex 审查后实施。现状以 2026-09-25 上午的代码（`05a0649`）和只读运行数据为准；运行快照见[交接](../handoff/current.md)，实现细节见 [TECHNICAL](../TECHNICAL.md)，上一轮审计见[项目审计与清理](project-audit-20260924.md)。本文不授权任何平台写入、进程启停或开关变更。

## 1. 总原则（用户 2026-09-25 确定）

1. **一套逻辑覆盖所有市场。** 市场差异只进配置：市场注册表（`markets.json`）、账号角色（`market-accounts.json`）、语言与内容（`market-content.json`）、额度与节奏（`operations-policy.json`）。新代码不写 `if market == 'it'` 这类分支；现有的 IT 与 BR/MY/UK 两套实现逐步合并。
2. **先看清再优化。** 先做每市场每日漏斗看板，用数字决定后续投入。
3. **结果未知的处理统一。** 所有平台写入都走同一条路：记录意图 → 结果未知 → 只按原意图只读核验 → 证据够就隔离并转人工 → 其余流程继续。不换意图重发，不把未知改判成功。
4. **执行边界不变**，以 [AGENTS.md](../../AGENTS.md) 为准：
   - 在独立 worktree 开发，全量 unittest（以及相关 Web 测试、build、typecheck）通过后才 ff 合入。
   - 合入不自动重启 worker。
   - 迁移只做增量加法且保证幂等，不删库重建。
   - 对生产数据只读试跑（`mode=ro`），用来抓测试覆盖不到的问题（09-25 曾靠这一步发现漏掉的导入）。

## 2. 推进顺序

| 阶段 | 内容 | 依赖 |
| --- | --- | --- |
| ① | 每市场每日漏斗看板（§3） | 无 |
| ② | OECID 补齐：统一实现、未解析重试节奏、B 类作者送解析、摆脱旧系统签名（§4.4） | ① 用来量化效果 |
| ③ | 统一“结果未知 → 只读核验 → 隔离 → 流程继续”框架（§5） | 无，可与 ② 并行设计 |
| ④ | 合并 IT 与其他市场的收信、二发、AI 回复发送、TapLink 清理、Kalodata B 类（§4.2、§4.5–4.8） | ③ 完成后再合并发送类 |
| 穿插 | 编排修复（§4.10 第 1、2 条）、适配器原因码（§4.7 第 2 条）、B 类清空缺陷（§4.5 第 1 条） | 小而独立，随时可做 |

每一步先给用户简短计划，确认后实施。

## 3. ① 每市场每日漏斗看板

**目标：** 在市场首页看到近 7 个北京日各层的数量，以及哪一层流失最多：

Campaign 商品 → 已建 TapLink → Kalodata 线索（A/B）→ 已解析 OECID → 可发 → 已确认触达 → 达人回复 → 加橱窗

**实现：**
- 新增 `scripts/lib/market_funnel.py` 和 CLI `scripts/market-funnel.py`（固定 argv，只读），经 server bridge 接到 `/api/market-funnel`，市场首页加一块卡片。
- 数据全部复用现有口径，不另算：
  - 商品与 TapLink：取 workflow 各阶段 `outputGenerationId` 对应的 head，以及 `catalog_current_binding`。
  - 线索、OECID、可发：取 `lead_pool.py` 的 counts。
  - 触达、回复、橱窗：取 `cycle_stats.py` 按北京日的口径。触达只算 `cycle_delivery_part.state='confirmed'`，排除历史补录。
- `lead_pool.pool()` 本来就有 market 参数，`cycle_stats` 按 plan_id 取数，两者都不需要改；市场到 plan 的映射从市场注册表读取。
- 数据缺失显示“不可用”，不显示 0；每层给出来源与计算时间。

**验收：**
- 四个市场都能出数，而且市场之间没有代码分支。
- 同一天的触达、回复、橱窗与会话页和日历页一致。
- 快照测试覆盖缺数据、跨日边界和多市场。

## 4. 各模块方案

每个模块按“现状 / 短板 / 方案 / 验收”写。完善度评估沿用原会话结论。

### 4.1 加入 Campaign（完善度：中高）

- **现状：** 四个市场都能跑。加入属于平台写入，先记意图，结果不明时先核验。
- **短板：**
  - 单个 Campaign 加入失败会让整轮停下（IT 09-23 `campaign-join-status_failed`），而且报错信息很少。
  - `/api/campaign-join` 把所有以 `campaign_` 开头的 CLI 崩溃都映射成 409，还把 stderr 尾部回传前端（审计 §6 P2）。
- **方案：**
  1. 失败按 Campaign 分级。明确拒绝的（活动已结束、资格不符）记为 `failed_known`，并排除出本轮，其余 Campaign 继续；只有全部失败或结果未知时，阶段才失败。
  2. 平台原生码和脱敏原因落账，页面显示稳定码。Route 区分业务错误和崩溃，不回传 stderr。
  3. 加入结果未知时，接入 §5 的统一框架。
- **验收：** 故障注入“一个 Campaign 被拒、一个成功”时，阶段结果为 `completed_with_gaps` 且写明缺口；未知不被计为成功。

### 4.2 Campaign 内建 TapLink（完善度：高）

- **现状：** 意图台账完整（准备、提交、回执、未知、作废）；写后按 30 秒和 120 秒各回读一次。UK 09-24 一轮建了 682 个。
- **短板：**
  - 准备逻辑集中在 `catalog_prepare.py`（727 行，一行多语句）。
  - 步骤顺序分市场：IT 是 seed/read → names → create，其他市场是 seed → localized names → read → create。
  - TapLink 清理只在 IT 执行，BR/MY/UK 整段跳过（`market_taplink_cleanup_not_enabled`）。
- **方案：**
  1. 统一步骤顺序为 seed → names → read → create。短名来源（IT 缓存或本地化生成）由 `market-content.json` 的 locale 决定，去掉市场分支。
  2. `taplink_clean` 对所有市场启用。先做只读扫描和本地停用（期限、佣金门槛），经用户确认后再对非 IT 市场开放平台删除。
  3. 按步骤把 `catalog_prepare.py` 拆成 seed、names、read、create 四个函数模块，意图台账不变。
- **验收：**
  - 同一批输入在新旧顺序下，生成的意图和绑定集合一致。
  - BR/MY/UK 清理只读扫描的输出与 IT 口径一致。
  - 平台写入数没有额外增加。

### 4.3 全托商品：货源采集与选品（完善度：中）

- **现状：** 采集有普通增量和按类目两种模式。选品先记意图再逐个回读；09-25 补上了跨活动选品自动隔离（`isolated_unverified`）。
- **短板：**
  - 这是最脆弱的模块：IT 从 09-21 断到 09-24，靠临时写的恢复工具（`workflow_recovery.py`）才续上。
  - 轮次和货源的对应关系是推算出来的（`selected_source_run_id` 由市场、北京日期和 run id 摘要算出），没有持久记录。
  - 采集模式分市场硬编码：IT 固定普通周更，UK 首次/月度按类目。
- **方案：**
  1. 在 catalog 阶段开始时，把本轮选定的来源轮次写入 stage 的持久字段（例如 `workflow_stage_run.input_source_run_id`，增量迁移）。下游和恢复只读这个字段，推算函数只用于兼容旧轮次。
  2. 采集模式（普通、按类目、月度节奏）进市场配置，代码只按配置执行。
  3. 把“部分完成可续跑”作为采集的正常路径：分区断点、repair page 和 `accepted_partial` 都写进 stage 结果，由通用续跑入口处理（§4.10 第 3 条），不再为每种故障写一次性工具。
- **验收：**
  - 模拟采集在第 N 页中断后，第二天的轮次能从持久断点续跑，无需人工工具。
  - 旧轮次仍能被推算函数识别。

### 4.4 ② OECID 获取（完善度：中低）

- **现状：** 两套完全不同的实现：
  - IT：`creator_discovery.py` / `identity_queue.py` / `second-cycle-identities.py`，精确 Find 加身份队列。
  - BR/MY/UK：`market_identity.py` 由 scheduler 调用，每 3 个 handle 拉起一次 `probe-italy-profile.py`（542 行），依赖旧系统的签名 runtime。
- **短板：**
  - IT 有 1,406 条线索未解析（lead-pool `unresolved`，09-25 10:45）。已判定的 `unresolved` 会被复用，目前看不到重试节奏，需核实。
  - B 类作者从不送去解析：`cycle_identity.freeze` 只取 A 类 selection，IT 有 134 条 `videoUnresolved`。
  - 探针初始化存在竞争（09-25 已加一次重试）。
  - 证据目录 `var/market-identity-{m}-*` 不清理。
  - 页面的身份队列、精确发现和画像刷新只支持 IT。
- **方案：**
  1. **统一实现。** 以证据更完整的一套为核心（建议 IT 的 discovery/outbox 台账，Codex 审查时确认），抽出“handle → OECID”的市场无关服务：
     - 输入：市场、handle 列表、来源（A/B）。
     - 输出：resolved、unresolved、blocked，附证据。
     - 账号、QPS、profile 类型集合从配置读取。
     - BR/MY/UK 切到这套服务后，删除 `market_identity.py` 与 `probe-italy-profile.py` 的重复部分。
  2. **未解析重试节奏。** 把 `unresolved` 分成两类：“找过、确实没有”和“没找成（blocked、超时）”。后者按原断点续跑。前者 N 天（建议 7 天，写入配置）后允许重试一次，最多 M 次；重试只追加证据，不覆盖原判定。
  3. **B 类作者送解析。** 冻结时同时取 A 类 selection 和 `video_lead_current` 的作者 handle，去重后进同一队列；解析后 B 类位置按 PROJECT 排序进入可发层。
  4. **摆脱旧系统签名依赖。** 把签名 runtime 纳入 `scripts/vendor-runtime`（固定 manifest hash，`--check` 可验），运行时不再读旧目录。迁移前后同一批 handle 的签名结果逐字节对比。
  5. 证据目录纳入 `state-retention.py` 的保留规则。
  6. 身份队列、精确发现和画像刷新的页面与 API 改为按市场参数工作。
- **验收：**
  - 四个市场走同一入口，scheduler 中 OECID 阶段没有市场分支。
  - IT 未解析数、B 类 `videoUnresolved` 在漏斗看板上可见并下降。
  - 同一 handle 重复提交不重复调用平台。
  - 旧目录不可读时，OECID 仍能运行。

### 4.5 Kalodata 线索（完善度：中）

- **现状：** A 类（销量）四个市场都有；B 类（视频）只有 IT。额度按市场分开计算。09-25 修复了缺作者和翻页重复导致的扫描崩溃。
- **短板：**
  - 新一代 B 类初始化时立即清空 `video_lead_current` 与 `kalodata_video_head`，扫描完成前 B 类位置消失（审计 §6 P1，至今未修）。
  - `kalodata-video-crawl.py` 没有 `--market` 参数，`video_lead_current` 也没有 market 列。
  - B 类没有独立的重读节奏。
- **方案：**
  1. **修清空缺陷：** 新一代写入独立的 generation；完整结束后在一个事务里切换 head，未完成时继续使用上一代。
  2. **B 类全市场化：** 按增量迁移给 `video_lead_current` 与 head 表加 market 列（旧行回填 `it`）；CLI 加 `--market`；是否对某市场启用 B 类由市场配置决定。
  3. **重读节奏：** B 类按 PROJECT 规定的节奏重读（若 PROJECT 未定，由用户决定后写入配置），与 A 类的 `refreshDays` 分开。
  4. 额度耗尽只发布已完整完成的 PID（现有合同保持）。
- **验收：**
  - 扫描中途读取可发池时，B 类位置数不变。
  - 迁移在旧库副本上幂等。
  - 开启 BR 的 B 类后，IT 数据不受影响。

### 4.6 收信（完善度：中高）

- **现状：** 四个市场都在稳定轮询，09-25 加了掉线（16201010）自动刷新。
- **短板：**
  - 两套实现：IT 用 `poll-cycle-inbox.py`（12 个会话/30 秒，经 `job_run` 与 `jobs.json` 的 `inbox_monitor` 拉起）；其他市场用 `poll-market-inbox.py`（20 个会话/10 秒，由 scheduler 拉起）。
  - 状态文件的 `pendingContent` 包含已处理的行，不能当待回复数用。
- **方案：**
  1. 合并为一个 poller，市场差异（账号、节拍、预算）放进 `cycle-inbox.json` 的分市场配置；统一由 scheduler 拉起，退役 IT 的 `job_run` 入口。
  2. 状态文件只输出与会话页一致的未回数（`conversation_workbench.UNREAD_PENDING` 口径），删除或改名 `pendingContent`。
  3. 按审计 §9 P1 补上完整性指标：各市场的发现与内容水位、gap 数，以及实时延迟的 P50/P95。
- **验收：**
  - 合并后逐市场按 messageId 对账：缺失 0、重复 0、跨市场 0。
  - 停掉旧 poller、启动新 poller 的切换步骤写进交接，并由用户确认时间点。

### 4.7 二发（完善度：中高）

- **现状：** 已跑满平台每天约 1,000 个新联系的额度；结果未知时有隔离规则（`quarantine_absent_card`、`quarantine_refused_creates`）。
- **短板：**
  - 两套实现：IT 用 `continuous_send.py`，其他市场用 `market_send_control.py` 与 `market_send_canary.py`（名字还叫 canary）。
  - 建会话没有回执时，只能人工核验。
  - 适配器把发送许可阶段的本地拒绝统一改写成 `it_delivery_dispatch_not_allowed`，调用方看不到原因码。
  - IT 每次写入前要查旧系统 PostgreSQL（`second_live_runtime.conflicts()`），旧库一停，IT 发送与回复就会失败。
- **方案：**
  1. **合并发送核心：** 控制读写、窗口、容量、候选选择、写前复检、会话预检、回执核验、worker 启动做成一套，市场只保留 transport 适配。模块改名（例如 `market_send.py`），scheduler 的 argv 同步修改。
  2. **保留原因码：** 适配器把许可拒绝的原因码（`delivery_expired`、`marketing_cooldown` 等）原样传出，外层错误码可以不变，但 payload 要带 `reason`。
  3. **建会话无回执：** 接入 §5 框架。自动隔离的证据标准需用户决定。备选：在建会话后 X 小时内，对该账号会话列表完整扫描两次（间隔 ≥5 分钟），列表未满 1,000 条且没有该达人，才判为未建成并隔离。
  4. **旧系统门禁：** 由用户确认旧发送器是否永久退役。退役则移除 `second_live_runtime` 检查；保留则纳入告警条。
- **验收：**
  - 四个市场走同一 worker；原有正反合同测试（容量、unknown、暂停、过期、隔离）全部迁移并通过。
  - 切换期间平台写入无重复（以 requestRef 对账）。

### 4.8 AI 回复（完善度：中）

- **现状：** 回复窗口、首轮试点、调用模型前先查阻断（`reply_blocker`）等机制齐全。
- **短板：**
  - 真实回复很少，四个市场都停在“首轮已完成、等开全量”。
  - 来信积压：09-25 10:44 未回 IT 102、BR 143、MY 41、UK 35，最久约 41 小时。
  - 发送通道两套：IT 用 `reply_transport.py`，其他市场用 `market_agent_reply.py`。
- **方案：**
  1. **业务侧（用户操作）：** 核对四市场首轮回执后，在页面开启全量。
  2. **积压处理：** 按“最早等待优先”处理；超过 N 天（建议 3 天）的来信先由模型判断是否仍需回复，避免对过期话题机械回复。
  3. **合并发送通道：** 与 §4.7 一起做成市场无关的 transport，冻结正文和 requestRef 的合同不变。
  4. **效果度量进漏斗看板：** 按市场统计回复数、转人工率、回复后 72 小时内加橱窗数；按审计 §9 做六类场景的真实多轮抽检。
- **验收：** 全量开启后一周内，积压最久等待时间降到一个回复窗口以内；转人工漏判和虚构承诺逐条复核为 0。

### 4.9 账号管理（完善度：中）

- **现状：** 身份代次、维护意图和定时刷新都由项目自己管理，掉线后能自动刷新（收信和 OECID 路径）。
- **短板：**
  - 登录有效期不固定：BR acc1 约 72 小时掉线，UK acc11 84 小时仍正常。
  - 密码和签名仍依赖旧系统（`01-BDSystem-V2`）。
  - 旧项目的 `io.bdhub.account-identity-lifecycle`（每 300 秒运行）与本项目的 72 小时维护分工不清。
- **方案：**
  1. **维护由健康探测驱动：** 每账号每 N 分钟做一次低成本只读认证，遇到 16201010 等失效码就登记刷新；固定 72 小时只作为上限。所有读写路径（收信、OECID、发送、回复）遇失效码都走同一个 `login_recovery.request_refresh`。
  2. **凭据迁入项目：** 保存到本机 Keychain 或项目内 0600 文件（方案需用户确认），迁移后旧目录只作回退；签名见 §4.4 第 4 条。
  3. **核清旧项目 launchd 任务**的实际动作，确认不会与本项目同时操作同一账号，结论写进 TECHNICAL。
- **验收：**
  - 模拟失效码时，四条路径都只登记同一代次的一个刷新意图。
  - 旧目录不可读时，账号维护仍能完成（凭据迁移完成后）。

### 4.10 编排：scheduler（完善度：中）

- **现状：** 阶段、屏障、租约和检查点都完整。
- **短板：**
  - **每一跳按批次阻塞（09-25 新发现）。** `tick()` 把就绪阶段并行拉起后，要等这一批全部结束（`ThreadPoolExecutor` 块）才进入下一跳。
    - 09-25 10:30 UK OECID 开始后，IT Kalodata 的第 3 次重试 10:48 到期，但一直等不到新的一跳。Kalodata 用的是自己的 `kalodata:global` 槽，本不需要等平台槽。
    - 这期间 `operations-scheduler-status.json` 也不更新。
  - 失败恢复靠六个一次性工具：`resume-short-names`、`resume-kalodata-preflight`、`check/resume-selected-recovery`、`resume-video-author-skip`、`resume-kalodata-auth`、`resume-after-fix`。
  - `operations_scheduler.py` 有 824 行，所有阶段都写在里面，还有 17 处 `market == 'it'` 判断。
  - 当前轮次停在 `needs_human` 时，该市场第二天的新轮次也不会开。
- **方案：**
  1. **去掉批次屏障：** 改为常驻循环，每次循环都领取新就绪的阶段；已在运行的阶段由 future 集合监督，完成一个就结算一个。槽位容量和租约保持不变。每次循环都刷新状态文件。
  2. **拆分 scheduler：** 按到期计算、阶段执行（每个阶段一个模块）、报告解码、进程监督拆开；`operations_workflow.py` 仍是唯一状态机。市场差异改从配置读取，状态键统一用 `{market}:{stage}`（兼容读取旧键）。
  3. **合并续跑工具为一个 `resume`：** 前置条件统一为“原阶段、原错误码在白名单内、零平台写入或写入已全部结清、当前证据满足该错误码的恢复条件、claim 已释放”。每个错误码的恢复条件写成声明式表。旧命令保留为别名，直到页面和文档都换掉。
  4. **`needs_human` 的影响范围：** 下游阶段未开始、而且问题与新一天的输入无关时，由用户决定是否允许第二天的新轮次照常开始。当前行为是整市场停住。
- **验收：**
  - 用假执行器模拟“一个长阶段加一个到期重试”，重试在 30 秒内开始。
  - 六个旧命令的现有测试全部改由 `resume` 通过。
  - scheduler 里没有市场名字面量。

## 5. ③ 统一结果未知框架

现有做法分散在多处：

| 场景 | 当前实现 |
| --- | --- |
| 卡片未知 | `quarantine_absent_card`（两次回查，间隔 ≥300 秒，读到历史却没有这条消息） |
| 建会话被拒（201） | `quarantine_refused_creates` |
| 建会话无回执 | 人工 `quarantine_unknown_conversation` |
| 选品活动不符 | `isolated_unverified`（两次回读，间隔 ≥5 分钟） |
| TapLink 新建回读缺失 | 保留 unknown，不重复 POST |
| 加入 Campaign 未知 | 先核验 |
| AI 回复未知 | `reply_blocker` 阻断 |

**方案：**
1. 抽出一个通用组件（例如 `scripts/lib/unknown_outcome.py`）：
   - 每类意图登记：只读核验函数、证据标准（最少回查次数、最小间隔、什么算“确认不存在”）、隔离动作（转人工、开 case、排除对象）、阻断范围（只挡该对象，还是挡整个市场）。
   - 台账字段：未知开始时间、最后核验时间、核验次数、证据缺口。
2. 所有 worker 遇到 unknown 统一进入 `waiting_reconciliation`，按登记的节奏回查。满足证据标准就隔离并继续，不满足就保持未知，按登记的阻断范围暂停。
3. 告警条按同一台账显示“未知 N 条、最久 X 小时、已隔离 M 条”。

**验收：**
- 现有七种场景迁移后，原测试全部通过。
- 新增的正反合同测试覆盖三种情况：证据不足不隔离；读不到历史不算“不存在”；隔离后不重发。

## 6. 待用户决定

1. 四市场 AI 首轮核对后，是否开启全量（§4.8）。
2. 建会话无回执时，自动隔离的证据标准（§4.7 第 3 条）。
3. IT B 类作者是否送 OECID 解析（§4.4 第 3 条，本方案建议送）；其他市场是否启用 B 类及其重读节奏（§4.5）。
4. 非 IT 市场的 TapLink 清理是否开放平台删除（§4.2）。
5. 旧发送器是否永久退役，以及能否移除旧系统门禁（§4.7 第 4 条）。
6. 账号凭据迁入项目的存放方式（§4.9）。
7. `needs_human` 时是否允许第二天的新轮次照常开始（§4.10 第 4 条）。

## 7. 请 Codex 审查的要点

1. 每条“现状/短板”是否与代码一致。标“需核实”的尤其要查：IT `unresolved` 是否有重试节奏，以及 lead-pool 的 `unresolved` 1,406 里，“找过没有”和“还没找”各占多少。
2. 推进顺序和依赖是否合理，有没有更小、更安全的切分。
3. 合并 IT 与其他市场的实现时，哪些合同有差异（例如 IT 的旧系统门禁、IT 会话索引库 `it-conversations.sqlite`），需要先对齐。
4. 迁移（market 列、stage 持久字段、未知台账）是否都是增量加法、幂等，并能在旧库副本上验证。
5. 哪些改动会改变正在运行的 worker 的合同，需要先核对进程、锁和断点，由用户决定切换时间。
6. 每项验收标准是否可测；是否缺少正反合同。

审查结论请写在本文末尾新增的“审查意见”一节，或另起一份文档并在这里链接。
