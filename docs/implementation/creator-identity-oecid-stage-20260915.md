# 达人身份（OECID）阶段：发送前的最后一道闸（2026-09-15）

## 为什么要有这一步

Kalodata 给的是 **handle**，不是平台身份。发送（TikTok IM）要用 **OECID** 定位达人，冷却与 500 人额度也是按 OECID 记的（`cycle_contact_reservation(plan_id, oec)`、`cycle_executor.create_once(oecId)`）。

关键点：**这一步早就存在，而且它就是发送池的闸门**。

发送池的「位置」来自 `cycle_identity_resolution`，而 `lib/cycle_identity.py` 只在 Find 同时返回 `creatorId` 和 `oecId` 时才写这张表。所以：

- 池里的 1,606（当时）个达人 **100% 有 OECID，0 缺失**——池子本身就是「已拿到 OECID」的结果集，不是缺了一列。
- handle 查不到的线索**根本不会成为位置**，不是池子里某层被过滤掉。

用户的三点确认（2026-09-15）：

1. 就是「handle → Find → OECID → Profile」这一步；查不到的 handle 就是「搜索不到」，**不能进发送池**。
2. 要**独立一张卡 + 手动按钮**，放在「达人线索查询队列」和「发送池」两张卡中间。
3. 这 1,000+ 条待补是 OECID 问题；抓完一遍就知道哪些进池、哪些因为找不到身份被淘汰。

## 三种状态，必须分开报

| 状态 | 含义 | 下一步 |
| --- | --- | --- |
| **已就位** | 已有 OECID，已在发送池 | 无需动作 |
| **待补** | 还没判定：未提交 / 排队 / 被账号挡住 | 点「开始补 OECID」 |
| **搜索不到** | Find 跑过、平台没有这个人 | 保留记录、不进池；**不伪造 OECID、不自动重试** |

三者必须严格划分全部线索，页面自己核对：`线索 ＝ 已就位 ＋ 待补 ＋ 搜索不到`。对不上时不显示那个等式，改写一句「合计暂时对不上，别据此判断池子大小」——**不给出一个自己都对不平的总数**。

数字同时给**达人**和**线索**两个口径：平台工作按 handle 去重（同一个 handle 出现多少条线索，只查一次），而用户按线索条数理解业务量。

## 实现（全部复用，没有重写）

```
点按钮 → job-run.py start --name identity
       → identity-batch.py（新驱动器，只决定跑几轮 + 发布进度）
            └─ 每轮：creator-profile-refresh.py worker --once --cohort-size 20
                     ├─ 一次 OEC profile refresh
                     ├─ 一个 Find cohort（12 QPS / 9 通道，走已发布验收）
                     └─ freeze + dispatch + reconcile → 写身份结果
```

- `lib/identity_queue.py`：只读统计（三类计数 + 发布策略 + 运行态）。
- `identity-queue.py status|save`：卡片读的 CLI。
- `identity-batch.py`：逐轮驱动器。`--limit` 是**上限不是目标**，待补不足就少跑；找不到的不会拿已判过的重试凑数。
- `lib/job_run.py`：新增 `identity` 作业（配置 `batchSize`/`cohortSize`，进程存活判断，进度文件 `var/job-identity-progress.json`）。它和 `links`/`selection` 是同一套：`start()` 先删旧进度、同时只跑一个（409）、存活问进程表。

**进度口径**：读/建链那套用「已判定/总数」；这里用 `claimed`（本轮实际领到多少个 handle）÷ `min(上限, 开始时待补)`。**不能用「开始时待补 − 现在待补」**——另一个常驻准备任务还在持续导入 Kalodata 线索，队列会一边补一边长，那个差值会出现「补了 20 个却显示已处理 0」的假象（实测踩到）。

## 实测

