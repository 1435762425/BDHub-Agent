# 旧 BDHub 一发、二发与商品准备：当前代码核对

> 后续澄清：用户已明确旧一发/二发匹配设计粗糙、未实际使用。本文只记录旧源码能力与语义，不能将旧筛选/排序或剧本直接作为新需求。正式匹配设计见 [新方案](../architecture/matching-and-token-budget.md)。

核对日期：2026-09-11。用途：纠正新系统的演示假设，为下一步数据契约提供依据；不是新的商业授权。

## 证据范围

旧仓库 `/Users/bjn00003/BDHub/01-BDSystem-V2` 当前 HEAD 为 `7cdc180ebd73a3040a788440a131c73f0c17ce1a`，但存在大量未提交修改。本文依据**当前工作树源码与测试断言**，不能把 HEAD 单独视为被核对的完整版本。文件内容摘要见文末。

本次只读代码与文档，没有读取原始私密会话、业务数据库或账号身份，没有调用远程 API，没有运行旧系统测试、服务或生产流程。下文“测试证据”指已检查的测试代码，不表示本次执行通过或生产验收。已读取旧仓库 AGENTS.md、相关业务规则和业务逻辑树；其中较早的固定筛选与市场状态被较新的源码/测试补充，不能直接搬成新系统默认规则。

## 八组旧版行为与可复用事实

### 1. 一发和二发是两种合作发现方式，不是消息序号

一发从可提供样品的商品出发，在同市场达人库按规范类目初筛，再由模型结合价格、近期视频/直播、带货表现匹配；画像陈旧进入待补资料，模型不能编造兴趣或合作意愿。二发从精确 PID 出发找已有出单证据的达人。两条路径可以命中同一商品、同一达人，最终应交给同一关系协调。

- 源码：`bdhub/research/outreach_ai_worker.py:142-194`；`outreach_ai.py:198-245`；`outreach_model.py:47-52`。
- 测试：`bdhub/research/tests/test_outreach_ai.py:98-105`，同一次运行一发有样品商品 1 个、二发商品 2 个；同文件 `:47-61` 检查模型不接触身份联系人并保留历史画像标记。
- 用户价值：增加二发有效线索，不会把“没回复再追一句”或“类目相近”冒充已经带过同款。
- 新契约：`Opportunity.kind` 保存 `sample_match` / `same_product_sales` 等来源语义，消息轮次、是否第一次联系另存，不用 `first/second` 推导接触历史。

### 2. 二发证据必须包含精确商品、达人、窗口和来源覆盖

按 PID 查询仅保留 `sale > 0` 的合法 Handle，同批去重；当前只读 GMV 排序的前 50 条，再截取请求数量，并明确不是全部达人。按 Handle 查询先精确解析 Kalodata 身份；没查到某 PID 不能推断从未带过。可选窗口为 7/14/30 天，结束日按查询日减 2 天冻结。视频/直播收入缺字段时渠道保持未知，不能猜。

- 源码：`bdhub/research/kalodata.py:25-64,81-104`；`kalodata_worker.py:144-187`。
- 测试：`bdhub/research/tests/test_kalodata.py:101-123` 覆盖正销量、去重、精确 Handle、同名近似拒绝；`:126-132` 覆盖截断标记；`:171-178` 保留账号偏好造成的币种未知。
- 用户价值：可看到究竟是“尚未覆盖”“无本窗口证据”还是“确实命中”，避免大量数据下错误排除与错误个性化。
- 新契约：证据保存市场、PID、源达人 ID、待解析/已解析 OEC、窗口、观测时间、销量、渠道与 `coverage/truncated`。同款销售仍不能证明当前持有实物。

### 3. Product、Campaign Offer、给达人的佣金方案和可发商品卡必须分开

一个 PID 可有多个 Campaign Offer。`offer_key = pid:campaign_id`，市场由快照/调用范围约束；每个 Offer 保存公开佣金、总佣金、佣差、样品、活动期限、平台可用性。给达人的佣金由明确规则计算，机构保留佣金为总佣金减达人佣金。当前规则包括平均分佣、保留机构利润、公开佣金上浮、全部给达人；这不是商品对象上的一个固定百分比。

