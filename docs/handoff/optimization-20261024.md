# 2026-10-24 优化发布核验

最近完成运行核验：2026-10-24 14:58（Asia/Shanghai）。本页补充[原交接](current.md)，运行细节以带时间的本机证据为准；产品规则见 [PROJECT](../PROJECT.md)，技术合同见 [TECHNICAL](../TECHNICAL.md)，变更和离线验收见[本批报告](../implementation/project-optimization-20261024.md)。

## 发布与保留状态

- 用户确认沿用现有规则和持久授权，并明确批准安全发布及必要技术修复。代码基线为 `48b512f`，Python 常驻最后载入 `9b538cd`、运行树与基线一致；Web Build ID `44v0sOyFiq3O1UVZoylx3`，304 个构建文件哈希一致。旧构建保留供回退；最后匹配数量展示修订只切换 Web，没有再次重启 Python 常驻。
- scheduler、五市场 sender、七市场 IM owner、七市场 AI 回复及 BR 一发共 21 个原常驻角色均已载入本批代码。Web 与 scheduler-watch 的 LaunchAgent 均已恢复。涉及 Python 常驻的切换先等待原工作收口，drained 核验无在途、无阶段 claim、无遗留旧常驻。
- 14:53 最终运行核验 13 项全部通过：七张原控制/授权表、一发控制、原 116,796 个 requestRef 的映射和唯一性保持，完整 `quick_check=ok`，21 角色为当前代码，七市场 SDK ready 且 HTTP 持续处理、当轮连续错误均为 0，构建哈希与 LaunchAgent 正常。原 `config/jobs.json` 和 `docs/handoff/current.md` 的未提交内容字节不变；原暂停状态保持，没有新增发送或审批权限。
- 前夜 FR/TH 两条原投递按原截止和预算进入技术隔离，保留未知及证据，未补发、未改判成功。其他原技术隔离和人工事项保留。

## 共享出口与收信

共享代理是本机程序共同使用的网络出口。七市场原 SDK 最后成功时间集中在 2026-10-23 16:29–16:32，早于本批发布。经用户授权，10-24 13:19:25 将 Main Proxy 从 US-DartNode-Opti-02 切到 US-Frontier-Opti-15，并通过 Clash GUI 保存到当前 profile；未清空整机连接或修改其他代理组。

七市场均恢复 `ready`、`sdkReady=true`，通过完整 600 秒自然续期后再次 ready，HTTP 持续处理；14:34 运行快照中七市场仍 ready。切换后最初 49 秒有 4 条旧周期收尾错误，此后该轮约 12 分钟没有新增 session_error；IT 观察到 3 个真实 SDK 事件信号。以上证明连接恢复，不把 SDK 信号解释成新达人来信或合作结果。没有修改 SDK 协议逻辑。

## 自主推进与页面验收

原一发准备任务 `first-auto-45d551b338f94c0a82044004f30bd962` 已按材料证据部分收口，reason 为 `first_outreach_materials_waiting_evidence`：10 个缺口项、9 个当前有效方案、actionable=0，unknown=0。缺口包括 4 个佣金未提供、1 个零创建次数的 superseded、5 个非当前绑定；历史台账的 14 ready 不等于 14 个有效方案。未改变资格、报价、意图或授权。

原授权随后自动推进 `first-auto-e6420d5db87247508251700363e54032`，继续同一 `catalog-d35fc34…` 断点：14:08 为 Campaign 206/555、225 页、4,838 件商品，14:34 为 450/555、495 页、11,456 件商品，14:52 已完成 555/555、608 页、14,257 件商品。同一任务已进入 stage 3 的匹配准备，error 为空；startedAt、控制摘要保持，平台写入 0，身份文件未变。

新货盘改变了匹配指纹，原完整 head 的 212,830 条候选全部保留，新匹配 generation 已扫描 40,000 人、保留 12,302 条候选并继续分片推进。新完整版本发布前不把旧 head 当当前匹配使用；此时 API 的 matchedCreators=0 表示当前版本尚未就绪，不能解释成真实零匹配。匹配展示应按既有 prepare_required/catalog_changed 原因显示待更新，完整结果的真实 0 仍保留。

首页、一发、样品、会话、二发和总览已完成精简。首页以实时 worker/control 状态展示 Agent，历史失败折叠到 lastRun；材料无进展时退避，不反复空转。最终 Chrome 首页与达人页均完整加载，无横向溢出和 JavaScript error，保留 unknown、真实失败、缺库及旧数据提示。

