# 达人发送池与 AI 回复策略 v1

状态：当前细节设计。确认日期：2026-09-19。

本页把 `TapLink → 达人线索 → OECID → 达人×PID → 发送池 → 收信分类` 收敛成一套可实施口径。产品边界仍以 [项目文档](../PROJECT.md) 为准，技术总览以 [技术文档](../TECHNICAL.md) 为准；本页提供两份主文档不宜展开的排序、上下文、回复模板和评测细节。

## 1. 一句话流程

```text
当前标准 TapLink
  → 每个 PID 查询近 14 天最多 20 条正销量线索
  → 用 OECID 合并同一达人及其历史 handle
  → 形成达人 × PID 位置
  → 按 Kalodata sourceRank 排达人和达人内部商品
  → 业务只显示「可发送 / 等待中 / 暂不参与」
  → 新回复立即冻结该达人
  → DeepSeek 或 Jev 只做意图与动作分类
  → 不回复 / 三种固定回复 / 人工接管
```

商品失效只影响该 PID；未解决回复和人工接管只冻结该达人名下的全部 PID，不冻结该 PID 对其他达人使用。

## 2. TapLink 到达人线索

### 2.1 查询前提

只有同时满足以下条件的 PID 才进入 Kalodata 查询队列：

1. 当前商品方案仍合格；
2. 当前方案有唯一标准 `currentListId`；
3. 该链接使用 `commission-1-to-2-v1`、`link-naming-v1` 和当前统一命名模板；
4. 链接最后一次成功核验结果仍有效。

历史卡不参与新发送，也不参与佣金比较。Campaign 来源每天刷新时核验对应 TapLink；全托已选商品每周核验一次；组批前和发送前不再远程复读。

### 2.2 每个 PID 的线索范围

- 时间窗：近 14 天；
- 排序请求：Kalodata `revenue DESC`；
- 数量：每个 PID 最多保留前 20 条正销量线索；
- `sale <= 0`、销量缺失或 handle 非法的记录不进入线索；
- 完成后 7 天才再次到期；失败不写 `queried_at`，额度耗尽保留断点；
- 原始 `revenue`/GMV 字符串作为来源证据保留，不把 `$ / £ / € / ¥` 混成一个可直接比较的金额字段。

`sourceRank` 是来源已按 GMV 排序后的相对名次，数字越小越优先。跨 PID 排序统一使用 `sourceRank`，不直接比较没有可靠币种归一的 GMV 原始字符串。

## 3. OECID 与达人改名

- 稳定身份键是 `市场 × OECID`；
- handle 只是发现入口和带时间的别名，不是长期主键；
- handle 改名后，同一 OECID 必须归并到原 `creatorId`；
- 归并后保留原有 PID 位置、发送历史、冷却、拒联和未结案件；
- 同名 handle 不能跨 OECID 串配；无 OECID 的线索不能进入发送池；
- 已经确定“找不到”的 handle 保留证据，不因另一个 PID 再次出现而重复 Find。

达人级只保存稳定身份、别名和全局控制。不能用一段会持续过期的“关系摘要”替代商品和消息发生时的真实事件。

## 4. 达人×PID 与统一排序

一个位置等于 `creatorId × PID`。同一达人可同时保留多个 PID 机会，但每轮只取一个位置进入当前发送批次。

排序分两步，使用同一原则：

1. **排达人**：取该达人全部当前有效位置中最小的 `sourceRank`；最小者排前。
2. **达人内部选 PID**：同样选择最小 `sourceRank` 的位置；并列时按正销量更高、PID 升序确定唯一结果。

佣金不再是达人内部排序键。佣金只负责判断商品方案是否合格以及确定要发送的标准卡，不负责把低 GMV 排名的商品提到前面。来源类型也不获得额外优先级。

示例：

| 达人 | 当前位置 | 本轮结果 |
| --- | --- | --- |
| Anna | PID-A `rank=1 / 13%`；PID-B `rank=4 / 17%` | 达人排第 1，本轮选 PID-A；不会因为 PID-B 佣金更高而改选 |
| Bruno | PID-A `rank=2 / 15%` | 达人排第 2，本轮选 PID-A |
| Carla | PID-C `rank=1`；PID-D `rank=3`；有未结问题 | 只冻结 Carla；PID-C 仍可发给其他达人 |

## 5. 发送池只显示三种业务结果