- 源码：`bdhub/research/campaign_catalog.py:91-163`；`catalog_rules.py:145-170`；`bdhub/send/sharelink/commission.py:68-129`。
- 测试：`bdhub/research/tests/test_catalog_workspace_rules.py:79-86` 同一示例按规则得到不同达人佣金，拒绝非法命名模板字段。
- 用户价值：Agent 讲的是这一活动、这一卡片、这一时点真正能给达人的条件，避免把总佣金全数承诺给达人或丢失机构收益。
- 新契约：拆为 `Product(market,pid)`、`Offer(market,pid,campaignId,sourceVersion)`、`CommissionQuote(ruleVersion,creatorBps,agencyBps)`、`CardBinding(account,listId,offerRef,verifiedAt)`。金额用整数最小单位或精确十进制，佣金用基点。

### 4. 样品资格是路线相关事实，二发不以样品作为门槛

较早 MX/BR Campaign 一发筛选要求同一 Offer 的数值 `sample_quota > 0`。较新的非 MX 已验证商品卡招募使用平台明确 `has_sample`；样品数量可以为 null，仍可进入一发，但不得宣称有已确认额度。二发同时包含有样品和无样品商品。网站 ShareLink 的 `sample_quota > 0` 门槛属于另一个业务，不可套到全部二发。

- 源码：`bdhub/research/campaign_catalog.py:111-132,269-279`；`commerce_links.py:318-368`；`outreach_model.py:62-63`。
- 测试：`bdhub/research/tests/test_commerce_workspace.py:162-173` 明确 `has_sample=true`、`sample_quota=null` 可以进入新路线一发；原无样品条目仍进入二发并保留卡片绑定。
- 用户价值：不因缺样品数误丢意大利机会，也不把“可以申请”夸大为“可批准/已预留/已发货”。
- 新契约：保留 `availability: available/unavailable/unknown`、可空 `quota`、来源、活动、观测时间和 `samplePolicy`，不能只用布尔值承载所有含义。

### 5. 两个月、佣差、评分、价格是可配置筛选，不是永久写死的招募法律

当前默认筛选为 `min_gap=0.01`、剩余两个月、平台可用；用户可以保存按天数/月份/指定日期/不限、样品、销量、评分和价格的条件。“全部货盘”保留零/负佣差、临期/失效商品，供调查，而筛选货盘解释为何排除。库存与卡片可用性另在具体动作前核对。同步不自动启动旧版 AI；新系统已获授权的 Agent 自动化应另有经营目标/调度，不把数据同步隐式当作发送授权。

- 源码：`bdhub/research/catalog_rules.py:22-34,78-104`；`campaign_catalog.py:137-163`；`outreach_ai.py:193-195`。
- 测试：`bdhub/research/tests/test_catalog_workspace_rules.py:33-48,143-147` 明确允许自定义 7 天筛选，不重新加入固定两个月或样品门禁；`test_outreach_ai.py:126` 检查同步不触发 AI。
- 用户价值：经营策略可随市场和货盘变化，数据被保留和解释，而不是采集时就被不可逆丢弃。
- 新契约：筛选和商业条件均有版本；Offer 变更触发重新评估，不能把原型中“有效 24 小时”当活动期限或商业政策。

### 6. 发现、选入、建链、卡片验证、发送是五类不同动作

当前商品 API 的 `campaign/global/selected` 是分开的来源。全球销售商品先选入才进入可建链来源；`/catalog/build` 明确拒绝直接从 global 建链。创建 Product List 的回执不是 IM 商品卡就绪：要精确核对 PID、Campaign、List ID、名称与账号；新路径要求库存有值且大于 0。IM 缺 Campaign 时可用 Product List 成员信息补证，禁止用其他 Campaign 代替。招募读取的已就绪卡片须在 24 小时内验证且未被清理屏蔽。这里只核对代码能力，未确认真实平台今天可用。

