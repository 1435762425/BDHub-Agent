# 一发/二发重新设计：可用数据与证据缺口

核对时间：2026-09-11。范围：只读旧 BDHub 的代码、类型、schema、规范化器与相关代码注释；未连接数据库，未读取真实达人身份、原始会话或业务快照，未调用采集或发送接口。旧仓库核对时 HEAD 为 `7cdc180ebd73a3040a788440a131c73f0c17ce1a`；引用为当时工作树行号，代码存在不代表已经部署或该市场已有数据。

本文件是新匹配架构的数据输入调查，**不承认旧匹配算法、旧一发/二发筛选或旧资格标签为商业要求**。用户已明确：旧设计粗糙且未实际使用，匹配需要重做。旧代码的价值是确定哪些事实可以迁入，以及哪些字段目前不足以支撑判断。

## 核心发现

大规模匹配不必把商品与达人两两提交给模型。现有结构数据已经可以支持：同市场 exact PID 关系召回、类目召回、价格带重叠、视频/直播适配、可执行 Offer 检查，以及基于历史活动的候选排序特征。LLM 的必要工作集中在增量识别明确需求、补充少量语义属性、解释少数难分候选和写最终话术。

最重要的补齐不是再增加 Agent 数量，而是四件事：保留字段来源与统计窗口；把商品和具体 Offer 分开；把历史行为证据与当前需求分开；把“没发现”“没覆盖”“未知”和确实为零分开。

## 1. 数据实体和引用方式

| 实体 | 旧代码事实 | 新系统应保留/新增 | 证据 |
|---|---|---|---|
| 达人 | `creator_identity` 以 `(bd_market, oec_id)` 唯一；handle 是可变化别名 | 以市场和稳定 OEC 引用达人；Kalodata 身份先保留来源身份，经过精确身份解析才归并 | S01:71–110；S05:95–96 |
| 商品 | PostgreSQL 来源池以版本+PID存储；研究货盘具有 market/PID | 新商品键 `(market, pid)`；PID 为字符串，不转数字，不凭标题做同品合并 | S01:2519–2588；S06:115–129 |
| Offer | 同 PID 可有多活动；字段含 campaign、总佣金、公开佣金、样品、有效期 | 新增不可变 `offer_version`，范围至少含 market、来源账号/机构、PID、活动、条款版本；价格/佣金/样品不得分别取不同活动最大值后拼成一个 Offer | S03:497–547；S06:91–134；S07:90–93 |
| 准备/绑定 | 建链结果有 account、binding_id、list_id、verified_at；源码中还混用了旧业务 filters | 保留真实准备状态、账号亲和和核验时间；重新定义业务适用性，不能直接消费旧 `ready_products()` 的最终过滤结果 | S08:318–335 |
| 关系/证据 | 画像快照、日报文件、IM server_id、来源任务都能提供引用 | 统一证据引用，匹配结果可追溯到字段快照/报表行/需求片段，而不是只保存一段 AI 理由 | S01:113–155、287–309、1493–1528；S04:70–107 |

## 2. 商品与 Offer 字段

“已有”表示代码中已有可读取契约；本轮未验证任何真实覆盖率。表中“更新触发”为新系统建议，**不是旧系统已有调度能力或已定时效 SLA**。

