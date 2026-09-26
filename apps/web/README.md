# BDHub-Agent Web

Next.js 16 / React 19 本机工作台，基于 TailAdmin Next.js Free。当前页面连接 `var/` 中的真实本地台账与 Python CLI；不同模块的外部能力边界不同，不能再把整个应用概括为“纯演示”或“全部生产可写”。

`/api/send` 提供当前市场的持续发送控制、明确 start/stop 和原意图 unknown 核验；执行器逐达人保存不可变 delivery，旧冻结批次入口已退役。实际开关与 worker 状态以本机台账及当前交接为准；GET、普通参数保存、构建与重启不自行启动发送，真实执行仍通过页面动作与持久授权门禁。

## 运行

需要 Node.js `>=22.18.0`，依赖固定在 `package-lock.json`。在开发 worktree 中：

```bash
cd <worktree>/apps/web
npm ci
npm test
```

服务只监听 `127.0.0.1:5198`，API 也只接受 Host `127.0.0.1:5198`/`localhost:5198`，所以换端口起开发服务时全部 API 返回 403。

生产实例由 `io.bdhub.agent.web`（`launchctl submit`，KeepAlive）在本目录执行 `npm run start`，占用 5198 并读取这里的 `.next`。因此不要在本目录执行 `npm run dev` 或 `npm run build`；代码改动在 `/Users/bjn00003/BDHub/` 下的独立 git worktree 里测试与构建。启动、停止或替换生产实例前，先核对监听 PID 与 cwd。

## 验证

```bash
npm test
npm run build
npm run typecheck   # tsc 会读取 .next/types，删改路由后先 build
```

这些测试验证页面/API 合同、本地状态机和故障恢复，不替代真实 TikTok/Kalodata 回执或业务验收。`campaign.test.mjs` 使用静态回执测试纯解码器；`creator-profile-refresh.test.mjs` 按项目文件标记验证路径，不依赖生产库或固定目录名。在 worktree 中运行不应忽略这两项失败。

## 主要页面

| 路由 | 当前用途 |
| --- | --- |
| `/{market}` | 运营首页：主链、异常、自动运营/全托商品发现/持续发送开关 |
| `/{market}/workspace/send`、`/{market}/workspace/history` | A/B 发送池、模板审核、持续发送与原意图核验；最近统计与日明细（旧 `/workspace/inbox` 重定向到会话） |
| `/{market}/conversations`、`…/conversations/templates`、`…/conversations/agent` | 三栏会话、人工/发送模板、Agent 指南与零写入试聊 |
| `/{market}/catalog` | 全托/Campaign、筛分、标准 TapLink、A/B 线索与链接命名 |
| `/{market}/creators` | 稳定达人身份、别名、画像与线索结果 |
| `/{market}/ops/jobs`、`/{market}/ops/kalodata`、`/{market}/ops/accounts` | 作业时间、Kalodata 身份、账号分工与维护（旧 `/ops/*` 只重定向到 `/it/ops/*`） |

IT、BR、MY、UK 复用市场页面；自动 A/B 读取和发送支持四市场，手动发送、身份/画像操作与 TapLink 清理仍有 IT 限制。通讯会话由 SDK owner 管理，页面二发和 AI 继续走原 HTTP 执行器；当前回复执行使用多轮 V2，旧五动作与固定模板评测不作为 V2 执行真值。页面清单和业务规则以项目主文档为准。

浏览器演示、`/flow-demo`、本地模拟器、旧二发预演/实测和 matching/outreach-drafts 页面已退出生产构建；2026-09-24 又删除了页面不再调用的 reply-review、global-source、catalog-screen、cycle-service API。对应 SQLite 仍作为历史数据保留并继续备份。产品规则见 [项目文档](../../docs/PROJECT.md)，实现结构见 [技术文档](../../docs/TECHNICAL.md)，动态运行状态见 [当前交接](../../docs/handoff/current.md)。

## 代码边界

- `src/app/api/`：仅本机 API 路由；所有写请求必须限制字段并走服务端 bridge。
- `src/server/`：CLI 适配、输入输出校验和错误映射；不在 React 组件中直接操作运行台账。已知例外：`server/creator-identities/store.ts` 由 Node 只读直连 SQLite，待迁回 CLI bridge。
- `src/features/`：页面、查询 hooks、交互和状态展示；业务资格与幂等不由前端保证。
- `tests/`：Node 合同测试。数量恒等式、未知结果、暂停和输入拒绝需要同时覆盖正向与反向案例。

本机敏感配置在仓库根被忽略的 `config/kalodata-identity.json`、`config/campaign-join.json`；历史 `config/typesafe.json` 已退出运行依赖，仍属私密文件。账号身份和运行数据在 `var/`；不得复制到前端 bundle、日志或 Git。
