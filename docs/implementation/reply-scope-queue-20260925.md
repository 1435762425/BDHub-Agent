# 集中回复范围与队列交付

2026-09-25 23:55（Asia/Shanghai）。对应[模块方案 §9.14](module-optimization-plan-20260925.md#914-集中回复队列排序与多条消息合并用户-2026-09-25-确认)，代码 `670d3e8` 在独立 worktree `../BDHub-Agent-reply-queue-20260925` 验证后快进合入。本次沿用已有窗口、商业政策及持久授权，没有开启四市场 AI 全量。

## 结果与实现

当前可处理达人按最早一条未处理来信的时间排序，同一达人后续补充不刷新年龄。同一轮全部未答消息进入一份模型上下文，最后一句感谢不会使前面的问题在输入中消失。生成期间来信变化拒绝应用旧决策；准备后、提交前的变化拒绝原草稿；已提交正文保持冻结，确认只处理它覆盖的消息版本，新问题继续排队。

- `scripts/lib/reply_scope.py` 读取原 inbox_event、content head、service_cursor 和精确外发证据。每份 decision.input_json 的 replyScope 冻结 eventRowid、CID、messageId、contentHash；apply_production 与 AutoReplies.begin 同时核对未处理范围、pending/control revision、人工控制和 tracking。没有创建平行消息或授权库。
- 确认/no_reply 在原事务内追加 `service_message_resolution`，关联原 reply/decision；service_cursor 只前进到连续已处理前缀。发送途中新增来信、较早消息被编辑时，当前版本保留，其他已处理版本不重复入队。后来的人工控制不被清除；商业 handoff 的确认只更新案件 ack，不表示人工问题已经解决。旧无 replyScope 的意图沿原合同兼容，未知不重建。
- `run-agent-replies.py` 在排序前排除等待资料、人工接管、缺正文、gap、多 CID 不明确及技术退避；同范围/指南的有界失败不反复占队头，新未答范围可以再判断。due_at 仍用于退避，队列年龄由原消息决定。领取最多 20 位候选只是一次处理片段，生成第一份可发送回复后即进入原执行路径。
- 真实 worker 使用 `production_context(...,live=True)` 的当前本地对话，`unansweredMessageIds` 明确本轮诉求；全部当前问题不受最近 24 条历史裁剪限制。原邀请仍标记 outreach_invitation，不能当作客服回答。默认目标时刻模式保留；完整历史指南/关系/事实回放和 24 条以前的长期提醒记忆尚未完成。
- 先用 provider 的实际序列化和 24,000 字节合同检查输入，优先移除可选旧历史，必需问题不裁。仍过长写 input_blocked/modelCalls=0，保留未回答范围，同范围/指南退出当前可运行队列，页面显示“上下文过长，待答问题已保留”。未实现超长诉求自动分段，不声称所有对话均可自动回答。新来信/指南变化重新评估，不能把本地失败记成 no_reply。
- 非 IT transport 对适配器包装的本地许可错误，仅在 ready、started/receipt/proof 全空时恢复原拒绝原因并取消过期草稿；已 inflight 的仍只核验原请求。IT 既有本地收口保持。
- 会话列表及未读积压年龄按最早未处理来信展示。补充查询索引解决外发/橱窗记录反复扫描的慢读，没有修改 React 或 API 返回结构。

## 迁移与验证

schema v25 `reply_message_scope_and_read_indexes_v1` 新增精确处理证据表，并为已有 inbox_event、cycle_delivery、service_reply 建立匹配实际查询的索引。只从已有 service_cursor 前缀复制旧已处理消息版本，`reference=legacy_service_cursor`，handled_at=NULL 表示原处理时间未知；不猜测旧回复范围或修改旧消息。空库中尚未初始化的旧模块不伪造业务表。

Python 全量 **1,452 项通过**；最后一次只读积压年龄调整后，相关会话测试 17 项再次通过。Node **180 项通过**，既有 ResourceWarning 保留。无 Web 代码改动，沿用现有构建、未重建或重载 Web。

新增 16 项消息范围测试，覆盖四市场多问题＋感谢、持续补充的公平排序、生成/准备/提交后新入站、旧消息编辑、后台已回复范围、人工控制、no_reply 幂等、30 条未答不截断、超预算零模型调用、重启、gap、旧 cursor 迁移两次、失败达人不占队头。另补非 IT 包装错误的确定未提交收口测试。测试使用假模型和本地回执，不证明真实模型的语义准确率。

生产数据库临时副本迁移约 59ms，旧消息、pending、cursor、关系、reply、decision、delivery 内容摘要不变；复制 40 条既有已处理版本，重复迁移为空，quick_check=ok。四市场列表约 0.58–1.05 秒。演练零平台写入、零模型调用，临时数据库已删除。

上线前只读 profiler 测得 IT 会话列表 34.405 秒：约 39,363 次 SQLite execute 累计 27.768 秒，主要在逐达人查外发与橱窗；不是模型或平台等待。索引上线后实际 HTTP 结果：

| 市场 | conversations GET | market-overview / send |
| --- | --- | --- |
| IT | 200，1.151 秒 | 均 200 |
| BR | 200，0.793 秒 | 均 200 |
| MY | 200，1.122 秒 | 均 200 |
| UK | 200，1.037 秒 | 均 200 |

这些为单次运行观测，未作为固定性能保证；前后统计时间不同，正常收信产生的新行仍保留。当前最慢会话 GET 从上一批 32–58 秒降至本次约 1.2 秒。

## 发布与运行边界

23:49 前保存主库一致备份并 quick_check。SIGTERM 原 scheduler `44906`，等待其正在执行的 UK 身份阶段自然结束、claim 释放；没有强杀身份子进程。确认没有 ready/inflight/accepted/unknown AI 回复后，四 Agent 在当前调用安全点退出。生产迁移约 56ms，重复 appliedNow=[]；随后合入并由原持久授权启动 scheduler `52659`，四 Agent 为 IT `52662`、BR `52694`、MY `52704`、UK `52710`。

四个收信 worker 与原运行中的 IT/BR/MY sender 保持进程；Web、旧项目未改。UK sender 在本次合入前已报 `unknown_message_needs_review`、停于 attention；原监督器之后曾换 PID 尝试，仍保留同一阻断（观察 PID 52996）。本批未绕过该门禁，也不能宣称所有市场发送都健康。后续须按原账号/消息证据独立诊断，不能靠重发或改成功消除。

上线后控制、授权事件、指南、Agent 设置、市场开关与备份一致，旧冻结快照/requestRef 未变；Agent decision/service_reply 无新增，四市场持久 rollout 均为 pilot_complete，状态为 pilot_complete_waiting_resume。MY 主链仍关闭，sender 按原授权等额度。收信继续扫描，scheduler 当前无错误。

只读读取四市场各前 20 位候选，耗时约 0.03–0.09 秒；真实 BR 队首上下文包含 3 条未答消息及 3 个版本，其他三市场样本各 1 条，均为 live 模式。这只验证真实数据可编译，不调用模型、不准备或发送回复。未为验收变更 AI 授权；新合并回复的实际送达和语义质量尚未经过生产试运行验证。

## 证据、回退及下一步

证据位于 `var/releases/reply-queue-20260925/`：before.sqlite、rehearsal.json、release.json、python-tests.log、node-tests.log、rehearsal.py。主文档已同步本批边界，原未提交内容继续保留，不代为整体提交。

回退必须保留新增消息和精确处理证据，不能恢复整份旧数据库。旧消费者不认识精确处理版本，在较早消息被编辑后可能重复选择已答的后续消息；需保留新范围门禁或停止自动回复后再切换旧消费者，不能将索引兼容等同于业务行为兼容。Web 无构建回退需求。

下一批优先 §9.17–9.19：完善模型失败分型/通道退避与回执，AI 回复 unknown 有限原意图核验后隔离、二发技术隔离后的新入站服务资格；继续补齐长期业务记忆和严格历史回放。UK 当前 unknown_message_needs_review 另按原始证据定位，不重新讨论已确认的商业回复政策。
