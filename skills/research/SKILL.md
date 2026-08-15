---
name: research
description: 算法 / 论文 / 工程实现研究技能（本项目适配：LAS 视觉定位、ACE 场景坐标回归、深度/法线估计、相机-LiDAR 融合）。当用户说「研究一下」「调研」「找论文」「有没有开源实现」「最新的方法是什么」「对比几种方案」「research」「survey」「find papers」「state of the art」时触发。固定流程：联网搜 → 论文 → GitHub 源码 → 对比 → 推荐 → 保存研究结果为项目资产 → 落地实现。严格区分「论文能做 / 开源代码能做 / 实际工程可靠」，禁仅凭摘要判断可用性；有 GitHub 实现必须读 README 与关键源码；研究结论必须保存到 research/ 目录。收尾必用表格汇报 + 下一步建议。
---

# Research Skill — 算法 / 论文 / 工程实现研究（本项目适配）

> 通用研究流程 + 本项目（LAS 视觉定位 / ACE / 深度法线估计 / 多模态融合）领域适配。
> 遵循项目根 `CLAUDE.md`（简中、表格收尾、下一步建议 1–3 条可执行动作）。

## 0. 触发与范围

**触发词**：「研究」「调研」「找论文」「最新方法」「有没有开源」「对比方案」「research」「survey」「SOTA」「state of the art」「找实现」。

**本项目高频研究领域**（关键词自动扩展见 §2）：
- 视觉定位 / Visual Localization / 场景坐标回归
- 单目深度估计 / Monocular Depth（MiDaS、DepthAnything、DPT）
- 法线估计 / Surface Normal（DSINE、MiDaS 深度转法线、基于 SfM）
- 相机-LiDAR 融合 / Camera-LiDAR Fusion / 跨模态配准
- 点云处理 / PCL / Open3D / PDAL
- 位姿图优化 / Ceres / g2o / SAM

## 1. 研究流程（八步闭环，顺序执行）

### ① 联网搜索（WebSearch + WebFetch）

**优先级源**（按序）：
1. **arXiv**（`arxiv.org/abs/...`）— 最新预印本
2. **Papers With Code**（`paperswithcode.com`）— 论文 + 代码 + 基准
3. **GitHub**（`github.com/<org>/<repo>`）— 开源实现、star 数、最近更新时间
4. **Google Scholar / Semantic Scholar** — 引用量、后续跟进
5. **官方项目页**（如 `github.com/isl-org/MiDaS`、`github.com/baegwangbin/DSINE`）

**时间过滤**：
- 主体：**最近 3 年**
- 重点：**最近 12 个月**
- 经典奠基性工作（>3 年但引用极高）可保留，但须标注年份

### ② 关键词自动扩展

根据用户问题自动扩展同义 / 近义 / 中英关键词组合搜索。示例表（按需裁剪）：

| 用户问题关键词 | 扩展搜索词 |
| --- | --- |
| 深度估计 | monocular depth estimation, MiDaS, DepthAnything, DPT, ZoeDepth, depth prediction |
| 法线估计 | surface normal estimation, DSINE, normal from depth, SfM normals, predicted normals |
| 视觉定位 | visual localization, scene coordinate regression, ACE, DSAC, camera relocalization |
| 相机雷达融合 | camera-LiDAR fusion, cross-modal registration, extrinsic calibration, targetless calibration |
| 点云配准 | point cloud registration, ICP, global registration, feature matching |
| 位姿优化 | pose graph optimization, Ceres, g2o, SAM, bundle adjustment |

### ③ 逐方法记录（每个候选方法填一张表）

每个候选方法**必须**记录以下字段（缺项标注「未披露 / 未验证」）：

| 字段 | 说明 |
| --- | --- |
| **Paper** | 标题 + arXiv / 会议链接 |
| **Year** | 发表年份（>3 年标黄） |
| **Problem** | 解决什么问题 |
| **Input** | 输入数据（图像 / 点云 / 法线 / 深度 / 标定参数） |
| **Required calibration** | 是否需要相机内参 / 外参 / LiDAR-Camera 联合标定 |
| **Required sensors** | 单目 / 双目 / RGB-D / LiDAR / IMU |
| **Algorithm** | 核心算法 1–2 句 |
| **Open-source implementation** | GitHub 链接 + star 数 + 最近更新时间 + 许可证 |
| **Runtime** | 推理耗时（注明硬件：GPU 型号 / CPU / MPS） |
| **Accuracy** | 精度指标（翻译误差 / 角度误差 / 成功率）+ 测试数据集 |
| **Engineering complexity** | 依赖项数量、安装坑、模型权重大小、是否即插即用 |

### ④ 三重区分（**强制**，禁混淆）

每个候选方法**必须**明确区分三层：

| 层次 | 含义 | 证据要求 |
| --- | --- | --- |
| **论文能做** | 论文实验条件下可达到的指标 | 论文 §Experiments |
| **开源代码能跑通** | 公开代码 + 权重可复现 | 亲自 `git clone` + 读 README + 跑 demo（或确认权重可达） |
| **实际工程可靠** | 在真实数据 / 边缘场景 / 长时间运行下稳定 | 第三方使用报告 / issue 列表 / 工程化细节（异常处理、数值稳定） |

