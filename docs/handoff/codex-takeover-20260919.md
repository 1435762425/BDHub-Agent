# BDHub-Agent Codex 接管状态

更新时间：2026-09-21（Asia/Shanghai）。本文件是当前开发交接入口；产品规则以 [项目文档](../PROJECT.md) 为准，技术结构以 [技术文档](../TECHNICAL.md) 为准。动态数量是本次只读快照，后续以 `var/` 台账和页面 API 回读为准。

## 0. 2026-09-21 自动运营首页与持续经营

- 根路由默认进入 `/it` 运营首页；主链按货盘 → TapLink → Kalodata → OECID → 发送池 → 持续二发 → Agent 回复展示，同一 workflow run/stage/generation 对账。首页三个开关均为持久 revision；当前自动运营已开启，全托周更新与持续二发关闭。
- migration v11–v13 已在 21 库在线备份 `var/backups/state/20260920T161947Z-before-ops-v11-v13` 验证通过后应用；schema check 当前 ready。备份 322,461,696 字节、凭据 0、`valid=true`。
- 本地合作状态回填 2,293 位达人：`normal=2,256`、`collaborated=36`、`paid=0`、`rejected=1`；paid 没有从消息文本猜测。重复 apply 为 0。
- ACC6/ACC9 固定角色分别为 communications/supply；页面已把固定职责与共同只读探针拆开。项目身份权威现由 `agent_identity_generation` 承担：立即重登直接打开可见浏览器并自动填写旧配置中只读读取的保存凭据，候选 profile/HTTP/IM 只写 `var/account-identities`，联合验证通过后原子发布；旧目录不修改，失败不覆盖上一代。
- 两个账号已分别完成一次真实可见浏览器自动重登并发布项目代次；随后从机构账号页面再次点击 ACC6“立即重登”，API→后台 worker→浏览器→联合验证→发布在约 13 秒内完成。项目身份双账号只读复核 `passed=true`、同机构/同市场/同 sender，Partner info、IM ID、IM token、选中货盘和商品卡均通过，平台写入 0、消息发送 0。
- 自动工作流调度、十个真实作业、完整货盘发布屏障、Kalodata 额度终态、OECID 后统一发池均已接通；标准主链由总开关统一授权，`config/jobs.json` 的逐项 `enabled` 继续关闭且不再形成第二层门禁。Agent durable setting 已明确开启，独立于旧 jobs 子开关。
- TapLink 创建 unknown 改为单 PID 隔离：整批结束后公共回读，再按 30/120 秒两次轮询；未找到记 `skipped_unknown`，不重复 POST。同账号验证码成功后只重放原冻结请求一次，重放仍挑战则暂停作业。清洗对平台明确失效卡建立意图，批后完整回读仍存在记 `failed_known`，不重复 DELETE。
- 用户可见 frozen-v2 已退役；持续发送在领取每位达人前把 OECID、PID、Offer、`currentListId`、模板 revision、最终正文与控制 revision 冻结进 `cycle_delivery`。旧 `cycle_bulk*` 只读保留，旧 freeze/start/stop/reconcile CLI 固定拒绝。
- 会话加入 `normal/collaborated/paid/rejected` 四态、人工优先与 showcase 自动升级；人工事项可直接确认且不要求先发送。三条 Agent 动作 key 不变，正文支持不可变 revision 编辑。
- 页面已重构为运营首页、合作工作台、会话、货盘、达人、运行与设置；结果页支持 7/14/30 天三条趋势线，货盘含命名 Tab，运行设置拆为 Kalodata/作业/机构账号。持续发送页已恢复二发模板的新增、revision 修改、删除、中文翻译与真实例子，当前模板删除门禁读取 `continuous_send_control`。
- 运营控制已按用户反馈收敛：标准主链只需开启首页“自动运营总开关”，作业页不再重复展示逐项启动/立即运行；全托周更新、持续发送、Agent 仍是独立开关。作业时间改为本地编辑后明确“保存时间”，不会再被轮询回写覆盖。
- 当前只读回读：持续发送 `off`、24 小时额度使用 0、unknown 0；Agent `enabled=true`、固定窗口 15:00–16:00；最新发送池为位置 4,731、可发送 1,643、等待 2,794、暂不参与 11、历史已发送 495。
- 账号重登的真实浏览器结果已在本节单独记录；本轮后续又按用户明确授权真实发送 6 条 Agent 固定模板回复，全部 confirmed、unknown 0。自动清洗、Campaign、自动建链已在下方完整实测；持续发送仍未启动。

本轮逻辑提交：`a8fbabd`（状态合同与 migration）、`a1e7d47`（账号身份代次）、`348e7a1`（自动工作流与 TapLink unknown）、`e4086e9`（持续发送）、`328400d`（首页与全页面重构）。

## 0B. 2026-09-21 会话、账号移交与自动运营复查

- 会话队列显示 `showcaseNotifications` 合成消息；移除“处理中”，待回复与已处理统一到“Agent 回复”，待处理项另显示“待回复”。
- 修复集中回复窗口永久跳过旧 pending：持久 pending 不再要求短期 `inbox_until` 尚未过期。用户授权的一次性运行按 6 个 request ID 逐条发送，当时 6 条 `sample_self_service` 均 confirmed；当前待 AI 回复 0。
- `/ops/accounts` 已回到统一 OpsWorkspace，Kalodata / 作业与定时 / 机构账号设置三个 Tab 在跳转后持续可见。
- 旧 BDHub 的 ACC6/ACC9 已在空闲、无租约状态下停用并退出全部旧业务池/身份生命周期；新项目 overlay 独立覆盖 enabled、角色和池，不继承旧停用字段。
- 自动运营修复：Campaign 先 verify unknown、再自动 join eligible、再采集筛分；加入验证码只重放同一冻结请求一次且最多 100 个/批；TapLink 在 create 前强制补齐全部短名；到期账号排队后自动启动维护 worker；Agent 由 durable setting 自恢复；scheduler 的 lastSuccess/lastAttempt 不再每 tick 清空。详见[复查记录](../implementation/automated-operations-audit-20260921.md)。
- 本轮代码验证：Python 994 项、Web 143 项、TypeScript、Next 生产构建和 121 份 Markdown 链接检查通过；旧项目账号相关 80 项测试与 8787 Dashboard/PostgreSQL 健康检查通过。

