# 009 实施任务

| 状态 | TASK-ID | 依赖 | 任务 | 完成证据 |
| --- | --- | --- | --- | --- |
| [x] | TASK-009-01 | 无 | 新建 `specs/009-feature-matching-accel/` 八件套 | 文件存在，validate-specs 通过 |
| [ ] | TASK-009-02 | TASK-009-01 | FAISS 检索后端（try-import，无则回退 numpy） | 单测：FAISS 与 numpy 结果等价 |
| [ ] | TASK-009-03 | TASK-009-01 | MPS 加速（torch.compile + FP16，失败回退 eager） | 单测：compile 成功 / 回退成功 |
| [ ] | TASK-009-04 | TASK-009-02, TASK-009-03 | fast_mode 参数 + 新算法注册（fast_loftr / fast_hybrid） | registry 新 ids 可用 |
| [ ] | TASK-009-05 | TASK-009-01 | XFeat 匹配器 + 注册（try-import，无则不注册） | 单测：XFeat 输出格式正确；未安装时不注册 |
| [ ] | TASK-009-06 | TASK-009-01 | 批量匹配 + 异步流水线 | 单测：batch 结果等价于串行 |
| [ ] | TASK-009-07 | TASK-009-04, TASK-009-05, TASK-009-06 | 前端新增选项（原 key 不变） | Playwright/DOM 验证 |
| [ ] | TASK-009-08 | TASK-009-07 | 基准对比（原 vs 新，≥5 张图）+ 门禁全绿 | benchmark_009.json + 三脚本绿 |
| [x] | TASK-009-09 | TASK-009-02 | macOS PyTorch+FAISS 双 OpenMP abort：运行时禁用并回退 numpy | TL-009-10 Red→Green；近邻 11 passed, 1 skipped |
