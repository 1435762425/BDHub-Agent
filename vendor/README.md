# BDHub 协议层

生产入口通过 `scripts/lib/legacy_runtime.py` 强制从本目录加载 `bdhub`，不再把旧仓库作为 Python import root。旧仓库仍是部分只读账号配置、保存凭据和签名 runtime 的来源；项目身份代次发布后，账号 profile/headers 由新项目台账覆盖。完整实现见[技术文档](../docs/TECHNICAL.md)。

此副本包含已审核的本地修改，不能直接覆盖回旧源码。MY 市场、BR/UK Campaign 能力和 UK 类目参数等差异已经保存在版本化补丁中。

## 来源与文件清单

- 初始协议导入来自旧仓库提交 `7cdc180ebd73a3040a788440a131c73f0c17ce1a`。
- 当前重建基线、169 个源码/资源文件的上游及目标 SHA-256 见 [manifest.json](../scripts/vendor-runtime/manifest.json)；目录检查不再只比较文件名。
- 四个文件的本地差异见 [local.patch](../scripts/vendor-runtime/local.patch)。为避免隐式导入旧发送器，历史生成的空 `__init__.py` 在清单中单独声明。
- `bdhub/enrich/pure_http_runtime_manifest.json` 是必须随包保存的协议资源；实际签名 runtime、身份、Cookie、数据库和 `config.yaml` 不属于复制范围。工具只允许 `.py` 和这一项明确资源，不能扩大为复制所有 JSON。
- `config.example.yaml` 是无凭据模板，不是当前真实配置。

## 检查与重建

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/vendor-legacy-bdhub.py --check
```

`--check` 不写文件。它验证上游源码、补丁哈希、当前 vendor 的完整文件集、逐文件哈希、Python 语法和必需资源格式。缺失、多余、内容变化或未审核上游修改都会返回非零。运行时 `__pycache__` 不纳入源码清单。

显式重建同一已审核快照使用 `--write`。工具先在目标文件系统的临时目录构建、应用补丁并验证，再原子替换整个包；失败保留原包并清理临时目录。macOS 使用 `RENAME_SWAP`，Linux 使用 `RENAME_EXCHANGE`；不支持原子交换时拒绝替换。源目录与目标重叠、符号链接或已有 vendor 出现未审核修改时也拒绝写入。

更新上游时，先审核源码与资源差异，再同步清单及本地补丁。若要从旧的已审核版本升级，在清单 `previousSnapshot` 中登记旧版本完整文件集与哈希；目标只允许匹配新快照或这一明确旧快照，不能用更新操作覆盖未知本地修改。正常 `--check` 仍只接受新的目标快照。

工具测试只使用临时目录和合成源码，覆盖缺文件、资源遗漏、补丁丢失、上游变化、升级、语法失败、原子交换失败和路径边界；不会重建当前运行包或访问平台。

## 保留的历史依赖

唯一引用 `bdhub.send.worker` 的旧 MX 调研探针已于 2026-09-24 删除，旧发送器（`send/worker`、`runner`、`component_sender` 等）目前没有仓库内入口，但仍在 manifest 中。精简前需核对动态导入并同步 manifest 与本地补丁；`bdhub.send.http_protocol`、`http_write_gate` 仍被当前 IM 会话使用，`bdhub.config` 仍为 IT 写入前的旧系统 PostgreSQL 只读检查提供连接串，不能随旧发送器删除。

当前外发与恢复语义在新项目 `scripts/lib` 中实现；协议更新不恢复任何旧任务或改变发送、Agent 开关。