- 一次真实轮次（20 个 handle，`cohort-size 20`）：`claimed 20`，**找到 OECID 14 个、查不到 6 个**，耗时 68 秒；已就位达人 1,606 → 1,620，平台写入 0。
- 未在验证中放大批量：按此速率，2,000 个 handle 约 2 小时，所以上限默认留 2,000 但**由用户决定点不点**。
- 通道策略从 `identity_runtime_policy` 读，实测 `12 QPS · 9 通道 · accept-ef6d3f880d256fe68deaab01818d`。**读不到时不能冒充**「3 QPS 默认」：卡片区分 `published`（已发布）和 `readable`（读得到），读不到就明说。这个库**会被别人长时间占着**（见下节「停下来的时候必须说清为什么」），所以读策略现在会等，等不到才报具名码。

### 速度到底被什么卡住（实测拆解）

20 个 handle 的那一轮，cohort 报告里：29 次请求（1.45 次/handle），请求开始跨度 30.6 秒 → 平均 0.95 次/秒，**只有 12 QPS 上限的 8%**；相邻请求间隔最小 0.085 秒（≈1/12 秒，节拍器只在突发时生效）、中位 0.101 秒、**最大 20.3 秒**。全部请求耗时里**网络 31.4 秒（10%）、客户端+节拍+验证 287.1 秒（90%）**；最慢的 3 个各约 34 秒，其中网络仅 2 秒。**20 个 handle 里 9 个触发验证挑战（45%）**，而 500 人验收那次 500 人只有 9 次（1.8%）——所以 84.6 人/分钟不能套到补身份上，按本次实测 17.6 个/分钟更诚实。

### 停止按钮（2026-09-15 追加）

一跑可能 1 小时以上，所以补了「停止」：

- `job_run.stop_path()` 写 `var/job-identity.stop`，**不是信号**——由驱动器自己决定安全退出点；`state()` 报 `stopping`，`start()` 连同旧进度一起清掉这个文件（否则新批次第一轮就会被旧停止请求干掉）。
- 驱动器**只在两轮之间检查**它：一轮是平台工作的最小单位，半途放弃只会留下一个结算到一半的 cohort，换不到任何收益。所以按钮语义是「跑完当前这一轮就停」，卡片上的按钮文字就是「本轮到点就停…」。
- 实测：`--limit 3 --cohort-size 1` 起跑后请求停止 → 第 1 轮跑完即停，`stopReason=stopped_by_operator`，没有开第 2 轮，`platformWrites 0`。
- **顺手修掉一个真 bug**：`--cohort-size 1` 时 `creator-profile-refresh.py` **根本不走 cohort 路径**（它走单条 discovery），返回里没有 `targets`，进度会一直显示「本次已领 0」。所以可选的每轮大小收紧为 **10 / 20**，1 被拒绝——不是限制能力，是不让进度条撒谎（要小批量就把「一次补多少」设小，那是上限）。

## 一条稳性教训：过期的配置文件不能拖垮整块面板

收紧 cohort 取值后，`config/identity-run.json` 里旧的 `cohortSize: 1` 让 `validate` 抛错，而 `job-run.py status` 是**一次读全部作业**的 → **`/api/identity-queue` 和 `/api/catalog-jobs` 同时 503**，两块面板一起黑。

修法分两层：`load_config` 遇到过期文件**退回默认值并把 `configInvalid` 报出来**（不静默改用户的文件，下次保存才重写）；桥接层对**历史运行记录**里的 config 同样回退到当前 config——运行记录是历史，规则收紧不该让页面消失（卡片本来也不展示 `run.config`）。

## 停下来的时候必须说清为什么（2026-09-15 第二次追加）

第一次真跑补 OECID，**第二轮停在 `internal_error`**，进度文件里只有 `rounds:2 / claimed:20 / stopReason:internal_error / errors:[]`——一个数字都对，但**没有一句话能说明发生了什么**。查出两层原因：

**① 真正的原因：账号通道配置那张表被别人占着。** `production_policy()` 以只读方式打开 `var/batch-tasks.sqlite`，而那个库是**回滚日志模式**（不是 WAL），常驻的 `scripts/batch-preparation-worker.py` 会持有几十秒的写事务，这期间**所有读者都读不了**。实测 10 分钟轮询到的占用时长：**74.3s / 56.8s / 42.6s / 23.9s**，可读间隙 50–129 秒。只读连接用的是 sqlite **默认 5 秒** busy 超时，于是 `sqlite3.OperationalError: database is locked` 从 `run_cohort` 裸抛出去，被 CLI 的 `except Exception` 压成兜底码。**旧行为下大约一半的轮次会这样死掉**——报错时那一轮活到 5.26 秒，正好是 5 秒超时。

