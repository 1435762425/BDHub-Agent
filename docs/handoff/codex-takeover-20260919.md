# BDHub-Agent Codex 接管状态

更新时间：2026-09-20（Asia/Shanghai）。本文件是当前开发交接入口；产品规则以 [项目文档](../PROJECT.md) 为准，技术结构以 [技术文档](../TECHNICAL.md) 为准。动态数量是本次只读快照，后续以 `var/` 台账和页面 API 回读为准。

## 1. 接管结论

DeepSeek/Agent 已经把 9 月 14 日的“货盘批量备链”继续推进到一条较完整的意大利二发准备链：Campaign/全托货盘、筛分与选入、TapLink 复用/创建、线索队列、达人级 OECID、发送池、收信监控、发送前预检和页面分区均已有代码与测试。

当前发送桥已经接通，但仍不是自动经营态：

- `/api/send` 已支持预览指纹、不可变冻结、明确 start/stop 和批次状态；只有用户在页面点击“确认并开始”才会启动真实 worker。本轮没有点击，真实发送仍暂停。
- 事件级回复账本、五动作影子分类和人工审核页已上线；AI 自动回复仍关闭，分类和审核都不会创建 `service_reply`。
- 2026-09-20 01:xx 未发现批次准备、二发、冻结发送或收信 worker；代码与断点保留，但本轮没有擅自恢复。

## 2. Git 基线

| 项目 | 当前值 |
| --- | --- |
| 仓库 | `/Users/bjn00003/BDHub/BDHub-Agent` |
| 当前开发分支 | `codex/v1-runtime-alignment` |
| 第一轮实现提交 | `3c0edad`（迁移底座）、`b024fec`（标准材料与当前线索）、`17cca7a`（历史索引）、`05ff3e1`（发送池性能）、`cb5c7f7`（跨代身份复用） |
| 冻结发送桥 | `e0b37be`（不可变批次、start/stop、frozen-v2 worker 与前端确认） |
| 回复事件与审核 | `dc12e80`（episode/turn、五动作、固定模板、DeepSeek/Jev adapter 与审核页） |
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
| 发送池 v2 | 1,306 个当前达人×商品位置：706 可发送、553 等待中、47 暂不参与；728 个达人；累计已发送历史 495 |
| 发送预检 | 默认请求 500，当前 463 条通过复检；滚动 24 小时本地新联系额度使用 0/500；窗口 09:00–24:00 当时为 closed，但关闭只阻止 dispatch，不阻止冻结 |
| 冻结批次 | migration v3 已应用；`cycle_bulk_freeze=0`、`cycle_bulk_candidate=0`，说明本轮没有代用户冻结或启动批次 |
| 收信 | 555 个索引会话、1,354 个事件、25 个待取内容；累计 live 回复 26、加橱窗 36；当前 open case 1 |
| 回复事件 | migration v4 已应用并只读回填 495 个外发 episode、35 个入站 turn、26 个有关联；最近 5 条已完成 DeepSeek 影子分类、0 条人工审核 |
| 自动回复 | 关闭；Jev 为 `unconfigured`，历史累计数字和影子分类都不授权恢复 |

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
| 冻结发送 | `previewHash`、完整候选快照、requestId 幂等、revision start/stop、2-lane worker、页面确认摘要与断点 | GET/save/freeze 不启动；只有页面明确 start 才真实发送，本轮批次与发送均为 0 |
| 收信与统计 | 现有 inbox worker 接入作业面板，按北京时间统计并排除历史补录 | 盘点中出现过一次 `im_transport_error`，最终回读已清除；继续观察而不是重启掩盖 |
| 回复分类 | 不可变 episode/turn/关联、五种动作、三条固定回复、DeepSeek 影子分类、中文理解和用户正误审核页 | Jev 未配置；旧事实工具只保留历史兼容，不在当前收信路径；AI 自动回复关闭 |
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
3. **新发送执行器尚未做真实平台验收**：冻结、start/stop 和离线故障合同已完成，但账号级日额度原生信号仍未取得；第一次真实执行仍需用户在页面单独启动并观察。
4. **本机 Git 无远端**：已有提交和标签可以本机回滚，但机器损坏时没有远端恢复点。配置 GitHub/GitLab 远端需要用户提供目标仓库或明确创建位置。
5. **文档曾混入大量动态流水**：原 `AGENTS.md` 已由本轮收敛；以后不得继续把每次数字和事故追加回根规则。
6. **测试资源释放告警**：Python 全量测试通过，但未隐藏 warning 时可见多处未关闭 SQLite connection 的 `ResourceWarning`。它不阻断本次文档交付，后续应按模块修复，避免长驻进程积累连接。
7. **旧批次仍有 legacy 兼容路径**：新 `/api/send` 只创建 frozen-v2 批次，不重选 PID、不远程复读 TapLink；历史 legacy-only `cycle_bulk` 仍保留旧路径，只用于追溯/兼容，不能拿旧授权恢复发送。
8. **回复仍处于影子验收**：事件账本和审核页已完成，但最近 5 条中可见“Certo!”被 DeepSeek 判为人工的可疑样本，必须由用户逐条审核形成固定正反例；Jev 尚未配置，真实回复 transport 未接新合同。
9. **收信监控当前未运行**：代码与断点都保留，但没有常驻 `poll-cycle-inbox.py --worker` 进程；这是运行状态，不授权本轮自动恢复。

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
- 收信 worker 删除 60 秒逐达人模型调用，改为只保存事件、立即冻结达人、两小时集中待处理；
- DeepSeek 只做五动作影子分类，固定模板与模型判断分离；Jev adapter 不猜接口，当前明确 `unconfigured`；
- 回复预演页已展示真实意大利原文、中文理解、关联 PID、候选固定模板和“正确/不正确”审核。

