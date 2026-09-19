# 作业面板与 Kalodata 抓取身份

2026-09-14 用户要求：作业不要只靠定时，每个作业都要能单独手动触发；定时做成可选开关并在前端显性呈现。同时为 Kalodata 抓取加一个账号身份模块：填插件激活码、获取身份、刷新身份、测试身份。

## 作业面板

`scripts/lib/jobs.py` 登记作业，`config/jobs.json` 存用户意向，页面在货盘页底部。

| 作业 | 手动入口 | 现状 |
| --- | --- | --- |
| 商品发现 / 刷新 | `/api/global-source`（主动采集） | 已接 |
| 商品筛分 | `/api/catalog-screen`（重新筛分） | 已接 |
| 达人线索查询（Kalodata） | — | 未接，见下 |
| 达人档案刷新 | — | 未接（单品入口在“稳定身份”页） |
| TapLink 链接准备 | — | 未接 |

两条硬规则：

- **没有已验证入口的作业显示“尚未接入”，不给按钮。** 面板不提供会假装成功的按钮。
- **定时开关默认关闭，且定时器尚未建立**（`schedulerReady:false`）。开关只记录用户意向，页面明确写出“不会自动运行任何作业”。不把定时设成唯一入口。

上次运行时间只在真有记录时显示：采集取 `global_source_run.updated`，筛分取 `global_source_screen_run.updated`，链接准备取 `catalog_prepare_run.updated`。缺失显示“尚未运行”，不显示 0。

## Kalodata 身份模块

`scripts/lib/kalodata_identity.py` 只做适配，不重写抓取器的登录流程。

| 动作 | 做什么 |
| --- | --- |
| 状态 | 只读文件：cookie 是否存在与最后更新时间（**不读内容**）、扩展代理是否配置、上次测试结论、专用 Chrome 是否打开 |
| 测试身份 | 用**生产读路径**（`batch_source_runtime.kalodata_provider` + `cycle_kalodata.PATH`）发一次真实达人列表请求 |
| 获取身份 | 把激活码通过 `KALODATA_ACTIVATION_CODE` 传给抓取器的登录器，打开专用 Chrome 完成扩展激活 |
| 刷新身份 | 复用扩展里已保存的卡号刷新 cookie 与代理（`scripts/kalodata-extension-refresh.py` 调用其现成的刷新函数） |

激活码存 `config/kalodata-identity.json`（0600）。用户为本系统唯一使用者，明确要求激活码可在前端显示和填写。校验上**不做截断**：超长或含换行的卡号直接拒绝，避免把凭据静默改短。

### 判定口径（一处重要更正）

抓取器自带两套检查：

- `account_canary.py`：额外要求 Cloudflare clearance cookie，实测在真实抓取仍可用时报 `PRODUCT_DATA_UNAVAILABLE`。
- 生产读路径：`curl_cffi` Chrome 指纹 + cookie，**不依赖** `cf_clearance`。

因此**就绪判定以生产读路径为准**，不用 `account_canary.py`。判定结果：

| verdict | identityOk | 含义 |
| --- | --- | --- |
| `ready` | true | 拿到达人列表数据 |
| `quota_exhausted` | true | 平台接受请求并回额度用尽——**证明会话有效** |
| `auth_required` | false | 会话被拒，需重新获取或刷新身份 |
| `business_rejected` | null | 会话可能有效但请求内容未被接受，留原始错误再判断 |
| `unreachable` | null | 请求没走通，不是身份结论 |

### 边界

- 不复制 `latest_cookie.txt`、`.chrome-profile` 或任何身份文件进本仓库；只报告存在性与修改时间。测试断言 cookie 路径不在本仓库内。
- 获取/刷新身份会打开专用 Chrome 窗口，需要人工在该窗口完成登录；关窗即结束。前端在窗口打开期间每 5 秒轮询，窗口关闭后自动停止。
- 测试身份只发一次只读达人列表请求，不改平台数据、不发消息。

## 实测（2026-09-14）

- 生产路径探测返回 `quota_exhausted`，0.66 秒；即**身份有效、当日额度已用尽**。
- 额度是自有作业消耗的：`batch_source_job` 里 1867 个已完成、1 个 `kalodata_daily_quota_exhausted`、100 个排队、2 个待身份。
- `hasCfClearance:false`、`proxyConfigured:false`，但抓取不受影响。
- 作业面板的“重新筛分”按当前门槛重跑：筛出 2291、待入池 599。

## 未接部分

“达人线索查询（Kalodata）”的手动按钮尚未接。它对应的是 second-cycle 的批量来源作业（`batch_source_job`），接之前要定清楚：一次查多少条、额度用完怎么等、与现有队列的优先级关系。面板先如实显示为“尚未接入”。
