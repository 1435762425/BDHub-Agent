# BDHub-Agent 当前交接

更新时间：2026-09-24 00:18（Asia/Shanghai）。本页只记录运行快照；规则见 [PROJECT](../PROJECT.md)，实现见 [TECHNICAL](../TECHNICAL.md)，后续方向见[审计整改建议](../implementation/audit-remediation-priorities-20260923.md)。更早流水见[历史交接](../archive/handoff/codex-takeover-history-20260923.md)。

## 本轮交付与验证

- 上轮已交付：四市场主动推品冷却 72h；机构后台外发正文进入会话与 AI 上下文；有真实卡的肯定合作场景补加橱窗提醒。MY 6 会话 7 条机构正文经本地与 HTTP 时间线核对，缺失/重复 0。
- 新增 `inbox-history.py`：原生 OLDER 分页、身份绑定、页级回执与断点。补扫不吞新达人回复、不创建 pending、不改关系/授权、不解除 gap 或 unknown；机构外发超过热读 20 条窗口仍可补正文。
- MY 两会话分别读完 3 页 13 条、3 页 6 条，第一会话跨进程续读；返回消息都已存在，新增 0、延后 0。证明本次分页、断点和幂等，不能外推全市场历史完整。实时窗口外的延后达人消息仍需独立恢复策略。
- V2 四市场 × 12 多轮合成评测已加入仓库。最后一轮 47/48 筛查通过；MY 无卡场景主动澄清且 reply/clarification 冲突被生产校验拒绝。另有三条内部中文解释精度观察；原始输入输出和失败样本保留，不作为生产准确率。
- 新增只读备份保留计划与发送耗时诊断。31 份完整备份校验通过；最新 33 库约 1.48 GB 在空目录恢复并验证，用时 5.043 秒，临时副本已清理。保留策略删除候选 0，未删除备份；异机副本尚未完成。
- 最终 Python 全量 1,250 项、24 项相关 Web 测试、文档链接与 diff 检查通过，无 ResourceWarning。本轮没有 Web 源码变更，不重复构建或更换在用 Web。

## 运行与数据回读

- `second-cycle` 已备份后应用 v19/v20 历史覆盖迁移。v20 前备份 `var/backups/state/second-cycle-before-history-v20-guide2-20260923T161345Z.sqlite`，527,781,888 字节，SHA-256 `cb41ad40bc93a4367d8f7d470a7fd3aa2c5c5264bbf3f35465a823889ac1cf2c`，quick_check=ok。所有 registry 检查通过。
- 四市场指南已保存为 revision 2，hash `9a2f2285dfe191d050321a2ec9ab3bbc919d2bd50534722f6b6addffe6e7da8f`，HTTP 回读一致。明确输出格式、纯感谢、重复提醒、无真实卡和无依据时限承诺的处理。
- 核对原 PID、锁、窗口和无在途回复后，仅重载既有 Agent worker：IT 35998、BR 36004、MY 36010、UK 36017；均 outside_reply_window。原 control_event、Agent 设置、发送控制、delivery_part、service_reply、pending 逐表 hash 未变。
- Web 保持 build `sG39QqsWI2i7rQLD5ti1Z`、5198 PID 25908；scheduler PID 15244 与发送/收信 worker 本轮未重启。发送组件 started 仍 4,999，本轮新增平台写入/真实发送 0。
- 本轮平台调用仅原账号只读历史与选入映射核验；模型调用仅合成评测。真实运行操作由主 Agent 单独执行。回执在 `outputs/audit-next-20260923/`，评测在 `outputs/agent-v2-eval-20260924-followup-v3-reviewed/`，备份/吞吐报告在 `outputs/audit-20260923/`。

## 未完成业务与接续

- IT 原轮次 `workflow-9285ce7cb0c7427f9e13c0a67389` 为 needs_human/parallel_selection_requires_review；后起重复轮次 `workflow-776f77d39bc2982e537f82670933` 为 needs_human/workflow_retry_requires_review。原 source 已发布进 coverage overlay，重复 source 已停且未发布；本轮没有恢复任一 workflow。
- 两 PID 尾号 77284、73153 已在 ACC9 已选池，但当前子活动与冻结活动不同，原活动 name/start/end 等映射证据不全。恢复预检正确拒绝 `workflow_selection_receipt_unverified`。已询问用户是否保留全部原证据并隔离这两项后继续；未获答案，不能代为选择。
- `workflow_recovery.py` 和 CLI 提供原阶段证据预检/幂等恢复准备；真实恢复尚未调用。实际恢复前需处理上述证据决策，并核对、加载同轮 source 复用的 scheduler 改动；在用 scheduler 未加载这些待交接改动。历史 platform_writes 累计保留，当前状态优先显示 active run。
- 原有 catalog/global/identity/scheduler 改动保留；本轮仅接纳 workflow 的累计写计数与 active 显示修正，其余不夹带提交或借发布自动执行。
- IT 两条建会话 quarantined_unknown 与商品选入映射是不同问题；均保留原意图，不能因 72h 到期重发。每市场 30 位/分钟仍未达成；继续先补分段耗时，再评估提速。批量历史补扫、真实多轮人工标注、异机备份是下一阶段方向。
