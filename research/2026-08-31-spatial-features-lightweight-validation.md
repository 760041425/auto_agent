# 空间感特征提取与轻量验证研究

> 研究日期：2026-08-31
>
> 研究者：@codex
>
> 触发：记录当前查询结果及论文路径，按 P0 / P1 / P3 / P4 验证；避免过重，兼顾速度和准确性
> 适用模块：`services/localizer/`、`projections/tile_index.json`、`reports/benchmark_008.json`、`reports/benchmark_009*.json`

## 1. 研究问题与约束

本研究回答两个问题：

1. 查询影像可提取哪些可与 LAS 地图对齐的空间特征；
2. 哪些方法能在不训练、不下载超大模型的前提下，尽快证明对速度或准确性有增益。

本轮约束：不训练 ACE，不修改默认定位路由；除既有模型外只允许一个小于 200MB 的官方几何候选，优先快速证伪而非扩大全量集成。

## 2. 推荐空间特征契约

### 2.1 查询影像

```text
visual_feature          稠密/稀疏视觉描述子
ray_camera              由真实或估计内参生成的逐像素视线
depth_or_inverse_depth  深度类型必须显式标注，禁止混用
point_camera            相机坐标系点图
normal_camera           相机坐标系法线
confidence              几何估计置信度/有效掩码
```

### 2.2 地图瓦片

```text
point_world      XYZ 图中的世界/SLAM 局部坐标
normal_world     LAS 投影法线
camera_pose      world_to_camera 旋转 + 相机位置
visual_feature   tile RGB 描述子
visibility       accepted 状态与有效像素掩码
```

比较前必须显式变换：

```text
point_camera  = R_cw · point_world + t_cw
normal_camera = R_cw · normal_world
e_normal      = acos(clamp(|dot(n_query_camera, n_map_camera)|, 0, 1))
```

法线使用绝对点积只解决正负极性，不解决坐标系、相机内参或深度语义错误。

## 3. 当前项目证据

### 3.1 历史基准

| 证据 | 样本 | 结果 | 可解释结论 |
| --- | ---: | --- | --- |
| `reports/benchmark_008.json` | 22 | scene3ch 1.999m；6ch constant **0.886m**；6ch MiDaS 1.085m | MiDaS 法线直接注入 ACE 比常量占位恶化约 23%，不能切默认路由 |
| `reports/benchmark_009_xfeat.json` | 12（2 真实 + 10 tile） | LoFTR-fast 11.583s、100%、16.66px | 是速度候选，但汇总受 tile 同图检索显著影响 |
| 同报告，仅 2 张真实查询 | 2 | LoFTR-fast 平均 12.231s、重投影约 90.22px；XFeat 24.775s、约 103.91px | 两条路径均未通过 8px 质量门，不能宣称真实图准确 |

### 3.2 P0：空间输出可达性（本机 CPU，缓存权重）

2 张 1920×1080 查询图 + 3 张 512×512 accepted tile：

| 项目 | 结果 |
| --- | --- |
| 输出契约 | 5/5 为 `(H,W,3)` float32，有限值率 100% |
| 法线长度 | 均值 0.940–1.000 |
| `[0,1]` 均值 | 0.467–0.492 |
| 冷启动 | 2.824s |
| 热态真实查询 | 0.926s |
| 热态 tile | 0.725–0.756s |

结论：MiDaS 适合做低成本候选生成或软评分实验；约 0.7–0.9s 的增量不适合无条件加入每次快速定位。

### 3.3 P1：法线语义对齐验证

将 3 张 tile 的 LAS 世界法线按 `camera_pose.quaternion_wxyz` 旋转到相机坐标系，再与查询端 MiDaS 法线比较：

| tile | 有效覆盖 | 有向角中位数 | 无向角中位数 |
| --- | ---: | ---: | ---: |
| yaw90 | 39.26% | 93.24° | 51.06° |
| yaw180 | 47.30% | 77.07° | 43.53° |
| yaw270 | 55.85% | 54.62° | 36.10° |