### 下一步：用户审核与评测

1. 用户先在“回复预演与训练”审核最近 5 条及后续样本，特别校准简短合作确认、礼貌拒绝和多消息合并；
2. 将用户审核结果形成固定正反例集，补动作准确率、人工漏判、错 PID、重复模板等指标；
3. Jev 获权后跑同一测试集比较；只有用户另行明确开启后，才设计真实自动回复 transport；
4. 账号级日额度探测仍由用户另行在页面明确启动，不与回复评测混在一起。

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
| 作业控制 | `scripts/lib/job_run.py`、`apps/web/src/features/ops/` |
| 身份 | `scripts/lib/identity_queue.py`、`scripts/identity-batch.py` |
| TapLink | `scripts/lib/catalog_prepare.py`、`scripts/lib/catalog_links.py` |
| 本机状态 | `var/*.sqlite`、`var/*status*.json`、`var/*.log`（均不入 Git） |

## 9. 本轮执行边界

本轮应用了本项目 `second-cycle.sqlite` 的 additive migration v3/v4，构建并重启了 5198 Web 服务，并对最近 5 条真实意大利入站内容执行 DeepSeek 影子分类；没有发送 TikTok IM、冻结业务批次、启动发送 worker、开启 AI 自动回复、创建回复发送意图、选入商品、加入 Campaign、创建/删除 TapLink、修改旧 BDHub 或恢复其他 worker。

## 10. 接管验证

2026-09-20 在冻结发送桥和事件级回复预演完成后：

- Python：`1044` 项 `unittest` 通过。
- Web：`374` 项 Node 测试通过。
- TypeScript：`npm run typecheck` 通过。
- Next.js：`npm run build` 通过，14 个静态页面（含 `/flow-demo`）及当前 API 路由生成成功。
- 文档：108 个 Markdown 文件的本地链接检查通过；`git diff --check` 通过。

以上均为本机代码与只读合同验证，不是新的平台写入或真实发送验收。
