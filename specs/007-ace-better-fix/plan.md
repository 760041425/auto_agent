# 007 实施计划

## 批次划分（单流水线，顺序执行；一次一个可验证行为）

### 批次 P0：法线策略 + 模型路由（TL-007-01/02，后端）

1. TL-007-01 红：写 `ace_with_better_normal` 的注入测试——注入假 `predict_dense` 捕获 `normal_map`，断言 6ch 回退路径传入**常量 0.5 占位**（非梯度噪声）。跑 → 确认红（当前传 `_estimate_normal_dsine` 输出）。
2. 绿：`enhanced_ace.py` 引入法线策略常量（`CONSTANT_NORMAL_VALUE = 0.5`）+ 6ch 回退输入构造；`_estimate_normal_dsine` 更名 `_estimate_gradient_normal`（仅 debug 参数启用）。跑 → 绿。
3. TL-007-02 红：写模型路由测试——(a) mock `projections/ace_model_scene.pth` 存在（3ch）→ 断言加载路径为 scene、`predict_dense` 不传 normal_map、`input_mode=ace_scene_rgb3ch`；(b) mock 不存在 → 回退 6ch + 常量占位、`input_mode=ace_6ch_constant_normal`。
4. 绿：新增 `resolve_ace_model()`（存在性 + `_detect_architecture` 通道检测 → 3ch 优先）并接入 `ace_with_better_normal`；`ace_with_normal` 同策略（共享 helper）。跑 → 绿。

### 批次 P1：PnP 诊断 + 死代码（TL-007-03/04/05，后端）

5. TL-007-03 红：构造无内点 2D/3D 对调 `solve_pnp_with_focal_search`，断言返回含 `attempts_summary`（tried_candidates/best_inliers/best_reproj_error_px）。
6. 绿：`pose_utils.py` 失败/成功分支带出 `attempts_summary`（内部已统计，仅透出）。跑 → 绿。
7. TL-007-04 红：mock PnP 失败 → 断言 `ace_better` 结果含 `diagnostics.{pnp, pred_xyz, las_bbox, overlap_with_las_bbox, model, input_mode}`。
8. 绿：`enhanced_ace.py`/`ace_localizer.py` 失败分支组装 `diagnostics`（预测 3D Z 范围 vs LAS bbox 重叠率复用 `_POINT_INDEX` bbox）。跑 → 绿。
9. TL-007-05 红：`ace_with_normal` 预测点不足分支不应 NameError（调用后得优雅失败 dict）。
10. 绿：移除 `ace_with_normal:87`/`ace_rgb_only:199` 的 `result` 未定义引用（低点分支直接返回失败/空 mask 安全处理）。跑 → 绿。

### 批次 P2：前端镜像（TL-007-06）

11. 红：`tests/` 新增 `_localize_failure_diag_line(result)` 镜像函数测试（best inliers / reproj / 预测 Z，缺字段 "—"）。
12. 绿：`web/app_v10.js` 失败诊断行渲染 diagnostics 统计（失败分支 × 成功分支共用辅助行）。

### 批次 P3：回归 + 门禁（TL-007-07/08）

13. TL-007-07：`ace_rgb_only` 成功路径（mock PnP 成功）行为不变 + SALAD 回归绿。
14. TL-007-08：validate-specs / run-all-tests fast / drift-check / 全量 pytest。
15. 文档同步（如术语/上下文变化）+ 浏览器人工真验（可选）。

## 验证方法

- Red→Green：每次改动用 `pytest` 单测验证，红输出文本留证据。
- 全部 mock：禁真实 ACE 训练（13 分钟/次）、禁真实大图 PnP。
- 前端无 JS 运行时：Python 镜像等价验证 + 人工真验。
- 诊断字段有效性：TL-007-04 以返回结构断言，真实精度效果留人工真验（RISK-007-05）。