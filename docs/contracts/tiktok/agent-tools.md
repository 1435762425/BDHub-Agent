# Agent工具合同与接入顺序

这是待实现/迁入的工具设计，名称不是已存在的可调用工具。旧assist目前只有creator_context、sample_state、promotable_offers等有限只读方法；新草稿生成器也不是完整关系Agent。官方网页、底层执行器、Agent工具三个层次独立验收。

## 统一调用边界

宿主固定institutionRef、market、creatorRef/OEC、relationshipRevision、inboxWatermark、policyVersion；模型不能换机构、换达人或自填confirmed=true。模型参数仅为当前任务内的productRef/offerRef/question/dateRange等允许引用，不暴露原始SQL/HTTP/shell/账号配置。

只读结果结构建议：

```json
{
  "schema": "bdhub.tool-evidence.v1",
  "tool": "read_offer",
  "status": "observed",
  "subjectRef": "bound-offer-reference",
  "observedAt": "2026-09-13T00:00:00Z",
  "sourceAsOf": null,
  "coverage": {"kind": "exact_subject", "complete": true},
  "facts": {"creatorCommissionPercent": "12.00"},
  "evidenceRefs": ["immutable-evidence-reference"],
  "missingFields": [],
  "allowedNextActions": ["explain_creator_commission"]
}
```

上例是合成合同示例，不是当前报价。status可为observed/missing/stale/unavailable/conflict/partial；权限不足与商品无货不得都写missing。evidenceRefs指向本方不可变观测，模型只取必要字段。内部总佣金、机构差额、联系方式、认证与设备信息默认不输出给回复模型。

## 逐工具映射

| 工具建议 | 输入与返回 | 真实底座/本轮证据 | 允许用途与失败处理 | 接入顺序 |
|---|---|---|---|---|
| read_relationship | 宿主固定关系；返回回复/积极/拒绝/人工控制与来源 | 旧IM/状态库；MX合作类型/has_permission新读取已验；新统一关系库未接 | 判断当前服务，不自动解除拒联；版本变化取消旧提案 | P0 |
| read_messages | 关系内游标/必要数量；原话、发送方、时间、附件引用 | 旧IM库、203/608/301；此前MX/IT读取已验 | 理解“上次那款”；查不到历史不编造；分页覆盖明确 | P0 |
| resolve_product_reference | 当前卡片/消息引用、候选商品；精确匹配或歧义 | PID卡绑定和本方发送记录 | 多候选则向达人澄清，绝不相似标题直接绑PID | P0 |
| read_offer | 当前offerRef；名称短名、当前达人佣金/期限/库存/状态 | 活动/已选/成员；本轮三市场货盘、IT成员读取 | 解释当前可给条件；内部佣金不进回复上下文；字段缺失不承诺 | P0 |
| read_card_binding | offerRef；PID/list/wire Campaign/来源Campaign和核验时间 | im/product_list/list＋members；IT既有证据及本轮复读成员 | 证明卡与方案一致；缺卡只返回缺口，不能暗建链接 | P0 |
| read_sample_status | 当前关系+PID；申请/审批方/状态/覆盖范围 | sample/records/list；本轮MX已发货单页 | 告知已知事实；无记录不等于从未申请；IT/BR未验不开放 | P0 |
| read_seller_contact_route | 当前商品/样品ref；角色已核验的商家联系路径 | 本轮MX sample product_info.seller_contact_info实际存在 | 用完/损坏引导商家；联系信息角色不明返回待核对；不能默认公开私人资料 | P0 |
| read_adoption_evidence | 关系、PID和日期；IM通知与本机构TAP证据分列 | IM此前10通知；TAP旧合同；排除样品橱窗作为二发采用依据 | IM通知可记积极；TAP核对本机构相关PID；不借样品记录判断二发链接采用 | P0 |
| read_creator_profile | 绑定OEC；具名指标、窗口、类目与观察时间 | 已有Find/Profile；此前IT实测，当前默认策略有文档漂移 | 排序/理解已有事实；不以旧指标假装当前或缺值淘汰 | P1 |
| read_content_evidence | 当前关系+PID+窗口；视频/直播标识及归属 | 样品is_posted/计数可用；TAP内容联合合同未接 | 计数不当video_id；无完整覆盖不能答“你没发” | P1，先补合同 |
| read_tap_metrics | 范围/窗口；机构归属GMV、订单和报告时点 | 旧report导出/解析/日事实 | 回答有证据表现；不把关联GMV当因果增量；新报表同步独立任务 | P1 |
| read_account_capacity | 宿主范围；可用能力/维护/本地额度估计与证据 | readiness/角色/租约；IM剩余额度接口未找到 | 用于程序调度，通常不对回复模型开放；未知不填无限 | 程序P0 |
| request_profile_refresh | 固定OEC引用；持久作业号 | 新身份刷新已实现 | 内部调度命令，与纯查询分开；同人刷新归并，失败不丢身份 | 程序P0 |
| request_catalog_refresh | 已配置机构市场来源；作业号/版本 | 旧两来源扫描/本轮只读分页 | 每日任务，完整版本发布；不由模型无限刷新 | 程序P0 |
| ensure_commission_link | 精确方案和政策ref；准备结果/绑定/unknown | 旧两路线执行；新自动供给未接 | 后台允许按规则自动准备；R04/R08对话仍交人，不由模型改范围 | 程序P0，写验收另做 |
| propose_reply | 冻结文本、证据、当前事项ref；意图号 | 新二发组件账本，回复通道未接 | 代码复核身份/水位/人工/拒绝/额度；unknown只核验 | P0，真实回复待验 |
| request_human | 场景/事实/所需决定；持久事项号 | 当前有局部演示，统一真实待办未接 | R04/R08/R10；先存待办与确认消息意图，再对外确认；24h内部提醒 | P0 |
| record_relationship_signal | 允许标签+消息证据；新关系版本 | 新局部状态，统一信号层未接 | 区分达人陈述/平台通知/模型推断；不得模型写“已送达/已退款” | P0 |

