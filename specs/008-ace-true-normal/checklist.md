# 008 完成清单

- [x] 法线估计模块：`estimate_normal(image)` → [0,1]（AC-008-01）
- [x] 降级：权重缺失回退常量 0.5 + `normal_source` 标注（AC-008-01）
- [x] `normal_mode` 参数接入 `ace_better`/`ace_normal` 6ch 路径（AC-008-02）
- [x] 抽样试跑 DSINE vs MiDaS 并记录选型决策（D-008-01 终选 → MiDaS，DSINE 21GB+GDrive 不可达）
- [x] 基准运行器：≥20 张、四路径对比、落 reports/（AC-008-03）
- [x] 路由决策：显式「切/不切」，数据与实现一致（AC-008-04）— 维持 007 现状（P3 数据）
- [x] 007 默认路由回归绿 + 新增测试全绿（AC-008-05）
- [x] 门禁 validate-specs / run-all fast / drift-check / 全量 pytest 全绿（AC-008-06）
- [ ] docs/contexts 术语同步（如法线估计条目）
- [ ] 人工复核基准报告与前端诊断行一致（「空间感」可量化）