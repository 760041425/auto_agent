# 004 技术计划

## 推荐方案

采用「RANSAC 平面分割 + 分层单应」两阶段方案，分三步交付：

1. **平面检测模块**：新建 `plane_detection.py`，封装 RANSAC 平面分割，返回平面参数 + 内点掩码
2. **分层 H 拟合**：修改 `build_local_coordinate_transform_context` 内部行为，先分割后拟合
3. **诊断 + 量化**：Phase C 生成诊断脚本，对比改进前后 median_m 变化

## 推荐执行顺序

### 步骤 1：新建 `plane_detection.py`（RANSAC 平面分割）

新建 `services/localizer/plane_detection.py`，实现 `segment_plane()`：

```python
def segment_plane(
    points_3d: np.ndarray,
    *,
    distance_threshold: float = 0.2,
    min_inliers: int = 4,
    max_iterations: int = 1000,
    seed: int = 1337,
) -> Tuple[Optional[Tuple[float, float, float, float]], Optional[np.ndarray]]:
    """RANSAC 平面分割，提取最大平面（地面）。

    参数
    ----------
    points_3d : (N, 3) 世界坐标点
    distance_threshold : 点到平面距离阈值（米），默认 0.2
    min_inliers : 最少内点数，默认 4
    max_iterations : RANSAC 最大迭代次数，默认 1000
    seed : 随机种子，保证可复现

    返回
    -------
    (plane_params, inlier_mask) — plane_params=(a,b,c,d) 归一化平面 ax+by+cz+d=0；
    inlier_mask 为长度 N 的 bool 数组。失败返回 (None, None)。
    """
```

实现细节：
- 3 点采样 → 计算平面法向量 → 归一化 (a,b,c,d)
- 计算所有点到平面距离 `|ax+by+cz+d| / sqrt(a²+b²+c²)`
- 统计内点数，保留最大内点集
- 用最大内点集最小二乘重新拟合平面（精化）

### 步骤 2：TDD 写 `test_plane_detection.py` 让它红

新建 `services/tests/test_plane_detection.py`，包含：
- 纯地面点 → 全部内点
- 地面+立面混合 → 只留地面
- 地面点不足 4 → (None, None)
- 全立面点（无地面）→ 返回某平面但内点占比低
- 确定性：同输入同输出

先跑测试让它红（ImportError）。

### 步骤 3：实现让它绿

实现 `plane_detection.py` 让测试通过。

### 步骤 4：改 `verify_projection.py` 在 `build_local_coordinate_transform_context` 里先调用 `segment_plane` 过滤

修改后的行为：

```python
def build_local_coordinate_transform_context(
    query_points, world_points, projection_xyz, output_path,
    *, reproj_thresh_m=3.0, consistency_threshold_m=0.3,
    consistency_sample_limit=256,
    plane_distance_threshold=0.2,  # 新增参数，默认 0.2m
    plane_seed=1337,               # 新增参数，默认 1337
) -> dict:
    # ... 参数校验 ...
    
    # 新增：RANSAC 平面分割
    plane_params, inlier_mask = segment_plane(
        world_points,
        distance_threshold=plane_distance_threshold,
        min_inliers=4,
        seed=plane_seed,
    )
    
    if inlier_mask is not None and int(inlier_mask.sum()) >= 4:
        ground_query = query[inlier_mask]
        ground_world = world[inlier_mask]
        plane_status = "plane_detected"
        n_ground = int(inlier_mask.sum())
    else:
        # 退化：回退到全点拟合
        ground_query = query
        ground_world = world
        plane_status = "insufficient_ground_points"
        n_ground = 0
    
    # 用 ground_* 拟合 H（替代原来的 query/world）
    homography, mask = cv2.findHomography(
        ground_query.reshape(-1, 1, 2),
        ground_world[:, :2].reshape(-1, 1, 2),
        cv2.RANSAC, reproj_thresh_m,
    )
    # ... 保存 XYZ NPY ...
    
    # 在 context 中增加 plane_segmentation 字段
    context = {
        ...,
        "plane_segmentation": {
            "status": plane_status,
            "n_ground_inliers": n_ground,
            "n_total_points": int(len(world)),
            "plane_params": plane_params if plane_params is not None else None,
            "distance_threshold_m": plane_distance_threshold,
        },
    }
```

### 步骤 5：TDD 写 `test_layered_homography.py` 让它红

新建 `services/tests/test_layered_homography.py`，包含：
- T1：纯地面点 → segment_plane 全部内点，H 与全点拟合一致
- T2：地面+混合立面点 → segment_plane 只留地面点，H 用子集拟合
- T3：地面点不足 4 → 退化到全点拟合
- T4：全立面点（无地面）→ 退化或返回 not_available
- T5：evaluate_local_coordinate_consistency 在分层 H 下 median 更小

### 步骤 6：实现让它绿

修改 `verify_projection.py` 让测试通过。

### 步骤 7：跑 `run-all-tests.sh fast` + `drift-check.sh`

```bash
./scripts/run-all-tests.sh fast
./scripts/drift-check.sh
```

### 步骤 8：跑诊断脚本（Phase C 会生成）量化改进

Phase C 生成诊断脚本，对比改进前后：
- 同一组真实场景（task #249 等）的 median_m 变化
- `reliable` 判定翻转比例（false→true / true→false）
- 地面点占比分布

## 上下文分配

| 上下文 | 职责 | 主要触达位置 |
| --- | --- | --- |
| 空间定位 | 平面检测、分层 H 拟合、一致性判定 | `services/localizer/plane_detection.py`（新建）、`services/localizer/verify_projection.py`（修改） |
| 测试 | TDD 测试、回归测试 | `services/tests/test_plane_detection.py`（新建）、`services/tests/test_layered_homography.py`（新建） |

## 接口兼容

- `build_local_coordinate_transform_context` 函数签名**不变**——新增参数全部带默认值，向后兼容
- `context` 返回字典只增加 `plane_segmentation` 字段，不删除/重命名旧字段
- `query_local_coordinate_transform` 不修改
- `evaluate_local_coordinate_consistency` 不修改

## 回滚

- 若分层 H 导致真实场景 median_m 普遍增大，可通过参数 `plane_distance_threshold=100.0` 强制全点拟合（阈值过大时所有点都是内点）
- 若 `segment_plane` 实现有 bug，可回退到全点拟合（`plane_segmentation.status=insufficient_ground_points` 路径）
- 新增模块独立，不影响其他代码

## 不推荐方案

- **不引入 scikit-learn 的 RANSACRegressor**：新依赖，且 50 行 numpy 即可实现
- **不引入点云法线估计**：过度工程，RANSAC 已足够
- **不引入多平面分割（如楼体多立面）**：本次只取最大平面=地面，多平面留后续规格
- **不直接删掉 H 改用 PnP 投影**：保持向后兼容，H 在地面稠密时更快
