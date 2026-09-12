# 意大利画像 HTTP 补全实测

2026-09-12。**已复用墨西哥现成的完整 HTTP、平台验证和原请求重放流程，完成意大利二发固定 48 人名单测试。31 人取得当前同 OEC 画像，26 人的本次 22 项允许字段全部有值；17 人未找到精确 handle。** 原先“需先人工完成验证”的结论已被这次实测取代。

## 实际结果

先对固定 3 人跑完整流程，再对余下 45 人分 15 批、每批 3 人串行测试；首轮已观察的成功和未匹配目标均未重复抓取。使用 ACC6 的意大利身份、EU Host/签名/验证资源与 market=8 页面，严格 1 QPS，未切账号。

| 项目 | 结果 |
| --- | ---: |
| 固定来源目标 | 48 |
| 精确 Find → 同 OEC Profile 成功 | 31 |
| 22 项允许字段全部有值 | 26 |
| 部分字段可用 | 5（2 人 21 项、2 人 19 项、1 人 6 项） |
| 无精确 handle 结果 | 17 |
| 平台验证 / 验证成功 / 原业务重放成功 | 1 / 1 / 1 |
| Find/Profile 业务请求（含重放） | 111 |
| 旧数据库写入 / 真实发送 / 模型调用 | 0 / 0 / 0 |

111 是业务请求计数，不包括 SDK 自举、验证码资源和验证子请求。16 个批次均正常结束，账号身份文件前后 SHA-256 均不变，临时 SDK/验证文件已清理。旧 Dashboard 健康回读为 ok；没有改变生产任务或服务。

31 人中，27 人当前 OEC 与历史提示一致，另外 4 人此前没有历史 OEC 提示。17 个未匹配目标也都没有历史提示；不回退近似搜索结果，不据此宣称达人不存在。当前 handle → OEC 的观察与历史 Kalodata ID 的跨来源关联仍分开保存，后者不是本次 Profile 接口能证明的事实。

目标 completed 表示本目标流程结束且当前平台身份核验通过，不等于所有字段完整。单个目标只有 6 项可用字段的情况也被保留；未返回的经营指标继续保持未知。

## 补回了哪些信息

| 字段 | 31 个当前画像中有值人数（含明确零值） |
| --- | ---: |
| handle、OEC、市场、粉丝、来源统计截止时间 | 31 |
| GMV、视频/直播 GMV、销量、均播 | 30 |
| 视频/带货视频发布次数、直播/带货直播次数 | 30 |
| GPM、直播 GPM、视频 GPM | 30 |
| 商品价格范围 | 28 |
| 商品行业分布 industry_groups | 28 |
| 来源分组 content_groups | 28 |
| 代表视频数量 | 29 |
| 合作品牌 | 30 |

content_groups 实际出现 video_gmv、showcase_gmv 等键，不能当作视频主题分类；保留来源键和权重，不猜测统计语义。代表视频只保存数量和允许结构键，不保存媒体 URL，也不当作总发布数量。金额保存精确十进制/原显示符号，范围与单值区分；不自行推断币种或统计窗口。0、无值、缺字段、未授权和错误分别保留。

用户已确认的匹配政策不变：分析忽略画像年龄，价格带与内容形式只作参考。新增这些字段并不把它们提升为主要匹配依据。

## 为什么上一轮没抓到，怎样复用现有实现

上一轮探针直接调用 _signed_post_once，首个 Find 虽然是 HTTP 200/code 0，却携带 bdturing-verify，随即停止。它只证明需要验证，没有验证墨西哥完整流程是否可用。原始证据仍保存在 var/italy-profile-probe-20260912-initial/。

这次新项目调用顺序为：

1. 通过旧账号 provider 只读准备本账号意大利 identity，并校验已锁定 runtime manifest。
2. _configure_market_signer_runtime 配置 EU 签名；_configure_market_captcha_runtime 配置现成 EU 验证资源。
3. 创建独立 Session/client，再由 _configure_market_transport 配置意大利业务 Host/Origin/Referer。
4. 调用 client.post()。现成运行包自行完成验证、同 Session 内 Cookie/fp 更新、signer reset 和同 stage/body 的原请求重放。
5. 最终仍检查无验证要求、HTTP 200、整数 code 0、精确 handle、同 OEC 和 IT 市场，才接收画像。

