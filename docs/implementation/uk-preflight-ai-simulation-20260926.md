# UK 发送预检收口与真实来信 AI 模拟

2026-09-26 00:37（Asia/Shanghai）。用户要求先处理 UK `unknown_message_needs_review`，之后查看真实达人来信的 AI 模拟效果；本次没有推进其他 AI 规则改造、修改指南或开启全量。

## UK 原因、修复与加载

原投递 `delivery-b3c37e75c6aa86810b5b9a419f60747a` 的会话创建已确认，但卡和文字都为 ready，started/receipt/confirmation 均空。历史会话存在一条 otherOrUnknown 消息，导致 `_continuous_history_eligible` 拒绝推品。该错误表示历史消息角色无法归类，不是本次卡文送达未知。

同一原账号 acc11、冻结 IM 身份与 CID 只读回查：共 1 条历史消息、hasMore=false、正文为空；sender ID 与原账号相符，但未满足当前身份角色分类合同，仍计 otherOrUnknown=1。不能仅凭 sender ID 把它改判为正常我方外发。读取没有发送权限，身份文件摘要未变。私密回查证据在 `var/research/uk-preflight-ai-simulation-20260926/uk-readback.private.json`。

真正堵队列的是非 IT `_preflight_or_close` 只处理“卡已确认、文字未发”的终止条件，没有处理整套卡文均未开始的 ready。worker 不断重取原投递、再次预检失败；过期清理又只接受未创建/ready 会话意图，漏掉“会话已确认而消息未提交”，所以 30 分钟到期也不能释放队头。

`93cb471` 从独立 worktree `../BDHub-Agent-uk-review-20260926` 验证后合入：

- 对既有 PREFLIGHT_TERMINAL 的单达人排除，只取消已证明完全未提交的原卡文；历史消息仍保留未知分类，原会话意图/回执不改写。账号/共享运行故障仍向上报错，不批量取消。
- 过期清理补上已确认会话、卡文从未提交的状态。cancel_unsubmitted 增加会话状态保护：inflight/received、组件在途/unknown 均不能改取消；卡已确认仍保留原 partial_delivery 规则。
- 原 snapshot、CID、requestRef 均保留，不重发未知意图，不把未知改成功。没有 schema 迁移。

Python 全量 1,458 项通过，含 6 项新增预检/过期/原会话保持/未知拒绝/共享错误测试；既有 ResourceWarning 保留。真实生产备份副本演练取消原未发组件、重复调用幂等，原会话/请求不变，`_unsettled` 不再返回原队头，quick_check=ok，零平台写入。

主库一致备份保存在 `var/releases/uk-preflight-20260926/before.sqlite`。原 scheduler `52659` 收到 SIGTERM 后不再监督拉起，当前 UK 身份阶段继续自然完成；在无在途发送、四市场均窗外等待时重载四 sender，原身份阶段不读本次改动的发送路径。合入后使用原 market executor 锁、`cancel_unsubmitted` 对已回查的原投递做本地收口：delivery/card/text 变 cancelled，会话仍 confirmed；没有平台调用。身份阶段退出后恢复 scheduler `62510`。

四 sender 为 IT `61715`、BR `61716`、MY `61717`、UK `61719`；四 inbox 和四 Agent 未重启，Web 未改。UK 现为 waiting_window、无错误；原 sender 队头已释放，当前窗口外没有主动实发验收，不能把等待窗口报成已经向其他达人送达。

控制、授权事件、指南与备份一致；生产 service_reply/agent_reply_decision_v2 没有新增。四市场仍 pilot_complete_waiting_resume。完整加载、PID、备份和校验见同目录 release.json；旧项目未修改。

## 真实来信模拟方法

四市场各取 4 组当前真实未答消息，合计 16 组，覆盖样品、佣金、广告、联系方式、促销条件、拒绝继续推广、纯感谢和多条消息。是按场景挑选的样本，不用于估算总体准确率。

在临时主库副本中补齐本地消息投影，用生产同一 `production_context(live=True)`、生效指南 revision 2、`generate(mode=simulation)`、DeepSeek deepseek-flash（thinking 关闭）与校验器。没有调用 apply_production、reply transport 或任何达人发送接口；完整实际请求/响应、usage 和费用回执保存到本机私密研究目录。临时库结束后删除，生产待办/决策/回复台账未写入。

每组只调用一次，不改提示词、不挑选重试后的好答案。16 次真实模型调用，14 组结构通过、2 组拦截；共 49,686 token，项目现行计价口径估算 ¥0.0431298，不作为实际账单。被拦截仍保留模型原话，不能冒充可发送回复。

完整用户对照在本机 `outputs/ai-reply-review-20260926/report.md`，包含所有达人原话、AI 回复原文、中文对照与人工点评；账号名/联系方式在交付页脱敏。`index.html` 另保留筛选版，但浏览器安全策略不允许直接 file URL 预览，因此交付可直接阅读的 Markdown 版本，没有绕过浏览器策略。

## 当前效果与剩余问题

本轮人工复核为 5 组可用、4 组可改进、5 组需修改、2 组被结构/证据校验拦截。此分类是当前样本的定性审阅，不等同于模型自评分或总体通过率。

- 可用代表：原链接佣金更高时建议沿用原链接；纯感谢不回复；有真实卡且明确愿意拍摄时简短确认；同轮合作确认和普通 Boost 可以合并。
- 明显业务偏差：售后损坏错套无样品入口规则；对已申请未获的达人重复建议申请；给出没有订单依据的结论；追加商家未来反馈承诺。
- 执行状态偏差：正文要求达人解释附件，route 却仍为 reply/waitFor=none，存在把尚待澄清的范围当作普通已答的风险。
- 两条被拦截：BR-03 同时给 route=reply 与非空 handoffReason；MY-03 引用了输入 showcaseEvidence 中的加橱窗通知 ID，校验器只接受 messages 数组的 ID，因此拒绝（第二轮复查更正；此前“不存在的 ID”表述不准确）。原文再自然也不能算执行成功。
- 可改进：样品数量不明确时不要猜“两份”；拒绝本商品后无需展开商品网站；交接不要额外保证具体外部渠道回复；手头无商品时优先回答样品问题，不急于追加推品提醒。

当前不建议直接开启全量。用户先看真实回复，后续修复优先围绕场景边界、正文与路由一致、证据 ID 约束及对已尝试步骤的遵循；这次保留现状基线，没有暗中修改指南后重新生成“更好版本”。既有商业政策继续有效，不据本轮模型错误重新扩大系统权限。

## 用户复核与第二轮（2026-09-26 00:59）

用户看完首轮 16 组后认为回复质量较高，不认可助理此前的质量评级。上述等级仅保留为当时的助理意见，不代表用户验收结论；本次未据此修改指南或权限。第二轮固定随机抽取四市场各 4 位新达人，与首轮达人和原消息均无重叠；使用同一 revision 2 指南和 DeepSeek flash，每组一次，生成后不挑选。16 次模型调用，14 组系统校验通过、2 组被拦，原文全部展示，没有主观质量等级。

第二轮对照：`outputs/ai-reply-review-round2-20260926/report.md`；私密证据：`var/research/ai-real-message-round2-20260926/`。合计 48,106 token，估算 ¥0.0330064；生产授权、指南、decision/service_reply 前后摘要一致，真实发送 0，临时模拟库已删除。MY-R2-02 同样引用输入中的通知 ID 而被校验器拒绝；MY-R2-04 的 route=reply / waitFor=clarification 不符合当前结构合同。只如实注明技术结果，不把它们当作文字质量评分。
