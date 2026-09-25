# 原卡文邀请与新来信衔接交付

2026-09-25 23:29（Asia/Shanghai）。对应[模块方案 §9.13](module-optimization-plan-20260925.md#913-收信与当前卡文邀请的衔接用户-2026-09-25-确认)。代码 `ceca684` 在独立 worktree `../BDHub-Agent-invitation-20260925` 验证后快进合入生产；用户此前已允许必要进程切换。本报告不产生新发送授权。

## 业务变化与范围

当同一原投递的卡已经确认、原文字从未提交，随后实时入站被收信落库时，只有可证明由该入站造成的关系版本变化允许原文字继续。原市场、账号、达人、CID、PID、Offer、正文和 requestRef 不变，不重新发卡；文字确认后未回答来信和未读状态保留，该达人后续主动推品仍被阻断，其他达人继续。

暂停、窗口结束、过期、人工接管、拒联、材料失效、容量不足、历史缺口和 unknown 均保持原限制。旧已取消、隔离或部分终结的投递不会因此复活；在途文字只核验。非 IT 出现明确终止条件时只取消未提交文字，保留已确认卡及 partial_delivery，不把单人终止变成市场停止。

当前继续凭证要求**摄取入站时卡已确认**。入站先落库、卡后确认的顺序未放宽，也不追溯放行旧投递。SDK 接收适配、完整 live/replay 上下文、§9.14 最早未处理排序/多问题合并及新隔离服务政策仍属后续。

## 实现和数据合同

- `scripts/lib/invitation_continuation.py` 复用 `cycle_delivery_check`，记录 `invitation_inbox_revision` 的原冻结 revision、连续 before/after revision、精确 CID/messageId 和 inboxUntil/unlocked。Inbox/Service 在原事务内写凭证，任意手工控制版本变化打断继续资格；逐写仍核对当前关系、tracking、pending 与 open case。没有平行授权或关系状态源。
- `cycle_delivery.py` 仅在原文字 begin 使用继续例外；`continuous_send.py` 和 `market_send_canary.py` 覆盖 IT/其他市场。非 IT 核对冻结 senderAccount/senderImId，旧记录仅兼容原固定通讯账号；IT 原 senderBindingHash 合同保留。适配器包装的本地许可错误，只在 ready 且完全无提交证据时恢复原 CycleError，不能把 inflight 改成未提交。
- `cycle_inbox.py` 首次 checkpoint 基线改为同 plan/CID/OEC 最新已确认卡的 started，兼容仅有已确认文字的旧记录；缺 CID 只由原已确认会话意图补齐，received/inflight 不算确认。不翻转历史消息、不重开已处理事项。查询限定关系/原投递，避免在 Inbox 写事务中扫描全部发送历史。
- 既有 `observed_messages` 按精确 CID/OEC/messageId 排除本系统邀请；同文但不同 messageId 的后台消息仍按实际外发处理。测试覆盖 `pending_rows` 和会话 unread 不被原邀请抑制。
- 真实 Agent 显式打开 `include_current_invitation`：仅补入同 CID、卡在来信前、原文字在来信后已确认且不晚于当前的同套邀请，标 `purpose=outreach_invitation`。默认 replay 仍按目标入站时间截断。新 episode/link 优先以实际已确认卡时间定位，旧 episode/link 不改写；没有改持久指南或商业场景规则。
- schema v24 `invitation_inbox_proof_index_v1` 幂等确保原核验表和 `(delivery_id,kind,checked)` 索引。生产原表已存在，实际只新增索引，没有业务行回填。

## 验证

Python 全量 1,435 项通过，含 18 项本批专测；Node 180 项通过，既有 ResourceWarning 保留。专测包含四市场正向继续、冻结内容与请求保持、重复 poll/重启幂等、入站编辑、手工暂停恢复、拒联/案件/gap/过期/unknown、异 CID、原账号、一次提交、pending/unread 保留、其他达人继续、live/replay 及已确认 CID 回退。没有 Web 代码改动，本次沿用已有构建，不重建或切换 Web。

生产库临时副本中模拟四市场“卡确认→来信→原文字确认”，待回复状态和快照不变；最大新增写事务约 15.4ms。迁移重复执行为空、原表内容不变、quick_check=ok。所有演练数据只存在临时副本，临时副本已删除，演练平台写入和模型调用均为 0。

生产安全点迁移前另保存一致备份；迁移前后 131 张非迁移业务表内容摘要一致，重复迁移 appliedNow=[]，quick_check=ok。恢复运行后检查 9,639 份旧快照、19,278 个组件 requestRef 和 191 个旧终结状态均未变；控制、授权事件、指南、Agent 设置、服务回复配置和市场开关不变，AI decision/service_reply 无新增。

## 发布与生产观察

23:21 后先 SIGTERM scheduler，等待 BR 身份阶段完成、claim 清空；再让四 sender、四 inbox、四 Agent 结束当前调用。迁移前没有未隔离的 inflight/accepted/unknown 发送组件或 ready/在途 AI 回复；未强杀在途平台请求。23:23 应用迁移、合入代码并由原 scheduler 按保存授权恢复：

| 进程 | 新 PID |
| --- | --- |
| scheduler | 44906 |
| IT / BR / MY / UK sender | 44913 / 44916 / 44919 / 44922 |
| IT / BR / MY / UK inbox | 44910 / 44914 / 44917 / 44920 |
| IT / BR / MY / UK Agent | 44911 / 44915 / 44918 / 44921 |

四市场持久 rollout 均为 pilot_complete，无 full-run 授权；运行面板可能短暂先显示 send_dispatch_active，不表示已经开启全量。MY 自动运营主链仍关闭，sender 原授权保留且等待额度；BR 有发送后等待供给，UK 正常继续发送，IT 原供给等待未放宽。收信继续扫描，UK 可短暂等待原账号互斥；scheduler 当前状态无错误。旧项目、Web 服务均未改动。

四市场 market-overview、send、conversations 共 12 个 GET 均 200。会话列表本次耗时 IT 49.57s、BR 31.91s、MY 52.43s、UK 58.34s，属于应进一步定位的性能问题；本批没有测得旧版本对照，不能判断为新回归或宣称响应性能已达标。下一批应在真实库副本分析会话聚合/上下文读取，并给出明确延迟验收。

运行恢复后的同一快照：完整邀请确认计数 BR 1,931→1,940、UK 2,399→2,412，IT 2,615、MY 2,499 不变。这是已有授权业务的真实进展，不是测试发送。新继续凭证计数仍为 0，尚未观察到本批目标交错的自然真实案例，不能将一般确认增长当作新例外实测成功。未为了验收发送真实消息、伪造入站或调用模型。

## 证据、恢复与后续

证据和两份一致备份位于 `var/releases/invitation-inbound-20260925T145214Z/`：`before.sqlite`、`quiescent.sqlite`、`release.json`、`python-tests.log`、`node-tests.log`。release 包含迁移、副本演练、进程、控制校验和 HTTP 结果。原未提交主文档内容保留，只更新本批已实施边界，不代为整体提交。

若需回退，先使相关 worker 在原调用安全点退出再切旧消费者；保留 v24 索引和新收到的消息/意图，不能回灌整份旧库。旧消费者可能重新拒绝符合本批规则的原文字，所以回退不是业务行为等价；不得为凑齐邀请重建请求。Web 无需回退。

下一批先实现 §9.14 的按达人最早未处理时间排序、合并当前未回答范围和确认时精确推进水位；同时梳理完整 live/replay 上下文，并定位本次发现的会话列表慢读。继续保持现有窗口、商业回复政策、技术隔离边界与首次全量授权要求。