| 业务结果 | 含义 | 典型内部原因 |
| --- | --- | --- |
| 可发送 | 当前三个前提都满足，可进入批次 | 商品/链接有效、OECID 已确认、达人未冻结且不在冷却 |
| 等待中 | 事实保留，等待条件满足后自动重算 | 等标准链接、等 OECID、冷却、该达人有未结回复、同达人其他 PID 等轮次 |
| 暂不参与 | 当前不能继续参与新发送 | 商品不合格/失效、明确拒联、当前规则排除 |

以下属于原因或历史，不再膨胀为业务状态：

- `queued / cooling / awaiting_reply / waiting_link` 是“等待中”的内部原因；
- `product_invalid / excluded` 是“暂不参与”的内部原因；
- 已发送属于历史结果，不是发送池状态；
- 技术层仍可保留细分枚举用于排障、对账和恢复，但主页面只展示上述三种结果。

## 6. 回复处理的五种动作

运营层只允许以下五种结果：

```text
no_reply
sample_self_service
collaboration_ack
link_usage
human
```

| 动作 | 使用范围 | 处理后 |
| --- | --- | --- |
| `no_reply` | 纯感谢、emoji、普通结束语；或无需继续回答的简短礼貌消息 | 标记已处理，解除本次回复冻结 |
| `sample_self_service` | 样品申请、无按钮、审批、物流、未收到、用完、损坏和补寄 | 每个 `creatorId × PID` 只发一次固定模板，随后关闭事项 |
| `collaboration_ack` | 明确愿意合作、会加入橱窗、会制作视频/LIVE、已发布内容 | 发送固定合作确认模板，随后关闭事项 |
| `link_usage` | 唯一 PID 清楚、标准 `listId` 存在，达人只是不知道如何使用商品卡 | 发送固定使用说明，随后关闭事项 |
| `human` | 付费、预算、目录、WhatsApp、Boost、投诉、拒联、佣金异常、链接打不开、多 PID 不明确、多意图、附件或无法理解 | 保持达人级冻结，等待人工处理并明确交还 |

礼貌拒绝当前商品可以关闭当前 `creatorId × PID` 机会，但不自动等于全局拒联。明确要求停止联系才设置达人级拒联。

## 7. 已确认固定回复

### 7.1 样品与物流

覆盖样品申请、没有申请按钮、审批、发货、未收到、样品用完、损坏和补寄，不再查询或承诺具体样品/物流事实。

意大利语：

> Se hai ancora il prodotto, puoi continuare a utilizzarlo nei tuoi prossimi video o LIVE 😊 Se invece ti serve un campione nuovo o sostitutivo, controlla nella pagina del prodotto su TikTok Shop se è disponibile il pulsante per la richiesta. Se il pulsante non compare o la richiesta non è disponibile, puoi contattare direttamente il venditore. La disponibilità, l’approvazione e la spedizione dei campioni dipendono dal venditore. Continueremo comunque a condividere con te altre opportunità e prodotti adatti ai tuoi contenuti. Grazie per la comprensione!

中文对照：

> 如果你手上还有该商品，可以继续用于接下来的视频或直播 😊 如果需要新的或替换样品，请在 TikTok Shop 商品页面查看是否有样品申请按钮；如果按钮不存在或无法申请，请直接联系商家。样品是否提供、是否批准以及发货均由商家决定。我们也会继续与你分享适合你内容的其他商品和合作机会。感谢理解！

纪律：不提问，不承诺审批、补寄、查询或物流时间；重复同一问题不再回复；同一消息同时包含其他重要诉求时转人工。

### 7.2 合作确认

意大利语：

> Perfetto, grazie per la disponibilità! 😊 Non vediamo l’ora di vedere il tuo prossimo video o LIVE. Continueremo a condividere con te nuove opportunità e prodotti adatti ai tuoi contenuti.

中文对照：

> 太好了，感谢你的配合！😊 期待看到你接下来的视频或直播。我们也会继续与你分享适合你内容的新机会和商品。

### 7.3 商品链接使用

只有唯一 PID 明确且当前标准 `listId` 存在时才能使用。

意大利语：

> Puoi aprire la scheda prodotto che ti abbiamo inviato, aggiungerla alla tua vetrina e utilizzare quel link nel tuo prossimo video o LIVE. La commissione migliorata si applicherà alle vendite generate tramite quel link. Non vediamo l’ora di vedere il tuo prossimo contenuto! 😊