- 源码：`dashboard/commerce_catalog_api.py:30-51,141-170`；`dashboard/commerce_api.py:221-225`；`bdhub/research/commerce_links.py:30-34,44-100,318-335`；`bdhub/send/taplink/protocol.py:34-86`。
- 测试：`bdhub/research/tests/test_commerce_workspace.py:77-97` 选入未知不重试；`:106-113` 缺库存不能通过新卡片验证；`:130-146` 通过精确成员补证且错 Campaign 拒绝。
- 用户价值：页面能准确告诉运营“已找到商品”“正在准备条件”还是“真的可发”，避免 Agent 对一张不存在或条件不同的卡片展开谈判。
- 新契约：独立 PreparationTask 和 CardBinding；每类外部写有自己的 intent 与结果未知状态，不能共用一个 `product.ready=true`。

### 7. 去重应按业务层次处理，不等于同达人同商品永远不能再次合作

发现层合并重复 Handle/PID；商品层按 PID 展示、保留多个 Offer；准备层按账号和 PID检查已有与未知意图；发送层冻结具体文字/卡片组件并原子预留。旧发送预检遇到活跃同商品观察关系时移除卡片但可保留文字，历史已存在商品关系也可保留文字；经具体规则可人工强制历史卡片但不产生新的获客归因。历史接触次数、待回复、缺画像是显式提示，拒联和身份不明才是硬排除。单达人任务中的多 PID 还会合并文字，按配置最多 1–4 张卡保留剩余项。

- 源码：`bdhub/send/preflight.py:121-175`；`preflight_service.py:236-310`；`service.py:41-57`；`bdhub/hub/repo/send_tasks.py:1266-1334`。
- 测试：`bdhub/send/tests/test_preflight_service.py:612-657` 活跃卡片不能用 override 绕过且重复行无组件；`bdhub/hub/tests/test_send_task_repo.py:456-581` 覆盖版本冲突、拒联重查、原子预留和活跃商品关系冲突。
- 用户价值：减少重复营销，同时允许有依据的服务回复、不同 Offer 或下一阶段合作继续；不会因为存在一项已关闭合作就永久吞掉新需求。
- 新契约：关系唯一性、机会归并、合作周期、卡片交付去重、消息 idempotency 必须分层。旧观察期/每次卡片数不能未经确认直接变成新系统的永久默认商业规则。

### 8. 发送组件按顺序持久执行，市场能力与结果必须有独立证据

发送服务在一个事务内重查预检事实与版本、预留归因，再交给 Worker；文字和商品卡分别有组件、attempt 和回执。未知结果停领，不自动重试；卡片归因先落核心账本，若崩溃仍转未知处理。核验必须有证据，不能把空响应写成未发送。市场显示和商品研究能力不等于正式 IM 批量发送已经验收；当前旧代码 MX `send=enabled`、BR 默认 pending、IT `send=canary`，这些是代码配置快照而非当天平台探针结果。

- 源码：`bdhub/send/service.py:41-80,108-123`；`runner.py:59-76,88-153,349-384`；`bdhub/hub/repo/send_tasks.py:2111-2166`；`bdhub/hub/markets.py:131-141,182-186,229-235`。
- 测试：`bdhub/send/tests/test_runner.py:282-365` 顺序、成功卡不重发、未知暂停、归因先落账；`:632-679` 跨市场发送拒绝；`bdhub/hub/tests/test_send_task_repo.py:735-770` 成功阻止新 attempt、未知不重试和消息 ID 必填。
- 用户价值：一次卡片超时不会导致批量重复联系，三市场的经营目标也不会误被理解为三个市场已经具备相同执行接口。
- 新契约：独立 `MarketCapabilityEvidence` 和账号绑定；保持本地模拟与真实执行的类型/通道隔离。

## 新 UI / 本地底座当前错误假设与建议顺序

下述路径相对于新仓库，记录的是本次调查看到的版本，主任务后续可能已经修正。

