# BDHub-Agent 开发交接：DeepSeek Harness

> 历史快照：本文件记录 2026-09-14 DeepSeek 接手时的边界，已被 [2026-09-19 Codex 接管状态](codex-takeover-20260919.md) 取代。业务规则仍以当前 PRD/DECISIONS 为准，运行数字与下一步不得从本文件直接恢复。

交接日期：2026-09-14（北京时间）。接收者负责继续现有项目开发，不重新生成一个项目。本文件是当前交接入口；业务细节按链接读取，不需要把整个历史对话、旧仓库或全部接口响应塞入模型上下文。

## 1. 工作区与当前版本

- 唯一可修改项目：`/Users/bjn00003/BDHub/BDHub-Agent`。
- 分支：`codex/research-foundation`。交接前业务代码提交：`8c010a0`；之前为`5c38b0c`（ACC9读取迁移）、`881a21d`（48/72小时维护策略）。交接文档本身会在之后单独提交。
- 旧BDHub：`/Users/bjn00003/BDHub/01-BDSystem-V2`，仅只读参考代码、协议和已授权数据；不要修改其代码、配置、数据库、凭据或服务。
- 前端：`apps/web`，Next.js 16 / React 19 / TailAdmin Next.js Free（MIT）；本机`http://127.0.0.1:5198`，最新页面已部署。沿现有设计系统开发，不引入Pro资源。
- Python兼容环境：旧仓库`.venv/bin/python`，调用新项目脚本时设置`PYTHONDONTWRITEBYTECODE=1`，不要安装包或写回旧环境。
- 状态与原始证据在新项目`var/`，不是Git可完整恢复的资产。没有这些本地文件时只能做离线开发，不应生成假数据冒充当前业务。
- 交接检查时工作树只有未跟踪`outputs/`（先前用户交付物）。保留，不清理、不顺手提交。
- 5198监听进程在交接检查时为11754；PID会变化，任何部署前重查端口与进程cwd。旧8787服务不属于本轮部署范围。

## 2. 产品目标与当前优先级

用户是产品经理。V1先做意大利二发自循环，再按同一逻辑扩展市场。二发是：找到曾经推广同一PID的达人，提供更高达人佣金的商品卡，邀请再次拍视频或LIVE。线索主要来自Kalodata的PID—达人记录，不是全池泛匹配。一发是另一个逻辑，放V2。

最终闭环：货盘发现/刷新 → 商品合格 → 入已选池或已加入Campaign → 复用/创建有效TapLink → 选PID查Kalodata → 正销量线索 → OEC身份/关系核验 → 按指定人数完整备料 → 分时段发送与回查 → 回复/橱窗/GMV反馈。单个阶段实测成功不代表整个闭环已上线。

用户指定明确人数，不用“发满”替代目标。一次准备目标N及约10%候补；已存在的正式任务后续扩容为2000正式＋100候补，保留这一具体任务配置，不改成通用10%。足量准备与发送窗口是独立条件；没有发送时段可以先备料。不要擅自新建另一个1000/2000任务。

### 当前暂停与外部动作边界

- **发送保持暂停，AI自动回复保持关闭。** 最新数据库检查`service_reply_config.enabled=0`。开发、重启、定时器、资格恢复或账号切换均不得解除暂停。
- 历史真实发送授权、小样建链成功不是恢复发送或启动任意新批量动作的授权。交接本身不新增对外操作范围。
- 已确认的开发、只读核验和必要本地测试继续做，不逐个函数要求用户批准。新批量真实建链须结合既有业务授权、明确商品范围和产品执行门禁判断；有必要确认时先做出具体计划和验收结果，再问最重要的一个问题。
- 不做平台物理清链、样品审批、赔偿、其他市场占号或旧服务维护移交来“顺便完成闭环”。这些不是本次默认范围。

## 3. 已确认、不得退回旧版的规则

