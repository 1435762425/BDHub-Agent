# 意大利 TapLink 标准化补齐记录

日期：2026-09-20。范围：BDHub-Agent 独立账本与意大利 ACC9 TapLink；旧 BDHub 保持只读。

## 执行口径

- 分佣规则：`commission-1-to-2-v1`；
- 命名规则：`link-naming-v1`，`🔥 BJN {short_name} {creator_percent}% {tail}`；
- 唯一当前材料：`catalog_current_binding.currentListId`；
- 历史卡不删除、不复用为新发送材料；
- 创建前完整搜索精确标准卡，符合则直接结算；只有确认缺失才创建；
- 每次创建先持久化 intent，写后按 receipt 的 `listId` 回读；unknown 不另建替代意图。

执行前 SQLite 备份：`var/backups/20260920-taplink-completion/catalog-links.sqlite`，SHA-256：
`aff7632e9863c2b20d5edd80e1c5ead1c64d078c9f7b2889d24dda2e12f5a9e4`。

## Campaign 非全托

第一次补齐基于旧快照完成 448/448 后，又执行了当前平台全量刷新：115 次只读请求取得 6,512 个 Offer，并按当前规则重新筛成 475 个 chosen PID；跨渠道排除 1 个后，最终建链目标为 474 个。

- 最终 run：`catalog-prepare-a1e63b3779f700236ca5b1a3`；
- ready：474，pending/incomplete：0；
- 当前目标精确绑定：474/474，`termsChanged=0`、`none=0`；
- 一个商品的公开佣金从 4% 变为 5%，达人佣金和标准卡名仍为 6%。流程复读到同一张精确标准卡后，只更新当前 Offer 绑定，没有重复平台创建；
- 冗余但从未提交的平台创建 intent 已本地标为 `superseded`，`platformCreateAttempts=0`。

## Selected 全托

旧 run 的 policy fingerprint 为 `a4cca…`，当前规则 fingerprint 为 `d1663…`。因此没有在旧 run 上继续创建，而是以当前配置建立新 run：

- run：`catalog-prepare-c3fe833279c22afb0457c09c`；
- 完整 live selected 条目：3,426；
- 当前不合格并明确 retired：1,129；
- 当前合格方案：2,297；
- 完成后 ready：2,297；
- 精确当前绑定：2,297/2,297，`termsChanged=0`、`none=0`；
- pending/incomplete/unknown：0；
- 4 个旧 `cycle_card_creation` 只处于 `prepared`、receipt/readback 均为空，已以 `platformCreateAttempts=0` 标为 `superseded` 后按当前规则结算；任何 `started/response_saved/unknown` 仍保持阻塞。

本轮所有创建报告合计记录 1,548 次平台创建写入：selected 1,210，campaign 两轮快照合计 338。每条均有本地意图和平台回读；最终 `catalog_link_intent` 为 3,326 verified、11 superseded，prepared/submitted/unknown 均为 0。其余候选通过精确标准卡、已有当前绑定、当前不合格或跨渠道排除结算，没有为凑数创建。

最终发送预览扫描 711 个 ready 位置，可冻结 500；跳过原因只剩达人关系冻结 5 和超出本批规模 206，`standard_link_missing` 与 `standard_link_terms_changed` 均为 0。

## 凭据与模型

- Kalodata 激活码保存在 Git 忽略的 `config/kalodata-identity.json`，权限 `0600`；真实同路径探测结果为 `ready`。
- TypeSafe key 保存在 Git 忽略的 `config/typesafe.json`，权限 `0600`；`GET /v1/models` 已验证可访问 `jev-latest/jev-preview`。
- Jev 固定使用官方 `jev-1.13.0` 和 `POST https://api.typesafe.ai/v1/systemone`，只参与影子分类，不连接回复发送。
- 同一组最近 5 条意大利 turn 已完成 DeepSeek/Jev 对照；`Certo!` 上 DeepSeek=`human`、Jev=`collaboration_ack`，需用户审核决定真值。

## 未授权动作

本轮没有发送 TikTok IM、没有开启 AI 自动回复、没有删除历史链接、没有启动发送批次，也没有修改旧 BDHub。
