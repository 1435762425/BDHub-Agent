# 按业务用途查TikTok接口

每项说明用途、输入、能取到的数据、覆盖边界和证据。样品记录不用于二发历史样品来源、链接采用或额度解锁。

| 分类 | 解决的问题 |
|---|---|
| 账号与认证 | 机构、市场、IM身份与临时认证 |
| 达人身份与关系 | handle/OEC映射、画像、绑定与合作类别 |
| 货盘与选品 | 商品、活动、库存、期限、佣金条件 |
| 货盘写操作 | 加入活动、选入商品 |
| 链接与商品卡 | 准备链接、查询可发卡、维护列表 |
| IM消息 | 会话、收信、补回、发送和核验 |
| 经营报表 | 本机构归属GMV、订单、达人/PID、内容指标 |
| 样品服务 | 本机构可见样品事项；不证明二发采用 |

## 账号与认证

### TK-014 获取机构市场IM身份

`GET /api/v1/affiliate/partner/im/id/get`

- **输入：** user_id=已核验market_id、type及机构身份参数
- **可取得：** im_id
- **业务用途：** 给会话读取/发送认证绑定正确机构市场身份
- **不能据此判断：** 不是达人OEC；多个ACC可能返回同一IM身份
- **副作用：** business_read；**证据：** 源码/历史脚本；未重验

### TK-016 获取IM临时Token与服务地址

`GET /api/v1/affiliate/partner/im/token/get`

- **输入：** im_id与本账号机构参数
- **可取得：** token、api_url、app_id、服务配置等，仅在内存
- **业务用途：** 建立正确IM host的认证会话
- **不能据此判断：** Token和设备字段不入模型、文档或日志；不证明发送权限
- **副作用：** business_read；**证据：** 源码/历史脚本；未重验

### TK-017 读取机构与市场身份

`GET /api/v1/affiliate/partner/info`

- **输入：** partner_type、partner_id、aid及同账号认证
- **可取得：** partner_biz_role_info.market_list、market_id/type_list、公司与角色信息
- **业务用途：** 确定机构×市场×角色映射
- **不能据此判断：** 市场可见不等于所有动作可用
- **副作用：** business_read；**证据：** 源码/历史脚本；未重验

## 达人身份与关系

### TK-013 IM达人映射响应

`POST /api/v1/affiliate/partner/im/filter/creator/mget`

- **输入：** creator_ids:[{creator_oec_id,conversation_id,可选creator_im_id}]；partner_id由同账号宿主提供
- **可取得：** data[]：OEC、creator_im_id、conversation_id、creator_type、handle/nickname/avatar的value/status/is_authorized
- **业务用途：** 补IM身份、核对当前名称与授权状态
- **不能据此判断：** 授权字段可能缺失；IM身份映射不证明实际合作、样品来源或发送额度
- **副作用：** business_read；**证据：** mx：成功读取

### TK-022 查询机构绑定关系

`GET /api/v1/affiliate/partner/relation/list`

- **输入：** status、cur_page/page_size、related_biz_types、relation_tag
- **可取得：** data.relations（有样本时）：关系、creator_info、OEC/handle等
- **业务用途：** MCN等绑定关系服务、身份来源
- **不能据此判断：** 不是全部二发达人池或全部回复达人
- **副作用：** business_read；**证据：** mx：成功读取

### TK-027 按handle精确发现或显式相似搜索

`POST /api/v1/oec/affiliate/creator/marketplace/4partner/find`

- **输入：** query、query_type、分页/filter；签名由宿主生成
- **可取得：** creator_profile_list中的OEC、handle及轻量画像
- **业务用途：** Kalodata handle→OEC；相似达人只作其他探索
- **不能据此判断：** 相似模式不是精确身份；不能取第一条替代精确匹配
- **副作用：** business_read；**证据：** 源码/历史脚本；未重验

### TK-028 按OEC获取画像

`POST /api/v1/oec/affiliate/creator/marketplace/4partner/profile`

- **输入：** creator_oec_id、profile_types
- **可取得：** creator_profile及分型指标；指标值/币种/类目/授权与时点
- **业务用途：** 画像更新、带货和类目排序、改名继续跟踪
- **不能据此判断：** 不证明本人持有样品或接受合作；缺值不能填0
- **副作用：** business_read；**证据：** 源码/历史脚本；未重验

### TK-034 查询指定达人与本机构的IM合作类型及权限标记

`GET /api/v1/affiliate/partner/im/collaboration/get`

