# 2026-10-25 运行故障修复与核验

核验时间：2026-10-25 14:00–14:01（Asia/Shanghai）。本页补充[当前交接](current.md)和[上一轮核验](autonomous-design-20261024.md)，保留原业务开关、持久授权、任务、窗口与人工暂停。

## 结论与发布基线

本次发现并修复两处真实故障：BR 自动回复的上游/发送端文本校验冲突，以及合法达人身份详情因别名聚合扫描整个市场而返回 503。修复已发布，最终六项 HTTP 检查全部 200；原失败详情本次为 0.285 秒。BR 原草稿尚未实际投递核验，继续等待原回复窗口。

- 运行代码基线 `3ca74d5`，包含详情修复 `d3a6d60`、回复修复 `07ed567`、候选统计文案 `3ca74d5`。Python 常驻载入 `07ed567`，与最终基线的 scripts/vendor 树一致；后续纯文档提交无需重启。
- 最终 Web Build `MOdrBA58WhxQ-3BbwQexo`，304 个构建文件 manifest 已核对。原 21 个常驻角色保持，均为当前运行代码；七市场 SDK ready，Web 与 scheduler-watch 已加载。
- 发布前原工作安全 drain，无在途、无阶段 claim、无旧 resident 后加载修复。最终运行核验 12 项通过：原七表控制、一发控制与 117,918 个 requestRef 的映射及唯一性保持，`quick_check=ok`，原 `config/jobs.json` 与 `docs/handoff/current.md` 的未提交内容字节不变。
- Python 全量 2,136 项、Web 全量 252 项、build、随后 typecheck 通过。独立回复合同审查 74 项通过；详情浏览器检查 8 项、最终文案检查 7 项通过，无 JS 错误/警告或横向溢出。

## 故障与修复证据

### BR 原回复卡住

原草稿为 1,000 字符、1,038 UTF-8 字节，停在 `ready`、未 started，且没有 sender、receipt 或 proof。上游允许 1,200 字符，发送 adapter 却限 900；昨夜记录 396 次 `it_delivery_input_invalid` 和 7 次 ProfileBusyError，BR 回复没有确认送达。

现用 `im_text_contract` 统一模型新输出、缓存 ready 输出、草稿冻结和发送端：最多 1,200 字符、4,096 UTF-8 字节，拒绝 NUL/非法 Unicode，不截断或改写正文。真正不满足本地合同且可证明未提交的生成草稿，才可审计取消为 `not_submitted`，关联 decision 记 `input_blocked`，保留原问题、正文、请求和商业 handoff；已 started、带提交证据或 unknown 的不能走此路径。同范围/指南不会反复消耗模型或挡住其他达人。

本次原 1,000 字草稿符合统一后的合同，因此没有取消、改写或新建：原 ID、requestRef、正文 hash、控制与 pending revision 均保持。BR 原回复窗口为北京时间 00:30–08:00，核验时在窗口外，没有绕开窗口发送测试消息。**1,200 字符是既有本地合同，历史 confirmed 正文均不超过 900；尚无真实平台回执证明更长正文已送达。**

### 达人详情读取失败

同一原失败身份的 aliases 查询因 `GROUP BY handle` 选择市场级 `identity_alias_lookup`，诊断扫描至少 98 秒；接口在原超时预算内返回 503。其余详情段正常，不以删字段或延长超时处理。

新查询先按原 market/OEC/kind 物化范围，再沿用原聚合、排序。相同只读快照中，新 SQL 与原 SQL 指定历史索引后的结果逐字一致；固定 Node CLI 约 369 毫秒。生产原请求先后返回 200 / 0.535446 秒、200 / 0.285 秒，22 个画像字段、市场/身份/OEC 和原 leadGmv 空列表保持，缺失值与零值没有互换。完整详情、核心画像和名字历史已通过浏览器验收。

### 候选统计文案

`poolRemaining` 只统计 ready 位置，实际发送仍检查 queued、当日安排、历史去重、身份、材料、模板和容量，并做后续平台预检。首页改称“候选达人”，发送页改称“发送候选位置”，保留原数值、停止入口、失败和 unknown。

只读存在性检查表明 BR/DE/FR/MY/TH 都能在既有门禁下选到候选，不能认定池已耗尽。昨夜取消主要是本地 `recipient_message_limit`，没有新增平台额度/拒绝 signal；末轮 idle 历史快照没有保留，不能反推昨晚最终停发原因。本次不为改善数字改变资格、去重或补发。

