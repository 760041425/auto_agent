# Wiki Skills 安装与使用核验

核验日期：2026-07-30

## 安装状态

| Skill | Claude 原版 | Codex 原生源 | Codex 全局安装 | 前向测试 |
| --- | --- | --- | --- |
| `wiki-bug-fix` | `.claude/skills` 保留 | `codex-skills/wiki-bug-fix` | 已更新 | 通过：不虚构根因，保持只读边界 |
| `wiki-issue-acceptance` | `.claude/skills` 保留 | `codex-skills/wiki-issue-acceptance` | 已更新 | 通过：正确区分 pass/not run/blocked |
| `wiki-issue-design` | `.claude/skills` 保留 | `codex-skills/wiki-issue-design` | 已更新 | 通过并收紧完整 AC-ID 规则 |
| `wiki-issue-dev` | `.claude/skills` 保留 | `codex-skills/wiki-issue-dev` | 已更新 | 通过：保护脏工作区，外部操作分权 |
| `wiki-session-report` | `.claude/skills` 保留 | `codex-skills/wiki-session-report` | 已更新 | 初测后收紧固定标题并复测 |

Codex 原生版本会在安装后的下一轮重新发现。原始 Claude 版本和外部来源均未覆盖。

## 历史使用证据

- 项目副本最晚在 Git 提交 `e3d981b`（2026-07-19）中已存在；
- 仓库内没有找到这些流程通常产生的 `acceptance.md`、`bugfix-*.md`、`01-backlog.md`、`02-analysis.md` 或 `PRD.md`；
- 除 skill 定义自身外，没有找到可证明 `/wiki-*` 命令实际执行过的项目产物。

因此可以确认“已放入仓库”，但不能据此确认“历史上完整跑过”。

## Codex 原生改造

- 5 个 `SKILL.md` 合计从 5461 行压缩为少于 500 行，详细模板放入各自 `references/`；
- 增加 `agents/openai.yaml`，支持 Codex 技能列表和默认提示；
- 删除 `AskUserQuestion`、`TaskOutput`、`ScheduleWakeup`、Claude MCP 名称、`devlock`、私有 memory 与缺失 `_shared` 依赖；
- K8s、仓库、URL、分支和部署名称改为从项目文档读取；
- 验收默认只读；修复、Git、GitHub、部署和 Issue 关闭分别按用户授权执行；
- 所有包已通过 `skill-creator` 的 `quick_validate.py`、相对引用和旧依赖扫描。

结论：5 个包已具备 Codex 自包含结构。真实 GitHub/K8s 闭环仍取决于目标项目是否提供连接、凭据、环境档案和用户授权。