将 MiDaS 输出按逆深度取倒数后，无向角中位数改善到 44.24° / 38.90° / 29.43°，仍不足以做硬门控。当前适配器把 MiDaS 预测直接传给 `depth_to_normals`，而 MiDaS 只承诺相对深度；深度/逆深度语义与尺度处理必须先建立稳定契约。

结论：P1 **不把法线直接接入 PnP 硬过滤**。若继续，只允许作为低权重软残差，并先用 MoGe-2-small 或 Metric3Dv2 重新做同一角误差实验。

#### P1b：MoGe-2 ViT-S normal 小模型实跑

固定官方 MoGe-2 代码 `microsoft/MoGe@42acd8f` 与权重快照 `Ruicheng/moge-2-vits-normal@679230677b4d282c6f304189a93e98e14f085902`。当前 MoGe 3 引入 macOS 不可用的 Triton/FlexGEMM，因此没有安装主分支；MoGe-2 采用 `--no-deps`，只补固定 `utils3d@3fab839f`，没有拉入 Gradio/pipeline。权重为 140,550,416 bytes（134.0MiB，托管页显示约 141MB）。这是推理最小环境而非完整 MoGe CLI/UI 环境：`pip check` 会据实报告缺少 Gradio、pipeline、moderngl，但固定模型导入、权重加载和 5 样本推理均已通过。

同一 2 张真实查询 + yaw90/yaw180/yaw270 三张 tile，CPU、1200 tokens：

| 项目 | 结果 |
| --- | --- |
| 输出契约 | 5/5 为 `(H,W,3)` float32，有限值率 100%，来源与 OpenCV 相机坐标系均显式记录 |
| 有效法线率 | 真实查询 99.70% / 92.64%；tile 100% / 96.92% / 100% |
| 首次下载+加载 / 缓存加载 | 29.18s / 1.10–1.14s |
| 热态推理 P50 | 0.997–1.021s（三次语义结果完全一致） |
| 对已发布 `normal.npy` | yaw90 68.88°、yaw180 55.17°、yaw270 28.67°；总体 52.79° |
| 对修正 XYZ 四邻域法线 | yaw90 63.45°、yaw180 31.66°、yaw270 15.87°；总体 33.47° |
| 资格结论 | 两种参考均大于 20°，**禁止进入软评分** |

坐标反证同时检查 `R_cw`、`R_wc` 和 OpenCV↔OpenGL Y/Z 翻转，最佳总体中位角仍为 48.87°，不是一个简单转置或固定轴翻转问题。早期 raw XYZ helper 没有屏蔽无效四邻域，产生的 36.88°/19.78° 已被后续 P1c 报告废弃；最新可信结果以 33.47°/25.57° 为准。因此本轮只证明“MoGe 不符合当前地图法线契约”，不能外推为 MoGe 通用能力不足，也不继续安装 Metric3Dv2。

实现与证据路径：`services/localizer/moge_normal_010.py`、`scripts/benchmark_010_moge_normals.py`、`reports/benchmark_010_moge_normals.json`。资格函数和未单位化叉积尺度回归位于 `services/tests/test_spatial_validation_010.py`。

#### P1c：地图法线参考修复

根因复现表明旧 `_compute_normal_map` 有两个耦合问题：XYZ 三轴分别 min-max 后双边平滑不保证旋转等变，合成斜面刚体旋转后的最大无向角达到 88°；中心差分只检查中心有效，跨 3×3 孔洞边缘生成了 12 个假单位法线。修复后直接对 float64 XYZ 做完整四邻域中心差分，只在中心和上下左右均有效、叉积非退化时输出 float32 单位法线。

