# 008 决策记录

| D-ID | 决策 | 理由 | 替代方案 | 状态 |
| --- | --- | --- | --- | --- |
| D-008-01 | 法线估计选型：**定案 MiDaS**（DSINE 备选，当前不可达） | P2 抽样试跑（2 张真实查询图 1920×1080 + 3 张 tile 渲染图 512×512）实证：MiDaS_small 端到端可达，零新依赖（torch/torchvision/kornia 已有），单图 ~0.6s（CPU）/ 更快（MPS），法线 |n|≈1.0 归一化正确、[0,1] mean 0.45-0.49 对齐训练真法线 0.5；DSINE (baegwangbin/DSINE, CVPR2024) 权重 dsine_eval.zip 达 21GB，托管 Google Drive 需浏览器式病毒扫描确认流程，且非 pip 包（无 setup.py），本环境不可达 | 只选一个不试跑 / 坚持 DSINE 优先 → 21GB+确认墙实际阻塞 | 定案 MiDaS (P2) |
| D-008-02 | 6ch 真法线路径以 `normal_mode` 参数接入（`"dsine"`/`"constant"`），默认仍 `"constant"` 直到基准报告出 | 避免未验证模型直接改默认引入回归；007 的常量占位与 3ch 路由是已验证基线 | 直接切默认 → 违反「以数据决策」 | 采用 |
| D-008-03 | 基准以**数据决策路由**：真法线显著胜出（mean_distance 相对最优提升 ≥30% 或 ≤0.5m）才切换 `resolve_ace_model` 默认；否则维持 007 现状并文档记录 | 路由升级必须可证伪；避免「换了个寂寞」 | 无基准直接切 → 不可验证 | **维持 007 现状（P3 数据 22 张：6ch_midas 1.085m 差于 6ch_constant 0.886m，恶化 23%，不触发切换；详见 reports/benchmark_008.json）** |
| D-008-04 | 基准查询集含真实查询图 + tile 渲染图（有 camera_pose 真值），指标含成功率/LAS 验证率/mean_distance/reproj/inliers | tile 有真值可算绝对误差，查询图算 LAS 相对验证；覆盖「用户真实输入」与「可控真值」两类 | 只用查询图（无真值）→ 无法算绝对精度 | 采用 |
| D-008-05 | 模型权重为运行产物不入库；基准报告落 `reports/`（不入库） | 与 AGENTS.md 运行产物约定一致；基准结论摘要同步进 specs/008 decisions | 权重大文件入库 → 违反仓库约定 | 采用 |