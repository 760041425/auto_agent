# DDD + SDD + TDD 工程实践

## 一次变更的标准路径

1. **DDD 定边界**：确认变更属于哪个上下文；必要时更新统一语言和上下文映射。
2. **SDD 写规格**：补齐 `spec → clarify → plan → tasks → testlist → checklist`，并记录风险和决策。
3. **TDD 做实现**：从测试清单选一个场景，依次 Red → Green → Refactor。
4. **验证防漂移**：运行规格校验、快速测试、追踪报告和漂移检查。
5. **交付**：清单中仍未完成的内容必须明确保留，不能以“代码已写”代替验收证据。

## Definition of Ready

- 目标、非目标和上下文归属明确；
- 验收标准具有稳定 ID 且可观察；
- 开放问题已回答或明确标记为阻塞；
- 风险、外部依赖和测试数据已识别；
- 测试清单至少覆盖主路径和关键失败路径。

## Definition of Done

- 规格、计划和决策与最终实现一致；
- `tasks.md` 和 `checklist.md` 状态真实；
- 新行为有自动化验证，相关回归测试通过；
- 领域词汇与公开契约已同步；
- 无新增未解释的运行产物进入 Git；
- 性能声明有可复现测量，失败和回滚路径有说明。

## 存量代码演进

本仓库当前按 `api/` 与 `services/` 技术目录组织。不要为了目录外观一次性搬家。每个真实需求只整理其触达范围：先建立测试和稳定契约，再抽出应用/领域概念，最后调整物理目录。`contexts/*/README.md` 是迁移地图，不是空壳架构完成证明。

---

## 多方案定位验证（2026-08-03，更新）

### 问题背景

视觉定位管线（SALAD 检索 → tile 匹配 → PnP）在真实拍摄图像上失败：
- DISK+LightGlue 仅 5-23 匹配（置信度 0.005），PnP 无法求解
- 历史根因之一：实验性多 pitch/水平八向资产曾覆盖生产索引，混入朝上、横转和非四向地面视图，扩大了合成 tile 与真实照片的视角域差距；现行生产契约固定为四向 pitch=-15° 斜地面投影。

### 历史实验观察（非验收结论）

修复前曾在 2 张查询图上运行五种方案。记录中的“米级误差”来自同一地图
派生的 tile/NPY 内部一致性或点到射线残差，不是独立位姿真值；设备、预热和
重复次数也未固定。因此这些数值仅用于定位代码问题，不能比较绝对精度、稳定性
或形成算法推荐。

当前可审计的指标分为：

- PnP 重投影误差（像素）：描述几何拟合；
- PnP 综合评分 `score = inlier_count / (reproj_error_px + 1e-6)`：多阶段焦距搜索后取最优，用于排序候选位姿；
- 2D 几何拟合诊断（像素）：描述匹配与 homography 的拟合程度；同源 NPY 不得输出米制验证；
- 本地坐标交叉验证（米）：V2 定位任务用最终 2D–3D 内点拟合 H，并按最终位姿生成查询图空间 XYZ NPY；人工选点比较 H→SLAM XYZ 与 NPY XYZ，无外部服务依赖，不是绝对位姿精度；
- 坐标差最终判定：从最终 NPY 最多确定性采样 256 个有效像素，仅当三维差中位数严格 `< coordinate_threshold_m` 才令 V2 或 SALAD+RoMa 原版 `reliable=true`；默认 0.3 米，等于门槛也不准，内点数和相似度不参与最终可信判定；
- 质量门控（2026-08-04 新增）：PnP 结果的多维度门控（`quality_score` / `quality_passed` / `quality_reasons`），门槛为 `score ≥ 4.0`、`inliers ≥ 6`、`reproj_error ≤ 8px`，任一不满足即标记 `quality_passed=false`；
- LAS 邻近性（米/通过率）：描述结果与地图点的邻近程度；
- 独立真值误差（米/度）：仅在 holdout 相机位姿或控制点存在时可用。

### PnP 多阶段焦距搜索与质量门控（2026-08-04）

#### 问题背景

