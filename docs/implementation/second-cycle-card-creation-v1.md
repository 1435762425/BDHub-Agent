# 二发闭环 B.6：四款商品卡创建与回查

2026-09-13。依据使用者继续准备此前4款缺卡商品的指令，按已选分佣规则逐款执行。实际创建4个新商品列表，全部通过IM搜索和成员回查；没有发IM消息，没有调用修改/删除旧列表接口。

## 实际结果

| 商品 | 达人佣金 | 结果 |
|---|---:|---|
| 颈椎枕 | 13% | 新建并回查确认 |
| 女士运动紧身裤 | 12% | 新建并回查确认 |
| 女士塑身套装 | 13% | 新建并回查确认 |
| 口腔片 | 13% | 新建并回查确认 |
| 数字万用表 | 13% | 复用已有匹配卡，本轮再次只读确认 |

4次创建POST、26次读取（逐款创建流程16次＋最终5卡联合回查10次），0未知结果、0发送、0模型调用。4个新listId互不相同，身份文件前后内容一致。重复执行首项返回already_verified，新增创建请求0。

意大利tap_link仍为canary，本轮每次CLI最多提交1次创建请求，逐项核验后再处理下一项；未把能力标成enabled，也未开放大规模后台创建。

## 代码与执行合同

- cycle_card_creation.py：每款独立持久意图、冻结方案/计划版本/唯一列表名称、started、response_saved、verified或unknown状态。同PID有未决意图时不能换新ID绕过；其他项有未知创建时不能继续写。
- create-cycle-card.py：prepare只建立本地意图；execute-one复查当前已选商品、分佣规则、45天/库存/可用条件及已有匹配卡，确缺时才创建。复用已匹配卡不产生创建POST。
- POST路径固定campaign/product_list/create，selected路线外层campaign_id=0/source=2，items内保留真实来源Campaign及精确creator_commission_rate。账号IT/ACC6、机构指纹与货盘来源一致。
- 创建前先短事务登记started；网络超时/未知回执保留unknown，不能重发。返回候选listId先保存，再按精确listId/名称、PID、来源Campaign、wire Campaign、佣金和成员状态回查。
- verify-one只读取；没有候选listId时须按唯一意图名称找到确切卡片，不能用任意同PID卡假装创建成功。
- 卡片验证回写当前offer指纹的准备结果。发送前仍须重新核验商业条件和关系/额度，不因本轮卡成功开放消息发送。

## 使用入口

```sh
# 仅准备本地缺卡意图
python scripts/create-cycle-card.py prepare --report var/create-NEW/intents.json
# 每次只处理一个明确意图；使用项目既有兼容Python环境
python scripts/create-cycle-card.py execute-one --id <intent-id> --report var/create-NEW/one.json
# 未决结果只读回查，不能代替再次创建
python scripts/create-cycle-card.py verify-one --id <intent-id> --report var/create-NEW/verify.json
```

Python环境实际使用旧项目.venv仅复用依赖和读取配置，不写旧业务库。命令不会默认为全部意图执行；报告新路径不能覆盖历史。名单/冻结正文与实际消息试点仍是下一层。

## 验证与证据

9项创建账本、11项材料/卡查询、3项API检查通过，共23项；类型检查/构建通过。覆盖重复创建、同PID未决、跨项未知阻断、计划暂停、候选回执ID不符、错误PID/点位、复用与只读恢复。页面已显示5款“已核验达人佣金／商品卡预检通过”。

证据在var/cycle-card-create-it-20260913，包括4次执行、重复运行和全卡回查报告；creation账本保存在var/second-cycle.sqlite。意图名称在首次外部提交前采用完整短词规范化，最初intents.json仅作准备草案，实际名称以账本和执行回查为准。

新Web PID84837，身份Worker及旧服务未重启。之前B.5的4卡缺口为历史状态，已由本次结果覆盖。

## 下一步

将已核验OEC、当前卡片、v4话术与关系/额度条件组装为具体可审阅的小批次；未完成这一层，不调用真实发送。旧被否定的暂停试点不恢复，不把旧批准转给新正文。