| 字段/含义 | 当前来源与语义 | 新系统处理与证据缺口 | 更新触发 | LLM |
|---|---|---|---|---|
| PID、market、campaign_id、source/account | 同品多活动、来源账号可不同；研究货盘 Offer key 只有 PID:campaign，不含市场 | 已有，需在新键中显式加入市场和授权来源；不要跨账号复用准备状态 | 新快照/加入或退出活动/身份或绑定变更 | 不需要 |
| 标题、店铺、seller_id、图、URL | 来源池保留商品与商家基本字段；S01:2563–2587，S06:117–123 | 已有。标题只用于检索和展示，不足以证明功能、材质、适用人群或同品 | 商品内容哈希变化 | 结构提取不需要；不清晰标题的语义分类可按需 |
| 类目 L1/L2/L3、canonical_category、mapping_method/version | PostgreSQL 来源池已有原类目与规范类目；规范字典名为 `mx-canonical-v1` | 已有部分。需要建立跨市场规范概念 ID 与本地显示名映射；未知类目保留未知，不能直接把 MX 字典当 BR/IT 官方 taxonomy | 平台类目/商品内容/映射版本变化 | 先 ID/字典映射；残余语义歧义可用 |
| price_min/max、currency、原价格文本 | 来源池保存区间和币种；研究规范化识别本地小数/千分符，S06:54–64、121–126 | 已有。保存金额 Decimal、币种、价格观测时刻及是否 SKU 区间；单位价、运费和到手价暂无统一字段，不应猜测 | 价格快照变化；临发送核验 | 不需要 |
| public_commission / total_commission | 研究源 `plan_commission_percent`→公开佣金，`total_commission_percent`/partner fallback→总佣金；平台基点除以100，S06:104–129、S03:89–97 | 已有。统一单位“百分点”；总佣金不等于机构净收益；公开值也不等于该达人当前实际所得 | 佣金/活动条款变化 | 不需要 |
| creator_commission / agency_margin | ShareLink plan/item/current保存达人成交方案和机构分成；计算代码存在多种模式，S01:959–974、1034–1038、1083–1089；S09:68–129 | 已有具体方案值；默认分配模式、最低利润和提升幅度需作为新商业政策确认，不能迁入旧默认算法。比较佣金提升须说明相对公开档还是该达人已知档 | 新计划、修改分佣、重新核验 | 不需要 |
| stock / stock_quantity | 商品当前表、来源池及研究 raw 均有库存；研究 `project_inventory_offer()` 没有投影 stock，S01:1025–1028、2584；S07:65–74 | 已有但投影有丢失。新适配器从正确来源提取，不能用有无样品代替库存，也不能把旧投影字段缺失解释为库存0 | 库存快照变化；临执行重新核验 | 不需要 |
| sample_quota / remaining_sample_budget / free_sample | 活动 offers分别保存数值额度、剩余样品预算与布尔提示，S03:509–539 | 已有。按具体 Offer 保存，不能只用 PID 聚合的 max/min。预算字段的单位与限制范围仍要适配器确认；free_sample不证明数量可用；未知额度不变成0 | 样品/活动新快照、已有申请变化；申请或承诺前核验 | 不需要 |
| campaign_start/end、platform status、governed、unavailable_type | 活动开始结束与平台可用状态已规范化，S03:524–527；S06:98–110、130–131 | 已有。保存完整时间/时区，不能只保留显示日期。旧“两个月”“正佣金差”等不是新需求；新的剩余期限应与具体合作所需时间比较 | 活动变更、时钟跨越有效期、预执行检查 | 不需要 |
| selected/joined、list_id、binding、verified_at | 来源选入、已加入活动、建链/卡片可用是不同状态，S07:90–93；S08:318–335 | 已有来源状态；需新增统一 `preparation_status` 及缺口清单。研究准备流程存在不代表正式渠道已开放 | 选入/加入/创建/核验/失效事件 | 不需要 |
| 商品销量、评分、评论数 | 来源当前表与研究 offer提供，S01:1025–1031；S06:124–126；S07:32–48 | 已有，但销量周期、精度和统计范围不统一；K/M展示量是近似值。只能在同口径内比较，不能假定都是30天销量 | 对应源快照变化 | 不需要 |
| 适合怎样展示、场景、属性、变体、内容卖点 | 目前统一结构没有充分字段；标题/图/来源属性可作为候选证据 | 需新增属性事实与语义标签，两者分开。模型提取附来源、版本与不确定性；不要从图猜商业承诺 | 内容变化时单次计算并缓存；高价值缺口按需处理 | 部分需要；不按达人×商品重复提取 |

## 3. 达人结构画像

