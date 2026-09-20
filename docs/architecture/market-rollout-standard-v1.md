# 多市场接入标准 v1

状态：当前扩展合同。基准市场：意大利。确认日期：2026-09-20。

## 1. 目标市场

| 市场 | canonical key | 当前状态 |
| --- | --- | --- |
| Belgium | `be` | planned |
| Brazil | `br` | planned |
| Germany | `de` | planned |
| Italy | `it` | V1 enabled |
| Japan | `jp` | planned |
| Malaysia | `my` | planned |
| Mexico | `mx` | planned |
| Netherlands | `nl` | planned |
| Philippines | `ph` | planned |
| Singapore | `sg` | planned |
| Thailand | `th` | planned |
| United Kingdom | `uk` | planned |
| USA | `us` | planned |
| Vietnam | `vn` | planned |

`planned` 不生成业务页面、数据库、任务或执行权限。旧 BDHub/vendored 协议中的市场登记和 canary 只作证据，不自动升级新项目状态。

## 2. 意大利参考标准

一个新市场只有同时通过以下九层，才能进入该市场的合作工作台：

1. **市场元数据**：平台 market code、host、locale、currency、语言、市场时区和运营日界线明确；
2. **机构与账号**：机构归属、通信/供给角色、账号亲和、身份文件、维护执行器和锁都能核验；
3. **逐能力状态**：Find、Profile、货盘、Campaign、Product List/TapLink、IM read、send 分别登记 `enabled/canary/pending/unsupported`；
4. **商品材料**：Offer 字段不跨活动拼接；标准分佣、命名、`currentListId` 和周期刷新都能回到证据；
5. **线索**：A 类销售窗口、B 类代表视频窗口、币种比较和额度断点按该市场验收；
6. **身份与关系**：稳定键为 `market × OECID`；handle 只作别名；冷却、拒联、人工接管按达人统一；
7. **冻结发送**：预览 hash、formal/reserve、freeze revision、账号、Offer、`currentListId`、话术和顺序不可变；
8. **结果核验**：卡＋文字逐项确认；单达人限制、账号额度、平台拒绝、暂停和 unknown 分开；unknown 只核验原账号与原 requestRef；
9. **回复政策**：该市场语言的固定模板和人工真值通过评测，自动回复仍需单独用户启用。

任何一层只有 canary 或 pending 时，只开放已经验收的只读/单条动作，不显示批量执行按钮。

## 3. 页面与 API

- canonical 页面使用 `/{market}/catalog`、`/{market}/creators`、`/{market}/workspace/{send|inbox|history}`；
- 服务器先校验 market 是否产品启用，再选择固定配置、数据库范围和账号；market 不参与任意路径或 shell 拼接；
- 只有一个市场启用时显示固定市场标签；两个以上启用后才显示切换器；切换器不提供“全部市场”；
- 跨市场汇总若以后需要，是独立只读报表，不与发送、建链、账号或批次动作共用页面。

## 4. 上线证据

每个市场的上线记录至少包含：

- 当前配置和能力矩阵版本；
- 账号/机构/market identity 证据；
- 只读真实接口回执和字段覆盖；
- 单条写入 canary 的持久意图、回执和读回；
- 失败、超时和 unknown 的反向测试；
- 本地数据库备份与恢复检查；
- 页面 API 恒等式、TypeScript、构建和相关 Python 测试；
- 用户明确启用该市场的记录。

意大利成功只能作为实现模板和验收清单，不能替代其他市场的现场证据或用户授权。
