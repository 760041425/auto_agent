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
| 2026-05-12 | 特征匹配成功率分析 + 加速方案 | `2026-05-12-feature-matching-success-analysis-and-acceleration.md` | ✅ 完成 | 根因：稀疏关键点在跨域场景枯竭（DISK+LG 仅 10-15 match vs LoFTR 81-187）；加速首选 torch.compile+FP16+FAISS（1.88s→0.6-1.0s），备选 XFeat 替换 DISK+LG |
| 2026-08-31 | 空间感特征提取 + 轻量验证 | `2026-08-31-spatial-features-lightweight-validation.md` | ✅ P0/P1/P1b/P1c/P1d/P3/P4/P4b/P4c 完成 | MiDaS/MoGe 法线未过 20° 门；确定性 8+2 下 LoFTR 保持准确率优势，pose-only cold 2.301s、LOO warm P50 0.675s；默认生产路径不变 |

## 使用方式

- 发起新研究：对 agent 说「研究一下 XXX」，agent 按 `skills/cv-research` 或 `skills/research` 流程执行
- 研究收尾：agent 自动按模板保存到 `research/<日期>-<slug>.md` 并更新本索引
- 复用旧研究：先读本索引定位相关文件，避免重复调研
