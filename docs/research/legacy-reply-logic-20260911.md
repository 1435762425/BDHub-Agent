# 旧 BDHub 回复业务核对：新系统应承接什么

> 后续澄清：用户已明确旧一发/二发匹配设计粗糙、未实际使用。本文只记录旧源码能力与语义，不能将旧筛选/排序或剧本直接作为新需求。正式匹配设计见 [新方案](../architecture/matching-and-token-budget.md)。

核对日期：2026-09-11。结论是继续建设关系 Agent，但业务对象应从“收到消息就发两组件”扩展为“一个关系中的具体合作事项”。旧系统提供了人工处理、去重、样品事实和只读建议的可复用契约；旧要号剧本不能作为完整经营 Agent 的业务基线。

## 证据范围

- 只读检查 `/Users/bjn00003/BDHub/01-BDSystem-V2` 的工作树代码、规则文件及测试源码；未连接数据库、模型或真实平台，未操作旧服务，未运行可能连接数据库的旧测试。
- 旧仓库 HEAD 为 `7cdc180ebd73a3040a788440a131c73f0c17ce1a`。`reply/engine.py`、`hub/repo/autoreply.py`、`hub/repo/im_states.py`、`hub/repo/sample_reviews.py`、`imops/conversations.py` 有未提交修改。以下只能证明检查时的源码行为，不能证明线上运行了该版本。末尾保存所引文件 SHA-256；没有复制旧源码全集。
- 历史 IM 只阅读已整理的 `scenario_catalog.md` 与 `execution_contract.md`，未读取原对话 JSON。以下只保留业务抽象，不复制达人标识、联系方式或原对话。
- 本文中的“测试”均指已检查测试源码中的契约，本轮未执行；“历史研究建议”不等于用户已确认的商业政策。

## 8 项关键规则

### 1. 人工回复、人工控制、事项完成和外发回执必须分开

**现有代码事实。** 人工发送仅向 `reply_outbox` 加入 `queued` 行，并不修改人工状态。人工完成事项只允许从 `pending_reply/processing` 变为 `replied`，要求入站水位仍一致、无待发队列，同时记录事件及备注。更新的入站会把非拒绝状态重新置为待回复。来源：[`conversations.py:523`](/Users/bjn00003/BDHub/01-BDSystem-V2/bdhub/imops/conversations.py:523)（523–563）；[`im_states.py:335`](/Users/bjn00003/BDHub/01-BDSystem-V2/bdhub/hub/repo/im_states.py:335)（335–409）；[`im_states.py:190`](/Users/bjn00003/BDHub/01-BDSystem-V2/bdhub/hub/repo/im_states.py:190)（190–227）。

旧自动回复的临发防撞车只看会话最后一条是否我方消息；读不到则停止。它不等于持久的关系所有权。运营显式要求生成一次建议时，旧 `assist` 允许生成草稿，但不交还自动控制。来源：[`reply/engine.py:293`](/Users/bjn00003/BDHub/01-BDSystem-V2/bdhub/reply/engine.py:293)（293–308）；[`assist/worker.py:24`](/Users/bjn00003/BDHub/01-BDSystem-V2/bdhub/assist/worker.py:24)（24–35）。

**测试依据。** `imops/tests/test_conversations.py:258` 的 `test_enqueue_human_outbox_does_not_change_manual_status`；`hub/tests/test_im_state_completion_unit.py:65/92/110` 分别核对完成事件、未发队列、过时入站；`hub/tests/test_im_state_repo.py:200` 核对更新入站重开；`reply/tests/test_engine.py:224` 核对人工已接话即停止。

**新系统落点。** 保留本地 runtime 的关系控制版本和提交前复核；补齐“人工发送/生成辅助草稿/标记事项完成”三个独立动作。人工发一条消息不能把所有商品事项判成功；在人工控制下，系统仍可保存事实、提示新消息和按明确请求生成建议。

### 2. 拒联、拒绝某商品、不转 WhatsApp 和重新主动咨询不是同一种状态