## 0C. 2026-09-21 自动运营真实全链验收

- 运行前在线备份 `var/backups/state/20260920T182149Z-pre-auto-ops-20260921`：21 库、约 324 MB、凭据 0、`valid=true`。自动运营总开关开启，全托周更新与持续二发关闭。
- 主运行 `workflow-e7b6aed0fba94d912b4acbdbdc65`：TapLink 当前库存 4,086 张，全部 valid、删除候选 0；历史 13 条删除意图保留为 verified。Campaign 已加入总数 86，完整读取 123 请求/7,054 offer，筛出 chosen 500、held 2,509。
- Campaign TapLink 目标 499 个全部 ready：复用当前标准绑定 232 个，真实新建并回读确认 267 个；新增短名 88 个、DeepSeek 18 次、错误 0，创建 unknown 0。A 类 Kalodata 完成 1,261/1,261 PID、1,487 条当前线索、1,515 请求、错误 0；B 类在真实日额度耗尽前完整完成 20 PID、297 条视频线索，并保存第 21 个 PID 的 6 页/112 详情断点。
- 实测发现并修复四个主链缺口：同日清洗投影残留旧卡；嵌套 TapLink 写入和 B 类完成数漏计；OECID 未冻结新 current edge 却误报 completed；身份探针仍受旧项目停用状态控制。OECID current handoff 的慢查询同时从 PID×全部历史 edge 改为索引点查。
- OECID 修复运行 `workflow-979d201b7799b0b39133f43d5394`：本地先复用既有 handle 终态，再对剩余 101 位执行真实 Find，60 resolved、41 unresolved、pending 0、错误 0；发送池修复运行 `workflow-b6f6fb7116382a2c12c40e8d8bf0` 随后重新发布。主运行的不可变旧 generation 仍保留修复前 `TapLink writes=0 / B completed=0` 的错误投影，真实子报告为 267/20；后续调度器已按子报告正确汇总。
- 最终验证：Python 1,003 项在 `PYTHONWARNINGS=default` 下通过；Web 143 项、TypeScript、Next 生产构建通过。整段验收的真实业务写入合计为 13 张失效 TapLink 删除、6 次 Campaign 加入请求和 267 张新标准 TapLink，均已回查明确、unknown 0；未发送二发 IM，持续发送控制仍为 `automaticEnabled=false / runRequested=false`。

本轮补充提交：`184a12a`、`f67983b`、`7139379`、`fceac2d`、`889a565`、`4edd6a9`、`a01cc5d`。

## 0A. 2026-09-20 会话工作台、模板与互斥窗口

- 新增一级 `/it/conversations`，默认打开“需人工”，二级页为人工回复模板和 Agent AI 回复设置；旧 `/it/workspace/inbox` 重定向到新入口，合作工作台只保留发送与结果历史。
- 当前真实只读队列为 33 条：需人工 5、处理中 6、Agent 已处理 1、已完成 21。需人工只计算当前仍有 pending/开放案件的会话，不把无 pending 的历史审核样本混入；详情展示原因、达人原文、等待时间、完整时间线、相关 PID/listId 和草稿。审核结论为人工但尚无实体 case 的 4 条会话会投影为可完成的人工事项，用户记录结果后才原子落账并解除本次冻结。
- 人工操作已接文本、人工模板、商品卡、草稿 revision、中→意翻译和事项完成；图片入口明确禁用，因为当前 Pure HTTP 适配器没有图片写端点。人工文字/卡片共用持久 `service_reply`、同账号写门禁、request ref 和精确回查，失败后复用同一意图，不生成盲目重发。
- 二发模板已经成为发送页独立卡片：五个内置模板无需保存批次即可看全文，并支持三参数自定义模板的创建、修订和归档；正在使用的模板不能归档。二发、人工回复和 Agent 固定模板三个域完全分离。
- Agent 执行链已接 `no_reply` 本地结案、三条固定模板、`human` 人工案件、持久回复意图和原意图回查；人工 `turn_review` 优先于模型。默认北京时间回复 `15:00–16:00`、缓冲 30 分钟、二发 `16:30–24:00`，保存时双向拒绝重叠；等待二发窗口不会挡住回复窗口。
- migration v10 已在备份 `var/backups/state/20260920T114352Z-before-conversation-workbench` 后应用，21 库校验通过。当前 `agent_reply_setting` 尚无持久行，页面读取默认设置；`service_reply_config.enabled=0`，Agent worker 未运行，真实回复与二发发送均为 0。
- 货盘达人线索页已去掉与当前政策冲突的可编辑条件，集中展示 A 类 14 天/20 位/7 天刷新、B 类 30 天/1,000 播放/7 天完整重读和“只按真实平台额度停”的固定规则；`batchSize=5000` 仅作技术上限。
- 会话页现按视口全宽铺满三栏；达人原文可逐条翻译成中文。右栏移除自由“处理结果”，改为有已确认人工发送证据后点击“确认人工回复”，并提供达人级拒绝合作标签及 GMV/粉丝/销量/均播/回复/橱窗数据。
- 二发系统模板与自定义模板均可编辑或删除，并提供中文翻译按钮；正在使用的 `video_focus` 仍受删除门禁保护。达人库顶部改为已查询、查得到、搜索不到、有回复、已加橱窗五个达人维度指标。

## 1. 接管结论

DeepSeek/Agent 已经把 9 月 14 日的“货盘批量备链”继续推进到一条较完整的意大利二发准备链：Campaign/全托货盘、筛分与选入、TapLink 复用/创建、线索队列、达人级 OECID、发送池、收信监控、发送前预检和页面分区均已有代码与测试。

