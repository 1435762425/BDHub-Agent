# 全面审计修复与发布验证（2026-10-03）

修复范围为本次审计的 A1–A8；运行代码于 19:57（Asia/Shanghai）加载，随后补齐了备份复制顺序。原运营开关、首发/全量授权、暂停状态及未知结果保持原合同。代码开发、假传输和临时库验证在独立 worktree 完成，未以真实达人发送验收新路径。

| 问题 | 修复后的行为 | 主要入口 |
| --- | --- | --- |
| A1 备份清单遗漏 | 纳入泰国线索库；清单缺失/未登记/不安全路径直接告警；恢复演练覆盖备份实际市场 | `config/state-backup.json`、`ops_alerts.py`、`restore_drill.py` |
| A2 二发首条恢复 | 精确绑定原页面 start，领取及提交事务复核；确认与原授权消费同事务，晋升可恢复；旧 ready 不借新 start，未知只查原意图 | `market_send_control.py`、`market_send_canary.py`、`cycle_delivery.py` |
| A3 AI 首条恢复 | 核验后重算 pilot，已提交首条先等待；提交事务复核；被动确认、主动回读及重启共用原证据完成能力登记 | `agent_reply_v2.py`、`cycle_auto_reply.py`、`run-agent-replies.py` |
| A4 Campaign 证据串用 | 只用本条原活动或冻结子活动的加入证据结算，保留原提交事实 | `campaign_join.py` |
| A5 TapLink 跨市场核验 | 原 run/spec/台账账号一致才领取；回读范围来自实际 transport；确认前再次复核，同类重新读取入口一起限定 | `catalog-link-prepare.py`、`catalog_links.py` |
| A6 paid 并发穿透 | 准备、建会话、卡和文字提交事务内检查 paid/rejected；仅取消确定未提交的受影响内容 | `cycle_delivery.py`、各市场候选入口 |
| A7 总览使用旧状态 | 每次只读 worker 状态与原控制，逐通道显示采样；真实新终态保留，过期状态不造人工待办 | `operations_console.py`、`OperationsConsole.tsx` |
| A8 文档矛盾 | 更正独立运行目录、private-data 依赖、G13/G14 决定和已实现的失败策略 | README、PROJECT、TECHNICAL、模块方案 |

首发归属不使用时间推测：缺少可靠绑定的历史记录保持明确等待；新的 standalone canary 不借用页面授权。已验证市场跳过首发历史扫描，正常持续发送不增加全量 JSON 解析成本。未批量改写历史 Campaign/TapLink 结果、unknown、requestRef 或用户授权。

## 验证

- Python 最终全量 1,721 项通过（96.206 秒）；原始结果保存在 `var/releases/audit-fixes-20261003/python-unittest.log`。
- Web 208 项通过，build 与随后 typecheck 通过；部署的 291 个构建文件逐项 hash 与 worktree 相同。
- 新增回归覆盖确认/停止/晋升间崩溃、暂停再 start、standalone 与旧记录边界、被动确认、原账号核验、paid 竞态、跨市场相同 PID、过期/终态/关闭通道及持续身份导入时的备份顺序。
- 首条授权与 paid 路径经过独立复核；数据库不做结构迁移，仍为 schema v27。
- 发布 postflight 全部通过：七张控制表摘要未变；原 60,662 条组件 requestRef 映射不变且全表唯一；主库 quick_check 为 ok；19 个常驻角色相同且加载当前代码，七个 IM owner ready。
- 页面、总览 API、告警 API 回读 HTTP 200。稳定后总览单次约 4.93 秒、告警约 3.26 秒；启动检查期间的冷读曾较慢，不把单次延迟当吞吐验收。
- `config/jobs.json` 的原未提交运营变更保留，SHA-256 仍为 `60accd285d34ef1948d40009f3b43b9e8b536bc82e5c2798348623bfecd0a259`。

## 备份与恢复

用户明确批准后，只删除了 `var/backups/state/20260929T132106Z-pre-audit-b798911`：删除前校验 valid、无打开文件，移除 3,210,859,560 字节；10 月 1 日、10 月 2 日及其他备份保留。删除字节数与操作系统可用空间变化分开记录，不把之后系统回收的空间归因于本次删除。

最终可恢复的备份：`/Users/bjn00003/BDHub-Agent-backups/state/20261003T121047Z-audit-fixes-ordered`。46 库，manifest/hash/quick_check 校验通过；正式隔离恢复演练 `state=restorable`、blockers 为空，所有临时恢复副本已删除。

前两份新备份各库均有效，但演练分别发现 5/3 个泰国身份引用缺口。原因是另一 Claude 会话的身份导入循环不属于常驻进程登记，复制父库后仍有新身份及关系写入；现场真实库没有这些缺口。本次保留该任务，改成先复制引用库（second-cycle/catalog-links），再复制追加式父库（身份/来源），新回归和实际备份均证明引用完整；不改写业务数据，不宣称跨库原子快照。前两份副本与其失败演练证据保留，不作为已通过恢复的副本。

数据恢复仍保留 `intent_identity_unverified:18` 提示：演练没有证明这些投递部件的原发送身份，外部账号身份文件仍须另行提供；`businessResumeProven=false`。不因此重新提交未知意图。

## 运行中发现并修复的库锁退出

19:34 左右，旧调度器与 MY 发送进程因 `sqlite3.OperationalError: database is locked` 退出。无人工停止文件，核对原控制不变后恢复既有调度。追加修复：调度器首次打开台账遇锁在领取前等待；发送器裸库锁进入短等待并沿原意图恢复；失败初始化的 SQLite 连接立即关闭。其他数据库错误继续报错，不一律重试。

19:54 排空检查无阶段 claim、无在途、无登记常驻进程；19:57 启动新代码。此前已授权的持续任务按原开关运行，IT/UK/TH 的发送关闭状态保持。未为测试新开首发、AI 全量或其他市场能力。

## 证据与提交

运行证据在 `var/releases/audit-fixes-20261003/`：`restart-preflight.json`、`restart-drained.json`、`restart-postflight.json`、`web-build-staged.json`、`ordered-backup-verified.json`、`ordered-restore-drill.json`、`approved-backup-cleanup.json` 及测试/API 回读日志。它们是本机数据，不入 Git。

主要代码提交：`435e429` / `9b9e855`（备份与告警）、`897815f`（新市场恢复引用）、`764e04a` / `0a58fd0`（供给）、`1e3f9bd` / `aac6910`（总览）、`570fecd` / `ad33a61` / `bf8c235`（首发与 paid）、`30c2bc4`（库锁）、`da05525`（文档）、`b0984f3`（备份顺序）。生产分支已快进合入；没有推送远端。
