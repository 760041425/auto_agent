# 仓库结构

| 路径 | 职责 |
| --- | --- |
| `api/` | FastAPI 接口、应用编排、SQLAlchemy 持久化 |
| `services/las_processor/` | LAS 下采样、投影、八叉树、特征准备（详见下方模块清单）|
| `services/localizer/` | 检索、匹配、PnP、重投影、ACE 训练与推理、日志配置 |
| `services/matcher/` | 图像区域到地图三维坐标的比较引擎 |

### services/las_processor/ 模块清单

| 文件 | 职责 |
|------|------|
| `projection_octree.py` | 八叉树构建、欧拉角视图计划、MapTile 渲染调度与 `tile_index.json` 发布 |
| `projection.py` | 单视图投影渲染调用、XYZ/法线图生成 |
| `colmap_reader.py` | 读取 COLMAP 稀疏重建结果（位姿、轨迹、三维点）|
| `features.py` | 地图投影特征提取与描述子索引构建 |
| `tile_index_migration.py` | 将旧版 32 向索引迁移为四向 p-15 临时发布集合 |

### services/localizer/ 模块清单

| 文件 | 职责 |
|------|------|
| `__init__.py` | 旧版 SIFT/COLMAP 定位器（legacy）|
| `salad_roma.py` | SALAD+RoMa 原版引擎（保留对照）|
| `salad_roma_v2.py` | v2 引擎（5 种匹配器：DISK+LG, LoFTR, Hybrid, ACE, Multi-Strategy）|
| `pose_utils.py` | 共享几何工具（稳定四元数、PnP+refine、E-matrix、LAS 验证、多阶段归一化焦距搜索、质量门控）|
| `coord_regression.py` | ACE 网络定义 + 自动加载器 |
| `registry.py` | 稳定算法 ID 到 runner 的应用层注册表 |
| `contracts.py` | 统一定位结果及旧字段兼容适配 |
| `evaluation.py` | 独立位姿真值的平移/旋转误差计算 |
| `verify_projection.py` | homography 2D 拟合诊断（像素，非 Benchmark）|
| `logger_config.py` | 日志配置（HTTP API / 业务分离）|
| `ace/` | 官方 Niantic ACE 网络（预训练权重）|

### scripts/ 工具清单

| 文件 | 职责 |
|------|------|
| `benchmark_localizers.py` | 复用算法注册表、输出 manifest/run_id 的评估框架 |
| `generate_verify_report.py` | 单图内部一致性 HTML 报告（嵌入图像 + XYZ 对比）|
| `render_ground_tiles.py` | 生产 MapTile：四向、pitch=-15、roll=0 的斜向地面 Euler 投影 |
| `render_multi_pitch_tiles.py` | 多 pitch 实验渲染，只能写隔离目录 |
| `render_horizontal_tiles.py` | 水平八向实验渲染，只能写隔离目录 |
| `verify_localization.py` | 离线验证脚本（homography + NPY 对比）|
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
