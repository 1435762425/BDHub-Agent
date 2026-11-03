# 文档导航

从问题进入对应文档，避免把历史方案当成当前合同：

| 内容 | 唯一维护位置 |
| --- | --- |
| 业务规则、自动执行范围和完成标准 | [PROJECT.md](PROJECT.md) |
| 当前模块、数据与并发合同、接口、运行方法、验证边界 | [TECHNICAL.md](TECHNICAL.md)；关键文件表在 §1 |
| 生产发布、进程、实际开关和当前阻塞 | [当前交接](handoff/current.md)；最新补充：[运行故障修复与核验](handoff/runtime-health-20261025.md)，均为带时间快照 |
| Agent 修改与执行边界 | [AGENTS.md](../AGENTS.md) |

日期化 implementation/research 证明当时范围和结果，不提供新授权。模块方案与 AI 审查页顶部实施表覆盖其早期“待实施”描述，最终以主文档和代码合同为准。

## 按问题阅读

| 问题 | 资料 |
| --- | --- |
| 另一台电脑的 Claude 继续开发 | [开发接手说明](handoff/development-20261102.md)：代码基线、环境、阅读顺序及未迁移的生产资源 |
| 新电脑配置 Kalodata 激活码、登录器和扩展 | [Kalodata 配置教程](setup/kalodata.md)：私有交接仓库、路径安装及正式 provider 验收 |
| 自主运行模式、读取成本与经营工作台 | [第二轮改进与验收](implementation/autonomous-design-20261024.md)、[后续架构候选](architecture/autonomous-runtime-evolution-20261024.md)；前一批见[运行恢复与页面精简](implementation/project-optimization-20261024.md) |
| BR 回复输入冲突、达人详情 503 与候选统计口径 | [运行修复、上线核验及备份压缩映射](handoff/runtime-health-20261025.md) |
| 分市场经营日报、累计指标和 Codex 定时通报 | [每日经营日报](implementation/business-daily-mail.md) |
| 每日发送量下降、Kalodata 供给与巴西发送恢复 | [发送量排查与恢复](handoff/send-volume-20261026.md) |
| 审计修复与发布验证 | [审计修复](implementation/audit-fixes-20261003.md)：首发授权、证据归属、市场隔离、总览时效和备份恢复 |
| 下一步改进、缺陷与验收指标 | [各模块优化方案](implementation/module-optimization-plan-20260925.md)（已审查，主体业务已逐项确认，§10 汇总实施边界）、[项目审计与清理](implementation/project-audit-20260924.md) |
| 调度/B 类连续性与 Agent 执行修复 | [验证、加载与剩余范围](implementation/continuity-agent-runtime-20260925.md) |
| 第一批只读运营面板交付 | [实现、验证与 Web 发布状态](implementation/readonly-operations-overview-20260925.md) |
| 商品全生命周期 | [PID 生命周期](architecture/pid-lifecycle-v1.md)、[Campaign](architecture/non-full-managed-campaign-v1.md) |
| 巴西一发、主营类目匹配与样品审批 | [交付与平台验证边界](implementation/br-first-outreach-20261023.md) |
| 当前代码、GitHub 与回退 | [仓库说明](../README.md#github-与恢复)、[SDK/HTTP 上线及保留数据的回退](implementation/sdk-http-rollout-20260926.md) |
| 工作流、市场与账号 | [自动运营设计](architecture/automated-operations-workflow-v1.md)、[市场接入标准](architecture/market-rollout-standard-v1.md)、[账号生命周期](architecture/dual-account-lifecycle.md) |
| 登录启动、进程缺失和持久暂停 | [macOS 启动恢复](implementation/macos-startup-recovery.md)、[TECHNICAL §7](TECHNICAL.md#7-运行与验证) |
| 身份与排序 | [稳定身份](architecture/creator-identity-and-rename.md)、[排序规则](PROJECT.md#43-发送位置) |
| TikTok 达人核心画像及各市场权限缺口 | [接入与验收](implementation/creator-core-profile-chain-20261011.md)、[长期链路实验](research/creator-core-profile-tiktok-20261011.md)；当前合同见 TECHNICAL |
| 会话与当前 Agent | [已确认 V2 场景与原设计](architecture/conversation-agent-upgrade-plan-20260923.md)、[AI 回复设计审查与优化方案](architecture/ai-reply-optimization-20260925.md)（已完成消息范围/原子准备；AI unknown 隔离、长期记忆等剩余项见顶部状态表） |
| 四市场真实平台结果/限制 | [启动实测](implementation/four-market-launch-implementation-20260923.md)、[接入证据](implementation/br-my-uk-onboarding-20260921.md) |
| DE/FR/TH 接入内容 | [德语](implementation/de-content-review-20261002.md)、[法语](implementation/fr-content-review-20261003.md)、[泰语](implementation/th-content-review-20261003.md)审阅稿；接入状态见 [TECHNICAL §5](TECHNICAL.md#5-账号配置与-vendor) 与[当前交接](handoff/current.md) |
| OECID 成本与恢复 | [真实吞吐](implementation/identity-oecid-throughput-20260920.md)、[12 QPS 验收](implementation/identity-12qps-500-release.md)、[技术预算与隔离](implementation/identity-budget-20260925.md) |
| 发送机会与未知隔离 | [80/20 分配](implementation/outreach-share-20260925.md)、[二发有限核验](implementation/delivery-isolation-20260925.md) |
| 待回复范围与原邀请衔接 | [集中回复队列](implementation/reply-scope-queue-20260925.md)、[邀请与新来信](implementation/invitation-inbound-20260925.md)、[UK 原意图与模拟](implementation/uk-preflight-ai-simulation-20260926.md) |
| Kalodata 数据口径 | [GMV 实测](implementation/kalodata-gmv-live-probe-20260920.md)、[视频证据](implementation/kalodata-zero-sale-video-evidence-20260920.md) |
| 回复模型历史局限 | [V1 人工评测](implementation/reply-model-evaluation-20260920.md)，不能外推当前 V2 |
| 平台协议与资源 | [TikTok 合同](contracts/tiktok/README.md)、[vendor](../vendor/README.md) |
| 历史实现与事故证据 | [货盘动作与事故](implementation/catalog-actions-and-incident-20260915.md)、[采集时筛分](implementation/catalog-screen-at-collection-v1.md)、[商品短名](implementation/catalog-short-names-20260915.md)、[全托无库存门槛](implementation/full-managed-no-stock-gate.md)、[线索查询队列](implementation/leads-query-queue-v1.md)、[精确发现](implementation/creator-discovery.md)、[OECID 阶段](implementation/creator-identity-oecid-stage-20260915.md)、[身份压测](implementation/identity-single-multi-stress.md)、[12 QPS 追加 1000](implementation/identity-12qps-extra1000.md)、[作业与 Kalodata 身份](implementation/workbench-jobs-and-kalodata-identity.md) |
| Web 模板许可 | [TailAdmin 许可核对](research/tailadmin-license-review.md)、[第三方来源](../apps/web/THIRD_PARTY_NOTICES.md) |
| 旧需求/决策/状态 | [归档](archive/README.md)；2026-09-24 移出的文档用 `git show audit-base-20260924:<路径>` 查看 |

## 维护规则

- 产品规则、范围或验收变化更新 PROJECT；模块、数据、接口、运行或测试变化更新 TECHNICAL。
- 数量、进程、提交、当前故障只更新 handoff；一次实验和事故放 implementation/research，注明时间、范围、证据与限制。
- 旧实现文件可为历史事实保留；被替代的设计标注历史。重复跳转和过时操作提示可直接删除，链接指向唯一内容；不为每次删减另存整份快照。
- 主文档与代码不一致时先识别需求变化、实现缺口或运行故障，不从历史报告推导新规则。

检查所有非忽略 Markdown 的本地路径、图片与标题锚点：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/check-docs.py
```