| 字段/维度 | 当前可用事实 | 缺口与新处理 | 更新触发 | LLM |
|---|---|---|---|---|
| identity、market、profile field_sources | current保存逐字段来源；partial快照仅推进有证据字段，S01:139–155；S04:70–107；S10:239–292 | 已有。整行 captured_at新不代表每个字段都新；新索引需继承逐字段质量和时间 | 画像观测、身份别名变更 | 不需要 |
| main_category | 来自 main_industry 或 industry_groups首项；S02:136–143、237 | 已有单值；完整多类目权重可能在raw分布中但规范列丢失，需按源结构验证后展开。单一“美妆”不能推出只适合美妆 | 类目来源快照变化 | 先结构化解析；语义映射困难再用 |
| followers、GMV、units、GPM | 规范列含 gmv_value/video_gmv/live_gmv/units_sold/gpm/live_gpm/video_gpm；S02:240–248 | 已有。GMV列没有独立window_start/end/currency；采集时间不是统计窗口。需要来源契约明确周期，否则只标 `window_unknown`。区间GPM两端不同目前不伪造单值，保留区间更合适 | 指标源更新；已有字段过期且确会影响候选时补充 | 不需要 |
| 历史 peak_gmv | current存 peak_gmv_value/at；S01:145–146；S04:755–796 | 已有，但这是观测到的最大值，不是“历史最佳30天”已验证指标，也不能替代近期表现 | 有效GMV观测 | 不需要 |
| video_cnt_30d / ec_video_cnt_30d / live_cnt_30d、avg_view | 前三个显式带30d，avg_view字段本身不带周期；S02:249–253 | 已有；形成视频/直播能力特征。视频数量和商业视频数量分开；不要用发帖量直接等同成交力 | 画像有效变化 | 不需要 |
| price_range、commission_rate | 价格带来自 product_price_range字符串，佣金范围来自 med_commission_rate_range，S02:240、249 | 已有但需解析成数值范围、币种和原文；佣金范围不是达人明确底价，不能作为谈判要求 | 画像变化；明确需求覆盖某范围时 | 常见格式不需要；歧义保持未知 |
| labels、brands、bio | 规范化为字符串；品牌仅保留有名字项，S02:113–133、262–264 | 已有辅助证据。可做全文/词法召回；不从bio推断隐私属性；品牌合作≠排他关系 | 来源文本哈希变化 | 非标准场景/风格解释可选，缓存到达人 |
| contact_available、is_active等提示 | contact_available保持bool/None；其他旧flag可能把未授权壳归false，S02:53–56、224–228、257–261 | 需在适配时回到valid_fields/source证据保留未知；平台“响应快”提示不等于本机构真实响应率 | 有效画像或本机构事件更新 | 不需要 |
| 样品 post_rate、video_count、average_views、showcase_added | 样品记录含这些字段；源normalizer有多种alias，S01:3081–3097；S11:274–309 | 已有MX路径。post_rate的分母、周期、单位不明确，不能直接当我方履约率；申请时快照不应覆盖当前画像；BR/IT覆盖需新增核对 | 样品同步、履约事件；按来源分别更新 | 不需要 |
| 受众地区/年龄/性别 | 已有female_pct/top_age/top_region，S02:254–256 | 只在商品存在明确适用范围、且运营需求有依据时用合适的聚合受众特征；当前只取top值不足以代表分布。无需作为首版匹配前置 | 对应画像快照 | 不需要 |
| 最近内容主题、演示方式、视频字幕/内容证据 | 本次核对的统一画像/schema未见带版本的内容语义库 | 需新增按内容对象保存的证据与提取结果；不为全体达人先做逐视频LLM全扫。候选不确定时有界补充，按content hash复用 | 新内容或候选需补证据 | 少量需要 |

## 4. exact PID历史与当前IM需求