中文对照：

> 你可以打开我们发送给你的商品卡，把它添加到橱窗，并在下一个视频或直播中使用该链接。通过这条链接产生的销售会应用提升后的佣金。期待看到你的下一个内容！😊

若达人要求重新发链接、询问“哪条链接”但会话中存在多个 PID、链接打不开或佣金异常，一律转人工。

## 8. 模型只做分类，不直接回复或改状态

### 8.1 Provider 可替换合同

先用 DeepSeek 做影子分类和迭代；Jev 获得权限并完成同一测试集实测后，可切换为首选分类器。业务代码不依赖具体厂商，只依赖 `ReplyClassifier` 合同：

```text
classify(input) -> {
  schemaVersion,
  provider,
  model,
  action,
  intentCode,
  evidenceMessageIds,
  evidenceQuotes,
  relatedEpisodeIds,
  confidence,
  humanReason,
  templateKey
}
```

模型的权限只有：选择五种动作、给出意图和引用证据。模型不能：

- 直接发送消息；
- 修改达人、PID、冷却、拒联或案件状态；
- 生成自由回复正文；
- 自动查询当前佣金、样品、物流、商品目录或链接状态后回答；
- 把自己的历史判断写成训练真值。

固定模板由业务代码按 `templateKey` 选择。多意图、证据不足、附件、PID 归属不唯一或守卫失败时，确定性代码强制改为 `human`。

### 8.2 Jev 切换条件

Jev 不能因为获得账号权限就直接上线。必须使用相同的用户审核样本，与 DeepSeek 比较：

- 五种动作准确率；
- 应转人工却误自动处理的数量；
- PID/消息证据引用是否正确；
- 延迟、成本和失败率；
- 意大利语与墨西哥西班牙语分别表现。

只有用户审核通过的案例才可成为正例或反例。Jev 未通过前，DeepSeek 继续作为影子分类器，真实自动回复保持关闭。

## 9. 事件级上下文，不发送整段聊天

同一达人可能在不同时间收到多个 PID，商品、佣金和链接事实也会变化。每次把完整聊天全部发给模型，既浪费成本，也容易让模型把旧商品和新问题串错；只保存一段“关系摘要”同样会丢失时间和 PID 归属。

目标上下文由五类记录组成：

| 对象 | 保存内容 |
| --- | --- |
| 达人全局控制 | `creatorId/OECID`、当前 handle 与别名、拒联、人工接管、语言；不保存会覆盖事件事实的自由摘要 |
| `outbound_episode` | 每次已确认外发的时间、PID、Offer、`listId`、佣金版本、消息/卡片引用和发送意图 |
| `inbound_turn` | 每条达人入站消息的原文、类型、时间、会话和不可变消息 ID |
| `turn_episode_link` | 当前入站消息与一个或多个外发 episode 的候选关联、规则证据和置信度 |
| `service_case` | 由哪些入站 turn 触发、涉及哪些 episode/PID、当前动作、人工原因、状态和关闭证据 |

分类输入只包含：当前未处理入站 turn、少量相邻 turn、候选相关 outbound episode、达人全局控制和固定政策版本。原始事件始终留在台账中；任何摘要都只是可重建缓存，不能成为事实源。

## 10. 集中处理节奏

1. 收信持续运行；收到新消息后立即对该 `creatorId` 设置业务冻结，避免在尚未分类时继续发新的 PID。
2. 删除固定“最后一条消息后等 60 秒”的逐达人 debounce。
3. 分类器按集中批次读取尚未处理的事件；初始建议每 2 小时一次，周期作为配置项，待首轮真实影子评测后再决定是否调整。
4. `no_reply` 和三个固定模板可以形成候选处理结果；真实自动发送仍须等待用户后续明确开启。
5. `human` 项进入每天固定 2–3 个运营处理窗口；投诉和明确拒联立即冻结，但不要求秒回。
6. 超过 24 小时仍未处理只做内部提醒，不自动编造回复或解除冻结。

集中处理不是把多条消息压成一个不可追溯文本。每条入站消息继续独立保存，批次只是执行调度方式。

## 11. 真实数据基线与评测方法

### 11.1 现有只读样本

