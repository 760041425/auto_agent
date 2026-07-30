# 技术说明

## 技术栈

- Python 3.10+
- FastAPI + Uvicorn
- SQLAlchemy 2 + SQLite（当前本地实现）
- NumPy、SciPy、OpenCV、Pillow、laspy
- PyTorch、Kornia、RoMa，以及按运行环境加载的 DINOv2/LightGlue
- 静态 HTML/CSS/JavaScript 前端
- pytest + Ruff

## 运行资产

- 数据库：`query_images/app.db`
- 上传文件：`query_images/`
- 地图输入：`las/`
- 派生产物：`projections/`
- 日志：`logs/`

所有相对路径都以仓库根目录为工作目录。脚本应先解析自身路径再执行，避免依赖调用者当前目录。

## 外部依赖

完整预处理可能依赖 `pdal`、`octree_build`、模型权重和本地模型缓存。缺失依赖必须返回可诊断信息，不能静默生成不完整资产。

## 非功能约束

- 上传与任务接口必须对不存在的资源返回明确的 4xx。
- 长任务不能阻塞 HTTP 请求线程；当前用后台线程，生产化前需要评估持久任务队列。
- 进程重启后，持久化的定位任务应能恢复。
- 性能目标必须记录硬件、输入规模、预热方式和百分位数。
- CORS 当前为开发态全开放，生产部署前必须收紧。