## 当前经营与等待

- scheduler 心跳正常、无当前 error；原供给/lease 没有卡住，五个启用供给市场的最新滚动 run 均完成。IT/UK 原供给与二发关闭，保持原值。
- BR 原四轮六小时同步已收口，无当前 `materials_no_progress` 无限退避；最新匹配完整扫描 586,436 人、匹配 212,830 人。材料有 9 个当前有效方案及 10 个证据缺口：5 个 binding 非当前、4 个佣金缺失、1 个旧意图被取代；当前 actionable=0。这些缺口不被伪造为可发送材料。
- 当前活动 delivery/service_reply unknown 为 0；历史 quarantined/isolated 继续保留。七个 FIFO 队列为空，七市场 HTTP checkpoint 持续推进，待处理 SDK 信号为 0，gap/backfilling 为 0。
- BR 首页 technical=1 对应 10 月 23 日已用完三次原核验预算的旧回复隔离，和本次 1,000 字草稿无关；保留该达人的未知隔离，不清状态或重发。当前 worker 心跳正常、窗口外等待，最近 15 分钟未重复输入错误；这不能替代窗口内真实送达验收。
- BR 样本的全量会话冷复查仍约 139 分钟，MY/TH 仍有更旧的全量覆盖；SDK 约 600 秒整体轮换仍是已记录的结构改进空间。本轮未修改这些合同，也不据 ready 推断全量消息无缺失。DE 13:13 曾发生一次 SDK reader 退出，已自动恢复；退出原因缺少细日志仍是诊断不足。
- 人工事项、历史技术隔离、样品审批未验收能力和原审批设置保持；没有为巡检发送测试消息、审批样品或重发 unknown。日常窗口内已有授权任务产生的真实经营成果，仍以各自平台回执验收。

## 离线备份压缩与恢复

本机可用空间由约 2.2 GiB 回升至约 4.9 GiB。仅无损压缩昨天本轮生成且没有进程占用的离线 SQLite 副本；没有删除业务库、账号身份或历史台账。

| 项目 | 保留记录 |
| --- | --- |
| 原路径 | `/Users/bjn00003/BDHub-Agent-backups/creator-identities-before-read-index-20261024T152759.sqlite` |
| 当前归档 | 原路径追加 `.gz` |
| 原字节 / 归档字节 | 3,422,093,312 / 633,397,769 |
| 原文件及完整解压流 SHA256 | `7df01457b09434ee42da191c48e974c9d7a9dca4f80993cce8882f41cd60d6fd` |
| gzip SHA256 | `e80fe6a0c170f44346f3d0ad672614be1b5a78dec722c84217dc0bf8794385f6` |
| 原 mode / mtime_ns | `0600` / `1792826960112970583` |

压缩前原备份 `quick_check=ok`，完整解压流 hash 与原文件一致后才移除未压缩副本。昨天报告中的旧路径现映射至此 `.gz`；恢复时先确认空间，将 gzip 解压到新的空目标，核对上述原 SHA256 和 SQLite 完整性，再按 manifest 恢复 mode/mtime，保留归档，不覆盖当前业务库。

另有 97 个 inactive identity 目录约 22.68 GiB 的只读清理候选，本轮没有归档或删除，需补最终 ProfileLease/进程占用及完整恢复验证。离机备份 U 盘仍未连接，本机副本不代替离机备份。

## 证据与回退

本机私有证据位于生产 `var/releases/runtime-health-20261025/`，不进入 Git：

- `preflight.json`、`pre-signal.json`、`drained.json`、`preserved-state.json`、`runtime-postflight.json`：原控制/请求、收口、原草稿 hash 和运行验证。
- `web-manifest.json`、`web-published.json`：最终构建；before-candidate-label 文件保留首轮发布记录。两个 `previous-web-build*` 目录均保留。
- `final-http.json`、`delivery-verification.json`：最终接口与交付汇总。
- `br-technical-queue-final.json`：首页唯一技术挂起的原未知隔离及当前原草稿、worker 状态。
- `backup-compaction-preflight.json`、`backup-compaction.json`：离线备份占用、压缩、字节、hash、mode/mtime 与空间记录。
- `validation/`：全量 Python 日志、详情 SQL 等价/计划/接口/浏览器结果、最终候选文案截图与验收。

代码按逻辑提交，可回退代码与相应 Web 构建；不恢复旧数据库覆盖新来信、授权、请求或正常经营进展。纯文档归档后不重复重启服务。