| 信号 | 当前来源 | 能证明什么、不能证明什么 | 新处理/更新触发 | LLM |
|---|---|---|---|---|
| 第三方同PID销售关系 | Kalodata `evidence()`含PID、来源达人ID、handle、sale/revenue、live/video revenue；请求有7/14/30天起止，默认截至前2天；S05:42–104 | 能提供所选统计窗口中的第三方估计/展示证据；不证明我方促成合作、当前仍持有样品、接受新报价或数据全覆盖 | 迁入时必须连同run query保留窗口、币种、来源精度、覆盖与身份解析状态；新抓取/新窗口只更新影响的边 | 不需要 |
| 官方/机构日报同PID表现 | `partner_attribution_daily`有日期、market、report_kind、creator、PID、订单/GMV、直播视频及佣金等；S01:1548–1608 | 能证明相应报表范围中的已观测表现；不同report_kind/dimension可能重叠，不可盲目相加 | 仅消费当前有效文件与正确report_contract；按creator+PID+window物化聚合。文件被取代/补报则重算受影响日分区 | 不需要 |
| TAP历史出现 | `tap_creator_product_history`保存first/last_report_date/seen_day_count，S01:1353–1367 | 正向“在报表出现过”关系；本表没有GMV/订单阈值，出现不能自动称“卖得好”；后续日报缺席不抹掉历史 | 同PID召回可用；强度仍回到日报数值与窗口 | 不需要 |
| 历史公开带货视频 | `creator_product`有video_id、PID、发布时间、播放/点赞，S01:505–525 | 证明历史关联，不证明销量；旧库残留表不代表采集仍在运行。当前项目事实明确公开视频PID采集迁出 | 新系统只能消费明确授权的外部证据适配器，按源时间判时效，不恢复旧入口 | 结构匹配不需要；理解视频内容另按需 |
| 已发送商品卡 | IM卡有card_pid；发送意图与component另有执行记录，S01:287–309、818–865 | 能证明某消息/组件引用了PID；不是达人采用或成交 | 新消息增量更新关系触达历史，去重与冷却由程序处理，不占LLM | 不需要 |
| 明确需求与约束 | IM保存server_id、角色/方向、content、intent、card_pid、时间，S01:287–309 | 原消息是证据；旧intent是处理标签，不是永久偏好、真实商业承诺或完整需求 | 新增结构化 `demand_fact`：scope、requested PID/category、must/avoid/prefer、时限、上下文对象、source message IDs、有效性/撤回/冲突；每个新消息批次只抽增量 | 需要语义提取；PID、币种、引用可程序预提取 |
| 人工备注、已有任务、拒联 | relationship有note，creator_task有状态/截止，creator_im_state有rejected等；S01:662–703、1287–1303 | 备注与状态用于核对当前关系；不应把所有历史rejected标签自动视为永久全渠道拒联 | 拒联/暂停/服务需求的语义和作用域明确后成为硬执行事实；市场、渠道、主题、到期分别记录 | 显式按钮不用；历史自由文本需有界抽取和验证 |

需要新增的需求字段示例（是字段结构，不是虚构真实消息）：

```text
demand_fact = {
  creator_id, topic_id, kind, target_ref, value, unit,
  strength: explicit | inferred,
  status: active | superseded | resolved | expired | conflicted,
  source_message_ids, observed_at, valid_until,
  extractor_version, confidence, supersedes_fact_id
}
```

LLM生成的推断不能悄悄变成显式事实。疑似“想要厨房类”可形成候选偏好；明确“只要某一PID”才约束这次服务请求。不同会话阶段的“不要”需分清是不要本品、暂时不要样品还是拒绝后续营销。模型只提供结构化提案，引用校验、状态覆盖和最终执行条件由程序判定。

## 5. 不用LLM即可做的召回与计算

1. **同市场 exact PID倒排索引**：从当前货盘找有历史同品证据的达人，也从达人已观测商品找当前Offer；索引只存真实稀疏关系，不物化全量达人×商品矩阵。
2. **类目倒排+层级映射**：平台类目ID与规范概念ID优先；词典/全文用于标题、品牌、标签。类目缺失走未知/探索召回通道，不能在候选全集中静默排除。
3. **同币种价格带重叠**：数值区间索引；币种/区间不清楚则保留缺口。不把跨市场原币金额直接比较。
4. **视频/直播证据和近期活动**：从显式30d量、同PID分渠道业绩提取数值特征；窗口不明单列。不设“没开直播就不推”的通用硬要求。
5. **真实关系事件**：已推PID、最后消息、显式拒联、人工接管、已有开放合作、需求主题。可由增量事件更新并控制候选冲突。
6. **Offer核验和资源计算**：库存、有效期、样品条款、佣金单位、准备状态、账号能力均为程序检查。已有库存或佣金变化只失效受影响Offer及其候选，不重新“理解”全部商品。

这些是可实施的召回维度，不是已确认的排序权重/商业硬门槛。可执行资格与排序偏好应分别输出。缺证据可以降低判断置信度、触发补证据或进入探索，不能直接将真实匹配得分归零。

## 6. 适合少量语义判断的地方和token边界

| 语义任务 | 输入边界 | 缓存键/失效 | 避免的重复消耗 |
|---|---|---|---|
| 商品语义卡 | 单品经清理的标题/已知属性/有限证据；不带整货盘 | market+PID+内容hash+schema/model版本；价格/库存变化通常不失效语义卡 | 不为每个达人重新解读同一商品 |
| 达人内容卡 | 结构画像摘要+少量有代表性内容；已明确需求单列 | 达人稳定ID+内容集hash+提取版本；数值表现独立更新 | 不每天重读相同个人简介/全部视频 |
| IM需求增量 | 新消息片段、必要引用、当前活动需求与未结事项 | creator+message watermark+主题状态版本 | 不每次召回商品都传整段历史对话 |
| 少数候选复核 | 程序短名单、简短证据卡、未决问题 | creator feature version+offer version+demand version+policy version | 不把所有候选对交给LLM；高确定性的exact匹配可跳过 |
| 最终个性化话术 | 最终选定Offer、确认的相关事实、必要近期对话与说话策略 | 外发意图+条款版本+关系水位 | 被淘汰候选不提前生成话术；重复调度复用已生成草稿 |

