# TikTok接口与Agent能力手册

版本2；核查日期2026-09-13。面向BDHub-Agent V1二发自循环、IM回复及必要账号/货盘/数据底座。本文是新系统的复用入口，不是任意TikTok API的完整官方规格，也不宣称所有市场已经接通。

## 用户纠正：样品不是二发采用证据

二发来源是Kalodata历史同品带货，不要求达人曾从本机构获批样品。样品记录仅服务其可见范围的样品咨询，不能验证二发历史持样来源，不能把其中橱窗布尔作为本次二发采用事实。采用主线保留IM互动与本机构TAP，精确单次链接归因另核实。

## 阅读入口

| 内容 | 文件 |
|---|---|
| 按业务查用途、输入与能取得的数据 | [业务分类目录](business-catalog.md) |
| 历史网页脚本补充发现 | [网页接口发现](web-discoveries.md) / [283条候选](web-bundle-candidates.json) |
| 方法、路径、副作用、实际读取市场 | [接口目录](endpoint-index.md) / [机器注册表](endpoint-registry.json) |
| 本轮真实响应的全部字段路径与类型 | [字段字典](field-dictionary.md) / [原始字段观察证据](field-observations-20260913.json) |
| 源文件/行号/指纹、源码候选字段、111张表字段 | [源码清单](source-inventory.json) |
| Agent工具、输入输出、业务权限与失败行为 | [Agent工具合同](agent-tools.md) |
| 新发现、旧能力复用、未解决项与调查路线 | [发现与缺口](findings-and-backlog.md) |

旧工作台API是本地应用命令，不等于TikTok接口。新注册表排除了公开推品站/internal sample等非TikTok路径，单独补入动态拼接的info/token与JSON报告合同。扫描405个非测试Python文件发现34个路径字符串；人工归类及本轮网页SDK实测补充后为34条TikTok路径记录，其中一项为下载路径前缀。无解析错误。动态拼接、浏览器SDK、JSON合同和公开Open API的其余能力可能不在该扫描范围，数量不是平台接口总量。

本目录主要记录已登录Partner后台和IM使用的原生网页接口，不是已取得Open API授权的证明；官方开放平台SDK/权限需要独立登记。

## 本轮实际验证

ACC6，MX/IT/BR活动列表及已选商品各1页，MX样品已发货状态1页、IT既有Freegrin列表成员1页：首轮8次请求，追加IT/BR样品各1次，共10次HTTP200/code0，490条按请求计算的字段路径观察。追加样品响应只有data.total而无明细，尚未证明IT/BR样品行字段与MX兼容。返回字段形状已保存，未经筛选的响应及身份凭据不保存。三市场身份文件前后哈希一致。

本轮不发送、不创建/删除链接、不加入活动、不审批样品、不启动旧同步/监听任务，不写旧数据库。现场只读请求与本地文档/证据写入分开。响应中有联系信息字段，但实际联系方式没有进入报告。

此前本任务MX三个会话的10条橱窗通知原始HTTP读取、IT画像及IM只读结果作为带日期的既有证据引用，不重复计入这10次请求。旧库中的sent、历史页面和代码可运行均不能替代本轮写入验收。

本轮续查新增10次请求：9次成功、IT类目树业务码10000未通过。累计20次请求、19次成功、652条按请求计字段路径；详见字段字典。新增发现的IM关系权限返回与批量身份映射已在MX实测，其他网页SDK候选仍分层记录。

## 证据状态

1. `source_only`：路径/调用/字段在代码中存在，不证明远端当前可用。
2. `historical_ui`：旧页面曾展示，不证明HTTP合同、所有字段或当前权限。
3. `live_read_sample`：明确机构/账号/市场/时间和样本；不表示全量覆盖。
4. `write_verified`：须有独立批准范围及逐项真实回读；本轮新增0。
5. `agent_registered`：有类型化工具、范围控制与业务测试后才可设置；本手册中的新工具目前均为设计。

