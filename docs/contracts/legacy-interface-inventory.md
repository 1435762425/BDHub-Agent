# 旧 BDHub 必要接口与底座语义清单

核对日期：2026-09-11。状态：**源码调查，不是生产 API 调用或新系统实现**。新系统位于 `/Users/bjn00003/BDHub/BDHub-Agent`，独立运行并以最终替代旧 BDHub 为目标；当前旧目录和旧库保持只读。本轮只读源码，没有运行服务、复制密钥、名单、对话或数据库。

这里记录可迁入或重实现的最小语义，不要求新系统长期依赖旧 Dashboard 页面、端口、进程管理器或 import 路径。现有 API 面向旧工作台，不是完整的新系统合同；只能作为适配参考。各源码快照的 SHA-256 见末表，S 编号对应来源。

## 1. 必要能力

下表区分读业务事实、写本地状态和真实平台动作。HTTP 方法或“preview”名称不能单独决定副作用。所有写入口本轮均未调用。

| 范围 | 真实入口与关键参数 | 已有副作用与限制 | 新项目保留的最小合同 |
|---|---|---|---|
| 达人身份/画像读取 | `GET /api/creators`：market、q、category、tier、limit/offset；`GET /api/creators/{oec_id}?market=`。S01:602–684；S02 | 业务读取；Handle 解析区分 current/alias/missing/ambiguous。主档、别名和画像快照分开。 | 以 market+OEC 稳定关联，精确 handle/唯一别名解析；金额精确数值、币种和字段观测时间，缺失不当零。新接口不接受模型换收件人。 |
| 画像补充 | prefix `/api/creator-refresh`，`GET /capabilities`；`POST /preview`、`POST /jobs`，请求由 source/scope、market、目标、dimensions 与 preview_id 等构成；jobs 支持暂停/继续/取消。S04、S05 | preview 会写本地预览 JSON；jobs 启动采集并更新画像，不能视为纯读。当前 Pure HTTP 与 Contact Browser readiness 分开。 | 拆“读取事实”和“发起刷新命令”；持久工作项、范围快照、幂等、逐字段来源、失败原因与账号资源。不能运行旧刷新来假装完成新系统导入。 |
| 货盘与 offer | `GET /api/products` 按 source_pool/category 等读旧统一商品库；`GET /api/commerce/catalog` 按 market/account/source/version_id/change 分页；`GET /api/commerce/recruitment?market=&purpose=first|second`。S06、S07、S08 | 商品读取来源不同；旧 products 未统一要求 market。catalog 的 campaign/global/selected 不等价；categories 可执行远端读取。`POST /sync` 启动采集并落状态。 | 统一 market+PID 的商品引用，另建具体活动/列表/佣金/样品/期限的 offer 引用与版本；返回来源、新鲜度、资格与缺口，不能拼接不同方案最优值。 |
| Kalodata 发现 | prefix `/api/kalodata`；`GET /runs`、`GET /runs/{run_id}`；`POST /runs`：run_id、market、mode、transport、targets、days、limit_per_pid。S10、S11 | POST 写研究目录并启动第三方采集；当前 days=7/14/30、每 PID 3/10/50、目标最多100；HTTP仅PID模式，查询窗口首次提交冻结。依赖外部采集项目/登录文件，不是独立新系统能力。 | 迁入查询标准化、精确PID证据、原币与统计窗口、来源/覆盖范围、去重和停止语义；新系统独立实现凭证供应与持续游标，不能拷贝旧Cookie或把销售记录解释成已有实物。 |
| IM 消息与人工状态 | prefix `/api/im-operations`；`GET /conversations/{conversation_id}`：market/limit/cursor；`PUT /inbox/status`：market、identity_key、command、status或expected_last_inbound_at。S12:499、998 | GET读取会话；PUT写处理状态/结案记录。`POST /conversation-sync`会排补捞任务。现有inbox状态不是跨旧机器人/新Agent的完整执行归属协议。 | 保留 server_id 去重、市场/OEC/CID、发送方/原时间、分页与历史来源；新增关系控制版本、人工消息观察、待发稿取消与唯一owner。历史补回不直接唤醒外发。 |
| 批量发送 | `POST /api/im-operations/tasks`：market/name/source_type/send_mode；后续 `/handles`、`/content`、`/preflight`、`/start`；start要求 confirm=true、revision和账号范围。S12:1382、1566、1952；S13 | 建任务/加名单写DB；preflight可能补身份；start经能力与监听门禁启动worker并真实发送。create_task自身没有外部请求幂等键。 | 保留 task→lead→component→attempt、逐人冻结正文、组件顺序、预检revision、账号亲和、暂停和unknown核验。新执行网关需同事务幂等注册与绑定，不照搬页面逐次确认，也不绕开实际动作资格。 |
| 单条发送与结果账本 | `POST /api/im-operations/conversations/{cid}/outbox`：market/body；内部 `MessageDeliveryRepo.begin/finish/ledger`：source_kind、source_id、market、cid、body、message_kind、account。S12:540；S14 | outbox 写出站队列，监听worker随后真实发送。begin写sending意图；human/ai来源唯一。ledger为批量与单条的读投影。 | 新系统保留唯一发送意图、固定目标/正文、回执及unknown；批量组件仍是其自身结果真值，不再抄写一份“AI已发送”。平台接受、送达、已读须有各自证据。 |
| 样品 | prefix `/api/sample-review`；`GET /requests`：pid/campaign_id/lifecycle_status等；`POST /batches/preview`：request_ids；`/batches/{id}/execute`：confirmed；`/requests/{id}/review`：action/reason/payment/confirmed。S15、S16 | 当前路线明确MX。同步写样品事实但不批准；preview写内部批次，execute/review排真实审批。等待账号、资格和未知结果单独保存。 | 按市场定义申请/官方或机构路线、批准权、额度和结果；保留申请身份、45天去重等现行适用规则，BR/IT不得套用MX。自动审批需单独配置商业策略与能力。 |
| ShareLink/TapLink | `/api/share-links/jobs/{id}/preview|generate`：market/account/revision，generate要求confirmed；`/api/tap-links/preview`准备；`/api/commerce/links`准备批次，`/links/{id}/{action}`执行；`/api/commerce/catalog/build`：market/account/catalog_source/choices/rule/request_key等。S17、S18、S08、S09 | ShareLink入口限MX。TapLink旧列表/商品入口限定MX/BR，IT经commerce路线。**catalog/build在draft时直接launch create，是建链动作**，不能当预览。 | 普通创建独立generation intent，未知不重试；网站特定绑定复用是独立规则。同市场/发送账号精确核验PID/list_id/Campaign才称卡片可用；供给准备与发送分离。 |
| 账号与市场能力 | `GET /api/markets`、`GET /api/account-readiness?market=&capability=&transport=`；内部 `require_capability`、`build_account_readiness`、`ProfileLease`。S01:355–385；S19–S21 | 读取目录、能力和本地运行/租约摘要；不能据账号存在推断登录有效，canary不能开放批量。真实维护另有动作入口，本轮未调用。 | 新项目独立配置账号引用与凭证提供器，保留市场动作矩阵、角色、账号独占、请求级节拍、亲和和维护；跨项目并存期必须防止同时抢占同一平台账号。 |

