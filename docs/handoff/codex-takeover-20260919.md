# BDHub-Agent Codex 接管状态

更新时间：2026-09-20（Asia/Shanghai）。本文件是当前开发交接入口；产品规则以 [项目文档](../PROJECT.md) 为准，技术结构以 [技术文档](../TECHNICAL.md) 为准。动态数量是本次只读快照，后续以 `var/` 台账和页面 API 回读为准。

## 1. 接管结论

DeepSeek/Agent 已经把 9 月 14 日的“货盘批量备链”继续推进到一条较完整的意大利二发准备链：Campaign/全托货盘、筛分与选入、TapLink 复用/创建、线索队列、达人级 OECID、发送池、收信监控、发送前预检和页面分区均已有代码与测试。

当前还不是可直接恢复自动经营的完成态：

- 真实发送仍暂停，`/api/send` 只支持只读预检和保存设置，没有“开始发送”写入口。
- AI 自动回复仍关闭；本次 API 回读 `automaticRepliesEnabled=false`，数据库配置为 `0`。
- 收信 worker 在运行；盘点中曾读到一轮 `im_transport_error`，最终回读已恢复为 `errorCode=null`。这说明进程和恢复路径可用，但仍不能仅凭进程存活宣称长期健康。
- 常驻批次准备、二发 worker 和收信 worker 都在运行；后续修改其合同前必须先做运行影响核对。

## 2. Git 基线

| 项目 | 当前值 |
| --- | --- |
| 仓库 | `/Users/bjn00003/BDHub/BDHub-Agent` |
| 当前开发分支 | `codex/v1-runtime-alignment` |
| 第一轮实现提交 | `3c0edad`（迁移底座）、`b024fec`（标准材料与当前线索）、`17cca7a`（历史索引）、`05ff3e1`（发送池性能）、`cb5c7f7`（跨代身份复用） |
| 上一个已提交开发头 | `4cdb759`（`agent/p0-catalog-links`） |
| 继承工作区固化提交 | `cd81dff` |
| 继承标签 | `takeover-20260919-inherited` |
| 远端 | 未配置；当前只有本机 Git，不具备异机备份或协作推送 |

`cd81dff` 保存了接手时 41 个已跟踪修改和 125 个新增源文件/文档；同时将 `outputs/`、Kalodata 激活码和 Campaign 联系邮箱排除出 Git，并提供无敏感值的样例配置。后续文档清理与功能开发必须另做逻辑提交，不能改写这一继承基线。

## 3. 本机运行快照

### 服务与进程

- Next.js 工作台监听 `127.0.0.1:5198`，cwd 为 `apps/web`；第一轮读侧升级完成后需用最新构建替换实例并回读。
- 2026-09-20 00:xx 进程回读未发现 `batch-preparation-worker.py`、`second-pilot-worker.ts` 或 `poll-cycle-inbox.py --worker`；本轮没有擅自恢复它们。
- 旧 BDHub `01-BDSystem-V2` 仍是独立生产系统，本项目不修改它。

### 2026-09-20 本地数据回读

| 领域 | 快照 |
| --- | --- |
| 身份 | 稳定身份库累计 3,069 个 handle；当前 20 条范围内 963 个 handle：728 已解析、235 明确未解析、0 blocked、0 未判定；`reconciled=true` |
| 当前线索 | 1,150 个 PID 的本机回执共 1,803 条正销量证据，按 v2 发布 1,617 条当前线索；另为身份复用建立 12,996 条历史索引，50 条旧记录因字段不完整未索引 |
| 发送池 v2 | 1,306 个当前达人×商品位置：706 可发送、553 等待中、47 暂不参与；728 个达人；累计已发送历史 495 |
| 发送预检 | 默认请求 500，当前预检可选 500；滚动 24 小时本地新联系额度使用 0/500；窗口 09:00–24:00 当时为 open |
| 收信 | 555 个索引会话、1,354 个事件、25 个待取内容；累计 live 回复 26、加橱窗 36；当前 open case 1 |
| 自动回复 | 关闭；历史累计数字不授权恢复 |

这些数字会随 worker 和时间变化，不应写入 UI 常量或业务规则。规范化索引与固定 JOIN 顺序完成后，`lead-pool.py status` 本机约 0.45 秒、`send-batch.py status` 约 1.69 秒；这是本机观测，不是 SLA。

## 4. 已完成到什么程度

