# 四市场 AI 正式回复开启记录

2026-09-26 01:08（Asia/Shanghai）。用户看过两轮真实来信模拟后明确认为 AI 回复可正式使用，并授权在不与二发冲突的前提下为每个市场开启。本次按该指令启用 IT/BR/MY/UK 全量；此前助理的主观评级不覆盖用户此次决定。

## 冲突核对

四市场实际二发窗口均为北京时间 16:30–24:00。核对的是 continuous_send / market_send_control 的持久生效值，不仅是 AI 设置中的二发时间副本。

| 市场 | AI 回复窗口（北京时间） | 二发前间隔 | 前一天二发结束后间隔 |
| --- | --- | --- | --- |
| IT | 00:30–03:00 | 810 分钟 | 30 分钟 |
| BR | 00:30–08:00 | 510 分钟 | 30 分钟 |
| MY | 09:00–16:00 | 30 分钟 | 540 分钟 |
| UK | 00:30–04:00 | 750 分钟 | 30 分钟 |

所有窗口均不重叠、满足原 30 分钟缓冲。worker 在调用模型前检查当前二发窗口和缓冲，提交前仍检查回复窗口、启用/停止及消息/控制版本；同账号真实发送共用写锁串行。本次未修改时间、缓冲、指南或执行代码。对以后在运行中修改二发窗口的所有竞态，本次没有做额外实现或保证。

开启前四市场均 pilot_complete，agent_reply 能力均 verified，enabled=true，没有未结 AI 发送意图；send_dispatch_active=false、reply_blocker=None。因此解除的是已完成首条试点后的等待继续限制，没有绕过首条验证或发送许可。

异常边界已向用户说明：某条服务回复如果 inflight/accepted/unknown 未结，二发 begin 会以 reply_reconciliation_required 阻止同市场新派发。这是避免重复/冲突的保护性等待，不是正常回复窗口重叠。AI unknown 有限核验后的自动隔离仍属后续范围，本次不宣称该异常已经不会影响二发。

## 开启动作与回读

按用户明确授权，通过页面同一 `/api/agent-replies?market=<market>` API 发起 `resume-full`，四市场均返回 stage=full、duplicate=false；原常驻 worker 按保存窗口处理。requestId 分别为 `agent-full-user-<market>-20260926-0105`，授权持久保存为 `agent-v2-full-run`。API 本身 platformWrites=0，实际回复由已有 worker 正常消费。

随后四个 GET 均读回 enabled=true、rolloutStage=full；设置、发送控制及指南 hash 与开启前完全一致，没有重启任何进程，没有修改旧项目。MY 自动运营主链的开关不随 AI 回复开启而改变。

01:07 的真实状态快照：从本次授权后创建的回复中，IT 已回读确认 11 条、BR 8 条、UK 4 条；MY 尚未到 09:00，状态 outside_reply_window。四市场二发均处于 waiting_window，授权后新建二发投递数为 0。这是正常授权业务发送，不是模拟测试；之后计数会继续增长。

UK 开始时短暂遇到身份任务占用账号的 ProfileBusyError，原 ready 意图保留、自动重试后已继续确认。BR 同样曾遇到账号占用并退避；这属于与身份读取的账号竞争，不是和二发窗口冲突。未终止身份任务抢占账号，未改请求重发。不能把短期 API 状态 full 等同于每个 worker 每时每刻都无等待。

## 证据

`var/releases/agent-full-run-20260926/activation.json` 保存用户授权摘要、四市场生效时间/缓冲检查、开启前试点状态与计数、四个 API 请求/回执、完整授权事件及开启后的实际业务快照。业务策略、窗口和 provider 没有变更；本次无代码改动，未重复运行与开关操作无关的全量测试。
