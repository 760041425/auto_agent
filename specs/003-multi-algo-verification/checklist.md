# 003 交付检查清单

## Definition of Ready

- [x] 问题代码路径和当前失败门禁已识别
- [x] 受影响上下文与依赖方向已明确
- [x] P0 修复范围、非目标和回滚边界已明确
- [x] 每条验收标准已映射测试场景
- [x] `CL-003-03`、`CL-003-04` 的成功/可靠性语义按本轮修复授权采用
- [ ] TODO：独立真值来源获确认
- [ ] TODO：benchmark 目标设备、样本清单和统计门槛获确认

Phase A 已完成；Phase B 的最终推荐作为 TODO 暂时遗留，待以上开放项解决后继续。

## Definition of Done

- [x] 五算法 API 分派契约全部通过
- [x] 统一结果契约和旧结果兼容验证通过
- [x] `Path`、`best_pose` 等 P0 回归已修复并有测试/静态门禁
- [x] 验证指标名称、来源和限制在代码、API、报告、文档中一致
- [x] 无真值时不输出绝对精度或最终推荐
- [x] 有真值时平移/旋转误差可复现
- [x] 前端能展示低可信、失败原因和各类验证状态
- [x] V2 成功路径基于最终返回位姿生成查询图、最终投影图和双图对比图
- [x] API 统一结果保留三类视觉 artifact 并映射为公开 URL
- [x] 后端重启后，以 task #217 LoFTR 完成浏览器两图加载验收（`TL-003-21`）
- [x] 同源 NPY 不再输出米制验证，task #220 的所有 `*_m` 为 `null`
- [x] 前端明确分离 2D 拟合诊断与独立真值 Benchmark 状态
- [x] 日志初始化幂等且可按任务/算法追踪
- [x] 干净工作区启动测试通过
- [x] 运行产物与权威验收资产分离
- [x] SALAD+RoMa（原版）拒绝零交集旧缓存，在当前地图索引上产生检索候选并真实调用 TinyRoMa
- [x] SALAD+RoMa（原版）生成最终位姿坐标产物，并严格以 `<0.3m` 而非高内点决定可信状态
- [x] 一致性判据比较 H→SLAM XYZ（Z=0）与 NPY XYZ 的三维欧氏距离，不再忽略 Z 分量（AC-003-16）
- [x] 精化步骤复用初始定位匹配器，不再硬编码 LightGlue（AC-003-17）
- [x] `./scripts/validate-specs.sh` 通过
- [x] `./scripts/run-all-tests.sh fast` 通过
- [x] 变更范围 Ruff 检查通过
- [ ] TODO：补齐空 `las/points3D.txt` 后通过指定 COLMAP points 集成测试
- [x] `./scripts/drift-check.sh` 通过（0 error，6 条历史产物 warning）
- [ ] TODO：真实 benchmark 报告经过人工评审