字段另区分absent/null/empty/present/invalid；历史最新观察与当前平台状态不是同一概念。注册表记录接口技术状态，运行批准仍在应用策略层，不把文档状态当开关。

## 身份、认证和配额

- 登录账号ACC是资源身份；机构、市场、远端IM发送者、TAP/MCN角色是不同维度。用户确认500新达人额度按机构×市场，不按ACC叠加。
- 业务键：关系用机构×市场×OEC；商品用市场×PID；合作方案再加Campaign/列表/佣金版本；会话CID必须回查OEC归属。
- MX、BR目前源码使用SG Partner API host；IT为EU Partner host。IM host应以本账号token返回并核对预期区域，不能把网页host直接当IM host。
- info→市场与机构身份→im/id/get→im/token/get；Token仅在内存，过期重新认证不恢复未知写入。
- `partner_id`、`aid`、市场参数、同账号Cookie/设备/签名由宿主构造，不从模型参数读取。Find/Profile签名与Partner免签读取属于不同传输，不套用。
- 当前未发现已验证的“剩余500名额/每达人5条余额/滚动重置时间”直接查询合同。`campaign_ongoing_quota_map`是活动相关计数，绝非IM配额；`s:is_stranger`仅为消息观察信号，非充分额度证明。
- 本机账号维护/租约与TikTok认证失效分开，Cookie文件时间不是平台有效期。新系统不得因readiness失败自动换账号规避机构市场额度。

## 分域请求与数据语义

### 1. 达人发现、画像、关系

`4partner/find` POST：query、query_type、分页及filter参数由`bdhub/enrich/client.py`定义；精确handle只有唯一精确命中才绑OEC。相似达人query_type=1000为已存在的扩展路线，不等于精确发现，也不作为V1二发同品依据。

`4partner/profile` POST：creator_oec_id、profile_types。金额取精确值与货币，不解析格式缩写。授权占位、字段缺失、观察窗口冲突都必须保留。旧源码当前creator_profile.py默认[2]与项目较早文档[1,2,6]存在口径差异；本轮只记录，不改旧系统或擅选生产策略，接入前核对实际调用入口和当前部署。

`relation/list` GET：status、page_size、cur_page、related_biz_types、relation_tag；这是机构绑定关系，不是所有可营销达人。IM mget可产生当前OEC/handle及授权状态，本轮已从历史网页callsite补明请求体并完成MX三人批量只读核验，见TK-013。

### 2. 活动货盘

`campaign/list` GET：cur_page/page_size、campaign_join_status_category/crs_campaign_types。样本包含data.campaign[]、total_num、加入状态计数、活动进行中计数、new_products_count、seller_campaign_extra佣金区间。新增商品数可触发增量复查，不能当作完整PID变更列表。

`campaign/product/list` GET：campaign_id、cur_page/page_size、marked，按需product_status或product_id。完整分页发布新版本之前保留上个完整版本。活动报名期与推广生效期分开，用户>45天条件针对可推广期限，不取报名截止。

`opportunity_product/list` POST：filter、page/page_size、product_id；全球销售来源使用源码已验证的campaign_type过滤，不能仅凭第三方商品字段判断。`campaign_detail` GET带product_id，返回同PID多方案；必须保存各方案，不能拼出不存在的“最高佣金+最晚日期”。

`pick_up/list` POST：cur_page/page_size、filter、按需product_ids。样本data直接是数组；不同市场字段可缺失。已选商品不等于已有可发商品卡。

### 3. 佣金、链接与卡

`campaign/product/link`是GET创建、`campaign/product_list/create`是POST创建，两者都是写动作且不同，具体冻结PID/Campaign/达人点位及来源路线见旧sharelink与taplink实现，不能将URL/Plan与商品列表ID视为同一实体。

`im/product_list/list` GET返回可发卡列表；`campaign/product_list/products` GET按list_id、source、cursor读取成员，返回campaign_products、total_num、next_cursor。当前IT单品样本补验成功。

