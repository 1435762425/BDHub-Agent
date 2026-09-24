# 文档导航

当前规则只在 [PROJECT.md](PROJECT.md)（产品）与 [TECHNICAL.md](TECHNICAL.md)（实现）维护。[AGENTS.md](../AGENTS.md) 规定执行边界，[当前交接](handoff/codex-takeover-20260919.md) 记录带时间的运行快照。日期化报告只证明当时的范围和结果，不提供当前执行授权。

## 按问题阅读

| 问题 | 资料 |
| --- | --- |
| 下一步改进与验收指标 | [审计与整改方向](implementation/audit-remediation-priorities-20260923.md) |
| 商品全生命周期 | [PID 生命周期](architecture/pid-lifecycle-v1.md)、[Campaign](architecture/non-full-managed-campaign-v1.md) |
| 工作流、市场与账号 | [自动运营设计](architecture/automated-operations-workflow-v1.md)、[市场接入标准](architecture/market-rollout-standard-v1.md)、[账号生命周期](architecture/dual-account-lifecycle.md) |
| 身份与排序 | [稳定身份](architecture/creator-identity-and-rename.md)、[排序规则](PROJECT.md#43-发送位置) |
| 会话与当前 Agent | [V2 设计](architecture/conversation-agent-upgrade-plan-20260923.md) |
| 四市场真实平台结果/限制 | [启动实测](implementation/four-market-launch-implementation-20260923.md)、[接入证据](implementation/br-my-uk-onboarding-20260921.md) |
| OECID 成本与恢复 | [真实吞吐](implementation/identity-oecid-throughput-20260920.md)、[12 QPS 验收](implementation/identity-12qps-500-release.md) |
| Kalodata 数据口径 | [GMV 实测](implementation/kalodata-gmv-live-probe-20260920.md)、[视频证据](implementation/kalodata-zero-sale-video-evidence-20260920.md) |
| 回复模型历史局限 | [V1 人工评测](implementation/reply-model-evaluation-20260920.md)，不能外推当前 V2 |
| 平台协议与资源 | [TikTok 合同](contracts/tiktok/README.md)、[vendor](../vendor/README.md) |
| 旧需求/决策/状态 | [归档](archive/README.md)，仅按具体问题查阅 |

## 维护规则

- 产品规则、范围或验收变化更新 PROJECT；模块、数据、接口、运行或测试变化更新 TECHNICAL。
- 数量、进程、提交、当前故障只更新 handoff；一次实验和事故放 implementation/research，注明时间、范围、证据与限制。
- 旧实现文件可为历史事实保留；被替代的设计标注历史。重复跳转和过时操作提示可直接删除，链接指向唯一内容；不为每次删减另存整份快照。
- 主文档与代码不一致时先识别需求变化、实现缺口或运行故障，不从历史报告推导新规则。

检查所有非忽略 Markdown 的本地路径、图片与标题锚点：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/check-docs.py
```
