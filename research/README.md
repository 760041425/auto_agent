# 研究项目索引

> 本研究目录收录项目全周期的算法 / 论文 / 工程实现研究结论，作为**项目资产**持久保存。
> 每个研究结论独立一份 Markdown，命名 `<YYYY-MM-DD>-<主题 slug>.md`。
> 所有研究均通过 `skills/research` 或 `skills/cv-research` 技能完成，严格区分「论文能做 / 开源代码能跑通 / 实际工程可靠」。

## 收录规范

| 字段 | 要求 |
| --- | --- |
| 文件名 | `<YYYY-MM-DD>-<topic-slug>.md`（日期为研究完成日） |
| 模板 | 见 `research/TEMPLATE.md` |
| 内容 | 研究问题、方法记录、三重区分、对比矩阵、推荐方案、参考文献 |
| 附件 | 代码片段 / 图表放 `research/assets/<同名>/`，大文件（权重/数据）记链接不入库 |
| 入库 | 文本 Markdown 入库；`.cache/`、`projections/`、`reports/` 等运行产物不入库 |

## 索引

| 日期 | 研究主题 | 文件 | 状态 | 关键结论 |
| --- | --- | --- | --- | --- |
| — | — | — | — | —（首次研究完成后填入） |

## 使用方式

- 发起新研究：对 agent 说「研究一下 XXX」，agent 按 `skills/cv-research` 或 `skills/research` 流程执行
- 研究收尾：agent 自动按模板保存到 `research/<日期>-<slug>.md` 并更新本索引
- 复用旧研究：先读本索引定位相关文件，避免重复调研
