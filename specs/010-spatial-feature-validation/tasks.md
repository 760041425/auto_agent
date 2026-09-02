# 010 任务

| 状态 | TASK-ID | 依赖 | 任务 | 完成证据 |
| --- | --- | --- | --- | --- |
| [x] | TASK-010-01 | 无 | 记录近年论文、官方代码路径与项目适配结论 | `research/2026-08-31-spatial-features-lightweight-validation.md` |
| [x] | TASK-010-02 | TASK-010-01 | 建立 010 八件套并登记索引 | `validate-specs.sh` |
| [x] | TASK-010-03 | TASK-010-02 | P0：2 查询 + 3 tile MiDaS 实跑 | 5/5 真实输出；研究 §3.2 |
| [x] | TASK-010-04 | TASK-010-03 | P1：LAS 法线旋转到相机系并算角残差 | 研究 §3.3 |
| [x] | TASK-010-05 | TASK-010-02 | P3：hloc/ACE0/SCR Priors 依赖审计 | 研究 §3.4 |
| [x] | TASK-010-06 | TASK-010-03 | P4：真实查询与 self-match 报告拆分 | 研究 §3.1/3.5 |
| [x] | TASK-010-07 | TASK-010-06 | P4：1 张 leave-one-out 位姿契约烟测 | 3.9mm / 0.079°；明确样本限制 |
| [x] | TASK-010-08 | TASK-010-07 | benchmark leave-one-out 真值生成与防同 key 契约（TDD） | TL-010-07 Red→Green；`test_spatial_validation_010.py` |
| [x] | TASK-010-09 | TASK-010-08 | 8 tile + 2 真实查询的速度/准确率 Pareto 基准 | `reports/benchmark_010.json`；8/8 leave-one-out，2 real diagnostic |
| [x] | TASK-010-10 | TASK-010-09 | hloc 轻量官方基线安装与对比 | 约 82MB 下载资产；3 次相同 8+2 报告可复现 |
| [x] | TASK-010-11 | TASK-010-09 | pose-only benchmark 后处理瘦身并同集复跑 | cold -29.4%，warm P50 -72.6%，位姿误差零漂移 |
| [x] | TASK-010-12 | TASK-010-04 | MoGe-2 ViT-S normal 小模型资格门与 2+3 实跑 | `reports/benchmark_010_moge_normals.json`；52.79°，拒绝软评分 |
| [x] | TASK-010-13 | TASK-010-12 | 修复地图法线旋转等变/无效邻域契约并验证 3 tile 候选副本 | TL-010-13 Red→Green；`reports/benchmark_010_map_normals.json` |
| [x] | TASK-010-14 | TASK-010-13 | 8 个不同空间位置的候选覆盖、ACE 输入影响与 MoGe 资格门验证 | 监督像素 16,396 不变；法向覆盖 91.94%→78.21%；MoGe 40.33°；`reports/benchmark_010_map_normals_8.json` |
| [x] | TASK-010-15 | TASK-010-11 | pose-only 跳过 5.2M 稠密点云/KD-Tree并以同一 8+2 验证零漂移和冷启动收益 | cold -70.7%，warm P50 -10.7%，位姿零漂移；`reports/benchmark_010_pose_only_no_dense.json` |
| [x] | TASK-010-16 | TASK-010-15 | pose-only 跳过二次 LightGlue 投影拟合诊断并验证性能与零漂移 | 同种子 8+2 核心差异 0；cold -65.0%，warm P50 -86.0%；`reports/benchmark_010_pose_only_core.json` |