| 市场 | 已盘点事实 | 对设计的影响 |
| --- | --- | --- |
| 意大利 | 35 条回复，其中 26 条 live、25 个会话；首轮 DeepSeek 影子结果为不回复 7、万能回复 6、人工 12 | 可用于小规模逐条用户审核，不能单独证明泛化能力 |
| 墨西哥 | 6,399 条达人入站、6,027 条正文、2,855 个会话；2,287 个有回复会话曾发送至少两个 PID，5,255 条回复发生在多 PID 会话 | 必须保留 episode/PID 关联，不能只看达人级摘要 |
| 墨西哥人工回复 | 327 条已发送人工回复，其中 245 条可按时间找到前一条达人消息；中位回复约 6.7 小时，90% 分位约 9.9 天 | 不需要秒回；旧库没有“人工回复引用哪条消息”，不能直接当训练真值 |

本机分析证据保存在 `var/reply-triage-shadow-v1.json`、`var/reply-triage-review-v1.md`、`var/mx-human-reply-analysis-v1.json` 和 `var/reply-policy-review-v2.md`，不提交 Git。

### 11.2 迭代闭环

```text
真实历史消息
  → 模型按当前政策分类（影子模式）
  → 页面展示原文、中文理解、关联 episode、动作和模板
  → 用户判定 OK / 不 OK，并给出正确动作或参考回复
  → 形成带政策版本的正例/反例
  → 回放完整固定测试集
  → 比较新版本，不自动从模型自己的输出学习
```

主要指标不是“模型给了多少自动回复”，而是：人工接管漏判、错 PID、错误解除达人冻结、重复模板和不该承诺的内容是否为 0。

## 12. 当前实现差距

2026-09-20 已完成第一轮读侧对齐：

1. `leads-queue-v2` 保存完整回执，但每 PID 只发布当前前 20 条正销量线索；
2. `market × OECID` 身份和历史 handle 继续归并到同一 `creatorId`；
3. `lead-pool.v2` 已统一 `sourceRank → units DESC → PID`，业务 API 与页面只显示三种结果，已发送单列历史；
4. `catalog_current_binding` 已成为唯一标准 TapLink 投影，旧卡不再进入线索或发送材料。

剩余明确差距：

1. `outbound_episode / inbound_turn / turn_episode_link / service_case_turn` 已建立并从本机历史证据只读回填；分类输入不再发送完整聊天。
2. 当前收信 worker 已移除 60 秒逐达人模型调用，只保存事件、立即冻结达人并进入两小时集中队列；旧 `cycle_agent.py`、`cycle_reply_facts.py` 和事实回复代码仅保留历史兼容，不在当前收信路径。
3. 五种动作和三条固定模板已收敛到 `config/reply-policy.json`；`reply_classification` 与 `reply_review` 分开保存模型判断和用户真值，预演页不会创建回复发送意图。
4. DeepSeek 与 TypeSafe Jev 已接同一影子合同；Jev 固定使用官方 `jev-1.13.0` System One Choice，35 条真实意大利 turn 已完成同集对照：26 条一致、9 条分歧，一致率 74.29%，尚待用户逐条审核形成真值。
5. 正式发送桥已冻结完整达人×PID×Offer×`currentListId`、话术与顺序；frozen-v2 执行只读本地当前绑定并调用 `descriptor()`，不再运行时重选 PID 或调用 `fresh_card()`。平台明确拒卡只让对应 PID 等刷新，unknown 仍停批核验。旧 legacy-only 批次保留兼容路径。
6. 当前自动回复开关必须继续关闭。完成代码不等于允许真实回复。

## 13. 后续实施顺序

1. 线索数量、统一排序、三结果投影和标准链接已完成，继续保持回放验收；
2. 冻结批次、start/stop 和移除发送前 `fresh_card()` 已完成，保持真实发送由页面单独启动；
3. 事件账本、provider 无关分类合同、确定性守卫、版本化模板和用户审核页面已完成；先由用户审核真实意大利样本并形成固定正反例集；
4. 批量影子分类、同集一致率、审核后准确率/误自动处理/误转人工指标已完成；下一步由用户审核 35 条真值并补受控的 service case 关闭映射，不连接自动发送；
5. 继续扩大 DeepSeek/Jev 同集评测并由用户审阅；只有用户另行明确开启后，才接真实自动回复发送和结果回查。

本阶段只完成规则固化、影子分类和人工评测，不恢复真实发送或 AI 自动回复。