没有重新实现验证器，也没有把 MX URL/凭证直接套给意大利。身份始终留在原账号边界，临时 SDK/验证材料只存在新项目本次 var/.../temporary，结束后清理。

每批保留父进程 180 秒、子进程 175 秒总截止；截止异常穿透运行包的普通异常捕获。运行包最多 3 次验证和 2 次瞬时业务重试，实际计数逐项记录。最终错误或账号维护/占用会停止后续批次，不盲目循环。标准互斥 guard 以只读文件描述符持锁；不写旧 lease 文件，其他标准账号领取可能短暂等待。Session/signature SDK 在 guard 释放前清理。

## 分型与后续请求量

本轮明确对照 Find → Profile [1,2,6] → Profile [2]；只有身份/粉丝仍缺时才尝试 [1,6]。旧 MX 核心完整性要求含 GMV，而 IT 只要求身份/粉丝，不能用旧的“核心完整”判定跳过经营字段补抓。

前两位目标的 Find 有 14 项有效字段，组合 [1,2,6] 新增价格、4 类次数和 GPM 共 6 项，单独 [2] 又增加直播/视频 GPM 两项。这说明“组合包含 2”不等于响应字段一定覆盖单独请求 [2]。

对已保存的 31 份成功观察做离线重组，Find + [2] 与本轮完整结果逐字段和值全部相同。因此后续可优先验证 Find → [2] → 按缺项补 [1,6]，避免一律发两次 Profile。这是同批响应的离线对照，尚不是省略 [1,2,6] 后独立运行的真实验收；本轮实际 111 次请求不能改写成预计的 80 次。

## 数据输出与验证

本轮数据留在新项目独立 var，未覆盖 matching/second-pilot 库，也未改旧库：

- 首轮现场证据：var/italy-profile-probe-20260912-full-http/。
- 其余 15 批：var/italy-second-profile-probes-20260912/，含固定来源与初始报告哈希、逐批目标、请求和字段状态。
- [汇总](../../var/italy-profile-live-completion-20260912/summary.json)及[可供后续导入的当前画像](../../var/italy-profile-live-completion-20260912/current-identities.private.json)：成功与未匹配名单分开，当前平台身份和历史来源身份不混同。
- 公开聚合验收：[italy-profile-completion-validation-20260912.json](italy-profile-completion-validation-20260912.json)。不包含 handle、OEC 或私信正文。

上一轮 var/italy-profile-field-completion-20260912.json 是对 3,978 份 7 月 30 日已有快照的白名单整理：3,830 人有来源分组、3,550 人有代表视频数量、3,663 人有品牌信息；价格/次数/GPM 8 项全部为无值。它仍是历史基线，不能改称本次实时全量刷新。本次按当前 OEC 精确关联该基线的 20 人全部有新增字段；其余 11 人没有该固定基线，不计算“相对历史新增”。

49 项离线测试通过，覆盖同身份 EU 配置、验证重放、请求级计数、精确 handle/OEC/市场、字段状态和精确金额、敏感诊断过滤、截止穿透、guard 内清理、固定名单去重、错误后整批停止以及汇总来源校验。

在新项目根目录复现离线验证：

```sh
PYTHONDONTWRITEBYTECODE=1 /Users/bjn00003/BDHub/01-BDSystem-V2/.venv/bin/python -m unittest discover -s tests -p 'test_profile*.py' -v
```

实时小样入口为 scripts/probe-italy-profile.py --account acc6，必须使用新的输出目录，不能覆盖原证据。48 人分批入口为 scripts/probe-italy-second-profiles.py；它读取已成功结束的首轮报告，排除其已观察目标，固定同账号，任一批次错误后立即停止。scripts/summarize-italy-profile-probes.py 仅聚合本地报告，不产生新平台请求。
