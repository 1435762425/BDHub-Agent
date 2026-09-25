# 四市场 SDK 收信与 HTTP 发信上线（2026-09-26）

用户明确要求按新方案直接上线 IT/BR/MY/UK，并先做好 Git 管理和回退备份。此次保持原发送执行器和授权，改变通讯会话的管理与 OECID 账号分工；没有启用新发送渠道。

## 账号与执行路线

| 市场 | SDK 收信、HTTP 二发及 AI 回复 | Campaign、商品、TapLink、OECID |
| --- | --- | --- |
| IT | acc6 | acc9 |
| BR | acc1 | acc2 |
| MY | acc8 | acc5 |
| UK | acc11 | acc4 |

每市场一个 `im-session-worker.py` 持通讯账号原 ProfileLease、浏览器会话及 HTTP 认证，原发送/回复消费者经同用户私有 socket 借用。接收通知和 HTTP 执行分线程，浏览器初始化期间 HTTP 可继续读写；借用仍须通过原发送许可、窗口、账号/代次、逐写锁、冻结材料和 requestRef 检查。SDK 不负责发信，未知回执不因迁移更换账号或重发。

供给账号真实 Find 验证通过后才发布 `oecid_find` 能力。IT/UK 的额外 Profile type2 也通过；BR/MY 此额外接口返回业务码 100000，未发布画像能力。BR/MY 正式 OECID 路线只依赖已验证 Find，不把它冒充画像验收。IT 先恢复原 acc6 的旧交接证据，再按新 acc9 有界查询，不迁移原 12 QPS 验收。

## Git 与状态备份

- 上线前所有已批准但未提交的文档原样纳入 `ba3b6d0`，保留原差异副本；生产分支为 `codex/v1-runtime-alignment`。
- 上线前标签 `pre-sdk-rollout-20260926`、分支 `codex/pre-sdk-rollout-20260926` 均指向 `ba3b6d0b44174db0fee62fc9a4d066b753deb563`。
- 离线 Git 备份 `var/releases/sdk-http-rollout-20260926/pre-sdk-rollout.bundle` 已通过 `git bundle verify`，SHA256 保存在同目录 `release.json`。
- 同目录保存 `before-second-cycle.sqlite`、`before-creator-identities.sqlite`、`before-creator-discovery.sqlite`，均在线一致备份且 `quick_check=ok`。凭据、生产状态与私密报告不入 Git。
- 独立 worktree `../BDHub-Agent-sdk-rollout-20260926`、分支 `codex/four-market-sdk-http` 开发验证后按逻辑提交快进合入。最终发布标签和运行快照见当前交接及 `release.json`。

schema v26 只追加 SDK 唤醒表和索引；真实生产副本演练验证旧业务表摘要一致、重复迁移为空。唤醒信号不是新消息台账，原 Inbox/Service 和消息版本仍是权威。

## 验证与上线中修正

- 全量 Python 1,474 项通过，包含原模式隔离回归、四市场会话借用、账号/代次拒绝、drain、原业务异常传播、唤醒幂等与编辑、旧卡片回放及 SDK 状态枚举。最后仅新增目标消息越过已知历史重叠及页数耗尽回归，owner 专项 15 项通过。旧用例明确固定兼容配置，不再被生产角色开关隐式改变。既有 ResourceWarning 保留。
- Web Node 180 项通过；Web 源码未改动，无需重复 build/typecheck。
- 四市场真实 HTTP 会话列表均经 `sdk_http_owner` 取得，每市场回读 10 个会话；没有跨市场会话或身份文件变化。
- 新主链的 IT/BR/UK 已产生供给账号的实际 Find 成功事件。MY 主链仍按原设置关闭；acc5 只读 canary 已通过，不为验收开启主链。
- scheduler 恢复后，原 HTTP AI 执行器已确认 BR 4、UK 2 条自然业务回复；该快照无 inflight/accepted/unknown AI 回复。IT 当时已过回复窗口，MY 尚未到窗口。没有人为生成测试来信或测试发送，不把会话列表可读当作卡文吞吐验收。
- 四市场正常运行至约 600 秒自动 drain，均 `errorType=null`，随后以新 epoch 恢复 SDK。续期观察 32 次、约每 5 秒一次的 HTTP 借用采样：IT 1 次不可用、BR/MY/UK 各 2 次不可用，之后恢复；这是交接短暂等待，不宣称完全无间断。SDK 列表初始化约一分钟期间，HTTP 已先恢复服务。没有再次出现约 80 秒的错误重连。
- 四市场页面与概览接口均返回 HTTP 200；实际新来信、发送窗口与业务样本限制仍按下文说明。
- 9,718 份原投递快照、19,436 个组件 requestRef 与上线前一致；发送控制、AI 设置、指南、回复配置、自动运营设置、授权事件及八账号 headers 一致，主库 `quick_check=ok`。