当前发送桥已经接通，但仍不是自动经营态：

- `/api/send` 已支持预览指纹、不可变冻结、明确 start/stop 和批次状态；只有用户在页面点击“确认并开始”才会启动真实 worker。本轮没有点击，真实发送仍暂停。
- 事件级回复账本、五动作审核、会话工作台和受控 Agent 执行链已上线；Agent 开关仍关闭，当前没有创建新的真实回复发送意图。
- 工作台“统计日历”已接通现有 `/api/inbox`：直接展示最近 14 个北京自然日的确认触达、确认卡片、回复、加橱窗和未确认，不增加轮询或后台任务。
- 2026-09-20 01:xx 未发现批次准备、二发、冻结发送或收信 worker；代码与断点保留，但本轮没有擅自恢复。

2026-09-20 意大利 V1 工作台已完成代码侧收敛，尚未在本节把构建成功误写成新的真实发送验收：

- 生产页面为 `/it/workspace/{send,history}`、`/it/conversations{,/templates,/agent}`、`/it/catalog`、`/it/creators` 与 `/ops/*`；根页进入意大利发送工作台。
- `/flow-demo`、浏览器演示页、local runtime、matching/outreach-drafts、second-pilot、second-live trial、旧二发历史和早期 batch task Web 入口已退出生产构建；历史 SQLite 未删除并继续备份。
- `lead-pool.v3` 把真实 A/B 来源证据发布到 Web；B-only 位置现在能以代表视频 source 进入冻结复检，不再因只有 A 类 `source_edge` 才能冻结而静默丢失。
- `/api/send` 增加一个明确的 verify-only unknown 核验动作：只读原 delivery，遇到后续未提交的 ready 组件立即停止；核验完成后仍需用户再次明确 start 才继续批次。
- 最新生产构建已通过 `io.bdhub.agent.web` 重启到 `127.0.0.1:5198`；新工作台、旧路由 404/308 和保留 API 已回读。本轮未冻结、未启动、未发送、未恢复自动回复或业务 worker，也未修改旧 BDHub。

## 2. Git 基线

| 项目 | 当前值 |
| --- | --- |
| 仓库 | `/Users/bjn00003/BDHub/BDHub-Agent` |
| 当前开发分支 | `codex/v1-runtime-alignment` |
| 第一轮实现提交 | `3c0edad`（迁移底座）、`b024fec`（标准材料与当前线索）、`17cca7a`（历史索引）、`05ff3e1`（发送池性能）、`cb5c7f7`（跨代身份复用） |
| 冻结发送桥 | `e0b37be`（不可变批次、start/stop、frozen-v2 worker 与前端确认） |
| 回复事件与审核 | `dc12e80`（episode/turn、五动作、固定模板、DeepSeek/Jev adapter 与审核页） |
| Jev 与意大利标准链接 | `67b0c98`（TypeSafe Jev、当前货盘重算、标准链接全量补齐） |
| 双模型回复评测 | `4de59f9`（35 条同集批量分类、一致率与审核后准确率指标） |
| Turn 级人工真值 | `e1b5b8c`（provider 解耦标准动作、v5 append-only 审核账本） |
| 审核结果受控应用 | `6984414`（revision 门禁、案件/候选映射、零平台写入） |
| 统计日历 | `fc91e35`（14 日聚合、今日高亮、未确认单列与恒等式保护） |
| 旧回复路径退役 | `6eae5e2`（`reply_facts` 退出调度、工作台口径对齐） |
| 日历日明细 | `e669ad4`（只读白名单投影、分页核对与明细弹窗） |
| 历史任务收敛 | `8b317e3`（旧任务只读、缺失选择明细保持未知） |
| 任意 N＋候补 | `54df9b9`（send-preview v3、冻结 formal/reserve、unknown 不补位） |
| 显式回复真值 | `b76d407`（模型不预选、政策模板直出、append-only 修订） |
| TapLink 周期调度 | `71035e8`（Campaign 日检、全托周检、只读 creates=0） |
| SQLite 测试资源 | `ad1f373`（夹具显式 close，未关闭连接 warning 归零） |
| 旧批量发送器退役 | `4145da0`（旧 CLI 固定拒绝、frozen-v2 授权与候选强门禁） |
| 状态备份与恢复 | `3fbe6ff`（21 库在线备份、完整性清单、空目录恢复） |
| 独立 Python 环境 | `c36240a`（全部 Web/worker 入口改用项目 `.venv`）、`1f599db`（完整版本锁） |
| Vendor 协议运行时 | `4d4db3a`（强制 vendor 源码、旧配置只读引用、禁止代码混用） |
| Web ESM 声明 | `a04c588`（消除 Node 测试的模块类型告警） |
| 回复审核深链接 | `8335e4a`（`tab=reply` 直达审核队列，未知页签回退） |
| 回复审核长原因修复 | `81c94fb`（Python/Web 统一500字符，保留已审核22条） |
| Kalodata 身份探针恢复 | `b23be8d`（迁移路径回退、列表计数、T−2窗口） |
| 意大利生产工作台收敛 | `db8e138`（market-scoped 路由、A/B 冻结、unknown verify-only、旧演示/试点退役） |
| 多市场参考标准 | `75e7817`（意大利基准、14 市场逐能力验收和当前文档对齐） |
| 会话工作台与互斥窗口 | `532980e`（三套模板、默认需人工、Agent 固定回复、15:00/16:30 窗口与安全恢复） |
| 会话与达人界面优化 | `e9fc133`（全宽会话、中译、人工确认/拒绝、经营画像、可编辑系统模板和达人维度统计） |
| 自动运营状态合同 | `a8fbabd`（workflow/identity/collaboration/continuous 三组 additive migration 与门禁） |
| 账号身份代次 | `a1e7d47`（72 小时全局维护队列、固定角色、原子代次与旧权威只读阻断） |
| 自动工作流与 TapLink unknown | `348e7a1`（generation 屏障、十作业调度、批后回读与清洗终态） |
| 持续发送 | `e4086e9`（逐条 delivery 快照、窗口/额度/unknown 恢复，旧冻结入口退役） |
| 自动运营首页与页面重构 | `328400d`（首页、趋势、四态合作、命名 Tab 与三运行设置入口） |
| 上一个已提交开发头 | `4cdb759`（`agent/p0-catalog-links`） |
| 继承工作区固化提交 | `cd81dff` |
| 继承标签 | `takeover-20260919-inherited` |
| 远端 | 未配置；当前只有本机 Git，不具备异机备份或协作推送 |