| 模块 | 已有实现 | 当前边界 |
| --- | --- | --- |
| 全托货盘 | 主来源采集、可配置筛分、选入账本、已选池回读 | 真实数字随台账变化；全托不使用库存门槛 |
| Campaign | IT Campaign 读取、筛分、加入活动、页面/API 和合同测试 | 仍按非全托规则核对期限、库存与额外条款；不与全托合并资格 |
| TapLink | `catalog_current_binding` 已成为唯一前向材料；本地回填 1,517 条标准卡，旧卡仍完整保留 | 1 条命名不一致和 1 条 canary 不提升为当前绑定；批量补建未执行 |
| 商品短名 | DeepSeek 批量生成与 PID 级缓存；发送预检可按 PID 复用 | 失败不自动重复调用模型；短名质量不应冒充发送资格 |
| Kalodata 线索 | `leads-queue-v2`、完整 receipt、当前前 20 条发布、历史规范化索引和无平台回填 | 失败不写 `queried_at`；额度耗尽停止并保留断点 |
| OECID | 达人级一次查询、blocked 重开、结果互斥分类、页面卡片 | 已明确查无的不自动重复；无 OECID 不进入发送位置 |
| 发送池 | `lead-pool.v2`、统一 `sourceRank → units DESC → PID`、三种业务结果、已发送历史分离 | 池本身只读重算；不能从可发送数量直接启动发送 |
| 发送预检 | 按池顺序选人/商品、卡片和短名复用、500/600 预览、本地额度与窗口 | 尚无 `create()` 落批次和页面 start/stop；真实发送为 0 |
| 收信与统计 | 现有 inbox worker 接入作业面板，按北京时间统计并排除历史补录 | 盘点中出现过一次 `im_transport_error`，最终回读已清除；继续观察而不是重启掩盖 |
| 回复分类 | IT/MX 历史数据已完成首轮影子分析；五种动作、三条固定回复和事件级上下文已固化在当前策略文档 | 代码仍是旧分类/事实工具/60 秒 debounce；Jev 等权限和同集实测；AI 自动回复关闭 |
| PID→发送池演示 | `/flow-demo` 展示 PID 生命周期树、每 PID 最多 20 条线索、OECID、统一 `sourceRank` 排序和三种业务结果 | 纯前端，PID 数量为 2026-09-19 只读静态快照，达人案例为虚构数据；页面运行时不连接 API、SQLite、平台或模型 |

## 5. 当前必须保持的业务门禁

- 真实发送和 AI 自动回复保持暂停；代码完成、预检通过、旧批次授权或页面有 ready 数量都不能自动解除。
- 第一次新发送执行器的真实发送必须由用户在页面明确点击开始。用户此前选择的越界探测档是 600，但该选择不等于本次接管自动执行 600 条。
- 卡片和文字都精确回查成功才算完整触达；单卡、单达人限额、平台拒绝和结果未知分别记账。
- `flight < 0` 是单达人触达限制，只结束该达人；账号级日额度信号尚未取得，不能编造或用本地 500 闸门替代。
- 发送超时或不明回执必须停批核验；不重新生成意图或换账号盲发。
- ACC9 负责货盘/备链，ACC6 负责选入、身份和通信；已验的单品/只读能力不自动推广到未验的批量写动作。
- 旧 BDHub 只读；不迁移凭据、不写旧数据库、不恢复旧任务。

## 6. 当前技术风险

1. **收信健康需持续观察**：盘点中出现过一轮 `im_transport_error`，最终只读回读已恢复为 `null`。后续若再次出现，读取脱敏日志和最近成功时间，区分短暂网络错误、身份问题或锁竞争，不用盲目重启掩盖。
2. **SQLite 长写事务**：历史实测 `batch-preparation-worker` 会持有 `batch-tasks.sqlite` 写事务 23.9–74.3 秒，导致页面或身份策略读取等待。现有超时/重试只是缓解，WAL 或缩短事务需要在不破坏活作业的前提下单独设计。
3. **发送执行尚未接通**：`send_batch.py` 当前只有 preview/status/save；授权落库、start/stop、断点执行和平台原始信号还未桥接到现有执行器。
4. **本机 Git 无远端**：已有提交和标签可以本机回滚，但机器损坏时没有远端恢复点。配置 GitHub/GitLab 远端需要用户提供目标仓库或明确创建位置。
5. **文档曾混入大量动态流水**：原 `AGENTS.md` 已由本轮收敛；以后不得继续把每次数字和事故追加回根规则。
6. **测试资源释放告警**：Python 全量测试通过，但未隐藏 warning 时可见多处未关闭 SQLite connection 的 `ResourceWarning`。它不阻断本次文档交付，后续应按模块修复，避免长驻进程积累连接。
7. **正式发送仍是旧执行合同**：读侧排序已统一，但 `cycle_bulk` 尚未冻结完整位置，执行器仍会重新挑候选并调用 `fresh_card()`；因此真实发送继续暂停。
8. **回复实现仍是旧合同**：`cycle_service.py` 仍使用 60 秒 debounce，`cycle_agent.py`/`cycle_reply_facts.py` 仍包含事实工具路径，尚无 episode/turn 关联、五动作 provider adapter 和固定模板注册表。
9. **收信监控当前未运行**：代码与断点都保留，但没有常驻 `poll-cycle-inbox.py --worker` 进程；这是运行状态，不授权本轮自动恢复。

