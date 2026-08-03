# BUG-003-01 — V2 定位成功但前端缺少查询图与最终投影图

## 1. 期望与实际

- 期望：`salad_roma_v2`、`salad_roma_v2_loftr`、`hybrid` 成功后生成查询图、最终位姿投影图和双图对比图；API 通过 `artifacts` 返回稳定 URL，前端可见。
- 实际：task #212 的 LoFTR 定位成功并返回 22 个内点，但结果 `artifacts={}`，`comparison_image`、`reprojection_image` 均为 `null`，结果卡片内没有任何 `<img>`。

## 2. 复现矩阵

| 环境/版本 | 输入与路径 | 预期 | 实际 | 证据 |
| --- | --- | --- | --- | --- |
| 本地 Web，2026-08-01，task #212 | image_id=21，`salad_roma_v2_loftr` | 查询图 + 最终投影图 | 只有文本结果 | 浏览器 DOM：结果卡 `imageCount=0` |
| SQLite task #212 | `TaskModel.result_json` | 三类 artifact URL | `artifacts={}`，两个兼容字段为 null | `query_images/app.db` 只读查询 |
| 文件系统 task #212 | `projections/localize/task_212/` | 稳定最终产物 | 仅 `_iter_v2_1.png` | 文件清单与 PNG 元数据 |

## 3. 根因分析

### 3.1 5 Why

1. 为什么前端没有图片？结果卡只有 `comparison_image` 存在时才创建图片元素，而 task #212 该字段为空。
2. 为什么 API 字段为空？统一契约只能保留 runner 已返回的 artifact，V2 runner 没有返回任何图像路径。
3. 为什么 V2 runner 没返回？它只在迭代开始时生成 `_iter_v2_<n>.png` 供再次匹配，最终成功分支未生成最终位姿投影、查询图或对比图。
4. 为什么迭代图不能直接当最终图？该图使用当轮优化前的位姿；后续 PnP 可能更新位姿，且文件名和返回契约都把它定义为内部临时产物。
5. 为什么此前没有发现？契约测试传入了人工构造的 `comparison_image`，分派测试使用 spy runner；前端测试只检查低可信和指标文案。没有任何测试执行“V2 成功 → 生成 artifact → API URL → DOM 图片”的纵向功能路径。

### 3.2 为什么未被测试/监控发现

- `TL-003-03` 证明的是适配器“不丢已有字段”，没有证明 runner 会生产字段。
- `TL-003-13` 只覆盖状态、错误和验证文案，未把图像 artifact 纳入 UI 验收。
- `TL-003-07` 一直未完成，却在 Phase A 总结中把 V2 产物路径视作已验收，形成规格状态漂移。
- API health 和快速模型 mock 不执行真实渲染路径。

## 4. 影响面

- 直接影响三个共用 V2 runner 的公开算法：`salad_roma_v2`、`salad_roma_v2_loftr`、`hybrid`。
- ACE/Multi-Strategy 当前也不保证视觉 artifact，但本 Bug 先恢复有点云最终位姿渲染能力的 V2 三路径；其他算法能力必须显式标为 unavailable，不伪造图片。
- 历史已完成任务不会自动回填产物，需要重新定位或另行执行离线回填。

## 5. 修复方案

1. V2 最终位姿确定后，固定生成 `query_<tag>.png`、`reprojection_<tag>.png`、`comparison_<tag>.png`。
2. 返回 `artifacts.query_image/comparison_image/reprojection_image` 及一个发布周期兼容字段。
3. API 将三类投影目录产物转换为 `/projections/...` URL。
4. 前端优先显示查询图与最终投影图，并保留双图对比入口；字段缺失时明确显示“未生成”，不静默消失。

## 6. 扩散覆盖矩阵

| 同模式位置 | 是否受影响 | 处理 | 测试 |
| --- | --- | --- | --- |
| V2 DISK+LG | 是 | 共用最终 artifact 生成器 | 参数化 runner/产物契约 |
| V2 LoFTR | 是 | 共用最终 artifact 生成器 | 参数化 runner/产物契约 |
| V2 Hybrid | 是 | 共用最终 artifact 生成器 | 参数化 runner/产物契约 |
| API 兼容适配 | 是 | 增加 query image 映射 | 契约测试 |
| 最新结果/轮询前端 | 是 | 共用 artifact 渲染函数 | JS 静态 + 浏览器 DOM |

## 7. 回归测试

- `TL-003-19`：成功 V2 产物生成器写出查询图、最终投影图、双图对比图。
- `TL-003-20`：统一契约和 API URL 适配保留三类 artifact。
- `TL-003-21`：前端两个结果入口都展示查询图/最终投影，缺失时显示诊断文案。
- L3：以与 task #212 相同的 LoFTR 用户路径重跑，结果卡至少包含两个可加载图片元素。

## 8. 风险与回滚

- 风险：最终渲染增加一次点云渲染耗时和磁盘占用；失败必须保留定位结果并返回 artifact 生成错误状态。
- 回滚：移除最终 artifact 调用即可恢复原定位路径；不涉及数据库 schema。

## 9. Before/After

- Before：task #212 定位成功，DOM `imageCount=0`，目录仅 `_iter_v2_1.png`。
- After（真实 runner）：使用 task #212 的同一查询图、LoFTR 参数直接复跑，仍得到 22 内点及位姿
  `[30.45639262887518, 4.077796588540384, 7.810239189013678]`，同时生成非空的
  `query_salad_v2_loftr.png`、`reprojection_salad_v2_loftr.png`、
  `comparison_salad_v2_loftr.png`，`artifact_generation.status=available`。
- After（历史结果降级）：浏览器读取 task #212 时不再静默留白，显示“视觉产物未生成；历史结果需重新定位后生成”。
- 对抗式复核：task #213/#214 实际运行的是默认勾选的 `salad_roma_v2`
  （DISK+LightGlue），不是 task #212 的 `salad_roma_v2_loftr`。三个候选分别只有
  8/15/10 对 3D-2D 匹配，4px RANSAC 未形成 PnP 一致解，因此返回“所有候选 PnP
  失败”且没有最终位姿投影符合契约；该结果不能作为 LoFTR artifact 修复的 After 证据。
- After（API/浏览器）：确认 task #215/#216 仍由 21:33 启动、早于源码修改的旧 PID 41944 执行；停止旧进程并加载当前源码后创建 task #217。API 返回 `artifact_generation.status=available` 和三类 `/projections/...` URL；浏览器结果卡显示“查询图像”“最终位姿投影”，三张 PNG 均 `complete=true`，尺寸为 512×512、512×512、1024×540，且无 artifact 缺失提示。
- 历史 task #212、#215、#216 只有优化前临时图，不能伪装为最终位姿投影或自动回填。

## 10. Changelog

- 2026-08-01：确认复现，完成 RCA，进入回归 TDD。
- 2026-08-01：完成 Red → Green；真实 runner 复跑生成三类 artifact，定向测试 4 项通过，快速测试 49 项通过，Ruff/JS/规格/漂移门禁通过。
- 2026-08-01：浏览器确认历史结果缺失提示生效；新任务图片加载验收等待后端重启，`TL-003-21` 保持未完成。
- 2026-08-01：复核 task #213/#214，确认两次均为 DISK+LightGlue 正常无解，未通过放宽阈值或更改默认算法掩盖失败；LoFTR L3 仍待执行。
- 2026-08-01：确认 task #215/#216 由修复前旧 PID 执行；重启加载当前源码后以 task #217 完成真实 API、文件和浏览器 L3 验收，`TL-003-21` 与 `TASK-003-13` 完成。