佣金字段常以百分之一百分点存整数串，需按具体合同转成百分点；库存也是字符串，不能做字典序比较。creator/plan/partner/total与ads commission分开，partner字段语义不可只凭名称认定机构净收益。Boost仍不作为自动答复内容。

卡的wire Campaign可能为0，成员来源Campaign另存；已验证的IT例子如此，不用来源Campaign替换发送协议值。

`product_list/list`及members用于清理只读核验，delete为独立写动作。混合有效/失效成员不能删除整个列表；明确失效停止使用与平台物理删除分开。用户目前认可的保留历史规则覆盖通用清理建议。

### 4. IM与附件

Protobuf203初始化、608会话核验、301历史读取；100发送、JSON get-or-create会话属于写入。本轮不执行100/create。读会话也不能隐式标已读或发确认回执。

保存server_id、CID/OEC、sender_role、is_from_me、create_ms、消息type、结构化扩展和来源版本。保留我方人工消息用于接管判断。history方向/anchor/分页需按协议核对，首屏不称全量；序号、返回cmd、业务码要一致。

本任务已验证的橱窗通知：type=notification、sender_role=3、starling_content_key=ttspc_im_message_relation_ststem_message_6_plural；扩展无显式PID，保留不透明字段的未知状态。不要靠西语关键词识别所有市场，也不要靠最近一张卡猜PID。

现有monitor具备media提取逻辑但新图片管线未接；附件原件读取应沿合法本账号渠道，图片在本方保管、按任务提供模型，不给模型Cookie或带认证信息的下载地址。图片理解不证明所截图条件仍有效。连续文本最后一条后60秒合并，生成中版本变化需重判。

### 5. 样品与商家服务

`sample/records/list` POST首屏本轮实际验证：search_params[{search_key:28,search_type:1,value:'2'}]、order_params[{order_key:9,order_type:2}]、page_size:5、cur_page:1。2为旧代码已发货枚举；不穷举未确认状态值。返回sample_records[]、total_num/状态汇总以实际字段字典为准。

关键关联为apply_id/sample_main_id、creator_info.oec_id、product_info.product_id、campaign_info.campaign_id。is_product_in_showcase、is_posted、video_count、room_count仅描述该可见样品记录的上下文。达人历史样品可能来自其他商家/机构，查不到不能说没有样品，查到也不能证明采用本次二发链接；不得输入二发采用或额度解锁判定。

商品seller_contact_info和活动contact_info是不同角色联系方式；先核实商家/活动联系人及允许用途，不能把达人私人电话或机构联系人误当卖家。新工具仅返回当前事项需要的商家联系路径，不批量给模型完整联系资料。

sample/budget/review为审批写入，sample/price已从历史SDK补明GET方法，但仍无足够证据确认参数与可复用请求。用户本轮索样/损坏规则是联系商家，不因此接通自动审批、补寄、赔偿。

### 6. TAP/MCN报表与内容

现有合同在report_sync/contracts/mx_v1.json：导出路径`/api/v1/insights/partner/report/data/list/export`，MX TAP biz_role=7、dimensions=[5,4,2,1,7]；MCN biz_role=2及另一组dimensions。下载是`/api/v1/insights/export/file/`前缀下的服务端返回定位符，不能猜文件ID。导出可能建立平台文件任务，不归类成无副作用GET，本轮未发起导出。

当前规范支持日期/达人handle/PID/Campaign/店铺、GMV各归因类别、订单件数、预计/实际佣金、已结算/退款/橱窗指标；不能将有视频GMV列当已保存video_id。

旧2026-09-09页面曾显示活动×达人×商品×视频联合报告、视频ID/时间及订单。当前parser未完整映射这些身份字段；新增内容合同需实录请求、维度、分页与联合主键，再小样对账。不要猜`/data/list`是现有export去掉后缀就能调用。