首次启动暴露了两个缺陷，已修正并记录证据：

1. SDK 启动回放的旧卡片带占位文本，原正文解析器把卡片记录为非文本，最初误当文本修改产生多余唤醒。修正后跳过已确认非文本回放；只将与原权威非文本消息精确匹配的唤醒行收口（IT 1045、BR 967、MY 959、UK 958），没有修改 Inbox/pending/回复/发送状态。
2. 旧探针把 SDK 状态 1 当作就绪。核对当前平台公开前端代码证实：1=INIT_LOADING，2=INIT_SUCCESS，3=INIT_FAILED；因此正常初始化完成后被误判断线，约 80 秒重启。新负责人按 2 判定，增加枚举回归测试；没有靠延长断线时间掩盖问题。调试采集不记录运行凭据，临时公开 JS/截图验收后清理。

接收通知已在 BR/UK 触发真实回查，但不能据此宣称四市场新达人消息的端到端实时延迟已充分验收。原时间窗、额度、MY 主链开关及 AI unknown 的既有阻断合同保持；此前规划的 AI unknown 有限隔离不在本次上线范围。Web 未改代码、未重建或重启，旧 BDHub 未修改。

## 回退步骤：保留新消息，回到原运行路线

常规回退使用**当前兼容代码和当前数据库**，将四市场 `imSessionMode` 改为 `http_polling`、`identityAccountRole` 改为 `communications`。不能直接 `git reset` 回旧代码再覆盖旧数据库：旧迁移检查不认识 v26，且恢复整库会丢失上线后新增来信、实际回复与回执。

1. 先保存当前 Git 差异和新的在线 SQLite 备份；记录 scheduler、四个 owner、原发送/AI worker 的当前 PID、授权与在途意图。
2. 对当前 scheduler 发 SIGTERM，等待其原 workflow 子任务完成、claims 释放。不要只杀父进程后启动第二个 scheduler。
3. 对原四市场 AI/发送 worker 发 SIGTERM，等待当前请求落账；inflight/unknown 保留原 requestRef，不重置。停止四个 owner，等借用客户端退出、原 ProfileLease 释放，确认无残留 SDK 浏览器及 socket。
4. 在独立 worktree 只改上述两个模式字段，运行配置校验和相关回归，提交后合入。固定角色映射、账号凭据、授权及业务表不改。旧未完成 OECID 报告仍按其冻结供给账号消费，不按新角色换号重试。
5. 使用 `lib.operations_scheduler.start_scheduler(Path.cwd())` 恢复原监督器；由其依原持久授权拉起 polling、HTTP sender 与 Agent，不重新点全量授权。若原有明确 stop/pause，则按原值保留，不删除它来强行启动。
6. 四市场核对：poller 健康且没有 owner；实际读取账号为原通讯账号；旧快照/requestRef、业务控制一致；已有未知意图仍走原核验；自然窗口内观察原 HTTP 发送回执。v26 唤醒表保留，兼容模式不消费，日后重启 SDK 可继续。

需要查看或重新构建上线前代码，可在新目录用标签 `pre-sdk-rollout-20260926` 建 worktree，或用已验证 bundle 恢复仓库；这是代码恢复点，不是覆盖生产状态的许可。严重故障若必须回到旧代码，应先单独评估新增表的兼容迁移和上线后数据承接，不执行破坏性降库。