| 领域 | 最新要求 |
| --- | --- |
| 全托来源 | 只用“高机会商品 → 仅全球销售商品”，是主力货盘 |
| 全托初筛 | 累计销量≥300；有评分须≥4.0；暂无评分允许；总佣金－公开佣金≥2个百分点；不要求库存数 |
| 字段不确定性 | 列表销量按累计，不声称为近30天；评分数值0的具体语义仍未证实，不直接当无评分或真实差评 |
| 非全托 | 来自Campaign，独立页面和来源资格；不得直接套全托300件门槛；期限>45天及普通商品库存>100等旧条件应按来源合同核对，全托库存例外不扩散 |
| 新建分佣 | 全托/Campaign统一：机构至少1、优先2个百分点，达人至少公开+1；机构=min(2,总－公开－1)，达人=总－机构；精确运算 |
| 旧链复用 | 有效旧链保留；用于二发须达人>当前公开、机构≥1及当前商品资格合格；先最高达人佣金，同佣金优先已使用；不为套新公式重建 |
| 资格变化 | 评分跌破4或佣金差不足2暂停新搜索/二发，保留历史；恢复资格也不解除人工全局暂停 |
| PID与活动 | 同PID可有多方案；搜索按PID去重，佣金、活动、期限和卡片必须来自同一个真实方案 |
| Kalodata | 只用正销量PID—达人；零销量过滤。未查PID优先，累计销量降序；已查线索每7天刷新；额度耗尽等待，不假设今天已恢复 |
| 二发文案 | 商品短名按商品/方案缓存；固定可编辑模板批量复用，不逐达人调用模型；强调佣金优势和再次视频/LIVE，不强调“BJN新链接”；不凭空声称爆款/销售表现 |
| 身份 | OECID稳定主键，handle可输入但只是带时间别名；改名不能丢人，避免重名串配；机构关系与市场分开 |
| 新触达额度 | 每机构×市场500名，不按登录账号翻倍；精确重置时间未证实 |
| 单达人限制 | 我方最多5条，商品卡算1；回复/加入橱窗后解锁；用户确认额度约一个月刷新，但真实刷新锚点待查，不能按自然月硬编码 |
| 二发计数 | 卡＋文字均精确回查成功才计完整1条；单卡、拒绝、未知单列；结果未知不盲重发 |
| 橱窗归因 | 已验IM通知没有显式PID，不猜最近商品卡；本机构TAP可补证但有延迟；样品记录不能证明采用本次链接或历史样品来自本机构 |
| 回复 | 文本最后一条后等60秒合并；截图/图片先人工；回复案例须用户审核后上线；机构收益不透露给达人，Boost暂不答；不自动批准/补寄样品 |
| 账号维护 | 无感身份更新48小时，登录维护72小时；无5分钟巡检、无6小时深检、无提前2小时维护 |

更细的回复参考答案和冷却规则按`docs/architecture/second-cycle-confirmed-policy.md`读取；不要根据历史脱离原问题的“可以/14天”重新猜政策。

## 4. 实现与实测状态

| 模块 | 当前证据 / 状态 | 仍缺什么 |
| --- | --- | --- |
| 全托发现 | IT首轮10000 PID已采集，尾页问题已处理；新599个合格未选商品已全部选入并回查 | 日常独立准备队列、覆盖统计与完整页面流程 |
| 双账号 | ACC6通信、ACC9货盘；两号同机构/市场且共享同一个IM sender；双号只读并行已验 | 统一执行器、备用能力验收与完整接管 |
| 货盘读取迁移 | 新读取任务走ACC9；历史固定账号断点保持原账号 | 选入等写动作不能随读取一起迁移 |
| 新分佣建链 | ACC9单品真实创建及ACC6同卡回查通过，只有1次创建；重复execute为0写入 | 批量调度、Campaign新路径实测、完整平台旧链自动复用 |
| 货盘建链台账 | `catalog-links.sqlite`及`CatalogLinks`；持久意图、未知核验和跨创建器防重复保护 | 从单品canary抽出通用货盘材料服务，消费者接入 |
| 二发准备 | 指定数字批次、Kalodata/身份/材料代码和本地状态已存在 | 仍有等名单齐再准备材料的旧顺序，需迁移 |
| 身份补全 | IT/ACC6 12 QPS、9通道、500人测试已验收：500/500，354.73秒，P95 6.5918秒 | ACC9/其他市场需独立验收；12 QPS不是消息或建链速率 |
| 自动回复 | 历史实现与案例资料存在，当前关闭 | 意大利案例审核、工具事实边界及上线验收；不要恢复草稿审批堆叠流程 |
| 前端 | 货盘、达人、合作工作台、账号页均有现有实现；账号页显示真实单品结果 | 全托/非全托、事前准备/发送、模板与达人库仍需按最新需求整理 |
| 维护周期 | 新配置/策略/页面是48/72；新维护Worker未启用 | 实际仍是旧服务维护，不能声称新周期已经接管 |