是否采用embedding、哪种模型、top K、语义复核比例和每次token上限还应通过实际数据覆盖与离线评估确定；本轮没有选定这些商业/模型参数。首版可先验证结构召回和词法召回，补充embedding时也是每个实体/语义变更计算一次，不能变成pair级生成。成本报表应分别计量补画像请求、embedding、语义提取、复核、话术生成与重试，避免只看单次请求token。

## 7. 必须保留的质量边界和接入顺序

- 每个数值携带：value、unit/currency、window、source_ref、observed_at、quality、coverage_scope。`missing`、`not_authorized`、`not_collected`、`stale`、`conflicted`和真0区别保存。旧bool的False或旧转换器的0不能绕过原始证据检查。
- 新快照不全，不得用空字段冲掉旧有效值；旧值仍保留其原观测时间。current不能提供每个历史时点的全部事实，评估应回到快照/当时有效报表版本。
- 代码支持MX/BR/IT不代表三市场同样覆盖。MX sample schema显式约束单市场；Kalodata列表支持多市场也不证明账号许可或实际返回完整。首轮接入应先输出字段覆盖、鲜度、统计窗口和身份可解析率报告，再决定补采预算。
- 源日报的缺席是未观测，不是拒绝或0销量；第三方销量是来源测量值，不是新增系统的合作成效标签。接触、回复、接受Offer、样品、发布、成交、佣金必须分开计量。
- 下一步最小接入：先迁移少量脱敏规范样本及合成边界样本，验证身份/金额/Offer完整性和exact PID索引；再做只读真实覆盖评估；数据适配稳定后才接在线模型。大规模全量LLM标签工程不作为首个前置条件。

## 8. 按实际量级收敛：首次建特征与增量更新

用户确认商品约 **1,000–10,000**，达人后期上万。`10,000–50,000`达人仅作为设计压力假设，不是已经核实的数据量。在这个量级，首版用现有关系数据库、普通索引、按实体版本保存特征和后台批任务即可；不需要先引入独立向量数据库、特征平台或常驻多Agent集群。全量商品×达人仍然不值得建表或提交模型。

| 优先级 | 首次建立的最少字段 | 建立方法 | 增量更新什么 | 暂不依赖什么 |
|---|---|---|---|---|
| P0 身份与数据质量 | market、stable creator ID、PID、source ID/ref、observed_at、字段状态 | 结构化适配；无法精确绑定的来源达人单独待解析 | 身份新观测/别名确认；受影响实体的索引 | 完整画像、LLM理解bio |
| P0 可执行Offer | Offer版本、market/account/PID/campaign、明确佣金单位与币种、活动期限、平台状态、库存/样品事实及缺口、准备/绑定状态 | 逐来源解析；各字段可以未知，但不能伪造可执行 | 价格/条款/库存/准备事件只失效对应Offer；发送前再核验必要事实 | 自动采纳旧资格标签；全局所有商品先完成建链 |
| P0 关系控制 | 人工控制、已有开放合作、触达PID、明确拒联/暂停、原消息水位 | 导入结构事实和可确认的历史；有歧义保留待判定 | 新消息/接管/暂停/发送结果只更新该关系 | 全历史IM一次性LLM全扫 |
| P1 便宜召回 | 已有exact PID边及窗口；商品/达人规范类目；同币种价格带；视频/直播量与来源时间 | SQL/程序解析、区间计算和倒排索引；按可用字段开放多条召回，缺失不阻塞全部通道 | 日报分区被新增/更正、画像/类目/价格变化；只重算受影响桶/边 | 内容向量、全部达人最新全画像、历史GMV窗口臆测 |
| P2 语义增益 | 当前活跃需求、少量不明确商品属性、进入短名单达人的必要内容 | 在P1效果与缺口分布明确后有界补充，结果按实体/内容hash缓存 | 新消息批次、新内容、语义版本变更；价格/库存变化无需重提语义 | 逐候选对长文本判断 |

