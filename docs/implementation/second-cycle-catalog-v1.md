# 二发闭环 B.2：实时货盘与拟分佣方案

2026-09-13。已把意大利ACC6的已加入Campaign及已选商品两路只读货盘接入新准备层；尚未接全量全球销售发现池、自动每日调度、Kalodata持续Worker或真实发送。

## 真实读取结果

| 来源 | 成功请求 | 方案记录 | 去重PID | 覆盖 |
|---|---:|---:|---:|---|
| 已加入Campaign | 67 | 3361 | 2503 | 43个类型1/5/7活动；每活动按total_num完整翻页 |
| 已选商品 | 29 | 2852 | 2852 | 当前product_status筛选合同下完整已选池；根total_num完整分页 |

两来源合计6213条方案，去重5354个PID；不是6213个不同商品。按>45天、库存>100、明确可用及拟达人佣金优势判定：2213条方案、1715个PID满足准备条件。评分是软偏好。

原5款历史商品均在当前已选池有合格拟方案；31个稳定关系的33个达人×商品机会可关联。历史同品身份归属仍未核验，历史14天统计窗口不当作今天新查的14天数据。

## 实现与来源

- `scripts/lib/cycle_catalog.py`：纯字段规范化与逐页状态机；Campaign、selected两个来源、报名/推广时间分开。库存/佣金用Decimal；不可用状态、未来生效、缺失值有明确语义。
- `scripts/sync-cycle-catalog.py`：闭合白名单仅GET活动/活动商品、POST已选商品读取。冻结机构指纹、市场、账号及分佣规则，读取同账号身份；每页落新项目检查点，出错不推进游标。源分页总量变化/重复页停下，需要复核或新运行，不能假称完整。
- 冻结规则按旧workspace当前选中首项读取：Campaign为average/MX_AVERAGE/FLOOR_INTEGER，selected为margin/KEEP_MARGIN、机构保留2个百分点。保留规则哈希，不把旧命名模板或Boost措辞写进新话术。
- `creatorPercent`明确为`proposed_not_applied`，`cardBindingVerified=false`。它是按规则可准备的点位，不是已经建好链接或已经发给达人的佣金。
- 新完整货盘发布后，历史商业导入不再混入当前货盘列表；历史快照、source_edge和旧试点仍保留。按PID创建当前offer候选投影，不改写原Kalodata边，拒绝和人工状态不被导入解除。
- 来源边与当前方案分开，因此PID关联不证明那位达人已经接受方案。供应计算只把请求的当前14天窗口计入已有供应，旧窗口不会掩盖新线索缺口。
- `/api/second-cycle`返回全量统计、最多40条明细，优先原线索商品；工作台展示方案数量及拟分配/未建链说明。

## 运行方式

从新项目根目录使用当前兼容Python：

```sh
/Users/bjn00003/BDHub/01-BDSystem-V2/.venv/bin/python scripts/sync-cycle-catalog.py --source campaign --run var/cycle-catalog-NEW/campaign.json --max-requests 100
/Users/bjn00003/BDHub/01-BDSystem-V2/.venv/bin/python scripts/sync-cycle-catalog.py --source selected --run var/cycle-catalog-NEW/selected.json --max-requests 100
```

同run路径用于恢复原检查点；completed运行只幂等回填本地发布/投影，不重复网络。下一次刷新必须新run路径，避免覆盖证据。最多100次是本次有界调用配置，不是平台限额；source规则变化不能在原运行里偷偷替换。暂未安装周期任务。

先保存完整源后发布；publish完成但投影中断时可再次执行completed运行回填。整个同步过程不创建卡、不选入、不加入活动、不改佣、不发送，不写旧业务库；两次采集中身份文件前后校验均未变化。

## 验证

31项second_cycle、12项catalog、3项API检查通过，共46项。类型检查、生产构建和页面点验通过。新增用例覆盖根total_num字符串、短页不代表完成、空完整源、分页重复/总量变化、未来活动、拟分佣与实卡状态区分、当前offer投影保留历史/控制，以及旧统计窗口不抵扣新供应需求。

真实货盘的隔离容量回放：以500作为测试输入，只准备5个PID作业并验证可领取，未运行Kalodata请求；测试库已清理，结果保存在capacity-replay.json。没有把500写成平台剩余额度，也没有在真实准备库开启采集。

证据目录：`var/cycle-catalog-it-20260913/`，含campaign.json、selected.json、capacity-replay.json、validation.json。页面/API实读6213/2213及31个条件齐全关系；当前Web预览PID67389。

## 下一接点

接Kalodata只读执行适配与持久作业消费，使用当前合格PID和14天窗口；先有限PID小样并验证重启/重复输入，再考虑周期运行。卡片实际准备/重新绑定、短名称AI预处理、模板更新、IM回复、账号统一执行归属仍独立验收。原暂停未批准试点不恢复。