10000、599、2291个合格等是带日期快照，不要作为动态UI常量。本地缺卡记录不等于平台无卡；10 PID查不到也不代表其余589无卡。

本轮前端（2026-09-15，按用户四条要求）：TapLink准备删明细、加**准备进展**（驱动器每步发布`var/job-links-progress.json`，`job_run.state()`读回挂在`run.progress`，桥接层严格校验，`start()`先清空上一次的，进度条按阶段换分子）；达人线索查询队列与发送池删明细表；查询队列改成发送池式卡片（四层合计＝队列总数）。详见[三次精简](../implementation/catalog-ui-reorg-20260914.md)与[建链进度](../implementation/catalog-actions-and-incident-20260915.md)。

同一轮补上发送前的 **OECID 闸门**：新增独立卡片「达人身份（OECID）」+ 手动按钮，位于查询队列与发送池之间；三类状态（已就位/待补/搜索不到）必须划分全部线索，搜索不到的**保留记录但不进池**。第一次真实运行发现 `leads-run.py` 自己加锁导致每批 0.02 秒即以 `browser_lock_busy` 结束（flock 同进程第二个 fd 也会被拒），已改为**一把锁一个主人**（provider 持锁）。详见[达人身份（OECID）阶段](../implementation/creator-identity-oecid-stage-20260915.md)。

补 OECID 第一次真跑**第二轮停在 `internal_error`**，已查明并修好：账号通道配置所在的 `var/batch-tasks.sqlite` 是**回滚日志库**，被常驻准备作业持有 **23.9–74.3 秒**的写事务（实测），只读连接用的 5 秒默认 busy 超时不够 → 裸 `OperationalError` 被压成兜底码。现在 `production_policy` **等**这个库（120 秒预算，页面那条路 6 秒）、等不到才报具名码 `identity_policy_unreadable`；驱动器对「还没碰到平台」的占用**重试 3 次且不计入轮次**（页面显示 `retry`）；兜底崩溃把 traceback 写进 `var/identity-worker-crash.log`（0600）、接口只给**异常类型**，页面在停止原因下显示它。真实端到端验证过：人为独占 25 秒后仍正常跑完一轮（`claimed 20 / found 14 / notFound 6 / errors []`），现场 3 次占用分别等 42.1s / 24.9s / 42.1s 全部成功。**未动的隐患**：`batch-preparation-worker` 的几十秒写事务仍会挡住页面读 `batch-tasks`，改 WAL 可根治但要动活作业。

### 不要重复创建的真实小样

- PID `1729480061238089885`，短名`quaderni di calligrafia`。
- 实际来源Campaign `7685262119046498070`；selected路线的wire Campaign为`0`，两者不可混用。
- 总14%、公开12%、达人13%、机构1个百分点。
- 意图`catalog-link-6c655517b39f516a97955958ea1d`，列表ID`8650756273145355030`，状态`verified`。
- ACC9创建及回查6.97秒；ACC6前两次账号忙，第三次读成功4.84秒。不要用单品耗时外推批量速度。
- 证据：`var/acc9-link-prepare-20260914.json`、`var/acc9-link-execute-20260914.json`、`var/acc9-link-acc6-readback-20260914-v3.json`、`var/acc9-link-replay-20260914.json`。

