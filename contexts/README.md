# 限界上下文

本目录定义目标领域边界，并映射到当前存量实现。生产代码暂不整体搬迁；后续变更按测试保护下的绞杀式迁移逐步进入对应上下文。

| 上下文 | 文档 | 当前主要实现 |
| --- | --- | --- |
| 地图准备 | `map-preparation/` | `services/las_processor/`、`api/routes/preprocess.py` |
| 空间定位 | `spatial-localization/` | `services/localizer/`、`services/matcher/` |
| 任务与影像 | `task-imagery/` | `api/models.py`、`api/routes/images.py`、`api/routes/tasks.py`、`api/routes/localize.py` |

跨上下文数据结构应先在规格中定义稳定契约，再进入 `integration/published-language/`。
