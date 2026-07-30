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

## 当前代码

- `services/localizer/`
- `services/matcher/`

算法库对象和 NumPy/Torch/OpenCV 结构属于基础设施细节；对外应转换为稳定的定位结果契约。