## 2. 必要实体与迁移边界

S03 定义的身份/画像、IM消息、creator_relationship/task/event、send_task/lead/component/attempt、im_delivery_intent、样品和ShareLink实体提供事实语义。AI计划/匹配/case/event只是旧运营预览基础，仍缺新系统持续经营合同，不应整表照搬为最终设计。

迁入前逐项确认字段映射、来源版本、身份归属、历史保留和缺失解释；旧库当前不可改，新系统建立自己的数据库/任务/配置。需要的数据通过获准的只读导出与校验接入；不把生产DB URL、旧账号文件或全部工作目录复制过去。最终替换旧系统时另行验收迁移与切换，调查阶段不停止旧服务。

MX旧要号引擎有进程内待发队列和got_wa/handoff等状态（S22），新owner切换须使旧引擎最终发送检查失效、保留在途/unknown并核验；不能只在新前端加开关。真实发送、审批和建链结果保持唯一归属，恢复不盲重发。

## 3. 契约不足与重实现优先级

现有路由普遍依赖本地ProcessManager、旧路径、文件存储及local_owner，不能直接充当新系统长期后端。新repo先建立稳定的查询/命令/事件合同和独立执行器适配，再迁入经过验证的底座逻辑与契约测试；旧界面只是调查入口。

优先补齐：统一市场与可追溯Evidence；幂等异步命令；关系owner/control_revision；来源事件与回执投影；平台适配及账号资源隔离。业务规则可以重新编排，自主程度按用户已确认政策设计；身份、金额、资格、unknown和实际完成证据不能退化。

## 4. 源码指纹

以下均为本轮实际读取的旧源码路径；SHA-256 标识核对版本，不表示已部署。文件后续变化应重新核对相关条目。只保存代码指纹，不保存凭证或业务记录。

