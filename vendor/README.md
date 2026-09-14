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

## 约束

- **不要就地修改本目录的文件。** 旧协议逻辑保持原样，便于与旧仓库逐行对账。
- 需要适配的部分（市场能力层、凭据、维护周期）在新项目侧做适配器，见 I2 / I3。
- 升级方式：重新按同一闭包算法从旧仓库复制，并更新上面的来源版本。
- 复制时务必排除 `config.yaml`、`secrets/`、身份与 Cookie 文件。