`cd81dff` 保存了接手时 41 个已跟踪修改和 125 个新增源文件/文档；同时将 `outputs/`、Kalodata 激活码和 Campaign 联系邮箱排除出 Git，并提供无敏感值的样例配置。后续文档清理与功能开发必须另做逻辑提交，不能改写这一继承基线。

## 3. 本机运行快照

### 服务与进程

- Next.js 工作台已用最新构建重启，监听 `127.0.0.1:5198`，cwd 为 `apps/web`，LaunchAgent 为 `io.bdhub.agent.web`。
- 2026-09-20 01:xx 进程回读未发现 `batch-preparation-worker.py`、`second-pilot-worker.ts`、`send-batch-worker.py` 或 `poll-cycle-inbox.py --worker`；本轮没有擅自恢复它们。
- 旧 BDHub `01-BDSystem-V2` 仍是独立生产系统，本项目不修改它。

### 2026-09-20 本地数据回读

| 领域 | 快照 |
| --- | --- |
| 身份 | 稳定身份库累计 3,069 个 handle；当前 20 条范围内 963 个 handle：728 已解析、235 明确未解析、0 blocked、0 未判定；`reconciled=true` |
| 当前线索 | 1,150 个 PID 的本机回执共 1,803 条正销量证据，按 v2 发布 1,617 条当前线索；另为身份复用建立 12,996 条历史索引，50 条旧记录因字段不完整未索引 |
| 发送池 v3 | 当前 A 类 3,281、B 类已完整发布 0；ready 1,222、累计已发送历史 495 |
| 发送预检 | 默认请求 500，当前可冻结 500；链接缺失/条款变化均为 0，仅达人关系冻结 5、超出本批规模 206 |
| 冻结批次 | migration v3 已应用；`cycle_bulk_freeze=0`、`cycle_bulk_candidate=0`，说明本轮没有代用户冻结或启动批次 |
| 收信 | 555 个索引会话、1,354 个事件、25 个待取内容；累计 live 回复 26、加橱窗 36；当前 open case 1 |
| 回复事件 | 495 个外发 episode、35 个入站 turn、26 个有关联；人工真值 35/35 完成。DeepSeek 80.00%（误自动 2），Jev 62.86%（误自动 3） |
| 凭据 | Kalodata 本机激活码已保存为 0600，刷新后真实探测 `ready/rows=50`；TypeSafe key 已保存为 0600，官方 models 接口可用，均不入 Git |
| 自动回复 | 关闭；DeepSeek/Jev 的影子分类结果都不授权恢复 |

最新意大利工作台只读回读：`lead-pool.v3` 当前 A 类位置 3,281、B 类已发布位置 0、可发送 1,222、等待 1,821、暂不参与 5、历史 sent 495；send preview 为正式 500＋候补 50，冻结批次仍为 0。B=0 是数据 generation 尚未完整发布，不是代码把 B 隐藏：A 类仍有 1,233 PID，B 类停在首 PID detail 断点；本轮恢复尝试 1 次请求后再次得到真实 `kalodata_daily_quota_exhausted`，完成数未被推进。

这些数字会随 worker 和时间变化，不应写入 UI 常量或业务规则。规范化索引与固定 JOIN 顺序完成后，`lead-pool.py status` 本机约 0.45 秒、`send-batch.py status` 约 1.69 秒；这是本机观测，不是 SLA。

## 4. 已完成到什么程度

| 模块 | 已有实现 | 当前边界 |
| --- | --- | --- |
| 全托货盘 | 主来源采集、可配置筛分、选入账本、已选池回读 | 真实数字随台账变化；全托不使用库存门槛 |
| Campaign | IT Campaign 读取、筛分、加入活动、页面/API 和合同测试 | 仍按非全托规则核对期限、库存与额外条款；不与全托合并资格 |
| TapLink | `catalog_current_binding` 是唯一前向材料；当前 selected 2,297/2,297、campaign 474/474 精确绑定 | 旧卡仍完整保留；本轮 1,548 次创建均有 intent+回读，当前 prepared/submitted/unknown 为 0 |
| TapLink 周期维护 | Campaign 每日来源刷新→资格重算→链接只读核验；全托已选按周只读核验；失败保留上次成功 | 两个周期和材料调度器当前均关闭；creates 固定为 0，不自动建链或删卡 |
| 商品短名 | DeepSeek 批量生成与 PID 级缓存；发送预检可按 PID 复用 | 失败不自动重复调用模型；短名质量不应冒充发送资格 |
| Kalodata 线索 | `leads-queue-v2`、完整 receipt、当前前 20 条发布、历史规范化索引和无平台回填 | 失败不写 `queried_at`；额度耗尽停止并保留断点 |
| OECID | 达人级一次查询、blocked 重开、结果互斥分类、页面卡片 | 已明确查无的不自动重复；无 OECID 不进入发送位置 |
| 发送池 | `lead-pool.v2`、统一 `sourceRank → units DESC → PID`、三种业务结果、已发送历史分离 | 池本身只读重算；不能从可发送数量直接启动发送 |
| 冻结发送 | 任意 N（1–2000）＋自动 10% 候补、`previewHash`、完整候选快照、requestId 幂等、revision start/stop、2-lane worker、页面确认摘要与断点 | 初始执行只含 N 位正式成员；明确未触达才提升冻结候补，unknown 不释放名额；只有页面明确 start 才真实发送 |
| 收信与统计 | 现有 inbox worker 接入作业面板，按北京时间统计并排除历史补录 | 盘点中出现过一次 `im_transport_error`，最终回读已清除；继续观察而不是重启掩盖 |
| 统计日历 | 最近 14 日汇总、每日紧凑日历、今日高亮、未确认单列、点击日期查看分页明细 | 明细严格只读并只投影白名单字段；原始 snapshot、平台 payload 和回执不下发 |
| 早期指定数量任务 | 历史任务卡改为只读，缺失的选择明细显示“历史未记录”而不是 0；当前页面不再唤醒旧 worker | 任意 N＋10% 候补已迁入 frozen-v2，旧任务权限不再参与当前执行 |
| 回复分类与执行 | 不可变 episode/turn/关联、五种动作、三条固定回复、人工真值优先、持久意图和精确回查 | Agent 开关关闭；`human` 永不自动发送，DeepSeek/Jev 仍继续影子评测 |
| 会话工作台 | 默认需人工队列、三栏时间线、草稿、人工模板、文本/卡片、翻译、事项完成和 Agent 设置 | 图片发送未接端点，入口保持禁用；人工发送只在用户点击后执行 |
| 生产导航 | 意大利合作工作台、会话工作台、货盘与材料、达人、运行与设置五个入口 | 旧体验导航与 `/flow-demo` 已退出构建；后续市场按意大利接入标准逐项验收，不提供“全部市场”执行页 |
| 货盘决策密度 | 采集、筛选、链接、线索、身份和发送池的核心数字统一为阶段表格 | 大容器仅保留阶段和动作；明细继续按需展开，不再为每个数字铺独立小卡 |
| 发送话术 | 独立模板卡展示 5 个内置全文，并支持三参数自定义模板与 revision | 模板 ID、revision 与最终正文进入 preview hash 和冻结候选；当前 `video_focus` 保持选中，真实发送仍为 0 |

