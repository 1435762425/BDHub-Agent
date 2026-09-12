# 墨西哥二发话术：当前用户原文与历史依据

调查记录：2026-09-12。旧 BDHub 仅作代码/Git 历史和 PostgreSQL 只读调查；数据库查询使用 `SET TRANSACTION READ ONLY`。本记录不包含达人身份、私信明细或凭证，没有执行采集、建链、发卡或发信。

## 1. 当前依据：用户明确提供的 MX 实用文案

**用户本轮提供的文案是当前依据；历史四模板和近期网站群发不替代它。** 以下只把示例达人和商品具体值标为 `{creator}`、`{product_name}`，保留用户原句、时令表达与 MX 联系方式，便于区分原文和通用模板。

> {creator}, dale otra vuelta a este para el regreso a clases 👀 {product_name} volvió a moverse bien y puede quedar muy bien para agosto. El enlace nuevo de BJN está arriba; agrégalo a tu escaparate y úsalo en tu siguiente video o LIVE. Si necesitas producto, desde ahí mismo puedes revisar si te aparece la opción de pedir muestra. Si se te atora algo con el enlace o la muestra, mándanos mensaje por WhatsApp Business al +86 133 8666 0687 o escríbenos a ryan001.bjn@gmail.com y te echamos la mano.

用户随后的明确要求：

- 二发采用固定模板和白名单商品名替换，不逐人调用模型；一发仍可个性化。
- 业务顺序是**商品卡在前，文字在后**：“BJN 新链接在上方 → 加入橱窗 → 下一条视频或 LIVE 再次使用该商品”。
- 样品只表述为“如果有可申请入口，可以申请”，不保证有样品或一定获批。
- 其他国家可以移除 WhatsApp/email；本轮意大利模板不携带这些联系方式。
- 原文中的返校季、八月和销量复苏不能固化为所有市场、所有商品的默认事实。通用意大利版本移除这些句子，也不声称看过对方视频。

原文没有承诺具体佣金比例、Boost 数字、保证寄样或必然发货。意大利固定模板以这条原文的再次推广结构为依据，不再采用先前“聊聊合作意愿”的文案。

## 2. 历史四分支：有代码和部分实发记录，不是当前默认

精确代码来源：旧仓库 `/Users/bjn00003/BDHub/01-BDSystem-V2`，Git 提交 `6743de9ab6b8da2a89ec81d22e4698f73b3e1d55` 中的 `bdhub/send/templates.py`。可只读复现：

```sh
git show 6743de9ab6b8da2a89ec81d22e4698f73b3e1d55:bdhub/send/templates.py
```

该文件在 `3bf5ccce5ece70bb6c02f85624f4b2b06c55c118`（2026-07-30）退役删除。现行 [CHANGELOG.md](/Users/bjn00003/BDHub/01-BDSystem-V2/CHANGELOG.md:2816) 与 [composer_api.py](/Users/bjn00003/BDHub/01-BDSystem-V2/dashboard/composer_api.py:2) 记录了旧发送台与旧队列入口退役、正式入口归入 `send-worker` 的事实。本研究不恢复退役入口。

| 历史模板 ID | `commission_diff` | `boost` | 历史表达重点 | PG `sent` | PG `send_failed` |
| --- | --- | --- | --- | ---: | ---: |
| `TREND_RATE_UP_BOOST` | > 0 | > 0 | 趋势 + 公开佣金到达人佣金的提升 + Boost | 141 | 44 |
| `TREND_RATE_UP` | > 0 | ≤ 0 | 趋势 + 佣金提升 | 10 | 3 |
| `TREND_BOOST` | ≤ 0 | > 0 | 趋势 + Boost | 12 | 4 |
| `TREND_REPUSH` | 其他组合 | 其他组合 | 再次推广的兜底表达 | 0 | 0 |

聚合来源：本机 PostgreSQL `outreach` 表，`bd_market='mx'`，仅按上述四个 `template_id` 和 `status` 聚合。前三类的 `sent_at` 均落在 **2026-07-05（UTC）**；本次未查到 `TREND_REPUSH` 的台账记录，不能将“四个代码分支”写成“四类均已实发”。`sent` 是旧库记录状态，本次没有联网重验历史交付。

历史源码第 63–85 行的输入与转换：

