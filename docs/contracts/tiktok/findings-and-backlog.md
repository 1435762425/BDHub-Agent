# 本轮发现、复用与尚待调查

## 对V1最有价值的结果

| 发现 | 实际依据 | 旧系统是否已利用 | 应用方式与限制 |
|---|---|---|---|
| 样品关联提供OEC×PID×橱窗布尔 | 本轮MX sample_records中creator_info.oec_id、product_info.product_id、is_product_in_showcase | 旧parse已映射showcase_added，不是新发现接口；新二发信号层尚未复用 | 为样品范围达人补另一条采用证据；不称全体实时橱窗或本次TapLink精确采用 |
| 商家联系信息与活动联系人分开 | product_info.seller_contact_info与campaign_info.contact_info同时返回 | 旧样品safe_snapshot未保留完整联系路径 | 对应用户“联系商家补样/损坏”场景；先核实角色，仅暴露必要入口 |
| 商家brief和偏好可从响应取得 | seller_campaign_extra.creator_preference、objective、exclusive，样品记录含category_setting/content_format/content_style/notes | 旧页面早有观察，当前样品归一化/货盘投影未完整保存 | 避免Agent猜合作要求；用户已说明形式不作主排序，不能以新字段擅增硬筛 |
| 普通佣金之外还有ads点位 | selected/member/sample出现open_collab_ads_commission_percent、total_ads_commission_percent | 当前商品/卡事实投影主要取creator/plan/total | 分开存为候选业务事实，不自动加到总佣金或回答Boost |
| 活动新增商品与容量计数 | 三市场campaign/list的new_products_count、campaign_ongoing_quota_map | 当前货盘主循环尚未使用完整状态信号 | 可用于刷新优先级；不是500新达人额度，不以新增数量跳过完整分页 |
| 样品内容履约状态 | is_posted、video_count、room_count及creator post_rate等 | 旧样品safe_snapshot已存部分，新Agent未接 | 是否已有内容的有限证据；精确视频与发布时间仍需内容合同 |
| TAP视频联合粒度缺口 | 2026-09-09真实页面记录＋本轮检查parser/JSON合同 | 解析器有视频GMV/播放数但无完整video_id身份映射 | 新增独立内容报告合同，不把旧固定维度CSV直接混入；本轮未重验原生查询接口 |
| 默认画像策略文档漂移 | 本轮creator_profile.py默认[2]，较早runtime-invariants说明[1,2,6] | 属于版本/入口差异待核对 | 不盲照旧描述迁入，源指纹和运行入口必须一起核查 |

## 橱窗及配额专项

此前本任务MX3会话10条系统通知证据位于新var/mx-showcase-review-20260913/report.json。明确type=notification、sender_role=3、稳定starling key，样本s:is_stranger=false；已解码扩展未见PID，field14未解释。不把最近发送商品卡当采用PID。

后续对照应读取同一关系通知前后的消息/会话状态，并与回复、TAP或样品橱窗关联交叉验证。只读调查无法证明发送限额已解除的所有条件，不通过反复发送来试额度；准确重置规则还需官方界面/错误合同证据。

## 已知缺口与最小下一步

| 优先级 | 缺口 | 下一步取证 | 可以据此做什么 |
|---|---|---|---|
| P0 | 市场机构500额度重置/剩余额度及5条计数 | 已登录官方IM原生限制提示/已有响应，不猜接口；与机构ID和计数时区对照 | 配额调度，避免按ACC放大额度 |
| P0 | IM加橱窗其他通知类型与不透明字段 | 小样扩展原生响应/SDK消息schema，保留ID来源 | 可靠跨语言事件识别；不能尚未证实就实现PID归因 |
| P0 | 样品商家联系路径是否可给达人 | 指定商品后台角色及联系入口对照 | 真正可用的联系商家指引，避免错发联系人 |
| P0 | 当前新系统Agent接口未注册 | 将read_offer/read_messages/read_sample_status等逐个封装并离线验收 | 有手脚且不越界的回复Agent |
| P0 | IT/BR样品明细未验 | 本轮已取得code0但只有data.total无明细；需有样本的状态/对象再核对 | 判断是否可迁入，不复制MX枚举/业务权限 |
| P1 | 内容联合报告请求及粒度 | 从官方已有联合查询原生请求建立合同，验证维度/分页/身份 | 追踪再发视频与合作GMV |
| P1 | 物流与售后真实接口 | 样品详情原生请求及状态链；不能只猜main_order_id下游路径 | 更准确判断寄送/损坏事项；不代表允许赔付 |
| P1 | 全部消息附件类型和读取 | 旧media解析与有授权截图小样端到端测试 | DeepSeek看图，失败交人；不做语音 |
| P1 | 货盘方案更新与列表删除差异 | 明确对象的生命周期/回读合同和版本对照 | 周期维护而不损害有效链接 |
| P2 | 奖励/MCN/合作创建/官方Open API | 既有官方页面或授权文档，按用户业务范围再展开 | 作为未来能力候选，不扩张V1回复权限 |

这些是尚未完成的具体调查项，不用“查不到”写成平台不支持。当前手册完成已有模块盘点与本轮单页字段核验，不能宣称TikTok后台所有隐藏接口已穷尽。

## 证据与维护原则

源码观察、历史UI、当前远端样本分别标记。原始权限、秘钥、Cookie、完整私信和联系方式不放进长期说明文档；字段字典记录路径、类型、样本范围和时间。需要业务值例子时使用合成占位，保留准确字段单位，不把模拟数据标实测。

字段首次看见后先判断能否改变具体业务决定，再决定是否进数据模型；不因字段多就全部装进Agent上下文。每次协议变化保存差异与新样本，保留原证据而非覆盖昨日事实。
