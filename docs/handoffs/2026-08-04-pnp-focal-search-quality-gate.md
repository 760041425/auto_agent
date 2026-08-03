# 交接：PnP 多阶段焦距搜索 + 质量门控

日期：2026-08-04
状态：已实现并通过验证
工作目录：`/Users/pangjinfu/code/opencode-demo`

## 1. 变更概述

在视觉定位管线的 PnP 环节增加两个核心能力：
1. **多阶段归一化焦距搜索**：参考 slam-map，在归一化焦距空间做粗→细搜索，自动找到最优相机内参
2. **质量门控**：对 PnP 结果做三维门控（score / inliers / reproj_error），输出 `quality_passed` 与 `quality_reasons`

## 2. 代码变更

### 2.1 新增函数（`services/localizer/pose_utils.py`）

| 函数 | 行数 | 职责 |
|------|------|------|
| `solve_pnp_with_focal_search` | ~120 | 多阶段归一化焦距搜索（粗 3 轮 + 精 2 轮） |
| `annotate_pnp_quality` | ~35 | 三维质量门控 |
| `fov_to_normalized_focal` | ~3 | 视场角 → 归一化焦距 |
| `normalized_focal_to_K` | ~6 | 归一化焦距 → K 矩阵 |
| `extract_normalized_focal` | ~3 | K 矩阵 → 归一化焦距 |
| `compute_pnp_score` | ~3 | 综合评分 = inliers / (reproj_error + eps) |

### 2.2 调用方升级

| 文件 | 改动 |
|------|------|
| `salad_roma_v2.py` | `localize_with_salad_roma_v2`、`localize_multi_strategy`、联合 PnP、迭代精化 4 处 PnP 调用全部替换为 focal search + quality gate |
| `salad_roma.py` | `_solve_pnp` 内部升级为 focal search + quality gate，结果透传 score/quality_passed/quality_reasons |
| `contracts.py` | quality 字典增加 score/quality_passed/quality_score/quality_reasons 字段 |
| `web/app_v10.js` | 两个渲染路径（loadLatestLocalizeResult + pollLocalize）增加耗时和质量门控显示 |
| `api/tests/test_localization_contract.py` | 适配新 quality 字段 |

### 2.3 关键参数

```python
# pose_utils.py 默认值（对齐原版行为）
reproj_error=8.0        # RANSAC 内点阈值（渲染 tile 匹配误差大，4.0 太严）
ransac_method=cv2.SOLVEPNP_ITERATIVE  # ITERATIVE 精度高于 EPNP
confidence=0.85         # RANSAC 置信度
min_inliers=6           # 最少内点

# 质量门控阈值
min_score=4.0           # 综合评分门槛
max_reproj_error=8.0    # 重投影误差上限

# 焦距搜索参数
search_range=0.3        # 归一化焦距相对搜索范围 ±30%
coarse_rounds=3         # 粗搜轮数
fine_rounds=2           # 精搜轮数
splits=5                # 每轮分段采样数
```

## 3. 受影响的算法路径

| 算法 ID | 路径 | 状态 |
|---------|------|------|
| `salad_roma_v2` | v2 引擎 (DISK+LG) | ✅ |
| `salad_roma_v2_loftr` | v2 引擎 (LoFTR) | ✅ |
| `hybrid` | v2 引擎 (Hybrid) | ✅ |
| `multi_strategy` | 多策略融合 | ✅ |
| `salad_roma` | 原版 SALAD+RoMa | ✅ |
| `salad_lightglue` | 旧版 LightGlue | ✅ |
| `ace` | ACE 端到端回归 | 不走 PnP，不受影响 |
| `flann` | SIFT+FLANN | 简单 PnP，暂不动 |

## 4. 性能影响

- 每算法 ~25 次 RANSAC PnP（粗 15 + 精 3 + 其他）
- 512×512 图上单次 PnP <10ms，总增加 <250ms/算法
- 实测总耗时：
  - SALAD+RoMa: ~20s（含多轮迭代渲染）
  - DISK+LG: ~3s
  - LoFTR: ~13s
  - Hybrid: ~16s
  - Multi-Strategy: ~11s

## 5. 验证结果

### 5.1 功能验证（全部通过）

- [x] 焦距搜索在所有 5 条 PnP 路径中正常运行
- [x] 质量门控正确标记不通过原因
- [x] 耗时数据正确采集
- [x] 前端正确显示耗时和质量门控状态
- [x] 快速测试 85 passed, 4 deselected
- [x] validate-specs / drift-check 通过