| 位置 | 发现 | 建议 |
| --- | --- | --- |
| `apps/web/src/features/bdhub/OpportunitiesPage.tsx:24` | 商品详情用 `sample ? first : second` 自动选择路径；有样品的商品被默认排除二发入口 | 显式提供一发/二发入口，分别解释资格 |
| `apps/web/src/features/bdhub/model.ts:68-70` | Pedro 的“同类商品表现”和 Andrea 的“内容形式匹配”被归为二发 | 只有精确 PID 证据才标二发；缺证据进入待核实，或作为一发匹配 |
| `apps/web/src/features/bdhub/model.ts:10` 与 `apps/web/src/server/runtime/engine.ts:102-115` | 商品直接持有单一佣金/方案版本，未建 Campaign Offer 和可发卡片绑定 | 先补来源契约与资格计算；不从 UI 文案倒推可承诺商业条件 |
| `apps/web/src/features/bdhub/model.ts:106-107` | `ensureCase` 查到任意历史同款 case 就返回，包含 closed | 先定义“关联历史关系”和“新合作事项”的区别；具体何时允许新一轮合作保留待确认 |

建议本轮可直接落地的部分：纠正路径语义和示例、补纯本地 Product/Offer/证据资格合同与离线场景，并展示具体缺失事实。真实货盘迁入、账号适配与自动化商业参数应在源码依据和使用者策略共同明确后推进。

## 需要使用者确认的商业缺口

1. **新系统主动二发是否统一要求先有已验证卡片，再寻找达人？** 旧非 MX 招募使用 ready TapLink，MX 历史路线从 Campaign 货盘发现；可以先发现后准备，也可以先准备后发现，两者对大货盘的成本不同。建议支持两阶段，已准备优先，未准备只建研究机会。
2. **相同达人、相同 PID 的下一轮合作何时可重新进入主动触达？** 旧卡片观察与新关系 Agent 的持续经营不是同一规则；是否按活动/条件变化、明确回复或间隔决定，需要业务确认。无需因此阻止合法服务回复或修复永久去重的数据建模问题。
3. **三市场默认分佣和筛选是否沿用旧系统各市场保存配置，还是从新经营目标单独指定？** 已具备算法不等于已经选择默认值；不能拿通用示例中的平均分佣、两个月、24 小时或每日预算当用户商业授权。

## 当前工作树内容摘要

下列 SHA-256 绑定本次阅读的主要文件；全部位于旧仓库。未复制源码或私密数据。

```text
dashboard/commerce_api.py cda64d88b6b2ecdf541307dc92993e54e2b8ee41888b5969aefcbda3d8c206ee
dashboard/commerce_catalog_api.py 4a0655e1e8faa81ec7feace447d8f8161627a4d5498ee1a16924d3bf51f9b5bc
bdhub/research/campaign_catalog.py 7c0b6f9e13e161ab05eb12feed7727705980f9c12fd3680c4311f6b5bc329252
bdhub/research/catalog_rules.py b269c2f2804e91c0ccbb6de9b6600261ff4bf39fb1d2097cc111f8d1e7db0b3d
bdhub/research/commerce_links.py 2f8ff2831e9fe88d8e7a380d77eea7c6fb4cca1548bd060bf0fe22b7c38eb2fc
bdhub/research/kalodata.py 1c8c2a948fe24c1550c08dd55a81864ab55d4e8f94050f8dcacbc3fcf47c2442
bdhub/research/kalodata_worker.py b100229a8f72fd9eae515753ce56f50e1c3b4ef098f11186924c77dda5a09e52
bdhub/research/outreach_ai_worker.py 5b69650884d04368d48406c9dfe289178f9bf455760ebc32aec46952e1e8883d
bdhub/send/preflight.py 44476aff90433cf38e8ee36f765d4205b87d08f2e68db2b4649a19f07a0e55f8
bdhub/send/preflight_service.py 3142e31966976630ec3092f31864e3b1350195e90a0f8017224b89610eba4533
bdhub/send/service.py 60dcfb15983652e57d21f43e74b45220ab2a215d88077d2558f24ba66329dd8a
bdhub/send/runner.py 06fbd8e661e36553f11ce4b37a1ff5d40fe2f355c763f71ef1e1f415c6b86d71
bdhub/hub/repo/send_tasks.py 34b9ad7cf89bbc4fdda677087023ea752dae6a7e4c87d6e3ff2420d6c4b44014
```
