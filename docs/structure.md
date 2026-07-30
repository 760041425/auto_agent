# 仓库结构

| 路径 | 职责 |
| --- | --- |
| `api/` | FastAPI 接口、应用编排、SQLAlchemy 持久化 |
| `services/las_processor/` | LAS 下采样、投影、八叉树、特征准备 |
| `services/localizer/` | 检索、匹配、PnP、重投影、ACE 训练与推理 |
| `services/matcher/` | 图像区域到地图三维坐标的比较引擎 |
| `web/` | 静态 Web 管理与可视化界面 |
| `docs/` | 长期产品、领域、技术和工程规则 |
| `contexts/` | 限界上下文定义及存量代码映射 |
| `specs/` | 特性级 SDD 规格包 |
| `tests/` | 跨上下文契约、验收、系统测试和共享夹具 |
| `scripts/` | 本地运行、质量门禁和规格追踪 |
| `reports/` | 可再生成的追踪与验证输出 |
| `las/`、`query_images/`、`projections/`、`logs/` | 输入或运行时数据，不属于生产源码 |

## 依赖方向

当前存量代码仍存在路由直接导入具体算法的情况。目标方向是：

```text
interfaces (FastAPI/Web)
        ↓
application (use case / task orchestration)
        ↓
domain (rules, result, lifecycle)
        ↑
infrastructure adapters (SQLAlchemy/filesystem/ML engines)
```

新代码不得增加反向依赖。大规模目录迁移需独立规格、回归测试和回滚方案。
