> 2026-09-14用户最新规则：全托不要求库存数，不以数量为准备/发送门槛。本页库存读取内容保留为接口能力记录；当前只为活动、选入与链接有效性核验调用必要接口，不为库存单独查询。

# 高机会商品：仅全球销售商品

核验日期：2026-09-14；实际范围IT/ACC6、自有机构身份。用户指定唯一全托发现来源，不调用另一个全球销售商品入口。

## 列表合同

`POST /api/v1/affiliate/partner/product/opportunity_product/list`

常规请求：filter.product_source=[]、filter.campaign_type=[8]、filter.label_type=[]、filter.product_status=1；page从1开始，page_size=15。账号参数沿已验证同账号HTTP协议生成，不持久化Cookie、fp或Token。只读POST，不是选入或发送。

响应：code=0，data.products数组、data.total整数、data.has_more布尔。产品PID为19位字符串。常规页已实测，不能以短页或达到total就提前完成；必须核对接口末页、去重数量、报告数量及本轮身份未变。

### 10000结果窗口的末页

- 原常规15条分页完成666页/9990个PID，has_more仍为true、total=10000。
- 第667页按15条请求（偏移9990、结束10005）返回HTTP200/code98001004；不得把它记成完成或消息额度耗尽。
- 相同偏移改为原生page=1000、page_size=10，成功返回最后10条、has_more=false、total=10000。
- 收尾后667个逻辑页/10000个去重PID，原生末页请求独立记录在global_source_page.request_payload；旧9990条及拒绝证据保留，未重采或修改成成功。
- 新采集器在这个已验证边界使用对齐尾页，不先触发一次错误。其他分页边界不猜测成功。
- page_size=100在首页本轮也返回98001004，message为“invalid params; detail:Failed to fetch the GNE product status”。15通过、100拒绝是实测，不等于已证明15是平台最大page_size。

10000是本账号、不带类目筛选查询的结果窗口，不是市场全部全托商品总量。旧完整性口径为`current_query_endpoint_and_total`。2026-09-21 已将28个官方一级类目分区逐一跑到末页并正式切换为 `category_l1_endpoint_and_totals`；分片扩大覆盖，但仍不宣称突破任何单类目自身可能存在的结果窗口。

### 一级类目分片正式验收

- 类目树实时返回 28 个一级类目；请求在原固定筛选上增加 `filter.category_id=[<一级类目ID>]`。
- 每个类目独立核对末页、稳定 reported total 与去重数；28 个分区全部完成后再跨类目按 PID 去重并原子发布。
- 正式运行 `it-global-cat-20260921-01`：2,134 页、31,809 个唯一 PID、类目成员 31,809、跨类目重复 0；相对旧 10,000 增加 21,809，规模为 3.18 倍。
- 总耗时 3,524.088 秒（58 分 44 秒）；最大类目女装和内衣 6,396，所有单类目均低于 10,000；4 个类目明确返回 0 商品并省略 `products`，已按 `total=0 / has_more=false` 的终态兼容。
- 运行错误 0、身份未变化、平台业务写入 0；本地筛分合格 2,321、拒绝 29,488。
- 2,321 个合格商品中原已选 2,300；新增 21 个由 ACC9 持久选入意图执行。20 个已在平台已选池回读 confirmed；1 个请求 HTTP 200/code 0 且来源列表已显示 selected，但子活动尚未出现在精确已选池，保持 `result_unknown` 并只回查原意图。

## 可取得的列表字段

PID、标题、价格区间与币种文本、列表活动ID、平台总佣金、公开佣金、公开广告佣金、销量展示文本、评分、是否已选、样品提示、店铺名称。列表还返回商品地区和图片等字段；当前业务持久化只保留已明确白名单的必要商品字段，联系方式字段不保存。

**本轮列表没有库存和活动期限。** 不借用旧转换函数中的默认product_status=2来证明真实可用，不把列表筛选或is_free_sample当完整推广资格。

## 活动与库存核验

1. `GET /api/v1/affiliate/partner/product/opportunity_product/campaign_detail?product_id=<PID>`返回product_campaign_detail。
2. 当前方案按详情内campaign.campaign_id、crs_campaign_type、commission、promotion_start_time、promotion_end_time，以及外层open_collab_rate/is_selected读取。全托方案详情可返回type9，不能因为列表筛选写[8]就丢弃type9。
3. **列表campaign_id与详情当前campaign_id在实测中不同。** 2026-09-14批量选入进一步核验：旧版原生快速路径可将当前列表ID作为选入请求引用，已选回查取得实际type8/9活动绑定。列表引用不能直接用作TapLink实际绑定。见[销量300件选入](../../implementation/global-selection-sales300.md)。
4. 已选池按精确product_ids查询，实际campaign_info与详情当前活动取交集，再从campaign_product取得库存、状态、公开/总佣金等。没有匹配记录就保留“需要选入或继续核验”，本轮不自动执行选入。
5. 佣金沿新项目已采用的旧版分配规则计算并标为拟方案，不冒充已经应用到TapLink的达人佣金。全托取消库存数量门槛，期限>45天及佣金优势等仍按当前规则判断。

三款真实样本完成详情＋已选交叉核验，各取得1个符合本地准备条件的方案；仅用于验证读取合同，未由此新增选入、建链或发送。

## 采集、版本与复用

- 全部记录进入新项目var/global-source.sqlite；scope冻结机构指纹、市场、账号及唯一源筛选。
- 每页原子提交，保存响应哈希、PID摘要、增量数量、has_more、报告总量和原生请求。重复页、无进展、总量不一致不能冒充完整；中断保留进度。
- 完整且身份核验通过后更新global_source_head；刷新不覆盖历史完整快照。新一轮部分数据不替代上次完整商品展示。
- 当前提供本机“同步全托来源／检查并续采”入口。请求幂等，已有采集Worker复用；未配置擅自推定的每日全量刷新频率。
- 当前仅列表自动逐页采集；活动、库存、选入和TapLink按任务商品定向处理。没有对10000商品逐个抓详情。
- 列表与详情指纹关联，版本变化时不能把旧详情拼到新观察上。后续跨版本差分复用需按商务相关字段和时间分别设计。

## 证据

- 首轮列表：var/global-opportunity-first-read-20260914.json。
- 分页大小与活动字段：var/global-opportunity-contract-20260914.json。
- 尾页与类目树：var/global-opportunity-boundary-20260914.json。
- 旧单查询：global_source_run.id=it-global-20260914，667页、10,000 PID。
- 当前正式分片查询：global_source_run.id=it-global-cat-20260921-01，2,134页、31,809 PID，28/28类完成并发布。
- 详情核验：var/global-source-enrichment-*.json及global_source_detail/global_source_stock。
- 当前源状态：GET /api/global-source。采集状态不表示发送资格，executionAllowed恒为false。

全部新代码只读复用旧BDHub协议，没有调用旧写租约工厂、改旧身份文件或写旧业务库。AI回复仍由用户手动暂停控制。
