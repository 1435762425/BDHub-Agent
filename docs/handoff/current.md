# BDHub-Agent 当前交接

更新时间：2026-09-24 11:40（Asia/Shanghai）。本页只记录运行快照；规则见 [PROJECT](../PROJECT.md)，实现见 [TECHNICAL](../TECHNICAL.md)，后续方向见[项目审计与清理](../implementation/project-audit-20260924.md)。更早流水见[历史交接](../archive/handoff/codex-takeover-history-20260923.md)。

## 2026-09-24 生产暂停（用户要求）

- 11:18–11:19 按用户要求暂停，全部使用系统自带开关，未强杀进程：
  - IT/BR/UK 自动运营总开关关闭（revision 5/3/3；MY 本来未开）。
  - 四市场持续发送执行 stop（`runRequested=false`、`stopRequested=true`，revision IT 16、BR 4、MY 2、UK 4）。
  - scheduler、IT 收信和 IT Agent 作业写入停止文件；BR/MY/UK 的收信与 Agent worker 收到 SIGTERM 后在安全点退出。
  - 11:19:45 起项目 worker 为 0，只有 Web 在运行。
- 暂停前没有在途平台写入（18 个 ready 组件未开始，另有 IT 2 个 quarantined_unknown、1 个 partial_delivery 保持原状）。Agent 开关、指南和发送模板未改。前后状态回执在本机 `outputs/pause-20260924/`。
- 暂停时待回复的达人来信：BR 48、IT 57、MY 15、UK 8。MY 收信从 10:43 起在认证阶段连续失败（`taplink_remote_read_failed`），早于暂停。
- 恢复需要明确操作：打开各市场自动运营、在页面对持续发送执行 start（首发授权规则不变）、启动 scheduler。恢复前先处理 MY 认证问题，并按讨论结论调整调度。

## 2026-09-24 本机清理（用户同意）

- `var/` 中 4,458 个 `market-identity-*` 临时目录和 917 个超过 3 天的旧运行报告与日志已压缩归档到 `/Users/bjn00003/BDHub/BDHub-Agent-backups/var-archive-20260924/`，逐字节校验后从 `var/` 移除，释放 225MB；归档目录附有清单和恢复命令。仍被代码读写的 15 个旧状态或日志文件保留。
- 删除了 4 个已完全合并的旧分支，以及 31 个空目录；它们仍在离线包 `BDHub-Agent-20260924.bundle` 中。
- 按用户确认的保留策略（当前加最近 2 份）首次执行 `state-retention.py`：
  - 执行前先做了 33 库全量备份 `var/backups/state/20260924T035449Z-pre-retention-20260924`（1.57GB，verify 通过）。
  - 归档到 `../BDHub-Agent-backups/retention/20260924T035517Z`（24MB）的内容：IT 的 7 份旧货盘快照；IT 的 3 个旧全托轮次明细（127,989 行，轮次记录保留）；8 个旧采集 JSON；调度日志轮转。
  - 压缩后全托库从 172MB 降到 105MB，主库从 552MB 降到 517MB。所有库 quick_check 为 ok，各货盘 head 完整。
  - IT 两轮待恢复的来源轮次、UK 全部 3 轮都在保护名单里，未动。账号身份旧代次还在 72 小时安全窗口内，下次执行时再归档（acc4 约 1.1GB）。

## 2026-09-24 会话页修复

- 自 2026-09-23 23:14 的 Web 构建起，会话页在浏览器里读不出任何队列（`fetch` 被当作对象方法调用，浏览器报 Illegal invocation），四个市场都显示 0 条和“暂时无法读取会话队列”。台账与接口数据完整。
- 已修复并补回归测试，重新构建 Web：当前 build `jso911ocYYSg1oS4_HKVd`；上一版构建备份在 `var/web-releases/conversation-fetch-fix-20260924/previous`。

## 2026-09-24 项目审计与清理

- 回退锚点：基线标签 `audit-base-20260924`（`b4efe70`）；审计开始时生产目录未提交改动的快照 `audit-wip-20260924`；仓库外离线包 `/Users/bjn00003/BDHub/BDHub-Agent-backups/git/BDHub-Agent-20260924.bundle`。
- 分支 `codex/claude-audit-20260924` 删除了 37 个无调用脚本、6 个 lib 模块、4 个页面已不调用的 API 和 109 个过时文档，并按代码修正了主文档。活代码路径、`config/*.json`、`var/`、服务和授权都未改。清单、缺陷与建议见[项目审计与清理](../implementation/project-audit-20260924.md)。
- 被删文件都不在 scheduler、job_run 或 Web bridge 的调用路径上，合入没有重启 worker。之后因会话页修复重新构建了 Web：已删的 4 个接口现返回 404，生产目录 typecheck 通过。
- 生产目录原有的 16 个未提交改动（与 IT workflow 恢复相关）保持原样，如何处置仍待用户决定。

## 本轮交付与验证

- 上轮已交付：四市场主动推品冷却 72h；机构后台外发正文进入会话与 AI 上下文；有真实卡的肯定合作场景补加橱窗提醒。MY 6 会话 7 条机构正文经本地与 HTTP 时间线核对，缺失/重复 0。
- 新增 `inbox-history.py`：原生 OLDER 分页、身份绑定、页级回执与断点。补扫不吞新达人回复、不创建 pending、不改关系/授权、不解除 gap 或 unknown；机构外发超过热读 20 条窗口仍可补正文。
- MY 两会话分别读完 3 页 13 条、3 页 6 条，第一会话跨进程续读；返回消息都已存在，新增 0、延后 0。证明本次分页、断点和幂等，不能外推全市场历史完整。实时窗口外的延后达人消息仍需独立恢复策略。
- V2 四市场 × 12 多轮合成评测已加入仓库。最后一轮 47/48 筛查通过；MY 无卡场景主动澄清且 reply/clarification 冲突被生产校验拒绝。另有三条内部中文解释精度观察；原始输入输出和失败样本保留，不作为生产准确率。
- 新增只读备份保留计划与发送耗时诊断。31 份完整备份校验通过；最新 33 库约 1.48 GB 在空目录恢复并验证，用时 5.043 秒，临时副本已清理。保留策略删除候选 0，未删除备份；异机副本尚未完成。
- 最终 Python 全量 1,250 项、24 项相关 Web 测试、文档链接与 diff 检查通过，无 ResourceWarning。本轮没有 Web 源码变更，不重复构建或更换在用 Web。

## 运行与数据回读

- `second-cycle` 已备份后应用 v19/v20 历史覆盖迁移。v20 前备份 `var/backups/state/second-cycle-before-history-v20-guide2-20260923T161345Z.sqlite`，527,781,888 字节，SHA-256 `cb41ad40bc93a4367d8f7d470a7fd3aa2c5c5264bbf3f35465a823889ac1cf2c`，quick_check=ok。所有 registry 检查通过。
- 应用户空间清理请求，先新建并验证 33 库完整备份 `var/backups/state/20260923T164039Z-pre-storage-cleanup`，再按明确清单删去 20 份中间重复快照，实测释放 11.49 GiB；12 份保留的多库备份逐份通过 hash 与 SQLite 校验。单库事故备份、其他 6 组旧存档、当前数据库和服务未动。明细回执在本机 `outputs/backup-cleanup-20260924/`；这仍是本机备份，异机副本尚未完成。
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
