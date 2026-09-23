# BDHub-Agent Web

Next.js 16 / React 19 本机工作台，基于 TailAdmin Next.js Free。当前页面连接 `var/` 中的真实本地台账与 Python CLI；不同模块的外部能力边界不同，不能再把整个应用概括为“纯演示”或“全部生产可写”。

`/api/send` 提供当前市场的持续发送控制、明确 start/stop 和原意图 unknown 核验；执行器逐达人保存不可变 delivery，旧冻结批次入口已退役。实际开关与 worker 状态以本机台账及当前交接为准；GET、普通参数保存、构建与重启不自行启动发送，真实执行仍通过页面动作与持久授权门禁。

## 运行

需要 Node.js `>=22.18.0`，依赖固定在 `package-lock.json`：

```bash
cd /Users/bjn00003/BDHub/BDHub-Agent/apps/web
npm ci
npm run dev
```

开发服务只监听 `127.0.0.1:5198`。生产模式本机预览：

```bash
npm run build
npm run start
```

不要同时启动两个 5198 实例。启动、停止或替换现有实例前，先核对监听 PID 与 cwd。

## 验证

```bash
npm test
npm run typecheck
npm run build
```

这些测试验证页面/API 合同、本地状态机和故障恢复，不替代真实 TikTok/Kalodata 回执或业务验收。

## 主要页面

| 路由 | 当前用途 |
| --- | --- |
| `/it/workspace/send` | A/B 发送池、持续发送、明确启动、停止和原意图 unknown 核验 |
| `/it/workspace/inbox` | 收信监控与人工事项 |
| `/it/workspace/history` | 最近 14 日统计和只读日明细 |
| `/it/catalog` | 全托/Campaign、筛分、标准 TapLink、A/B 线索和 OECID |
| `/it/creators` | 稳定达人身份、别名与画像 |
| `/ops/jobs` | 手动作业、材料周期、Kalodata 身份与命名设置 |
| `/ops/accounts?market=it` | 当前市场账号分工、身份维护与能力证据 |
| `/ops/reply-evaluation` | 历史 V1 DeepSeek/Jev 分类与人工真值追溯；不发送 |

IT、BR、MY、UK 复用市场页面；当前回复执行使用多轮 V2，旧五动作与固定模板评测不作为 V2 执行真值。页面清单和业务规则以项目主文档为准。

浏览器演示、`/flow-demo`、本地模拟器、旧二发预演/实测和 matching/outreach-drafts 页面已退出生产构建；对应 SQLite 仍作为历史数据保留并继续备份。产品规则见 [项目文档](../../docs/PROJECT.md)，实现结构见 [技术文档](../../docs/TECHNICAL.md)，动态运行状态见 [Codex 接管状态](../../docs/handoff/codex-takeover-20260919.md)。

## 代码边界

- `src/app/api/`：仅本机 API 路由；所有写请求必须限制字段并走服务端 bridge。
- `src/server/`：CLI/SQLite 适配、输入输出校验和错误映射；不在 React 组件中直接操作运行台账。
- `src/features/`：页面、查询 hooks、交互和状态展示；业务资格与幂等不由前端保证。
- `tests/`：Node 合同测试。数量恒等式、未知结果、暂停和输入拒绝需要同时覆盖正向与反向案例。

本机敏感身份与运行数据在仓库根 `config/*.json`（被忽略的本机文件）及 `var/`；不得复制到前端 bundle、日志或 Git。
