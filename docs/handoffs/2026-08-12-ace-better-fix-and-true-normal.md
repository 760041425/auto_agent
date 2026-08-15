# 交接文档：ACE 系法线 skew 修复（007）+ 推理期真法线（008 实施中）

日期：2026-08-12
状态：007 已实现并通过验证（含真实图真验）；008 P0+P1 已实现，P2/P3 待办
工作目录：`/Users/pangjinfu/code/opencode-demo`
Git 分支：`feature/006-ace-coordinate-consistency`（006+007+008 规格均在此分支）

---

## 1. 项目背景

### 1.1 仓库概述

LAS 点云地图视觉定位系统：上传查询图像 → 与点云渲染投影图匹配 → 估计相机位姿。
后端 FastAPI + SQLite，定位管线多算法（SALAD+RoMa、DISK+LG、LoFTR、Hybrid、ACE 系），
前端静态 HTML/JS（`web/app_v10.js`）。工程流程：DDD 定边界、SDD 管变更、TDD 驱动实现。

### 1.2 本次变更目标（用户报障 → 根因 → 修复）

用户报 `ace_better`（J. ACE+更好法线）返回「✗ 失败 / ACE PnP 失败」，并指出「空间感这块还有问题」。

**根因链（已用代码 + 产物证实）**：

1. **法线通道 train/serve skew（主根因）**：
   - 训练期（`train_ace_model` → `projections/ace_model.pth`，6ch，8/2）：`SceneCoordinateDataset`
     加载**真实 LAS 法线**（363 个 accepted tiles 全部带 `normal_path`，值域 [-1,1]，映射 `(n+1)*0.5`→[0,1]，分量均值≈0.5）；
   - 推理期（`ace_better`/`ace_normal`）：喂 **Sobel 梯度伪法线**（高频噪声，与训练真法线分布严重不符）→
     6ch 编码器吃分布外输入 → 预测 3D 坐标整体失真（「空间感」没建立）→ PnP 焦距搜索全部候选
     （~15 次）凑不齐 ≥6 内点/32px → 「ACE PnP 失败」。
2. **次根因——用错模型**：`ace_better` 恒用 8/2 旧 6ch `ace_model.pth`，放着 8/9 已自洽的
   3ch RGB-only 场景模型 `ace_model_scene.pth`（`train_ace_rgb`，训练只取 RGB）不用。
3. **「更好法线」名不副实**：`_estimate_normal_dsine` 实为 Sobel 梯度近似（注释「简化版」），非真 DSINE。
4. **失败不可观测**：失败只返回 `{"success": False, "error": "ACE PnP 失败"}`，无任何诊断。

---

## 2. 007 变更清单（已实施）

### 2.1 `services/localizer/enhanced_ace.py`

| 位置 | 内容 |
|------|------|
| 模块常量 | `SCENE_MODEL_PATH`/`DEFAULT_MODEL_PATH`/`CONSTANT_NORMAL_VALUE=0.5`/`INPUT_MODE_*`/`NORMAL_SOURCE_*` |
| `resolve_ace_model()` | 模型路由：scene 3ch 存在（通道检测 `in_channels==3`）→ RGB-only；否则回退 6ch。返回 `(model, info{model_path, in_channels, input_mode, normal_source})` |
| `build_constant_normal_map(h,w)` | 常量 0.5 法线占位（≈训练真法线映射均值） |
| `build_ace_failure_diagnostics(pts_3d, model_info, pnp_out, las_pts)` | 失败诊断组装：pnp 统计 / pred_xyz(Z 范围·中心·点数) / las_bbox / overlap_with_las_bbox / model / input_mode |
| `_estimate_normal_dsine` → `_estimate_gradient_normal` | 更名 + docstring 标注「梯度近似，debug-only，非 DSINE」 |
| `ace_with_better_normal(..., normal_mode="constant", debug_normal=False)` | 6ch 路径：`normal_mode=="constant"` → 常量占位；`debug_normal` → 梯度（标注 `gradient_debug`）；失败/成功分支携带 `input_mode`+`normal_source`+`diagnostics` |