### 5.2 精度验证（地面点）

Task #274 中心区域 16 点系统采样：
- **12 个地面点：平均误差 0.93m**（0.66-1.78m），NPY_Z ≈ -0.6m
- 4 个非地面点：误差 5-11m（立面/坡面，单应投影几何局限）

**结论：焦距搜索 + 质量门控未使结果变差。**

### 5.3 已知问题修复

- **reproj_error 从 4.0 改为 8.0**：初始实现用 4.0 导致内点门槛过严，内点集变化，位姿偏移，选点坐标差变大。恢复原版 8.0 后正常。
- **EPNP → ITERATIVE**：ITERATIVE 精度更高，对齐原版行为。
- **confidence 从 0.99 改为 0.85**：对齐原版，更宽松。

## 6. 交接给下一位 Agent 的事项

### 6.1 待解决

1. **独立真值 Benchmark（Phase B，暂时遗留）**
   - 当前测试图像无独立真值，无法评估 focal search 对绝对精度的收益
   - 需要：确认获批准的 holdout 真值集、目标设备、样本门槛
   - 详见 `specs/003-multi-algo-verification/clarify.md` 的 CL-003-05 至 CL-003-07

2. **单应投影局限**
   - H→SLAM 强制 Z=0，立面/坡面点误差 5-11m
   - 可选方案：过滤非地面点 / 用 PnP 3D 投影替代 H / 分层拟合多个 H
   - 当前不影响地面点精度（~1m）

3. **ACE + LAS 路径**
   - ACE 端到端回归不走 PnP，不受 focal search 影响
   - 当前 `contracts.py` 中 quality 字段对 ACE 路径返回 None

### 6.2 不要做的

- 不要将 `reproj_error` 降到 4.0 以下（渲染 tile 匹配误差大）
- 不要移除原版 `salad_roma.py`（作为对照路径保留）
- 不要修改 `.gitignore` 中的 `reports/generated/`（运行产物不提交）

### 6.3 关键文件速查

```
# 焦距搜索核心
services/localizer/pose_utils.py::solve_pnp_with_focal_search  (~line 374)
services/localizer/pose_utils.py::annotate_pnp_quality         (~line 530)

# 调用方
services/localizer/salad_roma_v2.py::localize_with_salad_roma_v2  (~line 536)
services/localizer/salad_roma_v2.py::localize_multi_strategy      (~line 441)
services/localizer/salad_roma.py::_solve_pnp                      (~line 898)

# 结果契约
services/localizer/contracts.py::normalize_localization_result   (~line 26)

# 前端
web/app_v10.js::loadLatestLocalizeResult  (~line 829)
web/app_v10.js::pollLocalize              (~line 988)

# 文档
docs/engineering-playbook.md  — PnP 焦距搜索与质量门控章节
docs/context-map.md           — 验证方式章节
docs/ubiquitous-language.md   — 术语表
docs/CHANGELOG-003.md         — 变更记录

# 验证报告
reports/verification/2026-08-04-focal-search-quality-gate.md
reports/verification/2026-08-04-ground-point-validation.md
```

### 6.4 质量门禁

```bash
./scripts/validate-specs.sh        # 规格校验
./scripts/run-all-tests.sh fast   # 快速测试（85 passed）
./scripts/drift-check.sh          # 漂移检查
./scripts/traceability-report.sh  # 追踪报告
```

### 6.5 Git 历史

```
959c0e3 docs: 地面点验证报告（证明优化未退化）
37f9659 fix: 焦距搜索恢复原版 RANSAC 参数，避免内点集偏移
f427cb9 docs: 修正轮数含义说明，v2 改用实际迭代轮数
926bb64 docs: 验证报告增加轮数和焦距搜索详情
ff377d2 docs: 添加算法验证报告（焦距搜索 + 质量门控）
041c7b6 feat: 原版 SALAD+RoMa 添加焦距搜索和质量门控
7722cd9 feat: PnP 多阶段焦距搜索 + 质量门控
40cb248 docs: 更新焦距搜索与质量门控相关文档
```

## 7. 参考

- slam-map PnP 实现：`/Users/pangjinfu/code/slam-map/slam-map-engine/engine/pnp_calculation.py`
- Spec 003 规格包：`specs/003-multi-algo-verification/`
- 统一语言：`docs/ubiquitous-language.md`