首次建特征先产出两个只读数据质量结果：①各市场字段覆盖/鲜度/窗口/单位分布；②有exact PID证据、只有类目证据、无足够证据的达人占比。这里的比例必须来自未来获准的真实读取，本轮不能编造。再用小批脱敏样本检查适配正确性，才启动批量结构特征导入。

增量任务分开维护 `product_content_version`、`offer_terms_version`、`creator_profile_version`、`relationship_watermark` 和日报分区版本。新增一个达人只建立该达人特征与候选；一个Offer失效只撤销相关候选和草稿；消息回复只重算该关系当前主题。不要因一条商品价格变更给全库达人重新生成推荐理由。每日token预算尚未确定，P2的吞吐可以由预算控制，P0/P1结构更新不依赖LLM预算。

## 源码索引

所有链接指向本轮实际核对的旧仓库代码。范围仅用于方便复查，不授权运行文件。

| 标识 | 文件与关键行 |
|---|---|
| S01 | [schema.py](/Users/bjn00003/BDHub/01-BDSystem-V2/bdhub/hub/schema.py:71)：身份71–110；快照113–155；消息287–309；公开视频关系505–525；关系/任务662–703；发送组件818–865；ShareLink959–1116；TAP关系1353–1367；报表合同1421–1537、指标1548–1608；来源池2519–2608；样品3057–3136 |
| S02 | [models.py](/Users/bjn00003/BDHub/01-BDSystem-V2/bdhub/hub/models.py:20)：规范字段20–27；解包53–98；类目136–143；画像178–216；映射224–267 |
| S03 | [product_source_imports.py](/Users/bjn00003/BDHub/01-BDSystem-V2/bdhub/hub/product_source_imports.py:89)：佣金换算89–97；逐Offer观察497–547 |
| S04 | [profile_snapshots.py](/Users/bjn00003/BDHub/01-BDSystem-V2/bdhub/hub/repo/profile_snapshots.py:70)：逐字段合并70–107、730–753；peak755–796 |
| S05 | [kalodata.py](/Users/bjn00003/BDHub/01-BDSystem-V2/bdhub/research/kalodata.py:42)：市场14；查询42–65；解析68–104；请求结果合并148–159 |
| S06 | [campaign_catalog.py](/Users/bjn00003/BDHub/01-BDSystem-V2/bdhub/research/campaign_catalog.py:91)：字段投影91–134；旧资格31–44、67–88及排序155–163只作反例，不迁入 |
| S07 | [commerce_pool.py](/Users/bjn00003/BDHub/01-BDSystem-V2/bdhub/research/commerce_pool.py:32)：销量精度32–48；安全字段51–74；Offer规范77–93 |
| S08 | [commerce_links.py](/Users/bjn00003/BDHub/01-BDSystem-V2/bdhub/research/commerce_links.py:318)：准备状态和账号/卡片绑定318–335 |
| S09 | [commission.py](/Users/bjn00003/BDHub/01-BDSystem-V2/bdhub/send/sharelink/commission.py:68)：方案计算68–129；仅复用数值契约，不默认采纳商业模式 |
| S10 | [profile_observation.py](/Users/bjn00003/BDHub/01-BDSystem-V2/bdhub/hub/profile_observation.py:239)：有效字段与降级识别239–292 |
| S11 | [sample transport.py](/Users/bjn00003/BDHub/01-BDSystem-V2/bdhub/sample_review/transport.py:274)：样品指标来源274–309 |
| S12 | [product_categories.py](/Users/bjn00003/BDHub/01-BDSystem-V2/bdhub/hub/product_categories.py:10)：现有MX规则版本10–13 |
| S13 | [parser.py](/Users/bjn00003/BDHub/01-BDSystem-V2/bdhub/report_sync/parser.py:25)：日报列映射25–83 |
| S14 | [business_tools.py](/Users/bjn00003/BDHub/01-BDSystem-V2/bdhub/assist/business_tools.py:95)：逐字段时效95–128；旧发布货盘关键词入口185–244，不能直接作为全量新匹配引擎 |
| S15 | [current-system-truth.md](/Users/bjn00003/BDHub/01-BDSystem-V2/docs/current-system-truth.md:284)：公开视频PID采集已迁出；此条只用于确认旧表不代表在运转，当前运行能力须另核验 |

验证：本文件所列字段与行号来自源码读取，未作数据覆盖率/渠道可用性/匹配效果的验证；没有运行测试、刷新、发送或数据库变更。