### 2.2 `services/localizer/ace_localizer.py`

| 位置 | 内容 |
|------|------|
| `_ace_failure_result()` | 共享失败结构（保留 error/tag/elapsed 顶层键 + input_mode/normal_source/diagnostics） |
| `ace_with_normal` | 同 007 策略（resolve_ace_model + 常量占位 + normal_mode）；**移除 87 行 `result['coords_3d']` 未定义引用死代码（NameError）** |
| `ace_rgb_only` | **移除 199 行 `result['coords_3d']` 死代码**，低点分支优雅失败 |

### 2.3 `services/localizer/pose_utils.py`

`solve_pnp_with_focal_search`：新增 `best_partial` 跟踪（含未达 min_inliers 的最优候选），
返回值增加 `attempts_summary`（`tried_candidates`/`best_inliers`/`best_reproj_error_px`），
成功与失败分支都带出；既有键与语义不变（SALAD 系兼容）。

### 2.4 `web/app_v10.js`

- `localizeFailureDiagLine(r)`（~L674）：失败结果含 `diagnostics` 时展示
  「内点 X | 重投影误差 Y px | 预测Z [a, b]」，缺字段 "—"，无 diagnostics 兜底原文案；
- 失败渲染接入两处：`loadLatestLocalizeResult`（~L1001）、`pollLocalize`（~L1188）。

### 2.5 测试

- `services/tests/test_ace_better_fix.py`（7 用例：TL-007-01..05，全部 mock）
- `tests/test_frontend_ace_better_fix.py`（6 用例：TL-007-06，Python 镜像）

### 2.6 文档

- `specs/007-ace-better-fix/` 八件套 + testlist/tasks/checklist 状态
- 术语同步：本次 handoff + `docs/ubiquitous-language.md` + `docs/context-map.md`

---

## 3. 007 真实验证（2026-08-11，`query_images/d7393932` 与用户报障同图）

| 路径 | PnP | LAS 验证率 | mean_distance_m | input_mode |
|------|-----|-----------|-----------------|-----------|
| scene 3ch（默认路由） | **success**（原「ACE PnP 失败」→ 恢复） | 1.0 | 1.84 | `ace_scene_rgb3ch` |
| 6ch 常量占位（回退） | success | 1.0 | **0.52（最优）** | `ace_6ch_constant_normal` |
| 6ch 梯度伪法线（修复前行为） | success | 1.0 | 1.14 | `gradient_debug` |

**结论**：skew 消除生效（路由正确、输入对齐、失败可观测）；但精度天花板仍低
（reproj 600~780px、scene 3ch 两次运行 pose z 相差 ~20m）→ 触发用户「不行最后再做 D」→ 008 立项。

---

## 4. 008 变更清单（实施中）

### 4.1 已完成（P0+P1，TDD 全绿）

#### `services/localizer/normal_estimator.py`（新建）

| 位置 | 内容 |
|------|------|
| `NORMAL_SOURCE_DSINE/MIDAS/FALLBACK`（:21-23） | 来源标签常量 |
| `normal_source_from_estimate()`（:38） | 读取 `_last_source`（本次推理实际来源） |
| `_load_model()`（:50） | **桩**：抛 `NormalModelNotReadyError`——接真实权重时在这里实现路径校验 + 懒加载缓存 + DSINE/MiDaS forward |
| `_raw_infer()`（:62） | 懒加载后推理 |
| `estimate_normal(image) -> np.ndarray`（:78） | BGR uint8 → `(H,W,3) float32 [0,1]`，映射 `(n+1)*0.5`（已 [0,1] 则原样）；权重不可用自动回退常量 0.5 + `constant_fallback` |

#### `services/localizer/enhanced_ace.py` / `ace_localizer.py`

