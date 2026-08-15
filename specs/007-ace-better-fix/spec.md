# 007 ACE 系法线 train/serve skew 修复 + PnP 失败诊断

状态：已实施（TDD 全绿 + 真实图三路径真验通过 + 提交推送；浏览器人工真验可选）
上下文：空间定位、ACE 场景坐标回归、PnP 验证、失败可观测性

## 背景与目标

### 背景

用户报 `ace_better`（J. ACE+更好法线）返回「✗ 失败 / ACE PnP 失败」，并指出「空间感这块还有问题」。排查证据（代码 + `projections/` 产物）链：

1. **法线通道 train/serve skew（根因）**：
   - 训练期（`train_ace_model` → `projections/ace_model.pth`，6ch，8/2）：`SceneCoordinateDataset.__getitem__`（`ace_trainer.py:99-106`）加载**真实 LAS 法线**（363 个 accepted tiles 全部带 `normal_path`，值域 [-1,1] → `(n+1)*0.5` 映射到 [0,1]，分量均值≈0.5）。
   - 推理期（`enhanced_ace.ace_with_better_normal`：`_estimate_normal_dsine`；`ace_localizer.ace_with_normal`：`_estimate_normal_simple`）喂 **Sobel 梯度伪法线**——高频噪声，与训练真法线分布严重不符。
   - 6ch 编码器吃分布外法线输入 → 预测 3D 坐标整体失真（「空间感」没建立）→ RANSAC PnP 在焦距搜索全部候选（~15 次）凑不齐 ≥6 内点/32px → 「ACE PnP 失败」。
2. **次根因——用错模型版本**：`ace_with_better_normal` 恒用 `load_coord_regression()` 加载 8/2 旧 6ch `ace_model.pth`；而 8/9 已有场景重训 **3ch RGB-only 模型** `projections/ace_model_scene.pth`（`train_ace_rgb`/`ACERegressor3Ch`，训练只取 RGB，见 `ace_trainer.py:340-388`）——该链路与 `train_ace`/`ace_rgb_only` 验证自洽可跑通，但 ace_better/ace_normal 未接入。
3. **「更好法线」名不副实**：`_estimate_normal_dsine` 实为 Sobel 梯度近似（注释「简化版，无需额外模型」），非真 DSINE。
4. **失败不可观测**：PnP 失败只返回 `{"success": False, "error": "ACE PnP 失败", "tag", "elapsed"}`，没有尝试次数/最佳内点/预测 3D 分布，「空间感」问题无法量化定位。
5. **同文件 copy-paste 死代码**：`ace_with_normal:87`、`ace_rgb_only:199` 引用未定义 `result['coords_3d']`，低点分支触发 NameError（扩散排查命中，与本修复同文件必达）。

### 目标（用户已拍板：先 A+B+C 治标，D 真法线管线最后再做）

1. **消除法线 skew（A）**：`ace_better`/`ace_normal` 推理不再用梯度伪法线喂 6ch 模型；与训练分布对齐（见 D-007-01 的「3ch 场景模型优先、常量 0.5 占位回退」策略），并在结果中标注 `input_mode` + `normal_source`。
2. **模型路由（B）**：`ace_better`/`ace_normal` 自动优先使用场景 3ch 模型 `projections/ace_model_scene.pth`（存在时），走 RGB-only 自洽推理；不可用时回退默认 6ch + 常量法线占位。
3. **PnP 失败诊断（C）**：`solve_pnp_with_focal_search` 返回 `attempts_summary`；ACE 系失败结果携带 `diagnostics`（pnp 统计 + 预测 3D 分布 vs LAS bbox 重叠率 + model/input_mode），让「空间感」可量化。
4. **诚实命名**：`_estimate_normal_dsine` 更名 `_estimate_gradient_normal` 并仅作 debug 输入，不再默认使用。
5. **死代码修复**：`ace_with_normal`/`ace_rgb_only` 的 `result` 未定义引用移除，低点分支优雅失败。
6. **前端诊断行**：失败时展示 PnP 统计（best inliers / reproj / 预测 Z 范围），沿用 specs/006 的 Python 镜像测试先例。

## 范围

### In Scope

