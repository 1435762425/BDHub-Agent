# 运行架构的下一层改进

2026-10-24。本文是有源码、日志及临时 fixture 支撑的设计，**尚未实施**的部分不作为当前能力。已交付改进见[本批实现](../implementation/autonomous-design-20261024.md)；业务授权和当前运行合同仍以 PROJECT/TECHNICAL 为准。

## SDK 浏览器与 HTTP 认证分离

`im-session-worker.py` 当前约每 600 秒连同 Receiver、HTTP owner 和 browser context 一起关闭，退出原认证上下文后释放 ProfileLease。最近多轮正常 drain 为 600.0–600.3 秒、没有业务错误，七市场几乎同时重建；重新 ready 的间隔约 88–109 秒。

600 秒是本地工程轮换边界，`ItalyImReadSession` 另有 900 秒本地寿命，均不是已验证的平台 token TTL。HTTP 在浏览器 hydration 前已恢复，持久信号、分页发现和补扫也保留，因此这是 SDK 回调缺席/延迟和重复启动成本，不等于全部收信丢失。

候选结构保留一个外层 browser/ProfileLease owner，另有可轮换的 HTTP AuthBundle、IPC epoch 与原 Receiver。候选认证重新核对同账号、机构、市场、IM ID、headers 和发布代次；拒绝新 borrow，排空旧客户端后切换 auth/epoch 和 Receiver，浏览器回调 buffer 保持“先持久化再 ACK”。停用、维护、身份/header 变化仍完整 drain；SDK 持续失 ready、回调丢失、overflow 或身份不符仍完整重建。不能直接删掉 600 或无限延长 900。

实施前需覆盖旧 epoch 拒绝、维护优先、轮换失败、队列/游标交接、停止和身份变化；单市场只读跨 2–3 次轮换验收浏览器长寿、自然回调、HTTP 借用与维护交接，再逐市场推广。完整研究证据保存在开发输出 `sdk-lifecycle-design.md`，当前代码没有宣称实现热轮换。

## FIFO 失主必须能关联原意图

临时 fixture 已复现：FIFO gate 的 active PID 死亡后连续三次报 `http_write_owner_lost`，旧队首不变。检查当时七个生产队列均为空，这不是当时正在发生的事故。

旧 entry 只有 token/PID/active，缺少 requestRef，不能证明它对应的原账已结清，不能直接按 PID 死亡清队列。未来 entry 应携带原市场、账号、意图/组件、requestRef 和进程启动证据；由既有 reconciler 结算原账，确认原 writer 已退出，再持 guard 归档/回收。不能关联的旧 entry 继续保留证据。本批没有修改 vendor、自动摘队首或扩大重发权限。

## 更少重复读取与增量匹配

全量收信核验也需按规模设计时效。BR 的 5,149 个会话与约 50 次/分钟核验，无法满足固定全盘五分钟门槛。通知信号、近期列表发现、gap/backfilling 与全量历史复查分别记录进度；原最旧核验年龄继续可见。下一步可验证冷队列的固定执行份额，防高信号/持续 pending 使其饥饿，并降低周期重建损耗。故障应根据未处理信号的年龄/错误、持续断层或 HTTP 成功核验停止前进判断；未发现停滞时不靠重启或清状态制造健康。仍需真实业务样本验证新来信时延，不能只看 SDK ready。

一次 `_catalog_fingerprint` 在真实数据样本约 0.395 秒、14,267 条 SQL；同片段的 `_offers/material_offers` 仍可能重复读取。下一小步可在一次请求内共用来源/分类输入，但发布前仍需复核当前 head、规则与材料 hold，不引入全局缓存。

更大的方向是依原画像和商品事实指纹增量复用匹配结果。需要先定义来源、画像变化和跨库水位的失效合同；不能凭“上轮数量相同”沿用候选或允许旧 head 发送。当前先优化同一原扫描的成本与续跑，保持完整发布合同。
