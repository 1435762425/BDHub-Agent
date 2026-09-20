# 会话工作台、模板库与互斥回复窗口

日期：2026-09-20。范围：意大利 V1，本机 BDHub-Agent。

## 目标

本轮把三个此前混在合作工作台里的问题拆开：二发话术单独管理、人工会话集中处理、Agent 固定场景在独立窗口执行。完成条件是页面和运行链都使用真实台账，默认突出需人工事项，并且 Agent 与二发不会同时外发。

## 产品结果

- 合作工作台只保留“发送”和“结果与历史”；旧 `/it/workspace/inbox` 重定向到 `/it/conversations`。
- 新增一级“会话工作台”，默认 `human` 队列；二级页为人工回复模板和 Agent AI 回复设置。
- 会话详情采用队列、时间线、达人/事项三栏；支持草稿、人工模板、文本、当前有效商品卡、中→意翻译和事项完成。图片入口因发送适配器无图片端点而明确禁用。
- 二发模板成为发送页独立卡片，直接展示五个内置模板全文；自定义模板只允许 `{creator_handle}`、`{product_name}`、`{creator_commission}`，并保存不可变 revision。
- 二发模板、人工回复模板、Agent 三条固定模板使用独立表和读取入口，互不混用。
- 达人线索页不再暴露与当前政策冲突的可编辑条件，改为集中表格展示 A/B 固定规则和真实额度停止条件。

## Agent 执行合同

| 动作 | 执行 |
| --- | --- |
| `no_reply` | 不发消息，推进处理水位并解除本次回复冻结 |
| `sample_self_service` | 发送固定样品自助模板 |
| `collaboration_ack` | 发送固定合作确认模板 |
| `link_usage` | 仅唯一 PID/有效 `listId` 时发送固定使用说明 |
| `human` | 永不自动发送，创建或复用人工案件 |

已有 `turn_review` 时人工标准动作优先；否则 DeepSeek 分类仍须通过证据和唯一 PID 守卫。三种固定回复进入持久 `service_reply`，复用现有 ACC6 写门禁、request ref、回执和精确回查；`inflight/accepted/unknown` 只核验原意图。

默认北京时间为 Agent `15:00–16:00`、缓冲 30 分钟、二发 `16:30–24:00`。设置保存和二发窗口保存都会拒绝重叠。等待二发窗口不阻塞 Agent，只有实际 dispatch 或在途组件才阻止另一条写链。

当前 `enabled=false`，未启动 Agent worker，也未执行真实回复。

## 数据与接口

`second-cycle` migration v10 新增：

- `send_message_template / send_message_template_revision`
- `manual_reply_template / manual_reply_template_revision`
- `conversation_draft`
- `agent_reply_setting / agent_reply_run`

新增本机接口：

- `/api/template-library`：三个模板域和 Agent 设置；mutation 使用精确字段、JSON 类型、请求体上限和 revision。
- `/api/conversations`：默认需人工列表、会话详情、草稿、翻译、人工文本和商品卡。

所有人工/Agent 外发继续写既有 `service_reply`，不另建平行发送台账。

## 运行与数据证据

- 迁移前备份：`var/backups/state/20260920T114352Z-before-conversation-workbench`，21 库，凭据文件 0。
- 当前会话只读投影：总计 33，需人工 5、处理中 6、Agent 已处理 1、已完成 21。需人工只包含当前 pending 或开放案件；无 pending 的历史审核样本归入已完成。4 条有当前 pending、人工真值但尚无实体 case 的会话会投影为虚拟人工事项，填写处理结果后原子生成案件与 resolution，再解除冻结。
- 当前设置：Agent 关闭；回复 `15:00–16:00`；二发 `16:30–24:00`；发送模板保持 `video_focus`。
- 本轮平台写入 0、真实发送 0；未点击人工文本或商品卡按钮。

## 验证边界

测试、TypeScript、生产构建、文档检查和浏览器页面验收分别记录在当前 [交接页](../handoff/codex-takeover-20260919.md)。浏览器验收只读取页面和 API，不操作任何人工发送、商品卡、冻结或 Agent 开关。
