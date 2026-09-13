# 官方网页脚本补充发现

继续调查旧版保存的官方网页脚本，发现Python路径扫描没有覆盖的动态接口定义。源文件为旧`outputs/im-http-probe-20260906/73c6c8af85a24be7ce885c006339808303f71ba9a94f2647140997bac1e54676.js`，保存日期属于历史证据；不执行该JS。

提取283条去重的默认v1路径，含既有接口、机构侧、达人侧及商家/通知等共享SDK定义。它们**不是283个当前已可调用的新接口**。全量名称、方法表达式、字节位置和SHA见[SDK候选目录](web-bundle-candidates.json)。未确认参数/权限/副作用的路径不做自动请求。

## 已补验的两个缺口

| 接口 | 参数与返回 | 本轮结果 | 可以用来做什么 |
|---|---|---|---|
| GET `/api/v1/affiliate/partner/im/collaboration/get` | creator_id；顶层collaboration_type数组、has_permission布尔 | MX同机构3位已知达人，3次HTTP200/code0；记录字段形状，尚未建立所有枚举/状态对照 | 查看SDK使用的陌生/绑定/合作类别和权限信号，进一步查联系资格 |
| POST `/api/v1/affiliate/partner/im/filter/creator/mget` | creator_ids数组，项含creator_oec_id、conversation_id、可选creator_im_id；机构参数由宿主提供 | MX批量3位，HTTP200/code0，返回3条身份行 | 映射OEC/IM ID/CID、当前handle/nickname及授权状态，避免错误名称回填 |

collaboration响应不包在data里。现探针旧版只尝试从data提取具体permission值，保留的证据只证明顶层字段和类型；代码已修供后续探针使用，本轮不捏造具体权限值。SDK将collaboration_type用于界面关系标签，但枚举、has_permission与500/5条限制之间的对应仍需有对照样本。mget不证明达人实际带过哪款商品，也不是同意营销的记录。

## 尚未调用的有意义候选

以下方法来自历史wrapper，不代表当前业务可执行；字段说明标明是期望调查对象，未伪造响应字段。

| 业务分类 | wrapper / 路径后缀 | 已确认内容 | 有意义的下一步 |
|---|---|---|---|
| IM数量 | GetIMMsgCount / `partner/im/msg/count` | GET定义，尚无实际调用参数/响应 | 查明是消息统计还是额度；不先称剩余500接口 |
| IM搜索 | SearchIMConversation / `partner/im/conversation/search` | GET定义 | 核对筛选、分页、对话与OEC字段，可能减少全量初始化 |
| 分享资格 | CheckIMProductSharing / `partner/im/product/sharing/check` | POST；callsite给creator_ids、product_id或product_list_id、campaign_id、product_level | 核验product_level枚举、creator_ids结构和检查副作用后，研究分享资格/错误原因 |
| 分享执行 | CreateIMProductSharing / `partner/im/product/sharing/create` | POST定义，与check是不同调用 | 可能产生外部分享；只登记，不当只读预检或替代已批准发送入口 |
| IM商品信息 | GetIMProductInfo / `partner/im/product/info` | GET定义 | 查明商品卡详情与当前方案信息，不能猜个人点位覆盖范围 |
| 图片解析 | GetPartnerImageUrl / `partner/im/image/url/get` | POST；callsite输入images数组、返回data | 核验images元素结构和授权后补图片读取合同；它不等于可以访问任意图片 |
| 图片凭证 | GetPartnerImageXStsToken / `partner/im/image/token/get` | GET定义 | 属于上传/图片服务认证候选，不给模型凭证，不作为普通读图工具 |
| 样品明细 | GetSampleDetail / `partner/campaign/sample/detail` | GET定义，无实际参数合同 | 仅用于本机构可见样品事项，核对申请/履约/物流；不验证二发原样品来源 |
| 样品价格 | GetSamplePrice / `partner/campaign/sample/price` | 本轮补明GET方法，参数/响应/副作用仍待验 | 解决原目录方法未知，不直接开放或调用 |
| 样品统计 | GetCampaignSampleStatistics / `partner/campaign/sample/statistics/get` | GET定义 | 活动供样汇总，不是达人二发采用统计 |
| 样品授权 | sample/authorization相关create/update/get/search | 多读写定义，分属partner与creator路径 | 清楚区分样品授权申请、邀请与审批；用户当前不要求AI自行补样 |
| 商家联系限制 | GetShopIMQuota / `notification/im/shop_quota`；permission/entrance_check | POST定义，属于notification/shop命名空间 | 必须先核对主体；不能移植为TAP机构额度或增加账号绕额度 |
| 达人侧消息 | `partner/creator/im/...` | 共享SDK存在 | 与机构侧分开，不能用机构认证尝试达人私有接口 |

## 供给接口的新增实测

IT既有列表盘点、全球销售高机会首屏、固定PID方案详情、精确PID的IM卡搜索均成功；MX绑定关系首屏成功。可用数据包括列表更新时间、列表商品数量、货盘价格币种/评分/销量/销售地区、open_collab_rate、方案活动期限，以及卡片成员绑定。语义详见[业务分类目录](business-catalog.md)。

IT类目树调用HTTP200但业务码10000，未取得成功字段，不继续重试或绕验证；此项保留未验收。库存、佣金、方案条件仍须发送前按实际对象核验。

## 可持续提取

`scripts/audit-tiktok-web-bundle.py --input <已有官方脚本文件> --output <新目录候选JSON>` 只扫描wrapper，不执行网络或JS。先审查源时间/哈希、方法别名和调用现场，再把有业务价值且已验证的项目晋级至TK注册表。未知字段不能通过模型猜填，候选也不能自动变为Agent工具。
