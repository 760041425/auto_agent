# 003 技术计划

## 推荐方案

采用“集中注册 + 统一结果契约 + 分层验证 + 可复现运行清单”，分两阶段交付。

### Phase A：运行路径可信

1. 在空间定位上下文建立算法注册表，例如 `services/localizer/registry.py`。注册项包含稳定 `algorithm_id`、标签、runner 和能力声明；路由只传入请求并调用注册表。
2. 建立 `services/localizer/contracts.py`，统一 `LocalizationResult`、`PoseQuality`、`ValidationResult` 和 `LocalizationError`。
3. 为旧算法结果增加兼容适配器；API 不再手工挑选并丢弃字段。
4. 修复 `Path`、候选保存、运行目录和日志 handler 等确定性回归。
5. 为分派、结果契约、失败隔离、比较任务和干净启动增加自动化测试。

### Phase B：测量结论可信（TODO，暂时遗留）

1. 将指标分为：
   - `reprojection_error_px`：PnP 几何拟合；
   - `projection_consistency_m`：匹配/单应在地图坐标图上的内部一致性；
   - `las_nearest_distance_m`：预测点是否贴近地图表面；
   - `translation_error_m` / `rotation_error_deg`：仅相对独立真值的绝对精度。
2. 删除自比较逻辑。没有 reference 时返回 `not_available`，不使用零值代替。
3. 建立 `datasets/benchmark/<dataset-id>/manifest.json` 或等价清单，只记录可复现路径、真值来源和分层标签，不提交敏感/大体积原图。
4. benchmark 固定种子、参数、设备、预热和重复次数；使用唯一 `run_id` 输出 manifest、原始 JSON 和 HTML。
5. 只有满足已批准数据规模和门槛时才生成算法推荐；否则输出候选排序和证据缺口。

## 上下文分配

| 上下文 | 职责 | 主要触达位置 |
| --- | --- | --- |
| 空间定位 | 算法注册、runner、统一结果、质量与验证语义 | `services/localizer/`、`services/matcher/` |
| 任务与影像 | 请求校验、任务编排、结果持久化、API 兼容 | `api/routes/localize.py`、`api/schemas.py` |
| 地图准备 | 保证 tile/NPY/LAS 契约和测试夹具可用 | `services/las_processor/`、`projections/` 契约 |
| Web 接口 | 选择稳定算法 ID、展示可靠性/指标类型/失败原因 | `web/index.html`、`web/app_v10.js` |

## 目标数据流

```text
POST /api/localize
  → 校验 algorithm_ids 与参数
  → 持久化任务请求
  → registry.run(algorithm_id, LocalizationInput)
  → runner 返回 LocalizationResult
  → compatibility adapter + JSON 持久化
  → GET /api/localize/{task_id}
  → 前端按 success/reliable/validations/artifacts 展示
```

## 结果契约

建议结构：

```json
{
  "algorithm_id": "salad_roma_v2_loftr",
  "success": true,
  "reliable": false,
  "pose": {"translation": [], "rotation_vector": []},
  "quality": {
    "match_count": 81,
    "inlier_count": 7,
    "reprojection_error_px": 3.2
  },
  "validations": {
    "projection_consistency": {"status": "available", "median_m": 2.1},
    "ground_truth": {"status": "not_available"}
  },
  "artifacts": {},
  "timings": {"total_s": 2.0},
  "error": null
}
```

`success` 表示算法是否产出几何解，`reliable` 表示是否达到当前可信门槛；两者不得混用。

## 接口兼容

- 请求中的现有算法 ID 保持稳定；删除 `matcher` 字符串二次映射，runner 由算法 ID 直接注册。
- 旧结果读取时由兼容适配器填充新字段；不存在的数据标记 `not_available`。
- 一个算法失败不影响同任务其他算法执行；异常必须携带 `algorithm_id`。
- 报告生成若继续经 HTTP 触发，应使用现有任务生命周期或线程池，使用 `sys.executable`、显式超时和确定输出路径，不能在 async event loop 中同步阻塞。

## 日志与观测

- `api/main.py` 复用 `logger_config.py`，不再维护第二套 handler；
- logger 初始化幂等，重复导入不增加 handler；
- 每条任务日志至少包含 `task_id`、`algorithm_id`、阶段、耗时和错误码；
- 报告 manifest 记录 Git commit、dirty 状态、设备、Python/依赖版本和数据集 ID。

## 迁移顺序

1. 先用失败测试锁定当前错误分派和字段丢失；
2. 引入契约和注册表，同时保留旧适配；
3. 切换 API 与 benchmark 共用同一 runner；
4. 修复运行时回归和干净启动；
5. 修正验证语义与报告结构；
6. 前端迁移到新契约；
7. 运行真实数据验收后更新文档状态。

## 回滚

- 注册表切换保持旧 runner 不变，可通过单一适配层回退；
- 新结果契约只增加字段，不立即删除旧字段；
- 报告格式变更不修改历史报告，只将其标记为“旧指标，不可作绝对精度证据”；
- 若 Phase B 缺少真值，Phase A 可以独立交付，算法推荐保持未决。

## 不推荐方案

- 不在路由中继续增加 `if/elif matcher`：名称漂移会再次发生，且业务分派被锁在接口层。
- 不把当前 NPY 自一致性改名后继续当精度：它可以保留为诊断指标，但不能代替独立真值。
- 不立即重构全部目录：当前首要风险是行为错误和证据不可信，而不是文件位置。