| 来源 | 绝对路径 | SHA-256 |
|---|---|---|
| S01 | `/Users/bjn00003/BDHub/01-BDSystem-V2/dashboard/app.py` | `7812640bd9e58d00cdcb40ea4949993c8df3c67b5f5550bafb487ab7fbae5647` |
| S02 | `/Users/bjn00003/BDHub/01-BDSystem-V2/bdhub/hub/repo/identity_reader.py` | `e927dbad7c91b237810c8f22587634d015ef070a87d43c38e5de5a6866690fd8` |
| S03 | `/Users/bjn00003/BDHub/01-BDSystem-V2/bdhub/hub/schema.py` | `8b02aad8163d657cb4562a57a29d2cca31d871940d00012d843bec8d5bcedded` |
| S04 | `/Users/bjn00003/BDHub/01-BDSystem-V2/dashboard/creator_refresh_api.py` | `3a560a50079e5af2c4c55faff7f80280e98925b4f28f63b0609421de7522a396` |
| S05 | `/Users/bjn00003/BDHub/01-BDSystem-V2/dashboard/creator_refresh.py` | `77a9cf3108a560df589916d43a60f6cb14694d5c036fa542818e083e9d45096b` |
| S06 | `/Users/bjn00003/BDHub/01-BDSystem-V2/dashboard/product_management_api.py` | `7c451431e42a4ed91686e1e09192d1b99340215e947a3f23680fa1bcbfcb1796` |
| S07 | `/Users/bjn00003/BDHub/01-BDSystem-V2/dashboard/commerce_catalog_api.py` | `4a0655e1e8faa81ec7feace447d8f8161627a4d5498ee1a16924d3bf51f9b5bc` |
| S08 | `/Users/bjn00003/BDHub/01-BDSystem-V2/dashboard/commerce_api.py` | `cda64d88b6b2ecdf541307dc92993e54e2b8ee41888b5969aefcbda3d8c206ee` |
| S09 | `/Users/bjn00003/BDHub/01-BDSystem-V2/bdhub/research/commerce_links.py` | `2f8ff2831e9fe88d8e7a380d77eea7c6fb4cca1548bd060bf0fe22b7c38eb2fc` |
| S10 | `/Users/bjn00003/BDHub/01-BDSystem-V2/dashboard/kalodata_api.py` | `96d7c43e0272ec5b349342f493575640b9da001047f32b52f8c529a844a2a3e5` |
| S11 | `/Users/bjn00003/BDHub/01-BDSystem-V2/bdhub/research/kalodata.py` | `1c8c2a948fe24c1550c08dd55a81864ab55d4e8f94050f8dcacbc3fcf47c2442` |
| S12 | `/Users/bjn00003/BDHub/01-BDSystem-V2/dashboard/im_operations_api.py` | `d274f8c554b80435a4fab43e014ce15391dbcc72329d751467ffb93846a327b4` |
| S13 | `/Users/bjn00003/BDHub/01-BDSystem-V2/bdhub/hub/repo/send_tasks.py` | `34b9ad7cf89bbc4fdda677087023ea752dae6a7e4c87d6e3ff2420d6c4b44014` |
| S14 | `/Users/bjn00003/BDHub/01-BDSystem-V2/bdhub/hub/repo/message_delivery.py` | `8e1f66545d02f89fecb1fd8738ec2e0507dda1dd6c75e828d9ee9828b5180bd7` |
| S15 | `/Users/bjn00003/BDHub/01-BDSystem-V2/dashboard/sample_review_api.py` | `f54f6804a0454594fd2dd2f03fc68bbb1c2ea89a822b4d6aeba4e7af897efa13` |
| S16 | `/Users/bjn00003/BDHub/01-BDSystem-V2/bdhub/hub/repo/sample_reviews.py` | `59d9aaa89d79155531669f22bd8fb9f4f48cf9d6862c4296e33b4e8ac7ecc9ff` |
| S17 | `/Users/bjn00003/BDHub/01-BDSystem-V2/dashboard/share_link_api.py` | `780e8681ad26a39c703bb1e7613de19a94ce9707056ca94db4e45899ce54913c` |
| S18 | `/Users/bjn00003/BDHub/01-BDSystem-V2/dashboard/taplink_api.py` | `8b113a351271d2b0f8efc80d8ea2ffe6824328db6b47008f413c3351e0d4af40` |
| S19 | `/Users/bjn00003/BDHub/01-BDSystem-V2/bdhub/hub/markets.py` | `6be5341d74ec0aa68f3df0b234527a4802d898c8a632bdd0dc7096bec02113b5` |
| S20 | `/Users/bjn00003/BDHub/01-BDSystem-V2/dashboard/account_readiness.py` | `f8669477006e9083f2681e870da94b76b467f271b0b6b495e13f274eb6e506ea` |
| S21 | `/Users/bjn00003/BDHub/01-BDSystem-V2/bdhub/enrich/profile_lease.py` | `1832494048c99d22a37b81303c2774f3aaaebd3e72aa15e527dee030f69a2037` |
| S22 | `/Users/bjn00003/BDHub/01-BDSystem-V2/bdhub/reply/engine.py` | `b4696083a49a252e5d80805040e4853193061b24fcb3968ce36dda90ec874e92` |
| S23 | `/Users/bjn00003/BDHub/01-BDSystem-V2/bdhub/hub/repo/share_links.py` | `b752c24f6de41cb6324468eb4a8f9a0dc3f8d55695c119bf9ecca13af8514900` |