**现有代码事实。** `creator_im_state.rejected` 不会因新入站被自动清除；只更新最新入站时间。旧 `ReplyEngine` 则是仅 MX 的要 WhatsApp 剧本，`got_wa/handoff/cooled` 后不继续自动对话，设有终身 8 条等旧上限。拿到号码先保存事实，再独立决定确认消息能否发送；重复号码不重复确认，拿号与人工“已加群”分开登记。来源：[`im_states.py:194`](/Users/bjn00003/BDHub/01-BDSystem-V2/bdhub/hub/repo/im_states.py:194)（194–227）；[`reply/engine.py:32`](/Users/bjn00003/BDHub/01-BDSystem-V2/bdhub/reply/engine.py:32)（32–95）；[`autoreply.py:69`](/Users/bjn00003/BDHub/01-BDSystem-V2/bdhub/hub/repo/autoreply.py:69)（69–89、178–225）。

**测试依据。** `hub/tests/test_im_state_repo.py:218` 的 `test_rejected_is_not_cleared_by_inbound`；`reply/tests/test_engine.py:482/492/502/511/521` 覆盖频控或模型故障仍记号码、重复号码、dry 仅阻止发送；`reply/tests/test_whatsapp_relationship_projection_unit.py:30` 核对关系事实投影。

**旧实现限制。** 旧剧本把问题统一导向要号，在 `prompts.py:54–62` 中没有“不愿转 WhatsApp 但继续在 IM 服务”的完整业务分支；库里仍有异议冷却兼容字段，不能误认为当前 Planner 正在分类执行该流程。`engine.py:183–222` 注释称低 GMV 不回复，但真实入口及 `test_low_gmv_polite_only`（379）表明是礼貌回复、不要号；无画像按 0 处理也只是旧窄剧本选择，不能迁移为低价值判定。旧引擎还直接把号码写日志（72–76、262–265），新系统应只记证据引用及脱敏审计。

**新系统落点及待决。** 本地 runtime 的 `marketingStopped` 加“新主动服务请求可处理”方向合理；不得把主动咨询当作恢复营销许可。需按关系/渠道/商品/话题记录停止范围、来源及时间。既有用户已授权范围内自主回复的方向保持；具体拒联词义、多轮拒绝恢复条件、要号策略、联系方式更新冲突仍须形成明确业务口径。旧 8 条及 GMV 阈值不能默认为三市场新规则。历史研究对应 `execution_contract.md:78–85`，只是评估种子。

### 3. 已有实物要卡片，是精确商品服务；卡片、链接和样品各有准备条件

**代码与规则事实。** `assist.promotable_offers` 只查询已发布网站目录、当前可推资格与已验证 ShareLink；没有返回原生 IM 卡片，也没有证明所有可推商品仅来自网站。真实 IM 卡片须核对该市场该发送账号下的 PID、`list_id`、来源 Campaign；已有 ShareLink 不能替代可发卡证据。来源：[`business_tools.py:185`](/Users/bjn00003/BDHub/01-BDSystem-V2/bdhub/assist/business_tools.py:185)（185–245）；[`runtime-invariants.md:47`](/Users/bjn00003/BDHub/01-BDSystem-V2/docs/agent-context/runtime-invariants.md:47)。

**测试依据。** `assist/tests/test_business_tools.py:128/135` 核对网站现有链接的资格及新鲜度；`send/tests/test_taplink.py:55` 核对精确列表、PID、来源 Campaign；同文件 `:147` 核对创建回执不等于卡片已验证，`:163` 核对发送账号准备，`:188` 明确二发不要求 selected endpoint 样品字段，`:273` 防止首条错误 PID 或多列表歧义。

**历史需求依据。** `execution_contract.md:78` 的“已有可用实物，不能再次机械要求申请样品”；`scenario_catalog.md:111–127` 的“精确同款”及“具体商品购买入口”，都是需要保留的业务分支。实物损坏/已送人另有场景，不能靠曾经合作过推断现在仍有可用实物。

**新系统不应沿用的假设。** 当前 `RuntimeRelationship.product` 只有一个商品；`request_card` 不接受明确合作事项/PID 绑定，Planner 直接拿关系预置商品；`planCard` 冻结的是模拟 JSON，没有真实列表、账号或 Campaign 绑定。不能从这条模拟成功链推出可发真实商品卡。需要一个关系多个事项，精确商品指代/实物状态、供给准备证据和明确缺口；已有实物无须重新申请样品。网站发布不是所有二发的共同前置条件。

