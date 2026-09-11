# 自定义 Skills 清理结果（2026-09-11）

按本轮“先清理”的授权执行登记 S01、S02；仅修改全局自定义 `remotion-best-practices`，未卸载技能。没有修改 `.system`、插件、全局 AGENTS 或旧 BDHub 业务文件。

| 登记 | 执行结果 | 修改依据 |
| --- | --- | --- |
| S01 | `remotion-markup/REFERENCE.md:24` 改为仅在交付要求包含 Studio 可编辑控件时遵循交互指导；普通成片不要求增加控件。 | 与 `remotion-create/REFERENCE.md:36` 的既有交付条件一致；保留动画、渲染和可编辑时间线知识。 |
| S02 | 顶层 `remotion-maps/` 为唯一维护入口；Markup 中两处引用改为 `../remotion-maps/REFERENCE.md`，重复嵌套目录完整移出 Skills。 | 两目录各 32 个文件，31 个哈希一致；唯一差异是 `techniques/static-map/TECHNIQUE.md` 为不同目录层级调整的相对链接，指向相同资料，无独有业务内容。 |

归档而非永久删除 32 个嵌套文件（488,029 字节）；目标技能下未发现 `__pycache__`，无需清除缓存。

## 恢复与证据

归档根目录：`/Users/bjn00003/BDHub/_archive/context-cleanup-20260911/skills/`。

- `remotion-before-edits/remotion-markup/REFERENCE.md`：修改前原文件，可恢复到 `/Users/bjn00003/.codex/skills/remotion-best-practices/remotion-markup/REFERENCE.md`。
- `remotion-nested-maps/`：完整嵌套目录，可移回 `/Users/bjn00003/.codex/skills/remotion-best-practices/remotion-markup/remotion-maps/`。
- `remotion-cleanup-manifest.json`：33 个归档文件的原路径、归档路径和修改前 SHA-256；归档完成后逐项回读哈希相符。恢复前须确认目标没有后续编辑，避免覆盖新内容。

## 验证

- 技能内 Markdown 相对文件引用：清理前 104 条、清理后 95 条，均无缺失目标；范围是该技能目录内的 `.md` 文件链接，不包含外部 URL 的可达性检查。
- 顶层 Maps 目录保留，嵌套目录已移出，3 个入口引用均指向顶层。
- `quick_validate.py` 返回 `Skill is valid!`。系统 `python3` 缺少 PyYAML，改用现有 BDHub `.venv/bin/python` 只读执行，并设置 `PYTHONDONTWRITEBYTECODE=1`，未安装依赖或改动环境。
- 未增添 Skill 规则包、脚本或新的自动加载入口；仅完成登记中的窄范围清理。
