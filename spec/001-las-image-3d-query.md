# 001-LAS影像3D坐标查询系统

## 背景

用户有一批 LAS 点云文件（存放在 `las/` 目录），以及待查询的地面/航拍图像（存放在 `query_images/` 目录）。需要构建一个系统，使用户上传图像后，能够自动匹配 LAS 点云数据，获取图像中任意区域的 3D 空间坐标。

## 需求描述

### 1. Web 前端

- 图像上传页面，支持拖拽/选择文件上传
- 已上传图像的列表页（缩略图、文件名、上传时间、处理状态）
- 图像详情页：显示原始图像，支持框选/点击查询区域的 3D 坐标
- 比较任务触发按钮：对单张图像发起与 LAS 数据的匹配计算
- 比较报告展示页：显示匹配结果、置信度、区域的 3D 坐标标注

### 2. API 后端（FastAPI）

- `POST /api/images/upload` — 上传查询图像
- `GET /api/images` — 图像列表（分页、筛选状态）
- `GET /api/images/{id}` — 图像详情
- `POST /api/tasks/compare` — 创建比较任务（指定图像 ID）
- `GET /api/tasks/{id}` — 查询任务状态与结果
- `GET /api/reports/{id}` — 获取比较报告详情

数据库表：

| 表名 | 字段 |
|------|------|
| `images` | id, filename, path, status, created_at |
| `las_files` | id, filename, path, projection_jpg_path, coord_map_path, created_at |
| `tasks` | id, image_id, las_file_id, status, result_json, created_at, finished_at |
| `reports` | id, task_id, matched_regions_json, confidence, created_at |

### 3. LAS 处理服务

- 扫描 `las/` 目录，自动检测新增 LAS 文件
- **LAS 投影生成**：将 3D 点云正射投影为 2D JPG 图像（指定分辨率）
- **像素-3D 坐标映射**：生成每个像素对应 3D 坐标的映射文件（JSON/NPZ 格式），记录 `{(x_pixel, y_pixel): (x_3d, y_3d, z_3d)}`
- **批量特征提取**：对 LAS 投影图提取局部特征描述子（SIFT / SuperPoint），存入索引
- 结果写入数据库 `las_files` 表

### 4. 比较任务（图像→LAS 匹配）

- 对查询图像提取特征描述子（与 LAS 投影使用相同算法）
- 特征匹配：将图像特征与 LAS 投影特征进行最近邻匹配 + RANSAC 几何验证
- 坐标映射：通过匹配点查询像素-3D 坐标映射表，获取图像区域的 3D 坐标
- 结果写入 `tasks` 表和 `reports` 表

## 验收标准

1. 用户上传图像后，能在列表中看到，状态正确流转
2. 触发比较任务后，任务异步执行，完成后可查看报告
3. 报告中包含图像中匹配区域的 3D 坐标（经度、纬度、海拔）
4. LAS 文件放入 `las/` 后自动触发投影和特征提取

## 技术要点

- 前端：React / Vue + 图像标注组件（支持框选区域）
- 后端：Python FastAPI + SQLAlchemy + SQLite / PostgreSQL
- LAS 处理：`laspy` 读取点云，`opencv` 做投影和特征提取，`pycolmap` 或 `kornia` 做特征匹配
- 任务队列：Celery / ARQ 实现异步比较任务
- 特征算法：SIFT（经典）或 SuperPoint+SuperGlue（深度学习方案，精度更高）

## 已知限制

### LAS Point Format 兼容性

`octree_build` **仅支持 LAS point format 0-3**。在 `_downsample_las_with_laspy()` 下采样时，`laspy.write()` 默认输出 LAS 1.4 format 7，需显式指定输出为 LAS 1.2 format 3 以兼容 octree_build。

前排提示：如有新的 point format 需求，需同步修改 `_downsample_las_with_laspy()` 中的 `LasHeader(point_format=3, version="1.2")` 以及 `octree_build` 的 C++ 源码。

## 目录结构

```
project-root/
├── las/                  # LAS 点云文件
├── query_images/         # 用户上传的查询图像
├── projections/          # LAS 投影 JPG 和坐标映射文件
├── web/                  # 前端项目
├── api/                  # FastAPI 后端
├── services/
│   └── las_processor/    # LAS 处理服务
└── spec/                 # 需求文档
```
