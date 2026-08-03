# Changelog — Spec 003 多方案定位验证（2026-08-01）

## 代码 → 文档 对应关系

| 代码变更 | 文档更新 |
|---------|---------|
| `services/localizer/logger_config.py`（新建）| `docs/tech.md` 日志架构 |
| `services/localizer/pose_utils.py`（新建）| `docs/structure.md` 模块清单 |
| `services/localizer/coord_regression.py`（新建）| `docs/structure.md` 模块清单 |
| `services/localizer/verify_projection.py`（新建）| `docs/structure.md` 模块清单 |
| `services/localizer/salad_roma_v2.py`（新建）| `specs/003/spec.md` 方案矩阵 |
| `services/matcher/__init__.py`（改日志）| `docs/structure.md` 模块清单 |
| `services/localizer/__init__.py`（改日志）| `docs/structure.md` 模块清单 |
| `services/localizer/salad_roma.py`（改日志）| `docs/structure.md` 模块清单 |
| `api/main.py`（加 HTTP 日志中间件）| `docs/tech.md` 日志架构 |
| `scripts/benchmark_localizers.py`（新建）| `README.md` 多方案验证 |
| `scripts/generate_verify_report.py`（新建）| `README.md` 多方案验证 |
| `scripts/render_multi_pitch_tiles.py`（新建）| `docs/structure.md` 工具清单 |
| `web/index.html`（算法选项更新）| `specs/003/spec.md` |
| `web/app_v10.js`（算法名匹配）| `specs/003/spec.md` |
| `services/localizer/salad_roma_v2.py`（BUG-003-01 最终视觉产物）| `specs/003-multi-algo-verification/bugfix-missing-v2-artifacts.md` |
| `services/localizer/contracts.py`、`api/routes/localize.py`（三类 artifact 契约/URL）| `specs/003-multi-algo-verification/spec.md` 的 `AC-003-11` |
| `web/app_v10.js`（查询图、最终投影与缺失诊断）| `specs/003-multi-algo-verification/testlist.md` 的 `TL-003-21` |
| `verify_projection.py`、两条验证报告脚本（BUG-003-02）| `specs/003-multi-algo-verification/bugfix-circular-projection-validation.md` |
| `services/localizer/salad_roma.py`（原版描述子缓存 key 校验 + TinyRoMa 分派，BUG-003-03）| `specs/003-multi-algo-verification/bugfix-stale-original-salad-index.md` |
| `services/localizer/verify_projection.py`（查询图选点 + 坐标转换迁入，BUG-003-03）| `specs/003-multi-algo-verification/bugfix-local-coordinate-transform.md` |
| `services/localizer/verify_projection.py`（一致性判据改为三维欧氏距离，BUG-003-04）| `specs/003-multi-algo-verification/bugfix-z-dimension-consistency.md` |
| `services/localizer/salad_roma.py`（`refine_pose_with_roma` 增加 `matcher_type` 分派，BUG-003-05）| `specs/003-multi-algo-verification/bugfix-refine-matcher-inconsistency.md` |

## 新增模块

| 文件 | 行数 | 职责 |
|------|------|------|
| `services/localizer/logger_config.py` | ~80 | 日志配置（HTTP API / 业务分离）|
| `services/localizer/pose_utils.py` | ~260 | 稳定四元数、PnP+refine、E-matrix、LAS 验证 |
| `services/localizer/coord_regression.py` | ~120 | ACE 网络 + 自动加载器（支持 3ch/6ch）|
| `services/localizer/verify_projection.py` | ~180 | 投影内部一致性（非绝对精度）|
| `services/localizer/registry.py` | - | API 与 benchmark 共用的算法注册表 |
| `services/localizer/contracts.py` | - | 稳定结果契约和旧字段适配 |
| `services/localizer/evaluation.py` | - | 独立真值位姿误差 |
| `services/localizer/salad_roma_v2.py` | ~900 | v2 引擎（5 种匹配器）|
| `scripts/benchmark_localizers.py` | ~360 | 统一评估框架 |
| `scripts/generate_verify_report.py` | ~280 | HTML 验证报告 |
| `scripts/render_multi_pitch_tiles.py` | ~70 | 多 pitch 隔离实验渲染（不得写生产索引）|

## 新增文档

| 文件 | 职责 |
|------|------|
| `specs/003-multi-algo-verification/` | 八件套规格包 |
| `docs/CHANGELOG-003.md` | 本文档 |

## 更新文档

| 文件 | 更新内容 |
|------|---------|
| `docs/engineering-playbook.md` | 日志架构章节 + 后续优化 |
| `docs/tech.md` | 日志架构表格 |
| `docs/structure.md` | 模块清单 + 工具清单 |
| `docs/context-map.md` | 多方案架构 + 验证方式 + 日志分离 |
| `README.md` | 多方案验证命令 + 验证指标 + 日志架构 |

## 验证门禁

```bash
# 单元测试
./scripts/run-all-tests.sh fast

# 全量 benchmark（无独立真值时不产生算法推荐）
python scripts/benchmark_localizers.py --queries "query_images/*.jpg" --algos all
```

修复前生成的 `reports/benchmark_*final*` 等文件只保留为历史实验，不是当前验收证据。

## BUG-003-01：V2 成功但前端无视觉图像

- 根因：V2 runner 仅生成迭代内部临时投影，最终成功分支没有生成或返回最终位姿 artifact；既有测试只验证人工字段适配和 spy 分派，没有执行真实纵向路径。
- 修复：最终位姿确定后生成查询图、最终位姿投影和双图对比；API 统一映射公开 URL；前端两个结果入口共用展示逻辑，缺失时明确提示。
- 已验证：同 task #212 输入真实 LoFTR runner 得到原 22 内点和相同位姿，同时生成三张非空 PNG；快速测试 49 项、规格、Ruff、JS 和漂移门禁通过。
- 已完成环境验证：停止修复前旧 PID 后加载当前源码，以 task #217 新建 LoFTR 任务；API 返回三类 artifact URL，浏览器加载查询图、最终投影图和对比图成功。历史任务没有最终位姿产物，仍不能用迭代临时图回填。

## BUG-003-02：0.000 m 同源自比较

- 根因：同一批匹配点拟合 homography 后又在同一批内点、同一张 NPY 上取两次坐标，循环验证被展示为米制结果；离线 Benchmark 并未参与单次定位。
- 修复：即时结果与两条旧报告路径只保留 homography 像素拟合诊断；前端单独显示独立真值 Benchmark 状态，无 holdout ground truth 时明确标记未执行。
- Phase B 状态不变：真实数据集、独立真值、设备和样本门槛仍为 TODO，不伪造精度或算法推荐。
- 环境验证：task #220 的同源米制字段全部为 `null`；浏览器只显示像素拟合诊断，并明确标记独立真值 Benchmark 未执行。
