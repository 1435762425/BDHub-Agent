# TikTok 达人核心画像长期链路研究

状态：2026-10-11 只读平台实验及离线原型；未合入生产、未导入正式画像、未启用刷新任务。达人画像来源限定为 TikTok。

## 结论

首版保留整体 GMV、成交类目、视频与直播 GMV。Find 除稳定身份外，还返回这些字段。BR、MY、TH 已验证可以通过正常 Find 读取其中允许分享数据的达人；Profile 接口系统错误不代表 Find 没有画像。IT、DE、FR 的按 OECID Profile `[2]` 已通过小样本实测。

UK 原登记账号 acc11 恢复登录后，Find/Profile 返回成功，但已测核心字段为 `unauthorized`。本轮扩测有真实合作记录的达人，仍不能把本系统 `collaborated/auto` 状态视为平台数据授权。

本轮六位合作达人全部完成 Find 与 Profile `[2]`，共 12 次业务请求均为 HTTP 200/code 0，原 OECID 全部一致；四个核心字段均为 `unauthorized`。加上前轮两位，共八位当前真实样本未取得核心字段。BR、MY 各两位再按原身份构造 Find 刷新，四位均重复读取成功、核心字段完整、OECID 一致；不能由这次同日复测宣称已验证跨日时效或永久稳定。

## 当前证据

读取 9,445 份本项目历史 `market-identity-*/output/report.private.json`，按原市场、原账号、成功 Find 回执、原输入、报告摘要和正式身份 observation 核对。时间统一转换为 UTC 后比较，不能用 `Z` 与 `+00:00` 的字符串差异误判。549 份未通过报告合同的文件不计入。

只统计留存报告中已消费 Find 的每位达人最新观测，不能据此推断当前整个市场的完整度。

| 市场 | 正式稳定身份 | 有可追溯历史 Find 的达人 | 核心字段完整 | GMV 可用 | 类目可用 | 视频与直播 GMV 可用 | 至少一个核心字段未授权 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| BR | 3,820 | 2,084 | 2,013 | 2,054 | 2,043 | 2,054 | 30 |
| MY | 8,984 | 4,602 | 2,509 | 2,539 | 4,531 | 2,539 | 2,063 |
| TH | 4,531 | 4,531 | 2,468 | 2,501 | 4,465 | 2,501 | 2,030 |
| UK | 8,331 | 3,692 | 0 | 0 | 0 | 0 | 3,692 |
| DE | 3,008 | 3,008 | 2,694 | 2,965 | 2,694 | 2,965 | 43 |
| FR | 2,136 | 2,136 | 1,893 | 2,117 | 1,893 | 2,117 | 19 |
| IT | 2,939 | 154 | 116 | 150 | 116 | 150 | 4 |

“完整”只指四个原始核心字段均为 `value/zero`，不代表金额正值、类目映射完成或渠道归因完整。IT 此表仅覆盖此类探针，未统计独立 discovery/Profile 路径。BR/MY/TH/DE/FR 最近留存的历史 Find 在 10 月 8–9 日；UK、IT 此类报告最近在 9 月 28 日。

私有证据保存在生产项目 `var/profile-core-tiktok-chain-20261011-160553/`；大样本汇总是 `historical-find-audit.json`，逐达人记录是 `historical-find-latest.private.json`。前轮平台实验保存在 `var/profile-core-probe-20261011-134922/`、`var/profile-core-investigation-20261011-144154/`。

## 长期链路

1. 新身份成功 Find 后，将已经返回的 TikTok 核心画像同步追加到 `identity_observation.payload_json`，沿用 `observe_profile()`、原观测时间及可追溯的 `:find-profile` 证据，不另建画像真相库、不增加 Profile 请求。
2. 历史补齐先作只读预览，必须确认原报告已被原身份台账消费；按原时间追加，幂等重放，不能把旧数据写成今天采集。正式补库尚未执行。
3. 老达人独立刷新，不依赖“是否已解析 OECID”。复用现有画像队列、账号恢复、ProfileLease、lease/fence；画像读取失败不能撤销已确认身份。后台多市场刷新尚未实现。
4. BR/MY/TH 的刷新输入选择当前 handle Find，冻结并核对 `market × OECID`。返回不同 OECID 时拒绝更新原档案；无当前 handle 时显示缺口。IT/DE/FR 继续按稳定 OECID 读取最小 Profile `[2]`。UK 同样可以正常查询，但未授权字段须保留该状态。
5. 刷新节奏建议按经营使用量决定：活跃候选定期刷新，准备匹配时检查时效；未授权不高频重试，仅在明确权限变化后重新核验。具体期限与优先级还需在正式匹配规则中确定。

