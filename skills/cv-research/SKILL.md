---
name: cv-research
description: 计算机视觉 / 多传感器融合研究技能（本项目深度适配：LAS 视觉定位、相机-LiDAR 融合、深度/法线估计、点云配准、位姿图优化）。当用户说「研究视觉定位」「相机雷达融合」「LiDAR 标定」「点云配准」「位姿优化」「camera-LiDAR」「extrinsic calibration」「cross-modal」「视觉 + 雷达」「CV 研究」「找视觉算法」时触发。继承 research 技能全部流程（含保存研究结果为项目资产），叠加 CV 专用关键词扩展表 + 领域数据源（OpenCV/PCL/PDAL/Open3D/Ceres/g2o）+ MCP 集成。收尾必用表格汇报 + 下一步建议。
---

# CV Research Skill — 计算机视觉 / 多传感器融合研究（本项目深度适配）

> 继承 `skills/research/SKILL.md` 全部流程，叠加 CV / 多传感器融合专用关键词、数据源与 MCP。
> 遵循项目根 `CLAUDE.md`（简中、表格收尾、下一步建议 1–3 条可执行动作）。

## 0. 触发与范围

**触发词**：「研究视觉定位」「相机雷达融合」「LiDAR 标定」「点云配准」「位姿优化」「camera-LiDAR」「extrinsic calibration」「cross-modal correspondence」「视觉 + 雷达」「多传感器融合」「CV 研究」「找视觉算法」「targetless calibration」。

**本项目 CV 研究领域**（关键词自动扩展见 §2）：
- 相机-LiDAR 融合 / Camera-LiDAR Fusion
- LiDAR-Camera 外参标定 / Extrinsic Calibration（含无靶标 / targetless）
- 视觉定位 / Visual Localization / 场景坐标回归
- 图像-点云配准 / Image-to-Point-Cloud Registration
- 跨模态对应 / Cross-Modal Correspondence
- 3D 目标定位 / 3D Object Localization
- 轨迹定位 / Trajectory-Based Localization
- 单目深度 / 法线估计（MiDaS、DepthAnything、DSINE）
- 点云处理 / PCL / Open3D / PDAL
- 位姿图优化 / Ceres / g2o / SAM

## 1. 研究流程（继承 research §1，CV 增强）

完全复用 `skills/research/SKILL.md` §1 八步闭环，以下仅补充 CV 专用增强点。

### ① 联网搜索（CV 增强优先级源）

在 research §1① 基础上，**额外优先**：

| 优先级 | 数据源 | 用途 |
| --- | --- | --- |
| 高 | **OpenCV 文档 / 教程**（`docs.opencv.org`） | 相机标定、特征匹配、PnP、epipolar |
| 高 | **PCL / Open3D / PDAL 官方** | 点云配准、滤波、分割、可视化 |
| 高 | **Ceres Solver / g2o / GTSAM** | 位姿图优化、Bundle Adjustment |
| 中 | **KITTI / NuScenes / Waymo 排行榜** | 基准数据集指标 |
| 中 | **ROS / Autoware 文档** | 传感器融合工程实践 |

### ② 关键词自动扩展（CV 专用扩展表）

根据用户问题自动扩展同义 / 近义 / 中英关键词组合搜索：

| 用户问题关键词 | 扩展搜索词 |
| --- | --- |
| 相机雷达融合 | camera-LiDAR fusion, sensor fusion, cross-modal fusion, image-LiDAR fusion, multi-modal perception |
| LiDAR 相机标定 | LiDAR-camera calibration, extrinsic calibration, targetless calibration, automatic calibration, hand-eye calibration, registration-based calibration |
| 无靶标标定 | targetless calibration, natural feature calibration, motion-based calibration, self-calibration |
| 外参 | extrinsic parameters, LiDAR-Camera extrinsics, rigid transformation, rotation translation estimation |
| 内参无关投影 | intrinsic-free projection, projection without calibration, direct projection, uncalibrated camera |
| 图像点云关联 | image point cloud association, cross-modal correspondence, feature matching across modalities, 2D-3D matching |
| 跨模态配准 | cross-modal registration, image-to-point-cloud registration, 2D-3D registration, RGB-LiDAR alignment |
| 视觉定位 | visual localization, camera relocalization, scene coordinate regression, image-based localization, 6-DoF pose estimation |
| 3D 目标定位 | 3D object localization, 3D detection, BEV detection, point cloud detection, frustum-based detection |
| 轨迹定位 | trajectory-based localization, sequence localization, temporal fusion, visual-inertial odometry, SLAM |
| 深度估计 | monocular depth estimation, depth prediction, MiDaS, DepthAnything, DPT, ZoeDepth, metric depth |
| 法线估计 | surface normal estimation, normal prediction, DSINE, depth-to-normal, predicted normals, SfM normals |
| 点云配准 | point cloud registration, ICP, global registration, feature-based registration, deep registration |
| 位姿优化 | pose graph optimization, bundle adjustment, Ceres Solver, g2o, GTSAM, factor graph |
| 语义分割 | semantic segmentation, panoptic segmentation, LiDAR segmentation, point cloud segmentation |

### ③–⑨ 同 research §1（含保存研究结果步骤）