## 5. 当前必须保持的业务门禁

- 真实发送和 AI 自动回复保持暂停；代码完成、预检通过、旧批次授权或页面有 ready 数量都不能自动解除。
- 第一次新发送执行器的真实发送必须由用户在页面明确点击开始。用户此前选择的越界探测档是 600，但该选择不等于本次接管自动执行 600 条。
- 卡片和文字都精确回查成功才算完整触达；单卡、单达人限额、平台拒绝和结果未知分别记账。
- `flight < 0` 是单达人触达限制，只结束该达人；账号级日额度信号尚未取得，不能编造或用本地 500 闸门替代。
- 发送超时或不明回执必须停批核验；不重新生成意图或换账号盲发。
- ACC6 固定负责收信、IM 发送、Agent 回复、OECID 和达人画像；ACC9 固定负责货盘、Campaign、商品选入和 TapLink；已验的只读能力不自动推广到未验的批量写动作。
- 旧 BDHub 只读；不迁移凭据、不写旧数据库、不恢复旧任务。

## 6. 当前技术风险

1. **收信健康需持续观察**：盘点中出现过一轮 `im_transport_error`，最终只读回读已恢复为 `null`。后续若再次出现，读取脱敏日志和最近成功时间，区分短暂网络错误、身份问题或锁竞争，不用盲目重启掩盖。
2. **早期任务库只读保留**：历史 `batch-preparation-worker` 曾持有 `batch-tasks.sqlite` 写事务 23.9–74.3 秒；当前 UI 已停止创建/控制旧任务并只读追溯。任意 N＋10% 候补已迁入 frozen-v2，不应重新唤醒旧 worker。
3. **新发送执行器尚未做真实平台验收**：冻结、start/stop 和离线故障合同已完成，但账号级日额度原生信号仍未取得；第一次真实执行仍需用户在页面单独启动并观察。
4. **本机 Git 无远端**：已有提交和标签可以本机回滚，但机器损坏时没有远端恢复点。配置 GitHub/GitLab 远端需要用户提供目标仓库或明确创建位置。
5. **文档曾混入大量动态流水**：原 `AGENTS.md` 已由本轮收敛；以后不得继续把每次数字和事故追加回根规则。
6. **测试资源释放告警已解决**：tracemalloc 证明告警来自测试夹具把连接事务上下文误当成 close；20 个夹具文件已显式关闭，`-W default` 全量 1081 项未关闭数据库 warning 为 0，生产 migration 本身没有泄漏。
7. **旧批次执行路径已退役**：`bulk-second-send.py` 固定拒绝；`cycle_burst` 缺少 running freeze 或不可变候选时在认证前拒绝。历史 legacy-only `cycle_bulk` 只读保留，不能拿旧授权恢复发送。
8. **回复执行链已接但仍未做真实平台验收**：DeepSeek 28/35、Jev 22/35；双模型一致仍有 2 条误自动处理，且本批没有 `link_usage` 真值。人工 `turn_review` 已优先，Jev 保持 challenger；Agent 开关必须维持关闭，直到用户明确启用并按小范围案例验收真实回执。
9. **收信监控当前未运行**：代码与断点都保留，但没有常驻 `poll-cycle-inbox.py --worker` 进程；这是运行状态，不授权本轮自动恢复。
10. **异机副本仍未配置**：21 库正式备份、校验和空目录恢复已经可用，但首份基线仍在本机；机器损坏时仍需要外部保存位置。
11. **保存凭据尚未迁移**：解释器、包依赖、协议源码和新身份发布目录已迁入本项目；用户名/密码仍只在登录时从旧配置读取，不复制。没有成功发布项目代次的账号仍继续使用旧只读基线，不能把单个账号重登成功外推为全部市场迁移完成。

## 7. 建议接续顺序

### 已完成：第一轮读侧对齐


- 增量迁移、SQLite backup 和真实库副本回放已完成；
- 标准 TapLink、当前前 20 条线索、历史身份索引、统一排序和三结果投影已上线本机数据；
- 回填与验证均为本机 SQLite，平台写入 0。

### 已完成：冻结发送执行桥