### 4. 样品咨询先查平台事实；查询、申请引导和批准是三个动作

**现有代码事实。** 只读工具按当前市场＋固定 OEC＋精确 PID 查询最新成功的活跃生命周期同步，忽略历史范围同步；缺记录不等于从未申请，过时快照或 `result_unknown` 不支持确定答复。来源：[`business_tools.py:141`](/Users/bjn00003/BDHub/01-BDSystem-V2/bdhub/assist/business_tools.py:141)（141–183）。

实际审批门禁仍检查平台待审、生命周期 `to_review`、期限、未知结果、官方/全托路线、同达人同 PID 45 天已批记录；匹配指定网站池的申请要求额度预留，未匹配页面的普通申请不因此锁死。商家建联与页面显示样品库存是展示事实，不是普通审批锁；遗留的 `merchant_confirmation_status` 待确认分支与商家建联字段不同，不能混称。来源：[`sample_reviews.py:3376`](/Users/bjn00003/BDHub/01-BDSystem-V2/bdhub/hub/repo/sample_reviews.py:3376)（3376–3431）；[`sample-review.md:7`](/Users/bjn00003/BDHub/01-BDSystem-V2/docs/operations/sample-review.md:7)（7–13、84–88）。批量批准出现未知即停止余项（`sample_review/runner.py:466–470`）。

**测试依据。** `assist/tests/test_business_tools.py:101/116`；`hub/tests/test_sample_reviews_repo_logic.py:40/59/78/104/130/173` 覆盖普通缺路线、全托、达人范围、未匹配网站页、库存/商家、数据库额度来源；`sample_review/tests/test_runner.py:263` 覆盖未知即停止。

**新系统落点。** 当前 `sample_question` 一律 `needs_facts`（本地 `engine.ts:236–238`）是尚未接入事实源，不是真实业务流程。应创建持久查询事项，由只读工具自动补事实；能够查到有效状态就答复，不能为保证安全而把普通查询全部交人。未来是否在预算/规则内自动批准，用户只确定了总体自主方向，尚未给出样品细项授权；旧系统“运营确认”是待映射的动作级政策，不能因接入新 Agent 自动取消。

### 5. 佣金协商必须比较该达人、该商品、该方案的当前条件

**现有代码事实。** 旧自动回复为避免乱承诺，拒绝模型生成数字、百分比、链接，违规改成要号话术；这只适用于旧要号剧本，无法完成佣金解释。新一些的 `assist` 明确当前有效链接不等于价格/佣金事实已齐全，特殊商业条件转人工。来源：[`reply/engine.py:242`](/Users/bjn00003/BDHub/01-BDSystem-V2/bdhub/reply/engine.py:242)（242–280）；[`reply/prompts.py:54`](/Users/bjn00003/BDHub/01-BDSystem-V2/bdhub/reply/prompts.py:54)（54–62）；[`business_tools.py:239`](/Users/bjn00003/BDHub/01-BDSystem-V2/bdhub/assist/business_tools.py:239)（239–245）；[`planner.py:19`](/Users/bjn00003/BDHub/01-BDSystem-V2/bdhub/assist/planner.py:19)（19–27）。

**测试依据。** `reply/tests/test_engine.py:189/548/555` 验证数字/百分比/拼写数词触发旧降级；`assist/tests/test_planner.py:62/105` 验证不可编造证据和商业写动作。测试证明约束，不证明自动协商已经实现。

**历史需求依据及待决。** `scenario_catalog.md:143–169` 要求比较个人现有条件、Boost 条件、生效范围；无优势不强推换链。有确定事实的佣金解释可以自动答复；提出新的比例、费用、排他或保证必须落在版本化商业权限中。当前本地模拟 `commissionBps` 只是商品单值，不包含达人已有佣金、机构总佣金、Boost、有效期等完整比较事实。不得把“新链接”写成“佣金必然更高”。市场/品类预算、佣金可让渡范围和例外报价策略仍需用户给出口径。

### 6. 去重以原请求和具体执行意图为准，结果未知先核验；正常后续服务仍要能做

