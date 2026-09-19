# BDHub-Agent 技术文档

状态：当前技术真相源。最后整理：2026-09-19。

本文回答“系统如何实现、模块如何协作、状态存在哪里、怎样运行和验证”。产品目标与业务规则见 [项目文档](PROJECT.md)，当前 Git/进程/数量和未解风险见 [Codex 接管状态](handoff/codex-takeover-20260919.md)。

## 1. 技术目标

系统必须满足以下工程属性：

- 外部写入有持久意图、幂等键、回执和核验，不因进程中断重复执行；
- 商品、达人、市场、机构、账号和活动绑定可追溯，不拼接互不相干的事实；
- 准备、发送、收信和服务是可恢复的独立阶段；
- 业务规则由确定性代码执行，模型只处理需要语义判断的少量任务；
- 页面展示来自真实台账，可对账且不能用 demo 数字冒充；
- 旧 BDHub 仅作为只读协议/身份运行时参考，新系统拥有独立代码和状态。

## 2. 技术栈

| 层 | 当前技术 |
| --- | --- |
| Web | Next.js 16、React 19、TypeScript 5.9 |
| UI | TailAdmin Next.js Free、自建业务组件 |
| Web API | Next.js App Router 本机 Route Handlers |
| 业务运行 | Python 3.13 CLI、worker、SQLite |
| 模型 | DeepSeek OpenAI-compatible provider，仅用于商品短名和受控语义任务 |
| 测试 | Python `unittest`、Node test runner、TypeScript、Next.js build |
| 服务地址 | `127.0.0.1:5198`，仅本机 |
| 当前 Python | 临时复用旧 BDHub `.venv/bin/python`，不修改旧环境 |

Node.js 要求 `>=22.18.0`。依赖由 `apps/web/package-lock.json` 固定。

## 3. 系统拓扑

```mermaid
flowchart LR
    UI[Next.js 页面] --> API[本机 /api Route]
    API --> Bridge[TypeScript bridge 与严格校验]
    Bridge --> CLI[Python CLI]
    CLI --> Domain[scripts/lib 领域服务]
    Domain --> DB[(var/*.sqlite)]
    Domain --> State[var/*.json / logs]
    Domain --> Lock[账号锁 / lease / fence]
    Domain --> TikTok[TikTok Partner / IM]
    Domain --> Kalo[Kalodata]
    Worker[持久 Worker] --> Domain
    DB --> API
```

固定调用方向：

```text
React feature
  → App Router route
  → server bridge
  → 固定 argv 的 Python CLI
  → scripts/lib 领域规则与 store
  → SQLite / 外部平台
```

React 组件不能直接读写 SQLite、启动任意命令或实现资格规则。Route 只接受白名单字段；bridge 同时校验请求和 CLI 返回结构，不把 stderr、凭据或原始私密数据返回浏览器。

## 4. 仓库结构

| 路径 | 职责 |
| --- | --- |
| `apps/web/src/app/` | 页面路由与本机 API |
| `apps/web/src/features/` | 业务页面、hooks、显示合同和交互 |
| `apps/web/src/server/` | CLI bridge、输入输出校验、错误映射 |
| `scripts/*.py` | 人工 CLI、worker、批次驱动和诊断入口 |
| `scripts/lib/` | 领域对象、规则、队列、台账、调度和平台适配 |
| `tests/` | Python 合同、状态机、故障与恢复测试 |
| `apps/web/tests/` | Route/bridge/UI 数据合同测试 |
| `config/` | 可提交业务参数及敏感配置样例 |
| `var/` | 本机真实 SQLite、进度、日志和证据，不入 Git |
| `vendor/` | 为独立运行引入的旧协议层；真实配置和数据不入 Git |
| `docs/` | 项目、技术、架构、实现证据和交接 |

## 5. 领域模块

### 5.1 货盘与 Campaign

| 模块 | 作用 |
| --- | --- |
| `global_source.py` / `collect-global-opportunity.py` | 全托来源分页采集、快照和断点 |
| `global_screen.py` / `global-screen.py` | 入池门槛、筛分版本和漏斗 |
| `global_selection.py` | 已选池写意图、逐项状态和回查 |
| `campaign_screen.py` | 非全托 Campaign 独立资格与方案选择 |
| `campaign_join.py` | 活动加入意图、联系资料、回执与核验 |
| `catalog_names.py` | PID 级商品短名缓存和模型调用 |

全托与 Campaign 共享后续线索/身份/发送池，但保留 `catalogSource`、活动和链接方案，不能在读取时折叠掉来源。

### 5.2 TapLink