达人大库查询已移出 Web 主线程，经固定 Node CLI 复用原只读 Store/SQL。原并发样本中静态请求和一发 HTML 等待约 12 秒；隔离后冷启动压力样本分别为 0.225、0.035 秒。七 SDK ready 后相同五请求的暖态对照全部成功：静态请求 0.041 秒、一发 HTML 0.016 秒、ops CLI 10.883 秒、ops API 10.720 秒、达人概览 12.477 秒。静态请求为缺失 favicon 的正常 404，其余 HTTP 为 200；这证明主线程响应恢复，不声称 SQL 本身变快。

14:33 正常回读中首页、一发和达人概览均成功，巡检另出现一次 30 秒超时。独占分段读取为 10.245 秒，human_queue 构建全列表占 6.521 秒、SQL 共 56,075 次；受控三读取重叠均成功（ops 7.283、首页 4.041、达人概览 9.598 秒），未稳定复现该次超时。最小修复让巡检与列表共用人工/技术挂起分类，只读取计数事实；真实库同一只读事务内七市场计数逐项相同，统计从 7.784 秒降至 0.836 秒，完整巡检 gather 为 2.024 秒。没有延长 30 秒超时或隐藏故障；这证明明确存在的读取浪费已消除，不断言它是偶发超时的唯一原因。

最后代码上线后，巡检、首页、一发和达人概览只读回读全部 200，分别为 3.478、3.912、2.377、10.877 秒。巡检返回 21 个载入当前代码的角色；告警仍包含原未读、人工事项、历史隔离和离机备份过期，没有为验收清空这些记录。

最后 Web 展示修订后，14:57 四请求并发回读全部通过：一发 HTML 0.042 秒、巡检 1.167 秒、首页 4.228 秒、达人概览 13.156 秒，均为 200；Chrome 一发页面确认匹配显示待更新、原等待提示保留，无横向溢出或 JavaScript error。14:58 汇总再次确认 21 角色当前代码、七市场 ready、最终 Web 304 文件哈希一致及原两个未提交文件不变。

## 验证边界与待办

Python 全量 2,109 项、Web 全量 235 项、build、随后 typecheck 通过。最后巡检计数修复另经定向 81 项及七市场真实只读对账通过；匹配数量展示另经 15 项一发合同通过。

IT/UK 供给与二发关闭、各市场发送/回复窗口、原 ready 草稿及历史隔离均按原选择保持。一发控制原 `approvals_enabled=1` 保持，但平台审批能力仍未验收，5 条样品 pending 也不满足已成功推荐且当前仍合格的自动审批范围，未产生自动审批写入。本批未发送测试消息、代审样品或清空人工案件。软件恢复不等于业务队列清空，未完成产品合同仍见主文档。

离机备份 U 盘未连接，离机副本约 29 天未更新，需要接入原备份盘；未伪造备份或修改时效标记。

## 本机证据与回退

- 首批发布：`var/releases/project-optimization-20261024/`，含原控制摘要、原请求、drained 与完整性验证。
- 网络及最终发布：`var/releases/network-recovery-20261024/`；`persistence.json` 为出口持久化回执，`final-postflight.json` 为 `bee73ce` 完整 8 项通过，`first-outreach-final.json` 为 14:34 原断点推进证据。
- 主线程隔离：上述目录的 `identity-isolation/`，含 preflight、drained、构建清单、冷/暖并发样本及 `runtime-final.json`；正常回读失败样本原样保留在 `normal-load-api.json`。
- 巡检计数：上述目录的 `operator-counts/`，`send-reliability-review.json` 含独立分段定位、受控重叠、七市场同事务对账和耗时证据；`final-postflight.json` 为最后 Python 发布的 13 项通过，`final-http.json` 为四个 API 回读，`first-after-release.json` 为货盘完成及原匹配换代证据。
- 最终交付：同目录 `delivery-verification.json` 汇总 14:58 结果，`final-concurrency.json` 为最后 Web 的并发回读，`final-web-manifest.json` / `final-web-publish.json` 为最终构建和安全切换；对应 UI 与测试证据已复制到 release 的 `validation/`。
- 页面与测试：开发 worktree 的 `outputs/frontend-audit-20261024/`、`outputs/optimization-20261024/`、`outputs/network-recovery-20261024/`。最终 UI 为 `deployed-ui-final.json` 及两张 `deployed-*-final.png`，已复制到生产 release 的 `validation/`。

各轮 `previous-web-build/` 均保留。回退代码/构建时保留新消息、原请求、授权及技术隔离证据，不恢复旧数据库覆盖业务进展。
