# 二发闭环 B.4：线索身份交接与回填

2026-09-13。Kalodata的23条新线索已通过现有身份发现Worker完成真实核验：15成功、8无精确handle匹配；成功项新增3个档案、已有12个。报告累计54次业务请求，0失败，未建链/发送/调用模型。

## 已实现

- `cycle_identity.py`冻结本计划未交接的Kalodata源边，按handle去重，同一批用稳定requestId提交CreatorDiscoveryStore；跨库提交完成但本地回执丢失时，重试取回同一个批次。
- 每条源边单独保存handoff、outcome和resolution；原source_edge不改写。Kalodata ID不是OEC。
- 回填核验同一发现item、IT、creatorId/OEC的discovery-result证据。只有completed或明确identity_only且证据匹配才绑定，不能只按库中同名查到就合并。
- 同一OEC沿用稳定creatorId，已有拒绝/人工控制不被来源导入解除。根据当前PID方案创建机会投影，原历史同品归属继续标未核验。
- 无精确匹配保留源线索与结果，但不永久占用“仍在解析”的准备容量；后续可继续补其他线索。
- source Worker在成功保存页面后自动冻结/提交身份交接；共享身份Worker每轮补回未交接的已保存源边、恢复提交回执，并核验/回填完成结果。没有新增独立常驻进程。
- outbox只有完成投影才标settled；绑定已写而投影中断时可恢复。settled批次不在每轮重复全量解析。

## 本次结果

批次`discovery_41f598820a704b39a8fabb542e9380dd`：23输入、15完成、8未匹配、0blocked，新增3/已有12，全部无在途项。新准备库34个稳定关系、73条来源边、36个达人×商品机会；新采集源边15/23已绑定OEC。

8条未匹配与原意大利来源的显式Kalodata-ID→OEC核验链对照，没有可复用的已知OEC链；没有因为改名删除已有达人。未匹配不等于永久不存在，也不猜首条搜索结果。

二次submit新增批次0、二次reconcile新增绑定0。UI显示已关联15/23、未精确匹配8和达人库入口。证据摘要：`var/cycle-identity-it-20260913/validation.json`；各项原始探针由身份模块留在其原目录，不另复制凭据或完整聊天。

## 验证与运行

9项桥接、31项底座、13项Kalodata、12项货盘、20项发现、20项刷新、3项API测试，共108项通过。类型检查/构建和页面点验通过。额外覆盖跨库提交中断、绑定后投影中断、缺证据拒绝、已有拒绝保持、未匹配释放待解析容量。

手动诊断入口：

```sh
python3 scripts/second-cycle-identities.py submit
python3 scripts/second-cycle-identities.py reconcile
```

两者只操作新项目数据及发现队列；submit会使现有身份Worker执行真实只读Find/Profile，所以不是单纯查询。CLI重复不会重新安排已交接源边。

在确认发现/刷新队列无queued/running后重启新项目身份Worker加载自动桥接：PID75937，Web PID75938。原模拟Worker和旧BDHub服务未修改。后续不用手动重复上述命令即可衔接新页面结果。

## 边界

已提交到发现队列的在途读取不能由上游plan暂停物理撤回；plan暂停阻止新的交接，发现批次自身的暂停入口控制后续领取。已经取得的身份事实仍可归档，营销控制保持。尚未宣称跨所有模块的统一暂停已经完整接通。

本轮没有放开真实发送，也未自动扩充新的Kalodata PID任务。商品短名称预处理、最新固定模板、可发卡准备、持续供给调度和完整IM回复仍需后续接入。
