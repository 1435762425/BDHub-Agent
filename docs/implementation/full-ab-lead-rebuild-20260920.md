# A/B 线索全量重建

日期：2026-09-20。用户确认以当前合格商品和当前标准 TapLink 为唯一 PID 范围，从头重建 A/B 线索；允许清理旧的当前投影，但必须保留已发送记录和历史证据。本轮不发送消息、不冻结批次、不创建或删除 TapLink。

## 范围与保护

- 当前 `catalog_current_binding` 有 3,029 条意大利 active 绑定、2,813 个 PID；佣金规则全部为 `commission-1-to-2-v1`，命名规则全部为 `link-naming-v1`。
- 与当前合格商品相交后，实际 Kalodata 范围为 2,756 个 PID：全托 2,282、Campaign 474。
- 迁移前备份：`var/backups/state/20260920T055554Z-before-full-ab-lead-rebuild`，21 库、289,570,816 字节、`valid=true`、Git 工作树干净。
- 清理前：3,172 个查询时钟、1,263 页缓存、1,150 个当前 A 类 head。
- 清理后：上述当前查询状态归零；1,150 个历史 run、1,617 个历史 selection、12,996 条来源索引及 495 个已发送达人×PID全部保留。

`linked_products()` 现在在读取端也显式要求 active、`commission-1-to-2-v1` 和 `link-naming-v1`，不只依赖绑定写入端保证。

## 实现

Migration v9 `ab_leads_and_video_scan_v1`：

- A 类索引新增 `revenue_value/revenue_currency`，只用同一市场当前采集口径的数值 GMV排序；
- B 类增加 generation、PID job、逐页 checkpoint、逐视频 detail checkpoint、作者缓存和 `video_lead_current`；
- B 类完整读取30天窗口，无固定页数/视频数上限；刷新时重新读取列表以发现播放量从 `<1,000` 跨到 `>=1,000` 的旧视频，已知视频作者不重复请求；
- `lead_pool.py` 合并 A/B：A 优先并按 GMV，B 只按最高单条视频播放量，同达人只占一个发送槽；
- `rebuild-current-leads.py` 只清当前查询缓存和 head，保留历史与 sent。

首轮发现“有效页面但0条正销量”被旧发布器误判为 `lead_empty_window_missing`；已修复为空 generation 也必须发布0-selection head，并增加回归测试。修复后断点复用既有页面，没有重复网络读取。

## 2026-09-20 首日实际进度

Kalodata 返回真实 `DETAIL.ACCESS_TIMES` 日额度耗尽后自动停止：

| 指标 | 当前值 |
| --- | ---: |
| 标准链接 PID 总范围 | 2,756 |
| A 类已完成 PID | 1,523 |
| A 类剩余首次 PID | 1,233 |
| A 类当前线索 | 4,504 |
| 其中0线索 PID | 460 |
| 已写数值 GMV 来源边 | 4,933 |
| 当前 A 类达人×PID位置 | 3,213 |
| 当前可发送达人 | 1,186 |
| 历史已发送达人×PID | 495（未减少） |

A 类续跑批次自身为1,678次网络读取、错误0，并因真实额度信号停止。B 类在额度耗尽前已经为第一个高销量 PID 保存5页列表，停在 detail checkpoint；`video_lead_current` 尚未发布，不把不完整 PID计入 B 类结果。

已建立线程 heartbeat `BDHub A/B线索全量重建`：每日00:15先继续 A 类，A 类完成后继续最新 B 类 generation；正常额度暂停时保持安静，只在全部完成、非额度故障、保护检查失败或需要用户操作时通知。真实发送、冻结、TapLink写入和自动回复保持关闭。

## OECID 接续

当前4,504条 A 类线索包含2,038个唯一 handle：1,260个达人已解析 OECID，778个待补。第一次全量身份批次发现 vendored 协议源码缺少 `pure_http_runtime_manifest.json`，网络请求前即失败；已补回与旧只读 runtime 完全匹配的哈希清单，并通过20人只读 cohort 验证 runtime 可启动。

随后 ACC6 Find 返回连续验证挑战：HTTP 200/code0，但 `verificationRequired=true`、`systemError3=true`，验证码重放没有成功，报告为 `request_or_signer_error`。批次已停止，未把这些达人误判为搜索不到。现有旧生命周期服务 `io.bdhub.account-identity-lifecycle` 每5分钟检查，ACC6下一维护点为2026-09-20 15:27 CST；项目不绕过该服务修改旧凭据。heartbeat 已增加 OECID 阶段，只处理与当前发送池待补 handle 重合的 outbox batch，账号未恢复时不硬重试。
