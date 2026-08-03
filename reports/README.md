# 报告

本目录保存可再生成的工程证据：

- `traceability/`：规格、任务、测试和风险的追踪报告（由 `scripts/traceability-report.sh` 生成）；
- `generated/`：每次 benchmark / verify 运行的 HTML/JSON 产物（已加入 `.gitignore`，不进入版本控制）；
- `verification/`、`quality/`、`architecture/`：预留目录，当前尚未启用；需要时创建并在此补充用途说明。

报告必须写明生成命令、输入版本和时间上下文；不得把一份过期报告当作当前实现证据。

仓库根目录中现有的 `benchmark_*`、`verify_*` HTML/JSON 是修复前的历史实验，
其误差来自内部一致性或地图派生数据，不是独立位姿真值，也不构成算法推荐。
新运行产物统一写入被忽略的 `reports/generated/`，权威结论只能在验收报告中引用
明确的 `run_id`、配置哈希和独立真值来源。