> ⚠️ **禁仅凭论文摘要判断可用性**。论文指标 ≠ 代码能跑 ≠ 工程可靠。

### ⑤ 有 GitHub 实现 → 必须读源码

若存在开源实现，**必须**：
1. 读 **README**：安装步骤、权重下载、依赖、已知限制
2. 读 **关键源码**：推理入口、预处理、后处理、模型加载
3. 检查 **Issues**：未关闭的 bug、环境兼容问题、权重链接失效
4. 检查 **最近更新时间**：>2 年未更新标黄（可能依赖过期）
5. 确认 **模型权重可达**：下载链接 / 是否需要特殊权限（如 Google Drive 确认墙）

### ⑥ 对比矩阵（≥3 个候选时）

用 Markdown 表格横向对比（列：方法、年份、输入、标定需求、传感器、运行时、精度、工程复杂度、推荐度）。

### ⑦ 推荐方案

给出 **1 个首选 + 1 个备选**，说明：
- 为何适合本项目（结合现有代码、数据、硬件）
- 主要风险与缓解
- 落地工作量估计（人天）

### ⑧ 保存研究结果为项目资产（**强制**）

研究结论必须持久保存到 `research/` 目录，作为项目资产复用。

**保存流程**：
1. 按 `research/TEMPLATE.md` 模板生成研究结论 Markdown
2. 文件名：`research/<YYYY-MM-DD>-<topic-slug>.md`（日期为研究完成日，slug 为主题短横线英文或拼音）
3. 代码片段 / 图表放 `research/assets/<同名>/`；**大文件（权重/数据）只记链接不入库**（写入 `research/assets/<同名>/links.md`）
4. 更新 `research/README.md` 索引表格（日期、主题、文件、状态、关键结论）
5. 提交到 git：`git add research/ && git commit -m "research(<slug>): <主题简述>"`

**目录结构**：
```
research/
├── README.md              # 索引（每次研究后更新）
├── TEMPLATE.md            # 本研究的记录模板
├── 2026-08-15-topic-a.md  # 研究结论
└── assets/
    └── topic-a/
        ├── snippet.py     # 代码片段
        └── links.md       # 权重 / 数据链接（不入库大文件）
```

### ⑨ 收尾汇报（**强制**）

用 Markdown 表格汇报（列：步骤 / 事项、状态、产物或证据链接、备注），表格后另起「**下一步建议**」1–3 条可执行动作（可复制命令或 `/wiki-*` 串联）。

## 2. MCP 集成（「去哪里拿资料」）

Skill 告诉 Agent「怎么研究」，MCP 告诉 Agent「去哪里拿资料」。推荐配置（按需添加到 `.mcp.json`）：

| MCP 服务器 | 用途 | 配置状态 |
| --- | --- | --- |
| **WebSearch / WebFetch**（内置） | 通用搜索、抓取论文 / GitHub 页面 | ✅ 始终可用 |
| **Playwright**（本项目已配） | 需要浏览器渲染的页面（动态加载的论文列表） | ✅ 已配置（`--headless`） |
| **GitHub MCP** | 查 star / issues / README / 代码搜索 | ❓ 可选配 |
| **arXiv MCP** | 结构化论文搜索（标题 / 作者 / 摘要 / 分类） | ❓ 可选配 |
| **Semantic Scholar MCP** | 引用量、相关论文推荐 | ❓ 可选配 |

> 无专用 MCP 时，用 WebSearch + WebFetch 直接访问 `arxiv.org`、`paperswithcode.com`、`github.com` 等效。

## 3. 落地衔接

研究完成后若用户要求实现：
1. 在 `specs/<feature-id>/` 建规格包（spec / clarify / plan / tasks / checklist / testlist / risks / decisions）
2. 按 Red → Green → Refactor 推进
3. 权重 / 大文件为运行产物，**不入库**（`.cache/`、`projections/`、`reports/` 等）
4. 项目硬红线：不重训 ACE 模型、不改 `solve_pnp_with_focal_search` 返回键、不提交运行产物

## 4. 输出示例（精简）

```
## 研究结论

| 方法 | 年份 | 输入 | 精度 | 代码 | 工程可靠 | 推荐度 |
| --- | --- | --- | --- | --- | --- | --- |
| MiDaS_small | 2022 | 单目 RGB | — | ✅ 可达 | ✅ 已在本项目验证 | ⭐⭐⭐⭐⭐ 首选 |
| DSINE | 2024 | 单目 RGB | SOTA | ✅ 可达 | ⚠️ 21GB+GDrive 确认墙 | ⭐⭐ 备选 |
| DepthAnything V2 | 2024 | 单目 RGB | 优 | ✅ 可达 | ✅ 轻量 | ⭐⭐⭐⭐ 备选 |

**首选**：MiDaS_small（已集成、MPS 可达、~0.6s/图）
**风险**：度量深度非绝对尺度 → 法线对尺度不敏感可接受

**下一步建议**：
1. `pip install depth-anything` 评估 DepthAnything V2 在本项目数据上的法线质量
2. 在 `specs/009-xxx/` 建规格包对比三种深度源对 ACE 6ch 精度的影响
3. `SSL_CERT_FILE=/etc/ssl/cert.pem python scripts/benchmark_008.py` 复跑基准确认当前最优
```
