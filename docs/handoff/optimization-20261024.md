# 2026-10-24 优化发布核验

核验时间：2026-10-24 13:08（Asia/Shanghai）。本页补充[原交接](current.md)，仅代表该时刻；产品规则见 [PROJECT](../PROJECT.md)，技术合同见 [TECHNICAL](../TECHNICAL.md)，变更与离线验收见[本批报告](../implementation/project-optimization-20261024.md)。

## 已发布与已核验

- 用户确认沿用现有规则和持久授权，并明确批准安全发布。运行代码基线 `705c800`；Web Build ID `jVglZeaWtbcPUx71Imt_Q`，304 个构建文件哈希一致。旧 Web 构建保留供回退。
- scheduler、五市场 sender、七市场 IM owner、七市场 AI 回复及 BR 一发共 21 个原常驻角色已安全切换，全部载入本批运行代码；源码无未提交运行改动。两次切换均在无在途提交、无阶段 claim 时完成。
- 七张原控制/授权表、一发控制、原 116,796 个 requestRef 映射及唯一性通过核对；数据库 `quick_check=ok`。原 `config/jobs.json` 和 `docs/handoff/current.md` 的未提交内容字节保持。原暂停状态保持；未开启新发送或审批权限。
- 前夜 FR/TH 两条未结投递按原截止与预算进入技术隔离，保留原未知及证据，未补发、未改判成功。其他原技术隔离和人工案件保留。
- 首页、二发、一发、会话、总控制台及四个主要只读 API 均有 200 回读；一发实测约 2.7 秒、概览约 14 秒且六分区可读。最终巡检查询继续优化后单次线上约 12 秒返回 200；并发负载下的持续稳定性不能由单次回读保证。
- 前端已做四页桌面、两页 390px 窄屏及生产一发/会话验收，无横向溢出或已记录的前端 JavaScript error；失败与旧数据提示保持。Python 全量 2,084、Web 225、build、typecheck、文档检查通过；最后布尔回执修订另通过 95 项相邻合同。

## 尚未完成的运行验收

七市场 SDK 尚未恢复 ready：各市场最后成功时间集中在 2026-10-23 16:29–16:32，早于本次发布；当前存在 `sdk_not_ready`、`sdk_http_reader_stopped`、`im_transport_error`。新增诊断只记录异常类，已观察到 `ReadTimeout`/`SSLError`，没有据此判登录失效。原账号的一次 cmd203 只读核验成功，部分市场 HTTP 收信也能完成，但不能宣称 SDK 推送或全量会话覆盖正常。

项目共享出网当前经过 Main Proxy 的 US-DartNode-Opti-02。只读线路测试中 US-Frontier-Opti-15 可达且延迟较低，尚未修改共享代理；切换影响同机其他服务，已向用户单独请求确认。收信通道未全部验收，因此不能把“21 个进程已载入新代码”当成全面自主运行已完成。

样品平台审批验证、原人工事项及其他未完成产品合同继续按主文档处理。本次没有进行新的测试发送或扩大商业决策权限。

## 本机证据与回退

发布证据：`var/releases/project-optimization-20261024/`，含原控制摘要、drained、完整性/原请求核验、最终运行比对、构建清单、HTTP 回读及 `previous-web-build/`。页面截图及索引：独立 worktree 的 `outputs/frontend-audit-20261024/`；测试与只读性能证据：`outputs/optimization-20261024/`。回退代码/构建时保留新消息、原请求、授权及技术隔离证据，不恢复旧数据库覆盖业务进展。