## 执行器不直接等于对话工具

加入Campaign、选入商品、建链/清理属于货盘后台的持久任务；发卡/文字属于发送执行器；样品审批、MCN邀请、退款、补寄、广告投放并未因为接口在旧系统存在就被赋予Agent权限。用户当前回复规则优先：补样/损坏联系商家，失效链接/LIVE缺链接/目录退款问题交人。

新消息先落库，60秒合并，读取当前事项与必要证据，有限工具查询后生成答复；发送前重核入站水位。生成途中有新消息/人工接管则旧未发稿失效。等待无需模型常驻。同关系的主动二发与回复共用控制，专家不能另起对外发送。

## 失败、重试与完成标准

- 只读短时网络错误可有界重试；具体次数由执行层配置而非模型循环。权限/身份冲突不当暂时错误重试。
- 图片先合法读取后识别；当前deepseek-flash官方支持，但应用图片接入未验，未接或失败转人工。截图仅是用户材料，佣金/样品状态必要时仍查平台。
- 已有回复/加橱窗可按业务口径解除新联系限制，明确拒联仍禁止营销；本地估算配额缺少平台重置证据需单独标记。
- 外部写已提交但未确认结果：unknown；不整组重发、换账号或新任务ID重试。
- human_request_created不等于问题解决，message_accepted不等于达人采用，sample_status_seen不等于已寄新样。

## 发布验收

每个工具的来源字段、市场和权限必须有记录；至少覆盖正常、空、缺失、跨人引用、过时、权限不足、调用失败、人工接管、新消息打断。对外回复还需用户校准的正反案例。先完成工具与业务验收，再增加Agent角色；不是先给模型全部接口再让它自行决定能做什么。
