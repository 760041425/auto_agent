# BUG-003-02 — 同源 NPY 自比较被展示为 0.000 m 验证

## 1. 期望与实际

- 期望：单次定位只展示可解释的 2D 几何拟合诊断；只有加载独立 holdout 位姿真值的 Benchmark 才展示米制平移误差和旋转误差。
- 实际：task #217 把同一批匹配点拟合出的单应矩阵再次作用于同一批内点，并在同一张 NPY 坐标图中比较，页面显示中位/最大一致性差均为 `0.000 m`。

> 2026-08-01 纠偏：上述结论只适用于 runner 内部的同批匹配自比较，不应删除
> 查询图人工选点链路。该能力已从参考工程迁入当前空间定位上下文，由任务自产
> H 和最终位姿 XYZ NPY；仍非独立位姿真值，但必须保留并显示两套坐标及差值。
> 迁移修复见 `BUG-003-03`。

## 2. 复现矩阵

| 环境/版本 | 输入与路径 | 预期 | 实际 | 证据 |
| --- | --- | --- | --- | --- |
| 本地 Web，task #217 | image_id=21，LoFTR | 拟合诊断与 Benchmark 状态分开 | `5/12` 后显示两个 `0.000 m` | 浏览器结果卡 |
| task #217 API | `validations.projection_consistency` | 同源米制指标不可用 | `status=available`、`median_m=max_m=0.0` | SQLite/API JSON |
| `verify_projection_local` | 同一匹配集 + 同一 NPY | 仅像素诊断 | 在单应内点上读取直接/预测 NPY 并计算米制差 | 代码路径 |

## 3. 根因分析

### 3.1 5 Why

1. 页面为什么显示 `0.000 m`？后端把 `projection_consistency.status` 标成 `available`，前端直接格式化米制字段。
2. 为什么数值必然接近零？单应矩阵由当前匹配点拟合，随后又只采样同一批单应内点，预测像素天然贴近直接匹配像素。
3. 为什么会被解释为 3D 米制差？两个像素都从同一张 tile NPY 取坐标，没有第二个独立 3D reference。
4. 为什么测试未发现？旧测试只故意扰动一个匹配并断言最大差增加，没有断言数据来源独立，也没有禁止同源米制字段。
5. 为什么 Benchmark 没参与？单次定位 API 只运行 runner 的即时诊断；Benchmark 是离线数据集编排器，只有 `--ground-truth` 提供独立位姿真值时才计算平移/旋转误差，而 Phase B 真值集仍为 TODO。

### 3.2 为什么未被测试/监控发现

- `TL-003-09` 把“两个计算路径”误当成“两个独立来源”，测试口径不足。
- `spec.md` 和 `clarify.md` 误写为自比较已修复，但实际只改了指标名称和免责声明。
- `generate_verify_report.py`、`verify_localization.py` 复制了相同算法，形成三处扩散。

## 4. 影响面

- 所有 V2 成功定位结果的 `projection_consistency` 展示。
- 页面“生成验证报告”产生的 HTML。
- `scripts/verify_localization.py` 的 JSON/终端输出。
- 不影响位姿求解、artifact 生成和带独立真值的 Benchmark 平移/旋转误差。

## 5. 修复方案

1. `verify_projection_local` 仅返回单应内点数、比例和像素残差；同源米制字段固定为 `None/not_available`。
2. 前端分开显示“2D 几何拟合诊断（非 Benchmark）”和“独立真值 Benchmark”；无真值时明确标记未执行。
3. 两条离线旧报告路径删除 NPY 米制自比较，统一输出像素残差。
4. 保留 `benchmark_localizers.py --ground-truth` 作为唯一独立位姿误差入口；不伪造或推断真值。

## 6. 扩散覆盖矩阵

| 同模式位置 | 是否受影响 | 处理 | 测试 |
| --- | --- | --- | --- |
| V2 即时验证 | 是 | 米制状态改为不可用，保留像素拟合 | TL-003-22 |
| 最新结果/轮询 UI | 是 | 拟合与 Benchmark 分栏 | TL-003-23 |
| HTML 验证报告 | 是 | 改为像素残差 | TL-003-24 |
| 旧 CLI 验证脚本 | 是 | 改为像素残差 | TL-003-24 |
| 带 ground truth 的 Benchmark | 否 | 保持平移/旋转误差入口 | TL-003-10、TL-003-18 |

## 7. 回归测试

- 同源 NPY 输入必须返回 `status=not_available`、`median_m/max_m=None`。
- 扰动匹配只影响 `homography_fit.*_residual_px`，不得重新产生米制字段。
- 前端源码必须明确区分非 Benchmark 拟合诊断与独立真值 Benchmark。
- 两条报告脚本不得包含旧 `median_error_m`/`xyz_npy_via_H` 字段。

## 8. 风险与回滚

- 风险：历史 task 仍保存旧 `0.000 m`；前端必须按新语义抑制其米制展示。
- 回滚：可恢复像素拟合展示，但不得恢复同源米制指标；无数据库迁移。

## 9. Before/After

- Before：task #217 显示单应内点 `5/12`、中位/最大一致性差 `0.000 m`。
- After（task #220 API）：`projection_consistency.status=not_available`，原因是 `same_source_npy_is_not_independent_validation`；`mean/median/max_m` 及兼容米制字段均为 `null`，只保留 `homography_fit` 像素诊断。
- After（task #220 浏览器）：结果卡显示“2D 几何拟合诊断（非 Benchmark）”与单应内点 `5/12`；独立真值 Benchmark 明确显示“未执行 / Phase B TODO”；不存在“中位一致性差”“最大一致性差”或 `0.000 m`，视觉产物仍全部加载成功。

## 10. Changelog

- 2026-08-01：确认文档/实现漂移，完成 5 Why；四条回归先 Red 后 Green，进入环境验证。
- 2026-08-01：task #220 完成 API 与浏览器 Before/After，`TL-003-22` 至 `TL-003-24`、`TASK-003-14` 完成；真实 Benchmark 仍等待 Phase B 独立真值。
