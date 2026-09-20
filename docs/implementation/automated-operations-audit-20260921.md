# 自动运营闭环复查与修复（2026-09-21）

## 结论

本轮按“货盘 → Campaign 加入 → TapLink 短名/建链 → Kalodata → OECID → 发送池 → 收信/Agent”重新检查了自动运营。发现并修复四个会让系统无法长期闭环的问题；自动运营总开关和持续发送仍保持关闭。

| 检查项 | 原问题 | 当前合同 |
| --- | --- | --- |
| TapLink 短名 | 自动阶段直接 seed/read/create；缺短名时会退回截断标题 | seed/read 后强制 `catalog-names prepare --all`，missing 非零不创建 |
| Campaign 日更新 | 只采集已加入列表并筛分，没有刷新可加入列表或自动加入 | 先结算旧 unknown，再预览并分批加入全部合格活动，全部明确后采集/筛分 |
| 验证码 | 读侧、商品选入、TapLink 已接；Campaign 加入写侧未接 | Campaign 持久意图落盘后，同账号求解并只重放同一请求一次；再次挑战进入 unknown |
| 长期恢复 | 到期账号只排队不启动维护 worker；Agent restart 依赖失真的 jobs 开关；调度状态每 tick 丢 lastSuccess | 调度器启动维护 worker；Agent 以 durable setting 自恢复；lastSuccess/lastAttempt 跨 tick 保留 |

## 会话与 Agent

- `showcaseNotifications` 作为“达人已将商品添加到橱窗”进入队列摘要和时间线，但不进入文本分类器。
- 移除“处理中”分类；未回复与已处理统一进入“Agent 回复”，未回复项显示“待回复”。
- 修复 durable pending 被过期 `inbox_until` 永久跳过的问题。
- 用户本轮明确授权后，使用带 request ID 的一次性运行逐条处理当时 6 条 `sample_self_service`；6 条均精确回查 confirmed，unknown 0。常规 Agent 仍只在 15:00–16:00 自动运行。

## 账号边界

- 旧 `/Users/bjn00003/BDHub/01-BDSystem-V2` 中 ACC6/ACC9 已停用并退出全部业务池和旧身份生命周期；变更前由旧账号 API 自动备份，修改时两账号均无租约或在途任务。
- 新项目继续使用自身 `var/account-identities` 中已发布的 profile/HTTP/IM 代次。账号启用、角色和池策略由新项目 overlay 决定，不再继承旧项目的停用/池字段。

## 当前只读事实

- 商品短名：scope 259、ready 259、missing 0；DeepSeek provider ready。
- Campaign：平台已加入总数 61，本地当前 job 18 条 joined、unresolved 0。
- workflow：尚无正式 run；总开关、全托周更新、持续发送均关闭。
- Agent：enabled，固定窗口 15:00–16:00；本轮新增 6 条 confirmed 回复。
- 自动运营审计和测试没有启动货盘、建链、Campaign 加入、Kalodata、OECID 或持续发送平台任务。
