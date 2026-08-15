# 008 ACE 推理期真法线估计 + 6ch 精度基准（D 治本）

状态：已交付（P0+P1 法线估计模块 + normal_mode 接入全绿；P2 抽样选型定案 MiDaS，``_load_model`` 由桩改真实；P3 基准完成、路由决策维持 007 现状；TL-008-06 全量门禁绿，待 commit）
上下文：空间定位、ACE 场景坐标回归、法线估计、精度基准

## 背景与目标

### 背景

specs/007 已消除 ACE 系法线 train/serve skew（推理不再喂 Sobel 梯度伪法线；场景 3ch 模型优先 / 6ch 常量 0.5 占位回退）并补齐 PnP 失败诊断。真实图真验（`query_images/d7393932`，2026-08-11）显示：

| 路径 | PnP | LAS mean_distance | reproj_error_px | 备注 |
| --- | --- | --- | --- | --- |
| scene 3ch RGB-only（默认路由） | success | 1.84 m | 672 | 两次运行 pose z 相差 ~20m（RANSAC/采样随机性大） |
| 6ch + 常量 0.5 占位（回退） | success | **0.52 m（最优）** | 659 | 6ch 旧模型用真实法线训练，常量≈均值占位反而稳 |
| 6ch + 梯度伪法线（修复前行为） | success | 1.14 m | 696 | 偶发「ACE PnP 失败」的根源输入 |

结论：**skew 消除后 PnP 不再系统性失败，但精度天花板仍低**（LAS 0.5~1.8m、reproj 600~780px、位姿随机性强），「空间感」只是缓解未根治。根因：6ch 旧模型 `ace_model.pth` 是用**真实 LAS 法线**（363 accepted tiles 的 `normal_path`）训练的，推理侧没有真法线（常量 0.5 只是无信息占位），无法发挥 6ch 法线通道信息；而 3ch 场景模型（8/9 重训、RGB-only）本身精度有限。用户此前拍板「先 A+B+C，不行最后再做 D」——007 真验证明「精度不足」，D 治本正式立项。

### 目标

1. **推理期真法线估计**：评估并接入一种图像法线估计（首选 DSINE 预训练模型，备选 MiDaS 深度→法线），替换「常量 0.5 占位 / 梯度伪法线」作为 6ch 路径的推理输入；法线映射与训练一致（`(n+1)*0.5` → [0,1]）。
2. **6ch 真法线推理路径**：`ace_better`/`ace_normal` 支持 `normal_mode="dsine"`（真法线）与 `"constant"`（现占位）；真法线模型可用时正式启用。
3. **精度基准**：固定查询集（≥20 张真实查询图，含已知/可推导位姿或仅 LAS 验证）+ 指标（PnP 成功率、LAS 验证率、mean_distance_m、reproj_error_px、inliers）对比四条路径：scene 3ch / 6ch 常量 / 6ch 真法线 / 6ch 梯度（对照线）。**以数据决定默认路由**。
4. **路由决策产物**：基准报告落 `reports/`（运行产物）；依据报告把 007 的 `resolve_ace_model` 决策升级（若 6ch 真法线显著胜出 → 默认优先 6ch 真法线；否则维持现状并在文档注明）。

## 范围

### In Scope

- 法线估计模块：新增 `services/localizer/normal_estimator.py`（DSINE 封装或 MiDaS 深度→法线；模型权重懒加载；`estimate_normal(image) -> [0,1] float32`，接口与 `(n+1)*0.5` 映射一致）。
- 真法线接入：`enhanced_ace.ace_with_better_normal`/`ace_localizer.ace_with_normal` 增加 `normal_mode` 参数；6ch 路径在真法线可用时使用。
- 基准脚本/测试：`services/tests/test_normal_estimator.py`（接口/映射/mock 权重）+ 基准运行器（脚本放 scripts/ 或日志化，可用 mock 法线替代真实权重跑通流程）。
- 路由决策：依据基准结果更新 `resolve_ace_model` 或决策文档。
- 文档：`docs/`（法线估计术语）、`contexts/spatial-localization/`。

### Out of Scope

- 不改训练管线（训练法线已有真实 LAS 法线，不重训、不改数据）。
- 不引入新的定位算法（仍是 ACE 系）。
- 不动 SALAD 系与 AC-003-14 可信判据展示（006 已交付）。
- 模型权重不入库（运行产物）。

## 验收标准

- **AC-008-01**：`estimate_normal(image)` 返回 [0,1] float32 与输入同尺寸；DSINE/MiDaS 权重缺失或加载失败时优雅回退常量 0.5（不崩溃）并标注 `normal_source`。
- **AC-008-02**：`ace_better`/`ace_normal` 支持 `normal_mode`（`"dsine"`/`"constant"`），6ch 路径真法线可用时输入与训练分布一致（真实法线而非占位/梯度）。
- **AC-008-03**：基准运行器对固定查询集输出四路径对比报告（成功率 / LAS 验证率 / mean_distance_m / reproj / inliers），结果落 `reports/`。
- **AC-008-04**：基于基准数据给出**显式路由决策**：6ch 真法线显著胜出 → 默认路由切换（specs/007 的 `resolve_ace_model` 更新 + 单测更新）；否则维持现状并文档注明理由（数据附表）。
- **AC-008-05**：007 既有行为不回归：默认路由（scene 3ch 存在时）与回退路径的 TL-007-01/02 仍绿；新增路径测试全绿。
- **AC-008-06**：门禁 validate-specs / run-all-tests fast / drift-check / 全量 pytest 全绿。

## 成功标准

- 推理侧有真实法线输入时，6ch 路径的 LAS mean_distance 相比常量/梯度对照显著下降（基准报告实证，目标 mean_distance ≤ 0.5m 或相对最优路径提升 ≥30%）。
- 「空间感」可量化收敛：基准报告 + 前端失败诊断行 double-check 一致。
- 默认路由选择有数据依据，决策与实现一致。

## 关联

- `specs/007-ace-better-fix/`（本规格的前置：skew 消除 + 路由 + 诊断；`resolve_ace_model` 待升级）
- `specs/006-ace-coordinate-consistency/`（ACE 系可信展示，不改判据）
- `services/localizer/enhanced_ace.py`、`ace_localizer.py`、`coord_regression.py`、`ace_trainer.py`（SceneCoordinateDataset 法线映射 `(n+1)*0.5`）
- 外部模型：DSINE（nianticlabs/DSINE，预训练权重）或 MiDaS（Intel）——权重为运行产物，不入库