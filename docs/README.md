# BDHub-Agent 文档导航

本文档树按“当前状态 → 产品规则 → 架构契约 → 实现证据 → 历史追溯”读取。文件日期表示记录时间，不代表它天然比当前交接或实时台账优先。

## 当前资料

| 目的 | 入口 |
| --- | --- |
| 接续当前开发 | [Codex 接管状态](handoff/codex-takeover-20260919.md) |
| 确认 V1 产品范围 | [PRD](PRD.md) |
| 查询已确认与待确认决策 | [决策登记](DECISIONS.md) |
| 了解开发与运行边界 | [项目约定](../AGENTS.md) |
| 追溯 DeepSeek 接手时状态 | [DeepSeek 交接快照](handoff/deepseek-harness-20260914.md) |

## 架构资料

只读取与当前任务直接相关的文档：

| 领域 | 资料 |
| --- | --- |
| 批次与 V1 主流程 | [批次架构](architecture/batch-outreach-v2.md)、[货盘先备链](architecture/catalog-first-preparation.md) |
| 货盘全链路 | [货盘页链路](architecture/catalog-page-chain.md)、[非全托 Campaign](architecture/non-full-managed-campaign-v1.md) |
| 达人线索与发送池 | [线索发送池](architecture/lead-sending-pool.md) |
| 发送、监控、回复和日历 | [工作台方案](architecture/send-monitor-reply-calendar-v1.md) |
| 身份、改名和关系 | [稳定身份](architecture/creator-identity-and-rename.md)、[二发确认规则](architecture/second-cycle-confirmed-policy.md) |
| 账号和维护 | [账号调度](architecture/account-scheduling.md)、[双账号生命周期](architecture/dual-account-lifecycle.md) |
| Agent 运行 | [Agent runtime](architecture/agent-runtime.md)、[系统设计](architecture/system-design.md) |

架构文档说明目标、状态机和合同；其中带日期的数量是当时证据，不应作为当前页面常量。

## 实现与验收证据

`implementation/` 保存按阶段形成的实现说明和验证结果。当前常用入口：

- [货盘主动动作与事故复盘](implementation/catalog-actions-and-incident-20260915.md)
- [货盘页面整理](implementation/catalog-ui-reorg-20260914.md)
- [商品短名](implementation/catalog-short-names-20260915.md)
- [OECID 阶段](implementation/creator-identity-oecid-stage-20260915.md)
- [线索查询队列](implementation/leads-query-queue-v1.md)
- [作业面板与 Kalodata 身份](implementation/workbench-jobs-and-kalodata-identity.md)
- [ACC9 单品建链](implementation/acc9-catalog-link-canary.md)
- [12 QPS / 500 人身份验收](implementation/identity-12qps-500-release.md)

实现报告用于说明“当时做了什么、怎样验证、有哪些限制”。当前能力仍须结合源码、测试、`var/` 台账和当前交接页核对。

## 接口合同与研究

- `contracts/tiktok/`：TikTok 接口、字段、工具边界和已知缺口。
- `contracts/legacy-interface-inventory.md`：旧 BDHub 能力索引，仅供只读借鉴。
- `research/`：DeepSeek、旧逻辑、匹配数据、UI 模板等调查资料；研究结论不自动成为业务规则。

## 归档

`archive/` 保存被当前产品口径替代的 PRD、README 和长状态流水。归档只用于追溯，不参与当前开发决策。完整历史也可从 Git 标签 `takeover-20260919-inherited` 和此前提交恢复。

## 维护规则

- 稳定边界写入 `AGENTS.md`；动态进展、Git 基线和运行快照写入当前交接页。
- 产品策略写入 PRD/DECISIONS；架构文件不以一次实测数字冒充永久配置。
- 每次功能改动只更新直接失真的文档；旧说明被取代时显式标记，不继续堆叠多个“最新”。
- 文档不保存激活码、联系邮箱、Cookie、身份文件、原始私密消息或无必要的业务身份。