| 项目 | yaw90 | yaw180 | yaw270 | 总体/结论 |
| --- | ---: | ---: | ---: | --- |
| 旧发布有效率 | 39.26% | 48.42% | 56.19% | 包含跨无效邻域法线 |
| 候选有效率 | 30.35% | 38.10% | 49.65% | 假有效法线 0 |
| 旧发布 vs 候选中位角 | 44.67° | 33.42° | 9.02° | 总体 25.57° |
| MoGe vs 候选中位角 | 63.45° | 31.66° | 15.87° | 总体 33.47°，仍拒绝软评分 |

合成测试旋转最大误差为 `1.57e-6°`（门槛 0.01°），候选生成 P50 0.0238s；三个候选写入 `projections/benchmark_010_map_normals/`，没有覆盖 `tile_index.json` 或发布 `normal.npy`。报告：`reports/benchmark_010_map_normals.json`。扩散检索发现 MoGe benchmark 的 raw helper 也跨无效邻域，已改为复用地图准备唯一契约；其余命中属于描述子可视化、全局 PCA 或 debug Sobel，不是同模式。

#### P1d：8 个空间位置的覆盖与 ACE 输入影响

从 accepted 地图按地图顺序均匀选取 8 个不同 `tile`，复刻 `SceneCoordinateDataset` 的最近邻缩放到 32 倍数、再 `[::8, ::8]` 采样。代码审计确认 ACE 的样本准入只要求 accepted、图像和 XYZ，`ACTLoss` 的监督掩码只由低分辨率 XYZ 非零决定；法线仅作为 6ch 输入，经 `(normal + 1) * 0.5` 映射。因而候选法线缺失不会删除监督点，而会把相应输入变成无信息的 0.5 占位。

| 指标 | 已发布法线 | 四邻域候选 | 影响 |
| --- | ---: | ---: | ---: |
| 8 位置 ACE XYZ 监督像素 | 16,396 | 16,396 | 0 |
| 监督点上的有效法向像素 | 15,074 | 12,824 | -2,250 |
| 监督点上的法向信息覆盖 | 91.94% | 78.21% | -13.72 个百分点 |
| 与候选的总体无向角中位数 | 24.57°（旧发布） | 40.33°（MoGe） | 均未过各自 10° / 20° 门 |

8/8 候选由当前生产函数逐数组精确复现，8 个路径均与发布路径隔离，`tile_index.json` 对候选目录引用数为 0；完整四邻域外假有效法线仍为 0。候选生成 P50 0.0214s，MoGe CPU 推理 P50 0.9635s。只有 2/8 位置的单 tile MoGe 中位角小于 20°，跨位置总体为 40.33°，所以停止模型接入，也不以本结果启动 ACE 重训或发布。证据：`reports/benchmark_010_map_normals_8.json`、`projections/benchmark_010_map_normals_8/`。

### 3.4 P3：官方基线可达性

| 项目 | 本地状态 | 本轮处理 |
| --- | --- | --- |
| hloc | 已安装 `hloc 1.5`、`pycolmap 4.1.1`、官方 LightGlue commit `eb42fee` | 完成 SuperPoint+LightGlue 前端 8+2；完整 pycolmap pipeline 因 macOS 双 libomp 安全跳过 |
| ACE0 | 未安装，需 DSAC* 绑定与场景增量学习 | 过重，保留为准确率上限，不进入本轮 |
| SCR Priors | 依赖 ACE0，扩展含 Nerfstudio/扩散先验 | 过重，保留研究路径 |
| SuperPoint/LightGlue | 权重已缓存 | 可做 hloc-lite 匹配器代理 |
| MoGe-2 ViT-S normal | 已固定 MoGe-2/权重 commit；134.0MiB 权重已缓存 | 2+3 与 8 位置扩展完成；8 位置修正参考总体 40.33°，资格门仍拒绝 |