## 5. 下一阶段，按此顺序交付

### P0：接通货盘级批量链接准备

先读`catalog_links.py`、`catalog-link-canary.py`、`batch_material_runtime.py`和相关测试，明确哪些纯规则可复用、哪些仅限单品canary。不要仅删除canary限制后启动批量。

1. 对当前合格、已选/已加入活动的PID建立独立可恢复准备任务，保留来源、机构、市场、实际活动、政策版本与账号亲和。
2. 完整查已有链接及成员，区分未检查、查询不完整、可复用、旧链待处理、确认缺链。复用要核对实际绑定与当前商业条件；没有合格旧链不自动等于可以另建。
3. 缺链才形成冻结的新建意图；按已验协议执行、持久回执、成员与卡片核验。所有不明结果留原意图/原账号回查。并发共享账号预算与创建锁，不能每个子任务各自叠加QPS。
4. 暂停/重启可恢复，已核验材料不重复创建。需要重新核验的新事实与历史创建成功分别存储。
5. 向二发提供“当前可用商品方案＋已核验链接”的读取契约，保留精确绑定、资格时间与失效原因。

重点缺口：`scripts/lib/batch_material_runtime.py`中`if report['candidateGap'] or not members:return`仍等待达人名单；新货盘台账已接管的PID会被旧`CardCreation`拒绝创建。必须接通读取新材料的消费者，不能删防重复保护来解除阻塞。该文件还读取旧`link_rules_for`，必须迁到新分佣事实，不能继续用旧固定留2/平均分佣。

P0验收：不足名单也能准备货盘材料；旧有效链接复用且0创建；不完整查询不创建；新链符合公式；多活动不串配；暂停/崩溃/超时/重复领取不重复创建；新材料能被二发读取；本阶段消息发送和自动回复仍为0。先离线和只读对账，再在明确范围内进行必要真实验收。

### P1：页面与供给流可视化

- 货盘一级下“全托商品/非全托商品”；功能区与统计区清楚分开，明细下钻。
- 显示发现、通过、筛除原因、已选已核验、可用链接覆盖PID、待建链、未知/待核验；链接条数与覆盖PID数分开。
- 二发分事前准备与发送执行；显示选了哪些PID、搜索进度、去重/身份/材料缺口；模板单独设置，速度用实际曲线。
- 不编演示进度，不把历史采集时未选标记当现在未选，不把HTTP200当业务成功。

### P2及以后

补ACC9商品选入真实验收与统一任务资源调度；再处理定时货盘03:00、7天线索刷新等运行接通。身份维护移交涉及旧服务/凭据，先做迁移方案，不能在旧项目只读边界下直接操作。然后达人库重构、效果归因、意大利自动回复案例审核；一发V2、其他市场复制排在后面。遇到影响业务政策的问题问产品经理最重要的一题；已有答案不重复确认。

## 6. 代码、数据与文档导航

| 内容 | 入口 |
| --- | --- |
| 总规则/最新事实 | 根`AGENTS.md`；`docs/PRD.md`；`docs/architecture/batch-outreach-v2.md` |
| 当前流程差异/分佣 | `docs/architecture/catalog-first-preparation.md` |
| 账号/维护 | `config/market-accounts.json`；`scripts/lib/market_accounts.py`；`docs/architecture/dual-account-lifecycle.md` |
| 新链规则/台账/小样 | `config/catalog-link-policy.json`；`scripts/lib/catalog_links.py`；`scripts/catalog-link-canary.py` |
| 账号HTTP与写入隔离 | `scripts/lib/global_source_transport.py` |
| 旧材料消费者/创建器 | `scripts/lib/batch_material_runtime.py`、`batch_materials.py`、`cycle_card_creation.py`、`cycle_catalog.py`；`scripts/create-cycle-card.py` |
| 批次供给/运行 | `scripts/lib/batch_sources.py`、`batch_source_runtime.py`、`batch_task_service.py`；`scripts/batch-preparation-worker.py` |
| 全托 | `scripts/lib/global_source.py`、`global_selection.py`、`global_selection_fast.py`；`scripts/global-source-control.py`、`select-global-products.py` |
| 页面 | `apps/web/src/features/catalog/CatalogWorkspace.tsx`；`second-outreach/`；`creator-identities/`；`accounts/MarketAccountsPage.tsx` |
| API字段与用途 | `docs/contracts/tiktok/README.md` → business-catalog / endpoint-index / field-dictionary / agent-tools；按问题读取 |
| 旧版借鉴 | `docs/contracts/legacy-interface-inventory.md`，再按路径只读旧代码；不用旧规则覆盖新确认 |
| 本地业务数据库 | `var/global-source.sqlite`、`global-selection.sqlite`、`catalog-links.sqlite`、`batch-tasks.sqlite`、`second-cycle.sqlite` |
| 关键实测文档 | `docs/implementation/acc9-catalog-link-canary.md`、`catalog-read-acc9-migration.md`、`global-selection-sales300.md`、`identity-12qps-500-release.md` |

