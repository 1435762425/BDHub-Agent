# 二发闭环 B.3：Kalodata持久Worker与认证阻断

2026-09-13。Worker代码及离线验收已完成。先前HTTP401已在用户完成插件登录后解除，原2个PID作业已完成真实读取，新增23条线索；不等于全天自循环或收件OEC解析已完成。

## 实现

- `scripts/second-cycle-worker.py`只调用Kalodata `/product/detail/creator/queryList`，复用原登录器会话和代理读取方式，不复制Cookie、不刷新登录、不回退浏览器。复用旧Kalodata锁文件的只读文件描述符取得advisory lock，不创建或写旧文件。
- 机构IT本地计划从当前合格货盘选有限PID，优先既有线索商品。`--prepare-target`是本地线索库存目标，不是已验证平台剩余联系额度。
- 沿旧版统计截止日滞后两天，固定14天窗口；本轮2026-08-29至2026-09-11。该日期口径记录在任务，重启不移动窗口。
- 单页50条，每PID默认最多2页。达到页数上限标page_cap，不称全部达人。正销量、合法handle才形成线索；缺失、零销量和非法名称逐项计数。
- 返回页先保存到cycle_source_receipt，再以原作业、游标、fence提交源边。保存后中断可复用原页，避免重复网络。分页重复、缺结构、假成功、身份/范围变化均阻断，不当空数据。
- Kalodata ID与handle只作为来源身份，creatorId/OEC保持空，进入待TikTok身份解析；不从历史同名映射直接合并。保留来源排名、销量和原币种营收显示值；金额字符串不当已规范化精确币值。
- 尚待身份解析的本期唯一来源达人也占用准备库存，避免身份解析未跟上时采集无限扩张；它们不计为可发送达人。
- source_issue与source_job保存阻断。CLI发现已有blocked默认不再请求；`--retry-blocked`为显式只读恢复入口，保留窗口，先检查计划仍active。当前不安装常驻/定时服务。

## 插件登录前的请求情况（历史）

准备目标20、最多2个PID、最多3个步骤。首PID第一次请求失败；补充诊断和对齐旧HttpClient referer后，后两次均明确HTTP401、无成功JSON结构。共3次有界请求，登录Cookie文件哈希均未变化。当前1个作业blocked、1个queued；后续PID未请求，没有新增源边，原50条历史来源保持。

401说明当前HTTP登录认证未通过，不能直接断言具体过期原因；需要原Kalodata登录器提供有效会话后再继续。本轮没有自动重登、换账号、清文件或修改TikTok任务。诊断存`var/cycle-kalodata-it-20260913/`，不含Cookie或原始响应正文。

## 运行方式

从新项目根目录，用现有兼容Python：

```sh
/Users/bjn00003/BDHub/01-BDSystem-V2/.venv/bin/python scripts/second-cycle-worker.py --prepare-target 20 --max-pids 2 --max-steps 3 --report var/cycle-kalodata-NEW/first.json
```

已有阻断时先解决原因，再使用`--retry-blocked --max-steps 1`及新的report路径恢复原任务。不要再次带prepare-target来绕过未处理阻断；CLI会先检查blocked。此工具只有读取路径，没有建链/发信实现。

## 验证

新增13项Worker测试，连同31项底座、12项货盘及3项API，共59项通过；类型检查/生产构建通过。测试包括：回执保存后中断、失效租约、暂停期间数据不提交、同页去重、重复页、页数范围不可扩大、未解析身份不伪造OEC，以及待解析库存阻止过量读取。

工作台显示任务排队/处理中/完成/阻断与冻结窗口，并明确提示Kalodata认证失败。原试点控制/真实发送仍未接通，网页读取不会重启任务。新Web PID69978，未改原画像和模拟Worker。

## 下一步

插件登录后的真实页读取已完成；下一步把23条新源边接入新系统身份发现队列。身份解析的持久跨库交接、正式周期调度以及新旧执行控制切换仍待实现；本轮没有把这些列为已完成。


## 插件登录后的续测（2026-09-13）

用户明确已通过插件完成验证登录。已核对原登录器会按其现有流程导出会话到latest_cookie.txt；新Worker读取更新后的同一来源，不复制或改写会话文件。首次恢复原作业第一页成功，12条有效线索；随后同一PID第二页结束、另一个原排队PID返回11条，合计23条/23个不同Kalodata身份及handle。共3次HTTP200/success=true，2个原作业completed、blocked=0。

保持原窗口2026-08-29—2026-09-11，未重新建立PID作业或扩大范围。待解析人数23达到本地准备目标20后停止读取；该目标不是平台剩余联系额度。Kalodata身份不冒充OEC，现有稳定关系仍31，来源边从50变为73，新增23条尚待身份发现/解析。

证据：after-plugin-login.json、after-plugin-login-continue.json和plugin-login-validation.json，均在var/cycle-kalodata-it-20260913。登录会话在本轮读取前后内容哈希一致，建链/发送/模型调用均0。旧401诊断保留为历史，不再描述为当前阻断。