安装实测：pycolmap arm64 wheel 20.4MB，hloc 源码归档 10.4MB，SuperPoint/LightGlue 权重合计 51MB，`.venv` 约从 1.9GB 增至 2.0GB，未触发 2GB 新增依赖停止线。官方 hloc 包入口会同时加载 pycolmap 与 Torch；两者各自捆绑 `libomp.dylib`，在 macOS 原生 abort。适配器没有使用 `KMP_DUPLICATE_LIB_OK`，而是只加载官方 SuperPoint/LightGlue 模块，继续使用项目权威 NPY 与 OpenCV PnP；完整 pycolmap pipeline 明确标记 `skipped: duplicate_openmp_runtime`。

相同 8+2 结果（CPU，SALAD 同候选/同 XYZ/同真值）：

| 方法 | LOO 成功/质量 | 平移均值 | 旋转均值 | warm P50 | 真实图质量 |
| --- | --- | ---: | ---: | ---: | --- |
| LoFTR pose-only | 8/8；7/8 | 0.0557m | 0.1278° | 5.41s | 0/2 |
| hloc SuperPoint+LightGlue | 8/8；7/8 | 0.1732m | 0.1531° | 1.67s（首跑） | 0/2 |
| hloc retry / retry2 | 均为 8/8；7/8 | 均为 0.1732m | 均为 0.1531° | 1.78s / 1.76s | 均为 0/2 |

hloc 对比可复现且约快 67%，但平移均值是 LoFTR 的约 3.1 倍；两个样本达到 0.415m/0.446m，普通重投影质量门仍未识别绝对误差。因此它只保留为速度对照，不升级默认。报告：`reports/benchmark_010_hloc.json`、`reports/benchmark_010_hloc_retry.json`、`reports/benchmark_010_hloc_retry2.json`。

### 3.5 P4：速度—准确率联合判断

同图烟测 3 张 accepted tile：LoFTR-fast 成功率 100%，平均平移误差 0.0021m、旋转误差 0.092°、平均耗时 19.01s；该结果只能证明位姿字段和求解链路自洽。

leave-one-out（从 SALAD 索引移除查询自身）1 张：命中相邻 tile，相似度 0.9749，平移误差 0.0039m、旋转误差 0.079°、冷启动 28.93s。样本太少，记为 P4 冒烟证据，不能替代 holdout 基准。

完成防同 key 契约后，使用 `scripts/benchmark_010.py` 对均匀抽取的 8 个不同 tile 做 leave-one-out，并单列 2 张真实查询：

| 分组 | 样本 | 成功/质量门 | 绝对精度 | 完整后处理耗时 |
| --- | ---: | --- | --- | --- |
| leave-one-out | 8 | 8/8 成功，7/8 质量通过 | 平移均值 0.0557m；旋转均值 0.1278° | cold 31.84s；warm P50/P95 19.78/22.25s |
| 真实查询 | 2 | 2/2 返回位姿，0/2 质量通过 | 无独立真值，仅诊断；重投影 133.89px / 23.61px | warm P50 18.54s |

这说明 LoFTR-fast 在同域留一数据上已有厘米级能力，但真实照片存在明显域差：函数“返回成功”不能视为可信定位，必须服从质量门。

针对耗时剖析新增显式 `pose_only_benchmark`，只跳过不参与位姿评分的坐标一致性、地面分割和视觉产物，默认生产路径不变。同一 8+2 复跑结果：cold 22.47s（-29.4%）、leave-one-out warm P50 5.41s（-72.6%）、真实查询 warm P50 5.34s（-71.2%），10 条总耗时 204.45s → 71.06s；逐条平移、旋转和重投影最大漂移均为 0。原始报告：`reports/benchmark_010.json`；加速报告：`reports/benchmark_010_pose_only.json`。