- 当前预览冻结为完整达人×PID×Offer×`currentListId`、话术和顺序；
- 页面提供独立“冻结本批”“确认并开始”“停止本批”，且显示授权摘要、revision、逐项结果和 worker 断点；
- frozen-v2 执行不调用 `choose_candidates` 或 `fresh_card()`；明确拒卡只让对应 PID 等刷新，单达人限制继续下一条，unknown 停止所有 lane；
- 本机 SQLite migration v3 已在备份后应用，备份位于 `var/backups/20260920-p3-send-bridge/second-cycle.sqlite`；平台写入 0。

### 已完成：事件级回复预演

- migration v4 建立 `outbound_episode / inbound_turn / turn_episode_link / service_case_turn / reply_classification / reply_review`，备份位于 `var/backups/20260920-p4-reply-events/second-cycle.sqlite`；
- 本机历史证据回填 495 episode、35 turn、26 个有关联，`platformWrites=0`、回填 `modelCalls=0`；
- 收信 worker 删除 60 秒逐达人模型调用，改为只保存事件、立即冻结达人；最初的两小时建议已由本轮 `15:00–16:00` 独立集中窗口取代；
- TypeSafe Jev 继续只做五动作影子分类；DeepSeek 已接 Agent 分类入口，但人工 `turn_review` 优先且 Agent 开关仍关闭。Jev 固定官方 `jev-1.13.0`，固定模板与模型判断分离；
- 回复预演页并列展示两模型完整判断、固定显示中文理解，并让用户直接选择独立的五动作标准答案；migration v5 的 append-only `turn_review` 已应用。
- migration v6 将审核与业务应用拆开：`no_reply` 安全解除已处理冻结、`human` 进入人工案件、模板动作只生成候选不发送；备份位于 `var/backups/20260920-review-application-v6/second-cycle.sqlite`。
- 旧 `reply_facts` 已从供给调度器的 active stage 集合和命令路由移除；本机历史行仍保留且只读可追溯，不再被 claim、recover 或页面状态投影。工作台同时移除“60 秒合并、事实工具自动答、发送前远程重验”等过期口径。
- 回复审核不再用两个模型的一致动作作为默认答案；每条都要求用户显式选择。固定模板直接来自版本化政策，模型没选中也能预览；已审核结果可追加新 revision 修正，旧真值和既有业务应用保持不可变。
- 用户审核到第 22 条后，下一条的 234 字符 `humanReason` 触发 Web 旧 200 字符上限，使 API 返回 503；`81c94fb` 将 Python 落库与 Web 解码统一为 500 字符并补齐上下界测试，22 条既有真值完整保留，自动回复和平台写入仍为 0。

### 已完成：统计日历聚合页

- `StatsCalendarPanel` 复用工作台现有 `useInboxMonitor()`，没有第二套 API、请求或轮询；
- 最近 14 个北京自然日展示确认触达达人次、确认商品卡/文字、实时回复、加橱窗和未确认，今天单独高亮；
- 未确认卡片不计成功，回复与橱窗排除历史补录；当前未结人工事项保持“现在的状态”，不伪拆到某一天；
- API decoder 与纯展示模型都校验 totals 和 today 恒等式；读取不可用、日期为空或不一致时不把它画成业务 0；
- 每个日期可打开有界明细，核对达人当前/发送时 handle、PID、商品、佣金、发送话术、达人原回复、加橱窗和人工案件；每页 50、最多 100，继续加载沿用同一天和下一偏移；
- 最新生产构建已重启到 `127.0.0.1:5198`，浏览器真实回读为 14 日确认卡 495、回复 26、加橱窗 36、未确认 0，收信 worker 仍未启动。

### 已完成：早期任务台账收敛

- `/api/batch-tasks` 的 503 根因是历史库缺少后来新增的 `batch_source_selection`；读侧现在返回“历史未记录”，不建空表、不伪造 0；
- 工作台移除旧任务创建、暂停、优先级和 resume 操作，只保留目标、候补、历史准备进度、来源页面和事件的只读追溯；
- 当前新发送的唯一页面入口仍是“发送池与发送”的预览→冻结→明确开始，历史任务 `executionConnected=false`，不会继承旧授权或唤醒准备 worker；
- 项目文档的任意 N＋10% 候补已迁到 frozen-v2，不再恢复旧并行任务源。

### 已完成：frozen-v2 任意 N＋候补

- 页面接受 `1–2000` 的明确正式目标，保留 500/1000 快捷值和 600 越界探测快捷值；
- send-preview v3 自动计算 `reserve=ceil(N×10%)`，正式与候补未全部备齐时服务端拒绝冻结，原目标不缩小；
- 冻结候选在不可变 JSON 中保存 `batchRole=formal|reserve`，只有 formal 写入初始 `cycle_bulk_item`，`cycle_bulk.target` 始终为 N；
- 明确 `recipient_limit/material_stale/needs_review` 等非触达终态可按冻结顺序提升候补；存在 unknown delivery 时提升为 0，整批先进入核验；
- 真实只读预检：N=500 → 500＋50、N=137 → 137＋14、600 越界 → 600＋60，均完整备齐；本轮没有冻结或启动真实批次。

### 已完成：旧批量发送器退役

- `bulk-second-send.py` 不再创建/恢复批次或动态选人，固定返回 `legacy_bulk_sender_retired`；
- `cycle_burst.run_cohort()` 必须读到 state=`running`、授权一致的 freeze 和每位成员的不可变 candidate，否则在账号认证前返回 `frozen_batch_required/frozen_batch_incomplete`；
- 动态 `choose_candidates()`、远程 `fresh_card()` 和无快照 fallback 已从运行路径移除；`cycle-send-cohort.py` 因共用该门禁也不能绕过；
- 历史表与旧行未删除，本轮没有认证、平台请求、冻结或发送。

### 已完成：SQLite 状态备份与空目录恢复

