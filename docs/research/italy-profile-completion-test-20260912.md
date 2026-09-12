# 意大利画像补全测试

2026-09-12。参考旧 BDHub 墨西哥的抓取方式，已做一次意大利真实只读请求，并对已有画像做字段级补全导出。**实时补抓被平台验证要求拦住，尚未进入 Profile 对照；本地已有数据中可以补出内容类目、代表视频数量和合作品牌。**

## 墨西哥与意大利的差异

两者都使用`4partner/find`和`4partner/profile`。意大利必须使用EU Host、`market=8`页面、EU签名资源和本账号的意大利Partner配置；不能只换market参数却保留MX的Host或身份。

当前旧代码有两种不同入口：普通`creator_profile.py`默认Find后取`[2]`；Pure HTTP Worker仍用`[1,2,6]`组合。不能把旧文档中的组合方式当作所有入口的唯一默认。

MX的完整性条件包含handle/OEC/粉丝/GMV；IT只要求handle/OEC/粉丝。因此IT已有GMV以外字段缺失，甚至缺GMV时，都可能被旧入口判为“核心画像完整”。定向补抓也不会因缺价格、次数或GPM自动追加请求。

旧库只读聚合进一步确认：IT的4,523份7月30日快照中，价格、视频/直播次数原始字段都有授权壳，但没有`value`；GPM等也没有值。规范解析映射已存在，不是简单漏映射或新项目漏导。MX历史快照有这些字段值，但不能由此推导当前IT一定支持相同内容。

## 本次真实请求

准备3个意大利目标：2个有历史OEC提示、1个无提示。先严格Find当前handle，再计划同账号同OEC对照`[1,2,6]`与`[2]`；缺身份/粉丝才补`[1,6]`。没有命中时不回退首条，不将Kalodata ID当OEC。

当前只读readiness中，ACC6是唯一可启动采集账号。实际取得既有账号guard的只读互斥锁后请求：

| 项目 | 实测 |
| --- | --- |
| 请求 | ACC6，EU签名资源，意大利Find，严格1 QPS |
| 返回 | HTTP 200、code 0，但存在`bdturing-verify`且`x-tt-system-error=3` |
| 处理 | 按验证要求立即停止，没有重试、切账号、处理验证码或发起Profile补抓 |
| 结果 | 当前身份解析0，实时完成目标0；不能把成功外观的状态码当正常结果 |
| 旧状态 | 身份文件字节校验不变，旧业务数据库写入0，真实发送0 |

本次因此**不能判定`[2]`能否补回意大利的价格、次数和GPM**，也不能断言意大利接口不支持。需要在ACC6完成平台验证后，再运行同一小样。

脚本只调用`_signed_post_once`，不调用带自动重试/验证码处理的高层方法。父进程55秒、子进程53秒截止；取消和超时清理自己的进程组，SDK/Session结束后再释放guard。只有既有guard的只读文件描述符被锁定，不创建或删除旧lease、Cookie或业务文件；标准账号领取可能短暂等待。此方式仅供短期小样，不代替正式长期租约。

## 从既有快照补出的信息

固定新项目原有3,978个OEC，逐一核对原观测时间、handle和市场；从7月30日已有快照提取22项允许字段。没有新的平台请求，不是刷新日期。

| 字段 | 有值人数 | 说明 |
| --- | ---: | --- |
| 内容类目`content_groups` | 3,830 | 提取标签、来源ID与权重 |
| 代表视频`top_video_data` | 3,550 | 只提取数量和结构键，不保存媒体URL；另421人无值、7人未授权 |
| 合作品牌`partnered_brand` | 3,663 | 只提取品牌ID、名称及来源状态 |
| 价格、发布/直播次数、GPM等8项 | 0 | 3,978人原始响应全部无value，保留未知 |

代表视频数量分布：2,504人有3条，670人有2条，376人有1条。只代表来源提供的代表条目，不当作视频发布总数。

输出：[独立补全文件](../../var/italy-profile-field-completion-20260912.json)。它未覆盖当前matching库或旧数据库；后续接入可按OEC及原观测时间读取这些字段，不应误标成今日采集。输出不含bio/email/联系方式、头像、签名媒体URL或凭证。

## 验证和复现

19项Python测试通过，覆盖六种字段状态、嵌套零值、精确金额、同OEC/市场合并、无值不覆盖有效值、隐私字段过滤、固定导出身份、内容哈希，以及验证头优先于code 0、取消/超时清理。允许字段导出哈希和过滤模块哈希均独立回读一致。

聚合证据：[italy-profile-completion-validation-20260912.json](italy-profile-completion-validation-20260912.json)。原始私有请求摘要留在`var/italy-profile-probe-20260912-initial/`；临时SDK文件已清理。

在新项目根目录，使用旧环境只执行新脚本：

```sh
PYTHONDONTWRITEBYTECODE=1 /Users/bjn00003/BDHub/01-BDSystem-V2/.venv/bin/python scripts/export-italy-profile-completion.py
PYTHONDONTWRITEBYTECODE=1 /Users/bjn00003/BDHub/01-BDSystem-V2/.venv/bin/python -m unittest discover -s tests -p 'test_profile*.py' -v
```

平台验证完成后才重跑现场小样，使用新输出目录保留原证据：

```sh
PYTHONDONTWRITEBYTECODE=1 /Users/bjn00003/BDHub/01-BDSystem-V2/.venv/bin/python scripts/probe-italy-profile.py --account acc6
```

源码依据：旧`pure_http_worker.py::prepare_collection_accounts`、`pure_http_worker_child.py::_execute_target`、`creator_profile.py::required_profile_fields`、`hub/models.py::parse_find_item`和`profile_lease.py::_os_mutex`。没有改旧源码、生产任务或服务，也没有提高发送能力状态。