| 模块 | 作用 |
| --- | --- |
| `catalog_links.py` | 单次创建意图与跨创建器防重复 |
| `catalog_prepare.py` | 旧链查询、复用、缺链、读卡和创建状态机 |
| `catalog-link-batch.py` | 可停止/恢复的批量读链与建链驱动 |
| `catalog_clean.py` | 链接清理候选与删除核验 |
| `link_naming.py` | 卡名模板与 PID 级短名读取 |

读链、建链、复用和清理必须使用同一持久账本。`readLimit`、`creates`、`lanes` 和 `qps` 是显式运行参数；`creates=0` 保持只读。未知创建/删除只核验原意图。

### 5.3 线索与身份

| 模块 | 作用 |
| --- | --- |
| `leads_queue.py` | 首次/到期 PID 队列、额度和尝试台账 |
| `leads-run.py` | 复用 Kalodata provider 的实际读取驱动 |
| `creator_discovery.py` / `discovery_cohort.py` | handle Find、cohort、lease、blocked 重试和证据 |
| `profile_refresh.py` | OECID 画像刷新与持久任务 |
| `identity_queue.py` / `identity-batch.py` | 达人级身份分类、批量补齐和进度 |
| `identity_acceptance.py` | 账号级 QPS/lanes 验收与发布策略 |

身份按达人去重，线索按达人×商品保留。明确 `unresolved` 与尚未请求/技术 blocked 分开；只允许技术未提交项有界重试，明确未找到不自动重问。

### 5.4 发送池、执行与收信

| 模块 | 作用 |
| --- | --- |
| `lead_pool.py` | 按当前时间重算位置、达人冷却和状态分层 |
| `send_batch.py` | 发送批次只读预检、容量、窗口和样例 |
| `cycle_review.py` | 候选复检、冻结与跳过原因 |
| `cycle_delivery.py` | 发送意图、组件状态、平台信号和额度预留 |
| `cycle_burst.py` / `cycle_executor.py` | 既有分批执行与外部调用协调 |
| `cycle_inbox.py` / `poll-cycle-inbox.py` | 只读收信水位、事件和待处理内容 |
| `cycle_service.py` | 服务案件、事实、人工接管与处理结果 |
| `cycle_stats.py` | 按北京时间聚合确认发送、回复和橱窗事件 |

当前 `/api/send` 只支持 GET 状态和 POST 保存 `count/widen/window`，没有 start 路由。`scripts/send-batch.py` 只有 `preview/status/save`；真正执行桥尚未完成。

### 5.5 Agent 与语义能力

- `cycle_agent.py`：基于当前关系事实生成结构化判断；不拥有绕过业务代码的写权限。
- `outreach_drafts.py` / `draft_provider.py`：受控草稿队列、模型用量和持久结果。
- `catalog_names.py`：批量商品短名；失败不自动无限重试。
- `cycle_reply_facts.py`：回复所需事实查询结果。

身份、金额、资格、额度、去重、暂停、授权和外部结果必须由代码和台账执行，不能交给模型自由决定。

## 6. 数据与状态

### 6.1 主要 SQLite

| 文件 | 主要事实 |
| --- | --- |
| `var/global-source.sqlite` | 全托来源 run/page/product/detail 和当前 head |
| `var/global-selection.sqlite` | 商品选入 run/item |
| `var/campaign-screen.sqlite` | Campaign 筛分与方案 |
| `var/campaign-join.sqlite` | 加入活动意图和回执 |
| `var/catalog-links.sqlite` | 链接准备 item、reuse、readback、intent 和卡片 |
| `var/second-cycle.sqlite` 的 `cycle_product_name` | PID 短名缓存 |
| `var/kalodata-leads.sqlite` | PID 查询时间、attempt、page 和状态 |
| `var/creator-discovery.sqlite` | handle 发现 batch/item/cohort/request |
| `var/creator-identities.sqlite` | 稳定 OECID、别名与来源 |
| `var/creator-profile-refresh.sqlite` | 画像刷新 job/request/heartbeat |
| `var/batch-tasks.sqlite` | 任务卡、成员、来源、材料和事件 |
| `var/second-cycle.sqlite` | plan、关系、发送、收信、服务和回复事实 |
| `var/it-conversations.sqlite` | IT/ACC6 会话索引 |
| `var/matching*.sqlite` | 独立匹配研究数据集和结果 |

表结构当前由各 `scripts/lib/*.py` 的 schema 初始化维护，还没有统一 migration registry。新增表/字段必须提供幂等升级和旧库兼容测试，不能只靠删除本地 DB 重建。

### 6.2 状态原则

