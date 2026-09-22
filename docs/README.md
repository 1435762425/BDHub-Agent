# BDHub-Agent 文档管理

项目只保留两个主文档入口：

1. [项目文档](PROJECT.md)：做什么、为什么、业务怎样运行、什么算完成；后续产品开发以此为依据。
2. [技术文档](TECHNICAL.md)：系统怎样实现、状态存在哪里、模块如何协作、怎样运行和验证。

[AGENTS.md](../AGENTS.md) 只管理 Agent 的执行边界和阅读路由；[当前交接](handoff/codex-takeover-20260919.md) 只管理动态进度。它们都不建立第三套产品或技术规则。

## 文档层级

| 层级 | 文档 | 应包含 | 不应包含 |
| --- | --- | --- | --- |
| 产品真相 | `PROJECT.md` | 目标、范围、角色、流程、业务规则、验收、路线图 | PID、进程号、临时故障、代码细节 |
| 技术真相 | `TECHNICAL.md` | 架构、模块、数据、API、运行、配置、测试、技术债 | 临时运行数量、历史流水、未经确认的产品规则 |
| Agent 路由 | `AGENTS.md` | 必读资料、授权边界、开发/验证方式 | 详细业务规则、动态状态、事故长文 |
| 当前状态 | `handoff/codex-takeover-20260919.md` | Git、进程、数量、当前风险、下一步和本轮验证 | 永久产品规则、完整架构说明 |
| 支撑资料 | `architecture/`、`implementation/`、`research/` | 细节设计、实现证据、实验、调查 | 与主文档竞争“当前真相” |
| 历史追溯 | `archive/` | 被替代的入口、状态流水和旧方案 | 当前开发指令 |

## 权威顺序

1. 用户当前明确指令；
2. `PROJECT.md` 的产品规则；
3. `TECHNICAL.md` 的实现契约；
4. 当前代码、测试和 `var/` 实时台账；
5. 当前交接页的动态快照；
6. 日期化架构、实现和研究资料；
7. 归档。

当产品文档与实际状态不一致时，不让代码或旧报告默默改写产品规则：先确认差异属于需求变化、实现缺口还是运行故障，再更新相应主文档。

## 支撑资料索引

### 产品细节与历史决定

- [旧 PRD 详细版](PRD.md)：2026-09-13 批次需求细节，现作为 `PROJECT.md` 的来源记录。
- [决策登记](DECISIONS.md)：逐轮确认与历史未决项，现作为来源记录。

### 架构细节

| 领域 | 资料 |
| --- | --- |
| 批次与 V1 主流程 | [批次架构](architecture/batch-outreach-v2.md)、[货盘先备链](architecture/catalog-first-preparation.md) |
| 货盘链路与 Campaign | [货盘页链路](architecture/catalog-page-chain.md)、[非全托 Campaign](architecture/non-full-managed-campaign-v1.md) |
| PID 来源、选入、链接、刷新和清理 | [PID 生命周期树](architecture/pid-lifecycle-v1.md) |
| 线索、OECID、发送池与 AI 回复 | [达人发送池与 AI 回复策略 v1](architecture/creator-pool-and-reply-policy-v1.md) |
| 旧发送池/工作台实施记录 | [线索发送池](architecture/lead-sending-pool.md)、[工作台方案](architecture/send-monitor-reply-calendar-v1.md)（仅用于追溯，冲突处以当前策略为准） |
| 身份与关系 | [稳定身份](architecture/creator-identity-and-rename.md)、[二发确认规则](architecture/second-cycle-confirmed-policy.md) |
| 账号与维护 | [账号调度](architecture/account-scheduling.md)、[双账号生命周期](architecture/dual-account-lifecycle.md) |
| 多市场扩展 | [多市场接入标准](architecture/market-rollout-standard-v1.md) |
| Agent 与系统 | [Agent runtime](architecture/agent-runtime.md)、[系统设计](architecture/system-design.md) |
| 自动运营首页与持续工作流 | [开发计划 v1](architecture/automated-operations-workflow-v1.md) |
| 全市场统一前端、并发与性能整理 | [开发计划 v1](architecture/multimarket-ui-runtime-hardening-plan-v1.md) |

### 实现与验收证据

`implementation/` 保存“当时实现了什么、如何测试、有哪些限制”。常用入口：

- [货盘主动动作与事故复盘](implementation/catalog-actions-and-incident-20260915.md)
- [货盘页面整理](implementation/catalog-ui-reorg-20260914.md)
- [商品短名](implementation/catalog-short-names-20260915.md)
- [OECID 阶段](implementation/creator-identity-oecid-stage-20260915.md)
- [线索查询队列](implementation/leads-query-queue-v1.md)
- [作业面板与 Kalodata 身份](implementation/workbench-jobs-and-kalodata-identity.md)
- [ACC9 单品建链](implementation/acc9-catalog-link-canary.md)
- [意大利标准 TapLink 全量补齐](implementation/italy-taplink-completion-20260920.md)
- [意大利回复分类最终人工评测](implementation/reply-model-evaluation-20260920.md)
- [Kalodata 意大利 PID GMV 实时口径探查](implementation/kalodata-gmv-live-probe-20260920.md)
- [12 QPS / 500 人身份验收](implementation/identity-12qps-500-release.md)
- [会话工作台、模板库与互斥回复窗口](implementation/conversation-workbench-and-template-library-20260920.md)

### 接口、研究与归档

- `contracts/tiktok/`：TikTok 接口、字段和工具边界。
- `contracts/legacy-interface-inventory.md`：旧 BDHub 能力索引，仅供只读借鉴。
- `research/`：模型、旧逻辑、匹配数据和 UI 调查；不自动成为业务规则。
- [Kalodata 网页可见字段清单](research/kalodata-web-fields-20260920.md)：直接基于已登录 IT 网页整理，不以当前接口字段代替网页口径。
- `archive/`：已被当前口径取代的状态流水和旧入口，只用于追溯。

## 更新规则

| 变化 | 必须更新 |
| --- | --- |
| 产品目标、范围、规则、用户流程、验收标准 | `PROJECT.md` |
| 模块、调用链、数据、API、配置、运行、部署、测试 | `TECHNICAL.md` |
| Git、动态数量、进程、当前故障、下一步 | 当前 handoff |
| Agent 执行权限或文档路由 | `AGENTS.md` |
| 一次实验、压测、发布或事故 | `implementation/` 或 `research/` |

同一事实只保留一个主归属。其他文档使用链接引用，不复制长段正文；旧说明被取代时标记“已被取代”，不继续堆多个“最新”。

文档变更后运行：

```bash
PYTHONDONTWRITEBYTECODE=1 \
  /Users/bjn00003/BDHub/BDHub-Agent/.venv/bin/python \
  scripts/check-docs.py
```

检查会确认主文档完整，并验证仓库内所有 Markdown 本地链接。