**现有代码事实。** 旧引擎每 CID 只保留一个内存待发项，新生成的回复计划替换旧待发；仅收到被跳过的消息未必执行该替换；发前查同文案已发及未解决意图，随后以市场＋CID＋正文哈希登记唯一发送意图，成功才记 AI 发送状态。人工 outbox 只允许明确 `failed` 行重试，unknown 不在可重试范围。来源：[`reply/engine.py:46`](/Users/bjn00003/BDHub/01-BDSystem-V2/bdhub/reply/engine.py:46)（46、139–154、293–355）；[`autoreply.py:48`](/Users/bjn00003/BDHub/01-BDSystem-V2/bdhub/hub/repo/autoreply.py:48)（48–65）；[`conversations.py:710`](/Users/bjn00003/BDHub/01-BDSystem-V2/bdhub/imops/conversations.py:710)（710–750）。

**测试依据。** `reply/tests/test_engine.py:197/234/258` 检查新消息取代、失败不重试、同文案不重发；`imops/tests/test_conversations.py:445/480` 检查保留审计的失败恢复及拒绝非失败/过时行。

**旧限制与新系统落点。** 同文案终身防重对要号有意义，但不能把“上个月这句话已发过”变成拒绝新样品/商品服务的理由；旧内存排期也不能承担跨天承诺。本地持久任务、来源消息幂等、组件回执和 fencing 应保留。当前 `replanLatest` 对任意部分已接收动作，统一等待新明确请求（本地 `engine.ts:300–304`）只是保守原型路径：新设计应判断原任务是否仍有效、哪些组件已经交付、剩余组件为何失效，能够有依据地继续未发部分，而不重发已交付部分。新消息如果改变需求或人工控制，应使相关旧动作失效；不应把无关商品的承诺全部删除。

### 7. 单 Agent 的上下文与工具应围绕固定身份、事项及事实，不能直接移植旧剧本

**现有代码事实。** `assist/planner.py` 已提供固定达人依赖、三个只读工具、结构化输出、实际工具引用校验、模型/工具次数限额；确定性答复要求有效证据，缺失/过时/故障不能当成 0 或没资格。`business_tools` 使用 PostgreSQL 只读事务、字段级时间和限定字段。`aiops` 只是类目初筛，候选仍标为渠道/链接、AI 复核待检查，并不授予发送。来源：[`planner.py:45`](/Users/bjn00003/BDHub/01-BDSystem-V2/bdhub/assist/planner.py:45)（45–83、94–132）；[`business_tools.py:71`](/Users/bjn00003/BDHub/01-BDSystem-V2/bdhub/assist/business_tools.py:71)（71–128）；[`aiops/matching.py:33`](/Users/bjn00003/BDHub/01-BDSystem-V2/bdhub/aiops/matching.py:33)（33–58）。

**测试依据。** `assist/tests/test_planner.py:27/35/62/76/88/105`；`assist/tests/test_business_tools.py:62/78/85/91`；`aiops/test_matching.py:16/23/32/38`。

**新系统落点。** 借鉴契约和证据约束，不因研究文档旧建议而锁定 Pydantic AI/旧部署。当前 runtime 的“最近 6 条，每条 450 字＋单商品”只是上下文限额试验（本地 `engine.ts:398–410`），不能代替事项索引、未兑现承诺、长期偏好与事实检索。未来输入保留源消息、对象、来源、观察时间、有效期、权限版本；按事项按需加载商品、样品、佣金 Skill。正常事实恢复自动继续，商务决定再交人；旧 Planner 将多种异常统一 handoff 的做法需要拆成可等待/可调查/需商业决定。MX 已支持的旧只读工具不能因三语言文案存在就视作 BR/IT 也已接入。

### 8. 合作成果需证据关联到具体事项；“已回”“已发”“关闭”都不是合作结果

**现有代码事实。** 旧人工 `replied` 是内部事项处理状态；`aiops` 事项以 `plan_id＋OEC＋PID` 幂等建立，更新校验 revision；`closed` 仅清除下次检查时间并写历史，没有校验采用、发帖或收益。来源：[`im_states.py:335`](/Users/bjn00003/BDHub/01-BDSystem-V2/bdhub/hub/repo/im_states.py:335)；[`aiops/repo.py:100`](/Users/bjn00003/BDHub/01-BDSystem-V2/bdhub/aiops/repo.py:100)（100–119、176–189）。