- `config/state-backup.json` 显式登记 21 个当前数据库；出现未登记数据库或缺库时拒绝创建，历史 `*-before-*.sqlite` 快照不重复纳入；
- `state-backup.py create` 使用 SQLite online backup API 捕获已提交 WAL，逐库写 SHA-256、大小、`quick_check`、schema 元数据和 Git 提交；不复制 sidecar、凭据、运行日志或 outputs；
- `verify` 重新校验清单、文件集合、哈希和 SQLite 完整性；`restore` 需要 `--confirmed`，只写不存在或空的目标目录，绝不覆盖当前 `var/`，并生成恢复回执；
- 首份真实基线位于 `var/backups/state/20260919T225438Z-v1-baseline`：提交 `3fbe6ff`、工作树干净、21 库、289,329,152 字节，独立回读全部通过，目录 0700、文件 0600、凭据 0；
- 合成 WAL、漏登数据库、篡改、非空目标和路径逃逸均有回归测试。本轮没有恢复当前库或启动任何 worker。

### 已完成：项目独立 Python 运行环境

- 新增 `requirements.txt` 和 Python 3.13/macOS arm64 的完整 `requirements.lock`，依赖安装在本仓库 Git 忽略的 `.venv`，未修改旧 BDHub 环境；
- 25 个 Web bridge/worker 入口和 7 个 Python 子进程入口全部改为项目 `.venv/bin/python`，活跃代码中不再存在旧仓库解释器路径；
- 项目 `.venv` 下 1081 项 Python 测试、vendored runtime check、migration check、发送池/发送预检/回复审核只读 CLI 均通过；26 个固定包与 lock 完全一致；
- Web 383 项、TypeScript 和 Next 生产构建通过；真实 API 回读为发送池 ready 711、回复 turn 35/已审核 22、自动回复关闭、真实发送 0；
- 本轮只迁移解释器与依赖，不复制旧凭据或账号文件，不启动业务 worker，也不解锁真实发送。

### 已完成：协议源码切换到本仓库 Vendor

- `legacy_runtime.py` 统一移除旧源码 import root，强制加载 `vendor/bdhub`；如果同一进程已加载非 vendor 的 `bdhub`，立即以 `nonvendored_bdhub_loaded` 拒绝；
- 货盘、TapLink、画像、IM、账号状态和 Kalodata 路径共 20 个运行入口已接入适配器，活跃 Python 不再把 `01-BDSystem-V2` 加入 `sys.path`；
- 旧 `config.yaml`、账号 headers、profile、锁和历史库继续只读使用，未复制或改写；运行核对识别 10 个账号引用且身份文件全部存在；
- `vendor-legacy-bdhub.py --check` 证明当前协议闭包 162 模块、40,262 行，missing=0、extra=0；真实账号状态仍为 `executionEnabled=false / realSends=0`；
- Python 全量 1081 项通过；新增测试固定 vendor 来源并阻止旧源码目录重新进入 `sys.path`。

### 已完成：来源化 TapLink 周期调度

- `jobs-v2` 新增 `campaign_material_refresh`（每日）和 `selected_taplink_verify`（每周、默认周一），两者默认关闭；
- Campaign 严格串行：先只读全量采集并重新筛分，成功后才启动非全托链接核验；全托周检独立运行；
- 所有周期链接作业强制 `creates=0`，不自动建链、不删卡；底层链接作业继续互斥并使用现有台账/断点；
- 调度失败保留 `lastSuccess`，记录错误并一小时后重试；手动作业占用时不并发抢同一链接账号；
- `/ops` 可分别设置周期并启动/停止本机调度器；未启动时页面明确说明即使保存周期也不会执行；
- 本轮只运行一次全关闭的 `--once`，子作业启动数 0、平台写入 0，正式调度器未启动。

### 已完成：SQLite 测试连接清理

- 用 tracemalloc 追到真实分配栈，确认高频告警来自测试中的 `with sqlite3.connect(...)`（只提交/回滚、不关闭），不是 migration 循环；
- 20 个测试文件改为事务退出后确定性 close，并修复一次重启测试覆盖旧 `CatalogPreparation` 对象；
- 未关闭数据库 `ResourceWarning` 从 265 → 45 → 13 → 0；`-W default` 下全量 1081 项通过。

### 下一步：保持开关关闭并准备小范围验收

1. 35 条人工真值已完成并形成 [最终人工评测](../implementation/reply-model-evaluation-20260920.md)；不应用历史样本，不生成或发送回复；
2. 下一轮真实入站样本重点补齐 `link_usage`、拒绝/停联、佣金异常、多意图和简短肯定词；DeepSeek 主影子、Jev challenger，继续比较误自动处理；
3. 真实自动回复 transport 已接通，但当前 `enabled=false`；只有用户在 Agent 设置页明确启用后，才在 `15:00–16:00` 窗口执行，并先核对固定模板动作的原意图回执；
4. 第一次 frozen-v2 真实发送和账号级日额度探测仍由用户另行在页面明确启动。

### 已完成：零销量视频证据可行性与完整分页

- 新增 `kalodata_video_run/evidence/head` 和 migration v7–v8；迁移前备份位于 `var/backups/state/20260920T042216Z-before-paged-video-evidence`，21 库、289,513,472 字节、`valid=true`；
- 实时确认商品视频列表支持 `create_time DESC` 与 `pageNo` 翻页，列表不含作者，必须逐视频调用 `/video/detail`；作者关联使用 Kalodata creator ID，不依赖可能变化的 handle；
- 已移除早期“播放量前 20 条”探针限制以及固定页数/视频数业务上限；正式读取按发布时间倒序覆盖完整窗口，越过窗口或自然短页才结束；
- 五个 PID 的完整 30 天样本读取 225 条视频、解析 86 条播放量 ≥1,000 的视频，得到 19 个当前零销量候选对，其中 12 个已有 OECID；≥5,000 为 6 对，≥10,000 为 4 对；
- 高活跃 PID 的列表探针可达 16 页/800 条、457 条达标视频，后续全池运行必须补断点与额度调度；本轮没有接入 `lead_pool`、冻结批次或发送消息。详细证据见 [零销量视频证据](../implementation/kalodata-zero-sale-video-evidence-20260920.md)。