## 7. 建议接续顺序

### 已完成：第一轮读侧对齐


- 增量迁移、SQLite backup 和真实库副本回放已完成；
- 标准 TapLink、当前前 20 条线索、历史身份索引、统一排序和三结果投影已上线本机数据；
- 回填与验证均为本机 SQLite，平台写入 0。

### P2：完成发送执行桥

在现有 `send_batch` 预检之上实现：

1. 把当前用户确认的范围冻结为持久批次与授权；
2. 页面提供 start/stop，开始前只显示 2–3 条样例和汇总；
3. 复用既有 `bulk-second-send.py` / `cycle_burst`，不重写平台发送协议；
4. 每条原始平台信号落 `cycle_platform_signal`；
5. 本批到顶、窗口关闭、本地额度、账号级平台额度、单达人限额和 unknown 分开处理；
6. 先完成离线/只读验收，真实 600 探测仍由用户点击启动。

### P3：发送与回复合同迁移

1. 把当前预览冻结成完整达人×PID×Offer×`currentListId` 批次，再接 start/stop；
2. 移除发送前 `fresh_card()`，明确拒卡只让对应 PID 等刷新，unknown 仍停批核验；
3. 建立 `outbound_episode / inbound_turn / turn_episode_link / service_case`；
4. 实现 DeepSeek/Jev 可替换分类器、五种动作、固定模板和用户审核回放；
5. 保持真实自动回复关闭，直到 Jev 获权、同集实测和单独验收完成。

## 8. 关键入口

| 目的 | 入口 |
| --- | --- |
| 产品规则 | `docs/PROJECT.md` |
| 技术结构 | `docs/TECHNICAL.md` |
| 达人发送池与 AI 回复当前策略 | `docs/architecture/creator-pool-and-reply-policy-v1.md` |
| 全链路 | `docs/architecture/catalog-page-chain.md` |
| 发送池 | `docs/architecture/lead-sending-pool.md`、`scripts/lib/lead_pool.py` |
| 发送预检 | `scripts/lib/send_batch.py`、`scripts/send-batch.py`、`apps/web/src/server/send/bridge.ts` |
| 收信与日历 | `scripts/poll-cycle-inbox.py`、`scripts/lib/cycle_stats.py`、`apps/web/src/server/inbox/bridge.ts` |
| 作业控制 | `scripts/lib/job_run.py`、`apps/web/src/features/ops/` |
| 身份 | `scripts/lib/identity_queue.py`、`scripts/identity-batch.py` |
| TapLink | `scripts/lib/catalog_prepare.py`、`scripts/lib/catalog_links.py` |
| 本机状态 | `var/*.sqlite`、`var/*status*.json`、`var/*.log`（均不入 Git） |

## 9. 接管时未执行的动作

本次只做只读盘点、Git 固化和文档整理；没有发送 TikTok IM、开启 AI 自动回复、选入商品、加入 Campaign、创建/删除 TapLink、修改旧 BDHub、停止/重启 worker 或发布新服务。

## 10. 接管验证

2026-09-20 在第一轮读侧升级后完成：

- Python：`1034` 项 `unittest` 通过。
- Web：`369` 项 Node 测试通过。
- TypeScript：`npm run typecheck` 通过。
- Next.js：`npm run build` 通过，14 个静态页面（含 `/flow-demo`）及当前 API 路由生成成功。
- 文档：108 个 Markdown 文件的本地链接检查通过；`git diff --check` 通过。

以上均为本机代码与只读合同验证，不是新的平台写入或真实发送验收。
