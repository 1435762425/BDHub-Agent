# vendor/：旧 BDHub 协议层接管（I1）

本目录是**从旧仓库原样搬入**的 `bdhub` 包副本，目的是让 BDHub-Agent 最终不再依赖
`/Users/bjn00003/BDHub/01-BDSystem-V2` 也能运行。

## 来源与范围

| 项 | 值 |
| --- | --- |
| 来源仓库 | `/Users/bjn00003/BDHub/01-BDSystem-V2`（只读参考） |
| 来源版本 | `7cdc180ebd73a3040a788440a131c73f0c17ce1a`（2026-08-28） |
| 选取方式 | 以新项目实际 import 的 17 个根模块为起点，解析相对导入后的传递闭包 |
| 规模 | 162 个模块 / 约 40,263 行 |
| 复制范围 | **仅 `.py` 源码**。未复制 `config.yaml`、`secrets/`、身份文件、Cookie、任何凭据 |

导入验证：17/18 个根模块可在只挂载 `vendor/` 时正常导入。

| 根模块 | 状态 |
| --- | --- |
| `bdhub.send.taplink.protocol` / `.transport` | ✅ |
| `bdhub.research.catalog_rules` / `commerce_transport` / `kalodata_worker` | ✅ |
| `bdhub.enrich.identity_store` / `creator_profile` / `profile_lease` / `pure_http_worker` / `shared_backoff` | ✅ |
| `bdhub.hub.markets` / `engine` / `imbase.account_binding` | ✅ |
| `bdhub.send.sharelink.transport` / `http_write_gate` / `account_policy` | ✅ |
| `bdhub.send.worker` | ❌ 依赖旧仓库的 `scripts/` 目录（非包），且绑定旧 PostgreSQL。新项目生产路径不使用它。 |

## 当前状态：已搬入，尚未接线

`scripts/` 目前仍通过 `LEGACY` 常量从旧仓库导入 `bdhub`。本目录是**纯新增**，
把 `vendor/` 放到 `sys.path` 优先位置后即可切换；切换前行为完全不变。

## 可复现

```sh
python scripts/vendor-legacy-bdhub.py --check   # 报告与闭包的差异，不写文件
python scripts/vendor-legacy-bdhub.py --write   # 重新生成 vendor/bdhub
```

当前 `--check`：162 模块、0 缺失、0 多余。

## 已验证

| 验证 | 结果 |
| --- | --- |
| 只挂载 `vendor/` 时导入 17 个根模块 | 17/18 通过 |
| `bdhub.send.taplink.protocol.create_payload` 差分（旧副本 vs vendor） | 输出哈希一致 `aa18f1de…` |

## 已知接缝（I2 / I3 要解决）

1. **配置**：`bdhub.config` 在包旁查找 `config.yaml`（即 `vendor/config.yaml`），
   该文件需要 `auth.headers_json`（指向 `secrets/headers.json`，即 Cookie）、
   `db.*`（PostgreSQL 连接）、`enrich.*`、`reply.api_key` 等。
   新项目必须提供**自己的**配置与凭据，不能复制旧仓库的。
   `config.example.yaml` 是模板，可用于对齐字段。
2. **数据库**：部分模块经 `bdhub.hub.engine/repo/*` 绑定旧 PostgreSQL。
   新项目已有对应 SQLite 表，生产路径不使用这些模块，适配时改指向新存储。
3. **`bdhub.send.worker`**：依赖旧仓库 `scripts/` 目录（非包），生产路径不使用。

## 约束

- **不要就地修改本目录的文件。** 旧协议逻辑保持原样，便于与旧仓库逐行对账。
- 需要适配的部分（市场能力层、凭据、维护周期）在新项目侧做适配器，见 I2 / I3。
- 升级方式：重新按同一闭包算法从旧仓库复制，并更新上面的来源版本。
- 复制时务必排除 `config.yaml`、`secrets/`、身份与 Cookie 文件。