P4b 继续审计发现，正式 pose-only 仍无条件构建 5,252,140 点稠密数组与 KD-Tree，而 tile XYZ→PnP 和独立位姿真值均不消费它。单独实测初始化 14.405s（完整子进程 17.02s，点/颜色数组至少 75.1MiB，尚不含 500 万 Python 对象和 KD-Tree）。改为按后处理计划懒加载后，同一 8+2 cold `22.473s→6.578s`（-70.7%），leave-one-out warm P50 `5.410s→4.829s`（-10.7%），真实查询 warm P50 `5.338s→4.963s`（-7.0%）。10 条成功、质量门、内点和重投影逐条一致，平移/旋转最大漂移均为 0；LAS 诊断 10/10 显式 `skipped: pose_only_benchmark`。报告：`reports/benchmark_010_pose_only_no_dense.json`。

P4c 再消除最终位姿确定后才运行的二次 DISK+LightGlue 投影拟合。首次对比暴露焦距搜索的 `ransac_seed` 虽声明却未传入底层 OpenCV，已按 Red→Green 修复，并用当前代码生成只切换投影诊断的同种子控制组。控制组与正式组 10 条成功、质量门、内点、PnP 重投影和位姿误差逐字段差异为 0；正式组 10/10 投影诊断显式 `skipped: pose_only_benchmark`。相对 P4b，cold `6.578s→2.301s`（-65.0%）、leave-one-out warm P50 `4.829s→0.675s`（-86.0%）、真实查询 warm P50 `4.963s→1.406s`（-71.7%）；相对同种子投影启用控制组，三项分别下降 33.9%、52.5%、34.3%。默认完整路径 smoke 仍返回投影拟合、LAS 100%、坐标 ready 和 3 个视觉产物。报告：`reports/benchmark_010_pose_only_projection_control.json`、`reports/benchmark_010_pose_only_core.json`。

当前双轨结论：

- **速度方向**：正式离线评分使用 `pose_only_benchmark`，cold 已降到 2.30s、leave-one-out 热态 P50 0.675s；生产默认仍加载稠密地图并输出完整诊断/产物。下一瓶颈转为 LoFTR 推理与 PnP 焦距搜索，应先分段剖析再决定是否缩小输入或搜索空间。
- **准确性方向**：确定性 8+2 下 LoFTR 留一平移/旋转均值为 0.0604m/0.1497°，仍优于 hloc 的 0.1732m/0.1531°；两者真实查询均 0/2 质量通过。地图法线生成契约已修正，但 MoGe-2 的 8 位置总体仍为 40.33°；ACE 的 XYZ 监督量不受候选覆盖下降影响，但法向信息覆盖降至 78.21%，默认路由不切换，也不继续堆叠模型。

## 4. 近年论文与官方代码路径

### 4.1 单目几何/法线