### 已完成：A/B 线索发送顺序模拟

- 产品规则已确认 A 类最近 14 天正销量、B 类最近 30 天精确 PID 视频播放量 `>=1,000`；B 类同一达人×PID只取最高单条视频，不求和、不计算播放速度；
- `lead_priority.py` 以纯函数实现 A/B 合并和排序模拟：A 类 `sourceRank → units → PID`，B 类 `最高单条 views → 发布时间 → PID`，同达人只占一个当前发送槽；
- 12 条合成来源形成 9 个位置、6 个发送顺序、3 个等待、2 个过滤；100,000 播放无 OECID和50,000播放未结回复均未进入发送顺序，150,000播放的同达人B类没有挤掉其A类；
- 排序合同随后已接入真实 `lead_pool.py`；本小节的12条仍只是合成验收，不替代下方全量重建数据。详细结果见 [排序模拟](../implementation/lead-priority-simulation-20260920.md)。

### 进行中：当前标准链接范围的 A/B 全量重建

- 当前 active 标准绑定 3,029 条/2,813 PID，规则版本与命名版本均一致；与合格货盘相交后实际范围 2,756 PID；读取端也已显式校验两项版本；
- 备份 `20260920T055554Z-before-full-ab-lead-rebuild` 后，仅清当前查询时钟、页面缓存和 A 类 head；历史 run/selection/source evidence 与495个已发送 pair保留；
- migration v9 和 durable B crawler 已应用；A 类使用数值 GMV，B 类有逐页/逐视频断点和作者缓存，真实池已经支持 A 优先、B 最高单条播放量；
- 首日因真实 Kalodata 日额度停止：A 类完成1,523/2,756 PID，当前4,504条线索、3,213个A类位置、1,186个ready；B 类首个PID保存5页后停在详情断点，尚未发布不完整结果；
- heartbeat `BDHub A/B线索全量重建` 每日00:15自动续跑，正常额度暂停不通知；没有发送、冻结、建链、删卡或开启自动回复。详见 [全量重建](../implementation/full-ab-lead-rebuild-20260920.md)。
- 当前 OECID 已全量判定：2,038个唯一 handle 中1,465已就位、573个明确搜索不到、待补/blocked/unknown均为0；可达位置3,491，发送池ready 1,391、sent 495。先本地复用498个同handle历史终态，再对230个真正未判定达人执行真实Find；连续82位实测160.8秒（约30.6位/分钟），12 QPS/9 lanes、请求82/82完成。身份批次默认50，并跳过无关画像刷新、只对当前精确outbox白名单回填；未发送IM、未开启自动回复。详见 [身份吞吐与全量判定](../implementation/identity-oecid-throughput-20260920.md)。

旧 `batch-tasks.sqlite` 只作迁移证据，不再作为执行入口；后续批次能力继续只在 frozen-v2 上扩展。

## 8. 关键入口

| 目的 | 入口 |
| --- | --- |
| 产品规则 | `docs/PROJECT.md` |
| 技术结构 | `docs/TECHNICAL.md` |
| 达人发送池与 AI 回复当前策略 | `docs/architecture/creator-pool-and-reply-policy-v1.md` |
| 全链路 | `docs/architecture/catalog-page-chain.md` |
| 发送池 | `docs/architecture/lead-sending-pool.md`、`scripts/lib/lead_pool.py` |
| 冻结发送 | `scripts/lib/send_batch.py`、`scripts/send-batch.py`、`scripts/send-batch-worker.py`、`scripts/lib/cycle_burst.py`、`apps/web/src/server/send/bridge.ts` |
| 收信与日历 | `scripts/poll-cycle-inbox.py`、`scripts/lib/cycle_stats.py`、`apps/web/src/server/inbox/bridge.ts` |
| 回复事件与审核 | `scripts/lib/reply_events.py`、`scripts/reply-review.py`、`config/reply-policy.json`、`apps/web/src/server/reply-review/bridge.ts` |
| 会话与模板 | `scripts/lib/conversation_workbench.py`、`scripts/lib/template_library.py`、`scripts/run-agent-replies.py`、`apps/web/src/features/conversations/` |
| 状态备份与恢复 | `config/state-backup.json`、`scripts/lib/state_backup.py`、`scripts/state-backup.py` |
| 作业控制 | `scripts/lib/job_run.py`、`apps/web/src/features/ops/` |
| 身份 | `scripts/lib/identity_queue.py`、`scripts/identity-batch.py` |
| TapLink | `scripts/lib/catalog_prepare.py`、`scripts/lib/catalog_links.py` |
| 本机状态 | `var/*.sqlite`、`var/*status*.json`、`var/*.log`（均不入 Git） |

## 9. 本轮执行边界

本轮已将本项目 `second-cycle.sqlite` additive migration 推进到 v13，并完成合作状态与账号 baseline 代次的本地回填。迁移前备份为 `var/backups/state/20260920T161947Z-before-ops-v11-v13`。本次后续修复真实执行了 ACC6/ACC9 项目自有浏览器重登并发布身份代次；没有发送 TikTok IM、创建/删除真实 TapLink、启动持续发送/收信/Agent/自动运营 worker、开启任何自动开关或修改旧 BDHub。

## 10. 接管验证

2026-09-21 自动运营工作流验证：

- Python：`984` 项当前 `unittest` 通过，`PYTHONWARNINGS=default` 下无未关闭数据库 warning。
- Web：`143` 项 Node 合同测试通过。
- TypeScript：`npm run typecheck` 通过。
- Next.js：`npm run build` 通过；生成运营首页、意大利 market-scoped 页面、`/ops/*` 与新 `/api/operations-home`、`/api/workflow`、持续 `/api/send`。
- 文档：119 个 Markdown 文件的本地链接检查通过；`git diff --check` 通过。

以上均为本机代码与只读合同验证，不是新的平台写入或真实发送验收。
