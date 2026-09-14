# 货盘级批量链接准备（P0）

2026-09-14。把 TapLink 准备从二发任务的达人名单门槛中拆出来，做成按商品（PID＋活动绑定）独立、可恢复、可核验的货盘级队列。本轮不发送消息、不恢复自动回复。数字为带时间的快照。

## 流程与代码

| 环节 | 入口 | 说明 |
| --- | --- | --- |
| 队列与状态机 | `scripts/lib/catalog_prepare.py` | `catalog_prepare_item`（PID×活动×来源）状态：pending → reading → missing / reuse / review / read_incomplete → prepared → submitted → unknown → ready；`retired` 表示已被当前活动绑定取代 |
| 批量执行 | `scripts/catalog-link-prepare.py` | `seed` / `read` / `create` / `status`；只读台账视图为 `--status-links` |
| 连续运行 | `scripts/catalog-link-batch.py` | 有界分轮，可随时中断重启；每轮从持久台账续做 |
| 意图与创建锁 | `scripts/lib/catalog_links.py`、`scripts/catalog-link-canary.py` | 共用 `catalog_link_intent`、单 PID 唯一开放意图与 `var/cycle-card-create.lock`；批量与单品 canary 互查未完成意图 |
| 二发消费者 | `scripts/lib/batch_material_runtime.py`、`batch_materials.py` | `batch_material_link` 按商品记录任务链接状态；`advance_links` 不再等待名单 |
| 页面 | `apps/web/src/features/catalog/CatalogWorkspace.tsx`、`apps/web/src/server/global-source/bridge.ts` | “已选商品链接准备”区展示覆盖、待建、查询不完整与阻塞原因 |

### 活动绑定必须来自平台

初版用本地选入快照的 `campaign_id` 作为活动绑定，实测 599 个全部与平台 `pick_up/list` 返回的当前活动不一致：快照记的是来源活动，实际推广绑定是选入后平台分配的另一个活动。这类行全部以 `selected_campaign_changed` 退役（`superseded_by_live_binding`），不当作缺链、不建链。现在 `seed` 直接读平台当前已选池，按平台返回的活动写行。

实测平台已选池一次调用最多返回 2000 行（`total_num` 3451），需要分页读全；`page_size` 200/500 会被平台拒绝。已选池缓存 `var/catalog-selected-pool-cache.json`（TTL 2 小时）避免每轮重复分页。

### 旧链复用的完整判定

对每个 PID 用 `key_word=PID` 完整搜索 IM 商品卡，再按 `list_id` 回读成员，只有成员级事实齐备才判定：

- 必须 `platformValid`（成员 `product_status=2`）、`productEligible`（非治理、`unavailable_type` 允许）
- 同活动绑定（selected 路线要求顶层 `campaign_id=0`，成员活动等于实际活动）
- 达人佣金 > 当前公开佣金，机构收益 ≥1 个百分点
- 全托商品不检查库存数量；`managementType/managementEvidenceRef` 缺失时不按全托免库存

命中多条时按达人佣金最高、同佣金优先已使用排序。查询不完整（分页不一致、成员总数不符、成员缺失）一律记为 `read_incomplete`，绝不降级为“缺链”。卡片属于其他活动时记 `existing_links_other_campaign`，同样不新建。

### 冻结与执行

只有“完整搜索且 total=0”才形成冻结意图（`purpose=catalog_batch_link`，密钥含 PID、活动、达人点位、规则指纹、账号，不含观测时间），并再次校验：商品当前仍符合初筛、活动绑定一致、佣金/期限未变、创建前二次搜索仍为 0。提交走已验证的 ACC9 单写协议，最多一次；回执与成员卡片核验一致才 `verified`，否则保持原意图与账号只读回查。创建前二次搜索使用与批量分类相同的 `page_size=20` 查询形状，避免口径不一致。

## 已完成验证（离线）

`tests/test_catalog_prepare.py` 13 项：不完整查询不创建、租约过期可重领、旧链复用与不达标理由（达人未高于公开、机构不足 1 点、平台无效、商品不合格）、多活动不串配、冻结幂等、重启后 unknown 不重复创建、链接条数与覆盖 PID 数分开、旧创建器不能为货盘已接管 PID 建第二条。

`tests/test_batch_material_runtime.py` 5 项：名单为空也能登记并推进货盘链接、未准备方案给出待建原因、佣金已变的旧卡绝不满足任务、任务暂停不登记、报告把商品覆盖与达人齐备分开。

其余回归：`test_catalog_links` 4、`test_cycle_card_creation` 14、`test_batch_materials` 7、`test_batch_tasks` 10、`test_batch_task_service` 12、`test_batch_sources` 13、`test_cycle_catalog` 13、`test_cycle_materials` 14、`test_global_selection` 6、`test_global_source` 14 通过；`apps/web` 新增 `tests/catalog-link-status.test.mjs` 3 项通过。

## 真实对账与剩余缺口

- 只读对账（599 个已选商品）结果、真实建链数量与回查证据见后续报告文件 `var/catalog-link-batch-*.json` 与 `var/catalog-link-read-*.json`；本轮只读阶段平台写入必须为 0。
- 批量创建按单品 canary 的串行协议执行，未做并发；实测单品约 7 秒，不外推为批量保证速度。
- Campaign（非全托）路线的批量建链尚未接入；`config/catalog-link-policy.json` 的新分佣对 Campaign 同样适用，但需要本来源资格规则先确认。
- 二发任务的 `batch_material_link` 目前按任务在存商品方案登记；完整任务级调度（多任务共享、ATC 额度）仍待统一执行器。