完全复用 `skills/research/SKILL.md` §1③–⑨，**额外强调**：
- CV 方法对**标定参数**敏感 → §③ 记录表 `Required calibration` 字段必须明确（内参 / 外参 / 联合标定 / 免标定）
- CV 方法对**传感器配置**敏感 → §③ 记录表 `Required sensors` 字段必须明确（单目 / 双目 / RGB-D / LiDAR / IMU / 组合）
- 深度 / 法线估计关注**尺度**（度量 / 相对）→ ACE 6ch 通道对尺度敏感性需在推荐方案中说明
- §⑧ **保存研究结果为项目资产**：CV 研究结论同样保存到 `research/<YYYY-MM-DD>-<slug>.md` 并更新索引，复用项目资产避免重复调研

## 2. MCP 集成（CV 增强）

Skill 告诉 Agent「怎么研究」，MCP 告诉 Agent「去哪里拿资料」。CV 增强配置：

| MCP 服务器 | 用途 | 配置命令 / 状态 |
| --- | --- | --- |
| **WebSearch / WebFetch**（内置） | 通用搜索、抓取论文 / GitHub / 文档页面 | ✅ 始终可用 |
| **Playwright**（本项目已配） | 需要浏览器渲染的页面（arXiv 搜索、GitHub 代码浏览、排行榜） | ✅ 已配置（`--headless`，见 `.mcp.json`） |
| **GitHub MCP** | 查 star / issues / README / 代码搜索 / 权重下载链接 | `claude mcp add github -s project -- npx -y @modelcontextprotocol/server-github` |
| **arXiv MCP** | 结构化论文搜索（标题 / 作者 / 摘要 / 分类 / 时间过滤） | `claude mcp add arxiv -s project -- npx -y @modelcontextprotocol/server-arxiv` |
| **Semantic Scholar MCP** | 引用量、相关论文、作者画像 | 可选配 |
| **Papers With Code MCP** | 论文 + 代码 + 基准一体化 | 可选配 |

> 安装后需重启 Claude Code 会话生效。无专用 MCP 时，WebSearch + WebFetch 直接访问 `arxiv.org`、`paperswithcode.com`、`github.com`、`docs.opencv.org` 等效。

## 3. 领域数据源速查（CV 专用）

| 类别 | 资源 | URL |
| --- | --- | --- |
| 相机标定 | OpenCV Calibration | `docs.opencv.org/master/d9/d0c/group__calib3d.html` |
| 点云处理 | PCL 教程 | `pointclouds.org/documentation/` |
| 点云处理 | Open3D | `www.open3d.org/docs/release/` |
| 点云处理 | PDAL | `pdal.io/en/latest/` |
| 位姿优化 | Ceres Solver | `ceres-solver.org/` |
| 位姿优化 | g2o | `github.com/RainerKuemmerle/g2o` |
| 位姿优化 | GTSAM | `github.com/borglab/gtsam` |
| 深度估计 | MiDaS | `github.com/isl-org/MiDaS` |
| 深度估计 | DepthAnything | `github.com/LiheYoung/Depth-Anything` |
| 法线估计 | DSINE | `github.com/baegwangbin/DSINE` |
| 视觉定位 | DSAC / ACE | `github.com/vislearn/DSAC` |
| 视觉定位 | hloc | `github.com/cvg/Hierarchical-Localization` |
| 配准 | TEASER / Quatro | `github.com/MIT-SPARK/TEASER-plusplus` |
| 融合 | Camel（本项目相关） | 见 `docs/engineering-playbook.md` |

## 4. 落地衔接（CV 特化）

研究完成后若用户要求实现：
1. 在 `specs/<feature-id>/` 建规格包（spec / clarify / plan / tasks / checklist / testlist / risks / decisions）
2. 按 Red → Green → Refactor 推进
3. **CV 特化红线**：
   - 不重训 ACE 模型（除非规格明确批准）
   - 不改 `solve_pnp_with_focal_search` 返回键名 / 语义（SALAD 系依赖）
   - 不用 Sobel 梯度伪法线作为默认 6ch 推理输入（skew 根因，仅 debug）
   - 权重 / 大文件为运行产物，**不入库**（`.cache/`、`projections/`、`reports/` 等）
4. 新 CV 算法接入 `services/localizer/` 时，同步更新 `docs/contexts/` 术语与 README

## 5. 输出示例（CV 特化）

```
## 研究结论：相机-LiDAR 外参自标定

| 方法 | 年份 | 输入 | 标定需求 | 精度 | 代码 | 工程可靠 | 推荐度 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| TargetlessCalib | 2024 | RGB + LiDAR | 免靶标 | 旋转<0.5° | ✅ MIT | ⚠️ 仅室内 | ⭐⭐⭐ 备选 |
| AutoCalib | 2023 | RGB + LiDAR + IMU | 免靶标 | 旋转<0.3° | ✅ Apache2 | ✅ 多场景 | ⭐⭐⭐⭐⭐ 首选 |
| RegNet | 2022 | RGB + LiDAR | 初值外参 | 旋转<0.2° | ✅ BSD | ✅ 稳定 | ⭐⭐⭐⭐ 备选 |

**首选**：AutoCalib（免靶标、IMU 增强、Apache2 可商用）
**风险**：IMU 时间同步要求高 → 需硬件同步或软件对齐
**本项目适配**：现有 ACE 6ch 输入可扩展接收外参，需新增标定模块

**下一步建议**：
1. `claude mcp add github -s project -- npx -y @modelcontextprotocol/server-github` 安装 GitHub MCP 后读 AutoCalib 源码
2. 在 `specs/010-camera-lidar-calib/` 建规格包评估 AutoCalib 接入工作量
3. `python scripts/benchmark_008.py --with-calibration` 评估外参精度对 ACE 6ch 的影响
```
