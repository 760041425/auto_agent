# 特性规格包

每个 `specs/<feature-id>/` 表示一次可追踪变更。目录名使用 `<三位序号>-<英文短名>`，必须包含以下文件：

| 文件 | 回答的问题 |
| --- | --- |
| `spec.md` | 做什么、为什么做、怎样验收 |
| `clarify.md` | 哪些歧义已澄清、哪些仍开放 |
| `plan.md` | 在哪些上下文和模块中怎样落地 |
| `tasks.md` | 按什么顺序执行，完成证据是什么 |
| `checklist.md` | Ready/Done 门禁是否满足 |
| `testlist.md` | 哪些行为要按 Red → Green → Refactor 验证 |
| `risks.md` | 风险、触发信号、缓解和回滚 |
| `decisions.md` | 特性级决策及其后果 |

## 当前规格

| ID | 名称 | 状态 | 入口 |
| --- | --- | --- | --- |
| 001 | LAS 影像 3D 查询 | 基线补录，待补自动化验收 | [规格](001-las-image-3d-query/spec.md) |
| 002 | 移动场景实时定位优化 | 迭代中，性能目标待基准证明 | [规格](002-realtime-localization-optimization/spec.md) |
| 003 | 多方案定位与可信验证修复 | Phase A 已实施并通过快速门禁；Phase B（独立真值）遗留 TODO | [规格](003-multi-algo-verification/spec.md) |
| 004 | 平面感知单应投影 | 已实施（2026-08-04） | [规格](004-plane-aware-homography/spec.md) |
| 005 | 坐标变换修复 | 已实施（006 交付时对齐守卫） | [规格](005-coordinate-transform-fix/spec.md) |
| 006 | ACE 坐标差最终判定链路对齐 | 已实施（降级「无法判定」独立状态） | [规格](006-ace-coordinate-consistency/spec.md) |
| 007 | ACE 系法线 train/serve skew 修复 + PnP 失败诊断 | 已实施（法线对齐 + 3ch 模型路由 + diagnostics） | [规格](007-ace-better-fix/spec.md) |
| 008 | ACE 推理期真法线估计 + 6ch 精度基准（D 治本） | 实施中（P0+P1 法线模块与 normal_mode 接入已交付，P2/P3 待办） | [规格](008-ace-true-normal/spec.md) |
| 009 | 特征匹配加速 + 多方案对比 | 已实施；FAISS macOS/PyTorch 运行时安全回退已通过门禁 | [规格](009-feature-matching-accel/spec.md) |
| 010 | 空间感特征轻量验证 + 速度—准确率双轨决策 | 实验已完成（P0/P1/P1b/P1c/P1d/P3/P4/P4b/P4c） | [规格](010-spatial-feature-validation/spec.md) |

## 质量命令

```bash
./scripts/validate-specs.sh
./scripts/traceability-report.sh
./scripts/run-all-tests.sh fast
./scripts/drift-check.sh
```
