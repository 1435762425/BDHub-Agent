# 意大利全托商品按销量300件门槛选入

2026-09-14，用户在销量和评分分布统计后明确授权：将累计销量≥300件且评分、佣金差达标的商品加入已选池。这里只执行全托商品选入，不触发TapLink创建、Kalodata查询、消息发送或AI回复。

## 范围与规则

- 来源：IT/ACC6，高机会商品中的仅全球销售商品，完整查询快照`it-global-20260914`。
- 累计销量≥300，包含恰好300件；评分≥4.0，暂无评分允许；总佣金率－公开佣金率≥2个百分点；不检查库存数量。
- 快照三条件通过2291个，其中1692个当时已选、599个当时未选。恰好300件的合格商品有8个，均在未选部分。
- 评分0的字段语义尚未证实，本批≥300件候选没有评分0商品。执行时遇到尚未解释的0/缺失评分先保留原因，不伪造为真实差评或已证明暂无评分。
- 累计销量解析适配意大利文本：`6.831 已售`为6831；`12,2K 已售`约12200，保留来源值。佣金按平台整数百分之一百分点换算，差200对应2个百分点。

## 选入流程

599个未选PID冻结至独立台账。15个一组先查当前已选池，已存在则跳过；再读取这些PID的当前全托列表，复核销量、评分、佣金差。提速后的默认路径复用旧版原生快速选入：将当前全托列表活动引用传给选入接口，再从已选池回读真实type8/9绑定。已用真实商品验证列表引用可用于选入，但不能将它直接作为后续TapLink绑定；建链仍读取已选池的实际活动。二发所需剩余45天和链接条件属于后续准备。

每个PID提交前先持久化意图，再发一次选入POST。商品卡创建和IM接口不在此会话写入白名单中。只读回查已选池后才记确认，来源原快照不改写。实时选入观察另附在货盘API和页面中。

同账号8条独立HTTP通道共用8 QPS请求上限，空闲通道持续补位。遇到拒绝或验证就停止补新请求，收齐在途后处理。平台验证复用旧版`CommerceTransport.resolve_selection_verification`与`commerce_verification.verify_session`。只处理同账号当前挑战并更新内存会话，不回写旧身份文件。原请求不直接回放。对HTTP200/code10000且明确verification=true、ambiguous=false的拦截，只有随后同时确认“已选池无该PID”和“当前全托列表fs_is_selected=false”，且当前三项条件仍满足，才保存verified_not_selected证据并在原意图内继续一次新尝试。旧回执、选入引用、双重读取证据保存在priorAttempts，最多2次这种已核验续做。code0未回读、超时、其他异常不走此路径。限流、验证失败或其他未处理异常停止当前执行。

## 状态与运维

记录：`var/global-selection.sqlite`中的`intake_run`和`intake_item`。本批ID：`select-a6a8fd3061845f719166608d`。汇总输出：`var/global-selection-status.json`。本地命令：

```
PYTHONDONTWRITEBYTECODE=1 ../01-BDSystem-V2/.venv/bin/python scripts/select-global-products.py status
PYTHONDONTWRITEBYTECODE=1 ../01-BDSystem-V2/.venv/bin/python scripts/select-global-products.py verify
```

`execute`默认使用原生列表引用、8通道连续补位；只处理pending，以及经过上述双重核验证明未选入的明确验证拦截。已确认、超时和其他未知不会被重新提交。`execute-serial`仅作串行诊断。`verify`只读取已选池。相同来源与规则生成相同批次ID；单进程锁和单次提交状态保护避免重复执行。

货盘页保留采集时已选/未选统计，并单独展示本批待选总数、新选入已回查、当前已选跳过、尚未提交、条件变化、待核验等。某PID选入回查成功后，行内显示“已选入（已回查）”。这些数字不等于有效TapLink数量。

## 验证

已通过24项相关离线测试和Next.js生产构建、TypeScript检查；单PID真实选入与已选回查已通过。批次运行结果以持久台账为准，不根据提交响应估计成功数。


## 本批完成与提速结果

599/599未选候选均已真实选入并回查确认，pending/result_unknown均为0；最终36个验证拦截经双重核验继续原意图后全部确认。原快照三条件合格2291个，其中原已选1692个没有重复提交。没有发送消息、创建TapLink、修改旧库或旧身份文件。

速度目标“完整处理至少5倍”**尚未验收通过**。串行清洁完成段约17个/分钟。8通道加逐件详情的40人轮次确认35个、51.55秒，约40.7个/分钟。原生列表连续补位的30商品轮次确认29个、45.84秒，其中选入阶段6.15秒，验证31.407秒；排除验证的其余处理约14.43秒，但不能把该值当完整速度。最后36商品核验续做轮次全部确认，用时52.67秒，约41.0个/分钟，包含验证17.309秒与原拒绝核验/新提交/回查。两种样本工作量不同，不能挑取最快阶段宣称达到5倍。

证据：`var/global-selection-fast-detail-benchmark.json`、`var/global-selection-native-canary.json`、`var/global-selection-native-group-benchmark.json`、`var/global-selection-pipeline-benchmark.json`、`var/global-selection-status.json`。早期报告verificationAttempts仅统计父会话，不能代表所有通道事件；实际验证耗时与事件见stageMetrics.verification。后续已补齐通道统计和验证子过程attempts/error_code记录。

目前瓶颈是平台验证及恢复的耗时和频率；本批没有剩余新商品可继续做真实速度试验。下一批需要同时验证整体吞吐、验证频率及其耗时，不能用重复已选商品模拟新增选入。