**测试依据。** `aiops/test_repo_postgres.py:94/109` 核对精确候选、并发幂等、状态 revision、关闭取消检查；`hub/tests/test_im_state_completion_unit.py:65` 核对人工完成事件。没有从这些测试得到收入归因已实现的结论。

**新系统不应沿用的假设。** 本地 runtime 只有紧邻的上一条实时事件为 `request_card`，且两个组件均 accepted，才接纳 `adopted`（`engine.ts:247–253`）。真实达人可能隔几条消息才确认，也可能确认另一商品，或已经通过其他合规入口采用；不能用消息相邻关系和组件数量替代关联证据。应分别记录服务交付、方案采用、样品履约、内容交付、归属收入/成本等结果，证据指向具体事项/商品/方案。请求单品卡并不要求每次都再发介绍文本；两组件是原型流程，不是合作采用的业务必要条件。历史研究 `execution_contract.md:89–91` 明确收益不由消息数、建链数或 GMV 替代。

## 对下一步实现的直接建议

先扩展独立本地 runtime 的事项与证据契约，再接三个可离线验证的服务分支：明确商品/实物后提供已有可用卡片、按申请事实答复样品进度、停止营销后仅处理新的明确主动服务。保留既有持久任务/唯一意图/未知核验，不增加真实平台写入。测试必须加入多个商品同一达人、隔消息确认采用、事实查询恢复、部分交付续办、拒绝 WhatsApp 但仍在 IM 咨询。

商业额度、拒联细分政策与自动审批边界属于仍需整理成用户口径的内容；本轮研究不替用户决定。旧 `sample_review` 的动作门禁、`reply` 的窄剧本政策、新 `assist` 的受限建议、历史需求研究是四类不同证据，不能选一套代码整体覆盖其他三类。

## 引用快照

下表为本轮读取的关键旧文件与新 runtime 文件哈希。行号对应这些快照；后续修改应按新的文件版本再核对。测试源码只读核对，未执行。

采集时间：2026-09-11T21:26:58+08:00

