# Kalodata 意大利 PID GMV 实时口径探查

日期：2026-09-20。目标：验证同一意大利市场 PID 的达人 GMV 是否应使用统一币种，以及历史回执中多种货币符号的真实含义。全程只调用达人列表读取接口，没有 TikTok 写入、发送、选入或建链。

## 探查范围

固定接口：`/product/detail/creator/queryList`。请求使用与生产线索任务相同的 14 天、T−2 结算窗口、`revenue DESC`、第一页 50 条。请求字段只有：

```text
authority, endDate, id, pageNo, pageSize, sort, startDate
```

请求没有 `market/country/currency/region` 参数；响应达人行也没有 ISO 币种、市场、国家或区域字段，只有 `revenue / video_revenue / live_revenue` 的格式化金额。

## 实时结果

选择两个历史回执中币种混杂的 PID 复测：

| PID | 历史本机回执 | 2026-09-20 实时第一页 |
| --- | --- | --- |
| `1729719436587473640` | 66 行：`฿` 50、`₫` 16 | 50 行：总GMV/视频GMV/直播GMV均为 `€` 50/50 |
| `1729769397156420420` | 51 行：`RM` 50、`₱` 1 | 50 行：总GMV/视频GMV/直播GMV均为 `€` 50/50 |

第一 PID 的新旧集合进一步按不公开的 Kalodata creator ID 与 handle 交叉核对：实时 50 行全部存在于历史 66 行中，ID 重合 50/50、handle 重合 50/50。即达人集合没有换成另一个市场；变化的是同一批金额的显示币种/会话口径。

## 身份恢复与探针修复

第一次实时读取返回 `kalodata_auth_required`，说明此前页面的 `ready` 是旧探测结果。专用登录器刷新最初因旧 BDHub 配置路径 `kalodatagrab` 与实际本机目录 `kaladatagrab` 相差一个字母而找不到 launcher；没有修改旧配置，而是在新项目适配层中改为“配置目录存在则使用，否则使用已验证本机 checkout”。

刷新后 Cookie 文件时间与大小发生变化，随后真实读取成功；刷新 Chrome 已关闭。健康探针同时修正两点：列表型响应能正确计数，探测窗口与生产统一为 T−2。最终探针为 `ready / identityOk=true / rows=50`。

## 结论

1. 用户判断成立：在当前意大利 Kalodata 会话中，同一 PID 的 GMV 展示使用统一欧元口径。
2. 历史 `revenueRaw` 的混合符号不是达人集合跨市场，而是采集时会话展示币种不同；不同采集批次的格式化金额不能直接转数值后横向比较。
3. 当前 `sourceRank` 仍可用：Kalodata 在同一次请求中以统一展示口径按 `revenue DESC` 排序，系统保存平台给出的名次，不跨会话比较原始金额。
4. `source_edge.currency='EUR'` 只能说明当前意大利业务口径，不能反向证明旧 `revenueRaw` 已是欧元。若后续需要跨 PID 使用绝对 GMV，必须在采集时固化可验证的币种/会话口径并刷新旧数据，不能对旧混币字符串直接换算。

本轮只做少量有界单页读取，用于认证、两个 PID 币种复测、集合交叉核对和健康探针校准；没有批量刷新现有 1,150 个 PID，也没有改写线索池。
