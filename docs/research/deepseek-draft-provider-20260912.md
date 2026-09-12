# DeepSeek 草稿 Provider：本地实现与离线验证

2026-09-12。此文记录固定官方模型调用适配器，不代表已经启用业务模型调用。用户此前仅要求过 V4.1 Flash 费用测算；本轮真实试用范围及总预算仍由上层工作流确认。本文对应实现和测试均未调用模型、未付费、未修改旧配置。

## 已核对的官方接口

- 请求固定 `POST https://api.deepseek.com/chat/completions`，模型名固定 `deepseek-flash`，当前实际版本为 DeepSeek-V4.1-Flash。
- 官方说明旧名 `deepseek-v4-flash`、`deepseek-v4-flash-vision-exp` 仍接收请求，但旧模型已经下线，由 V4.1-Flash 服务。新适配器直接采用推荐名，不依赖别名。
- 显式 `thinking.type=disabled`、`response_format.type=json_object`、`stream=false`，稳定指令要求只返回 JSON 对象。没有工具调用和外部业务执行权限。
- 每次最多 24,000 UTF-8 输入字节（包括消息结构与补充 JSON 指令）、1,200 输出 token。HTTP 请求同时设置 socket 超时和最多 60 秒的实际总截止，覆盖响应慢滴和 JSON 处理。仅一次 HTTP 请求，不自动重试，不跟随重定向。

官方来源：[首次调用](https://api-docs.deepseek.com/zh-cn/)、[模型与价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing)、[思考模式](https://api-docs.deepseek.com/zh-cn/guides/thinking_mode)、[缓存](https://api-docs.deepseek.com/zh-cn/guides/kv_cache)、[响应 usage](https://api-docs.deepseek.com/api/create-chat-completion)。均在本日公开读取核对；未使用密钥查询模型或账户。

## 密钥与状态

`provider_status()` 只检查本地配置。`ready` 与 `credentialReady` 同义，均不代表密钥已经通过远端认证，`availabilityVerified=false`。

密钥按以下顺序只读到进程内存：

1. `DEEPSEEK_API_KEY` 环境变量。
2. 固定旧项目 `/Users/bjn00003/BDHub/01-BDSystem-V2/config.yaml` 的 `reply.api_key`。旧配置有效 base URL 必须是 HTTPS `api.deepseek.com`，路径只能为空、`/`、`/v1` 或 `/v1/`；拒绝其它域名、用户信息、查询参数、片段和非标准端口。

旧配置的 `reply.model` 不被继承；此前实查值是 `deepseek-chat`，不能视作用户已部署 V4.1。密钥不复制到新项目文件、不进入命令行、日志、返回值或异常。默认运行使用既有 Python 环境中的 `requests`、`PyYAML`，没有新增依赖；离线测试可注入配置读取器和 HTTP 对象。

## 调用与账本契约

`call_model(messages, *, max_output_tokens=1200, timeout=60)` 成功返回 `content/model/responseId/usage/cost/startedAt/finishedAt`。`content` 必须能解析为单个 JSON 对象，拒绝数组、重复键、非有限 JSON 数值、非 JSON 内容和截断输出。业务字段、事实引用和草稿质量由上层继续校验。

`usage` 保留以下可空整数，缺失不写成零：

- `promptTokens`
- `cacheHitTokens`
- `cacheMissTokens`
- `completionTokens`
- `reasoningTokens`
- `totalTokens`

官方同义字段 `prompt_tokens_details.cached_tokens` 可以提供缓存命中量；与显式命中量同时存在时必须一致。校验 `hit + miss = prompt`、`prompt + completion = total`，以及 reasoning 不大于 completion。推理 token 是 completion 的分项，不再单独加费。其他缺失量不反推。

报价单位为人民币/百万 token：空闲命中 0.02、未命中 1、输出 4；高峰分别 0.04、2、8。高峰为北京时间周一至周五 09:00–12:00、14:00–18:00。价格版本为 `deepseek-flash-cny-2026-09-12`。

`cost.estimatedCny` 用实际返回的三个计费分项及单一时段报价计算；`upperBoundCny` 始终使用峰值报价，供上层预算核算。使用 Decimal 输出十进制字符串，不做浮点金额累加。跨时段或时钟不确定时，估算金额留空，保留可计算的峰值上界；缺缓存或 completion 分项、usage 不一致、返回其它模型时，两个金额都留空且 `complete=false`。这是报价估算，不是平台最终账单。

明确未提交请求的本地失败可以记录费用 0，但 usage 仍全部为 null；它与“已经提交但未拿到 usage”不同。后者不能记录为免费。上层应保留预算预留，不盲目补发或重新生成。

## 失败与恢复

`DraftProviderError` 仅包含固定 `code`、`outcome` 和安全 `receipt`：

- `request_not_sent`：消息、限制或密钥检查失败，未进入 HTTP 提交。
- `outcome_unknown`：进入 HTTP 提交之后的超时、网络、非 JSON、HTTP 拒绝、usage 或输出校验失败。若已读到 usage，异常 receipt 继续保留计费分项和费用元数据；不会因坏草稿而丢失一次调用的成本。

receipt 不保存原始 HTTP 响应、头部、Cookie、密钥、错误正文或 `reasoning_content`。上层先持久化请求意图，再调用此适配器；成功或失败后均先保存 receipt，再决定下一阶段。

总截止通过主线程 `SIGALRM / setitimer` 实现，抛出独立 `BaseException`，避免被 HTTP/JSON 库的普通异常处理吞掉；成功和失败都恢复旧 handler 与 timer。若非主线程或已经有其它组件的 active alarm，则在提交请求前返回 `provider_deadline_unavailable`，不抢占或延后原有计时器。Worker 应在自己的主线程调用；额外的进程级监督可在外层使用子进程截止，不嵌套争用同一个 alarm。离线测试直接触发安装的 handler 验证截止和恢复，另在有 2 秒外层监督的子进程中，用 50 毫秒真实 alarm 中断阻塞管道，验证系统调用确实被截止；没有网络请求或 sleep。

测试文件 `tests/test_draft_provider.py` 使用假的 HTTP、时间和密钥读取器，覆盖报价、缓存、推理分项、输入限制、官方 provider 校验、错误回执、零重试和隐私输出。测试本身不请求任何远端模型。