| 方法 | 年份 | 主要输出 | Paper | 官方代码 | 本项目判断 |
| --- | ---: | --- | --- | --- | --- |
| DSINE | 2024 | 相机射线感知法线、可选不确定性 | [arXiv:2403.00712](https://arxiv.org/abs/2403.00712) | [baegwangbin/DSINE](https://github.com/baegwangbin/DSINE) | 准确性对照；权重/评测资产获取不轻 |
| Metric3Dv2 | 2024 | metric depth、法线、置信度 | [arXiv:2404.15506](https://arxiv.org/abs/2404.15506) | [YvanYin/Metric3D](https://github.com/YvanYin/Metric3D) | P1 第二候选；BSD-2、支持 ONNX |
| MoGe / MoGe-2 | 2025 | point map、metric depth、内参、mask、normal | [MoGe CVPR 2025](https://github.com/microsoft/MoGe#publications) | [microsoft/MoGe](https://github.com/microsoft/MoGe) | 首选新几何候选；优先 35M/104M normal 小模型 |
| VGGT | 2025 | 多视图相机、深度、点图、tracks | [arXiv:2503.11651](https://arxiv.org/abs/2503.11651) | [facebookresearch/vggt](https://github.com/facebookresearch/vggt) | 1B 模型与许可证/权重门槛偏重，暂不跑 |

### 4.2 图像匹配与图像—点云注册

| 方法 | 年份 | 价值 | Paper | 官方代码 | 本项目判断 |
| --- | ---: | --- | --- | --- | --- |
| RoMa | 2024 | 稠密 warp + certainty | [arXiv:2305.15404](https://arxiv.org/abs/2305.15404) | [mtcto/roma](https://github.com/mtcto/roma) | 已有 `romatch`，保留强匹配对照 |
| MASt3R | 2024 | 3D pointmap + 稠密局部描述子 | [arXiv:2406.09756](https://arxiv.org/abs/2406.09756) | [naver/mast3r](https://github.com/naver/mast3r) | 准确性候选，但新权重/依赖较重，跳过 P2 |
| FreeReg | 2024 | 零样本图像—点云注册，扩散特征 + 单目几何 | [arXiv:2310.03420](https://arxiv.org/abs/2310.03420) | [WHU-USI3DV/FreeReg](https://github.com/WHU-USI3DV/FreeReg) | 思路相关，扩散模型过重 |
| 2D3D-MATR | 2023 | detection-free 图像—点云粗到细匹配 | [arXiv:2308.05667](https://arxiv.org/abs/2308.05667) | [minhaolee/2D3DMATR](https://github.com/minhaolee/2D3DMATR) | 预训练域偏室内、旧 CUDA 栈，不作为首跑 |
| I2PNet | 2025 | 原始 RGB/LiDAR 端到端 2D-3D cost volume | [arXiv:2306.11346](https://arxiv.org/abs/2306.11346) | [IRMVLab/I2PNet](https://github.com/IRMVLab/I2PNet) | 需训练和自定义算子，工程过重 |

### 4.3 定位/场景坐标基线

| 方法 | 年份 | Paper | 官方代码 | 本项目判断 |
| --- | ---: | --- | --- | --- |
| hloc | 2019/持续维护 | [arXiv:1812.03506](https://arxiv.org/abs/1812.03506) | [cvg/Hierarchical-Localization](https://github.com/cvg/Hierarchical-Localization) | P3 首个官方基线；Apache-2.0，需 `pycolmap` |
| ACE0 | 2024 | [arXiv:2404.14351](https://arxiv.org/abs/2404.14351) | [nianticlabs/acezero](https://github.com/nianticlabs/acezero) | 准确率上限；需增量学习/DSAC*，本轮不跑 |
| SCR Priors | 2025 | [arXiv:2510.12387](https://arxiv.org/abs/2510.12387) | [nianticspatial/scr-priors](https://github.com/nianticspatial/scr-priors) | 已并入 ACE0 可选能力；依赖较重，后置 |

## 5. 推荐架构

```text
查询影像
  └─ SALAD RGB 对称召回（top-1 fast）
       └─ LoFTR-fast / RoMa 匹配
            └─ tile 权威 XYZ 生成 2D-3D 对应
                 └─ PnP-RANSAC
                      ├─ 快速出口：质量门通过即返回
                      └─ 准确性出口：几何软评分/重排后再求解
```

几何模型不参与第一阶段全局召回，避免查询/索引模态不对称；只在候选很少时按需运行。

## 6. 下一验证顺序

1. P4 防同 key 的 8+2 基准、P4b 稠密点云冷启动消除和 P4c 二次投影诊断消除均已完成；保持 `pose_only_benchmark` 作为离线评分路径，不改变生产默认。下一速度切片只做 LoFTR/PnP 分段剖析。
2. P3 hloc 前端 8+2 已完成；保持速度对照，不切默认，完整 pycolmap pipeline 等依赖统一 OpenMP 后再评。
3. 地图法线代码契约已修复，8 位置候选仍未发布；MoGe-2 总体 40.33° 被 20° 门拒绝。ACE XYZ 监督像素不变，但法向信息覆盖下降 13.72 个百分点，因此不安装 Metric3Dv2、不进入软评分、不启动重训。
