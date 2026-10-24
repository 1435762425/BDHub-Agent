# 2026-10-24 第二轮自主运行与体验核验

核验时间：2026-10-24 16:15（Asia/Shanghai）。本页补充[当前交接](current.md)和[上一轮核验](optimization-20261024.md)，不覆盖原业务开关、授权或人工暂停。实现与测量见[本批报告](../implementation/autonomous-design-20261024.md)，尚未实施的结构候选见[架构演进](../architecture/autonomous-runtime-evolution-20261024.md)。

## 已发布与核验

- 代码基线 `b6850b6`；Python 常驻载入 `49e4b79`，两者脚本运行树一致。最终 Web Build `7HfE7Ulaqd6YAMQGiYlgZ`，304 个构建文件 hash 一致。Web 最后的展示修订单独切换，没有再次重启 Python。
- 原常驻全部安全 drain，核验无在途、无阶段 claim、无旧 resident 后合入。恢复的原 21 个角色均为当前运行代码，七市场 SDK ready；Web 与 scheduler-watch 均已加载。
- Python 全量 2,126、Web 全量 251、build、随后 typecheck 通过。独立审查覆盖只读核验不发送 ready/不调用模型/不删除 stop/不重置预算，以及轮询与写后新读取的独立代次。最终首页/人工会话的九项浏览器检查、覆盖提示的八项检查全部通过。
- 发布后的运行 postflight 十一项全通过，`quick_check=ok`；原七表控制、一发控制和 116,796 个 requestRef 的映射与唯一性保持，原 `config/jobs.json`、`docs/handoff/current.md` 未提交内容字节不变。16:15 汇总再次核对代码、会话、构建、原控制和请求。

## 结果

- 补齐生产旧身份库中缺失的 recent 索引：先做 3.2 GB online backup、SHA256 和完整 quick_check，再做幂等增量 DDL；四张业务表数量不变，目标表/索引 integrity_check 为 ok。同一 BR 概览计数保持，原生产接口的一次回读约 0.968 秒。
- 完整人工会话详情在上线后并发回读为 0.134 秒，原卡住的会话现可读到时间线、人工原因与处理入口；没有点击业务写入动作。首页 36 位人工达人与会话队列一致，积压与维护提醒分开计数。发送/回复显示原配置的继续条件，缺少控制或陈旧状态不猜恢复时间。
- 最终四 API 并发均 200：人工详情 0.134、达人概览 3.969、首页 5.489、六分区总览 5.865 秒；六分区均 available。单次与并发、冷/热缓存不同，不能当作固定响应承诺。
- BR 原任务、原 catalog 和匹配 generation 均连续；新 head `first-generation-13ea9545ebf64bb9bc2d63c13b21d503` 已完整扫描 586,436 人并发布 212,830 条匹配，旧 generation 及原候选保留。未重建任务或重置原游标。材料准备仍沿同一 `first-auto-e6420d5db87247508251700363e54032`，当前有缺口时按原证据退避，不人为标记 ready；已有有效方案继续按原规则使用。
- 原 AI 回复即使关闭新工作也可有限核验已提交原意图；IT sender 启动和心跳已统一启动互斥与原子发布。该类故障有 fixture/合同证据，当前没有可用于宣称“真实未知已恢复成功”的未隔离 reply 样本。

## 仍保留的边界

全量会话检查较旧的事实保留。样本 BR 5,149 会话、约 50 次/分钟核验，不能满足全盘五分钟门槛；页面保留原最旧约 138 分钟、数量与警示，说明新来信优先、全量按队列推进。检查时未完成 SDK 信号为零，七市场 gap/backfilling 为零，旧 checkpoint 确实前进；这些不等于证明全量消息已无缺失。MY/TH 原长时间旧覆盖仍需持续消化。

SDK 每约 600 秒整体轮换和约 90 秒回调缺席仍是已记录的结构优化空间。浏览器/HTTP 认证解耦尚未实施，须跨多次轮换验证；FIFO 失主的未来恢复仍须关联原意图，不能仅凭 PID 清队列，本批没有修改 vendor 或清空队列。

IT/UK 原供给/二发关闭、各市场窗口、首发授权、技术隔离及人工事项均保持。样品审批平台能力仍未验收，原申请不会被为了验收自动放行。本批没有发送测试消息、审批样品、重发 unknown 或扩大商业条件。离机备份 U 盘仍需接入，不能以本机新备份代替离机副本。

## 证据与回退

生产 `var/releases/autonomous-design-20261024/`：

- `identity-index/backup.json`、`applied.json`、`verified.json`：身份库备份、增量索引与前后对账。
- `preflight.json`、`pre-signal.json`、`drained.json`、`runtime-postflight.json`：原控制/请求、收口与运行核验。
- `web-manifest.json`、`web-published.json`：最终构建与切换；上一 Web 清单/发布记录另存 before-coverage-label 文件。
- `backend-http.json`、`final-http.json`、`delivery-verification.json`：API 和最终汇总。首次汇总误用了 Row row_factory 与按 tuple 设计的摘要函数，导致伪控制差异；已用默认 tuple 连接重新核对，原控制完全一致，原误差证据保留为 row-factory-artifact，不修改业务数据来通过检查。
- `validation/`：本批测试、性能、SDK 方案与体验审计截图，含完整路径和最后覆盖提示检查。

代码有逻辑提交，两个 previous-web-build 目录保留。回退不恢复旧库覆盖新消息、授权、原意图或正常经营进展。