**② 兜底码本身不可诊断。** CLI 不留 traceback、桥接和页面也没有装它的字段，所以页面上只剩「停在 internal_error」。

三处修法（都不是把错误藏起来，而是把口径做对）：

| 位置 | 改法 |
| --- | --- |
| `lib/identity_acceptance.production_policy(..., wait_seconds=120)` | **等**被占用的库（1 秒重试，预算内不算失败）。只有一直读不到才抛具名码 `identity_policy_unreadable`。**策略行与自己的验收证据对不上是另一回事**：抛具名码 `published_identity_policy_invalid`，不重试、不冒充。 |
| `identity-batch.py` | 新增 `RETRYABLE_CODES={'identity_policy_unreadable'}` / `RETRIES=3` / `RETRY_WAIT_SECONDS=5`：这个码只可能来自**轮次开始前**那次读取（还没碰到平台），所以重试**不计入轮次、不动队列**；连续用完才如实停下并保留该码。进度文件与页面新增 `retry`，显示「正被别人占用，已重试 N 次，还在等——不是卡死」。 |
| `creator-profile-refresh.py` | 兜底分支把**完整 traceback 写进 `var/identity-worker-crash.log`（0600）**，stderr 只报**异常类型**并指向该日志。驱动收进 `report.errors`（最近 2 条、每条截断 1200 字），`job_run` 转发，桥接加 `errors`，卡片在停止原因下显示「出错的那一轮留下的线索」。 |

两个刻意的取舍：

- **原始异常消息不进接口回答**。既有测试 `test_cli_rejects_extra_path_or_route_fields_and_sanitizes_errors` 就是钉这个的（第一版把 `type: message` 塞进 JSON，被它当场抓住）；远端原文/凭证只能落本机日志，接口只给**异常类型**＋日志位置——类型足以区分「库被锁」和「字段缺失」。
- **状态卡只等 6 秒**（`identity_queue.policy(..., wait_seconds=6)`）。补号批量可以等两分钟，页面不行：宁可说「读不到」，也不能干等几分钟，更不能拿 3 QPS 默认值冒充已发布通道。

验证：

- 单元：`BEGIN EXCLUSIVE` 下**具名拒绝**；占用 6.5 秒（>5 秒默认超时）时**等到并读到 12 QPS**；`result_hash` 不匹配时具名且不重试；驱动重试 2 次后照常跑完（`rounds` 只加 1）；一直被锁时停在 `identity_policy_unreadable`；崩溃只报类型＋日志、日志 0600。**每条新测试都反向验证过**（退回单次读取→2 条变红）。
- 真实端到端：人为把 `batch-tasks.sqlite` 独占 25 秒后再真跑一轮 → **等过去并正常完成**：`rounds 1 / claimed 20 / found 14 / notFound 6 / errors [] / platformWrites 0`（旧代码在同条件下约 5.2 秒就 `internal_error`）。
- 现场 4 分钟轮询：3 次读到被别人占用的库，分别等了 **42.1s / 24.9s / 42.1s 并全部成功，0 次裸 `OperationalError`**。

**剩下一个没动的隐患**：`batch-preparation-worker` 在回滚日志库上持几十秒写事务，同样会挡住页面读 `batch-tasks`。把 `batch-tasks.sqlite` 改成 WAL 能根治，但那要动一个正在跑的活作业，未擅自做。

## 边界（保持）

- 发送与 AI 自动回复仍然暂停；这张卡只补身份，不发送、不建链、不选入、不写任何平台业务数据。
- 吃的是采集账号（ACC6）的额度，**不占 Kalodata 日额度**。
- 搜索不到的 handle 永久保留、如实计数，不删除、不伪造、不进池；以后有了新来源或真实 OEC 证据可以再补。
- 页面上的「搜索不到」在发送池卡片里按**线索**显示，身份卡里同时给达人和线索两个数，避免两个口径互相打架。
