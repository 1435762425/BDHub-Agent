# PID 生命周期树

状态：业务方案草案 v1。数据快照：2026-09-19 只读台账。

本文件只讨论 PID，不讨论达人排序、冷却或发送执行。PID 主链只有三个业务结果：

- `material_ready`：当前合格、已在对应平台池、精确 TapLink 可用；可以查询达人线索。
- `waiting_action`：等待选入/加入活动、建链、重建或原意图核验。
- `inactive`：当前商品不合格或失效；历史事实保留，后续刷新可恢复。

```mermaid
flowchart TD
    PID[一个 PID] --> SRC{来自哪里}
    SRC -->|全托| FS[高机会 → 仅全球销售商品]
    SRC -->|非全托| CP[Campaign 商品]

    FS --> Q{当前资格通过?}
    CP --> Q
    Q -->|否| INACTIVE[当前不可用<br/>保留来源/历史/旧卡]
    Q -->|是| POOL{已在对应平台池?}

    POOL -->|全托未选| SELECT[创建选入意图<br/>回查 confirmed / already_selected]
    POOL -->|Campaign 未加入| JOIN[创建加入活动意图<br/>额外条款转人工]
    SELECT --> LINK
    JOIN --> LINK
    POOL -->|已在池| LINK{当前方案有精确 TapLink?}

    LINK -->|有且佣金一致| READY[PID Material Ready]
    LINK -->|完全没有| CREATE[创建唯一建链意图]
    LINK -->|旧卡佣金不同| REBUILD[旧卡保留<br/>创建当前佣金新卡]
    LINK -->|结果未知| VERIFY[只核验原意图<br/>不创建替代卡]
    CREATE -->|创建+回读成功| READY
    REBUILD -->|新 listId 核验成功| READY
    VERIFY -->|确认成功| READY
    VERIFY -->|仍未知| WAIT[等待处理]

    READY --> LEADS[允许进入 PID 达人查询队列]

    REFRESH[来源/活动/佣金刷新] -.-> Q
    INVENTORY[链接库存刷新] -.-> LINK

    INVENTORY --> CLEAN{链接健康}
    CLEAN -->|有效| KEEP[保留]
    CLEAN -->|混合有效| KEEP
    CLEAN -->|未知| REVIEW[复读/人工核对]
    CLEAN -->|整条失效| CLEAN_SCOPE[独立清理范围<br/>冻结删除意图+回读]
```

## 当前数据快照

### 来源和筛选

- 全托：10,000 个来源 PID；当前门槛筛出 2,291，筛除 7,709。
- Campaign：当前快照 2,697 个去重 PID；选中 449，held 2,248。
- 全托已选池建链台账：3,450 个 PID，包含历史已选商品，因此不等于当前 2,291 个合格 PID。
- Campaign 新加入台账：18 条 joined；既有已加入活动来源另由当前 Campaign 快照体现。

### 链接准备

- 全托：已核验 1,098；可复用 1,193；复读中 1,154；缺链 4；待判断 1。
- Campaign：已核验 420；可复用 152 个 PID；缺链 259；读取不完整 2；待判断 6。
- 已核验新建链接意图：1,519。

这些状态是技术处理细节，面向业务只映射为：`material_ready`、`waiting_action` 或 `inactive`。

## 刷新规则

### 来源刷新

- 全托主动采集或可选定时生成完整新快照；不完整快照不切换当前 head。
- Campaign 设计为每日完整刷新，也允许手动触发。
- 每次刷新都重新回答“当前资格通过吗”，不是生成一套新的永久状态。
- Campaign 加入时剩余期限大于 45 天不形成永久资格；刷新到 `<=45` 天时退出 `material_ready`。

### 方案与链接刷新

- 活动、佣金、期限变化后重新选择当前 Offer。
- 旧卡创建时的佣金不会自动变化。旧卡 12%、当前方案 13% 时进入待重建；旧卡保留，新发送只使用核验后的新 `listId`。
- 链接三层核验：本地台账判断 → 组批前平台只读复读 → 发送前 `fresh_card()`。
- `unknown/result_unknown` 只核验原意图，不换账号、不新建替代卡。

## 选入与加入活动

- 全托 `selected pool` 与当前业务资格分开。已选商品后来不合格时仍留在平台已选池，但不进入 `material_ready`。
- Campaign `joined` 与当前活动资格分开。活动失效、库存不足、期限不足或佣金不合格时退出 `material_ready`。
- 写入前保存唯一意图；回执和平台读回共同决定完成。重复点击沿用原意图。

## 链接清理旁路

当前链接库存：2,067 张列表 / 2,067 个成员记录。历史清理快照扫描 1,908 张，其中 1,786 有效、122 整条失效；历史台账有 122 条已核验删除记录。

当前 A 规则：PID 主流程不自动删除任何旧卡。清理必须是独立动作，并满足：

1. 明确清理范围；
2. 完整读取列表及成员；
3. 健康分类有证据；
4. 冻结删除意图；
5. 删除后读回确认；
6. 未知删除不重复执行。

有效、混合有效和未知链接不进入自动删除。历史删除结果不等于当前主流程获得删除授权。

## 当前实现差距

- 真实 `lead_pool` 尚未把 `material_ready` 作为位置入口，历史线索可能绕过当前 PID 树进入池子。
- 当前 `catalog_prepare_item` 的 `ready/reuse/reading/missing/review` 仍直接暴露为状态，后续应统一投影成三个业务结果。
- Campaign 与全托的当前 Offer、TapLink 和刷新证据仍分散在多个 SQLite 表，需要一个 PID Material 投影作为单一读取合同。