- **输入：** creator_id（绑定OEC），机构账号参数由宿主提供
- **可取得：** 响应顶层collaboration_type数组、has_permission布尔；不在data内
- **业务用途：** 补关系类别/联系资格调查依据；SDK据其显示陌生、绑定、已合作类别
- **不能据此判断：** 尚未证明has_permission与500/5条额度完全等价；不能推导配额重置时间
- **副作用：** business_read；**证据：** mx：成功读取；mx：成功读取；mx：成功读取

## 货盘与选品

### TK-001 读取商品类目树

`POST /api/v1/affiliate/lux/product/category/childrenv2`

- **输入：** category_id、status_param.region
- **可取得：** category_infos：category_id、name、is_leaf
- **业务用途：** 统一商品类目；辅助短名称与归类
- **不能据此判断：** 不是达人兴趣画像；错误响应不当空类目
- **副作用：** business_read；**证据：** it：拒绝/未验收

### TK-002 查询可见活动及加入状态

`GET /api/v1/affiliate/partner/campaign/list`

- **输入：** cur_page/page_size、加入状态/活动类型过滤
- **可取得：** campaign[]、total_num、推广/报名时间、佣金区间、new_products_count、活动配额计数
- **业务用途：** 活动发现、每日货盘刷新入口、增量刷新优先级
- **不能据此判断：** 活动quota不是IM配额；一页不是全量
- **副作用：** business_read；**证据：** mx：成功读取；it：成功读取；br：成功读取

### TK-004 读取某活动商品

`GET /api/v1/affiliate/partner/campaign/product/list`

- **输入：** campaign_id、marked、cur_page/page_size、可选PID/状态
- **可取得：** campaign_products及分页总量，商品/库存/评分/佣金/样品与活动字段
- **业务用途：** 形成全部货盘、按>45天/>100库存等规则筛选
- **不能据此判断：** 样品额度不作为二发必需；旧默认筛选不覆盖新规则
- **副作用：** business_read；**证据：** 源码/历史脚本；未重验

### TK-018 查询某PID可用活动方案

`GET /api/v1/affiliate/partner/product/opportunity_product/campaign_detail`

- **输入：** product_id
- **可取得：** data.product_campaign_detail[]；具体campaign/commission/期限/状态以字段字典为准
- **业务用途：** 比较同品多方案，给规则选品与建链供给
- **不能据此判断：** 不是达人在其他卖家的私人定邀点位
- **副作用：** business_read；**证据：** it：成功读取

### TK-019 搜索高机会/全球销售商品

`POST /api/v1/affiliate/partner/product/opportunity_product/list`

- **输入：** filter.product_source/campaign_type/label_type/product_status、page/page_size、可选product_id
- **可取得：** data.products、has_more及产品候选字段（空样本不证明详情合同）
- **业务用途：** 从平台供给找PID；全球销售独立路线
- **不能据此判断：** 不是Kalodata；不能把所有高机会商品都当全球销售
- **副作用：** business_read；**证据：** it：成功读取

### TK-020 查询已选商品

`POST /api/v1/affiliate/partner/product/pick_up/list`

- **输入：** cur_page/page_size、filter、可选product_ids
- **可取得：** data[]中的campaign_product、campaign_info、has_free_sample
- **业务用途：** 维护已选池，核对现有方案与库存
- **不能据此判断：** 已选≠已建商品列表≠已发送
- **副作用：** business_read；**证据：** mx：成功读取；it：成功读取；br：成功读取

## 货盘写操作

### TK-009 审核/加入活动类动作

`POST /api/v1/affiliate/partner/campaign/review`

- **输入：** 旧CommerceTransport调用的review参数；具体场景须核验
- **可取得：** 原生业务回执，当前未实测字段
- **业务用途：** 供给流程指定活动操作
- **不能据此判断：** 不可作为活动只读详情接口；不与seller_requested/review混用
- **副作用：** external_write；**证据：** 源码/历史脚本；未重验

### TK-012 加入商家发起活动

`POST /api/v1/affiliate/partner/campaign/seller_requested/review`

- **输入：** campaign_id、is_joined、contact_info.campaign_contact_info
- **可取得：** 加入业务结果，需活动列表回查
- **业务用途：** 按规则扩充可获取货盘的活动来源
- **不能据此判断：** 不是读取活动，也不自动继承全部商家承诺
- **副作用：** external_write；**证据：** 源码/历史脚本；未重验

### TK-021 选入商品