正在延续的批次ID：`batch-19e1044b23d017f1f553a2ecac0d`；目标和当前状态以`batch-tasks.sqlite`实际记录为准。旧`second-cycle.sqlite`中plan的active不等于消息Worker获准恢复，不据此取消用户暂停。

## 7. 接手验证与开发方式

先在新项目执行`git status --short`、`git log -3 --oneline`，读取本入口和AGENTS。核对本地DB、证据文件、5198服务。只读SQLite使用`mode=ro`；不要在初始化类会写表时把它当只读检查。缺失事实先报告，不补造。

已有回归入口（按改动范围选用，不需每次全跑）：

```sh
cd /Users/bjn00003/BDHub/BDHub-Agent
PYTHONDONTWRITEBYTECODE=1 /Users/bjn00003/BDHub/01-BDSystem-V2/.venv/bin/python -m unittest discover -s tests -p 'test_catalog_links.py'
PYTHONDONTWRITEBYTECODE=1 /Users/bjn00003/BDHub/01-BDSystem-V2/.venv/bin/python -m unittest discover -s tests -p 'test_cycle_card_creation.py'
PYTHONDONTWRITEBYTECODE=1 /Users/bjn00003/BDHub/01-BDSystem-V2/.venv/bin/python -m unittest discover -s tests -p 'test_catalog_read_routing.py'
PYTHONDONTWRITEBYTECODE=1 /Users/bjn00003/BDHub/01-BDSystem-V2/.venv/bin/python -m unittest discover -s tests -p 'test_batch_materials.py'
PYTHONDONTWRITEBYTECODE=1 /Users/bjn00003/BDHub/01-BDSystem-V2/.venv/bin/python -m unittest discover -s tests -p 'test_global*.py'
cd apps/web
node --experimental-strip-types --test tests/market-accounts-api.test.mjs
npm run build
```

上一轮实际通过：新链4项、旧创建器14项、读取路由3项、全托25项、账号7项、账号API2项及Next.js/TypeScript构建；本机API与账号页已核验。新增批量功能需增加真实故障/恢复契约测试，不能拿原有绿灯代表新实现完成。

服务重启只针对确认cwd的新项目5198，先构建再部署并回读，不能用旧Dashboard发布命令。交接本轮不启动任何Worker或定时任务，也不再次创建上述链接。

Agent组织：一位关系Agent持有一个达人的当前上下文，独立研究按需委派；调度、额度、暂停、幂等、执行结果由程序负责，不做多个固定Agent接力传整段对话。长期关系和原始消息留自控数据库，模型仅接当前必要事实。二发的大批量筛选以规则/SQL为主，AI用于商品短名和后续必要语义；Skills按任务读取，勿从旧项目/全局整包复制。DeepSeek Harness的具体可用工具、权限和模型ID在接手环境核对，本交接不假定它具备Codex浏览器或工具接口。

交付时说明：改了什么、哪些是真实验证、哪些仅离线通过、还缺哪些步骤；保留证据路径和必要交接记录，不写“全系统已完成”来代替局部验收。
