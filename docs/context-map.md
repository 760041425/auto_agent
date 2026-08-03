# 上下文映射

```text
地图准备上下文
  LAS / 轨迹 / COLMAP
        │ 发布 Map Tile + Pose + XYZ/Normal 契约
        ▼
空间定位上下文 ──────► 任务与影像上下文
  计算匹配/位姿          管理 Image / Task / Report
        │                       │
        └──────────┬────────────┘
                   ▼
                Web/API
```

## 上下文关系

| 上游 | 下游 | 关系与契约 |
| --- | --- | --- |
| 地图准备 | 空间定位 | 地图准备发布四向斜地面 `tile_index.json`（yaw 0/90/180/270、pitch -15、roll 0）、投影图、XYZ/法线图、实际渲染位姿、相机参数、特征索引和 ACE 权重；定位只消费当前 accepted MapTile，不修改其语义 |
| 任务与影像 | 空间定位 | 应用层传入影像路径和算法参数；定位返回稳定结果对象，不直接管理数据库事务 |
| 空间定位 | 任务与影像 | 任务上下文持久化成功、失败、位姿、匹配点、置信度与错误信息 |
| Web/API | 三个上下文 | API 负责协议转换和用例触发，不承载点云或定位规则 |

## 防腐层

外部 PDAL、octree renderer、COLMAP、OpenCV、DINOv2、LightGlue、RoMa 和 ACE 的原始数据结构必须在适配器边界内转换。领域和应用层不应依赖外部库特有的异常、张量形状或临时文件命名。

当前代码尚按技术目录组织；[contexts](../contexts/README.md) 记录现状映射，后续只在真实变更涉及的范围内渐进迁移。

## 空间定位上下文 — 多方案架构（2026-08-01）

### 定位算法路径

```
query_image
    │
    ├── 路径 A: DISK+LightGlue → 稀疏匹配 → PnP
    ├── 路径 B: LoFTR → 密集匹配 → PnP
    ├── 路径 C: ACE RGB → 端到端 3D 回归 → PnP + LAS 验证
    ├── 路径 D: Hybrid (DISK+LG + LoFTR) → 联合匹配 → PnP
    └── 路径 E: Multi-Strategy → 多策略融合选最优
```

### PnP 焦距搜索与质量门控（2026-08-04）

所有走 PnP 的路径（A/B/D/E + 原版 SALAD+RoMa）统一使用：
- **多阶段归一化焦距搜索**：`pose_utils.solve_pnp_with_focal_search`，粗 3 轮 + 精 2 轮，
  在初始估计 ±30% 范围内搜索最优内参；
- **质量门控**：`pose_utils.annotate_pnp_quality`，三维门控（`score ≥ 4.0`、
  `inliers ≥ 6`、`reproj_error ≤ 8px`），输出 `quality_passed` 与 `quality_reasons`。

### 验证方式

| 方案 | 验证方法 | 指标语义 |
|------|---------|---------|
| A/B/D | homography 匹配拟合 | 仅输出像素内点/残差；同源 NPY 不作米制验证 |
| C | LAS 点云近邻验证 | 地图邻近性，不代表独立真值误差 |
| E | 继承所选策略的验证 | 必须保留原指标类型和来源 |
| 全部 | PnP 综合评分 + 质量门控 | `score = inlier_count / (reproj_error + 1e-6)`；`quality_passed` 表示是否通过三维门控 |
| 前端人工选点 | 空间定位任务自产 H + 最终位姿 XYZ NPY | 本地展示 H→SLAM XYZ、NPY XYZ 及其米制差值；无需外部服务，只表示内部坐标一致性 |
| 全部 | 独立 holdout 位姿（可选） | 平移误差（米）和旋转误差（度） |

### 日志分离

- HTTP API 日志 → `logs/http_api.log`（FastAPI 中间件 + uvicorn）
- 业务日志 → `logs/backend.log`（localizer, matcher, preprocess 等）