`POST /api/v1/affiliate/partner/product/pick_up/select`

- **输入：** product_id、campaign_id
- **可取得：** 单次选入结果，需已选列表回查
- **业务用途：** 全球销售等路线的卡片供给准备
- **不能据此判断：** 结果未知不重发；不是商品搜索
- **副作用：** external_write；**证据：** 源码/历史脚本；未重验

## 链接与商品卡

### TK-003 创建普通高佣ShareLink

`GET /api/v1/affiliate/partner/campaign/product/link`

- **输入：** product_id、campaign_id、creator_commission_percent
- **可取得：** 平台创建结果与链接/方案信息；完整字段按旧create_link及实测回执核对
- **业务用途：** 生成某PID某方案的分享链接
- **不能据此判断：** GET有真实写入；不等于ProductList，不自动变成可发卡
- **副作用：** external_write；**证据：** 源码/历史脚本；未重验

### TK-005 创建商品列表/TapLink

`POST /api/v1/affiliate/partner/campaign/product_list/create`

- **输入：** name、campaign_id、items[{product_id,creator_commission_rate}]；selected路线source=2且外层campaign_id=0
- **可取得：** data.list，url及product_list_id/id/list_id候选
- **业务用途：** 为已选商品或活动商品准备可发卡列表
- **不能据此判断：** 写入成功不等于IM已可查卡；来源Campaign与wire Campaign分开
- **副作用：** external_write；**证据：** 源码/历史脚本；未重验

### TK-006 删除商品列表

`POST /api/v1/affiliate/partner/campaign/product_list/delete`

- **输入：** list_id
- **可取得：** 删除请求结果，仍须原来源回查
- **业务用途：** 有明确范围的失效列表清理
- **不能据此判断：** 真实删除；不能因近期无点击就删除有效或仍产佣链接
- **副作用：** external_write；**证据：** 源码/历史脚本；未重验

### TK-007 盘点既有商品列表

`GET /api/v1/affiliate/partner/campaign/product_list/list`

- **输入：** campaign_id、source、cur_page/page_size
- **可取得：** data.lists：id、name、url、total、update_time；data.total
- **业务用途：** 看现有链接、清理候选、检测变化
- **不能据此判断：** 列表计数不等于GMV/点击/达人采用人数
- **副作用：** business_read；**证据：** it：成功读取

### TK-008 读取列表的商品成员

`GET /api/v1/affiliate/partner/campaign/product_list/products`

- **输入：** list_id、source、cursor
- **可取得：** campaign_products：PID、Campaign、creator/plan/partner/total点位、stock、status、sample_quota；total_num/next_cursor
- **业务用途：** 精确卡片方案核验、发现失效成员
- **不能据此判断：** 不据列表存在判断达人已加入橱窗
- **副作用：** business_read；**证据：** it：成功读取

### TK-015 读取IM可发送商品卡列表

`GET /api/v1/affiliate/partner/im/product_list/list`

- **输入：** cur_page/page_size、version=1、search_type=2、key_word=PID（按当前已验搜索合同）
- **可取得：** data.list：product_list_id、列表名称、campaign_id、campaign_products；data.total
- **业务用途：** 验证PID和列表是否可用于IM卡片
- **不能据此判断：** 不能用product_id参数猜测等效搜索；查到不是已发送
- **副作用：** business_read；**证据：** it：成功读取

## IM消息

### TK-024 获取或创建会话

`POST /api/v1/im/conversation/create`

- **输入：** participants、options、biz_hook_ext，由已核验机构市场与OEC组装
- **可取得：** 候选CID及创建结果，后续608核验
- **业务用途：** 没有本地CID时取得可用会话
- **不能据此判断：** 可能创建远端会话，有写副作用，不能当纯查询
- **副作用：** external_write；**证据：** 源码/历史脚本；未重验

### TK-029 读取会话历史

`POST /v1/message/get_by_conversation`

- **输入：** cmd301、full/short CID、类型、方向、anchor、数量
- **可取得：** MessageBody：serverID、发送者、content、type、ext、时间/索引
- **业务用途：** 收信补回、关系上下文、发送结果核验
- **不能据此判断：** 首屏非全量；不可仅正文相似认定是某次发送
- **副作用：** business_read；**证据：** 源码/历史脚本；未重验

### TK-030 按消息ID回查

`POST /v1/message/get_by_id`