- SQLite 是业务事实；JSON progress 只用于显示正在运行的步骤，不替代最终台账。
- `var/` 不入 Git，也不能由源码完整恢复；备份和迁移需要单独设计。
- 所有 ID、PID、OECID、Campaign ID 和 listId 按字符串处理。
- 外部响应保存必要摘要、哈希和引用；凭据、Cookie、完整私密正文不进入普通日志。
- 动态资格和当前平台事实带观测时间；历史快照不自动覆盖更新事实。

## 7. 幂等、并发与未知结果

### 7.1 外部写入

每次选入、加入活动、建链、删除或发送必须先保存唯一意图，再调用平台，最后保存回执和只读核验。状态至少区分：

```text
pending → started/submitted → confirmed
                         ↘ failed_known
                         ↘ result_unknown
```

`result_unknown` 不具备自动重试资格。恢复流程只能查询原意图和原账号的实际结果。

### 7.2 锁、lease 与 fence

- 账号级平台操作使用排他锁，避免同一身份并发抢占。
- worker claim 使用持久 lease/fence，过期 worker 不能提交迟到结果。
- 页面启动作业前以真实进程表确认存活，配置同时只允许一个同名作业。
- stop 写入停止请求，worker 在安全点退出；不能 kill 后把 in-flight 当未提交。
- 多 lane 共享同一账号 QPS 预算，不能每个子任务独立累加上限。

### 7.3 当前 SQLite 风险

`batch-preparation-worker` 曾持有 `batch-tasks.sqlite` 写事务 23.9–74.3 秒。当前身份路径以有界等待和重试缓解，但不是根治。若引入 WAL 或缩短事务，必须先验证活任务、回滚语义、并发读写和崩溃恢复。

## 8. API 与 Web 合同

所有 `/api/*` 仅接受本机请求，变更类请求验证 Origin/Host、JSON 大小、字段白名单和数值范围。通用原则：

- 多一个字段也拒绝，不将未知字段静默传给 CLI；
- CLI 使用固定 argv 和 cwd，不通过 shell 拼接用户输入；
- child stderr 和异常只映射成稳定错误码，不暴露路径、凭据或原始响应；
- Web decoder 再次检查总数恒等式、枚举、ID 格式和最大行数；
- API 返回 `available=false` 与真实数字 `0` 分开；读取失败不能显示成业务 0。

主要入口：

| API | 当前用途 |
| --- | --- |
| `/api/global-source` | 货盘状态和手动作业 |
| `/api/catalog-screen` | 筛分配置与结果 |
| `/api/campaign-*` | Campaign 读取、筛分、加入和链接 |
| `/api/catalog-jobs` | 货盘作业状态/启动/停止 |
| `/api/leads-queue` | Kalodata 查询队列与运行 |
| `/api/identity-queue` | 达人级 OECID 分类与补齐 |
| `/api/lead-pool` | 发送池分层 |
| `/api/send` | 发送预检和设置保存；当前无 start |
| `/api/inbox` | 收信 worker、今日/历史统计和待人工 |
| `/api/jobs` | 手动作业与定时意向 |

`/flow-demo` 是纯前端业务沙盘：判断函数位于 `apps/web/src/features/demo/`，页面运行时不调用任何 `/api`、SQLite、CLI、平台或模型。PID 生命周期页内的数量是 2026-09-19 只读台账静态快照，达人案例为虚构数据；两者都不作为实时运行证据。页面把刷新分为事实变化、日常巡检、动作前强校验和独立清理四个时钟；建议周期必须标明“待确认”，并与 `schedulerReady=false`、作业开关关闭的当前运行事实分开。24 小时查询门禁读取共享库存证据的本地时间，不逐 PID 调平台；实测耗时展示必须注明样本、并发与非 SLA 边界。

## 9. 账号与外部系统

### TikTok

- ACC9：意大利货盘读取和 TapLink 准备主账号。
- ACC6：意大利商品选入、OECID/Profile、IM 和收信主账号。
- 两账号属于同机构/市场并共享同一 IM sender 语义；不能据此把新联系额度翻倍。
- 当前身份刷新/登录维护仍由旧 BDHub 服务负责；新项目不复制凭据、不启动第二套维护 worker。

### Kalodata

- 激活码仅保存在本机 `config/kalodata-identity.json`（0600），Git 只提交样例。
- 读取复用既有生产身份和 browser lock，不复制 Cookie 到仓库。
- 每日额度、认证和浏览器锁是明确停止条件；查询失败不写成功时间。

### 旧 BDHub

`/Users/bjn00003/BDHub/01-BDSystem-V2` 只读。兼容 Python 环境和 vendored 协议可被新项目调用，但不得修改旧代码、配置、数据库、服务或任务状态。