- `services/localizer/coord_regression.py`：`predict_dense` 对 6ch 模型的 normal 占位语义明确；可加 `input_mode` 回传辅助（若实现成本低）。
- `services/localizer/enhanced_ace.py`：`ace_with_better_normal` 法线策略 + 模型路由 + 失败 diagnostics；`_estimate_normal_dsine` 更名；保留 tag 兼容。
- `services/localizer/ace_localizer.py`：`ace_with_normal` 同策略；`ace_with_normal`/`ace_rgb_only` 死代码修复；`_estimate_normal_simple` 更名/降级。
- `services/localizer/pose_utils.py`：`solve_pnp_with_focal_search` 返回值补 `attempts_summary`（只增不改，SALAD 调用方不受影响）。
- 测试：`services/tests/`、`tests/`（前端镜像）、`api/tests/`（如需要）；全部 mock，禁止真实训练/真实 PnP 大数据。
- 文档：`docs/ubiquitous-language.md`、`contexts/spatial-localization/`（如涉及）。

### Out of Scope

- **D（治本，后续规格）**：推理期真法线估计（DSINE/MiDaS 集成）+ 6ch 真法线路径精度基准——用户明确「先 A+B+C，不行最后再做 D」，记录 RISK-007-01。
- 不重训任何模型；不改训练管线（训练法线已有真值，不动）。
- 不改 `ace_rgb_only`/`train_ace` 的成功路径行为（仅修死代码分支）。
- 不改 SALAD 系；不改 AC-003-14 可信判据（006 已交付）。

## 验收标准

- **AC-007-01**：`ace_better`/`ace_normal` 推理不再调用梯度伪法线作为默认 6ch 输入；输入构造与训练分布对齐，结果含 `input_mode`（`ace_scene_rgb3ch` 或 `ace_6ch_constant_normal`）与 `normal_source`。
- **AC-007-02**：模型路由——场景 3ch 模型存在时走 RGB-only（无 normal_map 输入到 3ch 模型）；不存在时回退 6ch + 常量 0.5 占位（≈训练真法线分布均值）。
- **AC-007-03**：`solve_pnp_with_focal_search` 返回 `attempts_summary`（`tried_candidates`/`best_inliers`/`best_reproj_error_px`），成功与失败分支都有。
- **AC-007-04**：ACE 系失败结果含 `diagnostics`：`diagnostics.pnp`、`diagnostics.pred_xyz`（z_min/z_max/center/count）、`diagnostics.las_bbox`、`diagnostics.overlap_with_las_bbox`、`diagnostics.model`（path/in_channels）、`diagnostics.input_mode`。
- **AC-007-05**：`ace_with_normal`/`ace_rgb_only` 低点分支优雅返回失败，不再 NameError（`result` 未定义引用移除）。
- **AC-007-06**：前端失败诊断行展示 PnP 统计（best inliers / reproj / 预测 Z 范围，缺字段 "—"），Python 镜像测试通过。
- **AC-007-07**：回归——`ace_rgb_only` 成功路径（mock PnP 成功）行为不变；SALAD 系回归测试全绿。
- **AC-007-08**：`validate-specs.sh` + `run-all-tests.sh fast` + `drift-check.sh` + 新旧 pytest 全绿。

## 成功标准

- 存在场景 3ch 模型时，`ace_better` 走与 `train_ace`/`ace_rgb_only` 同渠道的自洽 RGB 推理，消除法线 skew。
- `ace_better`/`ace_normal` 失败时结果/前端能指出 PnP 统计与预测 3D 相对场景 bbox 的空间分布，不再只报「ACE PnP 失败」。
- SALAD 系与 `train_ace`/`ace_rgb_only` 正常路径无回归；全量门禁绿。

## 关联

- `specs/006-ace-coordinate-consistency/`（ACE 系降级「无法判定」展示；本规格不改判据）
- `services/localizer/enhanced_ace.py`、`ace_localizer.py`、`coord_regression.py`、`pose_utils.py`、`ace_trainer.py`
- `projections/ace_model.pth`（6ch 旧模型，8/2）、`projections/ace_model_scene.pth`（3ch 场景模型，8/9，运行产物不入库）
- `web/app_v10.js`（失败诊断行渲染）
- `tests/` 前端镜像（沿用 005/006 先例）、`services/tests/`