现有缺口在 `market_identity.py::_apply()`：只持久化 exact Find 身份，没有保存响应画像；同时 `reuse_judgments()` 永久复用身份判定，不能承担画像刷新。仅补入库逻辑不会解决老达人画像过期。

本分支 `scripts/lib/creator_core_profile.py` 是无网络、无持久队列、无正式数据库写入的原型，提供白名单核心事实提取及最多三人的刷新输入。离线测试使用临时身份库验证幂等、最新未授权、原金额精度、零值、错账号/市场/身份拒绝及 Profile 失败保留 Find。

以 8,281 份已归属报告回放此原型，提取 20,212 条 Find 事实，适配器拒绝数为零。原型识别 IT 的 `bdhub.italy-profile-probe.v3` 与其他市场的 `bdhub.market-profile-probe.v1`，不把历史报告格式错当未知结果。针对原型、画像规范化、身份台账及市场报告合同的 46 项离线测试通过；文档路径检查通过。回放摘要 `prototype-replay.json`、UK 扩测 `uk-collaborated-results.json`、BR/MY 重复刷新 `refresh-repeat-results.json` 均在上述私有证据目录。

## UK 正常权限路径

在 Chrome 查看 [Get Marketplace Creator Performance v202608](https://partner.tiktokshop.com/docv2/page/get-marketplace-creator-performance-202608) 最新官方文档，确认：

- 请求需要应用 `seller.creator_marketplace.read` scope、seller OAuth token、`shop_cipher`；最新版使用 Creator Open ID，不能把现有 OECID 直接当 Open ID。
- 返回包含 `gmv`、`video_gmv`、`live_gmv`、`category_ids`、`category_gmv_distribution`、`content_gmv_distribution`，页面声明业绩窗口为近 30 天。
- 文档明确说明达人未允许精确数据分享时，精确 GMV 和视频/直播 GMV 不会返回；区间字段的市场条件也不能擅自套用 UK。
- `category_gmv_distribution.value`、`content_gmv_distribution.value` 采用百分之一百分点；不能直接套用 Find 中的小数权重。渠道枚举包含 VIDEO、LIVE、SHOWCASE。

本项目现有配置和运行代码未发现已接入此 OpenAPI 所需的应用授权材料，未进行真实 OpenAPI 调用。官方文档存在不等于本账号已获得可读字段。

[Creator authorization guide](https://partner.tiktokshop.com/docv2/page/678e3a362dccb8030ea6f98c) 与 [Affiliate integration](https://partner.tiktokshop.com/docv2/page/affiliate-integration) 说明 Creator OAuth 是另一种授权，需要应用 scope、平台审批及达人主动授权。它与本系统合作状态、店铺 seller token 和现有 Partner ID 不能互换；也不能仅凭 OAuth 流程存在，就保证能取得用户要求的全部业绩字段。未创建应用、发授权邀请、联系达人或接受协议。

店铺分析只能证明对应店铺成交；不能用它代替达人整体 GMV。官方 [Creators Analysis](https://seller-uk.tiktok.com/university/essay?knowledge_id=3847903585273632) 对店铺成交区分 Product Card、LIVE、Video。

## 数据口径

- 保留 `value/zero/absent/no_value/unauthorized/error` 六种状态。最新未授权与历史可用值分开，不能用历史值覆盖新状态。
- 原币种符号与精确十进制金额原样保存。不同币种或不同窗口不得直接混合比较。
- 已成功样本提供业绩截止 `2026-10-09T00:00:00Z`，Find/Profile 的窗口起点仍未核实，不能借用 OpenAPI 文档给它补上“近 30 天”。
- 总 GMV 大于零，而视频/直播 GMV 均为零时，不能判定主要成交渠道，也不能未经口径验证把差额强行记作橱窗 GMV。
- 类目 `-1` 或无名称保留未映射，后续商品匹配不得把它当确定的商品类目。
