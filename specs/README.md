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
| 003 | 多方案定位与可信验证修复 | 草案，Phase A 可实施，精度结论待真值 | [规格](003-multi-algo-verification/spec.md) |

## 质量命令

```bash
./scripts/validate-specs.sh
./scripts/traceability-report.sh
./scripts/run-all-tests.sh fast
./scripts/drift-check.sh
```
