# 地图准备上下文

## 职责

- 读取 LAS、轨迹、COLMAP 与地图偏移配置；
- 下采样并构建八叉树；
- 生成地图投影、XYZ 图、法线图与瓦片索引；
- 构建检索特征或训练场景模型。

## 发布契约

- `tile_index.json` 中的瓦片元数据；
- 投影图、XYZ/法线数组及其坐标系；
- SALAD 特征索引和 ACE 模型权重；
- 预处理进度、完成或失败结果。

## 当前代码

- `services/las_processor/`
- `services/localizer/ace_trainer.py`（训练职责待进一步解耦）
- `api/routes/preprocess.py`（接口与应用编排混合，后续抽离 use case）

本上下文不负责查询影像、在线位姿任务和报告持久化。