`ace_with_better_normal`/`ace_with_normal` 新增 `normal_mode: str = "constant"`：
- `"constant"`：007 行为不变（常量 0.5）；
- `"dsine"`：`estimate_normal(query_img)` + `normal_source_from_estimate()`，模型不可用自动落回退；
- 3ch 路径（scene 模型）不受影响（仍 RGB-only）。

#### 测试

`services/tests/test_ace_true_normal.py`（5 用例：TL-008-01/02/03，mock 估计器）。
门禁：validate-specs 8 包 ✓ / run-all fast **117 passed** ✓ / drift-check 0 error ✓ /
services/tests 全量 90 passed（唯一红 `test_read_points3d_txt` 为基线数据缺失，与本次无关）。

### 4.2 待办（P2/P3）

1. **P2 抽样选型**：真实权重可达性探测 → 5 张图 DSINE vs MiDaS 试跑（质量 + MPS/CPU 耗时）
   → 更新 `specs/008 decisions` D-008-01 终选。实现点：`normal_estimator._load_model()` 由桩改真实
   （需处理权重路径、懒加载缓存、DSINE/MiDaS 前向）。
2. **P3 基准 + 路由决策**：基准运行器（≥20 张：`query_images/` 真实图 + `projections/tiles/`
   accepted tile 渲染图有 camera_pose 真值）四路径对比（scene 3ch / 6ch 常量 / 6ch 真法线 /
   6ch 梯度对照），指标 success 率/LAS 验证率/mean_distance/reproj/inliers，落 `reports/benchmark_008*.json`；
   依据 D-008-03 切换条件（胜出 ≥30% 或 ≤0.5m）决定是否改 `resolve_ace_model` 默认路由；
   回归 TL-008-06（007 默认路由不回归）+ 门禁全绿。
3. **收尾**：更新 specs/008 追踪状态 → commit + push。

---

## 5. 验证门禁（当前基线）

| 门禁 | 结果 |
|------|------|
| `validate-specs.sh` | ✅ 8 规格包 |
| `run-all-tests.sh fast` | ✅ 117 passed, 5 deselected |
| `drift-check.sh` | ✅ 0 error（5 条历史运行产物 warning） |
| 全量 pytest | ✅ 140 passed；唯一失败 `test_read_points3d_txt`（@integration，缺 `las/points3D.txt` 数据文件，基线既有、与 007/008 无关，已确证） |

---

## 6. 已知问题 / 待解决

| 问题 | 影响 | 处置 |
|------|------|------|
| ACE 精度天花板低（LAS 0.52~1.84m、reproj 600~780px、位姿随机性） | 「空间感」只缓解未根治 | 008（推理期真法线 + 基准）治本 |
| 6ch 旧模型 vs scene 3ch 模型路径选择依赖基准数据 | 默认路由可能变化 | D-008-03 数据决策 |
| `web/app.js`（非 v10 旧 UI）仍渲染裸 `r.error` | 旧页面无失败诊断行 | 007 范围外，如需覆盖另立事项 |
| `test_read_points3d_txt` 数据缺失 | 全量 pytest 一条红 | 需补 `las/points3D.txt` 数据文件（@integration） |
| PR 未建（GitHub EMU 无 `createPullRequest` 权限） | 变更未合并 | 需有权限令牌或手动建 PR（compare 链接见 §8） |

---

## 7. 交接事项

### 7.1 下一位 Agent 建议做的

1. **008 P2 抽样选型**：先探测真实权重可达性（DSINE 权重源 / MiDaS torchvision）；不可达则按
   RISK-008-01 记录降级（基准可部分 mock），不阻塞流程。
2. **008 P3 基准与路由决策**：按 D-008-03 以数据决策；TL-007-01/02 若路由切换需同步更新。
3. **`las/points3D.txt` 补齐**：消除全量 pytest 唯一红（数据文件，非代码）。
4. 浏览器人工真验（可选）：失败诊断行「内点 X | 重投影误差 Y px | 预测Z [a,b]」展示。
5. PR 处置：换有权限令牌或手动建，标题建议「006+007+008: ACE 坐标差判定对齐 + 法线 skew 修复 + 真法线治本」。

