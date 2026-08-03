# 003 实施任务

Phase A 已实施；Phase B 作为 **TODO（暂时遗留）**，等待独立真值、目标设备和样本门槛。完成状态仅按已有自动化证据填写。

| 状态 | TASK-ID | 依赖 | 任务 | 完成证据 |
| --- | --- | --- | --- | --- |
| [x] | TASK-003-01 | 无 | 冻结现有报告为历史实验，修正 Spec 003 的完成状态和未经证明的推荐 | `reports/README.md`、README/工程手册已标注指标限制 |
| [x] | TASK-003-02 | TASK-003-01 | 先写五算法 API 分派失败测试和未知 ID 测试 | `TL-003-01`、`TL-003-02` 已经历 Red → Green |
| [x] | TASK-003-03 | TASK-003-02 | 实现集中算法注册表，API 与 benchmark 共用 runner | 五个 spy runner 与 benchmark 同源测试通过 |
| [x] | TASK-003-04 | TASK-003-02 | 定义统一定位结果契约及旧结果兼容适配器 | 成功、低可信、异常与兼容字段测试通过 |
| [x] | TASK-003-05 | TASK-003-03 | 修复 matcher 的 `Path`、V2 最佳候选保存、必要运行目录和重复 logger handler | 回归测试通过，变更范围 Ruff 无 F821 |
| [x] | TASK-003-06 | TASK-003-04 | 让 API 保留 validation、quality、artifact、timing 和 error 字段，更新前端展示 | 契约测试和前端静态验收通过 |
| [x] | TASK-003-07 | TASK-003-04 | 重构验证指标，删除同一 NPY 同像素自比较；无真值返回 `not_available` | `TL-003-08` 至 `TL-003-10` 通过 |
| [ ] TODO | TASK-003-08 | TASK-003-07 | 确认真值来源并完成 benchmark manifest 评审 | 解决 `CL-003-05` 至 `CL-003-07`，manifest 经评审 |
| [ ] TODO | TASK-003-09 | TASK-003-08 | 在批准数据上生成可复现 benchmark run 与报告 | 同配置复跑结果结构一致，报告有唯一 run_id |
| [x] | TASK-003-10 | TASK-003-05 | 合并 HTTP/业务日志配置并添加上下文字段 | handler 幂等测试通过；日志包含任务/算法字段 |
| [x] | TASK-003-11 | TASK-003-05 | 验证干净克隆启动并整理报告/日志/投影产物策略 | 临时目录测试和 API health 系统测试通过；生成报告已忽略 |
| [ ] TODO | TASK-003-12 | TASK-003-06,TASK-003-09,TASK-003-10,TASK-003-11 | 补齐 COLMAP points 集成和真实 benchmark 门禁，更新验收报告 | 命令输出和验收报告，不以口头结论替代 |
| [x] | TASK-003-13 | TASK-003-06 | 修复 BUG-003-01，恢复 V2 查询图、最终投影图和对比图 artifact | `TL-003-19` 至 `TL-003-21` 通过；task #217 API、文件和浏览器 Before/After 完整通过 |
| [x] | TASK-003-14 | TASK-003-07 | 修复 BUG-003-02，移除同源 NPY 米制自比较并区分即时拟合与 Benchmark | `TL-003-22` 至 `TL-003-24` 通过；task #220 API/浏览器 Before/After 通过 |
| [x] | TASK-003-15 | TASK-003-14 | 修复 BUG-003-03，将查询图选点与坐标转换能力完整迁入空间定位上下文 | `TL-003-25` 至 `TL-003-27` 经 Red → Green；task #223 生成 H(20/22) 与 512² XYZ NPY，本地 API/浏览器通过 |
| [x] | TASK-003-16 | TASK-003-15 | 将多点 H/NPY 中位坐标差升级为 V2 最终可信判据，内点/相似度降为辅助诊断 | `TL-003-28` 至 `TL-003-30` 经 Red → Green；最终门槛按用户确认改为严格 `<0.3m`，等于门槛也不准 |
| [x] | TASK-003-17 | TASK-003-05 | 修复 BUG-003-04：原版 SALAD 拒绝零交集旧缓存、真实运行 TinyRoMa，并接入 0.3m 最终判据 | `TL-003-31` 至 `TL-003-33` 经 Red → Green；同一查询恢复 3 候选，最终 640 内点但坐标差 7.563m，正确返回 `reliable=false` |
| [x] | TASK-003-18 | TASK-003-07 | 修复 BUG-003-04（Z 维度）：一致性判据比较 H→SLAM XYZ（Z=0）与 NPY XYZ 的三维欧氏距离 | `TL-003-28` 更新 + 新增 Z 维度测试；task #249 单点查询坐标差 0.968m（含 Z），多点 median=5.484m 正确判 `reliable=false` |
| [x] | TASK-003-19 | TASK-003-07, TASK-003-14 | 修复 BUG-003-05（精化匹配器）：`refine_pose_with_roma()` 增加 `matcher_type` 参数，API 端点从 `match_method` 推导 | `TL-003-34`（3 项单元：分派/回退/拒绝）+ `TL-003-35`（5 项 API 契约）经 Red → Green；task #249 精化从 1 对失败 → 273 对成功 |

## 推荐执行批次

### 批次 P0：阻断错误行为

`TASK-003-01` → `TASK-003-02` → `TASK-003-03` → `TASK-003-04` → `TASK-003-05` → `TASK-003-06`

### 批次 P1：建立可信测量

`TASK-003-07` → `TASK-003-08` → `TASK-003-09`

### 批次 P2：交付治理

`TASK-003-10` → `TASK-003-11` → `TASK-003-12`
