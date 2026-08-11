# 008 测试清单

| 状态 | TL-ID | 映射 AC | 层级 | 场景与期望 |
| --- | --- | --- | --- | --- |
| [ ] | **TL-008-01** | AC-008-01 | 单元/接口 | `estimate_normal(image)` 返回 (H,W,3) float32 且值域 [0,1]（注入假 DSINE 输出验证映射 `(n+1)*0.5`） |
| [ ] | **TL-008-02** | AC-008-01 | 单元/降级 | 法线模型权重缺失/加载抛错 → `estimate_normal` 回退常量 0.5、`normal_source=="constant_fallback"` 不崩溃 |
| [ ] | **TL-008-03** | AC-008-02 | 单元/注入 | `ace_better`/`ace_normal` 6ch 路径 `normal_mode="dsine"`（mock 法线估计器）→ predict_dense 收到真法线（非常量/梯度）；`normal_mode="constant"` 行为与 007 一致 |
| [ ] | **TL-008-04** | AC-008-03 | 集成/基准 | 基准运行器对抽样查询集（mock 法线 or 真实权重，按 RISK-008-01 可降级）输出四路径对比表（success 率 / LAS 验证率 / mean_distance / reproj / inliers）落 reports/ |
| [ ] | **TL-008-05** | AC-008-04 | 集成/路由 | 若基准数据触发切换条件（胜出 ≥30% 或 ≤0.5m）→ `resolve_ace_model` 默认优先 6ch 真法线且 TL-007-01/02 同步更新；未触发 → 维持现状（测试断言既有默认路径不变） |
| [ ] | **TL-008-06** | AC-008-05/06 | 回归+门禁 | 007 默认路由行为回归绿（scene 3ch 存在优先）；validate-specs / run-all fast / drift-check / 全量 pytest 全绿 |

## TDD 顺序（单流水线，禁止一次写一堆）

### 批次 P0：法线估计模块
1. TL-008-01 → 红 → 绿（normal_estimator.estimate_normal 接口+映射）
2. TL-008-02 → 红 → 绿（降级回退）

### 批次 P1：接入 runner
3. TL-008-03 → 红 → 绿（normal_mode 参数 + 6ch 真法线输入）

### 批次 P2：抽样试跑 + 选型
4. 抽样试跑 DSINE vs MiDaS（5 张图，质量+耗时）→ 记录决策 D-008-01 终选

### 批次 P3：基准 + 路由决策
5. TL-008-04 → 基准运行器 + 四路径对比报告
6. TL-008-05 → 依据数据更新路由（切/不切）与单测
7. TL-008-06 → 回归 + 门禁全绿

> 注意：所有单元测试 mock 法线估计器；禁止真实训练；真实权重试跑为抽样（≤10 张），基准 ≥20 张可部分 mock。