- **输入：** 旧build_readback组装的cmd/消息ID/会话参数
- **可取得：** 单消息查询候选；实际可用性待按旧失败记录复验
- **业务用途：** 指定消息核验候选路线
- **不能据此判断：** 不能保证所有市场支持；读取失败不能触发重发
- **副作用：** business_read；**证据：** 源码/历史脚本；未重验

### TK-031 发送文字或商品卡

`POST /v1/message/send`

- **输入：** cmd100、固定会话/ticket、sender、client UUID、content/type/ext
- **可取得：** 候选平台消息ID与状态，必须后续回查
- **业务用途：** 二发卡→文、已批准AI回复的最终执行
- **不能据此判断：** 真正外部发送；未知保留，禁止整组重发
- **副作用：** external_write；**证据：** 源码/历史脚本；未重验

### TK-032 核验指定会话身份与ticket

`POST /v2/conversation/get_info`

- **输入：** cmd608、CID与预期OEC
- **可取得：** ConversationInfoV2、ticket、core.ext中的creator_oec_id等
- **业务用途：** 防止发错人，恢复已有会话
- **不能据此判断：** 会话存在不证明消息已发或额度已解锁
- **副作用：** business_read；**证据：** 源码/历史脚本；未重验

### TK-033 初始化/按用户取会话与消息

`POST /v2/message/get_by_user_init`

- **输入：** cmd203、身份Token、游标及SDK固定字段
- **可取得：** 会话、消息集合、游标/has_more；取决于body字段
- **业务用途：** IM初始化与补回入口
- **不能据此判断：** 初始化成功不是持续监听上线，不能当完整收件箱
- **副作用：** business_read；**证据：** 源码/历史脚本；未重验

## 经营报表

### TK-025 下载已生成报表

`GET /api/v1/insights/export/file/`

- **输入：** 平台返回的文件定位符（路径前缀，不拼猜）
- **可取得：** 报表文件字节，格式与表头需核验
- **业务用途：** 导入TAP/MCN指定日期和维度报告
- **不能据此判断：** 下载成功不等于解析完整，不自动算本次发送贡献
- **副作用：** business_read；**证据：** 源码/历史脚本；未重验

### TK-026 请求TAP/MCN导出文件

`POST /api/v1/insights/partner/report/data/list/export`

- **输入：** 角色biz_role、维度dimensions、日期/过滤、pagination；MX既有合同
- **可取得：** 导出任务/文件定位符，随后下载
- **业务用途：** 本机构TAP归属GMV、达人/PID等日报
- **不能据此判断：** 可能建立远端导出作业；现有视频指标不等于含video_id
- **副作用：** remote_export_job；**证据：** 源码/历史脚本；未重验

## 样品服务

### TK-010 批准或拒绝样品申请

`POST /api/v1/affiliate/partner/campaign/sample/budget/review`

- **输入：** campaign_id、平台creator_id、product_id、is_approved、可选payment
- **可取得：** 审批业务回执，需重查申请生命周期
- **业务用途：** 仅处理可见并有权限的具体样品申请
- **不能据此判断：** 不是给任意二发达人补寄；creator_id不随意换OEC
- **副作用：** external_write；**证据：** 源码/历史脚本；未重验

### TK-011 样品价格相关路径候选

`GET /api/v1/affiliate/partner/campaign/sample/price`

- **输入：** 只发现PRICE_PATH常量，方法/参数未确认
- **可取得：** 未知；没有已验证响应合同
- **业务用途：** 暂只列待调查
- **不能据此判断：** 不对Agent开放，不猜参数试写
- **副作用：** unverified；**证据：** 源码/历史脚本；未重验

### TK-023 查询本机构可见的样品记录

`POST /api/v1/affiliate/partner/sample/records/list`

- **输入：** search_params状态、order_params、cur_page/page_size
- **可取得：** 申请ID、OEC、PID、Campaign、样品状态、卖家联系信息、样品上下文橱窗/内容字段
- **业务用途：** 回答该记录涉及的样品问题、准备商家联系路径
- **不能据此判断：** 不能证明达人原来从我们拿样；不能证明本次二发采用；查无记录不等于没样品
- **副作用：** business_read；**证据：** mx：成功读取；it：成功读取（无明细）；br：成功读取（无明细）

## 不是TikTok接口的能力

Kalodata是第三方同品线索来源；60秒合并、模板、人工待办、关系调度、本地配额账本与模型调用是BDHub能力。未确认的剩余额度/重置、精确单次链接采用等见[缺口清单](findings-and-backlog.md)。