视觉定位管线之前直接使用请求传入的固定 `fov_deg` 构造相机内参做单次 PnP，当真实
相机内参与默认值（75°）不一致时，重投影误差增大、内点减少，导致定位失败或精度
下降。参考 slam-map 的 `solve_pnp_with_normalized_focal_search`，在归一化焦距空间
做粗→细多阶段搜索，自动找到最优内参估计。

#### 算法流程

1. **归一化焦距**：`f_norm = focal_length / img_width`，在初始估计 ±30% 范围内搜索；
2. **粗搜阶段**（默认 3 轮）：每轮按 `splits=5` 等分采样归一化焦距，逐个调用 RANSAC
   PnP + LM 细化，按综合评分排序；下一轮在最优值附近收缩区间；
3. **精搜阶段**（默认 2 轮）：步长收紧到 ±0.01，局部精细搜索；
4. **质量门控**：对最优结果做三维门控（score / inliers / reproj_error），输出
   `quality_passed` 与 `quality_reasons`；
5. 返回最优 `rvec/tvec/inliers` + `focal_search_summary`（尝试次数/成功次数/最优焦距）。

#### 适用范围

所有走 PnP 的算法路径均已升级：
- `salad_roma_v2`（DISK+LG）
- `salad_roma_v2_loftr`（LoFTR）
- `hybrid`（DISK+LG + LoFTR 联合）
- `multi_strategy`（多策略融合）
- `salad_roma`（原版 SALAD+RoMa，含多轮迭代精化）

ACE 端到端回归路径不走 PnP，不受影响。

#### 关键参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `search_range` | 0.3 | 归一化焦距相对搜索范围 ±30% |
| `coarse_rounds` | 3 | 粗搜轮数 |
| `fine_rounds` | 2 | 精搜轮数 |
| `splits` | 5 | 每轮分段采样数 |
| `reproj_error` | 4.0 px | RANSAC 内点阈值 |
| `min_inliers` | 6 | 最少内点 |
| 质量门控 `min_score` | 4.0 | 综合评分门槛 |
| 质量门控 `max_reproj_error` | 8.0 px | 重投影误差上限 |

#### 性能影响

每算法 ~25 次 RANSAC PnP（粗 15 + 精 3 + 其他），512×512 图上单次 PnP <10ms，
总增加 <250ms/算法。可通过 `focal_search=False` 参数回退到单次 PnP。

#### 验证结果（2026-08-04，测试图像 `d7393932`）

| 算法 | 耗时 | 轮数 | 候选 | 焦距搜索尝试/成功 | 内点 | 重投影误差 | 质量门控 | score |
|------|------|------|------|-----------------|------|-----------|---------|-------|
| SALAD+RoMa (原版) | 20.72s | 1 | 3 | - | 282 | 378.62px | ✗ FAIL | - |
| SALAD v2 (DISK+LG) | 4.41s | - | 3 | - | - | - | N/A | - |
| SALAD v2 + LoFTR | 13.37s | 2 | 3 | 21/21 | 167 | 23.02px | ✗ FAIL | 7.26 |
| Hybrid (联合) | 16.13s | 2 | 3 | 21/21 | 169 | 29.71px | ✗ FAIL | 5.69 |
| Multi-Strategy | 11.34s | - | - | - | - | - | - | - |

详见 `reports/verification/2026-08-04-focal-search-quality-gate.md`。

**结论**：所有算法质量门控均正确工作；当前 FAIL 主因是点云渲染 tile 与真实
照片的视角/外观差距（重投影误差远超 8px 门槛），属已知限制。需要真实 holdout
数据集才能验证 focal search 对绝对精度的收益。

### 当前结论

五条算法路径均作为候选保留。独立真值来源、目标设备和最小样本门槛尚未批准，
所以当前不推荐最终算法。详见 `specs/003-multi-algo-verification/clarify.md`。

### 交付物（项目资产）

