# BDHub-Agent 当前交接

更新时间：2026-09-23 22:14（Asia/Shanghai）。本页只记录动态快照；业务规则见[项目文档](../PROJECT.md)，实现合同见[技术文档](../TECHNICAL.md)。旧交接流水已完整保留在[历史交接](../archive/handoff/codex-takeover-history-20260923.md)。数量和进程会变化，实际执行以本机台账为准。

## 代码与工作区

- 分支 `codex/v1-runtime-alignment`，审计开始时 HEAD `09c32d6`；原有 7 个未提交的 TapLink/Profile/identity 文件保留。本轮修复与归档尚未提交、尚未部署到正在运行的 Web/worker。
- 当前整改覆盖 Kalodata 分页范围与阶段屏障、原发送/人工回复意图回查、Agent 单项失败隔离、市场窗口显示、会话竞态、vendor 更新保护及旧资料归档。代码通过不能代替真实平台回执。
- Python 离线全量 1,196 项、Web 181 项、TypeScript 检查通过；Web 在临时复制目录完成生产构建，运行服务的 `.next` 未改写。4 条 SQLite 连接告警已定位并修复，`-W default` 全量回归为 0。文档与 vendor 只读检查通过。

## 本机运行快照

- 只读进程表：四市场发送 worker、四市场收信 worker、四市场 Agent worker 均存活。Agent 持久开关四市场为 `enabled=1`；实际回复动作仍须受各自时间窗、首次验证阶段和原意图门禁约束。
- `operations-scheduler.stop` 存在，原 scheduler PID 3002 已不在进程表；最近状态记录 IT `catalog` 失败，错误 `global_catalog_not_published`。该停止请求来源本轮未确认，未擅自删除标志或重启调度器。IT 当前已发布货盘 head 与未发布 run 均保留，后续是否恢复须先核对原断点、资源槽和操作来源。
- 当前发送台账有 IT `quarantined_unknown=2`；BR、IT、MY、UK 仍分别有未执行或未结算的 `ready` delivery。它们不是发送许可，也不能重建或改判原意图。
- 四个现有 `kalodata-leads*.sqlite` 在在线备份 `var/backups/state/20260923T141451Z-before-leads-window-receipts`（33 库、1,480,654,848 字节、独立验证 valid）后，已增量建好 `leads_page_scope` 与历史回执表；`migrate-lead-receipts.py check` 四市场均 ready，平台写入 0。旧台账及原回执未删除。

## 当前处理顺序

1. 本轮离线测试、文档、迁移、vendor 和隔离构建均已通过；继续在代码审阅中检查现场无法离线验证的意图与回执，保持旧项目只读。
2. 对 `operations-scheduler.stop` 的来源、原 IT catalog run 的真实停止点及后台进程归属作只读核验后，再决定是否恢复。不要把代码修复、备份或测试通过当作恢复现有持久任务的授权。
3. 发布新 Web 或替换持久 worker 前，分别核对进程 PID/cwd、原授权、窗口、原意图及回执；新代码尚未加载的运行实例不受本轮修改影响。平台写入与业务结果另行报告。