| 输入 / 占位符 | 旧实现的确切行为 |
| --- | --- |
| `creator` → `{creator}` | 传入值转字符串。 |
| `product_name` → `{product_name}` | 传入商品名，不由模板生成商品事实。 |
| `open_plan` → `{open_plan_rate}` | 例如 `0.08` 格式化为 `8%`。 |
| `creator_commission` → `{creator_commission_rate}` | 同上，写出具体达人佣金百分比。 |
| `commission_diff` | 由调用方提供，用于分支判断；该函数不计算或核验差值。 |
| `boost` → `{boost_rate}` | 由调用方提供，用于分支判断和百分比渲染。 |
| 缺失/无效数值/NaN | `_num` 默认回到 `0.0`；这是历史实现行为。 |
| 样品 | 没有样品输入参数，也不参与分支判断。四段正文都带 `si la opción te aparece disponible` 条件。 |

**Boost 的证据边界：**旧模板只证明它是名为 `boost` 的百分比输入，文案称 `Boost ... mientras siga activo`。该层未说明上游平台字段，也未证明它等于广告预算、额外广告佣金或另一种补贴。本次历史台账的扩展字段只见 `card_sent/error/flight`，没有保存可追溯的 Boost 原始商业字段。因此不能把当前链接规则中的 `BOOST_OVER_OPEN`（公开佣金上浮）自动当作旧模板 `boost` 的来源。

历史四模板都引用商品名；提佣分支引用实际填入的两个百分比，Boost 分支引用填入的 Boost 百分比。历史联系人与用户本轮提供的联系人并不相同，本轮不将历史联系信息带入意大利模板。

## 3. 卡先文后的代码事实

[dispatch.py](/Users/bjn00003/BDHub/01-BDSystem-V2/bdhub/send/dispatch.py:284) 的路径为：按 PID 取得商品列表卡元数据 → 有卡先调用 `_JS_SEND_CARD` → 卡步骤失败则返回 → 再调用 `_JS_SEND_TEXT`。它使用 `product_id/list_id/campaign_id/list_name` 等卡字段，正文不是把任意 URL 当作已经可发的商品卡。

历史四分支正文的 `el enlace de BJN que te mandamos arriba` 与这个先卡后文顺序对应。匿名台账还保留了 **20 条文字未完成但 `card_sent=true` 的失败记录**（提佣+Boost 17、提佣 2、Boost 1），说明两组件确实存在部分完成状态。`sent` 行本身没有独立 `card_sent` 标记，不能仅从该字段反推全部历史卡回执完整。

当前发送任务仍明确区分四种组件顺序，见 [send_tasks.py](/Users/bjn00003/BDHub/01-BDSystem-V2/bdhub/hub/repo/send_tasks.py:49)。任务管理页面默认选择“商品卡 + 文字”，另有“批量纯文字”，见 [TaskManagementPage.tsx](/Users/bjn00003/BDHub/01-BDSystem-V2/frontend/src/features/tasks/TaskManagementPage.tsx:107)。模板页在 `/tasks/templates`，任务管理在 `/tasks/manage`，具体确认/执行通过 `/api/im-operations/tasks` 与 `send-worker`。

当前二发选品也没有样品硬门槛：[campaign_catalog.py](/Users/bjn00003/BDHub/01-BDSystem-V2/bdhub/research/campaign_catalog.py:127) 区分一发样品资格与 `second_eligible`；[taplink/service.py](/Users/bjn00003/BDHub/01-BDSystem-V2/bdhub/send/taplink/service.py:20) 的二发事实比较排除了 `sample_quota`。这与“有申请入口再申请”的条件句一致，不等于承诺样品可得。

## 4. 近期网站群发是另一种业务

当前 `message_template` 中仍保存老的 `v1_high_commission_es/v2_high_commission_es` 种子；近期 `send_task` 实际使用的是网站推广模板，而非上述 TREND 四分支：

| 模板 | 当前关联发送任务数 | `sent` 组件数 | 模式 |
| --- | ---: | ---: | --- |
| `mx_bjn_sites_live_whatsapp_20260830` | 22 | 1,026 | `text_only` |
| `mx_commission_site_20260906` | 2 | 2,434 | `text_only` |

这里只保留匿名聚合用于辨别业务与年代，不复制网站群发正文。网站群发通过文内网站入口触达，不能取代用户现在提供的“同品商品卡在上方、再拍视频/LIVE”的二发文案。

上述调查仅记录既有事实与用户最新口径；意大利模板本地化和实际发送验收分别记录，不以历史 MX 台账证明意大利传输能力。
