# 空间定位上下文

## 职责

- 从查询影像提取特征；
- 从地图资产检索候选瓦片；
- 建立 2D–3D 对应并进行几何验证；
- 估计、比较和优化相机位姿；
- 生成区域坐标匹配结果及诊断证据。

## 核心领域概念

- `LocalizationRequest`
- `MapCandidate`
- `Correspondence`
- `PoseEstimate`
- `LocalizationResult`
- `PoseQuality`（内点数、重投影误差、置信度）
- `GeometricFitDiagnostic`（单应内点与像素残差，不是 Benchmark）
- `LocalCoordinateCrosscheck`（查询图选点后，比较任务自产的 H→SLAM XYZ 与最终位姿投影 NPY XYZ；不是绝对精度）
- `CoordinateConsistencyDecision`（多点三维坐标差的中位数与门槛，作为 V2 与 SALAD+RoMa 原版的最终可信标准）
- `GroundTruthEvaluation`（相对独立 holdout 位姿真值的平移/旋转误差）
- `LocalizationArtifacts`（查询图、最终位姿投影图、双图对比图及生成状态）

## 当前代码

- `services/localizer/`
- `services/matcher/`

算法库对象和 NumPy/Torch/OpenCV 结构属于基础设施细节；对外应转换为稳定的定位结果契约。
定位成功与视觉产物生成是两个可观察状态：V2 runner 必须尝试基于最终返回位姿
生成 artifact；渲染失败不得伪造 URL，也不得吞掉已有位姿，而应返回明确失败原因。
同一匹配集和同一 XYZ 图只能产生像素级拟合诊断，不得产生绝对米制精度。
V2 定位任务在成功时生成 query→SLAM XY 单应矩阵和最终位姿 XYZ NPY，本地
`/api/localize/coordinate-transform` 返回的两套 XYZ 差值可以米展示，但仍不得解释为
位姿绝对误差；绝对米制定位精度只属于加载独立真值的 Benchmark 评估结果。
V2 的 `reliable` 不再由内点数或相似度决定，而由最多 256 个有效 NPY 像素的
H/NPY 三维差中位数决定；门槛严格为 `<0.3` 米，等于或超过均判为不准，无可用指标时按低可信处理。