报表名称可能只有handle，必须有带时间身份链才关联OEC，避免改名/重名错归。日报延迟1–2天是用户业务经验，保留reported/observed时间与覆盖日期，补查历史而非缺行判未采用。

### 7. 平台更多能力与边界

商家brief/达人偏好/内容形式、广告佣金、活动奖励、物流、视频明细、MCN邀请/合同等列入发现清单，不在V1直接扩成自动服务。尤其用户暂不回答Boost，采到奖励字段不代表要在自动回复中启用。

账号余额、额度重置、通用达人个人定邀佣金、指定链接实时采用、补寄样品、资金退款等尚无已验收通用查询/动作，不发明接口。后续在已登录官方页面按明确对象观察原生请求后再建合同，不枚举或猜测路径。

## 持续维护与复用

每个新能力必须登记：ID/业务用途、host与角色市场、方法/路径、参数来源、请求样例（占位身份）、副作用、成功/错误合同、分页停止、字段路径/类型/单位/时间/空值、身份键、权限、重试/unknown处理、源指纹、样本证据、Agent接入状态、负责人/下次验证。

离线盘点：在新repo运行 `python3 scripts/audit-tiktok-interface-sources.py --output var/tiktok-source-inventory-next.json`。与已存指纹比较，人工审查差异后更新注册表，不能扫描发现路径就自动发请求。

有授权的单页远端核验：用旧兼容Python运行新脚本 `scripts/probe-tiktok-field-shapes.py --output var/tiktok-field-survey-NEW.json`，默认固定8个只读调用；可用--markets和--only缩小到已登记只读操作，输出不覆盖历史。它不代表已配置周期任务；对代码/账号/市场发生变化要先审查白名单。验证前按需要核查身份/维护，遇权限/验证错误停该市场，不跨账号补冲。

字段变化处理：保存新版本形状→区分缺失与空样本→只暂停依赖字段的功能→重复核对有效样本→更新转换与合同测试→再发布工具。不要因新增字段使所有服务停机，也不能把必需身份字段消失当兼容成功。

工具发布前需要正常/空/权限不足/身份冲突/过时/限流/结果未知的契约测试；写动作还须单独小样实证与业务授权。本文没有自动建立监控、启用Agent或新增付费模型调用。


生成分类与字段文档：审查并更新endpoint-registry.json及field-observations后，运行 `python3 scripts/render-tiktok-interface-docs.py`，同步生成用途目录/路径表/字段字典，避免多份说明漂移。完整静态候选见web-bundle-candidates.json，只有已审查项进入业务注册表。


## 后续实施补验：限定创建（2026-09-13）

此前只读调查之后，在用户继续准备4款匹配佣金商品卡的范围内，TK-005已完成4次单项创建与独立回查。能力仍为canary；未发送消息、未开放批量。见[实施证据](../../implementation/second-cycle-card-creation-v1.md)。该阶段与前文最初只读调查的0写入统计分开。

2026-09-13补充：用户确认未解锁达人消息额度会按月刷新，刷新锚点尚未探明。IT会话203分页已采集104条元数据，301新增field10=create_time观察（field4是索引）；历史累计我方5条不作当前额度耗尽证明。详见[会话与额度核验](../../implementation/second-cycle-conversation-index-v1.md)。

真实IT卡文回查补充：同一已验证IT会话内可能存在shop_region=TH的旧消息。目标回执需按精确请求ID/消息ID定位后核验其市场、身份、可见性和内容；整页仍需同CID。不可因无关旧消息市场字段异常而重复发送已接受的目标消息。实测与回归见[执行闭环](../../implementation/second-cycle-execution-and-scheduler-v1.md)。

Campaign 商品卡实证补充：IT列表父层可提供campaign_id，而成员商品行省略该字段。只有父层精确匹配且按该listId/source=1取得成员时，才允许继承父活动；已选来源wire Campaign=0不得采用该推断。创建与回查证据及回归见[按需素材](../../implementation/second-cycle-material-supply-v2.md)。