| 资产 | 路径 | 说明 |
|------|------|------|
| LoFTR 匹配器 | `salad_roma_v2.py` | `_match_tile_with_loftr`, `_get_loftr_model` |
| Hybrid 匹配器 | `salad_roma_v2.py` | `_match_tile_with_hybrid` |
| ACE + LAS 验证 | `salad_roma_v2.py` | `ace_localize_with_las_verify` |
| Multi-Strategy | `salad_roma_v2.py` | `localize_multi_strategy` |
| RGB-only ACE | `ace_trainer.py` | `ACERegressor3Ch`, `ace_predict_rgb`, `train_ace_rgb` |
| 生产四向斜地面渲染 | `scripts/render_ground_tiles.py` | 完整轨迹点+网格点，再展开 yaw 0/90/180/270、pitch -15、roll 0；MapTile 保存实际 pose |
| 多 pitch/水平实验渲染 | `scripts/render_multi_pitch_tiles.py`、`scripts/render_horizontal_tiles.py` | 仅写 `projections/experiments/*`，不得覆盖生产索引 |
| 焦距搜索 + 质量门控 | `pose_utils.py` | `solve_pnp_with_focal_search`（多阶段归一化焦距搜索）、`annotate_pnp_quality`（score/inliers/reproj 三维门控）|
| 验证模块 | `verify_projection.py` | 2D 单应拟合诊断 + 本地 H/最终位姿 NPY 坐标交叉验证（均非绝对 Benchmark）|
| 评估框架 | `scripts/benchmark_localizers.py` | 注册表同源 runner + manifest/run_id |
| 生成报告 | `reports/generated/` | 按运行生成且不进入版本控制 |
| 历史报告 | `reports/benchmark_*`、`reports/verify_*` | 修复前实验，不作验收证据 |

### 验证方法

```bash
# 运行全量 benchmark（5 种方案）
python scripts/benchmark_localizers.py --queries "query_images/*.jpg" --algos all

# 生成单图验证报告
python scripts/generate_verify_report.py --image query_images/xxx.jpg

# 通过 API 调用
curl -X POST http://localhost:8000/api/localize \
  -H 'Content-Type: application/json' \
  -d '{"image_id": 21, "algorithms": ["salad_roma_v2_loftr"]}'
```

### 前端选项

定位页面提供 5 种算法：
- SALAD v2 (DISK+LG) — baseline
- SALAD v2 + LoFTR — 候选
- Hybrid — DISK+LG + LoFTR 联合
- ACE + LAS 验证 — 候选
- Multi-Strategy — 自动选最优

### 日志架构（2026-08-01 更新）

| 文件 | 用途 | 写入方 |
|------|------|--------|
| `logs/http_api.log` | HTTP 请求日志 | FastAPI 中间件 + uvicorn |
| `logs/backend.log` | 所有业务日志 | localizer, matcher, preprocess 等 |
| `logs/archive/` | 旧日志归档 | 迁移时保留 |

### 已知问题（2026-08-03，已修复）

| ID | 问题 | 修复 | 验证 |
|----|------|------|------|
| BUG-003-04 | 一致性检查只比较 XY 平面距离，Z 分量被忽略 → 高度异常位姿被错误判为可靠 | 改为三维欧氏距离 `slam_xyz=(slam_x, slam_y, 0)` vs `npy_xyz` | task #249: 0.968m（含 Z） |
| BUG-003-05 | 精化步骤硬编码 LightGlue，与初始 SALAD+RoMa 的 TinyRoMa 不一致 | 增加 `matcher_type` 参数 + `_dispatch_matcher()` 分派 | task #249: 1 对失败 → 273 对成功 |

### 后续优化方向

1. **真实影像 tile**：用场景真实照片替换点云渲染（最根本）
2. **ACE LAS 验证完善**：与 LAS 点云对比得出精度指标
3. **混合匹配优化**：DISK+LG 粗匹配 → LoFTR 精匹配 → 联合 PnP
4. **降低 min_cert**：当前 0.001，可根据场景调整
5. **精化匹配器通用化**（BUG-003-05 已修复硬编码 LightGlue）：后续可进一步把 `matcher_type` 抽象为策略对象，支持运行时按场景选择 TinyRoMa / LoFTR / LightGlue
6. **焦距搜索参数自适应**（已实现基础版）：当前 `search_range=0.3 / splits=5` 为固定值，后续可根据匹匹配点数量和初始置信度动态调整搜索粒度