### 7.2 不要做的

- ❌ 不要重训任何 ACE 模型（训练法线已有真实 LAS 真值；重训是 008 基准确认后才考虑的独立事项）
- ❌ 不要改 `solve_pnp_with_focal_search` 既有返回键名/语义（SALAD 系依赖）
- ❌ 不要用 Sobel 梯度伪法线作为默认 6ch 推理输入（skew 根因，仅 debug）
- ❌ 不要提交 `projections/`（含模型权重）、`reports/`、`query_images/`、`las/` 运行产物
- ❌ 不要动受保护分支 `test`/`main`；PR 不直推

### 7.3 关键文件速查

```
# 007 核心
services/localizer/enhanced_ace.py        — resolve_ace_model / 常量占位 / 失败诊断 / ace_with_better_normal
services/localizer/ace_localizer.py       — ace_with_normal / ace_rgb_only（死代码已修）/ _ace_failure_result
services/localizer/pose_utils.py          — solve_pnp_with_focal_search（attempts_summary）
web/app_v10.js                            — localizeFailureDiagLine（L674）
services/tests/test_ace_better_fix.py     — 007 后端测试（7）
tests/test_frontend_ace_better_fix.py     — 007 前端镜像（6）

# 008 核心
services/localizer/normal_estimator.py    — estimate_normal / 来源标签 / 真实模型桩（_load_model）
services/tests/test_ace_true_normal.py    — 008 测试（5）

# 规格
specs/007-ace-better-fix/                 — 007 八件套（已实施）
specs/008-ace-true-normal/                — 008 八件套（P0+P1 已交付）
specs/006-ace-coordinate-consistency/     — 006 八件套（已实施）

# 文档
docs/ubiquitous-language.md               — 新增法线/输入模式/PnP 诊断术语
docs/context-map.md                       — ACE 法线对齐与诊断小节
docs/handoffs/2026-08-12-...md            — 本文档

# 数据（运行产物，不入库）
projections/ace_model.pth                 — 6ch 旧模型（8/2）
projections/ace_model_scene.pth           — 3ch 场景模型（8/9）
projections/tiles/*_normal.npy            — 训练真实法线（[-1,1]）
query_images/d7393932-*.jpg               — 用户报障同款真验图
```

### 7.4 Git 历史

```
91c0224 docs(specs): 008-ace-true-normal 八件套（D 治本）
dcc4a3a docs(specs): 007-ace-better-fix 八件套 + 追踪状态
2598d40 feat(web): ACE 失败诊断行展示 PnP 统计与预测Z范围
5452dd6 fix(localizer): ACE 法线对齐训练 + 场景3ch模型路由 + PnP失败诊断 + 死代码修复
4befffb feat(ace): ACE 场景实验 WIP 全量入库（006 随带）
5a540d0 docs+spec: specs/006 ACE 坐标差最终判定链路八件套 + 术语文档同步
```

### 7.5 常用命令

```bash
./scripts/validate-specs.sh        # 规格校验（8 包）
./scripts/run-all-tests.sh fast   # 快速测试（117 passed）
./scripts/drift-check.sh          # 漂移检查
.venv/bin/python -m pytest services/tests/test_ace_better_fix.py -v   # 007 后端
.venv/bin/python -m pytest services/tests/test_ace_true_normal.py -v  # 008 P0/P1
.venv/bin/python -m pytest services/tests/ -q                        # 后端全量
.venv/bin/python -m pytest api/tests/ services/tests/ tests/ -q      # git_auto 强制前置
```

---

## 8. PR 与合并

- 分支：`feature/006-ace-coordinate-consistency`（已推 `origin`，含 006+007+008 规格 commit）
- compare URL：`https://github.com/760041425/auto_agent/compare/test...feature/006-ace-coordinate-consistency`
- 阻塞：当前 gh 凭据（Enterprise Managed User）无 `createPullRequest` 权限 → 需用户手动建 PR
  或提供有权限令牌。base 应选 `test`（受保护分支，不直推）。