## 10. 配置管理

| 文件 | 内容 |
| --- | --- |
| `config/catalog-screen.json` | 全托筛分门槛 |
| `config/catalog-link-policy.json` | TapLink 分佣与复用策略 |
| `config/link-naming.json` | 卡名模板与长度 |
| `config/leads-queue.json` | 查询周期、批大小和失败上限 |
| `config/identity-run.json` | OECID 批大小和 cohort |
| `config/link-prepare*.json` | 链接读取/创建运行参数 |
| `config/send-batch.json` | 发送预检数量、窗口和越界档 |
| `config/jobs.json` | 可选定时意向；默认关闭 |
| `config/market-accounts.json` | 市场账号角色和维护目标 |
| `config/*.example.json` | 敏感本机配置样例 |

业务配置不得另建第二来源。敏感配置、邮箱、激活码、Cookie 和身份文件不入 Git。

## 11. 运行方式

### Web

```bash
cd /Users/bjn00003/BDHub/BDHub-Agent/apps/web
npm ci
npm run dev
```

生产模式本机预览：

```bash
npm run build
npm run start
```

两种模式都监听 `127.0.0.1:5198`，不能同时运行。重启前先核对 PID、cwd 和当前 worker 依赖。

### Python

```bash
cd /Users/bjn00003/BDHub/BDHub-Agent
PYTHONDONTWRITEBYTECODE=1 \
  /Users/bjn00003/BDHub/01-BDSystem-V2/.venv/bin/python \
  scripts/<entry>.py
```

手动作业统一优先走页面或 `scripts/job-run.py`，因为它保存配置、检查同名进程并在安全点停止。不要同时另开同一底层 CLI 绕过作业锁。

## 12. 测试与验证

### Python

全量：

```bash
PYTHONWARNINGS=ignore PYTHONDONTWRITEBYTECODE=1 \
  /Users/bjn00003/BDHub/01-BDSystem-V2/.venv/bin/python \
  -m unittest discover -s tests
```

按文件：

```bash
PYTHONDONTWRITEBYTECODE=1 \
  /Users/bjn00003/BDHub/01-BDSystem-V2/.venv/bin/python \
  -m unittest discover -s tests -p 'test_send_batch.py'
```

### Web

```bash
cd apps/web
npm test
npm run typecheck
npm run build
```

### 文档

```bash
PYTHONDONTWRITEBYTECODE=1 \
  /Users/bjn00003/BDHub/01-BDSystem-V2/.venv/bin/python \
  scripts/check-docs.py
```

该检查验证主文档存在且非空，并检查所有 Markdown 本地链接。

### 分层报告

验证结果必须区分：

1. 单元/合同测试；
2. TypeScript 与构建；
3. 本机 API 和页面回读；
4. 只读真实数据/接口；
5. 真实平台写入与回执；
6. 业务结果。

任何上一层成功都不自动证明下一层。

## 13. Git 与发布

- 开发使用 `codex/` 分支，按继承基线、文档、功能和修复拆成逻辑提交。
- `var/`、`outputs/`、`.next/`、`node_modules/`、凭据和本机敏感配置不入 Git。
- 当前仓库没有远端；本地提交/标签不能替代异机备份。
- 部署只针对新项目 5198；先测试和构建，再确认当前 PID/cwd，替换实例后回读相关 API。
- 代码变更不自动重启 worker、恢复发送、开启自动回复或改变持久任务状态。

## 14. 当前技术债与演进方向

- SQLite schema 分散在多个领域模块，缺统一 migration registry；需在稳定后收敛版本管理。
- `batch-tasks.sqlite` 长事务影响并发读取；需独立完成事务/WAL 设计与验证。
- Python 测试仍有未关闭 SQLite connection 的 `ResourceWarning`。
- Web Node 测试存在 module type warning；Next.js 构建有上游 deprecation warning。
- 发送预检尚未接持久批次、start/stop 和执行器。
- 本机 `var/` 缺正式备份、恢复和跨机器迁移方案。
- 新项目仍依赖旧 Python 环境与部分协议层；最终需要独立依赖和凭据管理。

## 15. 技术文档变更规则

- 模块职责、调用链、数据模型、API、运行、部署、配置或测试方式变化：同步更新本文。
- 业务目标、范围、规则和验收变化：更新 [项目文档](PROJECT.md)，不要只改技术实现。
- 当前进度、动态数量、Git 提交、进程和临时故障：更新当前交接页，不写入本文成为常量。
- 细节设计、实验、压测和事故复盘保留在 `architecture/`、`implementation/`、`research/` 或 `archive/`，并从本文按需链接。
