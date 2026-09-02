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

### ACE 系法线对齐与诊断（2026-08-11，specs/007）

ACE 系（`ace_better_normal`/`ace_normal`）推理输入与训练分布对齐：
- **模型路由**：`resolve_ace_model()`——场景 3ch RGB-only 模型（`projections/ace_model_scene.pth`）
  存在时优先（输入与训练完全一致，无 skew）；否则回退默认 6ch 模型 + 常量 0.5 法线占位
  （≈训练真实 LAS 法线映射后分布均值，不再喂 Sobel 梯度伪法线）；
- **输入标注**：结果携带 `input_mode`（`ace_scene_rgb3ch` / `ace_6ch_constant_normal`）与
  `normal_source`（`none_rgb3ch`/`constant_fallback`/`gradient_debug`/`dsine`/`mi_das`）；
- **PnP 失败诊断**：`solve_pnp_with_focal_search` 返回 `attempts_summary`；ACE 系失败结果含
  `diagnostics`（PnP 统计、预测 3D Z 范围、LAS bbox 重叠率、模型信息），前端失败诊断行
  `localizeFailureDiagLine` 展示「内点 X | 重投影误差 Y px | 预测Z [a,b]」；
- **治本（specs/008，实施中）**：推理期图像法线估计（DSINE/MiDaS）经 `normal_mode` 接入 6ch 路径，
  ≥20 张四路径精度基准以数据决策默认路由。

### 验证方式

| 方案 | 验证方法 | 指标语义 |
|------|---------|---------|
| A/B/D | homography 匹配拟合 | 仅输出像素内点/残差；同源 NPY 不作米制验证 |
| C | LAS 点云近邻验证 | 地图邻近性，不代表独立真值误差 |
| E | 继承所选策略的验证 | 必须保留原指标类型和来源 |
| 全部 | PnP 综合评分 + 质量门控 | `score = inlier_count / (reproj_error + 1e-6)`；`quality_passed` 表示是否通过三维门控 |
| 前端人工选点 | 空间定位任务自产 H + 最终位姿 XYZ NPY | 本地展示 H→SLAM XYZ、NPY XYZ 及其米制差值；无需外部服务，只表示内部坐标一致性 |
| 全部算法结果展示 | 契约归一化（`normalize_localization_result`）统一补齐结果字段 | 无坐标差判据产物时呈现「⚠ 无法判定」独立状态（徽章与判定卡均不显示 ✓），内点数/相似度/LAS 验证率仅作辅助诊断 |
| 全部 | 独立 holdout 位姿（可选） | 平移误差（米）和旋转误差（度） |

### 空间感特征轻量验证（2026-08-31，specs/010）

- 全局召回继续使用查询/索引对称的 RGB 描述子；XYZ、法线、点图只进入候选后的软评分、匹配过滤或 PnP 精化；
- 查询端 `normal_camera` 与地图端 `normal_world` 比较前必须经 `R_cw` 变换，不允许跨坐标系直接点积；
- tile 真值评估强制 leave-one-out，查询 key 不得仍在检索索引；自匹配只作契约烟测；
- 地图法线由 XYZ 完整四邻域中心差分生成；中心或任一上下左右像素无效时不得产生法线，算法必须对刚体旋转等变；
- ACE 训练损失只由 1/8 XYZ 非零掩码决定；法向量是输入信息通道。8 位置候选不改变 16,396 个 XYZ 监督像素，但法向信息覆盖从 91.94% 降至 78.21%；
- 当前 MiDaS 与 MoGe-2 法线均未达 20° 入场门槛；8 位置修正地图参考下 MoGe-2 总体为 40.33°，默认路由保持 009 的 LoFTR-fast 决策。
- 正式 8+2 pose-only 基准关闭位姿先验并只消费 tile XYZ，不加载 5,252,140 点稠密 LAS/KD-Tree；二次 LightGlue 投影拟合、LAS 验证、坐标转换和视觉产物显式跳过。生产默认路径仍完整加载并生成诊断。

### 日志分离

- HTTP API 日志 → `logs/http_api.log`（FastAPI 中间件 + uvicorn）
- 业务日志 → `logs/backend.log`（localizer, matcher, preprocess 等）
