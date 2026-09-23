# BDHub-Agent 当前交接

更新时间：2026-09-23 23:30（Asia/Shanghai）。只记录本轮快照；规则见 [PROJECT](../PROJECT.md)，实现见 [TECHNICAL](../TECHNICAL.md)，后续方向见[审计整改建议](../implementation/audit-remediation-priorities-20260923.md)。早期流水见[历史交接](../archive/handoff/codex-takeover-history-20260923.md)。

## 本轮交付与验证

- 四市场主动推品冷却统一 72h，`outreach_policy.py` 同时读取本地已确认/部分发送和平台我方外发；已回复/橱窗不缩短，客服回复仍独立。逐写精确排除同一 delivery 的已确认卡片，不误挡自己的配套文字。
- `Service.capture` 保存已验证的机构后台 ourMessages 正文；会话与 AI 按 plan/OEC/cid/messageId 去重。只含外发的会话也可打开；运营已在后台回复时不再自动回答旧 turn，新的达人消息可重新进入处理。
- 四市场指南已保存为 revision 1。肯定合作且上文有真实卡、尚无该商品加橱窗证据时补一句本地语言提醒；已加、已提醒、纯感谢不重复。7 个合成 DeepSeek 案例符合预期，未调用真实发送。
- Python 全量 1,212 项、Web 182 项、TypeScript、生产构建、文档检查通过；SQLite 连接 ResourceWarning 为 0。模型模拟是合成样本，不代表生产准确率。
- AGENTS/PROJECT/TECHNICAL/导航已删减；删除纯跳转与过时执行提示，关键历史实测保留。分支仍是 `codex/v1-runtime-alignment`，原有及并行 catalog/global/identity/workflow 改动未纳入本轮提交。

## 运行与数据回读

- Web 已替换为 build `sG39QqsWI2i7rQLD5ti1Z`，5198 监听 PID 25908；旧构建保留在 `var/web-releases/conversation-sync-20260923T151438Z/previous`。
- 四市场 sender、Agent、收信已在核对 PID/锁/断点后加载本轮代码。原持久授权、首条验证状态和停止条件未改；本轮没有通过 UI 发消息或修改模板开关。
- MY 的 6 个会话、7 条机构后台文本已经回补，并逐会话通过 HTTP timeline 回读，缺失/重复 0。当前每会话读取最新 20 条；未声称超过返回窗口的全部历史已覆盖。
- 四市场发送仍 waiting_capacity（滚动 24h 新联系 500）；Agent 仍在回复窗口外。本轮加载前后 `cycle_delivery_part.started` 数均为 4999，新增发送组件 0，Agent 已开始外发数仍为 0。
- 修改真实台账前已在线备份 `var/backups/state/second-cycle-before-outbound-sync-20260923T151121Z.sqlite`，524,001,280 字节，SHA-256 `d4a435fc69cbf797f700629abc517fd7346bed04a8590e36e99c34ccc4fe3de3`，quick_check=ok。只新增指南 revision 和真实消息正文；未重建发送或清理历史。
- 运行操作由本线程主 Agent 单独执行，每次核对原 PID/命令、原授权和锁后获取新 PID/锁/HTTP 回执；证据保存在 `outputs/audit-remediation-20260923/`，不入 Git。

## 未完成业务与接续

- IT `workflow-776f77d39bc2982e537f82670933` 仍为 `needs_human/workflow_retry_requires_review`，源头是 `global_catalog_not_published` 与重复普通采集。主链恢复需要关联原发布 run、阶段写入和 claim，不把其它 run 的完整 head 直接改算成功。
- IT 两条 quarantined_unknown 的原意图与案件保留；不因 72h 到期重发。每市场 30 位/分钟仍未验收达成。
- 优先推进双向消息历史分页覆盖/延迟、V2 真实多轮评测、阶段证据恢复，再做吞吐和备份保留期优化；具体指标见审计整改建议。