| 项目 | 文件 | SHA-256 |
| --- | --- | --- |
| 旧工作树 | `bdhub/reply/engine.py` | `b4696083a49a252e5d80805040e4853193061b24fcb3968ce36dda90ec874e92` |
| 旧工作树 | `bdhub/reply/prompts.py` | `3c4deb6b0261479313b9f6ac1ae0e6e05ff3faf183ef49c57bcc7a5cdaa7087d` |
| 旧工作树 | `bdhub/hub/repo/autoreply.py` | `f6379ae2d6893837e80f3ed44d00265349e0bbdb3fd10313e71e301668a04f0d` |
| 旧工作树 | `bdhub/hub/repo/im_states.py` | `bd5cc958eead653dbc1ef7615d005d48dbc9d4065d43c8b27fb95cbde116f0fc` |
| 旧工作树 | `bdhub/imops/conversations.py` | `ecf648bfe366b8f7112064ff250a8e59462ed6612821a3ce5ee703674a16a527` |
| 旧工作树 | `bdhub/assist/planner.py` | `8087a432ce517c4493643fadde72dd64d11b435c5044d34d9a6b00463c8dc15c` |
| 旧工作树 | `bdhub/assist/business_tools.py` | `100b088c56c4ba831bf07eadfedf304dd438c0e13b22bc205bbadf5a0deff2ff` |
| 旧工作树 | `bdhub/assist/worker.py` | `e3c65b97b5baabab82ee9e4b01a19c79bb700764139b3e475be543b6a0ea1ee9` |
| 旧工作树 | `bdhub/aiops/matching.py` | `5fe58a2fb1f86cbc6cc9e8512fb21249241812d7a1fb35963e6641809f46c004` |
| 旧工作树 | `bdhub/aiops/repo.py` | `73ccf171924d155517cf8c68afae0d6434eba1c117d86cee2507413505c0fe2b` |
| 旧工作树 | `bdhub/hub/repo/sample_reviews.py` | `59d9aaa89d79155531669f22bd8fb9f4f48cf9d6862c4296e33b4e8ac7ecc9ff` |
| 旧工作树 | `bdhub/sample_review/runner.py` | `79f6e21582af1fa814bf0519bdff2da7f501f69f48a7b33f0359f6e920f89dff` |
| 旧工作树 | `docs/agent-context/runtime-invariants.md` | `859dace1185ab836068626db3ac0ccc5b4c6c724c826fc64158a453d429836fe` |
| 旧工作树 | `docs/agent-context/business-rules.md` | `9a77f9392b0a44831d21359d9fe2a933013b1c64a770eaa7f1ee1fc1c534f25b` |
| 旧工作树 | `docs/operations/sample-review.md` | `cad1c1b3fd266a16f6b68b5ed93b68a925b7e4fde2ed754d1140eb4ab906272c` |
| 旧工作树 | `outputs/im-demand-analysis-20260908/tier-study/execution_contract.md` | `dca225573b81aef433b7c142bc71bb3a4a9f15280d67fb45d82ed012a5c6e8ab` |
| 旧工作树 | `outputs/im-demand-analysis-20260908/round2/scenario_catalog.md` | `0705b12bfb53692bd2b38610b65f23dd754aad8e65249d6f980e22310868acd5` |
| 旧工作树 | `bdhub/reply/tests/test_engine.py` | `83e19498a769a5fc2c6ba2b78ea2106e576e1588706d64d958acd3e7578e13bc` |
| 旧工作树 | `bdhub/reply/tests/test_whatsapp_relationship_projection_unit.py` | `f0a9cc689dcc74215091ae7beaa5e35d78b47b01b6b4ab6202bbb60cf68092af` |
| 旧工作树 | `bdhub/imops/tests/test_conversations.py` | `b714e7dde1e21f1d4eb326a692656d142186d223cf7cf325729629c0dbab1011` |
| 旧工作树 | `bdhub/hub/tests/test_im_state_completion_unit.py` | `d0fd0cda896cca626eddbeb40b937350d96d81d4ef39b59282fcbe8ad5a8cf49` |
| 旧工作树 | `bdhub/hub/tests/test_im_state_repo.py` | `e6edc3c1e340193b919342d0b9ef0a1db9d30a34027c7ad402af7aac8cc1a851` |
| 旧工作树 | `bdhub/assist/tests/test_planner.py` | `9ebc685498dcd64888b9740b9a2d9647c605d4f5acf82747cb57ccba87df20e5` |
| 旧工作树 | `bdhub/assist/tests/test_business_tools.py` | `ac2f4df657ae242c23fb0e5852dc5fbeb6bca021bebd3f276f97f2926a8c7c9d` |
| 旧工作树 | `bdhub/send/tests/test_taplink.py` | `f25ff86699089af8e21c3f900957829d8e0c0f0ce86477ac6230cc379f947db4` |
| 旧工作树 | `bdhub/hub/tests/test_sample_reviews_repo_logic.py` | `e7a77263d2746d263fa237d83346576797edeb13c2ab9c3048552eb3683ec192` |
| 旧工作树 | `bdhub/sample_review/tests/test_runner.py` | `5a132b1f604c89b1b3be1ba1c3c4ec07df51ccfd6381a287ec991cb21239d577` |
| 旧工作树 | `bdhub/aiops/test_matching.py` | `19c733f571f11d27ececbcaa5f263531beb357501d84df479cb4bdf6b57662f3` |
| 旧工作树 | `bdhub/aiops/test_repo_postgres.py` | `84b2f61b969a8ef60c75643822e87ee6d4131018b07b321e9e0d8e1973c49372` |
| 新原型 | `apps/web/src/server/runtime/engine.ts` | `049079775d926782c8fc736d0bd2e1c40d781e30cef64bfb8d516c1710c51b65` |
| 新原型 | `apps/web/src/server/runtime/skills.ts` | `1c1c771cdecd9dfbe8bf1bd8ad642d818659c922427db4b2bf688d8626300ea1` |
| 新原型 | `apps/web/src/features/runtime/contracts.ts` | `272d819f882dc68cf32bf8c4ca7c85bc456c00a634920e441140194af494c89c` |
