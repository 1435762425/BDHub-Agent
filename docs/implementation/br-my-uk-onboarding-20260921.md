# BR / MY / UK 市场接入记录（2026-09-21）

本轮把巴西、马来西亚和英国加入 BDHub-Agent 的产品市场注册表、账号生命周期、页面路由、自动运营控制面与货盘读取链。所有市场默认关闭自动运营、持续发送和 Agent 回复；没有从 IT 继承任务、达人、会话或发送授权。

## 账号归属

| 市场 | 通信账号 | 货盘账号 | 货盘能力 |
| --- | --- | --- | --- |
| BR | ACC1 | ACC2 | Campaign；全托不适用 |
| MY | ACC8 | ACC5 | Campaign；全托不适用 |
| UK | ACC11 | ACC4 | Campaign＋全托 |

六个账号均先由新项目自动登录，发布项目自有 browser/HTTP/IM generation，再从旧项目停用。旧项目仅保留 ACC7 ShareLink 与 ACC10 MX 监听；ACC2、ACC5 的旧 ShareLink LaunchAgent 已停用。迁移前检查两个账号均无活动 ShareLink job、无 promotion-site 活动批次、无 `result_unknown`。

## 现场验证

- 三个通信账号均通过机构市场、HTTP、IM 身份和商品卡读取；IM HTTP 只读初始化通过：BR 当前返回 2 个会话，MY/UK 当前返回 0 个，跨市场会话 0，平台写入 0。
- 三个货盘账号均通过 Campaign 读取。BR、MY、UK 各执行 1 个 Campaign 加入 canary，均写入一次并读回为 `completed`，unknown 为 0；之后 `campaign_join` 能力才升级为 verified。
- MY 首轮 Campaign 完整采集发布 70 个 offer；当前筛分 70 个 held、0 个 chosen。
- BR 首轮 Campaign 完整读取 10,509 个 offer、9,116 个 PID；修复旧的 10,000 offer 本地发布上限后复用同一完整读取发布，筛分得到 1,364 个 chosen、7,752 个 held。
- UK 首轮 Campaign 完整发布 50,778 个 offer、14,339 个 PID，筛分得到 1,815 个 chosen、12,524 个 held；全过程 800 个只读请求、平台写入 0。
- UK 首轮类目读取在用户指示后停止并冻结：64,089 个唯一商品、4,280 页、13/30 个一级类目完整，明确发布为 `operator_accepted_partial`，不是完整覆盖；本地筛选合格 7,932、拒绝 56,157。后续独立周更新改为普通不分类目读取。

## BR / UK 正式闭环

- BR 当前标准 TapLink 1,364/1,364 ready；Kalodata 固定 `BR/BRL`，真实线索读取与 OECID canary 完成；葡萄牙语商品卡＋正文真实发送 1 位，卡与文字均 confirmed、unknown 0。
- UK 当前部分快照筛出的 7,932 个商品中，首轮实测选入 400 个 confirmed、1 个网络歧义经两次间隔回读后 `skipped_unknown`，其余保留 pending 供自动工作流继续。验证码拒绝与登录失效只按原冻结意图和明确未选入证据恢复，没有盲重发。
- 用户反馈单 PID 选入慢且频繁验证后，现场检查 UK 当前商品后台与发布前端包：SDK 中存在 `/pick_up/batch_select`，但页面无批选入口，EU v1/v2/v3 后端返回 HTML 而非 API JSON，不能正式使用。首轮先验证 100 PID 共用一个 ACC4 会话、批量读取 listing、批末回读；随后正式优化为每轮 300 PID、8 lanes/8 QPS、100 PID group。真实 300-PID 轮次 300/300 confirmed，约 4 分钟；600-PID 轮次分别结算 598 confirmed＋2 skipped 和 600/600 confirmed，完整吞吐约 61–75 个/分钟。自动 workflow 会先清空当前 `operator_accepted_partial` 的 pending，再执行下一周普通读取；16201010 会自动重登并保留已验证能力。
- UK 400 个已选商品全部生成 `en-GB` 短名并创建 400 张标准 TapLink，全部 ready、unknown 0。实测发现一次 seed 丢标题导致 100 条 PID 名称，已在建链前停止并精确删除；正式合同现拒绝 `source_title=PID` 和包含 PID 的短名。
- UK Kalodata 刷新后固定使用 `GB/GBP`：完成 11 个 PID，得到 6 条当前正销量线索；OECID 为 5 个精确 resolved、1 个明确 unresolved、blocked 0。平台 Find 的地区码 `GB` 已映射为 canonical `uk`。
- UK 真实发送 canary 为 1 位，随后持续发送完成剩余 4 位；共 5/5 位卡＋英国英语正文双确认，unknown 0。BR/UK worker 空池后保持 `waiting_pool`。
- 最终开关：BR=`automatic=true/fullWeekly=false/continuous=true`；UK=`automatic=true/fullWeekly=true/continuous=true`；MY 三项保持 false。UK 当天先发布当前发送池，避免重新抓取；下一次周计划开始执行普通不分类目全托读取。

## 产品与安全边界

- `config/markets.json` 是产品启用市场和 Campaign/全托差异的真值；BR/MY 不显示可操作的全托开关，UK 显示。
- `config/market-content.json` 固定 IT=`it-IT`、BR=`pt-BR`、MY=`ms-MY`、UK=`en-GB`。每个市场分别提供五条二发正文、三条 Agent 固定回复、商品短名 prompt 和独立 TapLink 命名配置；中文翻译只供运营核对。
- TapLink 短名按 `cycle_product_name.locale` 隔离。BR/MY/UK 只接受本市场的 `shortName/mention`，缺少时返回 `link_naming_localized_short_name_missing`；不能读取 `it-IT`、`shortNameIt` 或截断原标题继续建链。
- 页面和本地 plan 按市场隔离。新市场没有数据时展示空状态，不回退到 IT 数据。
- 多市场维护队列保持全局单并发；每市场仍固定通信/货盘两个角色，不自动换号。
- Campaign 自动加入代码已接通，但只有市场首页总开关开启后才进入日常作业。
- TapLink 创建、OECID 与真实消息发送必须逐市场 canary；BR/UK 已验收并开启，MY 仍保持关闭。Agent 回复仍是独立开关，不随自动运营或持续发送自动开启。
- 在必需能力未全部 verified 前，首页禁用自动运营与持续发送开关，服务端也以 `market_automation_capabilities_pending` 拒绝直接请求；市场身份重登不会丢失同账号、同机构已验证的 canary 证据。

最终在线状态备份为 `var/backups/state/20260921T034948Z-post-br-my-uk-final`：28 个 SQLite 数据库、504,868,864 字节、凭据 0，独立 verify `valid=true`。备份策略已纳入三个市场的 Campaign join/screen 台账与 UK 全托 canary 库。

语言合同验证覆盖四市场 language/locale、同 ID 正文不复用、非 IT 不读取意大利语缓存及缺名 fail closed；市场模板与命名 CLI 回读通过。完整代码、构建和页面验证数字以交接页最新记录为准。
